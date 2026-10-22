"""
عميل API نظام أونكس: الأسعار + مزامنة الأصناف/الباركود.
"""

from __future__ import annotations

import logging
import time

import requests
from django.conf import settings
from django.db import transaction
from django.utils import timezone

logger = logging.getLogger(__name__)


class ApiClientError(Exception):
    """خطأ عام عند فشل الاتصال أو قراءة الاستجابة."""


def _api_cfg() -> dict:
    """EXTERNAL_API مع رابط وسنة أونكس والمخازن المحفوظة من شاشة الربط."""
    from urllib.parse import urlparse

    from .oracle_connection import screen_onyx_base_url, screen_onyx_year, screen_warehouses

    cfg = settings.EXTERNAL_API
    year = screen_onyx_year()
    base_url = screen_onyx_base_url()
    default_wh, compare_whs = screen_warehouses()
    if not year and not base_url and not default_wh and not compare_whs:
        return cfg
    cfg = dict(cfg)
    if year:
        for key in ('ITEMS_PARAMS', 'EXTRA_PARAMS'):
            if key in cfg:
                cfg[key] = {**(cfg.get(key) or {}), 'year': year}
    if default_wh:
        cfg['DEFAULT_WAREHOUSE'] = default_wh
        if 'EXTRA_PARAMS' in cfg:
            cfg['EXTRA_PARAMS'] = {**(cfg.get('EXTRA_PARAMS') or {}), 'price_w_code': default_wh}
    if compare_whs:
        cfg['COMPARE_WAREHOUSES'] = compare_whs
    if base_url:
        cfg['BASE_URL'] = base_url
        host = (urlparse(base_url).hostname or '').lower()
        # الرابط أدخله مدير النظام من الشاشة بعد التحقق من صيغته — مضيفه مسموح
        cfg['ALLOWED_HOSTS'] = [*(cfg.get('ALLOWED_HOSTS') or []), host]
    return cfg


def default_warehouse() -> str:
    from .oracle_connection import default_warehouse_code

    return str(_api_cfg().get('DEFAULT_WAREHOUSE') or default_warehouse_code() or '').strip()


def probe_onyx_service(base_url: str) -> None:
    """يتأكد أن رابط خدمة أونكس يستجيب قبل حفظه (طلب GET واحد، بلا تحويلات)."""
    url = str(base_url or '').strip().rstrip('/')
    try:
        response = requests.get(url, timeout=15, allow_redirects=False)
    except requests.Timeout as exc:
        raise ApiClientError('خدمة أونكس لم ترد خلال 15 ثانية — تحقق من العنوان والمنفذ.') from exc
    except requests.RequestException as exc:
        raise ApiClientError('تعذّر الوصول لخدمة أونكس — تحقق من العنوان والمنفذ.') from exc
    if response.status_code >= 400:
        raise ApiClientError(
            f'خدمة أونكس ردّت بالخطأ {response.status_code} — تحقق من مسار الخدمة '
            '(مثل /Service/OnyxService.svc).'
        )


# مسار خدمة أونكس القياسي على سيرفر IIS نفسه الذي يستضيف أوراكل
ONYX_SERVICE_PORT = 8090
ONYX_SERVICE_PATH = '/Service/OnyxService.svc'


def detect_onyx_service_url(oracle_host: str) -> str:
    """رابط خدمة أونكس على نفس سيرفر أوراكل إن استجاب، وإلا ''.

    oracle_host تحقق منه نموذج الشاشة (اسم مضيف/IP بلا مسار)، والمنفذ والمسار ثابتان.
    """
    host = str(oracle_host or '').strip()
    if not host:
        return ''
    url = f'http://{host}:{ONYX_SERVICE_PORT}{ONYX_SERVICE_PATH}'
    try:
        response = requests.get(url, timeout=6, allow_redirects=False)
    except requests.RequestException:
        return ''
    return url if response.status_code < 400 else ''


def _base_url() -> str:
    """يرجع رابط الأساس بعد التحقق من المضيف المسموح (حماية من SSRF)."""
    from urllib.parse import urlparse

    cfg = _api_cfg()
    base_url = (cfg.get('BASE_URL') or '').rstrip('/')
    if not base_url:
        raise ApiClientError('لم يُضبط رابط خدمة أونكس — أدخله من شاشة «ربط أوراكل».')

    parsed = urlparse(base_url)
    if parsed.scheme not in {'http', 'https'}:
        raise ApiClientError('بروتوكول رابط الـ API غير مسموح.')
    host = (parsed.hostname or '').lower()
    allowed = {h.lower() for h in (cfg.get('ALLOWED_HOSTS') or []) if h}
    if allowed and host not in allowed:
        raise ApiClientError('مضيف الـ API غير مدرج في القائمة المسموحة.')
    return base_url


def _safe_url(path: str) -> str:
    """يبني رابطًا داخل نفس المضيف المسموح فقط."""
    base = _base_url()
    if not path.startswith('/'):
        path = '/' + path
    # منع الخروج عن المسار الأساسي عبر ../ أو روابط مطلقة
    if '://' in path or '..' in path:
        raise ApiClientError('مسار API غير صالح.')
    return f'{base}{path}'


def _headers() -> dict[str, str]:
    cfg = _api_cfg()
    headers = {'Accept': 'application/json'}
    api_key = cfg.get('API_KEY') or ''
    if api_key:
        header_name = cfg.get('API_KEY_HEADER') or 'Authorization'
        prefix = (cfg.get('API_KEY_PREFIX') or '').strip()
        headers[header_name] = f'{prefix} {api_key}'.strip() if prefix else api_key
    return headers


def _request_get(url: str, params: dict, timeout: int | None = None, retries: int | None = None) -> requests.Response:
    """GET مع إعادة محاولة عند البطء أو انقطاع الشبكة."""
    cfg = _api_cfg()
    timeout = timeout if timeout is not None else cfg.get('TIMEOUT', 90)
    retries = int(cfg.get('RETRIES', 2) if retries is None else retries)
    last_exc: Exception | None = None

    for attempt in range(retries + 1):
        try:
            response = requests.get(url, params=params, headers=_headers(), timeout=timeout)
            response.raise_for_status()
            return response
        except requests.Timeout as exc:
            last_exc = exc
            logger.warning('API timeout attempt %s/%s url=%s', attempt + 1, retries + 1, url)
        except requests.RequestException as exc:
            last_exc = exc
            logger.warning('API error attempt %s/%s url=%s err=%s', attempt + 1, retries + 1, url, exc)

        if attempt < retries:
            time.sleep(1.5 * (attempt + 1))

    if isinstance(last_exc, requests.Timeout):
        raise ApiClientError(
            'انتهت مهلة الاتصال بالنظام. الشبكة بطيئة أو الخدمة مشغولة — أعد المحاولة.'
        ) from last_exc
    raise ApiClientError(f'فشل الاتصال بالنظام: {last_exc}') from last_exc


def fetch_all_items(*, g_code: str | None = None, subg_code: str | None = None) -> list[dict]:
    """جلب الأصناف من GetAllItems، مع تصفية اختيارية بالمجموعة/الفرعية."""
    cfg = _api_cfg()
    url = _safe_url('/GetAllItems')
    timeout = cfg.get('ITEMS_TIMEOUT', 180)
    params = dict(cfg.get('ITEMS_PARAMS') or {'year': 2026, 'active': 1})
    if g_code:
        params['g_code'] = str(g_code).strip()
    if subg_code:
        params['subg_code'] = str(subg_code).strip()

    response = _request_get(url, params, timeout=timeout)

    try:
        payload = response.json()
    except ValueError as exc:
        raise ApiClientError('استجابة GetAllItems ليست JSON صالحًا.') from exc

    if not isinstance(payload, list):
        raise ApiClientError('شكل GetAllItems غير متوقع.')
    return payload


def fetch_all_groups() -> list[dict]:
    """جلب مجموعات الأصناف من GetAllGroupDet."""
    cfg = _api_cfg()
    url = _safe_url('/GetAllGroupDet')
    timeout = cfg.get('TIMEOUT', 60)
    params = {
        'year': (cfg.get('EXTRA_PARAMS') or {}).get('year', 2026),
        'active': (cfg.get('EXTRA_PARAMS') or {}).get('active', 1),
    }
    response = _request_get(url, params, timeout=timeout)
    try:
        payload = response.json()
    except ValueError as exc:
        raise ApiClientError('استجابة GetAllGroupDet ليست JSON صالحًا.') from exc
    if not isinstance(payload, list):
        raise ApiClientError('شكل GetAllGroupDet غير متوقع.')
    return payload


def sync_barcode_index() -> int:
    """
    مزامنة الباركود/العبوات/رمز المجموعة من GetAllItems
    وأسماء المجموعات من GetAllGroupDet.

    الصفوف تُحفظ كما في المصدر (بما فيها التكرار واختلاف شكل الأحرف).
    """
    from .models import ItemBarcode, ItemGroup

    rows = fetch_all_items()
    mapped: list[ItemBarcode] = []

    for row in rows:
        if not isinstance(row, dict):
            continue
        # كما المصدر: قص الطول فقط لحدود الأعمدة.
        barcode = str(row.get('Barcode') or '').strip()[:128]
        item_code = str(row.get('Item_code') or '').strip()[:64]
        unit = str(row.get('itm_unt') or '').strip()[:64]
        if not item_code or not unit:
            continue

        g_raw = (
            row.get('Item_category')
            if row.get('Item_category') is not None
            else row.get('G_CODE')
            if row.get('G_CODE') is not None
            else row.get('g_code')
        )
        g_code = str('' if g_raw is None else g_raw).strip()[:64]

        pack_raw = (
            row.get('p_size')
            if row.get('p_size') is not None
            else row.get('P_SIZE')
            if row.get('P_SIZE') is not None
            else row.get('Pack_Size')
        )
        pack_size = '' if pack_raw is None else str(pack_raw).strip()

        mapped.append(
            ItemBarcode(
                barcode=barcode,
                item_code=item_code,
                name=str(row.get('Item_ar_name') or '').strip()[:255],
                unit=unit,
                pack_size=pack_size[:32],
                g_code=g_code,
            )
        )

    groups: list[ItemGroup] = []
    try:
        group_rows = fetch_all_groups()
        seen_g: set[str] = set()
        for row in group_rows:
            if not isinstance(row, dict):
                continue
            g_code = str(row.get('G_CODE') or row.get('g_code') or '').strip()
            if not g_code or g_code in seen_g:
                continue
            seen_g.add(g_code)
            groups.append(
                ItemGroup(
                    g_code=g_code,
                    g_name=str(row.get('G_A_NAME') or row.get('G_E_NAME') or '').strip(),
                )
            )
    except ApiClientError as exc:
        logger.warning('Group sync skipped: %s', exc)

    with transaction.atomic():
        ItemBarcode.objects.all().delete()
        ItemBarcode.objects.bulk_create(mapped, batch_size=2000)
        if groups:
            ItemGroup.objects.all().delete()
            ItemGroup.objects.bulk_create(groups, batch_size=500)

    logger.info('Synced %s barcode/unit rows and %s groups', len(mapped), len(groups))
    with_pack = sum(1 for m in mapped if m.pack_size)
    with_g = sum(1 for m in mapped if m.g_code)
    logger.info('Meta filled: pack=%s g_code=%s groups=%s', with_pack, with_g, len(groups))
    return len(mapped)


