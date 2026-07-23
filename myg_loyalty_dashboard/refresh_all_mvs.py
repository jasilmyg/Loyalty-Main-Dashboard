import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'myg_loyalty_dashboard.settings')
django.setup()

from django.db import connection

print("Fetching all materialized views...")
with connection.cursor() as cur:
    cur.execute("SELECT matviewname FROM pg_matviews;")
    mvs = [row[0] for row in cur.fetchall()]

print(f"Found {len(mvs)} Materialized Views. Starting refresh...")
for mv in mvs:
    try:
        with connection.cursor() as cur:
            try:
                cur.execute(f'REFRESH MATERIALIZED VIEW CONCURRENTLY "{mv}";')
                print(f"Refreshed (concurrent): {mv}")
            except Exception:
                cur.execute(f'REFRESH MATERIALIZED VIEW "{mv}";')
                print(f"Refreshed (non-concurrent): {mv}")
    except Exception as e:
        print(f"FAILED {mv}: {e}")

from django.core.cache import cache
cache.clear()
print("\nDjango cache cleared. Refresh complete!")
