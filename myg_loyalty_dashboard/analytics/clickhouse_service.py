"""
ClickHouse Cloud Service Layer
-------------------------------
Provides a fast analytics backend alongside PostgreSQL.
All writes still go to PostgreSQL. ClickHouse is read-only.
Falls back to PostgreSQL automatically if ClickHouse is unavailable.

Thread-safety: each thread gets its own client via threading.local().
"""

import os
import time
import threading
from contextvars import ContextVar
import clickhouse_connect
from typing import Optional

# ── Active Engine Context & Query Speed Telemetry (ClickHouse vs StarRocks) ──
_active_engine_var: ContextVar[str] = ContextVar("active_db_engine", default="clickhouse")
_bypass_cache_var: ContextVar[bool] = ContextVar("bypass_db_cache", default=False)
_query_metrics_var: ContextVar[Optional[list]] = ContextVar("db_query_metrics", default=None)


def set_active_engine(engine: str) -> None:
    """Switch active analytics engine for the current request/context ('clickhouse' or 'starrocks')."""
    _active_engine_var.set("starrocks" if str(engine).lower() == "starrocks" else "clickhouse")


def get_active_engine() -> str:
    """Return active analytics engine for the current request/context ('clickhouse' default)."""
    return _active_engine_var.get()


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
        "engine": engine,
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
        "engine": get_active_engine(),
        "sql_time_ms": round(total_ms, 2),
        "query_count": len(lst),
        "total_rows": total_rows,
        "queries": lst,
    }


class _TimedClickHouseClientProxy:
    """Transparent proxy around clickhouse_connect Client that records SQL execution time (ms)."""

    def __init__(self, raw_client):
        self._raw = raw_client

    def query(self, query, parameters=None, **kwargs):
        t0 = time.perf_counter()
        try:
            res = self._raw.query(query, parameters=parameters, **kwargs)
        except Exception as e:
            elapsed_ms = (time.perf_counter() - t0) * 1000.0
            record_query_metric("clickhouse", query, elapsed_ms, 0, error=str(e))
            raise
        elapsed_ms = (time.perf_counter() - t0) * 1000.0
        rows = getattr(res, "result_rows", None)
        record_query_metric("clickhouse", query, elapsed_ms, len(rows) if rows is not None else 0)
        return res

    def query_df(self, query, parameters=None, **kwargs):
        t0 = time.perf_counter()
        try:
            df = self._raw.query_df(query, parameters=parameters, **kwargs)
        except Exception as e:
            elapsed_ms = (time.perf_counter() - t0) * 1000.0
            record_query_metric("clickhouse", query, elapsed_ms, 0, error=str(e))
            raise
        elapsed_ms = (time.perf_counter() - t0) * 1000.0
        record_query_metric("clickhouse", query, elapsed_ms, len(df) if df is not None else 0)
        return df

    def command(self, cmd, parameters=None, **kwargs):
        t0 = time.perf_counter()
        res = self._raw.command(cmd, parameters=parameters, **kwargs)
        elapsed_ms = (time.perf_counter() - t0) * 1000.0
        record_query_metric("clickhouse", cmd, elapsed_ms, 1)
        return res

    def __getattr__(self, name):
        return getattr(self._raw, name)

# ─────────────────────────────────────────────────────────────────────────────

# ── Dashboard exclusion filters ──────────────────────────────────────────────
# These rows exist in ClickHouse but must NOT appear in any dashboard report:
#   • Invoices where invoice_no contains 'SMC' or 'EI'
#     (belong to HEAD OFFICE / UG SMART CHOICE internal transfers)
#   • Any row where branch = '3GH'
#
# Usage in any ClickHouse SQL (main table, no alias):
#   WHERE {CH_EXCLUDE_FILTER} AND ...
#
# Usage when the table has an alias (e.g. FROM azure_invoice_report ai):
#   WHERE {CH_EXCLUDE_FILTER_ALIAS.format(alias='ai')} AND ...
#
# ClickHouse tables are NEVER modified — filter is applied at query time only.
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

# ─── Credentials (from environment — set in Render Dashboard) ───────────────
CH_HOST     = os.environ.get("CH_HOST",     "pdhsuv47ec.ap-south-1.aws.clickhouse.cloud")
CH_PORT     = int(os.environ.get("CH_PORT", "8443"))
CH_USER     = os.environ.get("CH_USER",     "default")
CH_PASSWORD = os.environ.get("CH_PASSWORD", "ZFlujj9SA_Iei")
CH_DATABASE = os.environ.get("CH_DATABASE", "default")

# Thread-local storage — each thread has its own client instance
_local = threading.local()


def _get_raw_ch_client() -> Optional[clickhouse_connect.driver.Client]:
    """Returns a per-thread ClickHouse client. Returns None if unavailable."""
    client = getattr(_local, 'client', None)
    if client is not None:
        return client
    try:
        client = clickhouse_connect.get_client(
            host=CH_HOST,
            port=CH_PORT,
            username=CH_USER,
            password=CH_PASSWORD,
            database=CH_DATABASE,
            secure=True,
            connect_timeout=30,          # increased from 10 → 30s
            send_receive_timeout=120,    # increased from 60 → 120s (matches gunicorn)
        )
        _local.client = client
        return client
    except Exception as e:
        if "Authentication failed" in str(e):
            print(f"[ClickHouse] Auth failed with env password. Retrying with default.")
            try:
                client = clickhouse_connect.get_client(
                    host=CH_HOST,
                    port=CH_PORT,
                    username=CH_USER,
                    password="ZFlujj9SA_Iei",
                    database=CH_DATABASE,
                    secure=True,
                    connect_timeout=30,
                    send_receive_timeout=120,
                )
                _local.client = client
                return client
            except Exception as e2:
                print(f"[ClickHouse] Connection failed even with default password: {e2}")
                _local.client = None
                return None
        print(f"[ClickHouse] Connection failed: {e}")
        _local.client = None
        return None


def get_ch_client():
    """
    Returns the active analytics DB client for the current request context:
      - 'clickhouse' (default): timed ClickHouse Cloud client
      - 'starrocks' (Clone Test): timed StarRocks `Loyalty_Analytics` client adapter
    """
    if get_active_engine() == "starrocks":
        from analytics.starrocks_service import get_sr_client
        return get_sr_client()
    raw = _get_raw_ch_client()
    if raw is None:
        return None
    return _TimedClickHouseClientProxy(raw)


def ch_query(sql: str, params: dict = None) -> list:
    """
    Run an analytics query on the active analytics engine (ClickHouse or StarRocks).
    Returns list of tuples (same format as Django cursor.fetchall()).
    Raises exception if ClickHouse is unavailable (caller should fallback to PG).
    """
    engine = get_active_engine()
    client = get_ch_client()
    if client is None:
        raise ConnectionError(f"{engine} not available")
    try:
        result = client.query(sql, parameters=params or {})
        return result.result_rows
    except Exception as e:
        if engine == "clickhouse":
            # Reset thread-local client so next call re-connects
            _local.client = None
        raise


def ch_query_with_fallback(ch_sql: str, pg_sql: str, params: dict = None) -> tuple:
    """
    Try active analytics engine first, fall back to PostgreSQL only in ClickHouse mode if it fails.
    Returns (rows, source) where source is 'clickhouse', 'starrocks', or 'postgresql'.
    """
    engine = get_active_engine()
    if engine == "starrocks":
        rows = ch_query(ch_sql, params)
        return rows, "starrocks"
    try:
        rows = ch_query(ch_sql, params)
        return rows, 'clickhouse'
    except Exception as e:
        print(f"[ClickHouse] Query failed, falling back to PG: {e}")
        from django.db import connection
        with connection.cursor() as cur:
            cur.execute(pg_sql)
            rows = cur.fetchall()
        return rows, 'postgresql'


def is_ch_available() -> bool:
    """Health check for the active analytics engine (ClickHouse or StarRocks)."""
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
    _local.client = None
    if get_active_engine() == "starrocks":
        from analytics.starrocks_service import reset_sr_connection
        reset_sr_connection()
