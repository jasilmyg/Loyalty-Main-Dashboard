import os, django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'myg_loyalty_dashboard.settings')
django.setup()

from django.core.cache import cache

# Clear ALL cached results so pages reload fresh from refreshed MVs
cache.clear()
print("Django cache cleared.")

# Verify mv_monthly_members has June 2026
from django.db import connection
with connection.cursor() as cur:
    cur.execute("SELECT month_date, total_members, new_members, total_visits FROM mv_monthly_members WHERE month_date >= '2026-01-01' ORDER BY month_date")
    rows = cur.fetchall()
    print("\nmv_monthly_members 2026 data:")
    for r in rows:
        tag = "  <-- JUNE 2026" if "2026-06" in str(r[0]) else ""
        print(f"  {str(r[0])[:7]} | Total: {r[1]:,} | New: {r[2]:,} | Visits: {r[3]:,}{tag}")
