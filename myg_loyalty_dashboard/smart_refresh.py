"""
Refresh only MVs that were last updated BEFORE the June 2026 data upload.
Uses pg_stat_user_tables to check last_analyze/vacuum time.
"""
import os, django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'myg_loyalty_dashboard.settings')
django.setup()
from django.db import connection
from datetime import datetime, timezone

# The June data upload happened around 2026-06-20 06:00 UTC
# MVs refreshed AFTER this time are already up-to-date
UPLOAD_TIME_UTC = datetime(2026, 6, 20, 6, 0, 0, tzinfo=timezone.utc)

print("Checking MV freshness...")
with connection.cursor() as cur:
    cur.execute("""
        SELECT 
            relname,
            last_analyze,
            last_autoanalyze
        FROM pg_stat_user_tables
        WHERE relname LIKE 'mv_%'
        ORDER BY relname;
    """)
    rows = cur.fetchall()

stale_mvs = []
fresh_mvs = []
for relname, last_analyze, last_auto in rows:
    last_refresh = last_analyze or last_auto
    if last_refresh is None:
        stale_mvs.append(relname)
    elif last_refresh < UPLOAD_TIME_UTC:
        stale_mvs.append(relname)
    else:
        fresh_mvs.append(relname)

print(f"\nFresh MVs ({len(fresh_mvs)}) - SKIPPING:")
for mv in fresh_mvs:
    print(f"  OK: {mv}")

print(f"\nStale MVs ({len(stale_mvs)}) - REFRESHING:")
for mv in stale_mvs:
    print(f"  Pending: {mv}")

print("\nStarting targeted refresh...")
for mv in stale_mvs:
    try:
        with connection.cursor() as cur:
            try:
                cur.execute(f'REFRESH MATERIALIZED VIEW CONCURRENTLY "{mv}"')
                print(f"  Refreshed (concurrent): {mv}")
            except Exception:
                cur.execute(f'REFRESH MATERIALIZED VIEW "{mv}"')
                print(f"  Refreshed (non-concurrent): {mv}")
    except Exception as e:
        print(f"  FAILED {mv}: {e}")

from django.core.cache import cache
cache.clear()
print("\nDjango cache cleared.")
print(f"Done! Refreshed {len(stale_mvs)} stale MVs, skipped {len(fresh_mvs)} fresh MVs.")
