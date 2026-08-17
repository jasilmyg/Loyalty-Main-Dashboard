"""
load_aug_15_16_azure.py
========================
Downloads Aug 15 and 16 CSV files from Azure Blob and loads into:
  invoice_wise_sales_report/ -> azure_invoice_report
  item_wise_sales_report/    -> azure_sales_report
"""

import os, sys, io
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'myg_loyalty_dashboard.settings')
import django; django.setup()

import pandas as pd
from azure.storage.blob import ContainerClient
from analytics.clickhouse_service import get_ch_client

# ── Config ────────────────────────────────────────────────────
SAS_URL = "https://stmygoalposreports.blob.core.windows.net/sales-reports?sp=racwl&st=2026-08-11T03:51:43Z&se=2026-12-31T18:29:43Z&spr=https&sv=2026-02-06&sr=c&sig=b5URyZCBQKQU3rwuqxY5z2vqyKNrsDKIPABLQ%2FFyywQ%3D"

TARGET_DATES = ['15-08-2026', '16-08-2026']

cc = ContainerClient.from_container_url(SAS_URL)
ch = get_ch_client()

# ── Helpers ───────────────────────────────────────────────────
def safe_str(s):   return s.fillna('').astype(str).str.strip().replace('nan','').replace('None','')
def safe_int(s):   return pd.to_numeric(s, errors='coerce').fillna(0).astype(int)
def safe_float(s): return pd.to_numeric(s, errors='coerce').fillna(0.0).astype(float)

def download_blob(name):
    return cc.get_blob_client(name).download_blob().readall()

# ── Get schema from ClickHouse ────────────────────────────────
print("Fetching azure_invoice_report schema...")
inv_schema = {r[0]: r[1] for r in ch.query('DESCRIBE TABLE azure_invoice_report').result_rows}
print(f"  Columns: {list(inv_schema.keys())}")

print("Fetching azure_sales_report schema...")
sales_schema = {r[0]: r[1] for r in ch.query('DESCRIBE TABLE azure_sales_report').result_rows}
print(f"  Columns: {list(sales_schema.keys())}")

# ── List blobs for target dates ───────────────────────────────
all_blobs = list(cc.list_blobs())
inv_files   = sorted([b.name for b in all_blobs
                      if b.name.startswith('invoice_wise_sales_report/')
                      and any(d in b.name for d in TARGET_DATES)])
sales_files = sorted([b.name for b in all_blobs
                      if b.name.startswith('item_wise_sales_report/')
                      and any(d in b.name for d in TARGET_DATES)])

print(f"\nFound {len(inv_files)} invoice files and {len(sales_files)} sales files for Aug 15-16")

# ── Col maps matching azure table column names ────────────────
INV_COL_MAP = {
    'Date': 'date', 'Time': 'time',
    'Invoice No': 'invoice_no', 'Invoice No.': 'invoice_no',
    'Branch': 'branch', 'RBM': 'rbm', 'BDM': 'bdm',
    # customer columns — actual CH names differ from old script
    'Customer Bill To No': 'customer_mobile',
    'Customer Bill To No.': 'customer_mobile',
    'Customer Mobile': 'customer_mobile',
    'Customer Bill To Pincode': 'customer_pincode',
    'Customer Bill To GSTIN': 'customer_gstin',
    'Customer Type': 'customer_type',
    'Sales Staff Code': 'sales_staff_code',
    'Billing Staff Code': 'billing_staff_code',
    'Invoice Total': 'invoice_total',
    'Discount': 'discount', 'Buyback': 'buyback',
    'Deductions (Indirect)': 'deductions',
    'Exchange': 'exchange',
    'Financier Code': 'financier_code',
    'Financier Name': 'financier_name',
    'Scheme': 'scheme', 'Loan Amount': 'loan_amount',
}

SALES_COL_MAP = {
    'Date': 'date', 'Invoice No': 'invoice_no', 'Invoice No.': 'invoice_no',
    'Branch': 'branch', 'Item Code': 'item_code',
    'IMEI/Batch': 'imei_batch', 'IMEI/Batch No': 'imei_batch', 'IMEI/Batch No.': 'imei_batch',
    'QTY': 'qty', 'Qty': 'qty', 'Quantity': 'qty',
    'MOP': 'mop', 'Discount': 'discount', 'Buyback': 'buyback',
    'Sold Price': 'sold_price', 'Taxable': 'taxable',
}

def safe_datetime(s):
    """Parse date strings like '2026-08-15' or '15-08-2026' to Python datetime."""
    parsed = pd.to_datetime(s, dayfirst=True, errors='coerce')
    # Fill NaT with epoch
    parsed = parsed.fillna(pd.Timestamp('1970-01-01'))
    return parsed.dt.to_pydatetime()

def map_df_to_table(df, col_map, table_schema):
    """Rename columns and cast to match ClickHouse schema exactly."""
    df = df.rename(columns={k: v for k, v in col_map.items() if k in df.columns})
    df.columns = [c.strip().lower().replace(' ', '_') for c in df.columns]

    ch_cols = list(table_schema.keys())
    result = {}
    for col, dtype in table_schema.items():
        if col in df.columns:
            if col == 'date':
                result[col] = list(safe_datetime(df[col]))
            elif 'Int' in dtype or 'UInt' in dtype:
                result[col] = safe_int(df[col])
            elif 'Float' in dtype or 'Decimal' in dtype:
                result[col] = safe_float(df[col])
            elif 'DateTime' in dtype:
                result[col] = list(safe_datetime(df[col]))
            else:
                result[col] = safe_str(df[col])
        else:
            if col == 'date' or 'DateTime' in dtype:
                result[col] = [pd.Timestamp('1970-01-01').to_pydatetime()] * len(df)
            elif 'Int' in dtype or 'UInt' in dtype:
                result[col] = 0
            elif 'Float' in dtype or 'Decimal' in dtype:
                result[col] = 0.0
            else:
                result[col] = ''

    return pd.DataFrame(result)[ch_cols]

# ── Load invoice_wise -> azure_invoice_report ─────────────────
print(f"\n{'='*60}")
print("  Loading invoice files -> azure_invoice_report")
print(f"{'='*60}")
total_inv = 0
for blob_name in inv_files:
    short = blob_name.split('/')[-1]
    try:
        raw = download_blob(blob_name)
        df  = pd.read_csv(io.BytesIO(raw))
        df  = map_df_to_table(df, INV_COL_MAP, inv_schema)
        df  = df[df['invoice_no'].astype(str).str.strip() != '']
        if len(df) == 0:
            print(f"  {short} -> EMPTY, skipping")
            continue
        rows = df.values.tolist()
        ch.insert('azure_invoice_report', rows, column_names=list(inv_schema.keys()))
        total_inv += len(df)
        print(f"  {short} -> +{len(df):,} rows  (total: {total_inv:,})")
    except Exception as e:
        print(f"  {short} -> ERROR: {e}")

# ── Load item_wise -> azure_sales_report ──────────────────────
print(f"\n{'='*60}")
print("  Loading item_wise files -> azure_sales_report")
print(f"{'='*60}")
total_sales = 0
for blob_name in sales_files:
    short = blob_name.split('/')[-1]
    try:
        raw = download_blob(blob_name)
        df  = pd.read_csv(io.BytesIO(raw))
        df  = map_df_to_table(df, SALES_COL_MAP, sales_schema)
        df  = df[df['invoice_no'].astype(str).str.strip() != '']
        if len(df) == 0:
            print(f"  {short} -> EMPTY, skipping")
            continue
        rows = df.values.tolist()
        ch.insert('azure_sales_report', rows, column_names=list(sales_schema.keys()))
        total_sales += len(df)
        print(f"  {short} -> +{len(df):,} rows  (total: {total_sales:,})")
    except Exception as e:
        print(f"  {short} -> ERROR: {e}")

# ── Verify ────────────────────────────────────────────────────
print(f"\n{'='*60}")
print("  VERIFICATION")
print(f"{'='*60}")
r1 = ch.query("SELECT count() FROM azure_invoice_report WHERE date IN ('2026-08-15','2026-08-16')").result_rows[0][0]
r2 = ch.query("SELECT count() FROM azure_sales_report   WHERE date IN ('2026-08-15','2026-08-16')").result_rows[0][0]
t1 = ch.query("SELECT count() FROM azure_invoice_report").result_rows[0][0]
t2 = ch.query("SELECT count() FROM azure_sales_report").result_rows[0][0]
print(f"  azure_invoice_report  Aug 15-16 rows: {r1:,}")
print(f"  azure_sales_report    Aug 15-16 rows: {r2:,}")
print(f"  azure_invoice_report  Total rows    : {t1:,}")
print(f"  azure_sales_report    Total rows    : {t2:,}")
print(f"\n  Invoice rows inserted this run: {total_inv:,}")
print(f"  Sales rows inserted this run  : {total_sales:,}")
print("\nDone!")
