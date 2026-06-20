import os, sys, django
import pandas as pd
from sqlalchemy import create_engine

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'myg_loyalty_dashboard.settings')
django.setup()

from django.conf import settings
from django.db import connection

# --- STEP 1: Check existing DB columns -----------------------------------------
print("=" * 60)
print("STEP 1: Checking existing DB columns...")
with connection.cursor() as cur:
    cur.execute("""
        SELECT column_name FROM information_schema.columns
        WHERE table_name='sales_data'
        ORDER BY ordinal_position;
    """)
    db_cols = [r[0] for r in cur.fetchall()]
    print(f"DB columns ({len(db_cols)}): {db_cols}")

    cur.execute("SELECT COUNT(*) FROM sales_data;")
    count_before = cur.fetchone()[0]
    print(f"Total rows BEFORE any changes: {count_before}")

# --- STEP 2: Read Excel file ----------------------------------------------------
print("\n" + "=" * 60)
print("STEP 2: Reading Excel file...")
file_path = r"C:\Users\jasil_myg\Desktop\myG Loyalty Main Dashboard\myg_loyalty_dashboard\DSR JUN 2026 PART 2.xlsx"
df = pd.read_excel(file_path, engine='calamine')
original_count = len(df)
print(f"Loaded {original_count} rows from Excel.")
print(f"Excel columns: {df.columns.tolist()}")

# --- STEP 3: Filter out SMC/EI and HEAD OFFICE / UG SMART CHOICE --------------
print("\n" + "=" * 60)
print("STEP 3: Filtering out bad data from Excel before insert...")

smc_mask = pd.Series([False] * len(df))
branch_mask = pd.Series([False] * len(df))

if 'Invoice Number' in df.columns:
    smc_mask = df['Invoice Number'].astype(str).str.contains('SMC/EI', na=False, case=False)
    print(f"  -> SMC/EI rows in Excel: {smc_mask.sum()}")

if 'Branch' in df.columns:
    branch_mask = df['Branch'].astype(str).str.upper().str.strip().isin(['HEAD OFFICE', 'UG SMART CHOICE'])
    print(f"  -> HEAD OFFICE / UG SMART CHOICE rows in Excel: {branch_mask.sum()}")

bad_mask = smc_mask | branch_mask
df_clean = df[~bad_mask].copy()
print(f"  -> Excluded {bad_mask.sum()} bad rows from Excel.")
print(f"  -> Clean rows to insert: {len(df_clean)}")

# --- STEP 4: Align columns with DB ---------------------------------------------
print("\n" + "=" * 60)
print("STEP 4: Aligning Excel columns with DB schema...")
excel_cols = df_clean.columns.tolist()

# Keep only columns that exist in DB
matched_cols = [c for c in excel_cols if c in db_cols]
extra_cols   = [c for c in excel_cols if c not in db_cols]
missing_cols = [c for c in db_cols if c not in excel_cols]

print(f"  -> Matched columns: {matched_cols}")
if extra_cols:
    print(f"  -> Extra cols in Excel (will be DROPPED): {extra_cols}")
if missing_cols:
    print(f"  -> Missing cols in Excel (not in Excel, skipped): {missing_cols}")

df_clean = df_clean[matched_cols]

# --- STEP 5: Check for duplicate Invoice Numbers already in DB ------------------
print("\n" + "=" * 60)
print("STEP 5: Checking for duplicates...")

if 'Invoice Number' in matched_cols:
    excel_invoices = df_clean['Invoice Number'].dropna().astype(str).tolist()
    if excel_invoices:
        with connection.cursor() as cur:
            placeholders = ','.join(['%s'] * len(excel_invoices))
            cur.execute(f'SELECT "Invoice Number" FROM sales_data WHERE "Invoice Number" IN ({placeholders})', excel_invoices)
            existing = set(r[0] for r in cur.fetchall())
        dup_mask = df_clean['Invoice Number'].astype(str).isin(existing)
        dup_count = dup_mask.sum()
        print(f"  -> {dup_count} rows already exist in DB (duplicate Invoice Numbers).")
        df_clean = df_clean[~dup_mask]
        print(f"  -> Rows to insert after dedup: {len(df_clean)}")
    else:
        print("  -> No invoice numbers to check.")
else:
    print("  -> No 'Invoice Number' column — skipping dedup check.")

# --- STEP 6: Parse date column --------------------------------------------------
if 'Date' in df_clean.columns:
    df_clean['Date'] = pd.to_datetime(df_clean['Date'], dayfirst=True, errors='coerce')

# --- STEP 7: Insert into PostgreSQL --------------------------------------------
print("\n" + "=" * 60)
print(f"STEP 7: Inserting {len(df_clean)} clean rows into PostgreSQL...")
db = settings.DATABASES['default']
conn_str = f"postgresql://{db['USER']}:{db['PASSWORD']}@{db['HOST']}:{db['PORT']}/{db['NAME']}?sslmode=require"
engine = create_engine(conn_str)

if len(df_clean) > 0:
    df_clean.to_sql('sales_data', con=engine, if_exists='append', index=False, chunksize=3000)
    print("  + Data inserted successfully!")
else:
    print("  ! No new rows to insert (all duplicates or empty after filtering).")

# --- STEP 8: Delete SMC/EI and bad branches from ENTIRE DB ---------------------
print("\n" + "=" * 60)
print("STEP 8: Removing SMC/EI and HEAD OFFICE / UG SMART CHOICE from ENTIRE DB...")

with connection.cursor() as cur:
    cur.execute('DELETE FROM sales_data WHERE "Invoice Number" ILIKE \'%SMC/EI%\';')
    smc_deleted = cur.rowcount
    print(f"  -> Deleted {smc_deleted} rows with SMC/EI in Invoice Number.")

    cur.execute("DELETE FROM sales_data WHERE UPPER(TRIM(\"Branch\")) IN ('HEAD OFFICE', 'UG SMART CHOICE');")
    branch_deleted = cur.rowcount
    print(f"  -> Deleted {branch_deleted} rows with HEAD OFFICE / UG SMART CHOICE in Branch.")

connection.commit()
print("  + Deletions committed.")

# --- STEP 9: Verify final count ------------------------------------------------
print("\n" + "=" * 60)
print("STEP 9: Final verification...")
with connection.cursor() as cur:
    cur.execute("SELECT COUNT(*) FROM sales_data;")
    count_after = cur.fetchone()[0]
    print(f"  -> Total rows BEFORE: {count_before}")
    print(f"  -> Total rows AFTER:  {count_after}")
    print(f"  -> Net change: {count_after - count_before:+d}")

    cur.execute("SELECT MAX(\"Date\") FROM sales_data;")
    last_date = cur.fetchone()[0]
    print(f"  -> Last date in DB: {last_date}")

# --- STEP 10: Refresh materialized views ---------------------------------------
print("\n" + "=" * 60)
print("STEP 10: Refreshing materialized views...")
import subprocess
result = subprocess.run([sys.executable, 'refresh_mvs.py'], capture_output=True, text=True)
print(result.stdout)
if result.returncode != 0:
    print("  ⚠ Warning during MV refresh:", result.stderr)

print("Clearing Django cache...")
from django.core.cache import cache
cache.clear()

print("\n" + "=" * 60)
print("✅ ALL DONE!")
print(f"   Inserted: {len(df_clean)} new rows")
print(f"   Deleted (SMC/EI): {smc_deleted}")
print(f"   Deleted (Bad Branch): {branch_deleted}")
print("=" * 60)
