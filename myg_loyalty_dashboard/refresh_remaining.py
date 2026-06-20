import os, django, time
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'myg_loyalty_dashboard.settings')
django.setup()
from django.db import connection

views_remaining = [
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

ok, fail = 0, 0
for mv in views_remaining:
    t0 = time.time()
    print(f'  {mv}...', flush=True)
    try:
        with connection.cursor() as cur:
            try:
                cur.execute(f'REFRESH MATERIALIZED VIEW CONCURRENTLY "{mv}"')
            except Exception:
                cur.execute(f'REFRESH MATERIALIZED VIEW "{mv}"')
        elapsed = time.time() - t0
        print(f'    OK ({elapsed:.1f}s)')
        ok += 1
    except Exception as e:
        print(f'    FAILED: {e}')
        fail += 1

print(f'\n{"="*40}')
print(f'ALL DONE — {ok} OK, {fail} failed')
print('="*40')
