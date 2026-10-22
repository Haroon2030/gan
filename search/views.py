import logging

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.cache import cache
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET

from .api_client import (
    ApiClientError,
    _api_cfg,
    default_warehouse,
)
from .validators import ValidationError

logger = logging.getLogger(__name__)


def _welcome_cards_for_user(user) -> list[dict]:
    """بطاقات الترحيب حسب الشاشات المسموح بها."""
    from django.urls import reverse

    from .nav_permissions import user_can_access_screen

    catalog = (
        (
            'browse_income',
            'قائمة الدخل',
            'النتيجة المالية للفترة',
            'welcome-card--income',
            'M12 1v22M17 5H9.5a3.5 3.5 0 0 0 0 7h5a3.5 3.5 0 0 1 0 7H6',
        ),
        (
            'browse_sales',
            'المبيعات',
            'صافي الفروع والقنوات بعد المرتجع',
            'welcome-card--sales',
            'M4 19V5M4 19h16M8 16V9M13 16V6M18 16v-4',
        ),
        (
            'browse_performance',
            'الأداء',
            'مقارنة الفترات ومؤشرات التشغيل',
            'welcome-card--perf',
            'M12 20V10M18 20V4M6 20v-4M4 20h16',
        ),
        (
            'browse_inventory',
            'تحليل المخزون',
            'الأرصدة وحركة المجموعات',
            'welcome-card--inv',
            'M3 7l9-4 9 4-9 4-9-4zM3 12l9 4 9-4M3 17l9 4 9-4',
        ),
        (
            'browse_purchases',
            'تحليل المشتريات',
            'التوريد ودورة الموردين',
            'welcome-card--purch',
            'M6 6h15l-1.5 9H8L6 6zM9 20a1.5 1.5 0 1 0 0-0.01M18 20a1.5 1.5 0 1 0 0-0.01M6 6L5 3H2',
        ),
        (
            'browse_assets',
            'الأصول',
            'الأصول المسجّلة على الفروع',
            'welcome-card--assets',
            'M3 21h18M5 21V8l7-4 7 4v13M9 21v-6h6v6',
        ),
    )
    cards: list[dict] = []
    for key, title, hint, css, path_d in catalog:
        if not user_can_access_screen(user, key):
            continue
        try:
            href = reverse(key)
        except Exception:  # noqa: BLE001
            continue
        cards.append(
            {
                'key': key,
                'title': title,
                'hint': hint,
                'css': css,
                'href': href,
                'path_d': path_d,
            }
        )
    return cards


def _welcome_display_name(user) -> str:
    """اسم الشخص للعرض — يتجاهل الأرقام الصرفة (رقم الدخول) لصالح الاسم الحقيقي."""
    profile = getattr(user, 'profile', None)
    candidates = [
        ((profile.display_name if profile else '') or '').strip(),
        (user.get_full_name() or '').strip(),
        (user.first_name or '').strip(),
        (user.last_name or '').strip(),
        (user.username or '').strip(),
    ]
    for name in candidates:
        if not name:
            continue
        # تجاهل رقم الدخول/الهاتف إن وُجد اسم نصّي لاحقاً
        if name.isdigit():
            continue
        return name
    for name in candidates:
        if name:
            return name
    return 'مستخدم'


def _welcome_user_context(user) -> dict:
    """اسم العرض واسم الدور ونص الترحيب حسب الدور."""
    from django.utils.html import format_html

    from .nav_permissions import (
        EXECUTIVE_ROLE_NAMES,
        SECTION_MANAGER_ROLES,
        has_full_app_access,
        user_role_name,
    )

    display_name = _welcome_display_name(user)
    role_name = user_role_name(user)
    if not role_name:
        role_name = 'مدير النظام' if user.is_staff else 'مستخدم'

    name_html = format_html('<span class="welcome-sub-name">{}</span>', display_name)
    role_html = format_html('<span class="welcome-sub-role">{}</span>', role_name)

    role_copy = {
        'مدير مبيعات': (
            'لإدارة المبيعات',
            'تتاح لكم لوحة المبيعات: صافي الفروع، بحث المبيعات، وتحليل الأداء ضمن صلاحياتكم.',
        ),
        'مدير مشتريات': (
            'لإدارة المشتريات',
            'تتاح لكم لوحة المشتريات: بحث الأصناف، تحليل التوريد، ودوران الموردين وطلبات الشراء.',
        ),
        'مدير تسعيرة': (
            'لإدارة التسعيرة',
            'تتاح لكم لوحة التسعير: حد الربح، الأصناف غير المسعّرة، ومقارنة أسعار الموردين.',
        ),
        'مدير مخازن': (
            'لإدارة المخزون',
            'تتاح لكم لوحة المخزون: الأرصدة، المجموعات، والرصيد بلا مبيعات ضمن صلاحياتكم.',
        ),
        'مدير مستودع': (
            'لإدارة المستودعات',
            'تتاح لكم لوحة المستودعات: التحويلات، مقارنة الأرصدة، ومتابعة الحركة الصادرة.',
        ),
    }

    if has_full_app_access(user) or role_name in EXECUTIVE_ROLE_NAMES:
        kicker = 'للإدارة العليا'
        subtitle = format_html(
            'نرحّب بك بصفتكم {} نقدّم قراءة تنفيذية لقائمة الدخل والمبيعات ومؤشرات الأداء، '
            'ثم المخزون والمشتريات والأصول — لدعم قرار الإدارة.',
            role_html,
        )
    elif role_name in role_copy:
        kicker, detail = role_copy[role_name]
        subtitle = format_html(
            'نرحّب بكم أستاذ {} في منصة التحليل. بصفتكم {}، {}',
            name_html,
            role_html,
            detail,
        )
    elif role_name in SECTION_MANAGER_ROLES:
        kicker = f'لدور {role_name}'
        subtitle = format_html(
            'نرحّب بكم أستاذ {}. بصفتكم {}، '
            'البطاقات أدناه تعرض الشاشات المتاحة حسب صلاحيات دوركم.',
            name_html,
            role_html,
        )
    else:
        kicker = 'حسب صلاحياتك'
        subtitle = format_html(
            'نرحّب بكم أستاذ {} في منصة التحليل. '
            'البطاقات أدناه تعرض الأقسام والشاشات المسموح لكم بفتحها فقط.',
            name_html,
        )

    return {
        'display_name': display_name,
        'role_name': role_name,
        'is_staff': bool(user.is_staff),
        'welcome_kicker': kicker,
        'welcome_subtitle': subtitle,
        'welcome_cards': _welcome_cards_for_user(user),
    }


def _home_dashboard_access(user) -> dict:
    """ما يحق للمستخدم رؤيته من لوحة المؤشرات — نفس فحص الـ API."""
    from .home_dashboard import (
        RECENT_DOC_SCREENS,
        TREND_SERIES_SCREENS,
        WIDGET_SCREENS,
    )
    from .nav_permissions import user_can_access_screen

    widgets = {
        name: all(user_can_access_screen(user, s) for s in screens)
        for name, screens in WIDGET_SCREENS.items()
    }
    recent_types = sorted(
        t for t, s in RECENT_DOC_SCREENS.items() if user_can_access_screen(user, s)
    )
    trend_series = sorted(
        k for k, s in TREND_SERIES_SCREENS.items() if user_can_access_screen(user, s)
    )
    widgets['recent'] = bool(recent_types)
    widgets['trend'] = bool(trend_series)
    return {
        'widgets': widgets,
        'recent_types': recent_types,
        'trend_series': trend_series,
        'any': any(widgets.values()),
    }


def _home_period(request):
    from .home_dashboard import default_period

    d_from, d_to = default_period()
    raw_from = request.GET.get('date_from')
    raw_to = request.GET.get('date_to')
    if raw_from or raw_to:
        return _parse_sales_dates(raw_from or d_from.isoformat(), raw_to or d_to.isoformat())
    return d_from, d_to


@login_required
@never_cache
def home(request):
    """الرئيسية: لوحة المؤشرات التنفيذية حسب الصلاحيات، أو بطاقات الترحيب إن لم يُسمح بشيء."""
    from django.urls import reverse

    from .home_dashboard import previous_period, recent_months

    ctx = _welcome_user_context(request.user)
    access = _home_dashboard_access(request.user)
    period_error = ''
    try:
        d_from, d_to = _home_period(request)
    except ValidationError as exc:
        from .home_dashboard import default_period

        period_error = str(exc)
        d_from, d_to = default_period()
    p_from, p_to = previous_period(d_from, d_to)

    links = {}
    for key in (
        'browse_sales',
        'browse_performance',
        'browse_purchases',
        'browse_inventory',
    ):
        try:
            links[key] = reverse(key)
        except Exception:  # noqa: BLE001
            links[key] = ''

    return render(
        request,
        'search/home.html',
        {
            'display_name': ctx['display_name'],
            'role_name': ctx['role_name'],
            'is_staff_user': ctx['is_staff'],
            'welcome_kicker': ctx['welcome_kicker'],
            'welcome_subtitle': ctx['welcome_subtitle'],
            'welcome_cards': ctx['welcome_cards'],
            'dash': access,
            'dash_widgets': access['widgets'],
            'dash_links': links,
            'date_from': d_from.isoformat(),
            'date_to': d_to.isoformat(),
            'prev_from': p_from.isoformat(),
            'prev_to': p_to.isoformat(),
            'period_error': period_error,
            'trend_months': recent_months(6),
        },
    )


@login_required
@require_GET
@never_cache
def home_widget_api(request, name: str):
    """بيانات ودجت واحد من الرئيسية — يُفحص حق الشاشة المصدر لكل ودجت هنا."""
    import re
    from datetime import date

    from . import home_dashboard as hd
    from .oracle_stock import oracle_enabled, oracle_session

    access = _home_dashboard_access(request.user)
    if name not in access['widgets']:
        return JsonResponse({'ok': False, 'error': 'ودجت غير معروف.'}, status=404)
    if not access['widgets'][name]:
        return JsonResponse({'ok': False, 'error': 'لا تملك صلاحية هذا القسم.'}, status=403)

    try:
        d_from, d_to = _home_period(request)
    except ValidationError as exc:
        return JsonResponse({'ok': False, 'error': str(exc)}, status=400)

    today = date.today()
    period_key = (d_from.isoformat(), d_to.isoformat())
    period_ttl = hd._ttl_for(d_to, today)

    if name == 'trend':
        raw_month = str(request.GET.get('month') or '').strip()
        if not re.fullmatch(r'\d{4}-\d{2}', raw_month) or raw_month not in hd.recent_months(12, today):
            return JsonResponse({'ok': False, 'error': 'شهر غير صالح.'}, status=400)
        year, month = int(raw_month[:4]), int(raw_month[5:])
        series = set(access['trend_series'])
        m_to = hd.month_bounds(year, month, today)[1]
        key = (raw_month, ','.join(sorted(series)))
        ttl = hd._ttl_for(m_to, today)
        builder = lambda: hd.build_month_point(year, month, series)  # noqa: E731
    elif name == 'recent':
        types = set(access['recent_types'])
        key = (','.join(sorted(types)),)
        ttl = hd.RECENT_TTL
        builder = lambda: hd.build_recent_widget(types)  # noqa: E731
    elif name in ('inventory', 'shortage'):
        key = (today.isoformat(),)
        ttl = hd.LIVE_TTL
        builder = hd.build_inventory_widget if name == 'inventory' else hd.build_shortage_widget
    else:
        builders = {
            'sales': hd.build_sales_widget,
            'groups': hd.build_groups_widget,
            'top_items': hd.build_top_items_widget,
            'profit': hd.build_profit_widget,
            'turnover': hd.build_turnover_widget,
            'purchases': hd.build_purchases_widget,
        }
        fn = builders[name]
        key = period_key
        ttl = period_ttl
        builder = lambda: fn(d_from, d_to)  # noqa: E731

    if not oracle_enabled():
        return JsonResponse(
            {'ok': False, 'error': 'الاتصال بقاعدة البيانات غير مُعدّ.'}, status=503
        )

    def run():
        with oracle_session():
            return builder()

    try:
        data = hd.cached_widget(name, key, ttl, run)
    except Exception as exc:  # noqa: BLE001
        logger.warning('home widget %s failed: %s', name, exc)
        return JsonResponse(
            {'ok': False, 'error': 'تعذّر جلب البيانات الآن. حاول لاحقاً.'}, status=502
        )
    return JsonResponse(
        {
            'ok': True,
            'widget': name,
            'date_from': d_from.isoformat(),
            'date_to': d_to.isoformat(),
            'data': data,
        }
    )


def _clean_warehouse_name(code: str, raw_name: str) -> str:
    """يفصل اسم المخزن عن رقمه إن كان ملتصقاً في W_NAME مثل 64(مرتجعات اسكاي)."""
    code = str(code or '').strip()
    name = str(raw_name or '').strip() or code
    if not code or name == code:
        return name
    suffix = f'({code})'
    if name.endswith(suffix):
        name = name[: -len(suffix)].strip()
    wrapped = f'{code}('
    if name.startswith(wrapped) and name.endswith(')'):
        name = name[len(wrapped) : -1].strip()
    return name or code


def _parse_qty(value) -> float | None:
    if value is None or value == '':
        return None
    try:
        return float(str(value).replace(',', '').strip())
    except ValueError:
        return None


def _parse_sales_dates(raw_from: str | None, raw_to: str | None):
    """يحوّل تواريخ النموذج إلى date؛ الافتراضي يوم اليوم (من وإلى)."""
    from datetime import date, datetime

    today = date.today()

    def parse_one(raw: str | None, fallback: date) -> date:
        text = (raw or '').strip()
        if not text:
            return fallback
        try:
            return datetime.strptime(text[:10], '%Y-%m-%d').date()
        except ValueError as exc:
            raise ValidationError('صيغة التاريخ غير صحيحة. استخدم YYYY-MM-DD.') from exc

    d_from = parse_one(raw_from, today)
    d_to = parse_one(raw_to, today)
    if d_from > d_to:
        raise ValidationError('تاريخ البداية يجب أن يكون قبل تاريخ النهاية أو مساوياً له.')
    # حد أقصى معقول لتفادي استعلام ضخم
    if (d_to - d_from).days > 366:
        raise ValidationError('الفترة القصوى سنة واحدة.')
    return d_from, d_to

@login_required

@require_GET

@never_cache

@login_required
@require_GET
@never_cache
def browse_performance(request):
    """قياس الأداء — فلترة حقيقية ومقارنة فترتين."""
    from datetime import date as date_cls
    from datetime import datetime
    from datetime import timedelta

    error = ''
    insights = None
    branches: list[dict] = []
    groups: list[dict] = []
    active_system = str(request.GET.get('sys') or 'pos').strip().lower()
    if active_system not in ('pos', 'wholesale'):
        active_system = 'pos'
    selected_branch = str(request.GET.get('branch') or '').strip()
    selected_group = str(request.GET.get('group') or '').strip()
    compare_mode = str(request.GET.get('compare') or 'auto').strip().lower()
    if compare_mode not in ('auto', 'custom'):
        compare_mode = 'auto'

    systems = [
        {'key': 'pos', 'label': 'نقاط البيع'},
        {'key': 'wholesale', 'label': 'الآجل'},
    ]

    def _parse_one(raw: str | None, fallback: date_cls) -> date_cls:
        text = (raw or '').strip()
        if not text:
            return fallback
        try:
            return datetime.strptime(text[:10], '%Y-%m-%d').date()
        except ValueError as exc:
            raise ValidationError('صيغة التاريخ غير صحيحة.') from exc

    try:
        date_from, date_to = _parse_sales_dates(
            request.GET.get('date_from'),
            request.GET.get('date_to'),
        )
    except ValidationError as exc:
        today = date_cls.today()
        return render(
            request,
            'search/browse_performance.html',
            {
                'date_from': (request.GET.get('date_from') or '')[:10],
                'date_to': (request.GET.get('date_to') or '')[:10],
                'compare_from': (request.GET.get('compare_from') or '')[:10],
                'compare_to': (request.GET.get('compare_to') or '')[:10],
                'default_from': today.isoformat(),
                'default_to': today.isoformat(),
                'active_system': active_system,
                'selected_branch': selected_branch,
                'selected_group': selected_group,
                'compare_mode': compare_mode,
                'systems': systems,
                'branches': [],
                'groups': [],
                'insights': None,
                'error': str(exc),
            },
        )

    span_days = (date_to - date_from).days
    default_b_to = date_from - timedelta(days=1)
    default_b_from = default_b_to - timedelta(days=span_days)
    compare_from = None
    compare_to = None
    if compare_mode == 'custom':
        try:
            compare_from = _parse_one(request.GET.get('compare_from'), default_b_from)
            compare_to = _parse_one(request.GET.get('compare_to'), default_b_to)
            if compare_from > compare_to:
                raise ValidationError('فترة المقارنة: تاريخ البداية بعد النهاية.')
            if (compare_to - compare_from).days > 366:
                raise ValidationError('فترة المقارنة القصوى سنة واحدة.')
        except ValidationError as exc:
            error = str(exc)

    try:
        from .branches import fetch_income_branches
        from .oracle_stock import (
            SALES_SYSTEMS,
            fetch_sales_group_options,
            oracle_enabled,
            oracle_session,
        )
        from .sales_insights import build_performance_insights

        systems = [
            {'key': key, 'label': conf['label']}
            for key, conf in SALES_SYSTEMS.items()
        ]
        if not oracle_enabled():
            error = error or 'أوراكل غير مفعّل — لا يمكن قياس الأداء.'
        elif not error:
            with oracle_session():
                branches = sorted(
                    fetch_income_branches(),
                    key=lambda row: (
                        str(row.get('name') or ''),
                        str(row.get('code') or ''),
                    ),
                )
                branch_codes = {str(row.get('code') or '') for row in branches}
                if selected_branch and selected_branch not in branch_codes:
                    selected_branch = ''
                groups = fetch_sales_group_options()
                group_codes = {g['code'] for g in groups}
                if selected_group and selected_group not in group_codes:
                    selected_group = ''
                insights = build_performance_insights(
                    date_from,
                    date_to,
                    system=active_system,
                    branch_code=selected_branch,
                    group_code=selected_group,
                    compare_from=compare_from if compare_mode == 'custom' else None,
                    compare_to=compare_to if compare_mode == 'custom' else None,
                )
    except Exception as exc:  # noqa: BLE001
        logger.warning('browse_performance failed: %s', exc)
        error = f'تعذّر حساب قياس الأداء: {exc}'
        insights = None

    if compare_mode == 'custom' and compare_from and compare_to:
        compare_from_s = compare_from.isoformat()
        compare_to_s = compare_to.isoformat()
    else:
        compare_from_s = default_b_from.isoformat()
        compare_to_s = default_b_to.isoformat()

    return render(
        request,
        'search/browse_performance.html',
        {
            'date_from': date_from.isoformat(),
            'date_to': date_to.isoformat(),
            'compare_from': compare_from_s,
            'compare_to': compare_to_s,
            'default_from': date_from.isoformat(),
            'default_to': date_to.isoformat(),
            'active_system': active_system,
            'selected_branch': selected_branch,
            'selected_group': selected_group,
            'compare_mode': compare_mode,
            'systems': systems,
            'branches': branches,
            'groups': groups,
            'insights': insights,
            'error': error,
        },
    )


@login_required
@require_GET
@never_cache
def browse_sales(request):
    """تحليل المبيعات — الفروع فوراً، والمجموعات تُحمَّل لاحقاً عبر API."""
    from datetime import date
    from urllib.parse import urlencode

    from django.urls import reverse

    today = date.today()
    error = ''
    dashboard = None
    branches: list[dict] = []
    groups: list[dict] = []
    selected_branch = str(request.GET.get('branch') or '').strip()
    selected_group = str(request.GET.get('group') or '').strip()

    try:
        date_from, date_to = _parse_sales_dates(
            request.GET.get('date_from'),
            request.GET.get('date_to'),
        )
    except ValidationError as exc:
        return render(
            request,
            'search/browse_sales.html',
            {
                'date_from': (request.GET.get('date_from') or '')[:10],
                'date_to': (request.GET.get('date_to') or '')[:10],
                'default_from': today.isoformat(),
                'default_to': today.isoformat(),
                'selected_branch': selected_branch,
                'selected_group': selected_group,
                'branches': [],
                'groups': [],
                'dashboard': None,
                'error': str(exc),
                'browsed': False,
                'groups_api_url': '',
                'groups_month_api_url': '',
                'items_api_url': '',
                'activity_api_url': '',
            },
        )

    try:
        from .oracle_stock import (
            fetch_sales_group_options,
            fetch_warehouse_options,
            oracle_enabled,
            oracle_session,
        )
        from .sales_dashboard import build_sales_branches

        if not oracle_enabled():
            error = 'أوراكل غير مفعّل — لا يمكن تحليل المبيعات.'
        else:
            with oracle_session():
                warehouses = fetch_warehouse_options(active_only=True)
                groups = fetch_sales_group_options()
                branch_map: dict[str, str] = {}
                for w in warehouses:
                    brn = str(w.get('branch_code') or '').strip()
                    if brn:
                        branch_map[brn] = str(w.get('branch_name') or brn)
                branches = [
                    {'code': code, 'name': name}
                    for code, name in sorted(
                        branch_map.items(), key=lambda x: (x[1], x[0])
                    )
                ]
                branch_codes = {b['code'] for b in branches}
                group_codes = {g['code'] for g in groups}
                if selected_branch and selected_branch not in branch_codes:
                    selected_branch = ''
                if selected_group and selected_group not in group_codes:
                    selected_group = ''

            dashboard = build_sales_branches(
                date_from,
                date_to,
                branch_code=selected_branch,
                group_code=selected_group,
            )
    except Exception as exc:  # noqa: BLE001
        logger.warning('browse_sales failed: %s', exc)
        from .sales_dashboard import build_sales_branches_from_cache

        dashboard = build_sales_branches_from_cache(
            date_from,
            date_to,
            branch_code=selected_branch,
            group_code=selected_group,
        )
        if dashboard is not None:
            error = (
                'تعذّر الاتصال بأوراكل — عرض أرقام محفوظة مسبقاً (قد تكون أقل من أونكس الحي). '
                'فعّل VPN حتى يصل الجهاز إلى المنفذ 1521 ثم حدّث الصفحة.'
            )
        else:
            error = f'تعذّر تحليل المبيعات: {exc}'
            dashboard = None

    daily = None
    daily_error = ''
    if dashboard is not None and not dashboard.get('from_cache'):
        try:
            from .sales_dashboard import build_sales_daily

            daily = build_sales_daily(date_from, date_to, branch_code=selected_branch)
        except Exception as exc:  # noqa: BLE001
            logger.warning('sales daily table failed: %s', exc)
            daily_error = 'تعذّر جلب المبيعات اليومية الآن.'

    groups_api_url = ''
    groups_month_api_url = ''
    items_api_url = ''
    users_api_url = ''
    groups_seed = None
    if dashboard is not None:
        qs = {
            'date_from': date_from.isoformat(),
            'date_to': date_to.isoformat(),
        }
        if selected_branch:
            qs['branch'] = selected_branch
        if selected_group:
            qs['group'] = selected_group
        # بدون partial: عند اكتمال الشهور تُطابق المجموعات مع إجمالي جدول الفروع
        groups_api_url = f"{reverse('browse_sales_groups_api')}?{urlencode(qs)}"
        month_qs = {}
        if selected_branch:
            month_qs['branch'] = selected_branch
        if selected_group:
            month_qs['group'] = selected_group
        groups_month_api_url = reverse('browse_sales_groups_month_api')
        if month_qs:
            groups_month_api_url = (
                f"{groups_month_api_url}?{urlencode(month_qs)}"
            )
        items_api_url = f"{reverse('browse_sales_top_items_api')}?{urlencode(qs)}"
        users_api_url = f"{reverse('browse_sales_top_users_api')}?{urlencode(qs)}"
        # زرع فوري من الكاش — بلا حلقة شهور في الواجهة
        try:
            from .sales_dashboard import peek_sales_groups

            peeked = peek_sales_groups(
                date_from,
                date_to,
                branch_code=selected_branch,
                group_code=selected_group,
            )
            if peeked and peeked.get('rows'):
                groups_seed = {'ok': True, 'groups': peeked}
                dashboard['groups'] = {
                    'rows': peeked['rows'],
                    'totals': peeked['totals'],
                }
                dashboard['groups_pending'] = False
                dashboard['kpis']['group_sales'] = peeked['totals'][
                    'sales_total_display'
                ]
                dashboard['kpis']['group_count'] = peeked['totals'][
                    'group_count_display'
                ]
        except Exception:  # noqa: BLE001
            groups_seed = None

    return render(
        request,
        'search/browse_sales.html',
        {
            'date_from': date_from.isoformat(),
            'date_to': date_to.isoformat(),
            'default_from': today.isoformat(),
            'default_to': today.isoformat(),
            'selected_branch': selected_branch,
            'selected_group': selected_group,
            'branches': branches,
            'groups': groups,
            'dashboard': dashboard,
            'daily': daily,
            'daily_error': daily_error,
            'error': error,
            'browsed': dashboard is not None,
            'groups_api_url': groups_api_url,
            'groups_month_api_url': groups_month_api_url,
            'items_api_url': items_api_url,
            'users_api_url': users_api_url,
            'groups_seed': groups_seed,
        },
    )


@login_required
@require_GET
@never_cache
def browse_sales_groups_api(request):
    """تحميل لاحق لمبيعات المجموعات (تفاصيل الأصناف)."""
    try:
        date_from, date_to = _parse_sales_dates(
            request.GET.get('date_from'),
            request.GET.get('date_to'),
        )
    except ValidationError as exc:
        return JsonResponse({'ok': False, 'error': str(exc)}, status=400)

    branch_code = str(request.GET.get('branch') or '').strip()
    group_code = str(request.GET.get('group') or '').strip()

    try:
        from .oracle_stock import oracle_enabled
        from .sales_dashboard import build_sales_groups

        if not oracle_enabled():
            return JsonResponse(
                {'ok': False, 'error': 'أوراكل غير مفعّل.'},
                status=400,
            )
        # مطابقة إجمالي المجموعات مع صافي نقاط البيع (جدول الفروع)
        payload = build_sales_groups(
            date_from,
            date_to,
            branch_code=branch_code,
            group_code=group_code,
            reconcile=True,
        )
        return JsonResponse({'ok': True, 'groups': payload})
    except Exception as exc:  # noqa: BLE001
        logger.warning('browse_sales_groups_api failed: %s', exc)
        # 200 بدل 5xx حتى لا يحجب البروكسي الرسالة كـ HTTP 502
        return JsonResponse(
            {
                'ok': True,
                'groups': {
                    'rows': [],
                    'totals': {
                        'invoice_count_display': '0',
                        'qty_display': '0',
                        'sales_total_display': '0.00',
                        'group_count_display': '0',
                    },
                    'warning': str(exc)
                    or 'تعذّر جلب مبيعات المجموعات. أعد المحاولة بعد لحظات.',
                },
            }
        )


@login_required
@require_GET
@never_cache
def browse_sales_groups_month_api(request):
    """SQL لشهر واحد من مبيعات المجموعات → JSON (للجلب المتوازي من الواجهة)."""
    try:
        date_from, date_to = _parse_sales_dates(
            request.GET.get('date_from'),
            request.GET.get('date_to'),
        )
    except ValidationError as exc:
        return JsonResponse({'ok': False, 'error': str(exc)}, status=400)

    branch_code = str(request.GET.get('branch') or '').strip()
    group_code = str(request.GET.get('group') or '').strip()

    try:
        from .oracle_stock import oracle_enabled
        from .sales_dashboard import build_sales_groups_month

        if not oracle_enabled():
            return JsonResponse(
                {'ok': False, 'error': 'أوراكل غير مفعّل.'},
                status=400,
            )
        payload = build_sales_groups_month(
            date_from,
            date_to,
            branch_code=branch_code,
            group_code=group_code,
        )
        return JsonResponse({'ok': True, 'month': payload, 'source': 'sql'})
    except Exception as exc:  # noqa: BLE001
        logger.warning('browse_sales_groups_month_api failed: %s', exc)
        return JsonResponse(
            {
                'ok': False,
                'error': str(exc)
                or 'تعذّر جلب مبيعات مجموعات الشهر من أوراكل.',
            },
            status=200,
        )


@login_required
@require_GET
@never_cache
def browse_sales_top_items_api(request):
    """تحميل لاحق لأعلى أصناف الإرجاع من نقاط البيع."""
    try:
        date_from, date_to = _parse_sales_dates(
            request.GET.get('date_from'),
            request.GET.get('date_to'),
        )
    except ValidationError as exc:
        return JsonResponse({'ok': False, 'error': str(exc)}, status=400)

    branch_code = str(request.GET.get('branch') or '').strip()
    group_code = str(request.GET.get('group') or '').strip()

    try:
        from .oracle_stock import oracle_enabled
        from .sales_dashboard import build_sales_top_items

        if not oracle_enabled():
            return JsonResponse(
                {'ok': False, 'error': 'أوراكل غير مفعّل.'},
                status=400,
            )
        payload = build_sales_top_items(
            date_from,
            date_to,
            branch_code=branch_code,
            group_code=group_code,
            limit=20,
        )
        return JsonResponse({'ok': True, 'items': payload})
    except Exception as exc:  # noqa: BLE001
        logger.warning('browse_sales_top_items_api failed: %s', exc)
        return JsonResponse({'ok': False, 'error': str(exc)}, status=500)


@login_required
@require_GET
@never_cache
def browse_sales_top_users_api(request):
    """تحميل لاحق لأكثر المستخدمين مبيعاً."""
    try:
        date_from, date_to = _parse_sales_dates(
            request.GET.get('date_from'),
            request.GET.get('date_to'),
        )
    except ValidationError as exc:
        return JsonResponse({'ok': False, 'error': str(exc)}, status=400)

    branch_code = str(request.GET.get('branch') or '').strip()
    try:
        limit = int(request.GET.get('limit') or 15)
    except (TypeError, ValueError):
        limit = 15

    try:
        from .oracle_stock import oracle_enabled
        from .sales_dashboard import build_sales_top_users

        if not oracle_enabled():
            return JsonResponse(
                {'ok': False, 'error': 'أوراكل غير مفعّل.'},
                status=400,
            )
        payload = build_sales_top_users(
            date_from,
            date_to,
            branch_code=branch_code,
            limit=limit,
        )
        return JsonResponse({'ok': True, 'users': payload})
    except Exception as exc:  # noqa: BLE001
        logger.warning('browse_sales_top_users_api failed: %s', exc)
        return JsonResponse({'ok': False, 'error': str(exc)}, status=500)


@login_required
@require_GET
@never_cache
def browse_inventory(request):
    """تحليل المخزون — إجماليات حسب المخازن والمجموعات والفروع."""
    error = ''
    insights = None
    warehouses: list[dict] = []
    groups: list[dict] = []
    branches: list[dict] = []

    selected_warehouse = str(request.GET.get('warehouse') or '').strip()
    selected_group = str(request.GET.get('group') or '').strip()
    selected_branch = str(request.GET.get('branch') or '').strip()

    try:
        from .inventory_insights import build_inventory_insights
        from .oracle_stock import (
            fetch_sales_group_options,
            fetch_warehouse_options,
            oracle_enabled,
            oracle_session,
        )

        if not oracle_enabled():
            error = 'أوراكل غير مفعّل — لا يمكن تحليل المخزون.'
        else:
            with oracle_session():
                raw_warehouses = fetch_warehouse_options(active_only=True)
                groups = fetch_sales_group_options()
                wh_codes = {w['code'] for w in raw_warehouses}
                group_codes = {g['code'] for g in groups}
                if selected_warehouse and selected_warehouse not in wh_codes:
                    selected_warehouse = ''
                if selected_group and selected_group not in group_codes:
                    selected_group = ''

                branch_map: dict[str, str] = {}
                warehouses = []
                for w in raw_warehouses:
                    code = str(w.get('code') or '').strip()
                    if not code:
                        continue
                    brn = str(w.get('branch_code') or '').strip()
                    if brn:
                        branch_map[brn] = str(w.get('branch_name') or brn)
                    clean = _clean_warehouse_name(code, str(w.get('name') or ''))
                    warehouses.append(
                        {
                            'code': code,
                            'name': clean,
                            'branch_code': brn,
                            'branch_name': str(w.get('branch_name') or brn or '—'),
                        }
                    )
                branches = [
                    {'code': code, 'name': name}
                    for code, name in sorted(
                        branch_map.items(), key=lambda x: (x[1], x[0])
                    )
                ]
                if selected_branch and selected_branch not in branch_map:
                    selected_branch = ''

                # إن وُجد فرع محدد ومخزن خارج نطاقه — ألغِ المخزن
                if selected_branch and selected_warehouse:
                    allowed = {
                        w['code']
                        for w in warehouses
                        if str(w.get('branch_code') or '') == selected_branch
                    }
                    if selected_warehouse not in allowed:
                        selected_warehouse = ''

            # خارج الجلسة: الاستعلامات الثقيلة تعمل بالتوازي بجلسات مستقلة
            insights = build_inventory_insights(
                warehouse=selected_warehouse,
                group_code=selected_group,
                branch_code=selected_branch,
            )
    except Exception as exc:  # noqa: BLE001
        logger.warning('browse_inventory failed: %s', exc)
        msg = str(exc or '').lower()
        if 'interpreter shutdown' in msg or 'cannot schedule' in msg:
            # غالباً بعد حفظ ملف وإعادة تحميل runserver أثناء الطلب الطويل
            error = (
                'أُعيد تحميل الخادم أثناء التحليل — حدّث الصفحة وأعد المحاولة.'
            )
        else:
            error = f'تعذّر تحليل المخزون: {exc}'
        insights = None

    return render(
        request,
        'search/browse_inventory.html',
        {
            'selected_warehouse': selected_warehouse,
            'selected_group': selected_group,
            'selected_branch': selected_branch,
            'warehouses': warehouses,
            'groups': groups,
            'branches': branches,
            'insights': insights,
            'error': error,
        },
    )


@login_required
@require_GET
@never_cache
def browse_purchases(request):
    """تحليل فواتير المشتريات حسب الفرع والمجموعة والمورد."""
    from datetime import date

    today = date.today()
    month_start = today.replace(day=1)
    selected_branch = str(request.GET.get('branch') or '').strip()
    selected_group = str(request.GET.get('group') or '').strip()
    selected_vendor = str(request.GET.get('vendor') or '').strip()
    dashboard = None
    error = ''
    branches: list[dict] = []
    groups: list[dict] = []
    vendors: list[dict] = []

    try:
        date_from, date_to = _parse_sales_dates(
            request.GET.get('date_from'),
            request.GET.get('date_to'),
        )
    except ValidationError as exc:
        return render(
            request,
            'search/browse_purchases.html',
            {
                'date_from': (request.GET.get('date_from') or '')[:10],
                'date_to': (request.GET.get('date_to') or '')[:10],
                'default_from': month_start.isoformat(),
                'default_to': today.isoformat(),
                'selected_branch': selected_branch,
                'selected_group': selected_group,
                'selected_vendor': selected_vendor,
                'branches': [],
                'groups': [],
                'vendors': [],
                'dashboard': None,
                'error': str(exc),
            },
        )

    try:
        from .branches import fetch_income_branches
        from .oracle_purchases import (
            build_purchase_dashboard,
            fetch_purchase_vendor_options,
        )
        from .oracle_stock import (
            fetch_sales_group_options,
            oracle_enabled,
            oracle_session,
        )

        if not oracle_enabled():
            error = 'أوراكل غير مفعّل — لا يمكن تحليل المشتريات.'
        else:
            with oracle_session():
                branches = fetch_income_branches()
                groups = fetch_sales_group_options()
                vendors = fetch_purchase_vendor_options(date_from, date_to)
                if selected_branch not in {row['code'] for row in branches}:
                    selected_branch = ''
                if selected_group not in {row['code'] for row in groups}:
                    selected_group = ''
                if selected_vendor not in {row['code'] for row in vendors}:
                    selected_vendor = ''
                dashboard = build_purchase_dashboard(
                    date_from,
                    date_to,
                    branch_code=selected_branch,
                    group_code=selected_group,
                    vendor_code=selected_vendor,
                )
    except Exception as exc:  # noqa: BLE001
        logger.warning('browse_purchases failed: %s', exc)
        error = f'تعذّر تحليل المشتريات: {exc}'
        dashboard = None

    return render(
        request,
        'search/browse_purchases.html',
        {
            'date_from': date_from.isoformat(),
            'date_to': date_to.isoformat(),
            'default_from': month_start.isoformat(),
            'default_to': today.isoformat(),
            'selected_branch': selected_branch,
            'selected_group': selected_group,
            'selected_vendor': selected_vendor,
            'branches': branches,
            'groups': groups,
            'vendors': vendors,
            'dashboard': dashboard,
            'error': error,
        },
    )


def _oracle_connection_initial(cfg: dict) -> dict:
    from .oracle_connection import parse_year_schema

    schema = str(cfg.get('SCHEMA') or '').strip()
    parsed = parse_year_schema(schema)
    api_cfg = _api_cfg()
    return {
        'enabled': bool(cfg.get('ENABLED')),
        'host': str(cfg.get('HOST') or ''),
        'port': int(cfg.get('PORT') or 1521),
        'service_name': str(cfg.get('SERVICE_NAME') or ''),
        'sid': str(cfg.get('SID') or ''),
        'username': str(cfg.get('USER') or ''),
        'year': str(parsed[0]) if parsed else '',
        'unit': parsed[1] if parsed else '',
        'schema': '' if parsed else schema,
        'known_schemas': schema.upper() if parsed else '',
        'onyx_base_url': str(cfg.get('ONYX_BASE_URL') or ''),
        'default_warehouse': str(api_cfg.get('DEFAULT_WAREHOUSE') or ''),
        'compare_warehouses': ', '.join(api_cfg.get('COMPARE_WAREHOUSES') or []),
    }


def _saved_warehouse_options() -> list[dict]:
    """مخازن الربط المحفوظ من الكاش فقط — الشاشة لا تنتظر أوراكل عند فتحها (قد يكون منقطعاً)."""
    try:
        from .oracle_stock import fetch_warehouse_options, oracle_enabled

        if not oracle_enabled():
            return []
        return [
            {
                'code': str(w.get('code') or ''),
                'name': str(w.get('name') or ''),
                'branch_name': '' if w.get('branch_name') == '—' else str(w.get('branch_name') or ''),
            }
            for w in fetch_warehouse_options(active_only=True, cached_only=True)
            if w.get('code')
        ]
    except Exception as exc:  # noqa: BLE001
        logger.info('Saved warehouse options unavailable: %s', exc)
        return []


def _empty_discovery(error: str = '') -> dict:
    return {
        'ok': False,
        'error': error,
        'schemas': [],
        'selected': None,
        'warehouses': [],
        'warehouse_error': '',
        'onyx_base_url': '',
        'onyx_detected': False,
    }


def _discover_connection_data(form, candidate: dict) -> dict:
    """من بيانات الدخول وحدها: السنوات/الوحدات (مع اختيار الأحدث)، المخازن، ورابط خدمة أونكس."""
    from .api_client import detect_onyx_service_url
    from .oracle_connection import parse_year_schema
    from .oracle_stock import OracleStockError, discover_warehouses, discover_year_schemas

    result = _empty_discovery()
    try:
        schemas = discover_year_schemas(candidate)
    except OracleStockError as exc:
        result['error'] = str(exc)
        return result
    result['schemas'] = schemas

    chosen = str(form.cleaned_data.get('schema') or '').strip().upper()
    if not chosen or (parse_year_schema(chosen) and chosen not in {s['schema'] for s in schemas}):
        chosen = schemas[0]['schema'] if schemas else ''
    if not chosen:
        result['error'] = (
            'تم الاتصال، لكن لم يُعثر على مخططات أونكس بصيغة IAS + السنة + الوحدة '
            '(مثل IAS20261) متاحة لهذا المستخدم.'
        )
        return result
    parsed = parse_year_schema(chosen)
    result['ok'] = True
    result['selected'] = {
        'schema': chosen,
        'year': str(parsed[0]) if parsed else '',
        'unit': parsed[1] if parsed else '',
    }

    try:
        result['warehouses'] = discover_warehouses({**candidate, 'SCHEMA': chosen})
    except OracleStockError as exc:
        result['warehouse_error'] = str(exc)

    current_url = str(form.cleaned_data.get('onyx_base_url') or '').strip()
    if current_url:
        result['onyx_base_url'] = current_url
    else:
        detected = detect_onyx_service_url(candidate.get('HOST'))
        result['onyx_base_url'] = detected
        result['onyx_detected'] = bool(detected)
    return result


def _discover_summary(found: dict) -> str:
    if not found['ok']:
        return found['error'] or 'تعذّر جلب البيانات.'
    parts = [f"تم الاتصال — {len(found['schemas'])} سنة/وحدة (المختار: {found['selected']['schema']})"]
    if found['warehouse_error']:
        parts.append(f"المخازن: {found['warehouse_error']}")
    else:
        parts.append(f"{len(found['warehouses'])} مخزناً")
    if found['onyx_detected']:
        parts.append('واكتُشف رابط خدمة أونكس تلقائياً')
    elif not found['onyx_base_url']:
        parts.append('ولم تُكتشف خدمة أونكس تلقائياً — أدخل رابطها يدوياً إن وُجدت')
    return '، '.join(parts) + '. راجع الاختيارات ثم احفظ.'


def _form_error_text(form) -> str:
    for field, errors in form.errors.items():
        label = form.fields[field].label if field in form.fields else ''
        return f'{label}: {errors[0]}' if label else str(errors[0])
    return 'بيانات غير صالحة.'


@login_required
@never_cache
def oracle_connection_settings(request):
    """شاشة ربط أوراكل: اختبار بيانات الاتصال ثم حفظها وتفعيلها — لمدير النظام فقط."""
    from django.core.exceptions import PermissionDenied
    from django.core.exceptions import ValidationError as DjangoValidationError
    from django.http import HttpResponseBadRequest, HttpResponseNotAllowed
    from django.urls import reverse

    from .forms import OracleConnectionForm
    from .models import OracleConnection
    from .oracle_connection import (
        OracleConnectionError,
        build_candidate,
        connection_source,
        effective_oracle_config,
        save_connection,
    )
    from .api_client import ApiClientError, probe_onyx_service
    from .oracle_link import link_status
    from .oracle_stock import OracleStockError, probe_connection

    if not request.user.is_superuser:
        raise PermissionDenied
    if request.method not in ('GET', 'POST'):
        return HttpResponseNotAllowed(['GET', 'POST'])

    current = effective_oracle_config()
    has_saved_password = bool(current.get('PASSWORD'))
    row = (
        OracleConnection.objects.select_related('updated_by')
        .filter(pk=OracleConnection.SINGLETON_PK)
        .first()
    )
    test_result = None
    warehouse_options: list[dict] | None = None
    wants_json = request.headers.get('X-Requested-With') == 'fetch'

    if request.method == 'POST' and str(request.POST.get('action') or '') in ('link_generate', 'link_revoke'):
        return _oracle_link_action(request)

    if request.method == 'POST':
        action = str(request.POST.get('action') or '').strip()
        if action not in ('test', 'save', 'discover'):
            return HttpResponseBadRequest('إجراء غير معروف.', content_type='text/plain; charset=utf-8')
        form = OracleConnectionForm(
            request.POST,
            has_saved_password=has_saved_password,
            require_schema=action != 'discover',
        )
        if action == 'discover':
            if not form.is_valid():
                found = _empty_discovery(_form_error_text(form))
            else:
                candidate = build_candidate(
                    form.cleaned_data, form.cleaned_data.get('password') or None
                )
                found = _discover_connection_data(form, candidate)
            summary = _discover_summary(found)
            if wants_json:
                return JsonResponse({**found, 'message': summary})
            data = request.POST.copy()
            if found['schemas']:
                data['known_schemas'] = ','.join(s['schema'] for s in found['schemas'])
            if found['selected']:
                data['year'] = found['selected']['year']
                data['unit'] = found['selected']['unit']
            if found['onyx_detected']:
                data['onyx_base_url'] = found['onyx_base_url']
            warehouse_options = found['warehouses']
            form = OracleConnectionForm(data, has_saved_password=has_saved_password, require_schema=False)
            test_result = {'ok': bool(found['ok']), 'message': summary}
        elif form.is_valid():
            candidate = build_candidate(
                form.cleaned_data, form.cleaned_data.get('password') or None
            )
            probe_error, onyx_error = '', ''
            if action == 'test' or candidate['ENABLED']:
                try:
                    probe_connection(candidate)
                except OracleStockError as exc:
                    probe_error = str(exc)
            if candidate['ONYX_BASE_URL']:
                try:
                    probe_onyx_service(candidate['ONYX_BASE_URL'])
                except ApiClientError as exc:
                    onyx_error = str(exc)
            if action == 'test':
                parts = [
                    f'أوراكل: {probe_error}' if probe_error else
                    f'أوراكل: نجح الاتصال وقراءة IAS_ITM_MST من المخطط {candidate["SCHEMA"]}.'
                ]
                if candidate['ONYX_BASE_URL']:
                    parts.append(f'خدمة أونكس: {onyx_error or "تستجيب."}')
                test_result = {'ok': not probe_error and not onyx_error, 'message': ' — '.join(parts)}
                if wants_json:
                    return JsonResponse(test_result)
            elif probe_error:
                message = (
                    f'لم يُحفظ الربط — فشل اختبار أوراكل: {probe_error} '
                    'يمكنك الحفظ مع إيقاف أوراكل مؤقتاً (يُحفظ كل شيء ولا يُفعَّل ربط لم ينجح اختباره).'
                )
                if wants_json:
                    return JsonResponse({'ok': False, 'message': message, 'can_save_disabled': True})
                form.add_error(None, message)
            elif onyx_error:
                message = f'لم يُحفظ الربط — فشل اختبار خدمة أونكس: {onyx_error}'
                if wants_json:
                    return JsonResponse({'ok': False, 'message': message})
                form.add_error(None, message)
            else:
                try:
                    save_connection(candidate, user=request.user)
                except (OracleConnectionError, DjangoValidationError) as exc:
                    logger.warning('Oracle connection save rejected: %s', exc)
                    if wants_json:
                        return JsonResponse({'ok': False, 'message': f'تعذّر حفظ الربط: {exc}'})
                    form.add_error(None, f'تعذّر حفظ الربط: {exc}')
                else:
                    saved = [
                        'تم حفظ ربط أوراكل وتفعيله — التقارير تُقرأ الآن من هذا المصدر.'
                        if candidate['ENABLED']
                        else 'تم حفظ ربط أوراكل وهو معطّل — التقارير لن تقرأ من أوراكل حتى تفعيله.'
                    ]
                    if candidate['ONYX_BASE_URL']:
                        saved.append('خدمة أونكس تُقرأ الآن من الرابط المحفوظ.')
                    if candidate['DEFAULT_WAREHOUSE'] or candidate['COMPARE_WAREHOUSES']:
                        saved.append('تم حفظ إعدادات المخازن.')
                    messages.success(request, ' '.join(saved))
                    if wants_json:
                        return JsonResponse(
                            {'ok': True, 'redirect': reverse('oracle_connection_settings')}
                        )
                    return redirect('oracle_connection_settings')
        if wants_json:
            return JsonResponse({'ok': False, 'message': _form_error_text(form)})
    else:
        form = OracleConnectionForm(
            initial=_oracle_connection_initial(current),
            has_saved_password=has_saved_password,
        )

    if warehouse_options is None:
        warehouse_options = _saved_warehouse_options()

    return render(
        request,
        'search/oracle_connection.html',
        {
            'form': form,
            'test_result': test_result,
            'warehouse_options': warehouse_options,
            'source': connection_source(),
            'row': row,
            'oracle_enabled_now': bool(current.get('ENABLED')),
            'password_unreadable': (
                row is not None and bool(row.password_encrypted) and not has_saved_password
            ),
            'link': link_status(),
            # كلمة مرور أوراكل مشفّرة بمفتاح مشتق من SECRET_KEY — يجب أن يكون ثابتاً في الإنتاج
            'secret_key_unstable': not settings.DEBUG
            and not getattr(settings, 'SECRET_KEY_FROM_ENV', True),
        },
    )


def _oracle_link_public_host(request) -> str:
    configured = str(getattr(settings, 'ORACLE_LINK_PUBLIC_HOST', '') or '').strip()
    if configured:
        return configured
    host = request.get_host()
    if host.startswith('['):
        return host[1:].split(']', 1)[0]
    return host.rsplit(':', 1)[0] if host.count(':') == 1 else host


def _oracle_link_action(request):
    """توليد ملف وكيل الربط (zip لجهاز العميل) أو إلغاؤه — ضمن شاشة الربط لمدير النظام."""
    from django.http import HttpResponse

    from .oracle_link import OracleLinkError, generate_link_package, revoke_link

    action = request.POST.get('action')
    try:
        if action == 'link_generate':
            package = generate_link_package(_oracle_link_public_host(request))
            response = HttpResponse(package, content_type='application/zip')
            response['Content-Disposition'] = 'attachment; filename="OracleLink.zip"'
            response['Cache-Control'] = 'no-store'
            return response
        revoke_link()
        messages.success(request, 'تم إلغاء ملف الربط — جهاز العميل لن يستطيع الاتصال حتى تولّد ملفاً جديداً.')
    except OracleLinkError as exc:
        messages.error(request, str(exc))
    except OSError as exc:
        logger.warning('Oracle link action failed: %s', exc)
        messages.error(request, f'تعذّر تنفيذ العملية: {exc}')
    return redirect('oracle_connection_settings')
