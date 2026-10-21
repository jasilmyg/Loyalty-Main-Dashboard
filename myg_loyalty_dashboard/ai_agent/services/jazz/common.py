"""
Shared helpers for the Jazz AI specialist tools (RCA, forecasting, memory …).

Everything here is read-only against ClickHouse and uses the same safe client factory as the rest of the app.
"""

import logging
import threading
import time
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional, Sequence

logger = logging.getLogger(__name__)

# Internal / transfer data that the dashboards exclude from sales (same rule as the assistant prompt).
SALES_EXCLUSION_SQL = (
    "NOT (upper({a}invoice_no) LIKE '%SMC%' OR upper({a}invoice_no) LIKE '%EI%' "
    "OR upper({a}branch) IN ('HEAD OFFICE','UG SMART CHOICE','3GH','CSB','CSBW'))"
)

CH_ANALYSIS_SETTINGS = {"readonly": 1, "max_execution_time": 45, "max_result_rows": 2_000_000, "result_overflow_mode": "break"}


def sales_exclusion(alias: str = "") -> str:
    return SALES_EXCLUSION_SQL.format(a=f"{alias}." if alias else "")


def esc(value: Any) -> str:
    """Escape a value for use inside a single-quoted ClickHouse string literal."""
    return str(value).replace("\\", "\\\\").replace("'", "\\'")


def ch_query(sql: str):
    """Run a read-only query and return the clickhouse_connect result (raises on failure)."""
    from analytics.clickhouse_service import get_ch_client
    client = get_ch_client()
    if not client:
        raise RuntimeError("ClickHouse client could not connect.")
    return client.query(sql, settings=CH_ANALYSIS_SETTINGS)


def ch_df(sql: str):
    """Run a query and return a pandas DataFrame."""
    import pandas as pd
    res = ch_query(sql)
    return pd.DataFrame(res.result_rows, columns=list(res.column_names))


# ── Data freshness ────────────────────────────────────────────────────────────
_fresh = {"ts": 0.0, "date": None}
_fresh_lock = threading.Lock()


def latest_sales_date() -> date:
    """Most recent date that has sales rows (cached 5 minutes)."""
    with _fresh_lock:
        if _fresh["date"] and time.time() - _fresh["ts"] < 300:
            return _fresh["date"]
    res = ch_query("SELECT max(toDate(date)) FROM azure_sales_report WHERE toDate(date) <= today()")
    d = res.result_rows[0][0]
    if isinstance(d, datetime):
        d = d.date()
    with _fresh_lock:
        _fresh.update(ts=time.time(), date=d)
    return d


def parse_date(value: Any, default: Optional[date] = None) -> Optional[date]:
    if value in (None, ""):
        return default
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return datetime.strptime(str(value)[:10], "%Y-%m-%d").date()


def shift_months(d: date, months: int) -> date:
    """d shifted by N months, clamping the day to the target month length."""
    y, m = divmod(d.year * 12 + (d.month - 1) + months, 12)
    m += 1
    for day in (d.day, 30, 29, 28):
        try:
            return date(y, m, day)
        except ValueError:
            continue
    return date(y, m, 28)


# ── Business dimensions ───────────────────────────────────────────────────────
def category_of(product: str) -> str:
    """Dashboard category (CE / MOBILE / LAPTOP / ACC …) of a product name."""
    try:
        from dashboard.dashboard_api_logic import PRODUCT_CAT_MAPPING
    except Exception:  # noqa: BLE001
        return "OTHER"
    return PRODUCT_CAT_MAPPING.get(product, "OTHER")


def products_in_category(category: str) -> List[str]:
    try:
        from dashboard.dashboard_api_logic import PRODUCT_CAT_MAPPING
    except Exception:  # noqa: BLE001
        return []
    cat = str(category).strip().upper()
    return [p for p, c in PRODUCT_CAT_MAPPING.items() if str(c).upper() == cat]


def valid_categories() -> List[str]:
    try:
        from dashboard.dashboard_api_logic import PRODUCT_CAT_MAPPING
    except Exception:  # noqa: BLE001
        return []
    return sorted({str(c) for c in PRODUCT_CAT_MAPPING.values()})


_branches = {"ts": 0.0, "df": None}


def branch_master_df():
    """branch_master as a DataFrame (code, branch_name, rbm, bdm, district) – cached 30 minutes."""
    with _fresh_lock:
        if _branches["df"] is not None and time.time() - _branches["ts"] < 1800:
            return _branches["df"]
    df = ch_df("SELECT code, branch_name, rbm, bdm, district FROM branch_master")
    with _fresh_lock:
        _branches.update(ts=time.time(), df=df)
    return df


def _as_list(v: Any) -> List[str]:
    if v is None or v == "":
        return []
    if isinstance(v, (list, tuple, set)):
        return [str(x).strip() for x in v if str(x).strip()]
    return [s.strip() for s in str(v).split(",") if s.strip()]


def resolve_branch_codes(filters: Dict[str, Any]) -> Optional[List[str]]:
    """
    Turn branch / district / rbm / bdm filters into a list of branch codes.
    Returns None when no branch-level filter was given (= all branches).
    Raises ValueError when a filter matches nothing.
    """
    keys = [k for k in ("branch", "district", "rbm", "bdm") if _as_list(filters.get(k))]
    if not keys:
        return None
    df = branch_master_df()
    mask = None
    for k in keys:
        vals = [v.upper() for v in _as_list(filters.get(k))]
        if k == "branch":
            code_hit = df["code"].str.upper().isin(vals)
            name_hit = df["branch_name"].str.upper().apply(lambda n: any(v in n for v in vals))
            m = code_hit | name_hit
        else:
            m = df[k].str.upper().isin(vals)
        mask = m if mask is None else (mask & m)
    codes = df.loc[mask, "code"].tolist()
    if not codes:
        raise ValueError("No branch matched the filter " + ", ".join(f"{k}={filters.get(k)}" for k in keys))
    return codes


def fmt_inr(v: float) -> str:
    """₹ amount in Crore / Lakh / plain form."""
    a = abs(v)
    sign = "-" if v < 0 else ""
    if a >= 1e7:
        return f"{sign}₹{a / 1e7:,.2f} Cr"
    if a >= 1e5:
        return f"{sign}₹{a / 1e5:,.2f} L"
    return f"{sign}₹{a:,.0f}"


def fmt_val(v: float, unit: str) -> str:
    return fmt_inr(v) if unit == "₹" else f"{v:,.0f} {unit}".strip()


def day_range(start: date, end: date) -> List[date]:
    return [start + timedelta(days=i) for i in range((end - start).days + 1)]
