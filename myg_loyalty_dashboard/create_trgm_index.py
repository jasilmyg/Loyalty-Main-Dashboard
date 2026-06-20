import os
import django
import time

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'myg_loyalty_dashboard.settings')
django.setup()

from django.db import connection

print("Creating trigram extension and index for ultra-fast branch searches...")
start_time = time.time()

with connection.cursor() as cursor:
    cursor.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm;")
    # Check if index exists to avoid errors
    cursor.execute("SELECT 1 FROM pg_class WHERE relname = 'idx_sales_branch_trgm';")
    if not cursor.fetchone():
        cursor.execute('CREATE INDEX idx_sales_branch_trgm ON sales_data USING gin (UPPER("Branch") gin_trgm_ops);')
        print("Index created successfully!")
    else:
        print("Index already exists.")

print(f"Done in {time.time() - start_time:.2f} seconds.")
