import threading
from django.apps import AppConfig


class AnalyticsConfig(AppConfig):
    name = 'analytics'

    def ready(self):
        """Auto-prewarm analytics cache in background thread on server startup."""
        import os
        # Only prewarm in the main process (not in manage.py migrate, test, etc.)
        if os.environ.get('RUN_MAIN') != 'true' and os.environ.get('DJANGO_SETTINGS_MODULE'):
            # In production (gunicorn) there's no RUN_MAIN — always prewarm
            if not os.environ.get('SKIP_PREWARM'):
                self._start_prewarm()
        elif os.environ.get('RUN_MAIN') == 'true':
            # Django dev server main process
            self._start_prewarm()

    def _start_prewarm(self):
        import os

        # Avoid all Gunicorn workers hitting ClickHouse & 512MB RAM simultaneously at boot/respawn
        try:
            from analytics.portal_refresh import BASE_DIR, _try_acquire_warm_window

            if not _try_acquire_warm_window(cooldown_sec=300, stamp_file=BASE_DIR / '.startup_prewarm_stamp'):
                return
        except Exception:
            pass

        if os.environ.get('RENDER') or os.environ.get('CI'):
            try:
                import analytics.starrocks_service as _sr

                if str(getattr(_sr, 'SR_HOST', '')).startswith(('192.168.', '10.', '172.')):
                    _sr.SR_HOST = '127.0.0.1'
            except Exception:
                pass

        def _run():
            try:
                from analytics.management.commands.prewarm_cache import _prewarm
                _prewarm()
            except Exception:
                pass
        t = threading.Thread(target=_run, daemon=True, name='analytics-prewarm')
        t.start()
