"""
Nightly job: save today's 30-day forecast for ALL product lines + the top-N SKUs.

"Forecast vs Actual" scores each day against the last forecast saved BEFORE that day. Snapshots used to be
saved only when someone opened a product/SKU, so a product line like TV nobody looked at for 20 days was scored
with a 20-day-old forecast. Running this after the daily sales load keeps every product line and top SKU 1 day fresh.

Usage:
    python manage.py snapshot_demand_forecasts            # all product lines + top 1000 SKUs by 30-day volume
    python manage.py snapshot_demand_forecasts --top 300
    python manage.py snapshot_demand_forecasts --top 0    # product lines only
"""
import time

from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Save today's 30-day demand forecast snapshot for all product lines and top-N SKUs (keeps Forecast-vs-Actual fresh)."

    def add_arguments(self, parser):
        parser.add_argument('--top', type=int, default=1000, help='Number of SKUs (by 30-day volume). Default 1000. Set 0 for product lines only.')

    def handle(self, *args, **opts):
        from analytics.clickhouse_service import set_active_engine, set_bypass_cache
        from dashboard import demand_intelligence_service as dis

        set_active_engine('clickhouse')
        set_bypass_cache(True)           # always recompute from the freshest data
        t0 = time.time()

        # 1. Always snapshot ALL product lines first (fast: ~50 entities, ensures category charts stay 1-day fresh!)
        pl_res = dis.get_demand_intelligence_data(
            granularity='product_line', limit=400, sort_by='d30', snapshot_top_n=400,
        )
        pl_cnt = len(pl_res.get('products') or [])
        ref_disp = pl_res.get('meta', {}).get('ref_date_display', '?')
        self.stdout.write(self.style.SUCCESS(
            f"Product-line snapshots saved for {pl_cnt} product lines (data through {ref_disp}) in {time.time() - t0:.0f}s"
        ))

        # 2. Snapshot top-N SKUs
        top = max(0, min(int(opts['top']), 1000))
        if top > 0:
            t1 = time.time()
            res = dis.get_demand_intelligence_data(granularity='sku', limit=top, sort_by='d30', snapshot_top_n=top)
            meta = res.get('meta', {})
            self.stdout.write(self.style.SUCCESS(
                f"SKU snapshots saved for up to {top} SKUs (data through {meta.get('ref_date_display', ref_disp)}) "
                f"in {time.time() - t1:.0f}s (total {time.time() - t0:.0f}s)"
            ))
