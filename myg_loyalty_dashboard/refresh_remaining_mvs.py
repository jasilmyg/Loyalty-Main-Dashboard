import os, django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'myg_loyalty_dashboard.settings')
django.setup()
from django.db import connection

mvs = [
  "mv_branch_summary",
  "mv_cohort_cross_year",
  "mv_cohort_customer_years",
  "mv_cohort_retention",
  "mv_cohort_rfm",
  "mv_customer_active_years",
  "mv_customer_propensity",
  "mv_customer_summary",
  "mv_customer_yearly_revenue",
  "mv_dormant_reactivation",
  "mv_dormant_reactivation_customers",
  "mv_fy_members",
  "mv_fy_members_branch",
  "mv_fy_sales",
  "mv_fy_sales_branch",
  "mv_gap_analysis",
  "mv_loyalty_kpis",
  "mv_monthly_members",
  "mv_monthly_members_branch",
  "mv_monthly_retention_2026",
  "mv_monthly_summary",
  "mv_quarterly_members",
  "mv_quarterly_members_branch",
  "mv_redemption_analysis",
  "mv_rfm_segments",
  "mv_rfm_summary",
  "mv_true_repeat_amj_2026",
  "mv_yearly_cohort",
  "mv_yearly_customer_cohorts",
  "mv_yearly_members",
  "mv_yearly_members_branch"
]

print("Refreshing remaining materialized views...")
with connection.cursor() as cursor:
    for mv in mvs:
        print(f"Refreshing {mv}...")
        try:
            cursor.execute(f"REFRESH MATERIALIZED VIEW {mv};")
            print(f"  OK")
        except Exception as e:
            print(f"  FAILED: {e}")
            # reconnect if needed?
            connection.close()

print("All remaining views refreshed.")
