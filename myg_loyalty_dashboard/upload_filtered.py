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

# Select ONLY the rows that were PREVIOUSLY filtered out, so we don't duplicate the valid rows that were already inserted.
smc_mask = df['Invoice Number'].astype(str).str.contains('SMC/EI', na=False, case=False) if 'Invoice Number' in df.columns else False
branch_mask = df['Branch'].astype(str).str.upper().str.strip().isin(['HEAD OFFICE', 'UG SMART CHOICE']) if 'Branch' in df.columns else False

# Keep ONLY the rows that match the filters (which we previously deleted)
df_to_insert = df[smc_mask | branch_mask]

final_count = len(df_to_insert)
print(f"Found {final_count} previously filtered rows (SMC/EI or Head Office).")

if final_count > 0:
    db = settings.DATABASES['default']
    conn_str = f"postgresql://{db['USER']}:{db['PASSWORD']}@{db['HOST']}:{db['PORT']}/{db['NAME']}?sslmode=require"
    engine = create_engine(conn_str)

    print("Appending missing filtered rows to PostgreSQL...")
    df_to_insert.to_sql('sales_data', con=engine, if_exists='append', index=False, chunksize=5000)
    print("Data inserted successfully!")
else:
    print("No missing rows to insert.")
