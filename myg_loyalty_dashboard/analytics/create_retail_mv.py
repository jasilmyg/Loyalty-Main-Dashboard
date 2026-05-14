import psycopg2, os
conn = psycopg2.connect(
    host=os.environ.get('PGHOST', 'localhost'),
    port=int(os.environ.get('PGPORT', 25060)),
    dbname=os.environ.get('PGDATABASE', 'defaultdb'),
    user=os.environ.get('PGUSER', 'doadmin'),
    password=os.environ.get('PGPASSWORD', ''),
    sslmode='require'
)
conn.autocommit = True
cur = conn.cursor()

FAST_FIRST = """(CASE
    WHEN SUBSTRING(first_visit::text, 5, 1) = '-' THEN TO_DATE(SUBSTRING(first_visit::text, 1, 10), 'YYYY-MM-DD')
    WHEN SUBSTRING(first_visit::text, 3, 1) = '-' THEN TO_DATE(first_visit::text, 'DD-MM-YYYY')
    ELSE NULL
END)"""

FAST_DATE = """(CASE
    WHEN SUBSTRING("Date"::text, 5, 1) = '-' THEN TO_DATE(SUBSTRING("Date"::text, 1, 10), 'YYYY-MM-DD')
    WHEN SUBSTRING("Date"::text, 3, 1) = '-' THEN TO_DATE("Date"::text, 'DD-MM-YYYY')
    ELSE NULL
END)"""

print("Creating mv_retail_loyalty with fast substring parsing...")
cur.execute("DROP MATERIALIZED VIEW IF EXISTS mv_retail_loyalty;")
cur.execute(f"""
CREATE MATERIALIZED VIEW mv_retail_loyalty AS
WITH customer_first_visit AS (
    SELECT mobile,
        DATE_TRUNC('month', {FAST_FIRST}) AS first_period
    FROM mv_customer_summary
),
filtered_sales AS (
    SELECT "Customer Mobile" AS mobile,
        "Date"::text AS visit_date,
        DATE_TRUNC('month', {FAST_DATE}) AS period_date
    FROM sales_data
    WHERE "Customer Mobile"::text ~ '^[0-9]{{10}}$'
)
SELECT
    TO_CHAR(s.period_date, 'YYYY-MM') AS period_id,
    s.period_date AS order_date,
    COUNT(DISTINCT s.mobile) AS total_members,
    COUNT(DISTINCT s.visit_date) AS total_visits,
    COUNT(DISTINCT CASE WHEN s.period_date = f.first_period THEN s.mobile END) AS new_members,
    COUNT(DISTINCT CASE WHEN s.period_date > f.first_period THEN s.mobile END) AS repeat_members
FROM filtered_sales s
JOIN customer_first_visit f ON s.mobile = f.mobile
GROUP BY s.period_date, TO_CHAR(s.period_date, 'YYYY-MM');
""")
cur.execute("CREATE UNIQUE INDEX idx_mv_retail_loyalty ON mv_retail_loyalty (period_id);")
print("Materialized view mv_retail_loyalty created!")
