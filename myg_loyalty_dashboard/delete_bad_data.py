import os, sys, django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'myg_loyalty_dashboard.settings')
django.setup()
from django.db import connection

cur = connection.cursor()

print("Deleting SMC/EI invoices...")
cur.execute("DELETE FROM sales_data WHERE \"Invoice Number\" ILIKE '%SMC/EI%';")
print(f"Deleted {cur.rowcount} rows.")

print("Deleting HEAD OFFICE and UG SMART CHOICE branches...")
cur.execute("DELETE FROM sales_data WHERE UPPER(TRIM(\"Branch\")) IN ('HEAD OFFICE', 'UG SMART CHOICE');")
print(f"Deleted {cur.rowcount} rows.")

connection.commit()
print("Deletion complete.")

print("Refreshing materialized views...")
import subprocess
subprocess.run([sys.executable, 'refresh_mvs.py'])

print("Clearing django cache...")
from django.core.cache import cache
cache.clear()
print("Done!")
