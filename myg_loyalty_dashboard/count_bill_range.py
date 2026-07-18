import psycopg2

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

# Count of DISTINCT customers who have at least ONE bill between 40000 and 80000
cur.execute("""
    SELECT COUNT(DISTINCT "Customer Mobile") AS customer_count
    FROM sales_data
    WHERE "Total Value" BETWEEN 40000 AND 80000
      AND "Customer Mobile" ~ '^[0-9]{10}$';
""")
row = cur.fetchone()
print(f"Customers with at least one bill between Rs.40,000 and Rs.80,000: {row[0]}")

# Also show total number of such transactions
cur.execute("""
    SELECT COUNT(*) AS transaction_count
    FROM sales_data
    WHERE "Total Value" BETWEEN 40000 AND 80000
      AND "Customer Mobile" ~ '^[0-9]{10}$';
""")
row2 = cur.fetchone()
print(f"Total transactions in that range:                               {row2[0]}")

cur.close()
conn.close()
