import os, sys, django
import pandas as pd
from sqlalchemy import create_engine

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'myg_loyalty_dashboard.settings')
print("Setting up Django...", flush=True)
django.setup()
print("Django OK", flush=True)

from django.conf import settings
from django.db import connection

# --- STEP 1: Check existing DB columns -----------------------------------------
print("=" * 60, flush=True)
print("STEP 1: Checking existing DB columns...", flush=True)
with connection.cursor() as cur:
    cur.execute("""
        SELECT column_name FROM information_schema.columns
        WHERE table_name='sales_data'
        ORDER BY ordinal_position;
    """)
    db_cols = [r[0] for r in cur.fetchall()]
    print(f"DB columns ({len(db_cols)}): {db_cols}", flush=True)

    cur.execute("SELECT COUNT(*) FROM sales_data;")
    count_before = cur.fetchone()[0]
    print(f"Total rows BEFORE: {count_before:,}", flush=True)

# --- STEP 2: Read Excel file ----------------------------------------------------
print("\n" + "=" * 60, flush=True)
print("STEP 2: Reading Excel file...", flush=True)
file_path = r"C:\Users\jasil_myg\Desktop\myG Loyalty Main Dashboard\project_folder\DSR JULY 2026 PART 1.xlsx"
print(f"File: {file_path}", flush=True)
df = pd.read_excel(file_path, engine='calamine')
original_count = len(df)
print(f"Loaded {original_count:,} rows from Excel.", flush=True)
print(f"Excel columns: {df.columns.tolist()}", flush=True)

# --- STEP 3: Filter out SMC/EI and HEAD OFFICE / UG SMART CHOICE --------------
print("\n" + "=" * 60, flush=True)
print("STEP 3: Filtering bad data...", flush=True)

smc_mask = pd.Series([False] * len(df))
branch_mask = pd.Series([False] * len(df))

if 'Invoice Number' in df.columns:
    smc_mask = df['Invoice Number'].astype(str).str.contains('SMC/EI', na=False, case=False)
    print(f"  -> SMC/EI rows: {smc_mask.sum()}", flush=True)

if 'Branch' in df.columns:
    branch_mask = df['Branch'].astype(str).str.upper().str.strip().isin(['HEAD OFFICE', 'UG SMART CHOICE'])
    print(f"  -> HEAD OFFICE / UG SMART CHOICE rows: {branch_mask.sum()}", flush=True)

bad_mask = smc_mask | branch_mask
df_clean = df[~bad_mask].copy()
print(f"  -> Excluded {bad_mask.sum()} bad rows", flush=True)
print(f"  -> Clean rows to insert: {len(df_clean):,}", flush=True)

# --- STEP 4: Align columns with DB ---------------------------------------------
print("\n" + "=" * 60, flush=True)
print("STEP 4: Aligning columns...", flush=True)
excel_cols = df_clean.columns.tolist()
matched_cols = [c for c in excel_cols if c in db_cols]
extra_cols   = [c for c in excel_cols if c not in db_cols]
print(f"  -> Matched: {matched_cols}", flush=True)
if extra_cols:
    print(f"  -> Dropping extra: {extra_cols}", flush=True)
df_clean = df_clean[matched_cols]

# --- STEP 5: Dedup check -------------------------------------------------------
print("\n" + "=" * 60, flush=True)
print("STEP 5: Dedup check...", flush=True)
dup_count = 0
if 'Invoice Number' in matched_cols:
    excel_invoices = df_clean['Invoice Number'].dropna().astype(str).tolist()
    print(f"  -> Checking {len(excel_invoices):,} invoice numbers...", flush=True)
    if excel_invoices:
        BATCH = 5000
        existing = set()
        for i in range(0, len(excel_invoices), BATCH):
            batch = excel_invoices[i:i+BATCH]
            with connection.cursor() as cur:
                placeholders = ','.join(['%s'] * len(batch))
                cur.execute(f'SELECT "Invoice Number" FROM sales_data WHERE "Invoice Number" IN ({placeholders})', batch)
                existing.update(r[0] for r in cur.fetchall())
        dup_mask = df_clean['Invoice Number'].astype(str).isin(existing)
        dup_count = dup_mask.sum()
        print(f"  -> {dup_count} duplicates found.", flush=True)
        df_clean = df_clean[~dup_mask]
        print(f"  -> Rows after dedup: {len(df_clean):,}", flush=True)

# --- STEP 6: Parse dates -------------------------------------------------------
if 'Date' in df_clean.columns:
    df_clean['Date'] = pd.to_datetime(df_clean['Date'], dayfirst=True, errors='coerce')

# --- STEP 7: Insert into PostgreSQL --------------------------------------------
print("\n" + "=" * 60, flush=True)
print(f"STEP 7: Inserting {len(df_clean):,} rows into PostgreSQL...", flush=True)
db = settings.DATABASES['default']
conn_str = f"postgresql://{db['USER']}:{db['PASSWORD']}@{db['HOST']}:{db['PORT']}/{db['NAME']}?sslmode=require"
engine = create_engine(conn_str)

if len(df_clean) > 0:
    df_clean.to_sql('sales_data', con=engine, if_exists='append', index=False, chunksize=3000)
    print("  + Data inserted successfully!", flush=True)
else:
    print("  ! No new rows to insert.", flush=True)

# --- STEP 8: Clean up DB -------------------------------------------------------
print("\n" + "=" * 60, flush=True)
print("STEP 8: Removing bad data from entire DB...", flush=True)
with connection.cursor() as cur:
    cur.execute('DELETE FROM sales_data WHERE "Invoice Number" ILIKE \'%SMC/EI%\';')
    smc_deleted = cur.rowcount
    print(f"  -> Deleted SMC/EI rows: {smc_deleted}", flush=True)

    cur.execute("DELETE FROM sales_data WHERE UPPER(TRIM(\"Branch\")) IN ('HEAD OFFICE', 'UG SMART CHOICE');")
    branch_deleted = cur.rowcount
    print(f"  -> Deleted bad branch rows: {branch_deleted}", flush=True)

    print("  -> Updating parsed_date for new rows...", flush=True)
    cur.execute('UPDATE sales_data SET parsed_date = CAST("Date" AS DATE) WHERE parsed_date IS NULL AND "Date" IS NOT NULL;')
    updated_dates = cur.rowcount
    print(f"  -> Updated parsed_date for {updated_dates} rows.", flush=True)

connection.commit()
print("  + Committed.", flush=True)

# --- STEP 9: Verify count -------------------------------------------------------
print("\n" + "=" * 60, flush=True)
with connection.cursor() as cur:
    cur.execute("SELECT COUNT(*) FROM sales_data;")
    count_after = cur.fetchone()[0]
    cur.execute("SELECT MAX(\"Date\") FROM sales_data;")
    last_date = cur.fetchone()[0]

print(f"  -> Rows BEFORE: {count_before:,}", flush=True)
print(f"  -> Rows AFTER:  {count_after:,}", flush=True)
print(f"  -> Net change:  {count_after - count_before:+,}", flush=True)
print(f"  -> Latest date: {last_date}", flush=True)

# --- STEP 10: Refresh materialized views ----------------------------------------
print("\n" + "=" * 60, flush=True)
print("STEP 10: Refreshing all materialized views...", flush=True)
import subprocess
result = subprocess.run([sys.executable, 'refresh_mvs.py'], capture_output=True, text=True, timeout=900)
print(result.stdout, flush=True)
if result.returncode != 0:
    print("  WARNING during MV refresh:", result.stderr, flush=True)

# --- Clear cache ----------------------------------------------------------------
print("Clearing Django cache...", flush=True)
from django.core.cache import cache
cache.clear()

print("\n" + "=" * 60, flush=True)
print("ALL DONE!", flush=True)
print(f"  Inserted:            {len(df_clean):,} new rows", flush=True)
print(f"  Deleted (SMC/EI):    {smc_deleted}", flush=True)
print(f"  Deleted (BadBranch): {branch_deleted}", flush=True)
print("=" * 60, flush=True)
