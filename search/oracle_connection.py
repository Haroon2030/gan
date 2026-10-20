"""
ربط أوراكل القابل للتعديل من الشاشة.

المصدر الوحيد لبيانات العميل: صف OracleConnection المحفوظ من الشاشة. settings.ORACLE
وEXTERNAL_API لا تحمل بيانات عميل (فارغة)، فبلا صف يبقى الربط غير مضبوط.
الصف يحمل أيضاً رابط خدمة أونكس والمخازن، ومفتاح التفعيل فيه يحدد مصدر الكميات.
مهلات الشبكة والمجمّع ومسار Instant Client تبقى من .env دائماً.

تزامن عمليات السيرفر: الحفظ يكتب ملف ختم في DATA_DIR، وكل عملية تعيد قراءة
الصف فقط عند تغيّر الختم — لا استعلام قاعدة بيانات مع كل اتصال أوراكل
(الاستعلامات تُنفَّذ من خيوط متوازية وكل خيط يفتح اتصال DB خاصاً به).
تعديل الصف مباشرة من قاعدة البيانات دون الشاشة لا يُلتقط حتى إعادة التشغيل.
"""

from __future__ import annotations

import base64
import hashlib
import logging
import os
import re
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from django.conf import settings
from django.db import transaction

logger = logging.getLogger(__name__)

_IDENTITY_KEYS = ("HOST", "PORT", "SERVICE_NAME", "SID", "USER", "SCHEMA")

# مخطط أونكس لكل سنة ووحدة محاسبية: IAS + السنة (4 أرقام) + رقم الوحدة — مثل IAS20261
_YEAR_SCHEMA_RE = re.compile(r"^IAS(\d{4})(\d{1,3})$", re.IGNORECASE)
MIN_YEAR = 2000
MAX_YEAR = 2100


class OracleConnectionError(Exception):
    """فشل قراءة/حفظ إعدادات الربط."""


@dataclass(frozen=True)
class _Snapshot:
    overlay: dict[str, Any] = field(default_factory=dict)
    data_version: int = 0
    has_row: bool = False


_lock = threading.Lock()
_state: dict[str, Any] = {"snap": None, "stamp": None}


def _fernet():
    from cryptography.fernet import Fernet

    secret = (
        str(getattr(settings, "ORACLE_CONNECTION_SECRET", "") or "").strip()
        or str(settings.SECRET_KEY)
    )
    digest = hashlib.sha256(f"oracle-connection:{secret}".encode("utf-8")).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def encrypt_secret(value: str) -> str:
    return _fernet().encrypt(str(value).encode("utf-8")).decode("ascii")


def decrypt_secret(token: str) -> str:
    from cryptography.fernet import InvalidToken

    if not token:
        return ""
    try:
        return _fernet().decrypt(token.encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError) as exc:
        raise OracleConnectionError(
            "تعذّر فك تشفير كلمة مرور أوراكل (تغيّر مفتاح التشفير). أعد إدخالها من شاشة الربط."
        ) from exc


def _stamp_path() -> Path:
    return Path(settings.ORACLE_CONNECTION_STAMP_FILE)


def _read_stamp() -> int | None:
    try:
        return _stamp_path().stat().st_mtime_ns
    except OSError:
        return None


def _write_stamp(data_version: int) -> None:
    path = _stamp_path()
    tmp = path.with_suffix(".tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(f"{data_version}:{time.time_ns()}", encoding="utf-8")
        os.replace(tmp, path)
    except OSError as exc:
        logger.warning(
            "Oracle connection stamp write failed (%s): other workers keep the old "
            "connection until restart",
            exc,
        )


def _row_overlay(row) -> dict[str, Any]:
    try:
        password = decrypt_secret(row.password_encrypted)
    except OracleConnectionError as exc:
        logger.error("%s", exc)
        password = ""
    return {
        "ENABLED": bool(row.enabled),
        "HOST": row.host,
        "PORT": int(row.port),
        "SERVICE_NAME": row.service_name,
        "SID": row.sid,
        "USER": row.username,
        "PASSWORD": password,
        "SCHEMA": row.schema,
        "ONYX_BASE_URL": row.onyx_base_url,
        "DEFAULT_WAREHOUSE": row.default_warehouse,
        "COMPARE_WAREHOUSES": split_warehouses(row.compare_warehouses),
    }


def split_warehouses(raw: Any) -> list[str]:
    if isinstance(raw, (list, tuple)):
        raw = ",".join(str(p) for p in raw)
    seen: list[str] = []
    for part in str(raw or "").replace("،", ",").split(","):
        code = part.strip()
        if code and code not in seen:
            seen.append(code)
    return seen


def _load_snapshot() -> _Snapshot:
    try:
        from .models import OracleConnection

        row = OracleConnection.objects.filter(pk=OracleConnection.SINGLETON_PK).first()
    except Exception as exc:  # noqa: BLE001 — قبل migrate أو قبل جاهزية التطبيقات
        logger.debug("Oracle connection row unavailable: %s", exc)
        return _Snapshot()
    if row is None:
        return _Snapshot()
    return _Snapshot(
        overlay=_row_overlay(row),
        data_version=int(row.data_version or 0),
        has_row=True,
    )


def _snapshot() -> _Snapshot:
    stamp = _read_stamp()
    snap = _state["snap"]
    if snap is not None and _state["stamp"] == stamp:
        return snap
    with _lock:
        snap = _state["snap"]
        if snap is not None and _state["stamp"] == stamp:
            return snap
        snap = _load_snapshot()
        _state["snap"] = snap
        _state["stamp"] = stamp
        return snap


def invalidate() -> None:
    with _lock:
        _state["snap"] = None
        _state["stamp"] = None


def effective_oracle_config() -> dict[str, Any]:
    cfg = dict(getattr(settings, "ORACLE", {}) or {})
    cfg.update(_snapshot().overlay)
    return cfg


def env_oracle_config() -> dict[str, Any]:
    return dict(getattr(settings, "ORACLE", {}) or {})


def env_onyx_base_url() -> str:
    return str((getattr(settings, "EXTERNAL_API", {}) or {}).get("BASE_URL") or "").strip()


def screen_onyx_base_url() -> str:
    """رابط خدمة أونكس المحفوظ من الشاشة؛ '' = غير مضبوط."""
    snap = _snapshot()
    if not snap.has_row:
        return ""
    return str(snap.overlay.get("ONYX_BASE_URL") or "").strip()


def screen_warehouses() -> tuple[str, list[str]]:
    """(المخزن الافتراضي، مخازن المقارنة) من الشاشة؛ ('', []) قبل ضبط الربط."""
    snap = _snapshot()
    if not snap.has_row:
        return "", []
    return (
        str(snap.overlay.get("DEFAULT_WAREHOUSE") or "").strip(),
        list(snap.overlay.get("COMPARE_WAREHOUSES") or []),
    )


def default_warehouse_code() -> str:
    """المخزن الافتراضي من الشاشة، وإلا أول مخزن مقارنة؛ '' قبل ضبط الربط."""
    default_wh, compare = screen_warehouses()
    if default_wh:
        return default_wh
    base = getattr(settings, "EXTERNAL_API", {}) or {}
    fallback = str(base.get("DEFAULT_WAREHOUSE") or "").strip()
    if fallback:
        return fallback
    return compare[0] if compare else ""


def stock_qty_source() -> str:
    """oracle | api. مع ربط محفوظ من الشاشة يتبع مفتاح تفعيل أوراكل فيها، وإلا STOCK_QTY_SOURCE."""
    snap = _snapshot()
    if snap.has_row:
        return "oracle" if snap.overlay.get("ENABLED") else "api"
    return (getattr(settings, "STOCK_QTY_SOURCE", "api") or "api").strip().lower()


def connection_source() -> str:
    """'screen' عند وجود صف محفوظ، وإلا 'env'."""
    return "screen" if _snapshot().has_row else "env"


def current_data_version() -> int:
    return _snapshot().data_version


def parse_year_schema(schema: Any) -> tuple[int, str] | None:
    """(السنة، رقم الوحدة) من اسم مخطط أونكس، أو None إن لم يكن بهذه الصيغة."""
    match = _YEAR_SCHEMA_RE.match(str(schema or "").strip())
    if not match:
        return None
    year = int(match.group(1))
    if not MIN_YEAR <= year <= MAX_YEAR:
        return None
    return year, match.group(2)


def year_schema_name(year: int, unit: str) -> str:
    return f"IAS{int(year)}{unit}"


def screen_onyx_year() -> int | None:
    """سنة خدمة أونكس المأخوذة من مخطط الربط المحفوظ من الشاشة؛ None = السنة الحالية."""
    snap = _snapshot()
    if not snap.has_row:
        return None
    parsed = parse_year_schema(snap.overlay.get("SCHEMA"))
    return parsed[0] if parsed else None


def cache_key(key: str, key_prefix: str, version: int) -> str:
    """KEY_FUNCTION للكاش: يعزل نتائج كل مصدر بيانات عن الآخر.

    بلا ربط من الشاشة (أو بنفس هوية .env) يبقى الشكل الافتراضي لـ Django
    فلا يضيع الكاش الدافئ الموجود.
    """
    data_version = current_data_version()
    if not data_version:
        return f"{key_prefix}:{version}:{key}"
    return f"{key_prefix}:{version}:oc{data_version}:{key}"


def identity(cfg: dict[str, Any]) -> tuple:
    """هوية مصدر البيانات — تغيّرها يعني بيانات عميل/قاعدة أخرى."""
    return (
        str(cfg.get("HOST") or "").strip().lower(),
        int(cfg.get("PORT") or 1521),
        str(cfg.get("SERVICE_NAME") or "").strip().upper(),
        str(cfg.get("SID") or "").strip().upper(),
        str(cfg.get("USER") or "").strip().upper(),
        str(cfg.get("SCHEMA") or "").strip().upper(),
        str(cfg.get("ONYX_BASE_URL") or env_onyx_base_url()).strip().rstrip("/").lower(),
    )


def build_candidate(values: dict[str, Any], password: str | None) -> dict[str, Any]:
    """إعدادات مرشّحة للاختبار/الحفظ. كلمة مرور فارغة = الإبقاء على الحالية."""
    cfg = effective_oracle_config()
    cfg.update(
        {
            "ENABLED": bool(values.get("enabled")),
            "HOST": values["host"],
            "PORT": int(values["port"]),
            "SERVICE_NAME": values.get("service_name") or "",
            "SID": values.get("sid") or "",
            "USER": values["username"],
            "SCHEMA": values.get("schema") or "",
            "ONYX_BASE_URL": values.get("onyx_base_url") or "",
            "DEFAULT_WAREHOUSE": values.get("default_warehouse") or "",
            "COMPARE_WAREHOUSES": split_warehouses(values.get("compare_warehouses")),
        }
    )
    if password:
        cfg["PASSWORD"] = password
    return cfg


def save_connection(candidate: dict[str, Any], *, user=None):
    """يحفظ الربط ويعزل الكاش عند تغيّر مصدر البيانات ويعيد إنشاء مجمّع الاتصالات."""
    from .models import OracleConnection

    if not candidate.get("PASSWORD"):
        raise OracleConnectionError("كلمة مرور أوراكل مطلوبة.")

    env_identity = identity(env_oracle_config())
    new_identity = identity(candidate)
    with transaction.atomic():
        row = (
            OracleConnection.objects.select_for_update()
            .filter(pk=OracleConnection.SINGLETON_PK)
            .first()
        )
        if row is None:
            data_version = 0 if new_identity == env_identity else 1
            row = OracleConnection(pk=OracleConnection.SINGLETON_PK)
        else:
            previous = identity(
                {
                    "HOST": row.host,
                    "PORT": row.port,
                    "SERVICE_NAME": row.service_name,
                    "SID": row.sid,
                    "USER": row.username,
                    "SCHEMA": row.schema,
                    "ONYX_BASE_URL": row.onyx_base_url,
                }
            )
            data_version = int(row.data_version or 0) + (1 if previous != new_identity else 0)
        row.enabled = bool(candidate.get("ENABLED"))
        row.host = candidate["HOST"]
        row.port = int(candidate["PORT"])
        row.service_name = candidate.get("SERVICE_NAME") or ""
        row.sid = candidate.get("SID") or ""
        row.username = candidate["USER"]
        row.password_encrypted = encrypt_secret(candidate["PASSWORD"])
        row.schema = candidate["SCHEMA"]
        row.onyx_base_url = candidate.get("ONYX_BASE_URL") or ""
        row.default_warehouse = candidate.get("DEFAULT_WAREHOUSE") or ""
        row.compare_warehouses = ",".join(split_warehouses(candidate.get("COMPARE_WAREHOUSES")))
        row.data_version = data_version
        row.updated_by = user if getattr(user, "is_authenticated", False) else None
        row.full_clean()
        row.save()

    invalidate()
    _write_stamp(data_version)
    from .oracle_stock import _reset_pool

    _reset_pool()
    logger.info(
        "Oracle connection saved by %s: %s enabled=%s data_version=%s",
        getattr(user, "username", "?"),
        row,
        row.enabled,
        data_version,
    )
    return row
