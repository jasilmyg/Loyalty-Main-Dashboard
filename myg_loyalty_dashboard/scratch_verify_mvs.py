import os
import django
import time
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "myg_loyalty_dashboard.settings")
django.setup()

from django.db import connection

with connection.cursor() as cur:
    cur.execute("SELECT matviewname FROM pg_matviews ORDER BY matviewname")
    print("All mat views:", [r[0] for r in cur.fetchall()])

    t0 = time.time()
    cur.execute("SELECT period_id, total_members, new_members, repeat_members FROM mv_monthly_members LIMIT 5")
    print(f"\nmv_monthly_members sample ({time.time()-t0:.2f}s):")
    for r in cur.fetchall():
        print(r)

    t0 = time.time()
    cur.execute("SELECT fy_year, total_members, new_members FROM mv_fy_members ORDER BY fy_year")
    print(f"\nmv_fy_members ({time.time()-t0:.2f}s):")
    for r in cur.fetchall():
        print(r)
