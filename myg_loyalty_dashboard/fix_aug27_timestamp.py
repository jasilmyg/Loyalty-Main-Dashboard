"""
Fix: 27-Aug data was inserted at '2026-08-26 18:30:00' UTC due to IST timezone conversion.
Steps:
1. Delete rows at timestamp '2026-08-26 18:30:00' from both tables
2. Re-insert with correct date stored as UTC string '2026-08-27 00:00:00'
"""
import os, sys, io, django
import pandas as pd
from datetime import datetime, timezone

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

ITEM_BLOB = "item_wise_sales_report/item_wise_sales_report_28-08-2026_03_00_02_297323.csv"
INV_BLOB  = "invoice_wise_sales_report/invoice_wise_sales_report_28-08-2026_03_00_03_969553.csv"
SALES_TABLE   = "azure_sales_report"
INVOICE_TABLE = "azure_invoice_report"

# ── Step 1: Check and delete the wrongly-timestamped rows ─────────────────────
print("=== Step 1: Delete wrong timestamp rows ===")

# These rows are at '2026-08-26 18:30:00' which is 27-Aug IST data stored wrongly
r1 = client.query(f"SELECT count() FROM {SALES_TABLE} WHERE date = '2026-08-26 18:30:00'").result_rows[0][0]
r2 = client.query(f"SELECT count() FROM {INVOICE_TABLE} WHERE date = '2026-08-26 18:30:00'").result_rows[0][0]
print(f"  {SALES_TABLE} rows at 2026-08-26 18:30:00: {r1:,}")
print(f"  {INVOICE_TABLE} rows at 2026-08-26 18:30:00: {r2:,}")

if r1 > 0:
    client.command(f"ALTER TABLE {SALES_TABLE} DELETE WHERE date = '2026-08-26 18:30:00'")
    print(f"  DELETE issued on {SALES_TABLE}")
if r2 > 0:
    client.command(f"ALTER TABLE {INVOICE_TABLE} DELETE WHERE date = '2026-08-26 18:30:00'")
    print(f"  DELETE issued on {INVOICE_TABLE}")

# Wait for mutations to complete
import time
print("  Waiting for mutations to complete...")
time.sleep(10)

# Verify deletion
r1_after = client.query(f"SELECT count() FROM {SALES_TABLE} WHERE date = '2026-08-26 18:30:00'").result_rows[0][0]
r2_after = client.query(f"SELECT count() FROM {INVOICE_TABLE} WHERE date = '2026-08-26 18:30:00'").result_rows[0][0]
print(f"  After delete - {SALES_TABLE} at 18:30: {r1_after:,}")
print(f"  After delete - {INVOICE_TABLE} at 18:30: {r2_after:,}")

# ── Step 2: Re-insert with UTC midnight date ────────────────────────────────
print("\n=== Step 2: Re-insert 27-Aug data with correct UTC timestamp ===")

def safe_str(s):   return s.fillna('').astype(str).str.strip().replace({'nan':'','None':''})
def safe_float(s): return pd.to_numeric(s, errors='coerce').fillna(0.0).astype(float)

# Use UTC midnight datetime explicitly
AUG27_UTC = datetime(2026, 8, 27, 0, 0, 0)  # UTC midnight - no IST conversion

SALES_COLS = ['date','invoice_no','branch','item_code','imei_batch',
              'qty','mop','discount','buyback','sold_price','taxable']

# Load item wise
print(f"\nLoading {ITEM_BLOB} ...")
raw = container_client.get_blob_client(ITEM_BLOB).download_blob().readall()
df  = pd.read_csv(io.BytesIO(raw))
print(f"  Raw rows: {len(df):,}")

rename = {'Date':'date','Invoice No':'invoice_no','Invoice No.':'invoice_no',
          'Branch':'branch','Item Code':'item_code',
          'IMEI/Batch':'imei_batch','IMEI/Batch No':'imei_batch',
          'Qty':'qty','QTY':'qty','Quantity':'qty',
          'MOP':'mop','Discount':'discount','Buyback':'buyback',
          'Sold Price':'sold_price','Taxable':'taxable'}
df.rename(columns={k:v for k,v in rename.items() if k in df.columns}, inplace=True)

# Fix date - set all to UTC midnight of 27-Aug directly
df['date']       = AUG27_UTC
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

df = df[SALES_COLS]
df = df[df['invoice_no'].str.strip() != '']

rows = [tuple(r) for r in df.itertuples(index=False, name=None)]
client.insert(SALES_TABLE, rows, column_names=SALES_COLS)
print(f"  Inserted {len(rows):,} rows into {SALES_TABLE} at {AUG27_UTC}")

# Load invoice wise
INV_COLS = ['date','time','invoice_no','branch','rbm','bdm',
            'customer_mobile','customer_pincode','customer_gstin',
            'customer_type','sales_staff_code','billing_staff_code',
            'invoice_total','discount','buyback','deductions',
            'exchange','financier_code','financier_name','scheme','loan_amount']
INV_STR   = {'time','invoice_no','branch','rbm','bdm','customer_mobile',
             'customer_pincode','customer_gstin','customer_type',
             'sales_staff_code','billing_staff_code','financier_code','financier_name','scheme'}
INV_FLOAT = {'invoice_total','discount','buyback','deductions','exchange','loan_amount'}

print(f"\nLoading {INV_BLOB} ...")
raw = container_client.get_blob_client(INV_BLOB).download_blob().readall()
df  = pd.read_csv(io.BytesIO(raw))
print(f"  Raw rows: {len(df):,}")

rename2 = {'Date':'date','Time':'time','Invoice No':'invoice_no','Invoice No.':'invoice_no',
           'Branch':'branch','RBM':'rbm','BDM':'bdm',
           'Customer Bill To No':'customer_mobile','Customer Bill To No.':'customer_mobile',
           'Customer Bill To Pincode':'customer_pincode',
           'Customer Bill To GSTIN':'customer_gstin',
           'Customer Type':'customer_type',
           'Sales Staff Code':'sales_staff_code','Billing Staff Code':'billing_staff_code',
           'Invoice Total':'invoice_total','Discount':'discount','Buyback':'buyback',
           'Deductions (Indirect)':'deductions','Exchange':'exchange',
           'Financier Code':'financier_code','Financier Name':'financier_name',
           'Scheme':'scheme','Loan Amount':'loan_amount'}
df.rename(columns={k:v for k,v in rename2.items() if k in df.columns}, inplace=True)

for c in INV_COLS:
    if c not in df.columns:
        df[c] = '' if c in INV_STR else 0.0

# Fix date - UTC midnight directly
df['date'] = AUG27_UTC
for c in INV_STR:
    if c in df.columns: df[c] = safe_str(df[c])
for c in INV_FLOAT:
    if c in df.columns: df[c] = safe_float(df[c])

df = df[INV_COLS]
df = df[df['invoice_no'].str.strip() != '']
rows2 = [tuple(r) for r in df.itertuples(index=False, name=None)]
client.insert(INVOICE_TABLE, rows2, column_names=INV_COLS)
print(f"  Inserted {len(rows2):,} rows into {INVOICE_TABLE} at {AUG27_UTC}")

# ── Final verify ──────────────────────────────────────────────────────────────
print("\n=== Final Verification ===")
r = client.query("""
    SELECT toDate(date) as d, count(), round(sum(sold_price)/10000000,2) as cr
    FROM azure_sales_report
    WHERE toDate(date) >= '2026-08-24'
    GROUP BY d ORDER BY d
""").result_rows
print("azure_sales_report by date:")
for row in r:
    print(f"  {row[0]}: {row[1]:,} rows, Rs.{row[2]} Cr")

r2 = client.query(f"SELECT toDate(date), count() FROM {INVOICE_TABLE} WHERE toDate(date) >= '2026-08-24' GROUP BY toDate(date) ORDER BY toDate(date)").result_rows
print("\nazure_invoice_report by date:")
for row in r2:
    print(f"  {row[0]}: {row[1]:,} rows")
