import os, django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'myg_loyalty_dashboard.settings')
django.setup()
from django.db import connection

mvs_to_refresh = [
    'mv_dormant_reactivation',
    'mv_dormant_reactivation_customers',
    'mv_monthly_members',
    'mv_monthly_members_branch',
    'mv_monthly_retention_2026',
    'mv_monthly_summary',
    'mv_quarterly_members',
    'mv_quarterly_members_branch',
    'mv_branch_summary',
    'mv_loyalty_kpis',
    'mv_customer_summary',
    'mv_fy_sales',
    'mv_fy_sales_branch',
    'mv_fy_members',
    'mv_fy_members_branch',
    'mv_gap_analysis',
    'mv_rfm_segments',
    'mv_rfm_summary',
    'mv_true_repeat_amj_2026',
]

print("Refreshing key materialized views...")
for mv in mvs_to_refresh:
    try:
        with connection.cursor() as cur:
            try:
                cur.execute(f'REFRESH MATERIALIZED VIEW CONCURRENTLY "{mv}"')
            except Exception:
                cur.execute(f'REFRESH MATERIALIZED VIEW "{mv}"')
        print(f"  OK: {mv}")
    except Exception as e:
        print(f"  FAILED {mv}: {e}")

from django.core.cache import cache
cache.clear()
print("Django cache cleared.")
print("All done!")
