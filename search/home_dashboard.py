"""لوحة المؤشرات التنفيذية (الصفحة الرئيسية).

كل ودجت يُبنى من نفس دوال الشاشات المتخصصة حتى تتطابق الأرقام:
المبيعات من تحليل المبيعات، المشتريات من تحليل المشتريات، المخزون من
تحليل المخزون، والرصيد بلا مبيعات من شاشته. الاستعلامات الجديدة هنا
(أعمار المخزون، النفاد مع طلب، أحدث المستندات) SELECT فقط.

الصلاحية تُفحص لكل ودجت في الواجهة البرمجية — ليست هنا.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any, Callable

from django.core.cache import cache

HOME_CACHE_PREFIX = "home:v1"
LIVE_TTL = 300
CLOSED_TTL = 6 * 3600
RECENT_TTL = 60

# ودجت → شاشات يجب أن يملكها المستخدم كلها ليرى الودجت
WIDGET_SCREENS: dict[str, tuple[str, ...]] = {
    "sales": ("browse_sales",),
    "groups": ("browse_sales",),
    "top_items": ("browse_sales",),
    "profit": ("browse_performance",),
    "purchases": ("browse_purchases",),
    "inventory": ("browse_inventory",),
    # الدوران يكشف تكلفة المبيعات → يحتاج صلاحية الأداء أيضاً
    "turnover": ("browse_inventory", "browse_performance"),
    "shortage": ("browse_inventory",),
    "stagnant": ("browse_unsold",),
}

# أنواع المستندات في «العمليات الحديثة» وشاشة كل نوع
RECENT_DOC_SCREENS: dict[str, str] = {
    "sale": "browse_sales",
    "return": "browse_sales",
    "purchase": "browse_purchases",
    "transfer": "browse_inventory",
}

TREND_SERIES_SCREENS: dict[str, str] = {
    "sales": "browse_sales",
    "purchases": "browse_purchases",
}

AGING_BUCKETS: tuple[tuple[str, str], ...] = (
    ("0_30", "0–30 يوماً"),
    ("31_60", "31–60 يوماً"),
    ("61_90", "61–90 يوماً"),
    ("90_plus", "أكثر من 90"),
)

SHORTAGE_LOOKBACK_DAYS = 7


# ── الفترات ────────────────────────────────────────────────────────────


def default_period(today: date | None = None) -> tuple[date, date]:
    """من أول الشهر حتى اليوم."""
    today = today or date.today()
    return today.replace(day=1), today


def previous_period(d_from: date, d_to: date) -> tuple[date, date]:
    """نفس طول الفترة مباشرة قبلها — مثل قياس الأداء."""
    prev_to = d_from - timedelta(days=1)
    return prev_to - (d_to - d_from), prev_to


def month_bounds(year: int, month: int, today: date | None = None) -> tuple[date, date]:
    """حدود شهر؛ الشهر الحالي ينتهي اليوم."""
    today = today or date.today()
    start = date(year, month, 1)
    nxt = date(year + (month // 12), (month % 12) + 1, 1)
    end = nxt - timedelta(days=1)
    if start <= today <= end:
        end = today
    return start, end


def recent_months(count: int = 6, today: date | None = None) -> list[str]:
    today = today or date.today()
    y, m = today.year, today.month
    out: list[str] = []
    for _ in range(count):
        out.append(f"{y:04d}-{m:02d}")
        m -= 1
        if m == 0:
            y, m = y - 1, 12
    return list(reversed(out))


# ── أدوات عامة ─────────────────────────────────────────────────────────


def change_pct(current: float, previous: float) -> float | None:
    """نسبة التغير؛ None إن لم يكن للمقارنة معنى (سابق صفر)."""
    cur = float(current or 0)
    prev = float(previous or 0)
    if prev <= 0:
        return None
    return round((cur - prev) / prev * 100.0, 1)


def share_pct(part: float, total: float) -> float:
    total = float(total or 0)
    if total <= 0:
        return 0.0
    return round(float(part or 0) / total * 100.0, 1)


def _ttl_for(d_to: date, today: date | None = None) -> int:
    today = today or date.today()
    return LIVE_TTL if d_to >= today else CLOSED_TTL


def cached_widget(
    name: str,
    key_parts: tuple[Any, ...],
    ttl: int,
    builder: Callable[[], dict],
) -> dict:
    """كاش قصير فوق كاش الدوال الأصلية (بعضها يتجاوز الكاش لفترة اليوم)."""
    key = ":".join([HOME_CACHE_PREFIX, name, *[str(p) for p in key_parts]])
    try:
        hit = cache.get(key)
    except Exception:  # noqa: BLE001
        hit = None
    if hit is not None:
        return hit
    value = builder()
    try:
        cache.set(key, value, ttl)
    except Exception:  # noqa: BLE001
        pass
    return value


# ── المبيعات والقنوات ──────────────────────────────────────────────────


def split_channels(
    pos_raw: list[dict],
    credit_raw: list[dict],
    cash_raw: list[dict],
) -> dict[str, float]:
    """نفس منطق إجمالي تحليل المبيعات مفكوكاً إلى قنوات بلا تكرار.

    لكل فرع: max(POS، نقدي الفواتير) + الآجل
    = POS + الآجل + max(0، نقدي الفواتير − POS).
    """

    def by_code(rows: list[dict]) -> dict[str, float]:
        out: dict[str, float] = {}
        for r in rows or []:
            code = str(r.get("branch_code") or "").strip()
            if code:
                out[code] = out.get(code, 0.0) + float(r.get("sales_total") or 0)
        return out

    pos = by_code(pos_raw)
    credit = by_code(credit_raw)
    cash = by_code(cash_raw)
    extra_cash = sum(
        max(0.0, cash.get(code, 0.0) - pos.get(code, 0.0)) for code in cash
    )
    pos_total = sum(pos.values())
    credit_total = sum(credit.values())
    return {
        "pos": round(pos_total, 2),
        "credit": round(credit_total, 2),
        "cash_extra": round(extra_cash, 2),
        "total": round(pos_total + credit_total + extra_cash, 2),
    }


def _channel_totals(d_from: date, d_to: date) -> dict[str, float]:
    from .oracle_stock import fetch_branch_sales_totals

    pos_raw = fetch_branch_sales_totals(d_from, d_to, system="pos")
    credit_raw = fetch_branch_sales_totals(d_from, d_to, system="wholesale")
    cash_raw = fetch_branch_sales_totals(d_from, d_to, system="onix")
    totals = split_channels(pos_raw, credit_raw, cash_raw)
    totals["pos_invoices"] = sum(int(r.get("invoice_count") or 0) for r in pos_raw)
    return totals


CHANNEL_LABELS = (
    ("pos", "نقاط البيع"),
    ("credit", "الآجل"),
    ("cash_extra", "نقدي الفواتير"),
)


def build_sales_widget(d_from: date, d_to: date) -> dict:
    p_from, p_to = previous_period(d_from, d_to)
    cur = _channel_totals(d_from, d_to)
    prev = _channel_totals(p_from, p_to)
    channels = [
        {
            "key": key,
            "label": label,
            "amount": cur[key],
            "share_pct": share_pct(cur[key], cur["total"]),
            "prev_amount": prev[key],
            "change_pct": change_pct(cur[key], prev[key]),
        }
        for key, label in CHANNEL_LABELS
    ]
    return {
        "total": cur["total"],
        "prev_total": prev["total"],
        "change_pct": change_pct(cur["total"], prev["total"]),
        "pos_invoices": cur["pos_invoices"],
        "channels": channels,
    }


def _aggregate_groups(rows: list[dict]) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for r in rows or []:
        code = str(r.get("group_code") or "").strip()
        if not code:
            continue
        slot = out.setdefault(
            code,
            {"code": code, "name": str(r.get("group_name") or code).strip(), "amount": 0.0},
        )
        slot["amount"] += float(r.get("sales_total") or 0)
    return out


def build_groups_widget(d_from: date, d_to: date, limit: int = 5) -> dict:
    """نفس مجموعات تحليل المبيعات (مطابَقة مع صافي فروع نقاط البيع)."""
    from .sales_dashboard import build_sales_groups

    p_from, p_to = previous_period(d_from, d_to)
    cur_payload = build_sales_groups(d_from, d_to)
    prev_payload = build_sales_groups(p_from, p_to)
    cur = _aggregate_groups(cur_payload.get("rows") or [])
    prev = _aggregate_groups(prev_payload.get("rows") or [])
    total = sum(g["amount"] for g in cur.values())
    top = sorted(cur.values(), key=lambda g: g["amount"], reverse=True)[:limit]
    rows = []
    for g in top:
        before = prev.get(g["code"], {}).get("amount", 0.0)
        rows.append(
            {
                "code": g["code"],
                "name": g["name"],
                "amount": round(g["amount"], 2),
                "share_pct": share_pct(g["amount"], total),
                "prev_amount": round(before, 2),
                "change_pct": change_pct(g["amount"], before),
            }
        )
    return {
        "rows": rows,
        "source_label": "نقاط البيع",
        # التوزيع تقريبي حتى تكتمل شهور كاش المجموعات — كما تنبّه شاشة المبيعات
        "exact": bool(cur_payload.get("exact")) and bool(prev_payload.get("exact")),
    }


def build_top_items_widget(d_from: date, d_to: date, limit: int = 5) -> dict:
    from .oracle_stock import fetch_top_sales_items

    p_from, p_to = previous_period(d_from, d_to)
    cur = fetch_top_sales_items(d_from, d_to, system="pos", limit=limit)
    prev = fetch_top_sales_items(p_from, p_to, system="pos", limit=50)
    prev_map = {
        str(r.get("item_code") or "").strip(): float(r.get("sales_total") or 0)
        for r in prev
    }
    rows = []
    for idx, r in enumerate(cur[:limit], start=1):
        code = str(r.get("item_code") or "").strip()
        amount = float(r.get("sales_total") or 0)
        before = prev_map.get(code)
        rows.append(
            {
                "rank": idx,
                "code": code,
                "name": str(r.get("item_name") or code).strip(),
                "amount": round(amount, 2),
                "qty": round(float(r.get("qty_total") or 0), 2),
                # غير موجود في أعلى 50 للفترة السابقة → لا نخترع نسبة
                "change_pct": change_pct(amount, before) if before else None,
            }
        )
    return {"rows": rows, "source_label": "نقاط البيع"}


# ── الربحية والدوران ───────────────────────────────────────────────────


def _margin_totals(d_from: date, d_to: date) -> dict[str, float]:
    """هامش نقاط البيع فقط.

    هامش «الآجل» يقرأ IAS_BILL الذي يحوي فواتير نقاط البيع المنعكسة نقداً،
    فجمعه مع نقاط البيع يضاعف المبيعات.
    """
    from .oracle_stock import fetch_margin_ranks

    data = fetch_margin_ranks(d_from, d_to, system="pos", limit=500)
    branches = data.get("branches") or []
    sales = sum(float(b.get("sales_net") or 0) for b in branches)
    cost = sum(float(b.get("cost_total") or 0) for b in branches)
    return {"sales": round(sales, 2), "cost": round(cost, 2)}


def margin_summary(sales: float, cost: float) -> dict[str, float | None]:
    profit = round(float(sales or 0) - float(cost or 0), 2)
    margin = round(profit / sales * 100.0, 1) if sales and sales > 0 else None
    return {"profit": profit, "margin_pct": margin}


def build_profit_widget(d_from: date, d_to: date) -> dict:
    p_from, p_to = previous_period(d_from, d_to)
    cur = _margin_totals(d_from, d_to)
    prev = _margin_totals(p_from, p_to)
    cur_sum = margin_summary(cur["sales"], cur["cost"])
    prev_sum = margin_summary(prev["sales"], prev["cost"])
    delta = None
    if cur_sum["margin_pct"] is not None and prev_sum["margin_pct"] is not None:
        delta = round(cur_sum["margin_pct"] - prev_sum["margin_pct"], 1)
    return {
        "sales": cur["sales"],
        "cost": cur["cost"],
        "profit": cur_sum["profit"],
        "margin_pct": cur_sum["margin_pct"],
        "prev_margin_pct": prev_sum["margin_pct"],
        "prev_profit": prev_sum["profit"],
        "margin_delta_pts": delta,
        "profit_change_pct": change_pct(cur_sum["profit"], prev_sum["profit"]),
        "source_label": "نقاط البيع · قبل الضريبة",
    }


def turnover_summary(cost_of_sales: float, days: int, stock_value: float) -> dict:
    """دوران سنوي = تكلفة المبيعات المحوّلة لسنة ÷ قيمة المخزون الحالية."""
    if days <= 0 or stock_value <= 0 or cost_of_sales <= 0:
        return {"turnover": None, "days_on_hand": None}
    annual_cost = float(cost_of_sales) * 365.0 / float(days)
    turns = annual_cost / float(stock_value)
    return {
        "turnover": round(turns, 1),
        "days_on_hand": int(round(365.0 / turns)) if turns > 0 else None,
    }


def build_turnover_widget(d_from: date, d_to: date) -> dict:
    cur = _margin_totals(d_from, d_to)
    stock_value = _stock_value_total()
    days = (d_to - d_from).days + 1
    return {
        "days": days,
        "basis_label": "تكلفة مبيعات نقاط البيع",
        **turnover_summary(cur["cost"], days, stock_value),
    }


# ── المشتريات ─────────────────────────────────────────────────────────


def build_purchases_widget(d_from: date, d_to: date) -> dict:
    from .oracle_purchases import build_purchase_dashboard

    p_from, p_to = previous_period(d_from, d_to)
    cur = build_purchase_dashboard(d_from, d_to)
    prev = build_purchase_dashboard(p_from, p_to)
    k = cur.get("kpis") or {}
    pk = prev.get("kpis") or {}
    total = float(k.get("total_amount") or 0)
    prev_total = float(pk.get("total_amount") or 0)
    vendors = [
        {
            "code": str(v.get("code") or ""),
            "name": str(v.get("name") or v.get("code") or "").strip(),
            "amount": round(float(v.get("amount") or 0), 2),
            "share_pct": round(float(v.get("share_pct") or 0), 1),
            "invoice_count": int(v.get("invoice_count") or 0),
        }
        for v in (cur.get("by_vendor") or [])[:3]
    ]
    return {
        "total": round(total, 2),
        "prev_total": round(prev_total, 2),
        "change_pct": change_pct(total, prev_total),
        "invoice_count": int(k.get("invoice_count") or 0),
        "return_count": int(k.get("return_count") or 0),
        "vendor_count": int(k.get("vendor_count") or 0),
        "vendors": vendors,
    }


# ── المخزون ───────────────────────────────────────────────────────────


def _stock_value_total() -> float:
    from .oracle_stock import fetch_inventory_by_branch

    return round(
        sum(float(r.get("stock_value") or 0) for r in fetch_inventory_by_branch()), 2
    )


def assemble_aging(rows: list[dict]) -> list[dict]:
    """rows: [{BUCKET, STOCK_VALUE, ITEM_COUNT}] → 4 شرائح ثابتة الترتيب."""
    values = {key: 0.0 for key, _ in AGING_BUCKETS}
    items = {key: 0 for key, _ in AGING_BUCKETS}
    for r in rows or []:
        key = str(r.get("BUCKET") or "")
        if key in values:
            values[key] += float(r.get("STOCK_VALUE") or 0)
            items[key] += int(r.get("ITEM_COUNT") or 0)
    total = sum(values.values())
    return [
        {
            "key": key,
            "label": label,
            "value": round(values[key], 2),
            "item_count": items[key],
            "share_pct": share_pct(values[key], total),
        }
        for key, label in AGING_BUCKETS
    ]


def fetch_inventory_aging() -> list[dict]:
    """قيمة الرصيد الحالي حسب الأيام منذ آخر فاتورة شراء للصنف.

    صنف بلا فاتورة شراء في مخطط السنة الحالية يُحسب ضمن «أكثر من 90».
    """
    from .oracle_stock import (
        _fetch_all,
        _inv_expected_qty_sql,
        _pending_pos_net_sql,
        _schema,
    )

    schema = _schema()
    qty_sql = _inv_expected_qty_sql()
    pend_sql = _pending_pos_net_sql()
    cost = "NVL(w.I_CWTAVG, w.PRIMARY_COST)"
    rows = _fetch_all(
        f"""
        SELECT BUCKET,
               ROUND(SUM(VAL), 2) AS STOCK_VALUE,
               COUNT(DISTINCT I_CODE) AS ITEM_COUNT
        FROM (
          SELECT w.I_CODE,
                 {qty_sql} * {cost} AS VAL,
                 CASE
                   WHEN lp.LAST_DATE IS NULL THEN '90_plus'
                   WHEN TRUNC(SYSDATE) - TRUNC(lp.LAST_DATE) <= 30 THEN '0_30'
                   WHEN TRUNC(SYSDATE) - TRUNC(lp.LAST_DATE) <= 60 THEN '31_60'
                   WHEN TRUNC(SYSDATE) - TRUNC(lp.LAST_DATE) <= 90 THEN '61_90'
                   ELSE '90_plus'
                 END AS BUCKET
          FROM {schema}.IAS_ITM_WCODE w
          LEFT JOIN {pend_sql} pend
            ON pend.I_CODE = TO_CHAR(w.I_CODE)
           AND pend.W_CODE = TO_CHAR(w.W_CODE)
          LEFT JOIN (
            SELECT TO_CHAR(d.I_CODE) AS I_CODE, MAX(m.BILL_DATE) AS LAST_DATE
            FROM {schema}.IAS_PI_BILL_MST m
            JOIN {schema}.IAS_PI_BILL_DTL d
              ON d.BILL_NO = m.BILL_NO
             AND d.BILL_SER = m.BILL_SER
             AND d.BILL_DOC_TYPE = m.BILL_DOC_TYPE
            WHERE NVL(m.HUNG, 0) = 0
            GROUP BY TO_CHAR(d.I_CODE)
          ) lp ON lp.I_CODE = TO_CHAR(w.I_CODE)
          WHERE NVL(w.AVL_QTY, 0) > 0
        )
        WHERE VAL > 0
        GROUP BY BUCKET
        """
    )
    return assemble_aging(rows)


def build_inventory_widget() -> dict:
    from .oracle_stock import fetch_inventory_by_branch

    branches = fetch_inventory_by_branch()
    value = round(sum(float(r.get("stock_value") or 0) for r in branches), 2)
    return {
        "stock_value": value,
        "branch_count": len([r for r in branches if float(r.get("stock_value") or 0) > 0]),
        "aging": fetch_inventory_aging(),
    }


def build_shortage_widget(limit: int = 3) -> dict:
    """نقص حرج: صنف بِيع في نقاط البيع آخر 7 أيام ورصيده المتوقع صفر في كل المخازن.

    رصيد صفر في مخزن واحد يُعالَج بتحويل؛ صفر في الكل يحتاج شراء.
    """
    from .oracle_stock import (
        _fetch_all,
        _inv_expected_qty_sql,
        _pending_pos_net_sql,
        _pos_owner,
        _schema,
    )

    schema = _schema()
    pos = _pos_owner()
    qty = "NVL(d.P_QTY, NVL(d.I_QTY, 0) * NVL(d.P_SIZE, 1))"
    qty_sql = _inv_expected_qty_sql()
    pend_sql = _pending_pos_net_sql()
    days = int(SHORTAGE_LOOKBACK_DAYS)
    rows = _fetch_all(
        f"""
        SELECT s.I_CODE, s.SOLD_QTY, im.I_NAME
        FROM (
          SELECT TO_CHAR(d.I_CODE) AS I_CODE,
                 SUM({qty}) AS SOLD_QTY
          FROM {pos}.IAS_POS_BILL_DTL d
          JOIN {pos}.IAS_POS_BILL_MST m
            ON m.BILL_NO = d.BILL_NO
           AND m.BRN_NO = d.BRN_NO
           AND NVL(m.BILL_SRL, 0) = NVL(d.BILL_SRL, 0)
          WHERE m.BILL_DATE >= TRUNC(SYSDATE) - {days}
            AND NVL(m.HUNG, 0) = 0
          GROUP BY TO_CHAR(d.I_CODE)
          HAVING SUM({qty}) > 0
        ) s
        JOIN {schema}.IAS_ITM_MST im ON TO_CHAR(im.I_CODE) = s.I_CODE
        LEFT JOIN (
          SELECT TO_CHAR(w.I_CODE) AS I_CODE, SUM({qty_sql}) AS QTY
          FROM {schema}.IAS_ITM_WCODE w
          LEFT JOIN {pend_sql} pend
            ON pend.I_CODE = TO_CHAR(w.I_CODE)
           AND pend.W_CODE = TO_CHAR(w.W_CODE)
          GROUP BY TO_CHAR(w.I_CODE)
        ) st ON st.I_CODE = s.I_CODE
        WHERE NVL(im.INACTIVE, 0) = 0
          AND NVL(st.QTY, 0) <= 0
        ORDER BY s.SOLD_QTY DESC
        """
    )
    top = [
        {
            "code": str(r.get("I_CODE") or ""),
            "name": str(r.get("I_NAME") or r.get("I_CODE") or "").strip(),
            "sold_qty": round(float(r.get("SOLD_QTY") or 0), 2),
        }
        for r in rows[:limit]
    ]
    return {"item_count": len(rows), "lookback_days": days, "top": top}


def build_stagnant_widget(d_from: date, d_to: date) -> dict:
    from .oracle_unsold import build_unsold_report

    k = build_unsold_report(d_from, d_to).get("kpis") or {}
    return {
        "item_count": int(k.get("item_count") or 0),
        "branch_count": int(k.get("branch_count") or 0),
        "net_total": round(float(k.get("net_total") or 0), 2),
    }


# ── أحدث المستندات ────────────────────────────────────────────────────


def _recent_sales(kind: str, limit: int) -> list[dict]:
    from .oracle_stock import _branch_names, _fetch_all, _pos_owner

    pos = _pos_owner()
    if kind == "sale":
        table, no_col, date_col, amt_col = "IAS_POS_BILL_MST", "BILL_NO", "BILL_DATE", "BILL_AMT"
    else:
        table, no_col, date_col, amt_col = (
            "IAS_POS_RT_BILL_MST",
            "RT_BILL_NO",
            "RT_BILL_DATE",
            "RT_BILL_AMT",
        )
    rows = _fetch_all(
        f"""
        SELECT * FROM (
          SELECT {no_col} AS DOC_NO, TO_CHAR(BRN_NO) AS BRN_NO,
                 {amt_col} AS AMOUNT, NVL(POSTED, 0) AS POSTED,
                 NVL(AD_DATE, {date_col}) AS AT
          FROM {pos}.{table}
          WHERE {date_col} >= TRUNC(SYSDATE) - 1
            AND NVL(HUNG, 0) = 0
          ORDER BY NVL(AD_DATE, {date_col}) DESC
        ) WHERE ROWNUM <= :lim
        """,
        {"lim": int(limit)},
    )
    names = _branch_names()
    return [
        {
            "type": kind,
            "ref": str(r.get("DOC_NO") or ""),
            "location": names.get(str(r.get("BRN_NO") or ""), str(r.get("BRN_NO") or "")),
            "posted": bool(int(r.get("POSTED") or 0)),
            "amount": round(float(r.get("AMOUNT") or 0), 2),
            "qty": None,
            "at": r.get("AT"),
        }
        for r in rows
    ]


def _recent_purchases(limit: int) -> list[dict]:
    from .oracle_stock import _branch_names, _fetch_all, _schema

    schema = _schema()
    rows = _fetch_all(
        f"""
        SELECT * FROM (
          SELECT BILL_NO AS DOC_NO, V_NAME, TO_CHAR(BRN_NO) AS BRN_NO,
                 BILL_AMT AS AMOUNT, NVL(BILL_POST, 0) AS POSTED,
                 NVL(AD_DATE, BILL_DATE) AS AT
          FROM {schema}.IAS_PI_BILL_MST
          WHERE BILL_DATE >= TRUNC(SYSDATE) - 7
            AND NVL(HUNG, 0) = 0
          ORDER BY NVL(AD_DATE, BILL_DATE) DESC
        ) WHERE ROWNUM <= :lim
        """,
        {"lim": int(limit)},
    )
    names = _branch_names()
    out = []
    for r in rows:
        brn = str(r.get("BRN_NO") or "")
        vendor = str(r.get("V_NAME") or "").strip()
        branch = names.get(brn, brn)
        out.append(
            {
                "type": "purchase",
                "ref": str(r.get("DOC_NO") or ""),
                "location": f"{vendor} · {branch}" if vendor else branch,
                "posted": bool(int(r.get("POSTED") or 0)),
                "amount": round(float(r.get("AMOUNT") or 0), 2),
                "qty": None,
                "at": r.get("AT"),
            }
        )
    return out


def _recent_transfers(limit: int) -> list[dict]:
    """تحويلات صادرة — TR_AMT صفر في هذه البيئة فنعرض الكمية لا القيمة."""
    from .oracle_stock import _fetch_all, _schema

    schema = _schema()
    rows = _fetch_all(
        f"""
        SELECT * FROM (
          SELECT m.TR_NO AS DOC_NO,
                 TO_CHAR(NVL(m.F_W_CODE, m.W_CODE)) AS F_W,
                 TO_CHAR(m.T_W_CODE) AS T_W,
                 NVL(m.TR_POST, 0) AS POSTED,
                 NVL(m.AD_DATE, m.TR_DATE) AS AT,
                 (SELECT ROUND(SUM(NVL(d.I_QTY, 0)), 2)
                  FROM {schema}.IAS_WHTRNS_DTL d
                  WHERE d.TR_NO = m.TR_NO
                    AND d.TR_SER = m.TR_SER
                    AND d.TR_TYPE = m.TR_TYPE
                    AND d.TR_INOUT_TYPE = m.TR_INOUT_TYPE) AS QTY
          FROM {schema}.IAS_WHTRNS_MST m
          WHERE m.TR_DATE >= TRUNC(SYSDATE) - 7
            AND m.TR_INOUT_TYPE = 1
            AND NVL(m.HUNG, 0) = 0
          ORDER BY NVL(m.AD_DATE, m.TR_DATE) DESC
        ) WHERE ROWNUM <= :lim
        """,
        {"lim": int(limit)},
    )
    return [
        {
            "type": "transfer",
            "ref": str(r.get("DOC_NO") or ""),
            "location": f"{r.get('F_W') or '—'} ← {r.get('T_W') or '—'}",
            "posted": bool(int(r.get("POSTED") or 0)),
            "amount": None,
            "qty": round(float(r.get("QTY") or 0), 2),
            "at": r.get("AT"),
        }
        for r in rows
    ]


RECENT_TYPE_LABELS = {
    "sale": "فاتورة بيع",
    "return": "مرتجع بيع",
    "purchase": "فاتورة شراء",
    "transfer": "تحويل مخزني",
}


def build_recent_widget(allowed_types: set[str], limit: int = 6) -> dict:
    """أحدث مستندات كل نوع مسموح — فواتير نقاط البيع وحدها تملأ الجدول في دقيقة،
    لذا نأخذ حصة متساوية لكل نوع ثم نرتب بوقت الإدخال."""
    types = [t for t in RECENT_DOC_SCREENS if t in allowed_types]
    if not types:
        return {"rows": []}
    per_type = max(1, -(-limit // len(types)))
    docs: list[dict] = []
    for kind in types:
        if kind in ("sale", "return"):
            docs += _recent_sales(kind, per_type)
        elif kind == "purchase":
            docs += _recent_purchases(per_type)
        elif kind == "transfer":
            docs += _recent_transfers(per_type)
    docs.sort(key=lambda d: d.get("at") or datetime.min, reverse=True)
    out = []
    for d in docs[:limit]:
        at = d.get("at")
        out.append(
            {
                **d,
                "type_label": RECENT_TYPE_LABELS.get(d["type"], d["type"]),
                "at": at.isoformat(timespec="minutes") if hasattr(at, "isoformat") else "",
            }
        )
    return {"rows": out}


# ── اتجاه شهري ────────────────────────────────────────────────────────


def build_month_point(year: int, month: int, series: set[str]) -> dict:
    d_from, d_to = month_bounds(year, month)
    point: dict[str, Any] = {
        "month": f"{year:04d}-{month:02d}",
        "partial": d_to < (date(year + (month // 12), (month % 12) + 1, 1) - timedelta(days=1)),
    }
    if "sales" in series:
        point["sales"] = _channel_totals(d_from, d_to)["total"]
    if "purchases" in series:
        from .oracle_purchases import build_purchase_dashboard

        k = build_purchase_dashboard(d_from, d_to).get("kpis") or {}
        point["purchases"] = round(float(k.get("total_amount") or 0), 2)
    return point
