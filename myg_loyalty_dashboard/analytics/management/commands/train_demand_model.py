"""
Train the demand-forecast model in the backend on the whole sales history and store it.

    python manage.py train_demand_model                 # 90 origins x 15d (~3.7 years), saves to ClickHouse
    python manage.py train_demand_model --origins 36    # ~18 months only
    python manage.py train_demand_model --no-save       # dry run: train + report only
    python manage.py train_demand_model --last-ref 2026-10-16 --no-save   # evaluation run with a data cutoff

Re-run after each month of new data (or nightly). The dashboard picks the new model up within ~10 minutes.
"""
import json
import os
from datetime import date

from django.conf import settings
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Train the SKU demand model on the whole sales history (all SKUs, many 15-day origins) and store it."

    def add_arguments(self, parser):
        parser.add_argument('--origins', type=int, default=90, help='Number of 15-day training origins (default 90 = ~3.7 years of history).')
        parser.add_argument('--holdout', type=int, default=2, help='Most recent origins held out to choose config / blend weight.')
        parser.add_argument('--workers', type=int, default=4, help='Parallel ClickHouse fetches.')
        parser.add_argument('--last-ref', type=str, default='', help='Last data day to use (YYYY-MM-DD). Default: latest sales date.')
        parser.add_argument('--configs', type=str, default='', help='Comma list of configs to try (small_75,mid_250,deep_400).')
        parser.add_argument('--no-save', action='store_true', help='Train and report but do not store the model.')

    def handle(self, *args, **opts):
        from analytics.clickhouse_service import get_ch_client, set_bypass_cache
        from dashboard import demand_intelligence_service as dis
        from dashboard.demand_model_store import save_model
        from dashboard.demand_model_trainer import train_demand_model

        set_bypass_cache(True)
        ch = get_ch_client()
        if ch is None:
            self.stderr.write(self.style.ERROR('Analytics database is unavailable.'))
            return
        if opts['last_ref']:
            last_ref = date.fromisoformat(opts['last_ref'])
        else:
            last_ref = dis._get_reference_dates(ch)[0]
        cache_dir = os.path.join(str(getattr(settings, 'BASE_DIR', '.')), 'analytics', 'model_cache', 'train_frames')
        configs = [c.strip() for c in opts['configs'].split(',') if c.strip()] or None

        res = train_demand_model(
            ch, last_ref=last_ref, n_origins=opts['origins'], holdout_origins=opts['holdout'],
            cache_dir=cache_dir, workers=opts['workers'], log=lambda m: self.stdout.write(m), configs=configs,
        )
        self.stdout.write(json.dumps(res['report'], indent=2, default=str))
        if opts['no_save']:
            self.stdout.write(self.style.WARNING('--no-save: model NOT stored.'))
            return
        info = save_model(res['model'])
        self.stdout.write(self.style.SUCCESS(f"Model stored: {info}"))
