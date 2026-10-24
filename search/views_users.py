"""شاشة المستخدمين: القائمة والإضافة والتعديل والحذف — لأدوار الإدارة (is_staff) فقط."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta

from django.contrib import messages
from django.contrib.auth import get_user_model, update_session_auth_hash
from django.contrib.auth.decorators import login_required
from django.db.models import Q
from django.http import HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods

from .forms import AppUserForm
from .models import UserActivitySession, UserScreenView
from .nav_permissions import (
    ROLE_DETAILS,
    assignable_roles,
    can_manage_user,
    user_role_name,
)

User = get_user_model()


def _staff_only(view):
    """إدارة المستخدمين لمدير النظام والسوبر أدمن فقط."""

    def wrapper(request, *args, **kwargs):
        if not request.user.is_staff:
            messages.error(request, 'إدارة المستخدمين متاحة لمدير النظام فقط.')
            return redirect('home')
        return view(request, *args, **kwargs)

    wrapper.__name__ = view.__name__
    wrapper.__doc__ = view.__doc__
    return wrapper


def _superuser_only(view):
    """سجل النشاط للسوبر أدمن فقط."""

    def wrapper(request, *args, **kwargs):
        if not request.user.is_superuser:
            messages.error(request, 'سجل النشاط متاح للسوبر أدمن فقط.')
            return redirect('home')
        return view(request, *args, **kwargs)

    wrapper.__name__ = view.__name__
    wrapper.__doc__ = view.__doc__
    return wrapper


def _visible_users(actor):
    """حسابات السوبر أدمن لا يراها إلا سوبر أدمن — مدير النظام لا يعرف بوجودها."""
    qs = User.objects.select_related('profile')
    if not actor.is_superuser:
        qs = qs.filter(is_superuser=False)
    return qs


def _role_cards(users: list, actor) -> list[dict]:
    counts: dict[str, int] = {}
    for u in users:
        counts[u.role] = counts.get(u.role, 0) + 1
    return [
        {
            'name': role,
            'count': counts.get(role, 0),
            **ROLE_DETAILS[role],
        }
        for role, _label in assignable_roles(actor)
    ]


def _role_options(form) -> list[dict]:
    """أزرار اختيار الدور مع وصف ما يفتحه كل دور."""
    return [
        {'radio': radio, 'details': ROLE_DETAILS.get(radio.data['value'])}
        for radio in form['role_name']
    ]


def _decorate(user, actor) -> None:
    """يضيف للمستخدم ما يحتاجه الجدول: الاسم والرقم والدور وصلاحية التعديل."""
    profile = getattr(user, 'profile', None)
    user.display = ((profile.display_name if profile else '') or user.first_name or user.username).strip()
    user.phone_display = ((profile.phone if profile else '') or '').strip()
    user.role = user_role_name(user) or 'بلا دور'
    details = ROLE_DETAILS.get(user.role)
    user.role_tone = details['tone'] if details else 'legacy'
    user.role_screens = details['screens'] if details else ()
    user.can_manage = can_manage_user(actor, user)
    user.is_self = user.pk == actor.pk
    user.initial = user.display[:1]


@login_required
@never_cache
@_staff_only
def user_list(request):
    """قائمة المستخدمين مع بطاقة لكل دور."""
    users = list(_visible_users(request.user).order_by('-is_active', '-is_superuser', '-is_staff', 'first_name'))
    for u in users:
        _decorate(u, request.user)
    query = (request.GET.get('q') or '').strip()
    role_filter = (request.GET.get('role') or '').strip()
    shown = users
    if role_filter:
        shown = [u for u in shown if u.role == role_filter]
    if query:
        q = query.lower()
        shown = [u for u in shown if q in u.display.lower() or q in u.phone_display]
    return render(
        request,
        'search/users_list.html',
        {
            'users': shown,
            'total': len(users),
            'active_count': sum(1 for u in users if u.is_active),
            'role_cards': _role_cards(users, request.user),
            'query': query,
            'role_filter': role_filter,
        },
    )


@login_required
@never_cache
@_staff_only
@require_http_methods(['GET', 'POST'])
def user_create(request):
    form = AppUserForm(request.POST or None, actor=request.user)
    if request.method == 'POST' and form.is_valid():
        user = form.save()
        messages.success(request, f'أُضيف المستخدم «{user.first_name}».')
        return redirect('user_list')
    return render(
        request,
        'search/user_form.html',
        {'form': form, 'is_new': True, 'role_options': _role_options(form)},
    )


@login_required
@never_cache
@_staff_only
@require_http_methods(['GET', 'POST'])
def user_edit(request, pk: int):
    target = get_object_or_404(_visible_users(request.user), pk=pk)
    if not can_manage_user(request.user, target):
        return HttpResponseForbidden('لا تملك صلاحية تعديل هذا الحساب.')
    form = AppUserForm(request.POST or None, instance=target, actor=request.user)
    if request.method == 'POST' and form.is_valid():
        user = form.save()
        if user.pk == request.user.pk and form.cleaned_data.get('password'):
            # تغيير كلمة سر الحساب الحالي لا يُخرج صاحبه
            update_session_auth_hash(request, user)
        messages.success(request, f'حُفظت بيانات «{user.first_name}».')
        return redirect('user_list')
    _decorate(target, request.user)
    return render(
        request,
        'search/user_form.html',
        {'form': form, 'is_new': False, 'target': target, 'role_options': _role_options(form)},
    )


@login_required
@never_cache
@_staff_only
@require_http_methods(['GET', 'POST'])
def user_delete(request, pk: int):
    target = get_object_or_404(_visible_users(request.user), pk=pk)
    if not can_manage_user(request.user, target):
        return HttpResponseForbidden('لا تملك صلاحية حذف هذا الحساب.')
    _decorate(target, request.user)
    error = ''
    if target.pk == request.user.pk:
        error = 'لا يمكنك حذف حسابك.'
    elif target.is_superuser and not User.objects.filter(is_superuser=True, is_active=True).exclude(pk=target.pk).exists():
        error = 'هذا آخر سوبر أدمن مفعّل؛ لا يمكن حذفه.'
    if request.method == 'POST' and not error:
        name = target.display
        target.delete()
        messages.success(request, f'حُذف المستخدم «{name}».')
        return redirect('user_list')
    return render(request, 'search/user_confirm_delete.html', {'target': target, 'error': error})


# ── سجل النشاط: دخول وخروج المستخدمين والشاشات التي فتحوها ──────────────

ONLINE_WINDOW = timedelta(minutes=15)
ACTIVITY_LIMIT = 200


def _device_label(user_agent: str) -> str:
    """وصف مختصر للمتصفح والنظام من User-Agent."""
    ua = user_agent or ''
    if not ua:
        return '—'
    if 'Edg/' in ua:
        browser = 'Edge'
    elif 'OPR/' in ua or 'Opera' in ua:
        browser = 'Opera'
    elif 'Firefox/' in ua:
        browser = 'Firefox'
    elif 'Chrome/' in ua:
        browser = 'Chrome'
    elif 'Safari/' in ua:
        browser = 'Safari'
    else:
        browser = 'متصفح'
    if 'iPhone' in ua or 'iPad' in ua:
        system = 'iOS'
    elif 'Android' in ua:
        system = 'Android'
    elif 'Windows' in ua:
        system = 'Windows'
    elif 'Mac OS' in ua:
        system = 'Mac'
    elif 'Linux' in ua:
        system = 'Linux'
    else:
        system = ''
    return f'{browser} · {system}' if system else browser


def _duration_label(delta) -> str:
    minutes = int(delta.total_seconds() // 60)
    if minutes < 1:
        return 'أقل من دقيقة'
    hours, mins = divmod(minutes, 60)
    if hours and mins:
        return f'{hours} س {mins} د'
    if hours:
        return f'{hours} س'
    return f'{mins} د'


def _parse_day(value: str, default):
    try:
        return date.fromisoformat((value or '').strip())
    except ValueError:
        return default


@login_required
@never_cache
@_superuser_only
def user_activity(request):
    """من دخل ومتى خرج وما الشاشات التي فتحها — للسوبر أدمن فقط."""
    today = timezone.localdate()
    date_to = _parse_day(request.GET.get('date_to'), today)
    date_from = _parse_day(request.GET.get('date_from'), today - timedelta(days=6))
    if date_from > date_to:
        date_from, date_to = date_to, date_from
    query = (request.GET.get('q') or '').strip()

    start = timezone.make_aware(datetime.combine(date_from, time.min))
    end = timezone.make_aware(datetime.combine(date_to + timedelta(days=1), time.min))

    sessions = UserActivitySession.objects.filter(login_at__gte=start, login_at__lt=end)
    views = UserScreenView.objects.filter(viewed_at__gte=start, viewed_at__lt=end)
    if query:
        sessions = sessions.filter(Q(user_name__icontains=query) | Q(user_phone__icontains=query))

    now = timezone.now()
    rows = list(sessions.select_related('user', 'user__profile').order_by('-login_at')[:ACTIVITY_LIMIT])

    keys = [r.session_key for r in rows if r.session_key]
    by_session: dict[str, list] = {}
    last_seen: dict[str, object] = {}
    for v in views.filter(session_key__in=keys).order_by('viewed_at'):
        by_session.setdefault(v.session_key, []).append(v)
        last_seen[v.session_key] = v.viewed_at

    for row in rows:
        screens: dict[str, int] = {}
        for v in by_session.get(row.session_key, []):
            screens[v.screen_label] = screens.get(v.screen_label, 0) + 1
        row.screens = list(screens.items())
        seen = last_seen.get(row.session_key) or row.login_at
        row.is_online = row.logout_at is None and now - seen <= ONLINE_WINDOW
        end_at = row.logout_at or (now if row.is_online else seen)
        row.duration = _duration_label(end_at - row.login_at)
        row.device = _device_label(row.user_agent)
        row.role = (user_role_name(row.user) if row.user else '') or '—'
        details = ROLE_DETAILS.get(row.role)
        row.tone = details['tone'] if details else 'legacy'
        row.initial = (row.user_name or '؟')[:1]

    day_start = timezone.make_aware(datetime.combine(today, time.min))
    base_sessions = UserActivitySession.objects.all()
    base_views = UserScreenView.objects.all()
    recent_keys = base_views.filter(viewed_at__gte=now - ONLINE_WINDOW).values('session_key')
    online_now = (
        base_sessions.filter(logout_at__isnull=True, session_key__in=recent_keys)
        .values('user')
        .distinct()
        .count()
    )

    return render(
        request,
        'search/user_activity.html',
        {
            'rows': rows,
            'query': query,
            'date_from': date_from.isoformat(),
            'date_to': date_to.isoformat(),
            'truncated': len(rows) >= ACTIVITY_LIMIT,
            'cards': [
                {'name': 'متصلون الآن', 'count': online_now, 'summary': 'نشطون خلال آخر 15 دقيقة', 'tone': 'purchase'},
                {
                    'name': 'دخول اليوم',
                    'count': base_sessions.filter(login_at__gte=day_start).count(),
                    'summary': 'عمليات تسجيل الدخول منذ منتصف الليل',
                    'tone': 'system',
                },
                {
                    'name': 'شاشات فُتحت اليوم',
                    'count': base_views.filter(viewed_at__gte=day_start).count(),
                    'summary': 'كل فتحة لشاشة تُحسب مرة كل دقيقة',
                    'tone': 'finance',
                },
                {
                    'name': 'مستخدمون هذا الأسبوع',
                    'count': base_sessions.filter(login_at__gte=now - timedelta(days=7)).values('user').distinct().count(),
                    'summary': 'دخلوا خلال آخر 7 أيام',
                    'tone': 'super',
                },
            ],
        },
    )
