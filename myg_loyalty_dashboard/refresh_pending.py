import os, django, time
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'myg_loyalty_dashboard.settings')
django.setup()
from django.db import connection

PENDING_VIEWS = [
    'mv_yearly_members',
    'mv_yearly_members_branch',
    'mv_fy_members',
    'mv_fy_members_branch',
    'mv_monthly_members',
    'mv_monthly_members_branch',
    'mv_quarterly_members',
    'mv_quarterly_members_branch',
    'mv_rfm_segments',
    'mv_rfm_summary',
    'mv_customer_summary',
    'mv_customer_yearly_revenue',
]

print(f"Refreshing {len(PENDING_VIEWS)} remaining views...")
print("Note: These are slow views. Each may take 5-20 min. DO NOT interrupt.\n")

ok, fail = 0, 0
t_total = time.time()

for mv in PENDING_VIEWS:
    t0 = time.time()
    print(f"[{ok+fail+1}/{len(PENDING_VIEWS)}] Refreshing {mv}...", flush=True)
    try:
        with connection.cursor() as cur:
            # Use non-concurrent first (faster, no unique index requirement)
            cur.execute(f'REFRESH MATERIALIZED VIEW "{mv}"')
        elapsed = time.time() - t0
        print(f"  -> OK in {elapsed:.1f}s")
        ok += 1
    except Exception as e:
        elapsed = time.time() - t0
        print(f"  -> FAILED in {elapsed:.1f}s: {e}")
        fail += 1

total_elapsed = time.time() - t_total
print(f"\n{'='*50}")
print(f"ALL DONE — {ok} succeeded, {fail} failed")
print(f"Total time: {total_elapsed/60:.1f} minutes")
print("="*50)
