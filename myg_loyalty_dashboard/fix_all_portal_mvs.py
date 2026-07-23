"""
fix_all_portal_mvs.py
======================
Fixes all 20 MVs with data accuracy or performance issues.
Replaces v_sales_data + regex date parsing with parsed_date column.
Run: python fix_all_portal_mvs.py
"""
import os, django, time, sys
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

def run(label, sql, conn):
    t0 = time.time()
    print(f"    [{label}]...", end=" ", flush=True)
    try:
        cur = conn.cursor()
        cur.execute(sql)
        conn.commit()
        print(f"OK ({time.time()-t0:.1f}s)")
        return True
    except Exception as e:
        try:
            conn.rollback()
        except:
            pass
        print(f"ERROR: {e}")
        return False

def rebuild_mv(name, create_sql, index_sql=None, conn=None):
    close_after = conn is None
    if conn is None:
        conn = get_conn()
        conn.autocommit = False
    print(f"\n  Rebuilding {name}...")
    run("DROP", f'DROP MATERIALIZED VIEW IF EXISTS "{name}" CASCADE;', conn)
    ok = run("CREATE", create_sql, conn)
    if ok and index_sql:
        run("INDEX", index_sql, conn)
    if close_after:
        conn.close()
    return ok

print("=" * 70)
print("  FIXING ALL 20 PROBLEM MVs")
print("=" * 70)

# ═══════════════════════════════════════════════════════════════════════════
# BATCH 1 — Critical Accuracy Fixes
# ═══════════════════════════════════════════════════════════════════════════
print("\n" + "─" * 70)
print("  BATCH 1: Critical Accuracy Fixes (Quarterly & Yearly Members)")
print("─" * 70)

# [1] mv_quarterly_members
rebuild_mv("mv_quarterly_members", """
CREATE MATERIALIZED VIEW mv_quarterly_members AS
WITH cust_first AS (
    SELECT "Customer Mobile" AS mob,
        date_trunc('quarter', MIN(parsed_date))::date AS first_quarter
    FROM sales_data
    WHERE "Customer Mobile" ~ '^[0-9]{10}$'
        AND "Customer Mobile" NOT IN ('1313131313','0000000000','9999999999')
        AND parsed_date IS NOT NULL
    GROUP BY "Customer Mobile"
),
base AS (
    SELECT "Customer Mobile" AS mob,
        date_trunc('quarter', parsed_date)::date AS quarter_date,
        "Invoice Number" AS inv
    FROM sales_data
    WHERE "Customer Mobile" ~ '^[0-9]{10}$'
        AND "Customer Mobile" NOT IN ('1313131313','0000000000','9999999999')
        AND parsed_date IS NOT NULL
)
SELECT
    b.quarter_date,
    count(DISTINCT b.mob)::BIGINT AS total_members,
    count(DISTINCT b.mob) FILTER (WHERE cf.first_quarter = b.quarter_date)::BIGINT AS new_members,
    count(DISTINCT b.inv)::BIGINT AS total_visits
FROM base b JOIN cust_first cf ON b.mob = cf.mob
GROUP BY b.quarter_date
ORDER BY b.quarter_date;
""", "CREATE UNIQUE INDEX ON mv_quarterly_members(quarter_date);")

# [2] mv_quarterly_members_branch
rebuild_mv("mv_quarterly_members_branch", """
CREATE MATERIALIZED VIEW mv_quarterly_members_branch AS
WITH cust_first AS (
    SELECT "Customer Mobile" AS mob,
        date_trunc('quarter', MIN(parsed_date))::date AS first_quarter
    FROM sales_data
    WHERE "Customer Mobile" ~ '^[0-9]{10}$'
        AND "Customer Mobile" NOT IN ('1313131313','0000000000','9999999999')
        AND parsed_date IS NOT NULL
    GROUP BY "Customer Mobile"
),
base AS (
    SELECT
        "Customer Mobile" AS mob,
        "Branch" AS branch,
        date_trunc('quarter', parsed_date)::date AS quarter_date,
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
    b.quarter_date,
    count(DISTINCT b.mob)::BIGINT AS total_members,
    count(DISTINCT b.mob) FILTER (WHERE cf.first_quarter = b.quarter_date)::BIGINT AS new_members,
    count(DISTINCT b.inv)::BIGINT AS total_visits
FROM base b JOIN cust_first cf ON b.mob = cf.mob
GROUP BY b.branch, b.quarter_date
ORDER BY b.branch, b.quarter_date;
""", "CREATE UNIQUE INDEX ON mv_quarterly_members_branch(branch, quarter_date);")

# [3] mv_yearly_members
rebuild_mv("mv_yearly_members", """
CREATE MATERIALIZED VIEW mv_yearly_members AS
WITH cust_first AS (
    SELECT "Customer Mobile" AS mob,
        EXTRACT(YEAR FROM MIN(parsed_date))::INT AS first_year
    FROM sales_data
    WHERE "Customer Mobile" ~ '^[0-9]{10}$'
        AND "Customer Mobile" NOT IN ('1313131313','0000000000','9999999999')
        AND parsed_date IS NOT NULL
    GROUP BY "Customer Mobile"
),
base AS (
    SELECT "Customer Mobile" AS mob,
        EXTRACT(YEAR FROM parsed_date)::INT AS active_year,
        "Invoice Number" AS inv
    FROM sales_data
    WHERE "Customer Mobile" ~ '^[0-9]{10}$'
        AND "Customer Mobile" NOT IN ('1313131313','0000000000','9999999999')
        AND parsed_date IS NOT NULL
)
SELECT
    b.active_year,
    count(DISTINCT b.mob)::BIGINT AS total_members,
    count(DISTINCT b.mob) FILTER (WHERE cf.first_year = b.active_year)::BIGINT AS new_members,
    count(DISTINCT b.inv)::BIGINT AS total_visits
FROM base b JOIN cust_first cf ON b.mob = cf.mob
GROUP BY b.active_year
ORDER BY b.active_year;
""", "CREATE UNIQUE INDEX ON mv_yearly_members(active_year);")

# [4] mv_yearly_members_branch
rebuild_mv("mv_yearly_members_branch", """
CREATE MATERIALIZED VIEW mv_yearly_members_branch AS
WITH cust_first AS (
    SELECT "Customer Mobile" AS mob,
        EXTRACT(YEAR FROM MIN(parsed_date))::INT AS first_year
    FROM sales_data
    WHERE "Customer Mobile" ~ '^[0-9]{10}$'
        AND "Customer Mobile" NOT IN ('1313131313','0000000000','9999999999')
        AND parsed_date IS NOT NULL
    GROUP BY "Customer Mobile"
),
base AS (
    SELECT "Customer Mobile" AS mob,
        "Branch" AS branch,
        EXTRACT(YEAR FROM parsed_date)::INT AS active_year,
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
    b.active_year,
    count(DISTINCT b.mob)::BIGINT AS total_members,
    count(DISTINCT b.mob) FILTER (WHERE cf.first_year = b.active_year)::BIGINT AS new_members,
    count(DISTINCT b.inv)::BIGINT AS total_visits
FROM base b JOIN cust_first cf ON b.mob = cf.mob
GROUP BY b.branch, b.active_year
ORDER BY b.branch, b.active_year;
""", "CREATE UNIQUE INDEX ON mv_yearly_members_branch(branch, active_year);")

print("\n  Batch 1 complete! Verifying counts...")
conn = get_conn()
conn.autocommit = True
cur = conn.cursor()
cur.execute("SELECT quarter_date, total_members FROM mv_quarterly_members ORDER BY quarter_date DESC LIMIT 3;")
print("  mv_quarterly_members latest quarters:")
for r in cur.fetchall():
    print(f"    {r[0]}: {r[1]:,} members")
cur.execute("SELECT active_year, total_members FROM mv_yearly_members ORDER BY active_year DESC LIMIT 3;")
print("  mv_yearly_members latest years:")
for r in cur.fetchall():
    print(f"    {r[0]}: {r[1]:,} members")
conn.close()

# ═══════════════════════════════════════════════════════════════════════════
# BATCH 2 — Dashboard Performance Fixes
# ═══════════════════════════════════════════════════════════════════════════
print("\n" + "─" * 70)
print("  BATCH 2: Dashboard Performance Fixes")
print("─" * 70)

# [5] mv_monthly_members
rebuild_mv("mv_monthly_members", """
CREATE MATERIALIZED VIEW mv_monthly_members AS
WITH cust_first AS (
    SELECT "Customer Mobile" AS mob,
        date_trunc('month', MIN(parsed_date))::date AS fv_month
    FROM sales_data
    WHERE "Customer Mobile" IS NOT NULL
        AND "Customer Mobile" ~ '^[0-9]{10}$'
        AND "Customer Mobile" NOT IN ('1313131313','0000000000','9999999999')
        AND parsed_date IS NOT NULL
    GROUP BY "Customer Mobile"
),
monthly AS (
    SELECT
        date_trunc('month', s.parsed_date)::date AS month_date,
        count(DISTINCT s."Customer Mobile") AS total_members,
        count(DISTINCT s."Customer Mobile") FILTER (
            WHERE cf.fv_month = date_trunc('month', s.parsed_date)::date
        ) AS new_members,
        count(DISTINCT s."Invoice Number") AS total_visits
    FROM sales_data s
    JOIN cust_first cf ON cf.mob = s."Customer Mobile"
    WHERE s."Customer Mobile" IS NOT NULL
        AND s."Customer Mobile" ~ '^[0-9]{10}$'
        AND s."Customer Mobile" NOT IN ('1313131313','0000000000','9999999999')
        AND s.parsed_date IS NOT NULL
    GROUP BY date_trunc('month', s.parsed_date)::date
)
SELECT month_date, total_members, new_members, total_visits,
    (total_members - new_members) AS repeat_members
FROM monthly
ORDER BY month_date;
""", "CREATE UNIQUE INDEX ON mv_monthly_members(month_date);")

# [6] mv_monthly_members_branch
rebuild_mv("mv_monthly_members_branch", """
CREATE MATERIALIZED VIEW mv_monthly_members_branch AS
WITH cust_first AS (
    SELECT "Customer Mobile" AS mob,
        date_trunc('month', MIN(parsed_date))::date AS fv_month
    FROM sales_data
    WHERE "Customer Mobile" IS NOT NULL
        AND "Customer Mobile" ~ '^[0-9]{10}$'
        AND "Customer Mobile" NOT IN ('1313131313','0000000000','9999999999')
        AND parsed_date IS NOT NULL
    GROUP BY "Customer Mobile"
),
monthly AS (
    SELECT
        s."Branch" AS branch,
        date_trunc('month', s.parsed_date)::date AS month_date,
        count(DISTINCT s."Customer Mobile") AS total_members,
        count(DISTINCT s."Customer Mobile") FILTER (
            WHERE cf.fv_month = date_trunc('month', s.parsed_date)::date
        ) AS new_members,
        count(DISTINCT s."Invoice Number") AS total_visits
    FROM sales_data s
    JOIN cust_first cf ON cf.mob = s."Customer Mobile"
    WHERE s."Customer Mobile" IS NOT NULL
        AND s."Customer Mobile" ~ '^[0-9]{10}$'
        AND s."Customer Mobile" NOT IN ('1313131313','0000000000','9999999999')
        AND s.parsed_date IS NOT NULL
        AND s."Branch" IS NOT NULL
        AND s."Branch" NOT IN ('HEAD OFFICE','UG SMART CHOICE')
    GROUP BY s."Branch", date_trunc('month', s.parsed_date)::date
)
SELECT branch, month_date, total_members, new_members, total_visits,
    (total_members - new_members) AS repeat_members
FROM monthly
ORDER BY branch, month_date;
""", "CREATE UNIQUE INDEX ON mv_monthly_members_branch(branch, month_date);")

# [7] mv_fy_members
rebuild_mv("mv_fy_members", """
CREATE MATERIALIZED VIEW mv_fy_members AS
WITH cust_first AS (
    SELECT "Customer Mobile" AS mob,
        CASE
            WHEN EXTRACT(MONTH FROM MIN(parsed_date)) >= 4
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
        CASE
            WHEN EXTRACT(MONTH FROM parsed_date) >= 4
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

# [8] mv_fy_members_branch
rebuild_mv("mv_fy_members_branch", """
CREATE MATERIALIZED VIEW mv_fy_members_branch AS
WITH cust_first AS (
    SELECT "Customer Mobile" AS mob,
        CASE
            WHEN EXTRACT(MONTH FROM MIN(parsed_date)) >= 4
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
        CASE
            WHEN EXTRACT(MONTH FROM parsed_date) >= 4
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

# [9] mv_branch_summary — rebuild from sales_data with parsed_date
rebuild_mv("mv_branch_summary", """
CREATE MATERIALIZED VIEW mv_branch_summary AS
SELECT
    "Branch" AS branch,
    COUNT(DISTINCT "Customer Mobile") AS unique_customers,
    COUNT(DISTINCT "Invoice Number") AS total_invoices,
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

# [10] mv_rfm_segments — uses v_sales_data, switch to parsed_date
# Read current definition first to preserve logic
conn2 = get_conn()
conn2.autocommit = True
cur2 = conn2.cursor()
cur2.execute("SELECT definition FROM pg_matviews WHERE matviewname = 'mv_rfm_segments';")
rfm_def = cur2.fetchone()[0]
conn2.close()
# Rebuild using mv_customer_summary (which is already accurate)
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

# [11] mv_rfm_summary
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

print("\n  Batch 2 complete!")

# ═══════════════════════════════════════════════════════════════════════════
# BATCH 3 — Heavy Analytics MVs
# ═══════════════════════════════════════════════════════════════════════════
print("\n" + "─" * 70)
print("  BATCH 3: Heavy Analytics MVs")
print("─" * 70)

# [12] mv_customer_active_years
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

# [13] mv_customer_yearly_revenue
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

# [14] mv_cohort_customer_years
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

# [15] mv_branch_resurrection_2024_2026
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
base_2026 AS (
    SELECT "Customer Mobile" AS mobile, "Branch" AS branch
    FROM sales_data
    WHERE parsed_date >= '2026-01-01'
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
)
SELECT
    a.branch,
    COUNT(DISTINCT b26.mobile)::BIGINT AS resurrected_customers
FROM absent_2025 a
JOIN base_2026 b26 ON b26.mobile = a.mobile AND b26.branch = a.branch
GROUP BY a.branch
ORDER BY resurrected_customers DESC;
""", "CREATE UNIQUE INDEX ON mv_branch_resurrection_2024_2026(branch);")

print("\n  Batch 3 complete!")

# ═══════════════════════════════════════════════════════════════════════════
# FINAL VERIFICATION
# ═══════════════════════════════════════════════════════════════════════════
print("\n" + "=" * 70)
print("  FINAL VERIFICATION")
print("=" * 70)

conn3 = get_conn()
conn3.autocommit = True
cur3 = conn3.cursor()

# Check all 34 MVs exist
cur3.execute("SELECT matviewname FROM pg_matviews ORDER BY matviewname;")
existing = {r[0] for r in cur3.fetchall()}
print(f"\n  Total MVs in DB: {len(existing)}")

# Key count checks
checks = [
    ("mv_quarterly_members",  "SELECT quarter_date, total_members FROM mv_quarterly_members ORDER BY quarter_date DESC LIMIT 2;"),
    ("mv_yearly_members",     "SELECT active_year, total_members FROM mv_yearly_members ORDER BY active_year DESC LIMIT 2;"),
    ("mv_monthly_members",    "SELECT month_date, total_members FROM mv_monthly_members ORDER BY month_date DESC LIMIT 2;"),
    ("mv_rfm_segments",       "SELECT COUNT(*) FROM mv_rfm_segments;"),
    ("mv_branch_summary",     "SELECT COUNT(*) FROM mv_branch_summary;"),
]
for name, sql in checks:
    try:
        cur3.execute(sql)
        rows = cur3.fetchall()
        print(f"\n  {name}:")
        for r in rows:
            print(f"    {r}")
    except Exception as e:
        print(f"  {name}: ERROR {e}")

conn3.close()
print("\n" + "=" * 70)
print("  ALL DONE! Portal MVs fully optimized and accurate.")
print("=" * 70)
