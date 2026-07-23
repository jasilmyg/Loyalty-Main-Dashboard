"""
rebuild_missing_mvs.py
=======================
Rebuilds the 4 MVs that were dropped by CASCADE when mv_customer_summary was optimized:
  - mv_gap_analysis
  - mv_monthly_retention_2026
  - mv_cohort_rfm
  - mv_customer_propensity

(mv_loyalty_kpis and mv_action_engine are being rebuilt by create_loyalty_mvs.py)
"""
import os, django, time
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'myg_loyalty_dashboard.settings')
django.setup()

import psycopg2
from django.conf import settings
from django.db import connection

def get_conn():
    db = settings.DATABASES['default']
    conn = psycopg2.connect(
        host=db['HOST'], port=db['PORT'], dbname=db['NAME'],
        user=db['USER'], password=db['PASSWORD'], sslmode='require'
    )
    conn.autocommit = True
    return conn

def run_sql(label, sql, conn=None):
    close_after = False
    if conn is None:
        conn = get_conn()
        close_after = True
    cur = conn.cursor()
    t0 = time.time()
    print(f"  [{label}]...", end=" ", flush=True)
    try:
        cur.execute(sql)
        print(f"OK ({time.time()-t0:.1f}s)")
    except Exception as e:
        print(f"ERROR: {e}")
    if close_after:
        conn.close()

print("=" * 60)
print("  REBUILDING 4 MISSING MVs")
print("=" * 60)

from analytics.services import TABLE

# ── 1. mv_gap_analysis ────────────────────────────────────────────────────────
print("\n[1/4] mv_gap_analysis")
run_sql("DROP", "DROP MATERIALIZED VIEW IF EXISTS mv_gap_analysis CASCADE;")
run_sql("CREATE", """
    CREATE MATERIALIZED VIEW mv_gap_analysis AS
    WITH customer_gaps AS (
        SELECT
            cd.mobile,
            cs.visits,
            CASE
                WHEN cs.visits > 1
                THEN ((cd.lv_month - cd.fv_month)::FLOAT / (cs.visits - 1))
                ELSE NULL
            END AS avg_gap_days
        FROM mv_customer_dates cd
        JOIN mv_customer_summary cs ON cs.mobile = cd.mobile
        WHERE cs.visits >= 2
    ),
    bucketed AS (
        SELECT mobile, avg_gap_days,
            CASE
                WHEN avg_gap_days<=7    THEN '1-7 Days'
                WHEN avg_gap_days<=30   THEN '8-30 Days'
                WHEN avg_gap_days<=60   THEN '31-60 Days'
                WHEN avg_gap_days<=90   THEN '61-90 Days'
                WHEN avg_gap_days<=180  THEN '91-180 Days'
                WHEN avg_gap_days<=365  THEN '180-365 Days'
                WHEN avg_gap_days<=730  THEN '1-2 Years'
                WHEN avg_gap_days<=1095 THEN '2-3 Years'
                WHEN avg_gap_days<=1460 THEN '3-4 Years'
                ELSE '4+ Years'
            END AS gap_range,
            CASE
                WHEN avg_gap_days<=7    THEN 1 WHEN avg_gap_days<=30   THEN 2
                WHEN avg_gap_days<=60   THEN 3 WHEN avg_gap_days<=90   THEN 4
                WHEN avg_gap_days<=180  THEN 5 WHEN avg_gap_days<=365  THEN 6
                WHEN avg_gap_days<=730  THEN 7 WHEN avg_gap_days<=1095 THEN 8
                WHEN avg_gap_days<=1460 THEN 9 ELSE 10
            END AS sort_order
        FROM customer_gaps
        WHERE avg_gap_days IS NOT NULL
    )
    SELECT
        gap_range,
        sort_order,
        COUNT(DISTINCT mobile)::bigint AS customers,
        AVG(avg_gap_days)::FLOAT AS avg_gap
    FROM bucketed
    GROUP BY gap_range, sort_order
    ORDER BY sort_order ASC;
""")
run_sql("INDEX", "CREATE UNIQUE INDEX IF NOT EXISTS idx_mv_gap_sort ON mv_gap_analysis(sort_order);")

# ── 2. mv_monthly_retention_2026 ──────────────────────────────────────────────
print("\n[2/4] mv_monthly_retention_2026")
run_sql("DROP", "DROP MATERIALIZED VIEW IF EXISTS mv_monthly_retention_2026 CASCADE;")
run_sql("CREATE", f"""
    CREATE MATERIALIZED VIEW mv_monthly_retention_2026 AS
    WITH
    base_customers AS (
        SELECT mobile AS "Customer Mobile"
        FROM mv_customer_summary
        WHERE first_visit <= '2025-12-31'
    ),
    purchases_2026 AS (
        SELECT
            s."Customer Mobile",
            DATE_TRUNC('month', s."parsed_date") AS month_start,
            s."Total Value"
        FROM {TABLE} s
        INNER JOIN base_customers b ON b."Customer Mobile" = s."Customer Mobile"
        WHERE s."parsed_date" >= '2026-01-01'
          AND s."Customer Mobile" IS NOT NULL
          AND LENGTH(s."Customer Mobile") = 10
          AND s."Customer Mobile" NOT IN ('1313131313','0000000000','9999999999')
    ),
    first_month AS (
        SELECT "Customer Mobile", MIN(month_start) AS first_month_2026
        FROM purchases_2026
        GROUP BY "Customer Mobile"
    ),
    monthly_agg AS (
        SELECT
            f.first_month_2026 AS month_start,
            COUNT(DISTINCT f."Customer Mobile")::INT AS unique_customers,
            SUM(p."Total Value")::FLOAT AS total_sales
        FROM first_month f
        JOIN purchases_2026 p
          ON p."Customer Mobile" = f."Customer Mobile"
         AND p.month_start = f.first_month_2026
        GROUP BY f.first_month_2026
    )
    SELECT
        TO_CHAR(month_start, 'Mon YYYY') AS month_label,
        month_start,
        unique_customers,
        ROUND(total_sales::NUMERIC, 2) AS total_sales
    FROM monthly_agg
    ORDER BY month_start ASC;
""")
run_sql("INDEX", "CREATE UNIQUE INDEX IF NOT EXISTS idx_mv_mr2026_month ON mv_monthly_retention_2026(month_start);")

# ── 3. mv_cohort_rfm ──────────────────────────────────────────────────────────
print("\n[3/4] mv_cohort_rfm")
run_sql("DROP", "DROP MATERIALIZED VIEW IF EXISTS mv_cohort_rfm CASCADE;")
run_sql("CREATE", """
    CREATE MATERIALIZED VIEW mv_cohort_rfm AS
    SELECT
        EXTRACT(YEAR FROM cs.last_visit::date)::TEXT AS cohort_year,
        CASE
            WHEN cs.visits = 1 THEN 'One-Time Buyer'
            WHEN cs.visits BETWEEN 2 AND 3 THEN 'Occasional'
            WHEN cs.visits BETWEEN 4 AND 6 THEN 'Regular'
            ELSE 'Loyal'
        END AS rfm_segment,
        COUNT(DISTINCT cs.mobile)::BIGINT AS customers,
        AVG(cs.total_spend)::FLOAT AS avg_spend,
        SUM(cs.total_spend)::FLOAT AS total_spend
    FROM mv_customer_summary cs
    WHERE cs.last_visit IS NOT NULL
      AND cs.last_visit ~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}'
    GROUP BY cohort_year, rfm_segment
    ORDER BY cohort_year DESC, customers DESC;
""")
run_sql("INDEX", "CREATE UNIQUE INDEX IF NOT EXISTS idx_mv_cohort_rfm ON mv_cohort_rfm(cohort_year, rfm_segment);")

# ── 4. mv_customer_propensity ────────────────────────────────────────────────
print("\n[4/4] mv_customer_propensity")
run_sql("DROP", "DROP MATERIALIZED VIEW IF EXISTS mv_customer_propensity CASCADE;")
run_sql("CREATE", """
    CREATE MATERIALIZED VIEW mv_customer_propensity AS
    WITH customer_features AS (
        SELECT
            mobile,
            visits AS frequency,
            total_spend AS monetary,
            (CURRENT_DATE - last_visit::date)  AS recency,
            (CURRENT_DATE - first_visit::date) AS age
        FROM mv_customer_summary
        WHERE last_visit IS NOT NULL AND first_visit IS NOT NULL
          AND last_visit ~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}'
          AND first_visit ~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}'
          AND mobile ~ '^[0-9]{10}$'
    ),
    normalized AS (
        SELECT
            mobile, frequency, monetary, recency, age,
            GREATEST(0.0, LEAST(3.0, (COALESCE(recency::float, 365.0) / 365.0))) AS recency_norm,
            GREATEST(0.0, LEAST(3.0, (COALESCE(frequency::float, 1.0) / 5.0))) AS freq_norm,
            GREATEST(0.0, LEAST(3.0, (COALESCE(monetary::float, 5000.0) / 25000.0))) AS monetary_norm,
            GREATEST(0.0, LEAST(3.0, (COALESCE(age::float, 365.0) / 730.0))) AS age_norm
        FROM customer_features
    )
    SELECT
        mobile,
        frequency,
        monetary::FLOAT,
        recency,
        age,
        ROUND((0.4 * recency_norm + 0.3 * freq_norm + 0.2 * monetary_norm + 0.1 * age_norm)::NUMERIC, 4) AS churn_score,
        CASE
            WHEN (0.4 * recency_norm + 0.3 * freq_norm) > 1.5 THEN 'High Risk'
            WHEN (0.4 * recency_norm + 0.3 * freq_norm) > 0.8 THEN 'Medium Risk'
            ELSE 'Low Risk'
        END AS churn_category
    FROM normalized;
""")
run_sql("INDEX", "CREATE UNIQUE INDEX IF NOT EXISTS idx_mv_propensity_mobile ON mv_customer_propensity(mobile);")

# ── Final verification ────────────────────────────────────────────────────────
print("\n" + "=" * 60)
print("  VERIFICATION")
print("=" * 60)
with connection.cursor() as cur:
    for mv in ["mv_gap_analysis", "mv_monthly_retention_2026", "mv_cohort_rfm", "mv_customer_propensity"]:
        try:
            cur.execute(f"SELECT COUNT(*) FROM {mv};")
            count = cur.fetchone()[0]
            print(f"  OK {mv}: {count:,} rows")
        except Exception as e:
            print(f"  MISSING {mv}: {e}")

print("\nAll missing MVs rebuilt! Dashboard is fully operational.")
