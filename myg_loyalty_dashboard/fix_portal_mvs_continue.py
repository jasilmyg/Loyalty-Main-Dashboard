"""
fix_portal_mvs_continue.py
===========================
Continues from mv_yearly_members onwards.
Batch 1 remaining + Batch 2 + Batch 3
"""
import os, django, time
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'myg_loyalty_dashboard.settings')
django.setup()
import psycopg2
from django.conf import settings

db = settings.DATABASES['default']

def get_conn():
    return psycopg2.connect(
        host=db['HOST'], port=db['PORT'], dbname=db['NAME'],
        user=db['USER'], password=db['PASSWORD'], sslmode='require'
    )

def rebuild_mv(name, create_sql, index_sql=None):
    conn = get_conn()
    conn.autocommit = True
    cur = conn.cursor()
    print(f"\n  Rebuilding {name}...")
    t0 = time.time()
    try:
        cur.execute(f'DROP MATERIALIZED VIEW IF EXISTS "{name}" CASCADE;')
        print(f"    [DROP]... OK")
        cur.execute(create_sql)
        print(f"    [CREATE]... OK ({time.time()-t0:.1f}s)")
        if index_sql:
            cur.execute(index_sql)
            print(f"    [INDEX]... OK")
    except Exception as e:
        print(f"    ERROR: {e}")
    finally:
        conn.close()

print("=" * 70)
print("  CONTINUING MV FIXES (from mv_yearly_members)")
print("=" * 70)

# ── BATCH 1 REMAINING ──────────────────────────────────────────────────────
print("\n--- Batch 1 Remaining ---")

# mv_yearly_members is DONE

# mv_yearly_members_branch skipped (not needed)

# ── BATCH 2 ────────────────────────────────────────────────────────────────
print("\n--- Batch 2: Dashboard Performance Fixes ---")

# rebuild_mv("mv_monthly_members", """
# CREATE MATERIALIZED VIEW mv_monthly_members AS
# WITH cust_first AS (
#     SELECT "Customer Mobile" AS mob,
#         date_trunc('month', MIN(parsed_date))::date AS fv_month
#     FROM sales_data
#     WHERE "Customer Mobile" IS NOT NULL
#         AND "Customer Mobile" ~ '^[0-9]{10}$'
#         AND "Customer Mobile" NOT IN ('1313131313','0000000000','9999999999')
#         AND parsed_date IS NOT NULL
#     GROUP BY "Customer Mobile"
# )
# SELECT
#     date_trunc('month', s.parsed_date)::date AS month_date,
#     count(DISTINCT s."Customer Mobile")::BIGINT AS total_members,
#     count(DISTINCT s."Customer Mobile") FILTER (
#         WHERE cf.fv_month = date_trunc('month', s.parsed_date)::date
#     )::BIGINT AS new_members,
#     count(DISTINCT s."Invoice Number")::BIGINT AS total_visits,
#     (count(DISTINCT s."Customer Mobile") - count(DISTINCT s."Customer Mobile") FILTER (
#         WHERE cf.fv_month = date_trunc('month', s.parsed_date)::date
#     ))::BIGINT AS repeat_members
# FROM sales_data s
# JOIN cust_first cf ON cf.mob = s."Customer Mobile"
# WHERE s."Customer Mobile" IS NOT NULL
#     AND s."Customer Mobile" ~ '^[0-9]{10}$'
#     AND s."Customer Mobile" NOT IN ('1313131313','0000000000','9999999999')
#     AND s.parsed_date IS NOT NULL
# GROUP BY date_trunc('month', s.parsed_date)::date
# ORDER BY month_date;
# """, "CREATE UNIQUE INDEX ON mv_monthly_members(month_date);")

# mv_monthly_members_branch skipped (not needed)

WITH cust_first AS (
    SELECT "Customer Mobile" AS mob,
        CASE WHEN EXTRACT(MONTH FROM MIN(parsed_date)) >= 4
            THEN EXTRACT(YEAR FROM MIN(parsed_date))::INT
            ELSE EXTRACT(YEAR FROM MIN(parsed_date))::INT - 1
        END AS first_fy
    FROM sales_data
    WHERE "Customer Mobile" ~ '^[0-9]{10}$'
        AND "Customer Mobile" NOT IN ('1313131313','0000000000','9999999999')
        AND parsed_date IS NOT NULL
    GROUP BY "Customer Mobile"
),
base AS (
    SELECT "Customer Mobile" AS mob,
        CASE WHEN EXTRACT(MONTH FROM parsed_date) >= 4
            THEN EXTRACT(YEAR FROM parsed_date)::INT
            ELSE EXTRACT(YEAR FROM parsed_date)::INT - 1
        END AS fy_year,
        "Invoice Number" AS inv
    FROM sales_data
    WHERE "Customer Mobile" ~ '^[0-9]{10}$'
        AND "Customer Mobile" NOT IN ('1313131313','0000000000','9999999999')
        AND parsed_date IS NOT NULL
)
SELECT
    b.fy_year,
    count(DISTINCT b.mob)::BIGINT AS total_members,
    count(DISTINCT b.mob) FILTER (WHERE cf.first_fy = b.fy_year)::BIGINT AS new_members,
    count(DISTINCT b.inv)::BIGINT AS total_visits
FROM base b JOIN cust_first cf ON b.mob = cf.mob
GROUP BY b.fy_year
ORDER BY b.fy_year;
""", "CREATE UNIQUE INDEX ON mv_fy_members(fy_year);")

rebuild_mv("mv_fy_members_branch", """
CREATE MATERIALIZED VIEW mv_fy_members_branch AS
WITH cust_first AS (
    SELECT "Customer Mobile" AS mob,
        CASE WHEN EXTRACT(MONTH FROM MIN(parsed_date)) >= 4
            THEN EXTRACT(YEAR FROM MIN(parsed_date))::INT
            ELSE EXTRACT(YEAR FROM MIN(parsed_date))::INT - 1
        END AS first_fy
    FROM sales_data
    WHERE "Customer Mobile" ~ '^[0-9]{10}$'
        AND "Customer Mobile" NOT IN ('1313131313','0000000000','9999999999')
        AND parsed_date IS NOT NULL
    GROUP BY "Customer Mobile"
),
base AS (
    SELECT "Customer Mobile" AS mob,
        "Branch" AS branch,
        CASE WHEN EXTRACT(MONTH FROM parsed_date) >= 4
            THEN EXTRACT(YEAR FROM parsed_date)::INT
            ELSE EXTRACT(YEAR FROM parsed_date)::INT - 1
        END AS fy_year,
        "Invoice Number" AS inv
    FROM sales_data
    WHERE "Customer Mobile" ~ '^[0-9]{10}$'
        AND "Customer Mobile" NOT IN ('1313131313','0000000000','9999999999')
        AND parsed_date IS NOT NULL
        AND "Branch" IS NOT NULL
        AND "Branch" NOT IN ('HEAD OFFICE','UG SMART CHOICE')
)
SELECT
    b.branch,
    b.fy_year,
    count(DISTINCT b.mob)::BIGINT AS total_members,
    count(DISTINCT b.mob) FILTER (WHERE cf.first_fy = b.fy_year)::BIGINT AS new_members,
    count(DISTINCT b.inv)::BIGINT AS total_visits
FROM base b JOIN cust_first cf ON b.mob = cf.mob
GROUP BY b.branch, b.fy_year
ORDER BY b.branch, b.fy_year;
""", "CREATE UNIQUE INDEX ON mv_fy_members_branch(branch, fy_year);")

rebuild_mv("mv_branch_summary", """
CREATE MATERIALIZED VIEW mv_branch_summary AS
SELECT
    "Branch" AS branch,
    COUNT(DISTINCT "Customer Mobile")::BIGINT AS unique_customers,
    COUNT(DISTINCT "Invoice Number")::BIGINT AS total_invoices,
    SUM("Total Value")::FLOAT AS total_sales,
    COUNT(DISTINCT parsed_date) AS active_days,
    MIN(parsed_date) AS first_sale_date,
    MAX(parsed_date) AS last_sale_date
FROM sales_data
WHERE "Branch" IS NOT NULL
    AND "Branch" NOT IN ('HEAD OFFICE','UG SMART CHOICE')
    AND "Invoice Number" NOT LIKE 'SMC/%'
    AND "Invoice Number" NOT LIKE 'EI/%'
    AND parsed_date IS NOT NULL
GROUP BY "Branch"
ORDER BY total_sales DESC;
""", "CREATE UNIQUE INDEX ON mv_branch_summary(branch);")

rebuild_mv("mv_rfm_segments", """
CREATE MATERIALIZED VIEW mv_rfm_segments AS
SELECT
    mobile,
    visits,
    total_spend,
    last_visit,
    first_visit,
    CASE
        WHEN visits = 1 THEN 'One-Time'
        WHEN visits BETWEEN 2 AND 3 THEN 'Occasional'
        WHEN visits BETWEEN 4 AND 6 THEN 'Regular'
        ELSE 'Loyal'
    END AS segment,
    CASE
        WHEN (CURRENT_DATE - last_visit::date) <= 30 THEN 'Active'
        WHEN (CURRENT_DATE - last_visit::date) <= 90 THEN 'At Risk'
        WHEN (CURRENT_DATE - last_visit::date) <= 180 THEN 'Lapsing'
        ELSE 'Dormant'
    END AS recency_band
FROM mv_customer_summary
WHERE mobile ~ '^[0-9]{10}$'
    AND last_visit ~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}';
""", "CREATE UNIQUE INDEX ON mv_rfm_segments(mobile);")

rebuild_mv("mv_rfm_summary", """
CREATE MATERIALIZED VIEW mv_rfm_summary AS
SELECT
    mobile,
    visits,
    total_spend,
    last_visit,
    first_visit,
    CASE
        WHEN visits = 1 THEN 'One-Time'
        WHEN visits BETWEEN 2 AND 3 THEN 'Occasional'
        WHEN visits BETWEEN 4 AND 6 THEN 'Regular'
        ELSE 'Loyal'
    END AS segment,
    CASE
        WHEN (CURRENT_DATE - last_visit::date) <= 30 THEN 'Active'
        WHEN (CURRENT_DATE - last_visit::date) <= 90 THEN 'At Risk'
        WHEN (CURRENT_DATE - last_visit::date) <= 180 THEN 'Lapsing'
        ELSE 'Dormant'
    END AS recency_band,
    NTILE(4) OVER (ORDER BY (CURRENT_DATE - last_visit::date) ASC) AS r_score,
    NTILE(4) OVER (ORDER BY visits DESC) AS f_score,
    NTILE(4) OVER (ORDER BY total_spend DESC) AS m_score
FROM mv_customer_summary
WHERE mobile ~ '^[0-9]{10}$'
    AND last_visit ~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}';
""", "CREATE UNIQUE INDEX ON mv_rfm_summary(mobile);")

# ── BATCH 3 ────────────────────────────────────────────────────────────────
print("\n--- Batch 3: Heavy Analytics MVs ---")

rebuild_mv("mv_customer_active_years", """
CREATE MATERIALIZED VIEW mv_customer_active_years AS
SELECT
    "Customer Mobile" AS mobile,
    EXTRACT(YEAR FROM parsed_date)::INT AS active_year,
    SUM("Total Value")::FLOAT AS yearly_spend
FROM sales_data
WHERE "Customer Mobile" ~ '^[0-9]{10}$'
    AND "Customer Mobile" NOT IN ('1313131313','0000000000','9999999999')
    AND parsed_date IS NOT NULL
GROUP BY "Customer Mobile", EXTRACT(YEAR FROM parsed_date)::INT;
""", "CREATE INDEX ON mv_customer_active_years(mobile);")

rebuild_mv("mv_customer_yearly_revenue", """
CREATE MATERIALIZED VIEW mv_customer_yearly_revenue AS
SELECT
    "Customer Mobile" AS mobile,
    EXTRACT(YEAR FROM parsed_date)::INT AS active_year,
    SUM("Total Value")::FLOAT AS year_revenue
FROM sales_data
WHERE "Customer Mobile" ~ '^[0-9]{10}$'
    AND "Customer Mobile" NOT IN ('1313131313','0000000000','9999999999')
    AND parsed_date IS NOT NULL
GROUP BY "Customer Mobile", EXTRACT(YEAR FROM parsed_date)::INT;
""", "CREATE INDEX ON mv_customer_yearly_revenue(mobile);")

rebuild_mv("mv_cohort_customer_years", """
CREATE MATERIALIZED VIEW mv_cohort_customer_years AS
WITH customer_first AS (
    SELECT "Customer Mobile" AS mobile,
        EXTRACT(YEAR FROM MIN(parsed_date))::INT AS cohort_year
    FROM sales_data
    WHERE "Customer Mobile" ~ '^[0-9]{10}$'
        AND "Customer Mobile" NOT IN ('1313131313','0000000000','9999999999')
        AND parsed_date IS NOT NULL
    GROUP BY "Customer Mobile"
),
customer_yearly AS (
    SELECT "Customer Mobile" AS mobile,
        EXTRACT(YEAR FROM parsed_date)::INT AS activity_year,
        SUM("Total Value")::FLOAT AS year_revenue,
        COUNT(DISTINCT parsed_date) AS visit_days
    FROM sales_data
    WHERE "Customer Mobile" ~ '^[0-9]{10}$'
        AND "Customer Mobile" NOT IN ('1313131313','0000000000','9999999999')
        AND parsed_date IS NOT NULL
    GROUP BY "Customer Mobile", EXTRACT(YEAR FROM parsed_date)::INT
)
SELECT
    cf.cohort_year,
    cy.activity_year,
    (cy.activity_year - cf.cohort_year) AS year_index,
    COUNT(DISTINCT cy.mobile)::BIGINT AS active_customers,
    SUM(cy.year_revenue) AS year_revenue
FROM customer_yearly cy
JOIN customer_first cf ON cf.mobile = cy.mobile
GROUP BY cf.cohort_year, cy.activity_year, (cy.activity_year - cf.cohort_year)
ORDER BY cf.cohort_year, cy.activity_year;
""", "CREATE INDEX ON mv_cohort_customer_years(cohort_year, activity_year);")

rebuild_mv("mv_branch_resurrection_2024_2026", """
CREATE MATERIALIZED VIEW mv_branch_resurrection_2024_2026 AS
WITH base_2024 AS (
    SELECT "Customer Mobile" AS mobile, "Branch" AS branch
    FROM sales_data
    WHERE parsed_date BETWEEN '2024-01-01' AND '2024-12-31'
        AND "Customer Mobile" ~ '^[0-9]{10}$'
        AND "Customer Mobile" NOT IN ('1313131313','0000000000','9999999999')
        AND "Branch" IS NOT NULL AND "Branch" NOT IN ('HEAD OFFICE','UG SMART CHOICE')
    GROUP BY "Customer Mobile", "Branch"
),
absent_2025 AS (
    SELECT mobile, branch FROM base_2024
    EXCEPT
    SELECT "Customer Mobile", "Branch" FROM sales_data
    WHERE parsed_date BETWEEN '2025-01-01' AND '2025-12-31'
        AND "Customer Mobile" ~ '^[0-9]{10}$'
        AND "Branch" IS NOT NULL AND "Branch" NOT IN ('HEAD OFFICE','UG SMART CHOICE')
),
base_2026 AS (
    SELECT "Customer Mobile" AS mobile, "Branch" AS branch
    FROM sales_data
    WHERE parsed_date >= '2026-01-01'
        AND "Customer Mobile" ~ '^[0-9]{10}$'
        AND "Customer Mobile" NOT IN ('1313131313','0000000000','9999999999')
        AND "Branch" IS NOT NULL AND "Branch" NOT IN ('HEAD OFFICE','UG SMART CHOICE')
    GROUP BY "Customer Mobile", "Branch"
)
SELECT
    a.branch,
    COUNT(DISTINCT b26.mobile)::BIGINT AS resurrected_customers
FROM absent_2025 a
JOIN base_2026 b26 ON b26.mobile = a.mobile AND b26.branch = a.branch
GROUP BY a.branch
ORDER BY resurrected_customers DESC;
""", "CREATE UNIQUE INDEX ON mv_branch_resurrection_2024_2026(branch);")

# ── FINAL CHECK ────────────────────────────────────────────────────────────
print("\n" + "=" * 70)
print("  FINAL VERIFICATION")
print("=" * 70)
conn = get_conn()
conn.autocommit = True
cur = conn.cursor()
cur.execute("SELECT matviewname FROM pg_matviews ORDER BY matviewname;")
existing = [r[0] for r in cur.fetchall()]
print(f"\n  Total MVs in DB: {len(existing)}")
cur.execute("SELECT active_year, total_members FROM mv_yearly_members ORDER BY active_year DESC LIMIT 3;")
print("\n  mv_yearly_members:")
for r in cur.fetchall():
    print(f"    {r[0]}: {r[1]:,}")
cur.execute("SELECT month_date, total_members FROM mv_monthly_members ORDER BY month_date DESC LIMIT 3;")
print("\n  mv_monthly_members (latest):")
for r in cur.fetchall():
    print(f"    {r[0]}: {r[1]:,}")
conn.close()
print("\n  ALL DONE!")
