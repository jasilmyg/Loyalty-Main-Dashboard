"""
StarRocks Engine Routing, Cache Isolation & Live Fetch Speed Telemetry Middleware
---------------------------------------------------------------------------------
Configures every portal route (`/...` and `/sr/...`) to run exclusively against
StarRocks (`Loyalty_Analytics`).

Features:
  1. Sets `request.db_engine = 'starrocks'` and `set_active_engine('starrocks')`
     on every request.
  2. Transparently resolves any legacy `/sr/<any-route>` URL to `<any-route>`.
  3. `EngineAwareFileBasedCache` prefixes cache keys with `sr:` and skips cache
     reads during live DB speed tests (`X-Bypass-Cache: 1`, `?nocache=1`).
  4. Attaches `X-DB-Engine`, `X-DB-SQL-Time-Ms`, `X-DB-SQL-Count`, `X-DB-Rows-Fetched`,
     and `X-Server-Total-Ms` headers to every HTTP & API response.
"""

import json
import time
from django.core.cache.backends.filebased import FileBasedCache
from django.http import HttpResponseRedirect

from analytics.clickhouse_service import (
    get_active_engine,
    set_active_engine,
    get_bypass_cache,
    set_bypass_cache,
    reset_query_metrics,
    get_query_metrics,
)


class EngineAwareFileBasedCache(FileBasedCache):
    """
    Drop-in replacement for Django's FileBasedCache:
      - Prefixes StarRocks cache keys with `sr:`.
      - Returns cache miss on `.get()` when `get_bypass_cache()` is True (`nocache=1` / `X-Bypass-Cache: 1`).
    """

    def make_key(self, key, version=None):
        key = f"{'sr' if get_active_engine() == 'starrocks' else 'ch'}:{key}"
        return super().make_key(key, version=version)

    def get(self, key, default=None, version=None):
        if get_bypass_cache():
            return default
        return super().get(key, default=default, version=version)


class DBEngineCloneMiddleware:
    """
    Routes all portal requests to StarRocks `Loyalty_Analytics`
    and measures SQL + Server execution speed.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        raw_path = request.path_info or "/"

        # Normalize bare `/sr` -> `/sr/`
        if raw_path == "/sr":
            qs = request.META.get("QUERY_STRING", "")
            target = "/" + (f"?{qs}" if qs else "")
            return HttpResponseRedirect(target)

        is_sr_url = False
        if raw_path.startswith("/sr/"):
            is_sr_url = True
            stripped = raw_path[3:] or "/"
            request.original_clone_path = raw_path
            request.path_info = stripped
            request.path = stripped
        else:
            request.original_clone_path = raw_path

        engine = get_active_engine()  # 'starrocks', or 'clickhouse' while StarRocks is down

        bypass_cache = (
            request.GET.get("nocache") == "1"
            or request.headers.get("X-Bypass-Cache") == "1"
        )

        # Expose portal state to views & templates
        request.db_engine = engine
        request.is_sr_clone = is_sr_url
        request.sr_prefix = "/sr" if is_sr_url else ""
        request.clean_portal_path = request.path_info
        request.bypass_cache = bypass_cache

        set_active_engine(engine)
        set_bypass_cache(bypass_cache)
        reset_query_metrics()

        t0 = time.perf_counter()
        try:
            response = self.get_response(request)
            if hasattr(response, "render") and callable(response.render) and not getattr(response, "is_rendered", True):
                response.render()
        finally:
            t_server_ms = (time.perf_counter() - t0) * 1000.0
            metrics = get_query_metrics()
            engine = get_active_engine()
            set_bypass_cache(False)

        # Attach high-precision speed telemetry headers
        response["X-DB-Engine"] = engine
        response["X-DB-Label"] = (
            "StarRocks (Loyalty_Analytics)" if engine == "starrocks" else "ClickHouse Cloud (backup)"
        )
        response["X-DB-SQL-Time-Ms"] = f"{metrics['sql_time_ms']:.2f}"
        response["X-DB-SQL-Count"] = str(metrics["query_count"])
        response["X-DB-Rows-Fetched"] = str(metrics["total_rows"])
        response["X-Server-Total-Ms"] = f"{t_server_ms:.2f}"
        response["X-Cache-Bypassed"] = "1" if bypass_cache else "0"

        # Inject SSR telemetry payload into HTML pages containing `/*__SSR_DB_TELEMETRY__*/null`
        ctype = response.get("Content-Type", "")
        if "text/html" in ctype and not getattr(response, "streaming", False):
            marker = b"/*__SSR_DB_TELEMETRY__*/null"
            if marker in response.content:
                ssr_payload = {
                    "engine": engine,
                    "sql_time_ms": round(metrics["sql_time_ms"], 2),
                    "query_count": metrics["query_count"],
                    "total_rows": metrics["total_rows"],
                    "server_total_ms": round(t_server_ms, 2),
                    "cache_bypassed": bypass_cache,
                    "queries": metrics["queries"][:15],
                }
                payload_bytes = json.dumps(ssr_payload).encode("utf-8")
                response.content = response.content.replace(marker, payload_bytes, 1)
                if response.has_header("Content-Length"):
                    response["Content-Length"] = str(len(response.content))

        return response
