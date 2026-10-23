"""صلاحيات الأقسام الرئيسية والشاشات الفرعية للشريط الجانبي."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

# شاشات تفصيلية تُحسب ضمن الشاشة الأم عند الحجب
SCREEN_ALIASES: dict[str, str] = {}

# مسارات بلا قسم (دائماً مسموحة للمُسجّل) أو تُدار بـ is_staff
ALWAYS_ALLOWED: frozenset[str] = frozenset(
    {
        "home",
        "login",
        "logout",
    }
)

STAFF_ONLY: frozenset[str] = frozenset(
    {
        "user_list",
        "user_create",
        "user_edit",
        "user_delete",
        "user_activity",
        "user_permissions",
        # الشاشة نفسها تشترط is_superuser أيضاً
        "oracle_connection_settings",
    }
)

# الأدوار المعتمدة في شاشة المستخدمين
ROLE_SUPER_ADMIN = "سوبر أدمن"
ROLE_SYSTEM_MANAGER = "مدير النظام"
ROLE_FINANCE_MANAGER = "مدير مالي"
ROLE_PURCHASE_MANAGER = "مدير شراء"

# أدوار الإدارة: is_staff (كل الشاشات + المستخدمين)، والسوبر أدمن is_superuser أيضاً (ربط أوراكل)
STAFF_ROLE_NAMES: frozenset[str] = frozenset({ROLE_SUPER_ADMIN, ROLE_SYSTEM_MANAGER})

# أدوار ترى كل أقسام التطبيق دون إدارة المستخدمين (أدوار سابقة تبقى مدعومة لحساباتها)
EXECUTIVE_ROLE_NAMES: frozenset[str] = frozenset(
    {
        "رئيس تنفيذي",
        "مالك",
    }
)

# أقسام كل دور غير إداري — تُمنح تلقائياً عند الحفظ
ROLE_SECTIONS: dict[str, tuple[str, ...]] = {
    ROLE_FINANCE_MANAGER: ("sales", "finance", "purchases", "inventory"),
    ROLE_PURCHASE_MANAGER: ("purchases", "inventory"),
    # أدوار سابقة
    "مدير مشتريات": ("purchases",),
    "مدير مبيعات": ("sales",),
    "مدير تسعيرة": ("purchases",),
    "مدير مخازن": ("inventory",),
}

# أدوار مدير قسم واحد (للتوافق مع نصوص الترحيب القديمة)
SECTION_MANAGER_ROLES: dict[str, str] = {
    role: sections[0] for role, sections in ROLE_SECTIONS.items() if len(sections) == 1
}

ROLE_CHOICES: tuple[tuple[str, str], ...] = (
    (ROLE_SUPER_ADMIN, "سوبر أدمن"),
    (ROLE_SYSTEM_MANAGER, "مدير النظام"),
    (ROLE_FINANCE_MANAGER, "مدير مالي"),
    (ROLE_PURCHASE_MANAGER, "مدير شراء"),
)

# وصف مختصر لما يفتحه كل دور — يظهر في بطاقات الأدوار ونموذج المستخدم
ROLE_DETAILS: dict[str, dict[str, Any]] = {
    ROLE_SUPER_ADMIN: {
        "tone": "super",
        "summary": "كل الشاشات، وإدارة المستخدمين، وربط أوراكل",
        "screens": ("كل الشاشات", "المستخدمون", "ربط أوراكل"),
    },
    ROLE_SYSTEM_MANAGER: {
        "tone": "system",
        "summary": "كل الشاشات مع إدارة المستخدمين",
        "screens": ("كل الشاشات", "المستخدمون"),
    },
    ROLE_FINANCE_MANAGER: {
        "tone": "finance",
        "summary": "القوائم المالية، أداء المبيعات، المشتريات والموردون، أرصدة المخزون",
        "screens": ("القوائم المالية", "المبيعات", "المشتريات", "المخزون"),
    },
    ROLE_PURCHASE_MANAGER: {
        "tone": "purchase",
        "summary": "المشتريات والموردون، أرصدة المخزون",
        "screens": ("المشتريات", "المخزون"),
    },
}


def role_flags(role_name: str) -> tuple[bool, bool]:
    """(is_staff, is_superuser) المطلوبة لكل دور."""
    role = str(role_name or "").strip()
    if role == ROLE_SUPER_ADMIN:
        return True, True
    if role == ROLE_SYSTEM_MANAGER:
        return True, False
    return False, False


def assignable_roles(actor) -> tuple[tuple[str, str], ...]:
    """مدير النظام لا يمنح دور السوبر أدمن؛ السوبر أدمن يمنح كل الأدوار."""
    if getattr(actor, "is_superuser", False):
        return ROLE_CHOICES
    return tuple(choice for choice in ROLE_CHOICES if choice[0] != ROLE_SUPER_ADMIN)


def can_manage_user(actor, target) -> bool:
    """مدير النظام لا يعدّل حساب سوبر أدمن ولا يحذفه."""
    if not getattr(actor, "is_staff", False):
        return False
    return bool(getattr(actor, "is_superuser", False) or not getattr(target, "is_superuser", False))


@dataclass(frozen=True)
class NavScreen:
    key: str
    label: str


@dataclass(frozen=True)
class NavSection:
    key: str
    label: str
    screens: tuple[NavScreen, ...]


NAV_SECTIONS: tuple[NavSection, ...] = (
    NavSection(
        "inventory",
        "إدارة المخزون",
        (
            NavScreen("browse_inventory", "أرصدة المخزون"),
        ),
    ),
    NavSection(
        "purchases",
        "إدارة المشتريات",
        (
            NavScreen("browse_purchases", "المشتريات والموردون"),
        ),
    ),
    NavSection(
        "sales",
        "إدارة المبيعات",
        (
            NavScreen("browse_sales", "أداء المبيعات"),
        ),
    ),
    NavSection(
        "finance",
        "الإدارة المالية",
        (
            NavScreen("browse_profitability", "القوائم المالية"),
        ),
    ),
    # رواتب الأفراد حساسة: تُمنح لأدوار الإدارة (كل الأقسام) فقط، لا لمدير مالي أو شراء
    NavSection(
        "hr",
        "الموارد البشرية",
        (
            NavScreen("browse_hr", "الرواتب والعاملون"),
        ),
    ),
)

SECTION_KEYS: tuple[str, ...] = tuple(s.key for s in NAV_SECTIONS)
ALL_SCREEN_KEYS: frozenset[str] = frozenset(
    screen.key for section in NAV_SECTIONS for screen in section.screens
)

_SCREEN_TO_SECTION: dict[str, str] = {
    screen.key: section.key
    for section in NAV_SECTIONS
    for screen in section.screens
}
for alias, parent in SCREEN_ALIASES.items():
    if parent in _SCREEN_TO_SECTION:
        _SCREEN_TO_SECTION[alias] = _SCREEN_TO_SECTION[parent]


def normalize_screen_key(url_name: str | None) -> str:
    key = str(url_name or "").strip()
    return SCREEN_ALIASES.get(key, key)


def section_for_screen(url_name: str | None) -> str | None:
    return _SCREEN_TO_SECTION.get(normalize_screen_key(url_name))


def _as_str_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    out: list[str] = []
    seen: set[str] = set()
    for item in value:
        text = str(item or "").strip()
        if text and text not in seen:
            seen.add(text)
            out.append(text)
    return out


def user_role_name(user) -> str:
    if user is None or not getattr(user, "is_authenticated", False):
        return ""
    profile = getattr(user, "profile", None)
    role = ((profile.role_name if profile else "") or "").strip()
    if role:
        return role
    # حسابات الإدارة بلا دور محفوظ (مثل حساب الإقلاع)
    if getattr(user, "is_superuser", False):
        return ROLE_SUPER_ADMIN
    if getattr(user, "is_staff", False):
        return ROLE_SYSTEM_MANAGER
    return ""


def is_executive_role(user) -> bool:
    """رئيس تنفيذي / مالك: كل الشاشات ما عدا إدارة المستخدمين."""
    if user is None or not getattr(user, "is_authenticated", False):
        return False
    if getattr(user, "is_staff", False):
        return False
    return user_role_name(user) in EXECUTIVE_ROLE_NAMES


def is_section_manager_role(user) -> bool:
    return user_role_name(user) in ROLE_SECTIONS


def preset_sections_for_role(role_name: str) -> list[str] | None:
    """أقسام تُمنح تلقائياً حسب الدور، أو None إن كان الدور يعتمد على الصلاحيات اليدوية."""
    role = str(role_name or "").strip()
    if role in EXECUTIVE_ROLE_NAMES or role in STAFF_ROLE_NAMES:
        return list(SECTION_KEYS)
    sections = ROLE_SECTIONS.get(role)
    if sections:
        return [key for key in sections if key in SECTION_KEYS]
    return None


def has_full_app_access(user) -> bool:
    """وصول كامل لأقسام التطبيق (بدون بالضرورة إدارة المستخدمين)."""
    if user is None or not getattr(user, "is_authenticated", False):
        return False
    if getattr(user, "is_staff", False):
        return True
    return is_executive_role(user)


def load_user_permission_row(user) -> Any | None:
    if user is None or not getattr(user, "is_authenticated", False):
        return None
    if has_full_app_access(user):
        return None
    cache = getattr(user, "_nav_permission_cache", None)
    if cache is not None:
        return cache[0] if cache else None
    from .models import UserNavPermission

    try:
        row = user.nav_permission
    except UserNavPermission.DoesNotExist:
        row = None
    user._nav_permission_cache = (row,)
    return row


def user_sections(user) -> set[str]:
    if user is None or not getattr(user, "is_authenticated", False):
        return set()
    if has_full_app_access(user):
        return set(SECTION_KEYS)
    preset = preset_sections_for_role(user_role_name(user))
    row = load_user_permission_row(user)
    if row is None:
        # بلا سجل: أدوار المدير حسب القسم · غيرهم كل الأقسام حتى الحفظ الأول
        if preset is not None:
            return set(preset)
        return set(SECTION_KEYS)
    saved = {k for k in _as_str_list(row.sections) if k in SECTION_KEYS}
    if not saved and preset is not None:
        return set(preset)
    return saved


def user_blocked_screens(user) -> set[str]:
    if user is None or not getattr(user, "is_authenticated", False):
        return set()
    if has_full_app_access(user):
        return set()
    row = load_user_permission_row(user)
    if row is None:
        return set()
    return {
        normalize_screen_key(k)
        for k in _as_str_list(row.blocked_screens)
        if normalize_screen_key(k) in ALL_SCREEN_KEYS
    }


def user_can_access_screen(user, url_name: str | None) -> bool:
    """هل يحق للمستخدم فتح هذه الشاشة (اسم مسار Django)؟"""
    if user is None or not getattr(user, "is_authenticated", False):
        return False
    key = str(url_name or "").strip()
    if not key:
        return True
    if key in ALWAYS_ALLOWED:
        return True
    if key in STAFF_ONLY:
        return bool(getattr(user, "is_staff", False))
    if has_full_app_access(user):
        return True

    screen = normalize_screen_key(key)
    section = section_for_screen(key)
    if section is None:
        # مسار غير مصنّف في الكتالوج: اسمح للمُسجّل (APIs مساعدة…)
        return True
    if section not in user_sections(user):
        return False
    if screen in user_blocked_screens(user):
        return False
    return True


def user_can_see_section(user, section_key: str) -> bool:
    if section_key not in user_sections(user):
        return False
    blocked = user_blocked_screens(user)
    section = next((s for s in NAV_SECTIONS if s.key == section_key), None)
    if section is None:
        return False
    return any(screen.key not in blocked for screen in section.screens)


def build_nav_access(user) -> dict[str, Any]:
    """قاموس للقوالب: sections / screens."""
    sections: dict[str, bool] = {}
    screens: dict[str, bool] = {}
    for section in NAV_SECTIONS:
        sections[section.key] = user_can_see_section(user, section.key)
        for screen in section.screens:
            screens[screen.key] = user_can_access_screen(user, screen.key)
    return {
        "role": user_role_name(user) or "مستخدم",
        "is_full": has_full_app_access(user),
        "is_executive": is_executive_role(user),
        "sections": sections,
        "screens": screens,
    }


def permission_form_catalog(
    *,
    selected_sections: Iterable[str] | None = None,
    blocked_screens: Iterable[str] | None = None,
) -> list[dict[str, Any]]:
    selected = {str(x).strip() for x in (selected_sections or [])}
    blocked = {normalize_screen_key(x) for x in (blocked_screens or [])}
    out: list[dict[str, Any]] = []
    for section in NAV_SECTIONS:
        out.append(
            {
                "key": section.key,
                "label": section.label,
                "checked": section.key in selected,
                "screens": [
                    {
                        "key": screen.key,
                        "label": screen.label,
                        "blocked": screen.key in blocked,
                    }
                    for screen in section.screens
                ],
            }
        )
    return out


def parse_permission_post(post) -> tuple[list[str], list[str]]:
    """يستخرج الأقسام الممنوحة والشاشات المحجوبة من POST."""
    sections = [
        key
        for key in SECTION_KEYS
        if str(post.get(f"section_{key}") or "").strip() in ("1", "on", "true", "yes")
    ]
    section_set = set(sections)
    blocked: list[str] = []
    for section in NAV_SECTIONS:
        if section.key not in section_set:
            continue
        for screen in section.screens:
            raw = str(post.get(f"block_{screen.key}") or "").strip()
            if raw in ("1", "on", "true", "yes"):
                blocked.append(screen.key)
    return sections, blocked


def ensure_user_nav_permission(user, *, sections: list[str] | None = None) -> Any:
    from .models import UserNavPermission

    defaults = {
        "sections": list(sections) if sections is not None else [],
        "blocked_screens": [],
    }
    row, _ = UserNavPermission.objects.get_or_create(user=user, defaults=defaults)
    if hasattr(user, "_nav_permission_cache"):
        delattr(user, "_nav_permission_cache")
    return row
