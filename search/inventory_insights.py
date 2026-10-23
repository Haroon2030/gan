"""محرك تحليل المخزون — إجماليات حسب المخزن والمجموعة والفرع."""

from __future__ import annotations

from typing import Any


def _scope_label(warehouse: str, group_code: str, branch_code: str) -> str:
    parts: list[str] = []
    if branch_code:
        parts.append(f"فرع {branch_code}")
    if warehouse:
        parts.append(f"مخزن {warehouse}")
    if group_code:
        parts.append(f"مجموعة {group_code}")
    return " · ".join(parts) if parts else "كل المخزون المتاح"


def _money(v: float) -> str:
    return f"{float(v or 0):,.2f}"


def _qty(v: float) -> str:
    num = float(v or 0)
    if abs(num - round(num)) < 1e-9:
        return f"{int(round(num)):,}"
    return f"{num:,.2f}"


def _attach_inv_rank_bars(rows: list[dict], *, value_key: str = "stock_value") -> None:
    """شريط الترتيب: الأفضل = 100% والباقي نسبي منه."""
    if not rows:
        return
    peak = float(rows[0].get(value_key) or 0)
    for row in rows:
        val = float(row.get(value_key) or 0)
        row["bar_pct"] = round((val / peak * 100.0) if peak else 0.0, 1)


def _table_totals(rows: list[dict]) -> dict[str, Any]:
    """إجماليات صف التذييل من صفوف البعد (قيمة وكمية بدون تكرار محاسبي)."""
    value = round(sum(float(r.get("stock_value") or 0) for r in rows), 2)
    qty = round(sum(float(r.get("qty_total") or 0) for r in rows), 2)
    items = sum(int(r.get("item_count") or 0) for r in rows)
    warehouses = sum(int(r.get("warehouse_count") or 0) for r in rows)
    stock_before = round(sum(float(r.get("stock_before") or 0) for r in rows), 2)
    pending_cost = round(sum(float(r.get("pending_cost") or 0) for r in rows), 2)
    pending_qty = round(sum(float(r.get("pending_qty") or 0) for r in rows), 2)
    return {
        "stock_value": value,
        "qty_total": qty,
        "item_count": items,
        "warehouse_count": warehouses,
        "stock_before": stock_before,
        "pending_cost": pending_cost,
        "pending_qty": pending_qty,
        "stock_value_display": _money(value),
        "stock_before_display": _money(stock_before),
        "pending_cost_display": _money(pending_cost),
        "pending_qty_display": _qty(pending_qty),
        "qty_display": _qty(qty),
        "item_count_display": f"{items:,}",
        "warehouse_count_display": f"{warehouses:,}",
        "share_display": "100%" if rows else "0%",
        "row_count": len(rows),
    }


def _value_composition(
    by_group: list[dict], total_value: float, *, top: int = 5, key: str = "stock_value"
) -> list[dict]:
    """شريط التركيب: أكبر المجموعات قيمةً ثم «أخرى» — النسب من الإجمالي (مخزون أو مشتريات)."""
    if total_value <= 0:
        return []
    ranked = sorted(by_group, key=lambda r: -float(r.get(key) or 0))
    parts = []
    for row in ranked[:top]:
        value = float(row.get(key) or 0)
        if value <= 0:
            continue
        parts.append({"name": str(row.get("name") or row.get("code") or "—").strip("() ").strip(), "value": value})
    rest = total_value - sum(p["value"] for p in parts)
    if rest > total_value * 0.001:
        parts.append({"name": "أخرى", "value": rest, "is_rest": True})
    out = []
    for i, part in enumerate(parts):
        pct = part["value"] / total_value * 100.0
        out.append(
            {
                "name": part["name"],
                "value_display": _money(part["value"]),
                "share_display": f"{pct:.1f}%",
                # نص جاهز لـ style: تعريب القالب قد يكتب الفاصلة العشرية «,»
                "width": f"{pct:.3f}%",
                "tone": "rest" if part.get("is_rest") else ("lead" if i == 0 else f"g{i}"),
            }
        )
    return out


_MAP_W = 100.0
_MAP_H = 56.0


def _worst_ratio(row: list[float], side: float) -> float:
    total = sum(row)
    return max(max(side * side * a / (total * total), (total * total) / (side * side * a)) for a in row)


def _squarify(areas: list[float], x: float, y: float, w: float, h: float) -> list[tuple]:
    """تقسيم مستطيل إلى مستطيلات مساحتها متناسبة مع القيم، بأقرب شكل للمربع."""
    rects: list[tuple] = []
    rest = list(areas)
    while rest:
        side = min(w, h)
        row = [rest[0]]
        for a in rest[1:]:
            if _worst_ratio(row + [a], side) <= _worst_ratio(row, side):
                row.append(a)
            else:
                break
        total = sum(row)
        if w >= h:
            col_w = total / h
            cy = y
            for a in row:
                rects.append((x, cy, col_w, a / col_w))
                cy += a / col_w
            x, w = x + col_w, w - col_w
        else:
            row_h = total / w
            cx = x
            for a in row:
                rects.append((cx, y, a / row_h, row_h))
                cx += a / row_h
            y, h = y + row_h, h - row_h
        rest = rest[len(row):]
    return rects


def _stagnant_tiles(rows: list[dict]) -> None:
    """خريطة المخزون الراكد: المساحة = الكمية، ودرجة اللون = القيمة بالتكلفة."""
    import math

    items = [r for r in rows if float(r.get("qty_total") or 0) > 0]
    if not items:
        return
    total_qty = sum(float(r["qty_total"]) for r in items)
    scale = _MAP_W * _MAP_H / total_qty
    rects = _squarify([float(r["qty_total"]) * scale for r in items], 0.0, 0.0, _MAP_W, _MAP_H)
    peak_value = max(float(r.get("stock_value") or 0) for r in items)
    for row, (x, y, w, h) in zip(items, rects):
        share_of_map = w * h / (_MAP_W * _MAP_H) * 100.0
        value = float(row.get("stock_value") or 0)
        row["tile"] = {
            # نصوص جاهزة لـ style: تعريب القالب قد يكتب الفاصلة العشرية «,»
            # الخريطة تبدأ من اليمين: نستخدم right بدل left
            "right": f"{x / _MAP_W * 100:.3f}%",
            "top": f"{y / _MAP_H * 100:.3f}%",
            "width": f"{w / _MAP_W * 100:.3f}%",
            "height": f"{h / _MAP_H * 100:.3f}%",
            "size": "lg" if share_of_map >= 12 else "md" if share_of_map >= 4 else "sm" if share_of_map >= 1.2 else "xs",
            # كتلة ضيقة لا تتسع للمبلغ بعملته — تبقى النسبة، والمبلغ في التلميح
            "narrow": w / _MAP_W * 100 < 18,
            "level": 1 if peak_value <= 0 else max(1, math.ceil(value / peak_value * 4)),
        }


def _build_stagnant_items(
    rows: list[dict],
    *,
    total_stock_qty: float,
    total_stock_value: float,
) -> dict[str, Any]:
    """أصناف بأعلى كمية بلا مبيعات — لقائمة الترتيب."""
    stagnant = list(rows or [])
    stagnant_qty = round(sum(float(r.get("qty_total") or 0) for r in stagnant), 2)
    stagnant_value = round(sum(float(r.get("stock_value") or 0) for r in stagnant), 2)
    for row in stagnant:
        if "qty_display" not in row:
            row["qty_display"] = _qty(row.get("qty_total") or 0)
        if "stock_value_display" not in row:
            row["stock_value_display"] = _money(row.get("stock_value") or 0)
        qty = float(row.get("qty_total") or 0)
        share = (qty / stagnant_qty * 100.0) if stagnant_qty > 0 else 0.0
        row["share_pct"] = round(share, 1)
        row["share_display"] = f"{share:.1f}%"
    top = stagnant[:15]
    _attach_inv_rank_bars(top, value_key="qty_total")
    _stagnant_tiles(top)
    of_all = (
        (stagnant_qty / total_stock_qty * 100.0) if total_stock_qty > 0 else 0.0
    )
    of_value = (
        (stagnant_value / total_stock_value * 100.0) if total_stock_value > 0 else 0.0
    )
    return {
        "rows": top,
        "count": len(stagnant),
        "qty_total": stagnant_qty,
        "qty_display": _qty(stagnant_qty),
        "stock_value": stagnant_value,
        "stock_value_display": _money(stagnant_value),
        "of_total_pct": round(of_all, 1),
        "of_total_display": f"{of_all:.1f}%",
        "of_value_pct": round(of_value, 1),
        "of_value_display": f"{of_value:.1f}%",
    }


def build_inventory_insights(
    *,
    warehouse: str = "",
    group_code: str = "",
    branch_code: str = "",
) -> dict[str, Any]:
    """يبني لوحة تحليل مخزون من أوراكل (قيمة بعد خصم مبيعات POS غير المرحلة)."""
    from datetime import date, timedelta

    from .oracle_stock import (
        fetch_inventory_by_branch,
        fetch_inventory_by_group,
        fetch_inventory_by_warehouse,
        fetch_stagnant_items,
        oracle_session,
    )

    wh = str(warehouse or "").strip()
    gcode = str(group_code or "").strip()
    brn = str(branch_code or "").strip()
    activity_to = date.today()
    activity_from_sales = activity_to - timedelta(days=6)

    def _by_wh():
        with oracle_session():
            return fetch_inventory_by_warehouse(
                warehouse=wh, group_code=gcode, branch_code=brn
            )

    def _by_group():
        with oracle_session():
            return fetch_inventory_by_group(
                warehouse=wh, group_code=gcode, branch_code=brn
            )

    def _by_brn():
        with oracle_session():
            return fetch_inventory_by_branch(
                warehouse=wh, group_code=gcode, branch_code=brn
            )

    def _stagnant():
        with oracle_session():
            return fetch_stagnant_items(
                activity_from_sales,
                activity_to,
                warehouse=wh,
                group_code=gcode,
                branch_code=brn,
                limit=15,
            )

    # تسلسلي فقط: تجنّب ThreadPoolExecutor (يتعطّل بعد إعادة تحميل runserver على Windows)
    by_warehouse = _by_wh()
    _attach_inv_rank_bars(by_warehouse)
    by_group = _by_group()
    by_branch = _by_brn()
    stagnant_rows = _stagnant()

    sales_period_label = f"{activity_from_sales.isoformat()} → {activity_to.isoformat()}"

    total_value = round(sum(float(r.get("stock_value") or 0) for r in by_warehouse), 2)
    total_qty = round(sum(float(r.get("qty_total") or 0) for r in by_warehouse), 2)
    total_rows = sum(int(r.get("row_count") or 0) for r in by_warehouse)
    warehouse_count = len(by_warehouse)
    group_count = len(by_group)
    branch_count = len(by_branch)
    distinct_items_est = sum(int(r.get("item_count") or 0) for r in by_warehouse)

    warehouse_totals = _table_totals(by_warehouse)
    group_totals = _table_totals(by_group)
    branch_totals = _table_totals(by_branch)
    group_totals["warehouse_count"] = warehouse_count
    group_totals["warehouse_count_display"] = f"{warehouse_count:,}"
    branch_totals["warehouse_count"] = warehouse_count
    branch_totals["warehouse_count_display"] = f"{warehouse_count:,}"

    stagnant = _build_stagnant_items(
        stagnant_rows,
        total_stock_qty=total_qty,
        total_stock_value=total_value,
    )

    top_wh = by_warehouse[0] if by_warehouse else None
    top_group = by_group[0] if by_group else None
    top_branch = by_branch[0] if by_branch else None

    alerts: list[dict] = []
    actions: list[dict] = []
    if top_wh and float(top_wh.get("share_pct") or 0) >= 40:
        alerts.append(
            {
                "severity": "warn",
                "title": "تركّز عالٍ في مخزن واحد",
                "detail": (
                    f"{top_wh['name']} يستحوذ على {top_wh['share_display']} "
                    f"من قيمة المخزون ضمن الفلتر."
                ),
            }
        )
        actions.append(
            {
                "severity": "warn",
                "text": "راجع توزيع المخزون بين المخازن لتقليل المخاطر التشغيلية.",
                "from_alert": "تركّز مخزن",
            }
        )
    if top_group and float(top_group.get("share_pct") or 0) >= 35:
        alerts.append(
            {
                "severity": "info",
                "title": "مجموعة مهيمنة بالقيمة",
                "detail": (
                    f"{top_group['name']} تمثّل {top_group['share_display']} "
                    f"من قيمة المخزون."
                ),
            }
        )
    if warehouse_count == 0:
        alerts.append(
            {
                "severity": "bad",
                "title": "لا رصيد ضمن الفلتر",
                "detail": "لا توجد كميات متاحة > 0 بالمعايير المحددة.",
            }
        )
    if stagnant["count"] > 0 and float(stagnant.get("of_total_pct") or 0) >= 10:
        alerts.append(
            {
                "severity": "warn",
                "title": "أصناف راكدة بكمية عالية",
                "detail": (
                    f"{stagnant['count']} صنفاً بكمية {stagnant['qty_display']} "
                    f"({stagnant['of_total_display']} من الكمية) بلا مبيعات خلال آخر 7 أيام."
                ),
            }
        )
        actions.append(
            {
                "severity": "warn",
                "text": "راجع الأصناف الأعلى كمية والأقل حركة: عروض، نقل مخزون، أو إعادة تسعير.",
                "from_alert": "مخزون راكد",
            }
        )

    return {
        "scope_label": _scope_label(wh, gcode, brn),
        "warehouse": wh,
        "group_code": gcode,
        "branch_code": brn,
        "composition": _value_composition(by_group, total_value),
        "kpis": {
            "stock_value": _money(total_value),
            "stock_value_num": total_value,
            "qty_total": _qty(total_qty),
            "qty_num": total_qty,
            "row_count": f"{total_rows:,}",
            "item_rows": f"{distinct_items_est:,}",
            "warehouse_count": warehouse_count,
            "group_count": group_count,
            "branch_count": branch_count,
            "top_warehouse_share": float((top_wh or {}).get("share_pct") or 0),
            "top_warehouse_share_display": (top_wh or {}).get("share_display") or "0%",
            "top_group_share": float((top_group or {}).get("share_pct") or 0),
            "top_group_share_display": (top_group or {}).get("share_display") or "0%",
        },
        "highlights": {
            "top_warehouse": {
                "name": (top_wh or {}).get("name") or "—",
                "value": (top_wh or {}).get("stock_value_display") or "0.00",
                "share": (top_wh or {}).get("share_display") or "0%",
            },
            "top_group": {
                "name": (top_group or {}).get("name") or "—",
                "value": (top_group or {}).get("stock_value_display") or "0.00",
                "share": (top_group or {}).get("share_display") or "0%",
            },
            "top_branch": {
                "name": (top_branch or {}).get("name") or "—",
                "value": (top_branch or {}).get("stock_value_display") or "0.00",
                "share": (top_branch or {}).get("share_display") or "0%",
            },
        },
        "by_warehouse": by_warehouse[:80],
        "by_group": by_group[:80],
        "by_branch": by_branch[:40],
        "sales_period_label": sales_period_label,
        "stagnant": stagnant,
        "warehouse_totals": warehouse_totals,
        "group_totals": group_totals,
        "branch_totals": branch_totals,
        "alerts": alerts,
        "actions": actions,
        "note": (
            "القيمة محسوبة من الكمية المتاحة × متوسط التكلفة. "
            "الكمية مجموع وحدات التخزين وقد تختلف وحداتها بين الأصناف."
        ),
    }
