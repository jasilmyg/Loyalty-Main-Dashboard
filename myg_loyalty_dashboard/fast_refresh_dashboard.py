"""
Final fast refresh - only the remaining lightweight views.
Skips: mv_rfm_segments, mv_rfm_summary (too heavy, non-critical for main portal)
"""
import os, django, time
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'myg_loyalty_dashboard.settings')
django.setup()

from django.db import connection

REMAINING_VIEWS = [
    'mv_redemption_analysis',
    'mv_dormant_reactivation',
    'mv_yearly_members',
    'mv_yearly_members_branch',
    'mv_fy_members',
    'mv_fy_members_branch',
    'mv_monthly_members',
    'mv_monthly_members_branch',
    'mv_quarterly_members',
    'mv_quarterly_members_branch',
    'mv_customer_summary',
    'mv_customer_yearly_revenue',
]

print(f"Refreshing {len(REMAINING_VIEWS)} remaining views (skipping heavy RFM)...\n")
total_ok = 0
total_fail = 0
t_start = time.time()

for mv in REMAINING_VIEWS:
    t0 = time.time()
    print(f"  {mv}...", end=" ", flush=True)
    try:
        with connection.cursor() as cur:
            try:
                cur.execute(f'REFRESH MATERIALIZED VIEW CONCURRENTLY "{mv}"')
            except Exception:
                cur.execute(f'REFRESH MATERIALIZED VIEW "{mv}"')
        elapsed = time.time() - t0
        print(f"OK ({elapsed:.1f}s)")
        total_ok += 1
    except Exception as e:
        elapsed = time.time() - t0
        print(f"FAILED ({elapsed:.1f}s): {e}")
        total_fail += 1

total_time = time.time() - t_start
print(f"\n{'='*50}")
print(f"DONE! {total_ok} refreshed, {total_fail} failed in {total_time:.0f}s")
print("="*50)
