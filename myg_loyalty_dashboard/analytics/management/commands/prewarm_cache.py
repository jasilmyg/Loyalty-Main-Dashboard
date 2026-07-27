"""
python manage.py prewarm_cache

Preloads all global analytics cache keys so the first page load is fast.
Runs each query in a thread so it doesn't block startup.
"""
import threading
import logging
from django.core.management.base import BaseCommand

logger = logging.getLogger(__name__)


def _prewarm():
    """Run all heavy analytics queries with empty filters to populate cache."""
    try:
        from analytics.services import AnalyticsService
        svc = AnalyticsService()
        filters = {}

        tasks = [
            ('sales_overview',        lambda: svc.get_sales_overview(filters)),
            ('customer_analytics',    lambda: svc.get_customer_analytics(filters)),
            ('frequency_distribution',lambda: svc.get_frequency_distribution(filters)),
            ('rfm_segments',          lambda: svc.get_rfm_segments(filters)),
            ('monetary_quintiles',    lambda: svc.get_monetary_quintiles(filters)),
            ('gap_segmentation',      lambda: svc.get_gap_segmentation(filters)),
            ('loyalty_kpis',          lambda: svc.get_loyalty_overview_kpis(filters)),
            ('staff_performance',     lambda: svc.get_staff_performance(filters)),
            ('branch_performance',    lambda: svc.get_branch_performance(filters)),
            ('fy_sales_report',       lambda: svc.get_fy_sales_report(filters)),
            ('fy_loyalty_report',     lambda: svc.get_fy_loyalty_report(filters)),
            ('retail_loyalty_matrix', lambda: svc.get_retail_loyalty_matrix(filters)),
        ]

        for name, fn in tasks:
            try:
                fn()
                logger.info(f'[prewarm] {name} OK')
            except Exception as e:
                logger.warning(f'[prewarm] {name} FAILED: {e}')

        logger.info('[prewarm] Cache warm-up complete.')
    except Exception as e:
        logger.error(f'[prewarm] Aborted: {e}')


class Command(BaseCommand):
    help = 'Prewarms the analytics cache by running all global queries in a background thread.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--sync', action='store_true',
            help='Run synchronously (block until complete). Default: background thread.'
        )

    def handle(self, *args, **options):
        if options['sync']:
            self.stdout.write('Prewarming cache (sync)...')
            _prewarm()
            self.stdout.write(self.style.SUCCESS('Cache warm-up complete.'))
        else:
            self.stdout.write('Prewarming cache in background thread...')
            t = threading.Thread(target=_prewarm, daemon=True)
            t.start()
            self.stdout.write(self.style.SUCCESS('Background prewarm started.'))
