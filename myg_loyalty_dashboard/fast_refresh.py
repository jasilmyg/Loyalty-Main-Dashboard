import os, django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'myg_loyalty_dashboard.settings')
django.setup()
from django.db import connection

cur = connection.cursor()
connection.autocommit = True

print("Refreshing mv_customer_summary...")
cur.execute('REFRESH MATERIALIZED VIEW "mv_customer_summary";')
print("Refreshing mv_loyalty_kpis...")
cur.execute('REFRESH MATERIALIZED VIEW "mv_loyalty_kpis";')

print("Clearing django cache...")
from django.core.cache import cache
cache.clear()

print("Done! Dashboard updated.")
