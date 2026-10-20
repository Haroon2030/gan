"""قائمة الفروع للفلاتر — مستقلة عن قائمة الدخل."""

from __future__ import annotations

from .oracle_stock import _branch_names


def fetch_income_branches() -> list[dict]:
    """فروع من S_BRN للفلتر."""
    names = _branch_names()
    return [
        {"code": code, "name": name}
        for code, name in sorted(names.items(), key=lambda x: x[0])
    ]
