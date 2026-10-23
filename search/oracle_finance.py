"""الربحية والقراءة المالية من قيود الترحيل في أوراكل (IAS_POST_DTL) — قراءة فقط.

دليل الحسابات في Onyx: 1 أصول · 2 خصوم · 3 حقوق ملكية · 4 إيرادات · 5 مصروفات.
المبالغ تُجمع على المستوى الثالث (أول 3 أرقام من A_CODE) داخل أوراكل.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from django.core.cache import cache

from .oracle_stock import _as_date, _fetch_all, _schema

_CACHE_TTL = 1800

_MONTHS_AR = (
    "يناير", "فبراير", "مارس", "أبريل", "مايو", "يونيو",
    "يوليو", "أغسطس", "سبتمبر", "أكتوبر", "نوفمبر", "ديسمبر",
)


def _money(value: float) -> str:
    # + 0.0 يمنع ظهور «-0.00»
    return f"{round(value, 2) + 0.0:,.2f}"


def _whole(value: float) -> str:
    """أرقام الجدول الشهري بلا هللات: المقارنة بين الأشهر لا تحتاجها."""
    return f"{round(value) + 0:,}"


def _pct(value: float | None, digits: int = 1) -> str:
    return "—" if value is None else f"{value:.{digits}f}%"


def _ratio(num: float, den: float) -> float | None:
    return num / den if den else None


def previous_span(d_from: date, d_to: date) -> tuple[date, date] | None:
    """الفترة السابقة؛ None إن خرجت عن سنة البيانات (مخطط أوراكل سنوي).

    فترة تبدأ أول الشهر وتنتهي داخله تُقارن بنفس الأيام من الشهر السابق
    (أكتوبر 1–23 ← سبتمبر 1–23، وسبتمبر كاملاً ← أغسطس كاملاً)، وغيرها بنفس الطول.
    """
    if d_from.day == 1 and (d_from.year, d_from.month) == (d_to.year, d_to.month):
        p_to_month_end = d_from - timedelta(days=1)
        p_from = p_to_month_end.replace(day=1)
        month_end = (d_to.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)
        p_to = p_to_month_end if d_to == month_end else p_from.replace(
            day=min(d_to.day, p_to_month_end.day)
        )
        return None if p_from.year != d_from.year else (p_from, p_to)
    days = (d_to - d_from).days + 1
    p_to = d_from - timedelta(days=1)
    p_from = p_to - timedelta(days=days - 1)
    if p_from.year != d_from.year:
        return None
    return p_from, p_to


def _account_names(schema: str) -> dict[str, str]:
    rows = _fetch_all(
        f"SELECT A_CODE, A_NAME FROM {schema}.ACCOUNT WHERE A_LEVEL IN (2, 3)",
        {},
    )
    return {
        str(r.get("A_CODE") or "").strip(): str(r.get("A_NAME") or "").strip().strip('"')
        for r in rows
    }


def _fetch_raw(d_from: date, d_to: date, prev: tuple[date, date] | None) -> dict[str, Any]:
    schema = _schema()
    year_start = date(d_to.year, 1, 1)
    p_from, p_to = prev or (d_from, d_from - timedelta(days=1))
    params = {
        "f": d_from,
        "t": d_to + timedelta(days=1),
        "pf": p_from,
        "pt": p_to + timedelta(days=1),
        "ys": year_start,
    }
    # الإيرادات والمصروفات: الفترة، والسابقة، ومن أول السنة (لأيام المخزون والموردين)
    pnl = _fetch_all(
        f"""
        SELECT SUBSTR(p.A_CODE, 1, 3) AS K3,
               SUM(CASE WHEN p.DOC_DATE >= :f AND p.DOC_DATE < :t
                        THEN NVL(p.DR_AMT, 0) - NVL(p.CR_AMT, 0) END) AS CUR,
               SUM(CASE WHEN p.DOC_DATE >= :pf AND p.DOC_DATE < :pt
                        THEN NVL(p.DR_AMT, 0) - NVL(p.CR_AMT, 0) END) AS PREV,
               SUM(NVL(p.DR_AMT, 0) - NVL(p.CR_AMT, 0)) AS YTD
        FROM {schema}.IAS_POST_DTL p
        WHERE SUBSTR(p.A_CODE, 1, 1) IN ('4', '5')
          AND p.DOC_DATE >= :ys
          AND p.DOC_DATE < :t
        GROUP BY SUBSTR(p.A_CODE, 1, 3)
        """,
        params,
    )
    monthly = _fetch_all(
        f"""
        SELECT TO_CHAR(p.DOC_DATE, 'MM') AS MM, SUBSTR(p.A_CODE, 1, 2) AS K2,
               SUM(NVL(p.DR_AMT, 0) - NVL(p.CR_AMT, 0)) AS AMT
        FROM {schema}.IAS_POST_DTL p
        WHERE SUBSTR(p.A_CODE, 1, 1) IN ('4', '5')
          AND p.DOC_DATE >= :ys
          AND p.DOC_DATE < :t
        GROUP BY TO_CHAR(p.DOC_DATE, 'MM'), SUBSTR(p.A_CODE, 1, 2)
        """,
        {"ys": year_start, "t": params["t"]},
    )
    # المركز المالي كما في نهاية الفترة (يشمل قيد أول المدة)
    balance = _fetch_all(
        f"""
        SELECT SUBSTR(p.A_CODE, 1, 3) AS K3,
               SUM(NVL(p.DR_AMT, 0) - NVL(p.CR_AMT, 0)) AS AMT
        FROM {schema}.IAS_POST_DTL p
        WHERE SUBSTR(p.A_CODE, 1, 1) IN ('1', '2', '3')
          AND p.DOC_DATE < :t
        GROUP BY SUBSTR(p.A_CODE, 1, 3)
        """,
        {"t": params["t"]},
    )
    return {
        "names": _account_names(schema),
        "pnl": [
            {
                "k3": str(r.get("K3") or ""),
                "cur": float(r.get("CUR") or 0),
                "prev": float(r.get("PREV") or 0),
                "ytd": float(r.get("YTD") or 0),
            }
            for r in pnl
        ],
        "monthly": [
            {"mm": int(r.get("MM") or 0), "k2": str(r.get("K2") or ""), "amt": float(r.get("AMT") or 0)}
            for r in monthly
        ],
        "balance": {str(r.get("K3") or ""): float(r.get("AMT") or 0) for r in balance},
    }


def _line(label: str, cur: float, prev: float | None, revenue: float, *, kind: str = "item",
          favorable_up: bool = True) -> dict:
    change = None
    if prev is not None and abs(prev) > 0.005:
        change = (cur - prev) / abs(prev) * 100.0
    tone = ""
    if change is not None and abs(change) >= 0.05:
        tone = "up" if (change > 0) == favorable_up else "down"
    return {
        "label": label,
        "kind": kind,
        "cur": cur,
        "cur_display": _money(cur),
        "prev_display": "—" if prev is None else _money(prev),
        "change_display": "—" if change is None else f"{change:+.1f}%",
        "change_tone": tone,
        "of_revenue": _pct(_ratio(cur, revenue) * 100 if revenue else None),
        "negative": cur < 0,
    }


def _income_statement(raw: dict, has_prev: bool) -> dict:
    names = raw["names"]
    by_k3 = {r["k3"]: r for r in raw["pnl"]}

    def total(prefix: str, field: str, sign: float) -> float:
        return sign * sum(r[field] for k, r in by_k3.items() if k.startswith(prefix))

    def side(field: str) -> dict[str, float]:
        revenue = total("41", field, -1)
        cogs = total("51", field, 1)
        selling = total("52", field, 1)
        admin = total("53", field, 1)
        other = total("47", field, -1)
        gross = revenue - cogs
        operating = gross - selling - admin
        return {
            "revenue": revenue, "cogs": cogs, "gross": gross, "selling": selling,
            "admin": admin, "operating": operating, "other": other, "net": operating + other,
        }

    cur = side("cur")
    prev = side("prev") if has_prev else None
    ytd = side("ytd")
    rev = cur["revenue"]

    def p(key: str) -> float | None:
        return prev[key] if prev else None

    lines = [
        _line("إيرادات النشاط (المبيعات)", cur["revenue"], p("revenue"), rev, kind="lead"),
        _line("تكلفة المبيعات", cur["cogs"], p("cogs"), rev, favorable_up=False),
        _line("مجمل الربح", cur["gross"], p("gross"), rev, kind="subtotal"),
        _line("مصروفات بيعية وتسويقية", cur["selling"], p("selling"), rev, favorable_up=False),
        _line("مصروفات عمومية وإدارية", cur["admin"], p("admin"), rev, favorable_up=False),
    ]
    # تفصيل العمومية والإدارية على المستوى الثالث، الأكبر أولاً
    admin_rows = sorted(
        (r for k, r in by_k3.items() if k.startswith("53") and abs(r["cur"]) > 0.005),
        key=lambda r: -r["cur"],
    )
    for r in admin_rows:
        lines.append(
            _line(names.get(r["k3"], r["k3"]), r["cur"], r["prev"] if has_prev else None, rev,
                  kind="detail", favorable_up=False)
        )
    lines += [
        _line("الربح التشغيلي", cur["operating"], p("operating"), rev, kind="subtotal"),
        _line("إيرادات أخرى", cur["other"], p("other"), rev),
        _line("صافي الربح (الخسارة)", cur["net"], p("net"), rev, kind="total"),
    ]
    biggest = admin_rows[0] if admin_rows else None
    return {
        "lines": lines,
        "cur": cur,
        "prev": prev,
        "ytd": ytd,
        "biggest_expense": (
            {"name": names.get(biggest["k3"], biggest["k3"]), "amount": biggest["cur"]} if biggest else None
        ),
    }


def _monthly(raw: dict, d_to: date) -> dict:
    """صفوف الأشهر من أول السنة، وسطر إجمالي بأسفل الجدول (مثل جداول المخزون)."""
    months: dict[int, dict[str, float]] = {}
    for r in raw["monthly"]:
        m = months.setdefault(r["mm"], {"revenue": 0.0, "cogs": 0.0, "opex": 0.0, "other": 0.0})
        if r["k2"] == "41":
            m["revenue"] -= r["amt"]
        elif r["k2"] == "51":
            m["cogs"] += r["amt"]
        elif r["k2"] in ("52", "53"):
            m["opex"] += r["amt"]
        elif r["k2"].startswith("4"):
            m["other"] -= r["amt"]
    def row(label: str, m: dict[str, float]) -> dict:
        gross = m["revenue"] - m["cogs"]
        net = gross - m["opex"] + m["other"]
        return {
            "month": label,
            "revenue_display": _whole(m["revenue"]),
            "gross_display": _whole(gross),
            "margin_display": _pct(_ratio(gross, m["revenue"]) * 100 if m["revenue"] else None),
            "opex_display": _whole(m["opex"]),
            "net_display": _whole(net),
            "net_negative": net < 0,
        }

    rows = [row(_MONTHS_AR[mm - 1], months[mm]) for mm in range(1, d_to.month + 1) if mm in months]
    total = {
        key: sum(m[key] for mm, m in months.items() if mm <= d_to.month)
        for key in ("revenue", "cogs", "opex", "other")
    }
    return {"rows": rows, "total": row("الإجمالي", total) if rows else None}


def _financial_position(raw: dict, ytd_net: float) -> dict:
    bal = raw["balance"]
    names = raw["names"]

    def amt(prefix: str, sign: float = 1) -> float:
        return sign * sum(v for k, v in bal.items() if k.startswith(prefix))

    non_current_assets = amt("11") + amt("12") + amt("13")
    current_assets = amt("16")
    total_assets = non_current_assets + current_assets

    def share(value: float) -> str:
        return _pct(value / total_assets * 100 if total_assets else None)

    def parts(prefix: str, sign: float, *, level: int = 3) -> list[dict]:
        # level=2 يجمع الحسابات تحت أصلها (مثل 11 أصول ثابتة) لتقصير الجدول
        grouped: dict[str, float] = {}
        for k, v in bal.items():
            if k.startswith(prefix):
                grouped[k[:level]] = grouped.get(k[:level], 0.0) + v
        return [
            {"name": names.get(k, k), "amount": sign * v, "amount_display": _money(sign * v),
             "share": share(sign * v)}
            for k, v in sorted(grouped.items())
            if abs(v) > 0.005
        ]
    non_current_liab = amt("21", -1)
    current_liab = amt("22", -1)
    equity = amt("3", -1)
    total_liab = non_current_liab + current_liab
    # قيد أول المدة غير متوازن (أرباح سنة سابقة لم تُقفل) — يظهر سطراً مستقلاً بدل إخفائه
    gap = total_assets - (total_liab + equity + ytd_net)
    inventory = amt("161")
    cash = amt("168")
    return {
        "current_assets": current_assets,
        "current_assets_parts": parts("16", 1),
        "non_current_assets": non_current_assets,
        "non_current_assets_parts": [
            part for prefix in ("11", "12", "13") for part in parts(prefix, 1, level=2)
        ],
        "total_assets": total_assets,
        "current_liab": current_liab,
        "current_liab_parts": parts("22", -1),
        "non_current_liab": non_current_liab,
        "total_liab": total_liab,
        "equity": equity,
        "ytd_net": ytd_net,
        "gap": gap,
        "inventory": inventory,
        "cash": cash,
        "suppliers": amt("223", -1),
        "share": {
            "current_assets": share(current_assets),
            "non_current_assets": share(non_current_assets),
            "current_liab": share(current_liab),
            "non_current_liab": share(non_current_liab),
            "equity": share(equity),
            "ytd_net": share(ytd_net),
            "gap": share(gap),
        },
        "display": {
            "current_assets": _money(current_assets),
            "non_current_assets": _money(non_current_assets),
            "total_assets": _money(total_assets),
            "current_liab": _money(current_liab),
            "non_current_liab": _money(non_current_liab),
            "total_liab": _money(total_liab),
            "equity": _money(equity),
            "ytd_net": _money(ytd_net),
            "gap": _money(gap),
            "total_right": _money(total_liab + equity + ytd_net + gap),
        },
    }


def _ratios(stmt: dict, pos: dict, d_from: date, d_to: date) -> list[dict]:
    cur, ytd = stmt["cur"], stmt["ytd"]
    rev = cur["revenue"]
    ytd_days = (d_to - date(d_to.year, 1, 1)).days + 1
    opex = cur["selling"] + cur["admin"]
    ratio_items = [
        ("هامش مجمل الربح", _ratio(cur["gross"], rev), "pct", 0.20, 0.12),
        ("هامش صافي الربح", _ratio(cur["net"], rev), "pct", 0.03, 0.0),
        ("المصروفات من المبيعات", _ratio(opex, rev), "pct_inv", 0.12, 0.18),
        ("نسبة التداول", _ratio(pos["current_assets"], pos["current_liab"]), "x", 1.2, 1.0),
        ("السيولة السريعة", _ratio(pos["current_assets"] - pos["inventory"], pos["current_liab"]), "x", 1.0, 0.5),
        ("الالتزامات إلى الأصول", _ratio(pos["total_liab"], pos["total_assets"]), "pct_inv", 0.6, 0.85),
        ("أيام المخزون", _ratio(pos["inventory"] * ytd_days, ytd["cogs"]), "days_inv", 45, 75),
        ("أيام سداد الموردين", _ratio(pos["suppliers"] * ytd_days, ytd["cogs"]), "days", None, None),
    ]
    out = []
    for label, value, fmt, good, bad in ratio_items:
        if value is None:
            display, tone = "—", ""
        elif fmt in ("pct", "pct_inv"):
            display = f"{value * 100:.1f}%"
        elif fmt == "x":
            display = f"{value:.2f}"
        else:
            # الوحدة في التسمية («أيام …»)؛ الرقم وحده يبقى سليماً داخل bdi
            display = f"{value:.0f}"
        tone = ""
        if value is not None and good is not None:
            if fmt in ("pct_inv", "days_inv"):
                tone = "good" if value <= good else ("bad" if value > bad else "warn")
            else:
                tone = "good" if value >= good else ("bad" if value < bad else "warn")
        out.append({"label": label, "display": display, "tone": tone})
    return out


def _reading(stmt: dict, pos: dict, d_to: date) -> list[dict]:
    """قراءة مالية مختصرة بلغة الإدارة: ماذا تقول الأرقام وأين الضغط."""
    cur, ytd = stmt["cur"], stmt["ytd"]
    rev = cur["revenue"]
    notes: list[dict] = []
    if rev <= 0:
        return [{"tone": "warn", "text": "لا توجد إيرادات مرحّلة في هذه الفترة لقراءتها."}]

    gm = cur["gross"] / rev
    opex = cur["selling"] + cur["admin"]
    notes.append(
        {
            "tone": "good" if gm >= 0.2 else "warn",
            "text": f"من كل 100 ريال مبيعات يبقى {gm * 100:.1f} ريال بعد تكلفة البضاعة، "
            f"وتستهلك المصروفات التشغيلية {opex / rev * 100:.1f} ريال منها.",
        }
    )
    if cur["net"] < 0:
        notes.append(
            {
                "tone": "bad",
                "text": f"الفترة خاسرة بـ {_money(-cur['net'])}: مجمل الربح {_money(cur['gross'])} "
                f"لا يغطي المصروفات التشغيلية {_money(opex)}.",
            }
        )
    else:
        notes.append({"tone": "good", "text": f"الفترة رابحة بصافي {_money(cur['net'])} ({cur['net'] / rev * 100:.1f}% من المبيعات)."})
    if gm > 0:
        breakeven = (opex - cur["other"]) / gm
        gap = breakeven - rev
        notes.append(
            {
                "tone": "good" if gap <= 0 else "warn",
                "text": (
                    f"بهذا الهامش تحتاج الفترة مبيعات {_money(breakeven)} لتغطية المصروفات، "
                    + (f"أي أكثر من الحالية بـ {gap / rev * 100:.1f}%." if gap > 0 else "والمبيعات الحالية تتجاوز ذلك.")
                ),
            }
        )
    big = stmt.get("biggest_expense")
    if big and big["amount"] > 0:
        notes.append(
            {
                "tone": "warn" if big["amount"] / rev > 0.08 else "",
                "text": f"أكبر بند مصروف: {big['name']} بـ {_money(big['amount'])} ({big['amount'] / rev * 100:.1f}% من المبيعات).",
            }
        )
    prev = stmt.get("prev")
    if prev and prev["revenue"] > 0:
        rev_change = (rev - prev["revenue"]) / prev["revenue"] * 100
        gm_prev = prev["gross"] / prev["revenue"]
        notes.append(
            {
                "tone": "good" if rev_change >= 0 else "warn",
                "text": f"المبيعات {'ارتفعت' if rev_change >= 0 else 'انخفضت'} {abs(rev_change):.1f}% عن الفترة السابقة، "
                f"وتحرّك هامش مجمل الربح {(gm - gm_prev) * 100:+.1f} نقطة.",
            }
        )
    if pos["current_liab"] > 0:
        cr = pos["current_assets"] / pos["current_liab"]
        notes.append(
            {
                "tone": "good" if cr >= 1.2 else ("bad" if cr < 1 else "warn"),
                "text": f"نسبة التداول {cr:.2f}: الأصول المتداولة "
                + ("تغطي" if cr >= 1 else "لا تغطي")
                + f" الالتزامات المتداولة ({_money(pos['current_assets'])} مقابل {_money(pos['current_liab'])}).",
            }
        )
    ytd_days = (d_to - date(d_to.year, 1, 1)).days + 1
    if ytd["cogs"] > 0 and pos["inventory"] > 0:
        days = pos["inventory"] * ytd_days / ytd["cogs"]
        notes.append(
            {
                "tone": "good" if days <= 45 else "warn",
                "text": f"المخزون يغطي نحو {days:.0f} يوماً من تكلفة المبيعات.",
            }
        )
    return notes


_CLASS_NAMES = {
    "1": "الأصول",
    "2": "الخصوم",
    "3": "حقوق الملكية",
    "4": "الإيرادات",
    "5": "المصروفات",
}


def _fetch_trial(d_from: date, d_to: date) -> list[dict]:
    """ميزان المراجعة على الحسابات التحليلية (الترحيل يتم على المستوى السادس فقط)."""
    schema = _schema()
    return _fetch_all(
        f"""
        SELECT p.A_CODE AS CODE, MAX(a.A_NAME) AS NAME,
               SUM(CASE WHEN p.DOC_TYPE = 0 OR p.DOC_DATE < :f
                        THEN NVL(p.DR_AMT, 0) - NVL(p.CR_AMT, 0) END) AS OPENING,
               SUM(CASE WHEN p.DOC_TYPE <> 0 AND p.DOC_DATE >= :f THEN NVL(p.DR_AMT, 0) END) AS DR,
               SUM(CASE WHEN p.DOC_TYPE <> 0 AND p.DOC_DATE >= :f THEN NVL(p.CR_AMT, 0) END) AS CR
        FROM {schema}.IAS_POST_DTL p
        LEFT JOIN {schema}.ACCOUNT a ON a.A_CODE = p.A_CODE
        WHERE p.DOC_DATE < :t
        GROUP BY p.A_CODE
        ORDER BY p.A_CODE
        """,
        {"f": d_from, "t": d_to + timedelta(days=1)},
    )


def _side(value: float) -> dict:
    """الرصيد يُعرض موجباً مع طبيعته (مدين/دائن) كما في ميزان المراجعة."""
    if abs(value) < 0.005:
        return {"display": "0.00", "side": ""}
    return {"display": _money(abs(value)), "side": "مدين" if value > 0 else "دائن"}


def _trial_balance(rows: list[dict]) -> dict:
    groups: dict[str, dict] = {}
    totals = {"dr": 0.0, "cr": 0.0, "close_dr": 0.0, "close_cr": 0.0, "open_dr": 0.0, "open_cr": 0.0}
    for r in rows:
        code = str(r.get("CODE") or "").strip()
        opening = float(r.get("OPENING") or 0)
        dr = float(r.get("DR") or 0)
        cr = float(r.get("CR") or 0)
        closing = opening + dr - cr
        if max(abs(opening), dr, cr, abs(closing)) < 0.005:
            continue
        cls = code[:1]
        group = groups.setdefault(
            cls,
            {"name": _CLASS_NAMES.get(cls, cls), "rows": [], "dr": 0.0, "cr": 0.0, "closing": 0.0},
        )
        group["rows"].append(
            {
                "code": code,
                "name": str(r.get("NAME") or code).strip().strip('"'),
                "opening": _side(opening),
                "dr_display": _money(dr),
                "cr_display": _money(cr),
                "closing": _side(closing),
            }
        )
        group["dr"] += dr
        group["cr"] += cr
        group["closing"] += closing
        totals["dr"] += dr
        totals["cr"] += cr
        totals["open_dr" if opening > 0 else "open_cr"] += abs(opening)
        totals["close_dr" if closing > 0 else "close_cr"] += abs(closing)
    out_groups = []
    for cls in sorted(groups):
        g = groups[cls]
        out_groups.append(
            {
                "name": g["name"],
                "count": len(g["rows"]),
                "rows": g["rows"],
                "dr_display": _money(g["dr"]),
                "cr_display": _money(g["cr"]),
                "closing": _side(g["closing"]),
            }
        )
    return {
        "groups": out_groups,
        "count": sum(g["count"] for g in out_groups),
        "totals": {
            "open_dr": _money(totals["open_dr"]),
            "open_cr": _money(totals["open_cr"]),
            "dr": _money(totals["dr"]),
            "cr": _money(totals["cr"]),
            "close_dr": _money(totals["close_dr"]),
            "close_cr": _money(totals["close_cr"]),
        },
        # الفرق = قيد أول المدة غير المتوازن؛ حركات الفترة نفسها متوازنة
        "close_gap": _money(totals["close_dr"] - totals["close_cr"]),
        "balanced": abs(totals["close_dr"] - totals["close_cr"]) < 0.01,
    }


def build_profitability(date_from, date_to) -> dict:
    d_from, d_to = _as_date(date_from), _as_date(date_to)
    cache_key = f"finance:profitability:v6:{d_from}:{d_to}"
    cached = cache.get(cache_key)
    if isinstance(cached, dict):
        return cached

    prev = previous_span(d_from, d_to)
    raw = _fetch_raw(d_from, d_to, prev)
    stmt = _income_statement(raw, prev is not None)
    pos = _financial_position(raw, stmt["ytd"]["net"])
    cur = stmt["cur"]
    rev = cur["revenue"]
    costs = cur["cogs"] + cur["selling"] + cur["admin"]
    scale = max(rev, costs, 1.0)
    result = {
        "period_label": f"{d_from.isoformat()} → {d_to.isoformat()}",
        "prev_label": f"{prev[0].isoformat()} → {prev[1].isoformat()}" if prev else "",
        "kpis": {
            "net": cur["net"],
            "net_display": _money(cur["net"]),
            "revenue_display": _money(rev),
            "gross_margin": _pct(_ratio(cur["gross"], rev) * 100 if rev else None),
            "net_margin": _pct(_ratio(cur["net"], rev) * 100 if rev else None),
        },
        # شريط «أين تذهب المبيعات»: التكاليف مقابل خط الإيرادات
        "flow": {
            "segments": [
                {"tone": "cogs", "label": "تكلفة المبيعات", "width": f"{cur['cogs'] / scale * 100:.3f}%",
                 "share": _pct(_ratio(cur["cogs"], rev) * 100 if rev else None)},
                {"tone": "selling", "label": "بيعية وتسويقية", "width": f"{max(cur['selling'], 0) / scale * 100:.3f}%",
                 "share": _pct(_ratio(cur["selling"], rev) * 100 if rev else None)},
                {"tone": "admin", "label": "عمومية وإدارية", "width": f"{max(cur['admin'], 0) / scale * 100:.3f}%",
                 "share": _pct(_ratio(cur["admin"], rev) * 100 if rev else None)},
            ]
            + (
                [{"tone": "profit", "label": "ربح تشغيلي", "width": f"{cur['operating'] / scale * 100:.3f}%",
                  "share": _pct(_ratio(cur["operating"], rev) * 100 if rev else None)}]
                if cur["operating"] > 0
                else []
            ),
            "revenue_mark": f"{rev / scale * 100:.3f}%",
            "overflow": costs > rev,
        },
        "statement": stmt["lines"],
        "monthly": _monthly(raw, d_to),
        "position": pos,
        "ratios": _ratios(stmt, pos, d_from, d_to),
        "reading": _reading(stmt, pos, d_to),
        "trial": _trial_balance(_fetch_trial(d_from, d_to)),
    }
    cache.set(cache_key, result, _CACHE_TTL)
    return result
