"""
fix_aug28_timezone.py
======================
The 28-Aug-2026 data was inserted with pandas datetime which Python
treated as IST → ClickHouse stored as 2026-08-27 18:30:00 UTC.

Fix:
  1. Delete rows where date = '2026-08-27 18:30:00' (these are the wrong 28-Aug rows)
  2. Re-insert with date explicitly set to 2026-08-28 00:00:00 UTC
"""
import os, sys, io, django
import pandas as pd
from datetime import datetime

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'myg_loyalty_dashboard.settings')
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
django.setup()

from analytics.clickhouse_service import get_ch_client
from azure.storage.blob import ContainerClient

ACCOUNT_NAME   = "stmygoalposreports"
CONTAINER_NAME = "sales-reports"
SAS_TOKEN      = "sp=racwl&st=2026-08-11T03:51:43Z&se=2026-12-31T18:29:43Z&spr=https&sv=2026-02-06&sr=c&sig=b5URyZCBQKQU3rwuqxY5z2vqyKNrsDKIPABLQ%2FFyywQ%3D"
container_url  = f"https://{ACCOUNT_NAME}.blob.core.windows.net/{CONTAINER_NAME}?{SAS_TOKEN}"
container_client = ContainerClient.from_container_url(container_url)
client = get_ch_client()

ITEM_BLOB = "item_wise_sales_report/item_wise_sales_report_29-08-2026_03_00_02_444257.csv"
INV_BLOB  = "invoice_wise_sales_report/invoice_wise_sales_report_29-08-2026_03_00_03_919584.csv"
SALES_TABLE   = "azure_sales_report"
INVOICE_TABLE = "azure_invoice_report"

# The CORRECT UTC datetime for 2026-08-28 00:00:00 (date only, no timezone shift)
CORRECT_DATE = datetime(2026, 8, 28, 0, 0, 0)  # UTC midnight = 28 Aug in ClickHouse

def safe_str(s):   return s.fillna('').astype(str).str.strip().replace({'nan': '', 'None': ''})
def safe_float(s): return pd.to_numeric(s, errors='coerce').fillna(0.0).astype(float)

print("=" * 60)
print("  Fixing Aug-28 timezone issue in ClickHouse")
print("=" * 60)

# ── Step 1: Delete the wrongly-stored rows ─────────────────────────────────
print("\n[1] Deleting wrongly-stored rows (date = 2026-08-27 18:30:00)...")
before_s = client.query(f"SELECT count() FROM {SALES_TABLE}").result_rows[0][0]
before_i = client.query(f"SELECT count() FROM {INVOICE_TABLE}").result_rows[0][0]

client.command(f"ALTER TABLE {SALES_TABLE} DELETE WHERE date = toDateTime('2026-08-27 18:30:00')")
client.command(f"ALTER TABLE {INVOICE_TABLE} DELETE WHERE date = toDateTime('2026-08-27 18:30:00')")

# Wait for mutations to apply
import time
time.sleep(5)

after_del_s = client.query(f"SELECT count() FROM {SALES_TABLE}").result_rows[0][0]
after_del_i = client.query(f"SELECT count() FROM {INVOICE_TABLE}").result_rows[0][0]
print(f"    azure_sales_report:   {before_s:,} -> {after_del_s:,} (deleted {before_s - after_del_s:,} rows)")
print(f"    azure_invoice_report: {before_i:,} -> {after_del_i:,} (deleted {before_i - after_del_i:,} rows)")

# ── Step 2: Re-download blobs and insert with correct UTC date ─────────────
SALES_COLS = ['date', 'invoice_no', 'branch', 'item_code', 'imei_batch',
              'qty', 'mop', 'discount', 'buyback', 'sold_price', 'taxable']

print("\n[2] Re-downloading and inserting azure_sales_report with correct date...")
raw = container_client.get_blob_client(ITEM_BLOB).download_blob().readall()
df  = pd.read_csv(io.BytesIO(raw))
rename = {
    'Date': 'date', 'Invoice No': 'invoice_no', 'Invoice No.': 'invoice_no',
    'Branch': 'branch', 'Item Code': 'item_code',
    'IMEI/Batch': 'imei_batch', 'IMEI/Batch No': 'imei_batch',
    'Qty': 'qty', 'QTY': 'qty', 'Quantity': 'qty',
    'MOP': 'mop', 'Discount': 'discount', 'Buyback': 'buyback',
    'Sold Price': 'sold_price', 'Taxable': 'taxable'
}
df.rename(columns={k: v for k, v in rename.items() if k in df.columns}, inplace=True)
df['invoice_no'] = safe_str(df['invoice_no'])
df['branch']     = safe_str(df['branch'])
df['item_code']  = safe_str(df['item_code'])
df['imei_batch'] = df['imei_batch'].fillna('').astype(str).str.strip()
df['qty']        = safe_float(df['qty'])
df['mop']        = safe_float(df['mop'])
df['discount']   = safe_float(df['discount'])
df['buyback']    = safe_float(df['buyback'])
df['sold_price'] = safe_float(df['sold_price'])
df['taxable']    = safe_float(df['taxable'])
df = df[df['invoice_no'].str.strip() != '']

# Force all rows to use CORRECT_DATE (2026-08-28 00:00:00 UTC)
rows = [(CORRECT_DATE, r.invoice_no, r.branch, r.item_code, r.imei_batch,
         r.qty, r.mop, r.discount, r.buyback, r.sold_price, r.taxable)
        for r in df.itertuples(index=False)]
client.insert(SALES_TABLE, rows, column_names=SALES_COLS)
print(f"    Inserted {len(rows):,} rows with date = {CORRECT_DATE}")

# ── Step 3: Re-insert invoice data ─────────────────────────────────────────
INV_COLS  = ['date', 'time', 'invoice_no', 'branch', 'rbm', 'bdm',
             'customer_mobile', 'customer_pincode', 'customer_gstin',
             'customer_type', 'sales_staff_code', 'billing_staff_code',
             'invoice_total', 'discount', 'buyback', 'deductions',
             'exchange', 'financier_code', 'financier_name', 'scheme', 'loan_amount']
INV_STR   = {'time', 'invoice_no', 'branch', 'rbm', 'bdm', 'customer_mobile',
             'customer_pincode', 'customer_gstin', 'customer_type',
             'sales_staff_code', 'billing_staff_code', 'financier_code', 'financier_name', 'scheme'}
INV_FLOAT = {'invoice_total', 'discount', 'buyback', 'deductions', 'exchange', 'loan_amount'}

print("\n[3] Re-downloading and inserting azure_invoice_report with correct date...")
raw = container_client.get_blob_client(INV_BLOB).download_blob().readall()
df2 = pd.read_csv(io.BytesIO(raw))
rename2 = {
    'Date': 'date', 'Time': 'time', 'Invoice No': 'invoice_no', 'Invoice No.': 'invoice_no',
    'Branch': 'branch', 'RBM': 'rbm', 'BDM': 'bdm',
    'Customer Bill To No': 'customer_mobile', 'Customer Bill To No.': 'customer_mobile',
    'Customer Bill To Pincode': 'customer_pincode', 'Customer Bill To GSTIN': 'customer_gstin',
    'Customer Type': 'customer_type',
    'Sales Staff Code': 'sales_staff_code', 'Billing Staff Code': 'billing_staff_code',
    'Invoice Total': 'invoice_total', 'Discount': 'discount', 'Buyback': 'buyback',
    'Deductions (Indirect)': 'deductions', 'Exchange': 'exchange',
    'Financier Code': 'financier_code', 'Financier Name': 'financier_name',
    'Scheme': 'scheme', 'Loan Amount': 'loan_amount'
}
df2.rename(columns={k: v for k, v in rename2.items() if k in df2.columns}, inplace=True)
for c in INV_COLS:
    if c not in df2.columns:
        df2[c] = '' if c in INV_STR else 0.0
for c in INV_STR:
    if c in df2.columns: df2[c] = safe_str(df2[c])
for c in INV_FLOAT:
    if c in df2.columns: df2[c] = safe_float(df2[c])
df2 = df2[df2['invoice_no'].str.strip() != '']

rows2 = [(CORRECT_DATE, r.time, r.invoice_no, r.branch, r.rbm, r.bdm,
          r.customer_mobile, r.customer_pincode, r.customer_gstin, r.customer_type,
          r.sales_staff_code, r.billing_staff_code, r.invoice_total, r.discount,
          r.buyback, r.deductions, r.exchange, r.financier_code, r.financier_name,
          r.scheme, r.loan_amount)
         for r in df2.itertuples(index=False)]
client.insert(INVOICE_TABLE, rows2, column_names=INV_COLS)
print(f"    Inserted {len(rows2):,} rows with date = {CORRECT_DATE}")

# ── Verify ─────────────────────────────────────────────────────────────────
time.sleep(3)
print("\n[4] Verifying date distribution...")
r_s = client.query("""
    SELECT toDate(date) AS d, count() AS rows
    FROM azure_sales_report
    WHERE toDate(date) >= '2026-08-26'
    GROUP BY d ORDER BY d
""").result_rows
r_i = client.query("""
    SELECT toDate(date) AS d, count() AS rows
    FROM azure_invoice_report
    WHERE toDate(date) >= '2026-08-26'
    GROUP BY d ORDER BY d
""").result_rows

print("\n  azure_sales_report (Aug 26+):")
for row in r_s:
    print(f"    {row[0]}  -> {row[1]:,} rows")
print("\n  azure_invoice_report (Aug 26+):")
for row in r_i:
    print(f"    {row[0]}  -> {row[1]:,} rows")

t1 = client.query(f"SELECT max(date), count() FROM {SALES_TABLE}").result_rows[0]
t2 = client.query(f"SELECT max(date), count() FROM {INVOICE_TABLE}").result_rows[0]
print(f"\n  Sales:   max={t1[0]}  total={t1[1]:,}")
print(f"  Invoice: max={t2[0]}  total={t2[1]:,}")
print("\n  DONE - 2026-08-28 now stored correctly as Aug 28!")
