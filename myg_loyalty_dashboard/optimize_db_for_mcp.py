import os
import sys
import django

sys.path.insert(0, '.')
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'myg_loyalty_dashboard.settings')
django.setup()

from django.db import connection

print("Starting database optimization...")

sql_index = """
CREATE INDEX IF NOT EXISTS idx_sales_mobile_date_val 
ON sales_data ("Customer Mobile", parsed_date, "Total Value");
"""

sql_mv = """
CREATE MATERIALIZED VIEW IF NOT EXISTS mv_customer_lifetime_summary AS
SELECT 
    "Customer Mobile" as customer_mobile,
    SUM("Total Value") as total_spend,
    MIN(parsed_date) as first_visit_date,
    MAX(parsed_date) as last_visit_date,
    COUNT(*) as total_visits
FROM sales_data
WHERE "Customer Mobile" IS NOT NULL AND "Customer Mobile" != ''
GROUP BY "Customer Mobile";
"""

sql_mv_indexes = """
CREATE UNIQUE INDEX IF NOT EXISTS idx_mv_cust_mobile ON mv_customer_lifetime_summary(customer_mobile);
CREATE INDEX IF NOT EXISTS idx_mv_cust_spend ON mv_customer_lifetime_summary(total_spend);
CREATE INDEX IF NOT EXISTS idx_mv_cust_first ON mv_customer_lifetime_summary(first_visit_date);
CREATE INDEX IF NOT EXISTS idx_mv_cust_last ON mv_customer_lifetime_summary(last_visit_date);
"""

try:
    with connection.cursor() as cursor:
        print("1. Adding composite index to sales_data (this may take a minute)...")
        cursor.execute(sql_index)
        
        print("2. Creating materialized view 'mv_customer_lifetime_summary' (this may take a few minutes)...")
        cursor.execute(sql_mv)
        
        print("3. Indexing the materialized view...")
        cursor.execute(sql_mv_indexes)
        
    print("Database optimization complete successfully!")
except Exception as e:
    print(f"Error during optimization: {e}")
