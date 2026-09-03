"""
clean_insert_aug28.py
======================
Wait for ClickHouse mutations to complete, then cleanly insert Aug-28 data.
Uses direct SQL INSERT with explicit date string so no timezone conversion happens.
"""
import os, sys, io, django, time
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

def safe_str(s):   return s.fillna('').astype(str).str.strip().replace({'nan': '', 'None': ''})
def safe_float(s): return pd.to_numeric(s, errors='coerce').fillna(0.0).astype(float)

print("=" * 60)
print("  Clean insert Aug-28 data into ClickHouse")
print("=" * 60)

# Step 1: Delete ANY existing Aug-28 data (all possible UTC timestamps)
print("\n[1] Removing any existing Aug-28 data...")
client.command("ALTER TABLE azure_sales_report   DELETE WHERE date >= toDateTime('2026-08-27 18:29:59') AND date <= toDateTime('2026-08-28 18:30:00')")
client.command("ALTER TABLE azure_invoice_report DELETE WHERE date >= toDateTime('2026-08-27 18:29:59') AND date <= toDateTime('2026-08-28 18:30:00')")

# Wait for mutations
print("    Waiting 15s for ClickHouse mutations to complete...")
time.sleep(15)

# Verify clean
s_check = client.query("SELECT count() FROM azure_sales_report   WHERE date >= toDateTime('2026-08-27 18:29:59') AND date <= toDateTime('2026-08-28 18:30:00')").result_rows[0][0]
i_check = client.query("SELECT count() FROM azure_invoice_report WHERE date >= toDateTime('2026-08-27 18:29:59') AND date <= toDateTime('2026-08-28 18:30:00')").result_rows[0][0]
print(f"    Rows in Aug-28 range after delete: sales={s_check}, invoice={i_check}")
if s_check > 0 or i_check > 0:
    print("    Mutations still pending, waiting 15 more seconds...")
    time.sleep(15)

# Step 2: Insert with date as string (ClickHouse will parse it as UTC date)
# Use toDateTime('2026-08-28') which ClickHouse treats as UTC midnight 2026-08-28
SALES_COLS = ['date', 'invoice_no', 'branch', 'item_code', 'imei_batch',
              'qty', 'mop', 'discount', 'buyback', 'sold_price', 'taxable']

print("\n[2] Downloading and inserting sales data...")
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

# Use string date — ClickHouse insert_formats will parse '2026-08-28' as UTC date
DATE_STR = '2026-08-28'
rows = [(DATE_STR, r.invoice_no, r.branch, r.item_code, r.imei_batch,
         r.qty, r.mop, r.discount, r.buyback, r.sold_price, r.taxable)
        for r in df.itertuples(index=False)]
client.insert(
    'azure_sales_report', rows,
    column_names=SALES_COLS,
    column_type_names=['String', 'String', 'String', 'String', 'String',
                       'Float32', 'Float32', 'Float32', 'Float32', 'Float32', 'Float32']
)
print(f"    Inserted {len(rows):,} rows")

# Step 3: Invoice
INV_COLS  = ['date', 'time', 'invoice_no', 'branch', 'rbm', 'bdm',
             'customer_mobile', 'customer_pincode', 'customer_gstin',
             'customer_type', 'sales_staff_code', 'billing_staff_code',
             'invoice_total', 'discount', 'buyback', 'deductions',
             'exchange', 'financier_code', 'financier_name', 'scheme', 'loan_amount']
INV_STR   = {'time', 'invoice_no', 'branch', 'rbm', 'bdm', 'customer_mobile',
             'customer_pincode', 'customer_gstin', 'customer_type',
             'sales_staff_code', 'billing_staff_code', 'financier_code', 'financier_name', 'scheme'}
INV_FLOAT = {'invoice_total', 'discount', 'buyback', 'deductions', 'exchange', 'loan_amount'}

print("\n[3] Downloading and inserting invoice data...")
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

rows2 = [(DATE_STR, r.time, r.invoice_no, r.branch, r.rbm, r.bdm,
          r.customer_mobile, r.customer_pincode, r.customer_gstin, r.customer_type,
          r.sales_staff_code, r.billing_staff_code, r.invoice_total, r.discount,
          r.buyback, r.deductions, r.exchange, r.financier_code, r.financier_name,
          r.scheme, r.loan_amount)
         for r in df2.itertuples(index=False)]
client.insert(
    'azure_invoice_report', rows2,
    column_names=INV_COLS,
    column_type_names=['String', 'String', 'String', 'String', 'String', 'String',
                       'String', 'String', 'String', 'String', 'String', 'String',
                       'Float32', 'Float32', 'Float32', 'Float32', 'Float32',
                       'String', 'String', 'String', 'Float32']
)
print(f"    Inserted {len(rows2):,} rows")

# Step 4: Final verification
time.sleep(5)
print("\n[4] Final verification...")
for tbl in ['azure_sales_report', 'azure_invoice_report']:
    rows_check = client.query(f"""
        SELECT date, count() FROM {tbl}
        WHERE date >= '2026-08-26'
        GROUP BY date ORDER BY date
    """).result_rows
    total = client.query(f"SELECT max(date), count() FROM {tbl}").result_rows[0]
    print(f"\n  {tbl}:")
    print(f"    max={total[0]}  total={total[1]:,}")
    for r in rows_check:
        print(f"    {r[0]}  ->  {r[1]:,}")
