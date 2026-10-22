"""التحقق من مدخلات البحث والمخزن + كشف أنماط حقن SQL."""

from __future__ import annotations

import re


# أنماط شائعة لمحاولات SQL Injection (دفاع إضافي فوق ORM)
_SQLI_PATTERNS = (
    re.compile(r"('\s*or\s+'?\d|'?\s*or\s+\d+\s*=\s*\d)", re.I),
    re.compile(r'(union(\s+all)?\s+select)', re.I),
    re.compile(r'(insert\s+into|drop\s+table|alter\s+table|create\s+table|truncate\s+table|delete\s+from)', re.I),
    re.compile(r'(information_schema|sysobjects|sys\.tables|pg_catalog)', re.I),
    re.compile(r'(sleep\s*\(|benchmark\s*\(|waitfor\s+delay|pg_sleep\s*\()', re.I),
    re.compile(r'(xp_cmdshell|load_file\s*\(|into\s+(out|dump)file)', re.I),
    re.compile(r'(;\s*(select|drop|delete|update|insert|exec|execute)\b)', re.I),
    re.compile(r'(--|#|/\*|\*/)', re.I),
)

# حقول لا تُفحص (كلمات السر قد تحتوي رموزاً خاصة عمداً)
_SKIP_FIELDS = frozenset({
    'password',
    'password1',
    'password2',
    'old_password',
    'new_password1',
    'new_password2',
    'csrfmiddlewaretoken',
    'sync_secret',
    'stock_token',
})


class ValidationError(ValueError):
    pass


def contains_sql_injection(value: str | None) -> bool:
    """هل النص يشبه محاولة حقن SQL؟"""
    text = str(value or '')
    if not text or len(text) > 4000:
        return bool(text) and len(text) > 4000
    for pattern in _SQLI_PATTERNS:
        if pattern.search(text):
            return True
    return False


def reject_sql_injection(value: str | None, field_name: str = 'input') -> str:
    """يرفع ValidationError إن وُجدت أنماط حقن."""
    text = str(value or '')
    if contains_sql_injection(text):
        raise ValidationError(f'قيمة غير مسموحة في الحقل «{field_name}».')
    return text


def scan_request_for_sql_injection(request) -> str | None:
    """
    يفحص GET/POST بحثاً عن محاولات حقن.
    يرجع اسم الحقل المخالف أو None إن كان الطلب آمناً.
    """
    sources = []
    if hasattr(request, 'GET'):
        sources.append(request.GET)
    if request.method in {'POST', 'PUT', 'PATCH'} and hasattr(request, 'POST'):
        sources.append(request.POST)

    for params in sources:
        for key in params.keys():
            name = str(key or '').lower()
            if name in _SKIP_FIELDS or name.endswith('password'):
                continue
            for value in params.getlist(key):
                if contains_sql_injection(value):
                    return key
    return None
