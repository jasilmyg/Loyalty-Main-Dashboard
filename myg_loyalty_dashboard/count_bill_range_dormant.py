import psycopg2
from datetime import date, timedelta

import os
conn = psycopg2.connect(
    host='db-postgresql-blr1-90397-do-user-3146770-0.e.db.ondigitalocean.com',
    port=25060,
    dbname='defaultdb',
    user='doadmin',
    password=os.environ.get('PGPASSWORD', ''),
    sslmode='require'
)
cur = conn.cursor()

cutoff_date = date.today() - timedelta(days=365)  # 2025-07-06
print(f"Cutoff date (1 year ago): {cutoff_date}")

# FAST approach: LEFT JOIN to find customers with 40K-80K bills
# who have NO visit in the last 1 year
cur.execute("""
    WITH bill_range_customers AS (
        SELECT DISTINCT "Customer Mobile"
        FROM sales_data
        WHERE "Total Value" BETWEEN 40000 AND 80000
          AND "Customer Mobile" ~ '^[0-9]{10}$'
    ),
    recent_visitors AS (
        SELECT DISTINCT "Customer Mobile"
        FROM sales_data
        WHERE parsed_date >= %s
          AND "Customer Mobile" ~ '^[0-9]{10}$'
    )
    SELECT COUNT(*) AS dormant_count
    FROM bill_range_customers b
    LEFT JOIN recent_visitors r ON b."Customer Mobile" = r."Customer Mobile"
    WHERE r."Customer Mobile" IS NULL;
""", (cutoff_date,))

row = cur.fetchone()
print(f"Dormant customers (bill 40K-80K, no visit in last 1 year): {row[0]}")

cur.close()
conn.close()
