"""
load_aug28_from_aug29_blob.py
==============================
Loads 28-Aug-2026 data from Azure blob files named 29-08-2026
into azure_sales_report and azure_invoice_report in ClickHouse.

Files:
  item_wise_sales_report/item_wise_sales_report_29-08-2026_03_00_02_444257.csv
  invoice_wise_sales_report/invoice_wise_sales_report_29-08-2026_03_00_03_919584.csv
"""
import os, sys, io, django
import pandas as pd

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'myg_loyalty_dashboard.settings')
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
django.setup()

from analytics.clickhouse_service import get_ch_client
from azure.storage.blob import ContainerClient

ACCOUNT_NAME   = "stmygoalposreports"
CONTAINER_NAME = "sales-reports"
SAS_TOKEN      = "sp=racwl&st=2026-08-11T03:51:43Z&se=2026-12-31T18:29:43Z&spr=https&sv=2026-02-06&sr=c&sig=b5URyZCBQKQU3rwuqxY5z2vqyKNrsDKIPABLQ%2FFyywQ%3D"
ACCOUNT_URL    = f"https://{ACCOUNT_NAME}.blob.core.windows.net"
container_url  = f"{ACCOUNT_URL}/{CONTAINER_NAME}?{SAS_TOKEN}"
container_client = ContainerClient.from_container_url(container_url)

client = get_ch_client()

ITEM_BLOB = "item_wise_sales_report/item_wise_sales_report_29-08-2026_03_00_02_444257.csv"
INV_BLOB  = "invoice_wise_sales_report/invoice_wise_sales_report_29-08-2026_03_00_03_919584.csv"

SALES_TABLE   = "azure_sales_report"
INVOICE_TABLE = "azure_invoice_report"

def safe_str(s):   return s.fillna('').astype(str).str.strip().replace({'nan': '', 'None': ''})
def safe_float(s): return pd.to_numeric(s, errors='coerce').fillna(0.0).astype(float)
def parse_dt(s):   return pd.to_datetime(s, format='%d-%m-%Y', errors='coerce')

SALES_COLS = ['date', 'invoice_no', 'branch', 'item_code', 'imei_batch',
              'qty', 'mop', 'discount', 'buyback', 'sold_price', 'taxable']

print("=" * 60)
print("  Loading 28-Aug-2026 data from 29-08-2026 blob files")
print("=" * 60)

# ── Check existing row counts ──────────────────────────────────────────────────
before1 = client.query(f"SELECT count() FROM {SALES_TABLE} WHERE toDate(date) = '2026-08-28'").result_rows[0][0]
before2 = client.query(f"SELECT count() FROM {INVOICE_TABLE} WHERE toDate(date) = '2026-08-28'").result_rows[0][0]
print(f"\nRows for 2026-08-28 BEFORE insert:")
print(f"  {SALES_TABLE}:   {before1:,}")
print(f"  {INVOICE_TABLE}: {before2:,}")

# ── 1. Load azure_sales_report ─────────────────────────────────────────────────
print(f"\n[1] Downloading item wise blob ...")
raw = container_client.get_blob_client(ITEM_BLOB).download_blob().readall()
df  = pd.read_csv(io.BytesIO(raw))
print(f"    Raw rows: {len(df):,}  cols: {list(df.columns)}")

rename = {
    'Date': 'date', 'Invoice No': 'invoice_no', 'Invoice No.': 'invoice_no',
    'Branch': 'branch', 'Item Code': 'item_code',
    'IMEI/Batch': 'imei_batch', 'IMEI/Batch No': 'imei_batch',
    'Qty': 'qty', 'QTY': 'qty', 'Quantity': 'qty',
    'MOP': 'mop', 'Discount': 'discount', 'Buyback': 'buyback',
    'Sold Price': 'sold_price', 'Taxable': 'taxable'
}
df.rename(columns={k: v for k, v in rename.items() if k in df.columns}, inplace=True)

df['date']       = parse_dt(df['date'])
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

df = df[SALES_COLS].dropna(subset=['date'])
df = df[df['invoice_no'].str.strip() != '']
print(f"    Dates found in data: {sorted(df['date'].dt.date.unique())}")

# Convert to tuples with Python datetime for ClickHouse insert
rows = [(r.date.to_pydatetime(), r.invoice_no, r.branch, r.item_code, r.imei_batch,
         r.qty, r.mop, r.discount, r.buyback, r.sold_price, r.taxable)
        for r in df.itertuples(index=False)]
client.insert(SALES_TABLE, rows, column_names=SALES_COLS)
print(f"    ✅  Inserted {len(rows):,} rows into {SALES_TABLE}")

# ── 2. Load azure_invoice_report ───────────────────────────────────────────────
INV_COLS  = ['date', 'time', 'invoice_no', 'branch', 'rbm', 'bdm',
             'customer_mobile', 'customer_pincode', 'customer_gstin',
             'customer_type', 'sales_staff_code', 'billing_staff_code',
             'invoice_total', 'discount', 'buyback', 'deductions',
             'exchange', 'financier_code', 'financier_name', 'scheme', 'loan_amount']
INV_STR   = {'time', 'invoice_no', 'branch', 'rbm', 'bdm', 'customer_mobile',
             'customer_pincode', 'customer_gstin', 'customer_type',
             'sales_staff_code', 'billing_staff_code', 'financier_code', 'financier_name', 'scheme'}
INV_FLOAT = {'invoice_total', 'discount', 'buyback', 'deductions', 'exchange', 'loan_amount'}

print(f"\n[2] Downloading invoice wise blob ...")
raw = container_client.get_blob_client(INV_BLOB).download_blob().readall()
df2 = pd.read_csv(io.BytesIO(raw))
print(f"    Raw rows: {len(df2):,}  cols: {list(df2.columns)}")

rename2 = {
    'Date': 'date', 'Time': 'time', 'Invoice No': 'invoice_no', 'Invoice No.': 'invoice_no',
    'Branch': 'branch', 'RBM': 'rbm', 'BDM': 'bdm',
    'Customer Bill To No': 'customer_mobile', 'Customer Bill To No.': 'customer_mobile',
    'Customer Bill To Pincode': 'customer_pincode',
    'Customer Bill To GSTIN': 'customer_gstin',
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

df2['date'] = parse_dt(df2['date'])
for c in INV_STR:
    if c in df2.columns: df2[c] = safe_str(df2[c])
for c in INV_FLOAT:
    if c in df2.columns: df2[c] = safe_float(df2[c])

df2 = df2[INV_COLS].dropna(subset=['date'])
df2 = df2[df2['invoice_no'].str.strip() != '']
print(f"    Dates found in data: {sorted(df2['date'].dt.date.unique())}")

rows2 = [(r.date.to_pydatetime(), r.time, r.invoice_no, r.branch, r.rbm, r.bdm,
          r.customer_mobile, r.customer_pincode, r.customer_gstin, r.customer_type,
          r.sales_staff_code, r.billing_staff_code, r.invoice_total, r.discount,
          r.buyback, r.deductions, r.exchange, r.financier_code, r.financier_name,
          r.scheme, r.loan_amount)
         for r in df2.itertuples(index=False)]
client.insert(INVOICE_TABLE, rows2, column_names=INV_COLS)
print(f"    ✅  Inserted {len(rows2):,} rows into {INVOICE_TABLE}")

# ── Verify ─────────────────────────────────────────────────────────────────────
r1 = client.query(f"SELECT max(date), count() FROM {SALES_TABLE}").result_rows[0]
r2 = client.query(f"SELECT max(date), count() FROM {INVOICE_TABLE}").result_rows[0]
after1 = client.query(f"SELECT count() FROM {SALES_TABLE} WHERE toDate(date) = '2026-08-28'").result_rows[0][0]
after2 = client.query(f"SELECT count() FROM {INVOICE_TABLE} WHERE toDate(date) = '2026-08-28'").result_rows[0][0]

print("\n" + "=" * 60)
print("  LOAD COMPLETE")
print("=" * 60)
print(f"  {SALES_TABLE}:")
print(f"    max date          = {r1[0]}")
print(f"    total rows        = {r1[1]:,}")
print(f"    rows 2026-08-28   = {after1:,}")
print(f"\n  {INVOICE_TABLE}:")
print(f"    max date          = {r2[0]}")
print(f"    total rows        = {r2[1]:,}")
print(f"    rows 2026-08-28   = {after2:,}")
print("=" * 60)
