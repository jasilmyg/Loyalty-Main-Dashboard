"""
Management command: seed_forecast_cache

Seeds the ForecastCache database table from the local lstm_forecast_cache.json
file. Run this once locally, then the data lives in PostgreSQL and is available
on Render without any file deployment.

Usage:
    python manage.py seed_forecast_cache
    python manage.py seed_forecast_cache --force   # overwrite even if exists
"""
import json
import os
from django.core.management.base import BaseCommand
from django.conf import settings
from analytics.models import ForecastCache


class Command(BaseCommand):
    help = 'Seeds the ForecastCache table from analytics/lstm_forecast_cache.json'

    def add_arguments(self, parser):
        parser.add_argument(
            '--force',
            action='store_true',
            help='Overwrite existing cache entry even if one already exists.',
        )

    def handle(self, *args, **options):
        cache_path = os.path.join(settings.BASE_DIR, 'analytics', 'lstm_forecast_cache.json')

        if not os.path.exists(cache_path):
            self.stderr.write(self.style.ERROR(
                f'Cache file not found: {cache_path}\n'
                'Run the LSTM forecaster first to generate it.'
            ))
            return

        existing = ForecastCache.objects.filter(cache_key='lstm_amj_2026').first()
        if existing and not options['force']:
            self.stdout.write(self.style.WARNING(
                'ForecastCache already exists. Use --force to overwrite.'
            ))
            self.stdout.write(f'  Last updated: {existing.updated_at}')
            return

        with open(cache_path, 'r') as f:
            data = json.load(f)

        ForecastCache.set_lstm_cache(data)
        self.stdout.write(self.style.SUCCESS(
            'ForecastCache seeded successfully into PostgreSQL.'
        ))
        kpis = data.get('KPIs', {})
        self.stdout.write(f"  Forecast_Final : {kpis.get('Forecast_Final', 'N/A')}")
        self.stdout.write(f"  Prob_Target    : {kpis.get('Prob_Target', 'N/A')}")
        self.stdout.write(f"  Actual dates   : {len(data.get('Charts', {}).get('BurnUp', {}).get('Actual_Dates', []))} days")
        self.stdout.write(f"  Forecast dates : {len(data.get('Charts', {}).get('BurnUp', {}).get('Forecast_Dates', []))} days")
