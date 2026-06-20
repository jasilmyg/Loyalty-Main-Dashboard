import os, sys, django
import pandas as pd
from sqlalchemy import create_engine

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'myg_loyalty_dashboard.settings')
django.setup()

from django.conf import settings

file_path = r"C:\Users\jasil_myg\Desktop\myG Loyalty Main Dashboard\myg_loyalty_dashboard\DSR MAY 2026 PART 3.xlsx"

print(f"Reading {file_path}...")
df = pd.read_excel(file_path, engine='calamine')
original_count = len(df)
print(f"Loaded {original_count} rows.")

final_count = len(df)
filtered_out = 0
print(f"Filtered out {filtered_out} rows. Proceeding with {final_count} rows.")

db = settings.DATABASES['default']
conn_str = f"postgresql://{db['USER']}:{db['PASSWORD']}@{db['HOST']}:{db['PORT']}/{db['NAME']}?sslmode=require"
engine = create_engine(conn_str)

print("Appending to PostgreSQL (this may take a minute)...")
# chunksize to avoid memory/network limits
df.to_sql('sales_data', con=engine, if_exists='append', index=False, chunksize=5000)
print("Data inserted successfully!")

print("Refreshing materialized views...")
import subprocess
subprocess.run([sys.executable, 'refresh_mvs.py'])

print("Clearing django cache...")
from django.core.cache import cache
cache.clear()

print("Done!")
