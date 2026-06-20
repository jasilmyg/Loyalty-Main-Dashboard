"""
Creates mv_yearly_customer_cohorts — a deduplicated MV with one row
per (customer, year). This makes ALL cross-year customer queries
run in < 2 seconds instead of 3+ minutes on the 12.6M row table.

Also pre-computes mv_cohort_cross_year — a pivot with flags for
each year (2021-2026), making "bought in X not Y" queries instant.
"""
import os, django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'myg_loyalty_dashboard.settings')
django.setup()

from django.db import connection

def run(sql, desc=""):
    print(f"  Running: {desc}...")
    with connection.cursor() as cur:
        cur.execute(sql)
    print(f"  Done: {desc}")

print("=" * 60)
print("  Building Fast Cohort Materialized Views")
print("=" * 60)

# 1. mv_yearly_customer_cohorts: one row per (customer, year)
#    This is the base for all cross-year analysis
run("""
    DROP MATERIALIZED VIEW IF EXISTS mv_yearly_customer_cohorts CASCADE;
""", "Dropping mv_yearly_customer_cohorts")

run("""
    CREATE MATERIALIZED VIEW mv_yearly_customer_cohorts AS
    SELECT
        "Customer Mobile"                          AS customer_mobile,
        EXTRACT(YEAR FROM parsed_date)::INTEGER    AS purchase_year,
        COUNT(*)                                   AS purchase_count,
        SUM("Total Value")                         AS total_spend,
        MIN(parsed_date)                           AS first_purchase,
        MAX(parsed_date)                           AS last_purchase
    FROM sales_data
    WHERE parsed_date IS NOT NULL
      AND "Customer Mobile" IS NOT NULL
      AND "Customer Mobile" != ''
    GROUP BY "Customer Mobile", EXTRACT(YEAR FROM parsed_date)::INTEGER;
""", "Creating mv_yearly_customer_cohorts")

# 2. Index on customer_mobile and purchase_year for fast lookups
run("""
    CREATE INDEX idx_mv_yearly_cohort_mobile ON mv_yearly_customer_cohorts(customer_mobile);
""", "Index on customer_mobile")

run("""
    CREATE INDEX idx_mv_yearly_cohort_year ON mv_yearly_customer_cohorts(purchase_year);
""", "Index on purchase_year")

run("""
    CREATE INDEX idx_mv_yearly_cohort_both ON mv_yearly_customer_cohorts(customer_mobile, purchase_year);
""", "Composite index")

# 3. mv_cohort_cross_year: pre-computed pivot (customer x year flags)
#    Makes "bought in 2024 but not 2026" a simple COUNT with WHERE clauses
run("""
    DROP MATERIALIZED VIEW IF EXISTS mv_cohort_cross_year CASCADE;
""", "Dropping mv_cohort_cross_year")

run("""
    CREATE MATERIALIZED VIEW mv_cohort_cross_year AS
    SELECT
        customer_mobile,
        MAX(CASE WHEN purchase_year = 2020 THEN 1 ELSE 0 END) AS in_2020,
        MAX(CASE WHEN purchase_year = 2021 THEN 1 ELSE 0 END) AS in_2021,
        MAX(CASE WHEN purchase_year = 2022 THEN 1 ELSE 0 END) AS in_2022,
        MAX(CASE WHEN purchase_year = 2023 THEN 1 ELSE 0 END) AS in_2023,
        MAX(CASE WHEN purchase_year = 2024 THEN 1 ELSE 0 END) AS in_2024,
        MAX(CASE WHEN purchase_year = 2025 THEN 1 ELSE 0 END) AS in_2025,
        MAX(CASE WHEN purchase_year = 2026 THEN 1 ELSE 0 END) AS in_2026
    FROM mv_yearly_customer_cohorts
    GROUP BY customer_mobile;
""", "Creating mv_cohort_cross_year pivot")

run("""
    CREATE INDEX idx_mv_cross_year_mobile ON mv_cohort_cross_year(customer_mobile);
    CREATE INDEX idx_mv_cross_year_2024 ON mv_cohort_cross_year(in_2024);
    CREATE INDEX idx_mv_cross_year_2026 ON mv_cohort_cross_year(in_2026);
""", "Indexes on mv_cohort_cross_year")

print("\n" + "=" * 60)
print("  Testing queries on the new MVs...")
print("=" * 60)

with connection.cursor() as cur:
    # Test 1: customers in 2024 not in 2026
    cur.execute("""
        SELECT COUNT(*) AS unique_customer_count
        FROM mv_cohort_cross_year
        WHERE in_2024 = 1 AND in_2026 = 0;
    """)
    row = cur.fetchone()
    print(f"\n  Q: Customers who purchased in 2024 but NOT in 2026")
    print(f"  A: {row[0]:,}")

    # Test 2: customers in 2024 and also 2026
    cur.execute("""
        SELECT COUNT(*) AS unique_customer_count
        FROM mv_cohort_cross_year
        WHERE in_2024 = 1 AND in_2026 = 1;
    """)
    row = cur.fetchone()
    print(f"\n  Q: Customers who purchased in 2024 AND also in 2026")
    print(f"  A: {row[0]:,}")

    # Test 3: total rows in base MV
    cur.execute("SELECT COUNT(*) FROM mv_yearly_customer_cohorts;")
    row = cur.fetchone()
    print(f"\n  Total rows in mv_yearly_customer_cohorts: {row[0]:,}")

    # Test 4: total unique customers in pivot
    cur.execute("SELECT COUNT(*) FROM mv_cohort_cross_year;")
    row = cur.fetchone()
    print(f"  Total unique customers in mv_cohort_cross_year: {row[0]:,}")

print("\n  [DONE] Materialized views created successfully!")
print("=" * 60)
