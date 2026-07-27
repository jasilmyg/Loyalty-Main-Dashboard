"""
ClickHouse Cloud Service Layer
-------------------------------
Provides a fast analytics backend alongside PostgreSQL.
All writes still go to PostgreSQL. ClickHouse is read-only.
Falls back to PostgreSQL automatically if ClickHouse is unavailable.

Thread-safety: each thread gets its own client via threading.local().
"""

import os
import threading
import clickhouse_connect
from typing import Optional

# ─── Credentials (read from environment or .env) ────────────────────────────
CH_HOST     = os.environ.get("CH_HOST",     "ytoyqewr56.ap-south-1.aws.clickhouse.cloud")
CH_PORT     = int(os.environ.get("CH_PORT", "8443"))
CH_USER     = os.environ.get("CH_USER",     "default")
CH_PASSWORD = os.environ.get("CH_PASSWORD", "QyB2XKWS44Qt~")
CH_DATABASE = os.environ.get("CH_DATABASE", "default")

# Thread-local storage — each thread has its own client instance
_local = threading.local()


def get_ch_client() -> Optional[clickhouse_connect.driver.Client]:
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
            connect_timeout=10,
            send_receive_timeout=60,
        )
        _local.client = client
        return client
    except Exception as e:
        print(f"[ClickHouse] Connection failed: {e}")
        _local.client = None
        return None


def ch_query(sql: str, params: dict = None) -> list:
    """
    Run an analytics query on ClickHouse.
    Returns list of tuples (same format as Django cursor.fetchall()).
    Raises exception if ClickHouse is unavailable (caller should fallback to PG).
    """
    client = get_ch_client()
    if client is None:
        raise ConnectionError("ClickHouse not available")
    try:
        result = client.query(sql, parameters=params or {})
        return result.result_rows
    except Exception as e:
        # Reset thread-local client so next call re-connects
        _local.client = None
        raise


def ch_query_with_fallback(ch_sql: str, pg_sql: str, params: dict = None) -> tuple:
    """
    Try ClickHouse first, fall back to PostgreSQL if it fails.
    Returns (rows, source) where source is 'clickhouse' or 'postgresql'.
    """
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
    """Health check for ClickHouse."""
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
