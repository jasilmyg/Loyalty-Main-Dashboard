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
        def _run():
            try:
                from analytics.management.commands.prewarm_cache import _prewarm
                _prewarm()
            except Exception:
                pass
        t = threading.Thread(target=_run, daemon=True, name='analytics-prewarm')
        t.start()
