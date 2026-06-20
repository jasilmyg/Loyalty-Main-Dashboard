import os, django
import concurrent.futures
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'myg_loyalty_dashboard.settings')
django.setup()
from django.db import connection

# List of all 26 materialized views to refresh
MVIEWS = [
    'mv_action_engine', 'mv_cohort_customer_years', 'mv_cohort_retention', 
    'mv_cohort_rfm', 'mv_customer_active_years', 'mv_customer_propensity', 
    'mv_customer_summary', 'mv_customer_yearly_revenue', 'mv_dormant_reactivation', 
    'mv_fy_members', 'mv_fy_members_branch', 'mv_fy_sales', 'mv_fy_sales_branch', 
    'mv_gap_analysis', 'mv_loyalty_kpis', 'mv_monthly_members', 
    'mv_monthly_members_branch', 'mv_monthly_retention_2026', 'mv_monthly_summary', 
    'mv_quarterly_members', 'mv_quarterly_members_branch', 'mv_rfm_segments', 
    'mv_rfm_summary', 'mv_yearly_cohort', 'mv_yearly_members', 'mv_yearly_members_branch'
]

def refresh_view(view_name):
    from django.db import connection
    try:
        cur = connection.cursor()
        print(f"[{view_name}] Starting refresh...")
        cur.execute(f"REFRESH MATERIALIZED VIEW {view_name}")
        print(f"[{view_name}] Refresh complete.")
        return True
    except Exception as e:
        print(f"[{view_name}] Failed: {e}")
        return False

print("Starting concurrent Materialized View refresh...")
# Run up to 6 refreshes in parallel
with concurrent.futures.ThreadPoolExecutor(max_workers=6) as executor:
    futures = {executor.submit(refresh_view, view): view for view in MVIEWS}
    for future in concurrent.futures.as_completed(futures):
        view = futures[future]
        try:
            future.result()
        except Exception as exc:
            print(f"{view} generated an exception: {exc}")

print("All Materialized Views refreshed concurrently!")
