import os
import django
import time

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'myg_loyalty_dashboard.settings')
django.setup()

from django.db import connection

print("Creating optimized index for active years...")
start_time = time.time()

with connection.cursor() as cursor:
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_active_years_optimized ON mv_customer_active_years(active_year, mobile);")
    
print(f"Done in {time.time() - start_time:.2f} seconds.")
