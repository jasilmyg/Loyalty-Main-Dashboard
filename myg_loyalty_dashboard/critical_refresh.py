"""
critical_refresh.py
====================
Refreshes ONLY the MVs that power the main dashboard pages.
Skips heavy cohort/resurrection MVs (those can be done overnight).
Expected completion: 3-8 minutes.
"""
import os, django, time
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'myg_loyalty_dashboard.settings')
django.setup()

import psycopg2
from django.conf import settings
from django.core.cache import cache

def get_conn():
    db = settings.DATABASES['default']
    conn = psycopg2.connect(
        host=db['HOST'], port=db['PORT'], dbname=db['NAME'],
        user=db['USER'], password=db['PASSWORD'], sslmode='require'
    )
    conn.autocommit = True
    return conn

# Only the MVs used by live dashboard pages
CRITICAL_MVS = [
    # Home dashboard
    "mv_monthly_summary",
    "mv_loyalty_kpis",
    "mv_customer_summary",
    # Customer Analytics
    "mv_rfm_segments",
    "mv_rfm_summary",
    # Monthly Retention page
    "mv_monthly_retention_2026",
    # Members / Branch pages
    "mv_monthly_members",
    "mv_monthly_members_branch",
    "mv_quarterly_members",
    "mv_quarterly_members_branch",
    "mv_yearly_members",
    "mv_yearly_members_branch",
    # FY Sales
    "mv_fy_sales",
    "mv_fy_sales_branch",
    "mv_fy_members",
    "mv_fy_members_branch",
    # Campaign / Dormant
    "mv_dormant_reactivation",
    "mv_dormant_reactivation_customers",
    # Redemption
    "mv_redemption_analysis",
    # Gap / Action / KPIs
    "mv_gap_analysis",
    "mv_action_engine",
    # AMJ repeat
    "mv_true_repeat_amj_2026",
]

total = len(CRITICAL_MVS)
print("=" * 60)
print(f"  CRITICAL MV REFRESH — {total} MVs")
print("=" * 60)

start_all = time.time()
done = 0
failed = []

for i, mv in enumerate(CRITICAL_MVS, 1):
    t0 = time.time()
    print(f"  [{i}/{total}] {mv}...", end=" ", flush=True)
    try:
        conn = get_conn()
        cur = conn.cursor()
        try:
            cur.execute(f'REFRESH MATERIALIZED VIEW CONCURRENTLY "{mv}";')
            mode = "CONCURRENT"
        except Exception:
            conn.close()
            conn = get_conn()
            cur = conn.cursor()
            cur.execute(f'REFRESH MATERIALIZED VIEW "{mv}";')
            mode = "STANDARD"
        conn.close()
        elapsed = time.time() - t0
        done += 1
        print(f"OK ({mode}) in {elapsed:.1f}s", flush=True)
    except Exception as e:
        elapsed = time.time() - t0
        failed.append(mv)
        print(f"FAILED in {elapsed:.1f}s: {e}", flush=True)

cache.clear()
total_elapsed = time.time() - start_all

print("\n" + "=" * 60)
print(f"  DONE: {done}/{total} MVs refreshed in {total_elapsed:.1f}s")
if failed:
    print(f"  FAILED: {failed}")
print("  Cache cleared. Dashboard is now showing fresh July data!")
print("=" * 60)
