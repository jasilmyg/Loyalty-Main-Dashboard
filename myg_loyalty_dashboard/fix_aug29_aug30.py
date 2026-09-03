"""
FIX: Aug 29 and Aug 30 data counts are swapped.
- Aug 29 currently has 58,374 rows (this is actually Aug 30 data)
- Aug 30 has 0 rows (Aug 29 data is missing)

Fix:
1. Delete wrong rows for 2026-08-29 (the 58,374 rows that are Aug 30 data)
2. Reload Aug 29 from 30-08-2026 blob → correctly gets 71,148 rows  
3. Reload Aug 30 from 31-08-2026 blob → correctly gets 58,374 rows
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
container_url  = f"https://{ACCOUNT_NAME}.blob.core.windows.net/{CONTAINER_NAME}?{SAS_TOKEN}"
container_client = ContainerClient.from_container_url(container_url)
ch = get_ch_client()

SALES_TABLE   = "azure_sales_report"
INVOICE_TABLE = "azure_invoice_report"

SALES_COLS = ['date', 'invoice_no', 'branch', 'item_code', 'imei_batch',
              'qty', 'mop', 'discount', 'buyback', 'sold_price', 'taxable']
INV_COLS   = ['date', 'time', 'invoice_no', 'branch', 'rbm', 'bdm',
              'customer_mobile', 'customer_pincode', 'customer_gstin',
              'customer_type', 'sales_staff_code', 'billing_staff_code',
              'invoice_total', 'discount', 'buyback', 'deductions',
              'exchange', 'financier_code', 'financier_name', 'scheme', 'loan_amount']
INV_STR    = {'time', 'invoice_no', 'branch', 'rbm', 'bdm', 'customer_mobile',
              'customer_pincode', 'customer_gstin', 'customer_type',
              'sales_staff_code', 'billing_staff_code', 'financier_code', 'financier_name', 'scheme'}
INV_FLOAT  = {'invoice_total', 'discount', 'buyback', 'deductions', 'exchange', 'loan_amount'}

RENAME_ITEM = {
    'Date': 'date', 'Invoice No': 'invoice_no', 'Invoice No.': 'invoice_no',
    'Branch': 'branch', 'Item Code': 'item_code',
    'IMEI/Batch': 'imei_batch', 'IMEI/Batch No': 'imei_batch',
    'Qty': 'qty', 'QTY': 'qty', 'Quantity': 'qty',
    'MOP': 'mop', 'Discount': 'discount', 'Buyback': 'buyback',
    'Sold Price': 'sold_price', 'Taxable': 'taxable'
}
RENAME_INV = {
    'Date': 'date', 'Time': 'time', 'Invoice No': 'invoice_no', 'Invoice No.': 'invoice_no',
    'Branch': 'branch', 'RBM': 'rbm', 'BDM': 'bdm',
    'Customer Bill To No': 'customer_mobile', 'Customer Bill To No.': 'customer_mobile',
    'Customer Bill To Pincode': 'customer_pincode',
    'Customer Bill To GSTIN': 'customer_gstin', 'Customer Bill to GSTIN': 'customer_gstin',
    'Customer Type': 'customer_type',
    'Sales Staff Code': 'sales_staff_code', 'Billing Staff Code': 'billing_staff_code',
    'Invoice Total': 'invoice_total', 'Discount': 'discount', 'Buyback': 'buyback',
    'Deductions (Indirect)': 'deductions', 'Exchange': 'exchange',
    'Financier Code': 'financier_code', 'Financier Name': 'financier_name',
    'Scheme': 'scheme', 'Loan Amount': 'loan_amount'
}

def safe_str(s):   return s.fillna('').astype(str).str.strip().replace({'nan': '', 'None': ''})
def safe_float(s): return pd.to_numeric(s, errors='coerce').fillna(0.0).astype(float)
def parse_dt(s):   return pd.to_datetime(s, format='%d-%m-%Y', errors='coerce')

print("=" * 65)
print("  STEP 1: Delete existing data for 2026-08-29 and 2026-08-30")
print("=" * 65)
for d in ['2026-08-29', '2026-08-30']:
    before_s = ch.query(f"SELECT count() FROM {SALES_TABLE}   WHERE toDate(date)='{d}'").result_rows[0][0]
    before_i = ch.query(f"SELECT count() FROM {INVOICE_TABLE} WHERE toDate(date)='{d}'").result_rows[0][0]
    print(f"\n  {d} before delete:  sales={before_s:,}  invoices={before_i:,}")
    ch.command(f"ALTER TABLE {SALES_TABLE}   DELETE WHERE toDate(date)='{d}'")
    ch.command(f"ALTER TABLE {INVOICE_TABLE} DELETE WHERE toDate(date)='{d}'")
    print(f"  ✅ Delete issued for {d}")

# Wait for deletes to propagate
import time
print("\n  Waiting 15s for deletes to propagate...")
time.sleep(15)

for d in ['2026-08-29', '2026-08-30']:
    s = ch.query(f"SELECT count() FROM {SALES_TABLE}   WHERE toDate(date)='{d}'").result_rows[0][0]
    i = ch.query(f"SELECT count() FROM {INVOICE_TABLE} WHERE toDate(date)='{d}'").result_rows[0][0]
    print(f"  {d} after delete:   sales={s:,}  invoices={i:,}")

def load_sales(blob_name, target_date_label):
    print(f"\n  Downloading: {blob_name}")
    raw = container_client.get_blob_client(blob_name).download_blob().readall()
    df  = pd.read_csv(io.BytesIO(raw))
    df.rename(columns={k: v for k, v in RENAME_ITEM.items() if k in df.columns}, inplace=True)
    df['date']       = parse_dt(df['date'])
    df['invoice_no'] = safe_str(df['invoice_no'])
    df['branch']     = safe_str(df['branch'])
    df['item_code']  = safe_str(df['item_code'])
    df['imei_batch'] = df['imei_batch'].fillna('').astype(str).str.strip() if 'imei_batch' in df.columns else ''
    df['qty']        = safe_float(df['qty'])
    df['mop']        = safe_float(df['mop'])
    df['discount']   = safe_float(df['discount'])
    df['buyback']    = safe_float(df['buyback']) if 'buyback' in df.columns else 0.0
    df['sold_price'] = safe_float(df['sold_price'])
    df['taxable']    = safe_float(df['taxable'])
    if 'imei_batch' not in df.columns: df['imei_batch'] = ''
    if 'buyback'    not in df.columns: df['buyback']    = 0.0
    df = df[SALES_COLS].dropna(subset=['date'])
    df = df[df['invoice_no'].str.strip() != '']
    print(f"  Dates in data: {sorted(df['date'].dt.date.unique())}  |  Rows: {len(df):,}")
    rows = [(r.date.to_pydatetime(), r.invoice_no, r.branch, r.item_code, r.imei_batch,
             r.qty, r.mop, r.discount, r.buyback, r.sold_price, r.taxable)
            for r in df.itertuples(index=False)]
    ch.insert(SALES_TABLE, rows, column_names=SALES_COLS)
    print(f"  ✅ Inserted {len(rows):,} → {SALES_TABLE}  [{target_date_label}]")
    return len(rows)

def load_invoices(blob_name, target_date_label):
    print(f"\n  Downloading: {blob_name}")
    raw = container_client.get_blob_client(blob_name).download_blob().readall()
    df2 = pd.read_csv(io.BytesIO(raw))
    df2.rename(columns={k: v for k, v in RENAME_INV.items() if k in df2.columns}, inplace=True)
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
    print(f"  Dates in data: {sorted(df2['date'].dt.date.unique())}  |  Rows: {len(df2):,}")
    rows2 = [(r.date.to_pydatetime(), r.time, r.invoice_no, r.branch, r.rbm, r.bdm,
              r.customer_mobile, r.customer_pincode, r.customer_gstin, r.customer_type,
              r.sales_staff_code, r.billing_staff_code, r.invoice_total, r.discount,
              r.buyback, r.deductions, r.exchange, r.financier_code, r.financier_name,
              r.scheme, r.loan_amount)
             for r in df2.itertuples(index=False)]
    ch.insert(INVOICE_TABLE, rows2, column_names=INV_COLS)
    print(f"  ✅ Inserted {len(rows2):,} → {INVOICE_TABLE}  [{target_date_label}]")
    return len(rows2)

print("\n" + "=" * 65)
print("  STEP 2: Load Aug 29 data (from 30-08-2026 blob)")
print("=" * 65)
load_sales(   'item_wise_sales_report/item_wise_sales_report_30-08-2026_03_00_02_812386.csv',    'Aug 29')
load_invoices('invoice_wise_sales_report/invoice_wise_sales_report_30-08-2026_03_00_03_821420.csv', 'Aug 29')

print("\n" + "=" * 65)
print("  STEP 3: Load Aug 30 data (from 31-08-2026 blob)")
print("=" * 65)
load_sales(   'item_wise_sales_report/item_wise_sales_report_31-08-2026_03_00_02_751833.csv',    'Aug 30')
load_invoices('invoice_wise_sales_report/invoice_wise_sales_report_31-08-2026_03_00_04_179739.csv', 'Aug 30')

print("\n  Waiting 10s for inserts to commit...")
time.sleep(10)

print("\n" + "=" * 65)
print("  FINAL VERIFICATION")
print("=" * 65)
for d in ['2026-08-27', '2026-08-28', '2026-08-29', '2026-08-30']:
    s = ch.query(f"SELECT count() FROM {SALES_TABLE}   WHERE toDate(date)='{d}'").result_rows[0][0]
    i = ch.query(f"SELECT count() FROM {INVOICE_TABLE} WHERE toDate(date)='{d}'").result_rows[0][0]
    mark = '✅' if s > 0 else '❌'
    print(f"  {mark} {d}:  sales={s:>8,}   invoices={i:>7,}")
ms = ch.query(f"SELECT max(toDate(date)) FROM {SALES_TABLE}").result_rows[0][0]
mi = ch.query(f"SELECT max(toDate(date)) FROM {INVOICE_TABLE}").result_rows[0][0]
print(f"\n  Max date in {SALES_TABLE}   : {ms}")
print(f"  Max date in {INVOICE_TABLE} : {mi}")
print("=" * 65)
