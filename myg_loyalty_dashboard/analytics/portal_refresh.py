"""
Centralized cache invalidation, shared cross-worker epoch management, and
automatic background warm-up for the complete myG Loyalty portal.

Why an epoch file (`BASE_DIR/.portal_cache_epoch`) is needed:
  - Production Render (`render.yaml`) runs Gunicorn with `--workers 2`.
  - Calling `cache.clear()` clears Django's shared FileBasedCache on disk, but
    cannot clear per-worker in-process Python caches (`stock_report_service`,
    `starrocks_stock_service`, `demand_model_store`, `campaign_intelligence`,
    `neural_intelligence_engine`, `ch_assistant._schema_cache`).
  - Prefixing Django cache keys and tagging in-memory cache entries with
    `get_cache_epoch()` guarantees every worker drops stale data immediately on
    the very next request whenever `invalidate_all_caches()` bumps the epoch.
"""
from __future__ import annotations

import glob
import hashlib
import hmac
import json
import logging
import os
import tempfile
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_ANALYTICS_DIR = Path(__file__).resolve().parent
BASE_DIR = _ANALYTICS_DIR.parent
EPOCH_FILE = BASE_DIR / ".portal_cache_epoch"
REFRESH_STAMP_FILE = BASE_DIR / ".portal_refresh_active"

DEFAULT_PORTAL_URL = "https://loyalty-main-dashboard.onrender.com"

_epoch_lock = threading.Lock()
_epoch_mem: Dict[str, Any] = {"mtime_ns": -1, "epoch": "0"}
_warm_lock = threading.Lock()
_warm_running = False


def get_cache_epoch() -> str:
    """Return the shared portal cache epoch token (re-read only on file change)."""
    try:
        st = os.stat(EPOCH_FILE)
        m_ns = getattr(st, "st_mtime_ns", int(st.st_mtime * 1e9))
        if m_ns == _epoch_mem["mtime_ns"]:
            return _epoch_mem["epoch"]
        with _epoch_lock:
            st2 = os.stat(EPOCH_FILE)
            m_ns2 = getattr(st2, "st_mtime_ns", int(st2.st_mtime * 1e9))
            if m_ns2 != _epoch_mem["mtime_ns"]:
                raw = EPOCH_FILE.read_text(encoding="utf-8").strip() or "0"
                _epoch_mem["mtime_ns"] = m_ns2
                _epoch_mem["epoch"] = raw
            return _epoch_mem["epoch"]
    except FileNotFoundError:
        return "0"
    except Exception:
        return _epoch_mem.get("epoch", "0")


def bump_cache_epoch() -> str:
    """Atomically advance the shared portal cache epoch so all workers invalidate."""
    new_epoch = f"{time.time_ns()}-{os.getpid()}"
    with _epoch_lock:
        BASE_DIR.mkdir(parents=True, exist_ok=True)
        fd, tmp_path = tempfile.mkstemp(prefix=".epoch_", dir=str(BASE_DIR))
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(new_epoch)
            os.replace(tmp_path, EPOCH_FILE)
        except Exception:
            try:
                if os.path.exists(tmp_path):
                    os.remove(tmp_path)
            except Exception:
                pass
            raise
        try:
            st = os.stat(EPOCH_FILE)
            _epoch_mem["mtime_ns"] = getattr(st, "st_mtime_ns", int(st.st_mtime * 1e9))
        except Exception:
            _epoch_mem["mtime_ns"] = -1
        _epoch_mem["epoch"] = new_epoch
    return new_epoch


def _stale_json_paths() -> List[Path]:
    return [
        _ANALYTICS_DIR / "ai_forecast_cache.json",
        _ANALYTICS_DIR / "lstm_forecast_cache.json",
        _ANALYTICS_DIR / "neural_intelligence_cache.json",
        _ANALYTICS_DIR / "propensity_cache.json",
        _ANALYTICS_DIR / "model_cache" / "campaign_intelligence.json",
        BASE_DIR / "june_2026_forecast.json",
        BASE_DIR / "temp_cohort_data.json",
    ]


def invalidate_all_caches(clear_ml_pickles: bool = True) -> Dict[str, Any]:
    """
    Synchronously invalidate every cache layer (disk JSON/PKL, Django FileBasedCache,
    cross-worker epoch token, and current-worker module globals). Fast (<50ms).
    """
    epoch = bump_cache_epoch()
    removed: List[str] = []

    for path in _stale_json_paths():
        try:
            if path.exists():
                path.unlink()
                removed.append(path.name)
        except Exception as exc:
            logger.warning("[portal_refresh] Could not remove %s: %s", path.name, exc)

    if clear_ml_pickles:
        for pkl in glob.glob(str(_ANALYTICS_DIR / "model_cache" / "*.pkl")):
            try:
                os.remove(pkl)
                removed.append(os.path.basename(pkl))
            except Exception as exc:
                logger.warning("[portal_refresh] Could not remove %s: %s", pkl, exc)

    django_cleared = False
    try:
        from django.core.cache import cache

        cache.clear()
        django_cleared = True
    except Exception as exc:
        logger.warning("[portal_refresh] Django cache.clear() failed: %s", exc)

    # Clear known in-memory globals in this worker immediately as well
    for mod_path, attrs in (
        ("analytics.campaign_intelligence", ("_cache", "_cache_time")),
        ("analytics.neural_intelligence_engine", ("_cache", "_cache_built_at")),
        ("dashboard.stock_report_service", ("_cached_results", "_snapshot_cache", "_bm_cache")),
        ("dashboard.starrocks_stock_service", ("_sr_cached_results", "_sr_snapshot_cache")),
        ("dashboard.demand_model_store", ("_mem",)),
    ):
        try:
            mod = __import__(mod_path, fromlist=["*"])
            for attr in attrs:
                val = getattr(mod, attr, None)
                if isinstance(val, dict):
                    val.clear()
                elif attr in ("_cache", "_cache_time", "_cache_built_at"):
                    setattr(mod, attr, None)
        except Exception:
            pass

    return {
        "epoch": epoch,
        "django_cache_cleared": django_cleared,
        "removed_files": removed,
    }


def _try_acquire_warm_window(cooldown_sec: int = 120, stamp_file: Path = REFRESH_STAMP_FILE) -> bool:
    """Best-effort cross-worker coalesce guard so burst pings run one warm-up."""
    now = time.time()
    try:
        if stamp_file.exists():
            age = now - stamp_file.stat().st_mtime
            if 0 <= age < cooldown_sec:
                return False
        fd, tmp = tempfile.mkstemp(prefix=".refresh_", dir=str(BASE_DIR))
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(f"{now}\n")
        os.replace(tmp, stamp_file)
        return True
    except Exception:
        return True


def _clear_warm_window() -> None:
    try:
        if REFRESH_STAMP_FILE.exists():
            REFRESH_STAMP_FILE.unlink()
    except Exception:
        pass


def run_warm_tasks(
    warm_stock: bool = True,
    warm_propensity: bool = True,
    warm_neural: bool = True,
    warm_demand_filters: bool = True,
    warm_analytics: bool = True,
    warm_enterprise: bool = True,
) -> Dict[str, Any]:
    """
    Sequentially rebuild lightweight ClickHouse caches and JSON caches so every
    dashboard section immediately serves fresh data with low memory overhead.
    """
    results: Dict[str, Any] = {}
    t_all = time.perf_counter()

    # Ensure ClickHouse errors never stall for 60s per query waiting on private LAN StarRocks
    # when running inside Render/CI where 192.168.x.x is unreachable.
    if os.environ.get("RENDER") or os.environ.get("CI") or os.environ.get("GITHUB_ACTIONS"):
        try:
            import analytics.starrocks_service as _sr

            if str(getattr(_sr, "SR_HOST", "")).startswith(("192.168.", "10.", "172.")):
                _sr.SR_HOST = "127.0.0.1"
        except Exception:
            pass

    if warm_stock:
        t0 = time.perf_counter()
        try:
            from dashboard.stock_report_service import get_stock_ageing_data

            res = get_stock_ageing_data(force_refresh=True)
            results["stock_ageing"] = f"ok ({time.perf_counter() - t0:.1f}s, as_of={res.get('as_of_date')})"
        except Exception as exc:
            results["stock_ageing"] = f"err: {exc}"

    if warm_demand_filters:
        t0 = time.perf_counter()
        try:
            from dashboard.demand_intelligence_service import get_demand_filter_options

            opts = get_demand_filter_options()
            results["demand_filter_options"] = (
                f"ok ({time.perf_counter() - t0:.1f}s, sales={opts.get('ref_date_display')}, "
                f"stock={opts.get('stock_date_display')})"
            )
        except Exception as exc:
            results["demand_filter_options"] = f"err: {exc}"

    if warm_propensity:
        t0 = time.perf_counter()
        try:
            from analytics.customer_propensity_engine import generate_propensity_forecast

            generate_propensity_forecast()
            results["propensity"] = f"ok ({time.perf_counter() - t0:.1f}s)"
        except Exception as exc:
            results["propensity"] = f"err: {exc}"

    if warm_neural:
        t0 = time.perf_counter()
        try:
            from analytics.neural_intelligence_engine import build_neural_intelligence

            build_neural_intelligence(force_rebuild=True)
            results["neural_intelligence"] = f"ok ({time.perf_counter() - t0:.1f}s)"
        except Exception as exc:
            results["neural_intelligence"] = f"err: {exc}"

    if warm_analytics:
        t0 = time.perf_counter()
        try:
            from analytics.management.commands.prewarm_cache import _prewarm

            _prewarm()
            results["analytics_prewarm"] = f"ok ({time.perf_counter() - t0:.1f}s)"
        except Exception as exc:
            results["analytics_prewarm"] = f"err: {exc}"

    if warm_enterprise:
        t0 = time.perf_counter()
        try:
            from types import SimpleNamespace
            from datetime import date, timedelta
            from dashboard.dashboard_api_logic import build_api_response

            today = date.today()
            prev_month = (today.replace(day=1) - timedelta(days=1))
            req = SimpleNamespace(
                GET={
                    "type": "monthly",
                    "base": str(prev_month.month),
                    "comp": str(today.month),
                    "base_year": str(prev_month.year),
                    "comp_year": str(today.year),
                }
            )
            build_api_response(req)
            results["enterprise_dashboard"] = f"ok ({time.perf_counter() - t0:.1f}s)"
        except Exception as exc:
            results["enterprise_dashboard"] = f"err: {exc}"

    results["total_seconds"] = round(time.perf_counter() - t_all, 1)
    logger.info("[portal_refresh] Warm-up completed: %s", json.dumps(results, default=str))
    return results


def start_background_refresh(
    stock_updated: bool = True,
    sales_updated: bool = True,
) -> bool:
    """Launch sequential cache warm-up in a background thread if not already active."""
    global _warm_running
    with _warm_lock:
        if _warm_running or not _try_acquire_warm_window(cooldown_sec=90):
            return False
        _warm_running = True

    def _worker():
        global _warm_running
        try:
            run_warm_tasks(
                warm_stock=bool(stock_updated or sales_updated),
                warm_propensity=bool(sales_updated or stock_updated),
                warm_neural=bool(sales_updated or stock_updated),
                warm_demand_filters=True,
                warm_analytics=bool(sales_updated or stock_updated),
                warm_enterprise=bool(sales_updated),
            )
        except Exception as exc:
            logger.exception("[portal_refresh] Background warm-up failed: %s", exc)
        finally:
            _clear_warm_window()
            with _warm_lock:
                _warm_running = False

    t = threading.Thread(target=_worker, daemon=True, name="portal-refresh-worker")
    t.start()
    return True


def _read_dotenv_fallback(key: str) -> str:
    env_file = BASE_DIR / ".env"
    if not env_file.exists():
        return ""
    try:
        from dotenv import dotenv_values

        return str(dotenv_values(env_file).get(key) or "").strip()
    except Exception:
        try:
            for line in env_file.read_text(encoding="utf-8", errors="ignore").splitlines():
                s = line.strip()
                if not s or s.startswith("#") or "=" not in s:
                    continue
                k, v = s.split("=", 1)
                if k.strip() == key:
                    return v.strip().strip('"').strip("'")
        except Exception:
            pass
    return ""


def _env_or_dotenv(key: str) -> str:
    val = os.environ.get(key, "").strip()
    return val if val else _read_dotenv_fallback(key)


def expected_refresh_token() -> str:
    """
    Return the shared authentication token for the portal refresh webhook.
    Uses `PORTAL_REFRESH_TOKEN` if configured; otherwise derives a domain-separated
    HMAC-SHA256 digest from `CLICKHOUSE_PASSWORD` (already shared between GitHub
    Actions secrets and Render environment variables).
    """
    explicit = _env_or_dotenv("PORTAL_REFRESH_TOKEN")
    if explicit:
        return explicit
    secret = _env_or_dotenv("CLICKHOUSE_PASSWORD") or _env_or_dotenv("CH_PASSWORD")
    if not secret:
        return ""
    return hmac.new(secret.encode("utf-8"), b"myg-portal-refresh-v1", hashlib.sha256).hexdigest()


def is_valid_refresh_token(provided: Optional[str]) -> bool:
    expected = expected_refresh_token()
    if not expected or not provided:
        return False
    return hmac.compare_digest(provided.strip(), expected)


def trigger_remote_portal_refresh(
    base_url: Optional[str] = None,
    stock_updated: bool = False,
    sales_updated: bool = False,
    prewarm: bool = True,
    source: str = "auto-loader",
    timeout: int = 90,
) -> Dict[str, Any]:
    """
    Notify the live web portal (Render) to invalidate all caches and warm up.
    Handles Render free-tier hibernation cold starts with retries and never logs secrets.
    """
    url_base = (base_url or _env_or_dotenv("PORTAL_URL") or DEFAULT_PORTAL_URL).strip().rstrip("/")
    if not url_base:
        return {"ok": False, "reason": "no portal url"}
    token = expected_refresh_token()
    if not token:
        return {"ok": False, "reason": "no refresh token available"}

    endpoint = f"{url_base}/api/v1/internal/portal-refresh/"
    body = json.dumps({
        "source": source,
        "stock_updated": bool(stock_updated),
        "sales_updated": bool(sales_updated),
        "prewarm": bool(prewarm),
    }).encode("utf-8")

    last_err = None
    for attempt in range(1, 4):
        try:
            req = urllib.request.Request(
                endpoint,
                data=body,
                method="POST",
                headers={
                    "Content-Type": "application/json",
                    "X-Portal-Refresh-Token": token,
                    "User-Agent": "myg-portal-auto-refresh/1.0",
                },
            )
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read().decode("utf-8", errors="replace")
                data = json.loads(raw) if raw else {}
                return {"ok": True, "status": resp.status, "response": data}
        except urllib.error.HTTPError as exc:
            last_err = f"HTTP {exc.code}"
            if exc.code in (401, 403, 404):
                break
        except Exception as exc:
            last_err = f"{type(exc).__name__}: {exc}"
        if attempt < 3:
            time.sleep(attempt * 5)

    return {"ok": False, "error": last_err or "unknown error"}


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description="Invalidate/refresh live myG Loyalty Portal caches.")
    ap.add_argument("--remote", action="store_true", help="Trigger remote portal webhook")
    ap.add_argument("--local", action="store_true", help="Invalidate local portal caches")
    ap.add_argument("--stock-updated", action="store_true")
    ap.add_argument("--sales-updated", action="store_true")
    ap.add_argument("--no-prewarm", action="store_true")
    ap.add_argument("--source", default="cli")
    ap.add_argument("--portal-url", default=None)
    args = ap.parse_args()

    if args.local or not args.remote:
        inv = invalidate_all_caches()
        print(f"Local invalidation OK (epoch={inv['epoch']}, removed={len(inv['removed_files'])})")
    if args.remote:
        stock_upd = args.stock_updated or os.environ.get("STOCK_UPDATED", "").lower() == "true"
        sales_upd = args.sales_updated or os.environ.get("SALES_UPDATED", "").lower() == "true"
        res = trigger_remote_portal_refresh(
            base_url=args.portal_url,
            stock_updated=stock_upd,
            sales_updated=sales_upd,
            prewarm=not args.no_prewarm,
            source=args.source,
        )
        if res.get("ok"):
            print(f"Live portal refresh: HTTP {res.get('status')} (epoch={res.get('response', {}).get('epoch', '?')})")
        else:
            print(f"Live portal refresh FAILED: {res}")
            raise SystemExit(1)
