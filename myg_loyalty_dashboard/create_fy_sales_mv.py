"""
Create mv_fy_sales - pre-aggregated FY sales data built from 
mv_monthly_summary (for revenue) + mv_fy_members (for member counts).
This avoids the 3-minute full scan of v_sales_data.
"""
import os, django, time
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'myg_loyalty_dashboard.settings')
django.setup()
from django.db import connection

def run(sql, label=""):
    with connection.cursor() as cur:
        cur.execute(sql)
    if label:
        print(f"  OK: {label}")

print("Step 1: Creating mv_fy_sales...")
run("DROP MATERIALIZED VIEW IF EXISTS mv_fy_sales CASCADE")
run("""
CREATE MATERIALIZED VIEW mv_fy_sales AS
WITH monthly_by_fy AS (
    SELECT
        CASE WHEN EXTRACT(MONTH FROM month_date) >= 4
             THEN EXTRACT(YEAR FROM month_date)
             ELSE EXTRACT(YEAR FROM month_date) - 1
        END::INTEGER AS fy_year,
        SUM(revenue)::FLOAT AS total_sale
    FROM mv_monthly_summary
    GROUP BY 1
)
SELECT
    m.fy_year,
    m.total_sale,
    f.total_members                          AS total_customers,
    f.new_members,
    (f.total_members - f.new_members)        AS repeat_members,
    -- Approximate new/repeat sale split using member ratio
    CASE WHEN f.total_members > 0
         THEN m.total_sale * f.new_members::FLOAT / f.total_members
         ELSE 0
    END                                       AS new_sale,
    CASE WHEN f.total_members > 0
         THEN m.total_sale * (f.total_members - f.new_members)::FLOAT / f.total_members
         ELSE 0
    END                                       AS repeat_sale
FROM monthly_by_fy m
JOIN mv_fy_members f ON f.fy_year = m.fy_year
ORDER BY m.fy_year ASC
WITH DATA
""", "mv_fy_sales created")

run("CREATE UNIQUE INDEX ON mv_fy_sales(fy_year)", "index created")

print("\nStep 2: Verifying mv_fy_sales...")
with connection.cursor() as cur:
    cur.execute("SELECT * FROM mv_fy_sales ORDER BY fy_year")
    rows = cur.fetchall()
    print(f"  Rows: {len(rows)}")
    for r in rows:
        fy = int(r[0])
        print(f"  FY {fy}-{str(fy+1)[-2:]}: sale={r[1]/1e7:.2f}Cr  cust={r[2]:,}  new_cust={r[3]:,}  new_sale={r[5]/1e7:.2f}Cr")

print("\nStep 3: Also create mv_fy_sales_branch for filtered queries...")
run("DROP MATERIALIZED VIEW IF EXISTS mv_fy_sales_branch CASCADE")
run("""
CREATE MATERIALIZED VIEW mv_fy_sales_branch AS
WITH monthly_by_fy_branch AS (
    SELECT
        UPPER("Branch") AS branch,
        CASE WHEN EXTRACT(MONTH FROM month_date) >= 4
             THEN EXTRACT(YEAR FROM month_date)
             ELSE EXTRACT(YEAR FROM month_date) - 1
        END::INTEGER AS fy_year,
        SUM(revenue)::FLOAT  AS total_sale,
        SUM(customers)::BIGINT AS total_customers
    FROM mv_monthly_summary
    WHERE "Branch" IS NOT NULL AND "Branch" != ''
    GROUP BY 1, 2
),
branch_members AS (
    SELECT
        UPPER("Branch") AS branch,
        CASE WHEN EXTRACT(MONTH FROM month_date) >= 4
             THEN EXTRACT(YEAR FROM month_date)
             ELSE EXTRACT(YEAR FROM month_date) - 1
        END::INTEGER AS fy_year,
        SUM(customers)::BIGINT AS new_members
    FROM mv_monthly_summary
    WHERE "Branch" IS NOT NULL AND "Branch" != ''
    GROUP BY 1, 2
)
SELECT
    m.branch,
    m.fy_year,
    m.total_sale,
    m.total_customers,
    bm.new_members,
    CASE WHEN m.total_customers > 0
         THEN m.total_sale * bm.new_members::FLOAT / m.total_customers
         ELSE 0
    END AS new_sale,
    CASE WHEN m.total_customers > 0
         THEN m.total_sale * (m.total_customers - bm.new_members)::FLOAT / m.total_customers
         ELSE 0
    END AS repeat_sale
FROM monthly_by_fy_branch m
JOIN branch_members bm ON bm.branch = m.branch AND bm.fy_year = m.fy_year
ORDER BY m.branch, m.fy_year ASC
WITH DATA
""", "mv_fy_sales_branch created")

run("CREATE INDEX ON mv_fy_sales_branch(branch, fy_year)", "branch index created")

print("\nStep 4: Benchmark...")
t0 = time.time()
with connection.cursor() as cur:
    for _ in range(5):
        cur.execute("SELECT * FROM mv_fy_sales ORDER BY fy_year")
        cur.fetchall()
elapsed = (time.time() - t0) / 5
print(f"  mv_fy_sales avg query: {elapsed*1000:.1f}ms")

print("\nDone! mv_fy_sales and mv_fy_sales_branch are ready.")
