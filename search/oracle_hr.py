"""الموارد البشرية: الرواتب والمصروفات على العاملين من أوراكل — قراءة فقط.

المصدران:
- مسير الرواتب في نظام الموارد البشرية (HRS_SLRY_CALC_DTL للشهر الحالي، و..._HSTRY للأشهر المرحّلة).
  البنود: 1 الأساسي · 2/3/4/59 البدلات · 52 مبالغ زائدة · 20 إجمالي الاستحقاق · 40 الاستقطاعات · 50 الصافي.
- قيود الحسابات العامة لحسابات «تكاليف العاملين» (الحساب 531) في IAS_POST_DTL.
الفترة تُؤخذ بالأشهر الكاملة لأن المسير شهري.
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

# بنود البدلات في المسير (سكن، مواصلات، تغذية، بدلات أخرى)
_ALLOWANCE_ARTICLES = (2, 3, 4, 59)


def _money(value: float) -> str:
    return f"{round(value, 2) + 0.0:,.2f}"


def _whole(value: float) -> str:
    return f"{round(value) + 0:,}"


def _months_word(n: int) -> str:
    return {1: "شهر واحد", 2: "شهران"}.get(n, f"{n} أشهر" if 3 <= n <= 10 else f"{n} شهراً")


def _pct(num: float, den: float) -> str:
    return f"{num / den * 100:.1f}%" if den else "—"


def whole_months(date_from: date, date_to: date) -> tuple[date, date]:
    """بداية شهر «من» ونهاية شهر «إلى» (حدّ علوي حصري)."""
    start = date_from.replace(day=1)
    end = (date_to.replace(day=28) + timedelta(days=4)).replace(day=1)
    return start, end


def _fetch_monthly(start: date, end: date) -> list[dict]:
    schema = _schema()
    return _fetch_all(
        f"""
        SELECT d.YR AS YR, d.MM AS MM, COUNT(DISTINCT d.EMP_NO) AS EMPS,
               SUM(CASE WHEN d.ARTCL_NO = 1 THEN d.AMT END) AS BASIC,
               SUM(CASE WHEN d.ARTCL_NO IN (2, 3, 4, 59) THEN d.AMT END) AS ALLOW,
               SUM(CASE WHEN d.ARTCL_NO = 20 THEN d.AMT END) AS GROSS,
               SUM(CASE WHEN d.ARTCL_NO = 40 THEN d.AMT END) AS DED,
               SUM(CASE WHEN d.ARTCL_NO = 50 THEN d.AMT END) AS NET
        FROM (
            SELECT YR_NO_HRS AS YR, PRD_NO_HRS AS MM, EMP_NO, ARTCL_NO, AMT, DOC_DATE
            FROM {schema}.HRS_SLRY_CALC_DTL
            UNION ALL
            SELECT YR_NO_HRS, PRD_NO_HRS, EMP_NO, ARTCL_NO, AMT, DOC_DATE
            FROM {schema}.HRS_SLRY_CALC_DTL_HSTRY
        ) d
        WHERE d.DOC_DATE >= :f AND d.DOC_DATE < :t
        GROUP BY d.YR, d.MM
        ORDER BY d.YR, d.MM
        """,
        {"f": start, "t": end},
    )


def _fetch_employees(start: date, end: date) -> list[dict]:
    schema = _schema()
    return _fetch_all(
        f"""
        SELECT d.EMP_NO AS EMP_NO, MAX(e.EMP_L_NM) AS NAME, MAX(e.STRT_WRK_DATE) AS START_DATE,
               COUNT(DISTINCT d.MM) AS MONTHS, MAX(d.MM) AS LAST_MM,
               SUM(CASE WHEN d.ARTCL_NO = 1 THEN d.AMT END) AS BASIC,
               SUM(CASE WHEN d.ARTCL_NO IN (2, 3, 4, 59) THEN d.AMT END) AS ALLOW,
               SUM(CASE WHEN d.ARTCL_NO = 20 THEN d.AMT END) AS GROSS,
               SUM(CASE WHEN d.ARTCL_NO = 40 THEN d.AMT END) AS DED,
               SUM(CASE WHEN d.ARTCL_NO = 50 THEN d.AMT END) AS NET
        FROM (
            SELECT PRD_NO_HRS AS MM, EMP_NO, ARTCL_NO, AMT, DOC_DATE
            FROM {schema}.HRS_SLRY_CALC_DTL
            UNION ALL
            SELECT PRD_NO_HRS, EMP_NO, ARTCL_NO, AMT, DOC_DATE
            FROM {schema}.HRS_SLRY_CALC_DTL_HSTRY
        ) d
        LEFT JOIN {schema}.S_EMP e ON e.EMP_NO = d.EMP_NO
        WHERE d.DOC_DATE >= :f AND d.DOC_DATE < :t
        GROUP BY d.EMP_NO
        ORDER BY NET DESC NULLS LAST, NAME
        """,
        {"f": start, "t": end},
    )


def _fetch_gl_monthly(start: date, end: date) -> list[dict]:
    schema = _schema()
    return _fetch_all(
        f"""
        SELECT EXTRACT(YEAR FROM p.DOC_DATE) AS YR, EXTRACT(MONTH FROM p.DOC_DATE) AS MM,
               SUM(NVL(p.DR_AMT, 0) - NVL(p.CR_AMT, 0)) AS AMT
        FROM {schema}.IAS_POST_DTL p
        WHERE p.A_CODE LIKE '531%'
          AND p.DOC_DATE >= :f AND p.DOC_DATE < :t
        GROUP BY EXTRACT(YEAR FROM p.DOC_DATE), EXTRACT(MONTH FROM p.DOC_DATE)
        ORDER BY 1, 2
        """,
        {"f": start, "t": end},
    )


def _fetch_gl_accounts(start: date, end: date) -> list[dict]:
    schema = _schema()
    return _fetch_all(
        f"""
        SELECT p.A_CODE AS CODE, MAX(a.A_NAME) AS NAME,
               SUM(NVL(p.DR_AMT, 0) - NVL(p.CR_AMT, 0)) AS AMT
        FROM {schema}.IAS_POST_DTL p
        LEFT JOIN {schema}.ACCOUNT a ON a.A_CODE = p.A_CODE
        WHERE p.A_CODE LIKE '531%'
          AND p.DOC_DATE >= :f AND p.DOC_DATE < :t
        GROUP BY p.A_CODE
        HAVING ABS(SUM(NVL(p.DR_AMT, 0) - NVL(p.CR_AMT, 0))) > 0.005
        ORDER BY AMT DESC
        """,
        {"f": start, "t": end},
    )


def _num(row: dict, key: str) -> float:
    return float(row.get(key) or 0)


def _monthly_table(payroll: list[dict], gl: list[dict]) -> dict:
    by_key: dict[tuple[int, int], dict] = {}
    for r in payroll:
        key = (int(r["YR"]), int(r["MM"]))
        by_key[key] = {
            "emps": int(r.get("EMPS") or 0),
            "basic": _num(r, "BASIC"),
            "allow": _num(r, "ALLOW"),
            "gross": _num(r, "GROSS"),
            "ded": _num(r, "DED"),
            "net": _num(r, "NET"),
            "gl": 0.0,
            "has_payroll": True,
        }
    for r in gl:
        key = (int(r["YR"]), int(r["MM"]))
        row = by_key.setdefault(
            key,
            {"emps": 0, "basic": 0.0, "allow": 0.0, "gross": 0.0, "ded": 0.0, "net": 0.0, "gl": 0.0, "has_payroll": False},
        )
        row["gl"] += _num(r, "AMT")
    rows = []
    total = {"basic": 0.0, "allow": 0.0, "gross": 0.0, "ded": 0.0, "net": 0.0, "gl": 0.0}
    for (yr, mm), r in sorted(by_key.items()):
        other = r["gross"] - r["basic"] - r["allow"]
        for k in total:
            total[k] += r[k]
        rows.append(
            {
                "month": f"{_MONTHS_AR[mm - 1]} {yr}",
                "has_payroll": r["has_payroll"],
                "emps": r["emps"],
                "basic": _whole(r["basic"]),
                "allow": _whole(r["allow"] + other),  # الزائد على الأساسي والبدلات يُحسب مع البدلات
                "gross": _whole(r["gross"]),
                "ded": _whole(r["ded"]),
                "net": _whole(r["net"]),
                "gl": _whole(r["gl"]),
            }
        )
    payroll_months = [x for x in rows if x["has_payroll"]]
    return {
        "rows": rows,
        "total": {
            "basic": _whole(total["basic"]),
            "allow": _whole(total["gross"] - total["basic"]),
            "gross": _whole(total["gross"]),
            "ded": _whole(total["ded"]),
            "net": _whole(total["net"]),
            "gl": _whole(total["gl"]),
        }
        if rows
        else None,
        "payroll_months": len(payroll_months),
        "raw_total": total,
    }


def _employee_table(rows: list[dict], last_month: int | None) -> dict:
    out = []
    total = {"basic": 0.0, "allow": 0.0, "gross": 0.0, "ded": 0.0, "net": 0.0}
    for r in rows:
        basic, gross = _num(r, "BASIC"), _num(r, "GROSS")
        net, ded = _num(r, "NET"), _num(r, "DED")
        months = int(r.get("MONTHS") or 0)
        last = int(r.get("LAST_MM") or 0)
        start = r.get("START_DATE")
        total["basic"] += basic
        total["allow"] += gross - basic
        total["gross"] += gross
        total["ded"] += ded
        total["net"] += net
        left = bool(last_month and last < last_month)
        out.append(
            {
                "no": int(r["EMP_NO"]),
                "name": str(r.get("NAME") or f"موظف {r['EMP_NO']}").strip(),
                "start": start.date().isoformat() if hasattr(start, "date") else "",
                "months": months,
                "basic": _whole(basic),
                "allow": _whole(gross - basic),
                "ded": _whole(ded),
                "gross": _whole(gross),
                "net": _whole(net),
                "avg": _whole(gross / months) if months else "—",
                "status": f"آخر راتب {_MONTHS_AR[last - 1]}" if left and 1 <= last <= 12 else "على الراتب",
                "left": left,
            }
        )
    return {
        "rows": out,
        "total": {k: _whole(v) for k, v in total.items()} if out else None,
        "left_count": sum(1 for r in out if r["left"]),
    }


def _gl_table(rows: list[dict]) -> dict:
    total = sum(max(_num(r, "AMT"), 0.0) for r in rows)
    items = []
    for r in rows:
        amt = _num(r, "AMT")
        items.append(
            {
                "code": str(r.get("CODE") or "").strip(),
                "name": str(r.get("NAME") or r.get("CODE") or "").strip().strip('"'),
                "amount": _money(amt),
                "share": _pct(max(amt, 0.0), total),
                "width": f"{(max(amt, 0.0) / total * 100) if total else 0:.1f}%",
            }
        )
    return {"rows": items, "total": _money(sum(_num(r, "AMT") for r in rows)) if items else None}


def _reading(monthly: dict, employees: dict, gl_rows: list[dict], gl_months: list[tuple[int, int]],
             payroll_months: list[tuple[int, int]]) -> list[dict]:
    notes: list[dict] = []
    t = monthly["raw_total"]
    rows = [r for r in monthly["rows"] if r["has_payroll"]]
    if not rows:
        return [{"tone": "warn", "text": "لا يوجد مسير رواتب مسجّل في هذه الفترة."}]
    if t["gross"] > 0:
        notes.append(
            {
                "tone": "",
                "text": f"إجمالي الاستحقاق {_money(t['gross'])} خلال {_months_word(len(rows))}؛ البدلات والزيادات "
                f"{_pct(t['gross'] - t['basic'], t['gross'])} منه والأساسي {_pct(t['basic'], t['gross'])}.",
            }
        )
    first, last = rows[0], rows[-1]
    if first["emps"] and last["emps"] and first["emps"] != last["emps"]:
        diff = last["emps"] - first["emps"]
        notes.append(
            {
                "tone": "good" if diff < 0 else "warn",
                "text": f"عدد العاملين في المسير {'انخفض' if diff < 0 else 'ارتفع'} من {first['emps']} "
                f"({first['month']}) إلى {last['emps']} ({last['month']}).",
            }
        )
    if employees["left_count"]:
        notes.append(
            {
                "tone": "",
                "text": f"{employees['left_count']} موظفاً ظهروا في المسير ثم غابوا عن آخر شهر؛ "
                "حالتهم في الجدول «آخر راتب …».",
            }
        )
    if t["ded"] > 0:
        notes.append({"tone": "", "text": f"الاستقطاعات (غياب وسلف ونحوها) {_money(t['ded'])}، أي {_pct(t['ded'], t['gross'])} من الاستحقاق."})
    if gl_months and payroll_months and max(gl_months) > max(payroll_months):
        y, m = max(payroll_months)
        gy, gm = max(gl_months)
        notes.append(
            {
                "tone": "warn",
                "text": f"مسير الرواتب مسجّل حتى {_MONTHS_AR[m - 1]} {y} فقط، بينما قيود الحسابات حتى "
                f"{_MONTHS_AR[gm - 1]} {gy}؛ أشهر ما بعد المسير ظاهرة من الحسابات وحدها.",
            }
        )
    gl_total = sum(max(float(r.get("AMT") or 0), 0.0) for r in gl_rows)
    if gl_total > 0:
        salary = next((float(r["AMT"]) for r in gl_rows if str(r.get("CODE")) == "5311010001"), 0.0)
        others = [r for r in gl_rows if str(r.get("CODE")) != "5311010001"]
        top = str(others[0].get("NAME") or "").strip().strip('"') if others else ""
        notes.append(
            {
                "tone": "",
                "text": f"من تكلفة العاملين في الحسابات ({_money(gl_total)}) تذهب {_pct(salary, gl_total)} للرواتب الأساسية "
                f"والباقي لمصروفات أخرى{(' أكبرها «' + top + '»') if top else ''}.",
            }
        )
    return notes


def build_hr_overview(date_from, date_to) -> dict:
    d_from, d_to = _as_date(date_from), _as_date(date_to)
    start, end = whole_months(d_from, d_to)
    cache_key = f"hr:overview:v3:{start}:{end}"
    cached = cache.get(cache_key)
    if isinstance(cached, dict):
        return cached

    payroll = _fetch_monthly(start, end)
    gl = _fetch_gl_monthly(start, end)
    monthly = _monthly_table(payroll, gl)
    payroll_months = [(int(r["YR"]), int(r["MM"])) for r in payroll]
    gl_months = [(int(r["YR"]), int(r["MM"])) for r in gl]
    last_mm = max((m for _y, m in payroll_months), default=None)
    employees = _employee_table(_fetch_employees(start, end), last_mm)
    gl_rows = _fetch_gl_accounts(start, end)
    gl_accounts = _gl_table(gl_rows)
    t = monthly["raw_total"]
    rows_with_payroll = [r for r in monthly["rows"] if r["has_payroll"]]
    emp_count = len(employees["rows"])
    months = monthly["payroll_months"]
    gross = t["gross"]
    result = {
        "period_label": f"{start.isoformat()} → {(end - timedelta(days=1)).isoformat()}",
        "period_from": start.isoformat(),
        "period_to": (end - timedelta(days=1)).isoformat(),
        "kpis": {
            "gross_display": _money(gross),
            "net_display": _money(t["net"]),
            "ded_display": _money(t["ded"]),
            "emp_count": emp_count,
            "months": months,
            "avg_display": _whole(gross / emp_count / months) if emp_count and months else "—",
            "last_headcount": rows_with_payroll[-1]["emps"] if rows_with_payroll else 0,
        },
        "composition": [
            {"tone": "basic", "label": "الأساسي", "share": _pct(t["basic"], gross),
             "width": f"{(t['basic'] / gross * 100) if gross else 0:.2f}%"},
            {"tone": "allow", "label": "البدلات والزيادات", "share": _pct(gross - t["basic"], gross),
             "width": f"{((gross - t['basic']) / gross * 100) if gross else 0:.2f}%"},
        ],
        "monthly": monthly,
        "employees": employees,
        "gl": gl_accounts,
        "gl_total_display": gl_accounts["total"] or "0.00",
        "reading": _reading(monthly, employees, gl_rows, gl_months, payroll_months),
    }
    result["monthly"] = {k: v for k, v in monthly.items() if k != "raw_total"}
    cache.set(cache_key, result, _CACHE_TTL)
    return result
