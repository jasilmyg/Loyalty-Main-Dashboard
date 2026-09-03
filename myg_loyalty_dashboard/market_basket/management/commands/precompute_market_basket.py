"""
Management command: precompute_market_basket
Usage:
  python manage.py precompute_market_basket
  python manage.py precompute_market_basket --quick        (180 days, fewer customers)
  python manage.py precompute_market_basket --days 365
  python manage.py precompute_market_basket --setup-only   (create tables, no compute)
"""
import time
from django.core.management.base import BaseCommand
from django.utils import timezone


class Command(BaseCommand):
    help = "Precompute Market Basket & Cross-Sell analytical tables in ClickHouse"

    def add_arguments(self, parser):
        parser.add_argument('--quick',      action='store_true',
                            help='Use last 180 days (faster for testing)')
        parser.add_argument('--days',       type=int, default=730,
                            help='Days of history to analyze (default: 730)')
        parser.add_argument('--setup-only', action='store_true',
                            help='Only create tables, do not run analysis')

    def handle(self, *args, **options):
        start = time.time()
        self.stdout.write(self.style.SUCCESS(
            '\n' + '═' * 60 + '\n  Market Basket & Cross-Sell Precomputation\n' + '═' * 60
        ))

        if options['setup_only']:
            self.stdout.write('Creating ClickHouse tables only…')
            from analytics.clickhouse_service import get_ch_client
            from market_basket.ch_tables import create_all_tables
            ch = get_ch_client()
            if ch is None:
                self.stderr.write(self.style.ERROR('Cannot connect to ClickHouse'))
                return
            results = create_all_tables(ch)
            for name, status in results.items():
                self.stdout.write(f'  {name}: {status}')
            self.stdout.write(self.style.SUCCESS('Done.'))
            return

        try:
            from market_basket.engine import run_full_precompute
            quick = options['quick']
            days  = options['days']

            self.stdout.write(f'Mode: {"Quick (180d)" if quick else f"Full ({days}d)"}')
            self.stdout.write('Starting engines…')

            result = run_full_precompute(days_back=days, quick=quick)

            elapsed = time.time() - start
            if result['status'] == 'success':
                r = result['results']
                self.stdout.write(self.style.SUCCESS(f'''
Results:
  Association Rules      : {r.get("rules", 0):,}
  Cross-Sell Opportunities: {r.get("opportunities", 0):,}
  Branches Analyzed      : {r.get("branches", 0):,}
  Staff Records          : {r.get("staff", 0):,}
  Customer Recommendations: {r.get("recommendations", 0):,}
  Sequential Patterns    : {r.get("sequential", 0):,}

Completed in {elapsed:.1f}s ✓
'''))
            else:
                self.stderr.write(self.style.ERROR(f'Failed: {result.get("message")}'))

        except Exception as e:
            self.stderr.write(self.style.ERROR(f'Error: {e}'))
            raise
