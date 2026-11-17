"""
Nightly job: save today's 30-day forecast for the top-N SKUs.

"Forecast vs Actual" scores each day against the last forecast saved BEFORE that day. Snapshots used to be
saved only when someone opened a SKU, so a SKU nobody looked at for 11 days was scored with an 11-day-old
forecast. Running this after the daily sales load keeps every top SKU's forecast 1 day fresh.

Usage:
    python manage.py snapshot_demand_forecasts            # top 1000 SKUs by 30-day volume
    python manage.py snapshot_demand_forecasts --top 300
"""
import time

from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Save today's 30-day demand forecast snapshot for the top-N SKUs (keeps Forecast-vs-Actual fresh)."

    def add_arguments(self, parser):
        parser.add_argument('--top', type=int, default=1000, help='Number of SKUs (by 30-day volume). Default 1000.')

    def handle(self, *args, **opts):
        from analytics.clickhouse_service import set_bypass_cache
        from dashboard import demand_intelligence_service as dis

        set_bypass_cache(True)           # always recompute from the freshest data
        t0 = time.time()
        top = max(10, min(int(opts['top']), 1000))
        res = dis.get_demand_intelligence_data(granularity='sku', limit=top, sort_by='d30', snapshot_top_n=top)
        meta = res.get('meta', {})
        self.stdout.write(self.style.SUCCESS(
            f"Forecast snapshots saved for up to {top} SKUs (data through {meta.get('ref_date_display', '?')}) "
            f"in {time.time() - t0:.0f}s"
        ))
