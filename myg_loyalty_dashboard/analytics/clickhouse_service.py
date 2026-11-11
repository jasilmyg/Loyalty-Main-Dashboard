"""
StarRocks Analytics Service Layer (`Loyalty_Analytics` Only)
------------------------------------------------------------
Provides the unified analytics backend (`get_ch_client`, `ch_query`,
`ch_query_with_fallback`, `CH_EXCLUDE_FILTER`, `ch_exclude_filter`, etc.)
backed exclusively by StarRocks (`Loyalty_Analytics`).

All ClickHouse Cloud connections have been completely removed; every query
written in ClickHouse SQL dialect is transparently translated and executed
against StarRocks via `analytics.starrocks_service`.
"""

import os
import time
from contextvars import ContextVar
from typing import Optional

# ── Active Engine Context & Query Speed Telemetry (StarRocks Only) ──────────
_active_engine_var: ContextVar[str] = ContextVar("active_db_engine", default="starrocks")
_bypass_cache_var: ContextVar[bool] = ContextVar("bypass_db_cache", default=False)
_query_metrics_var: ContextVar[Optional[list]] = ContextVar("db_query_metrics", default=None)


def set_active_engine(engine: str = "starrocks") -> None:
    """Active analytics engine is permanently set to 'starrocks'."""
    _active_engine_var.set("starrocks")


def get_active_engine() -> str:
    """Return active analytics engine: 'starrocks', or 'clickhouse' while StarRocks is unreachable."""
    try:
        from analytics.starrocks_service import sr_is_up
        return "starrocks" if sr_is_up() else "clickhouse"
    except Exception:
        return "starrocks"


def set_bypass_cache(flag: bool) -> None:
    """Enable/disable cache bypass for live raw DB speed benchmarking."""
    _bypass_cache_var.set(bool(flag))


def get_bypass_cache() -> bool:
    return _bypass_cache_var.get()


def reset_query_metrics() -> None:
    """Reset per-request SQL execution timings."""
    _query_metrics_var.set([])


def record_query_metric(engine: str, sql: str, elapsed_ms: float, row_count: int = 0, error: str = "") -> None:
    """Record a single SQL query execution timing in the active request context."""
    lst = _query_metrics_var.get()
    if lst is None:
        lst = []
        _query_metrics_var.set(lst)
    compact_sql = " ".join(str(sql).split())[:180]
    lst.append({
        "engine": engine or "starrocks",
        "elapsed_ms": round(float(elapsed_ms), 2),
        "rows": int(row_count or 0),
        "sql_preview": compact_sql,
        "error": error,
    })


def get_query_metrics() -> dict:
    """Return aggregated SQL execution timings for the current request."""
    lst = _query_metrics_var.get() or []
    total_ms = sum(item["elapsed_ms"] for item in lst)
    total_rows = sum(item["rows"] for item in lst)
    return {
        "engine": "starrocks",
        "sql_time_ms": round(total_ms, 2),
        "query_count": len(lst),
        "total_rows": total_rows,
        "queries": lst,
    }


# ── Dashboard exclusion filters ──────────────────────────────────────────────
# These rows exist in the database but must NOT appear in any dashboard report:
#   • Invoices where invoice_no contains 'SMC' or 'EI'
#     (belong to HEAD OFFICE / UG SMART CHOICE internal transfers)
#   • Any row where branch = '3GH'
#
# Usage in any SQL (main table, no alias):
#   WHERE {CH_EXCLUDE_FILTER} AND ...
#
# Usage when the table has an alias (e.g. FROM azure_invoice_report ai):
#   WHERE {ch_exclude_filter('ai')} AND ...
CH_EXCLUDE_FILTER = """
    NOT (
        upper(invoice_no) LIKE '%SMC%'
        OR upper(invoice_no) LIKE '%EI%'
        OR upper(branch) IN ('HEAD OFFICE', 'UG SMART CHOICE', '3GH')
    )
""".strip()


def ch_exclude_filter(alias: str = '') -> str:
    """Return the exclusion WHERE fragment, with optional table alias prefix.
    e.g. ch_exclude_filter('s') → 'NOT (upper(s.invoice_no) LIKE ...'
    """
    p = f'{alias}.' if alias else ''
    return (
        f"NOT ("
        f" upper({p}invoice_no) LIKE '%SMC%'"
        f" OR upper({p}invoice_no) LIKE '%EI%'"
        f" OR upper({p}branch) IN ('HEAD OFFICE', 'UG SMART CHOICE', '3GH')"
        f")"
    )
# ─────────────────────────────────────────────────────────────────────────────

# ─── StarRocks Connection Details (aliased for backward compatibility) ───────
CH_HOST     = os.environ.get("STARROCKS_HOST",     "192.168.0.252")
CH_PORT     = int(os.environ.get("STARROCKS_PORT", "9030"))
CH_USER     = os.environ.get("STARROCKS_USER",     "test1")
CH_PASSWORD = os.environ.get("STARROCKS_PASSWORD", "")
CH_DATABASE = os.environ.get("STARROCKS_DATABASE", "Loyalty_Analytics")


def get_ch_client():
    """
    Returns the timed StarRocks `Loyalty_Analytics` client adapter (`StarRocksClientAdapter`),
    which translates ClickHouse SQL queries and executes them on StarRocks.
    """
    from analytics.starrocks_service import get_sr_client
    return get_sr_client()


def ch_query(sql: str, params: dict = None) -> list:
    """
    Run an analytics query on StarRocks (`Loyalty_Analytics`).
    Returns list of tuples (same format as Django cursor.fetchall()).
    """
    client = get_ch_client()
    if client is None:
        raise ConnectionError("starrocks not available")
    result = client.query(sql, parameters=params or {})
    return result.result_rows


def ch_query_with_fallback(ch_sql: str, pg_sql: str, params: dict = None) -> tuple:
    """
    Execute query on StarRocks (`Loyalty_Analytics`).
    Returns (rows, 'starrocks').
    """
    rows = ch_query(ch_sql, params)
    return rows, "starrocks"


def is_ch_available() -> bool:
    """Health check for StarRocks (`Loyalty_Analytics`)."""
    try:
        client = get_ch_client()
        if client is None:
            return False
        client.query("SELECT 1")
        return True
    except Exception:
        return False


def reset_client():
    """Force reconnect on next query for the current thread."""
    from analytics.starrocks_service import reset_sr_connection
    reset_sr_connection()
