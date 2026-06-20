import os
import django
import time

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'myg_loyalty_dashboard.settings')
django.setup()

from django.db import connection

queries = [
    # 1. B-Tree on Date for range queries
    'CREATE INDEX IF NOT EXISTS idx_sales_date ON sales_data("Date");',
    
    # 2. B-Tree on Customer Mobile
    'CREATE INDEX IF NOT EXISTS idx_sales_mobile ON sales_data("Customer Mobile");',
    
    # 3. Trigram index on Branch for LIKE/ILIKE (already created earlier but ensure existence)
    'CREATE EXTENSION IF NOT EXISTS pg_trgm;',
    'CREATE INDEX IF NOT EXISTS idx_sales_branch_trgm ON sales_data USING gin (UPPER("Branch") gin_trgm_ops);',
    
    # 4. Composite index for Invoice/Date commonly joined
    'CREATE INDEX IF NOT EXISTS idx_sales_invoice_date ON sales_data("Invoice Number", "Date");',
    
    # 5. Active years optimization (already created earlier)
    'CREATE INDEX IF NOT EXISTS idx_active_years_optimized ON mv_customer_active_years(active_year, mobile);'
]

print("Running Database Index Optimization...")
with connection.cursor() as cursor:
    for q in queries:
        start = time.time()
        print(f"Executing: {q}")
        try:
            cursor.execute(q)
            print(f"  -> Done in {time.time() - start:.2f}s")
        except Exception as e:
            print(f"  -> Error: {e}")

print("Optimization Complete.")
