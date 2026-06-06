import os, django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'myg_loyalty_dashboard.settings')
django.setup()

from django.db import connection
from analytics.services import TABLE

print("Creating mv_dormant_reactivation...")
try:
    with connection.cursor() as cur:
        cur.execute("DROP MATERIALIZED VIEW IF EXISTS mv_dormant_reactivation CASCADE;")
        cur.execute("DROP MATERIALIZED VIEW IF EXISTS mv_dormant_reactivation_customers CASCADE;")
        
        # 1. Customer-level materialized view (Detailed data for Excel download)
        cur.execute("""
            CREATE MATERIALIZED VIEW mv_dormant_reactivation_customers AS
            SELECT 
                "Customer Mobile",
                MAX("Customer Name") AS customer_name,
                MAX("Branch") AS last_branch,
                MAX(parsed_date) AS last_purchase_date,
                MAX(EXTRACT(YEAR FROM parsed_date)) FILTER (WHERE parsed_date < '2026-01-01') AS cohort_year,
                MIN(DATE_TRUNC('month', parsed_date)) FILTER (WHERE parsed_date >= '2026-01-01') AS first_2026_month,
                SUM("Total Value"::numeric) FILTER (WHERE parsed_date >= '2026-01-01') AS reactivated_revenue,
                SUM(NULLIF(REPLACE(REPLACE("POINT REDUMPTION (DEDUCTION)", ',', ''), ' ', ''), '')::numeric) FILTER (WHERE parsed_date >= '2026-01-01') AS reactivated_redeemed_points,
                SUM(CASE WHEN NULLIF(REPLACE(REPLACE("POINT REDUMPTION (DEDUCTION)", ',', ''), ' ', ''), '')::numeric > 0 THEN "Total Value"::numeric ELSE 0 END) FILTER (WHERE parsed_date >= '2026-01-01') AS reactivated_redeemed_sales,
                COUNT(DISTINCT CASE WHEN NULLIF(REPLACE(REPLACE("POINT REDUMPTION (DEDUCTION)", ',', ''), ' ', ''), '')::numeric > 0 THEN "Customer Mobile" END) FILTER (WHERE parsed_date >= '2026-01-01') AS reactivated_redeemed_customers
            FROM sales_data
            WHERE "Customer Mobile" IS NOT NULL
              AND LENGTH("Customer Mobile") = 10
              AND "Customer Mobile" NOT IN ('1313131313','0000000000','9999999999')
            GROUP BY "Customer Mobile";
        """)
        print("Done creating mv_dormant_reactivation_customers.")
        cur.execute("CREATE INDEX idx_mv_dormant_cust_cohort ON mv_dormant_reactivation_customers (cohort_year);")
        cur.execute("CREATE INDEX idx_mv_dormant_cust_month ON mv_dormant_reactivation_customers (first_2026_month);")

        # 2. Aggregated materialized view for dashboard
        cur.execute("""
            CREATE MATERIALIZED VIEW mv_dormant_reactivation AS
            SELECT 
                cohort_year,
                first_2026_month,
                COUNT(*) AS unique_customers,
                SUM(reactivated_revenue) AS total_revenue,
                SUM(reactivated_redeemed_points) AS total_redeemed_points,
                SUM(reactivated_redeemed_sales) AS total_redeemed_sales,
                SUM(reactivated_redeemed_customers) AS total_redeemed_customers
            FROM mv_dormant_reactivation_customers
            WHERE cohort_year BETWEEN 2020 AND 2024
            GROUP BY cohort_year, first_2026_month
            ORDER BY cohort_year ASC, first_2026_month ASC;
        """)
        print("Done creating mv_dormant_reactivation.")
        cur.execute("CREATE INDEX idx_mv_dormant_cohort ON mv_dormant_reactivation (cohort_year);")
        print("Indices created.")
    connection.commit()
except Exception as e:
    import traceback
    traceback.print_exc()

