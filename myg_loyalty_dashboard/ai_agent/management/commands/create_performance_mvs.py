from django.core.management.base import BaseCommand
from django.db import connection

class Command(BaseCommand):
    help = 'Creates or replaces highly optimized Materialized Views for AI analytics'

    def handle(self, *args, **kwargs):
        self.stdout.write("Creating advanced Materialized Views for sub-second Analytics...")
        
        with connection.cursor() as cursor:
            # 1. Branch Summary View
            cursor.execute("DROP MATERIALIZED VIEW IF EXISTS mv_branch_summary CASCADE;")
            cursor.execute("""
                CREATE MATERIALIZED VIEW mv_branch_summary AS
                SELECT 
                    UPPER("Branch") as branch_name,
                    COUNT(DISTINCT "Invoice Number") as total_invoices,
                    COUNT(DISTINCT "Customer Mobile") as unique_customers,
                    COALESCE(SUM("Total Value"), 0) as total_revenue,
                    COALESCE(SUM("Total Value") / NULLIF(COUNT(DISTINCT "Invoice Number"), 0), 0) as atv
                FROM v_sales_data
                GROUP BY UPPER("Branch");
            """)
            
            # 2. RFM Summary View
            cursor.execute("DROP MATERIALIZED VIEW IF EXISTS mv_rfm_summary CASCADE;")
            cursor.execute("""
                CREATE MATERIALIZED VIEW mv_rfm_summary AS
                WITH raw_rfm AS (
                    SELECT 
                        "Customer Mobile",
                        MAX("Date") as last_purchase_date,
                        CURRENT_DATE - MAX("Date") as recency_days,
                        COUNT(DISTINCT "Invoice Number") as frequency,
                        COALESCE(SUM("Total Value"), 0) as monetary_value
                    FROM v_sales_data
                    GROUP BY "Customer Mobile"
                )
                SELECT 
                    "Customer Mobile",
                    last_purchase_date,
                    recency_days,
                    frequency,
                    monetary_value,
                    CASE 
                        WHEN recency_days <= 90 AND frequency >= 3 THEN 'Champions'
                        WHEN recency_days <= 180 AND frequency >= 2 THEN 'Loyal'
                        WHEN recency_days <= 365 AND frequency = 1 THEN 'Potential Loyalist'
                        WHEN recency_days > 365 AND recency_days <= 730 AND frequency >= 2 THEN 'At Risk'
                        ELSE 'Lost'
                    END as segment
                FROM raw_rfm;
            """)
            
            # 3. Branch Cohort Resurrection View (2024 -> 2026)
            cursor.execute("DROP MATERIALIZED VIEW IF EXISTS mv_branch_resurrection_2024_2026 CASCADE;")
            cursor.execute("""
                CREATE MATERIALIZED VIEW mv_branch_resurrection_2024_2026 AS
                WITH cohort_2024 AS (
                    SELECT UPPER("Branch") as branch_name, "Customer Mobile"
                    FROM v_sales_data
                    WHERE "Date" >= '2024-01-01' AND "Date" < '2025-01-01'
                    GROUP BY UPPER("Branch"), "Customer Mobile"
                ),
                buyers_2025 AS (
                    SELECT "Customer Mobile"
                    FROM v_sales_data
                    WHERE "Date" >= '2025-01-01' AND "Date" < '2026-01-01'
                    GROUP BY "Customer Mobile"
                ),
                dormant_base AS (
                    SELECT c.branch_name, c."Customer Mobile"
                    FROM cohort_2024 c
                    LEFT JOIN buyers_2025 b ON c."Customer Mobile" = b."Customer Mobile"
                    WHERE b."Customer Mobile" IS NULL
                ),
                cohort_sizes AS (
                    SELECT branch_name, COUNT(DISTINCT "Customer Mobile") as cohort_size
                    FROM dormant_base
                    GROUP BY branch_name
                ),
                reactivated AS (
                    SELECT b.branch_name, b."Customer Mobile"
                    FROM v_sales_data a
                    INNER JOIN dormant_base b ON a."Customer Mobile" = b."Customer Mobile"
                        AND UPPER(a."Branch") = b.branch_name
                    WHERE a."Date" >= '2026-01-01' AND a."Date" < '2027-01-01'
                    GROUP BY b.branch_name, b."Customer Mobile"
                ),
                reactivated_counts AS (
                    SELECT branch_name, COUNT(DISTINCT "Customer Mobile") as reactivated_size
                    FROM reactivated
                    GROUP BY branch_name
                )
                SELECT 
                    c.branch_name, 
                    COALESCE(r.reactivated_size, 0) as resurrected_customers,
                    c.cohort_size,
                    ROUND(COALESCE(r.reactivated_size, 0) * 100.0 / NULLIF(c.cohort_size, 0), 2) as resurrection_rate
                FROM cohort_sizes c
                LEFT JOIN reactivated_counts r ON c.branch_name = r.branch_name;
            """)
            
            # Indexes on Materialized Views for fast querying
            cursor.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_mv_branch ON mv_branch_summary (branch_name);")
            cursor.execute('CREATE UNIQUE INDEX IF NOT EXISTS idx_mv_rfm_mobile ON mv_rfm_summary ("Customer Mobile");')
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_mv_rfm_recency ON mv_rfm_summary (recency_days);")
            cursor.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_mv_resurrection_branch ON mv_branch_resurrection_2024_2026 (branch_name);")
            
        self.stdout.write(self.style.SUCCESS("Successfully created all performance materialized views with indexes!"))
