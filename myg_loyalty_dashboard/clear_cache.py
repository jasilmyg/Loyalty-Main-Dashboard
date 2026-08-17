"""Clear all Django cache so every portal section reloads fresh data from ClickHouse."""
import os; os.environ.setdefault('DJANGO_SETTINGS_MODULE','myg_loyalty_dashboard.settings')
import django; django.setup()

from django.core.cache import cache

# Clear entire cache
cache.clear()
print("Django cache cleared successfully!")
print("All portal sections will now reload fresh data from ClickHouse on next page visit.")
