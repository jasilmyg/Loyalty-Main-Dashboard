import os, sys, io, django, time
import pandas as pd
from datetime import datetime, timezone

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'myg_loyalty_dashboard.settings')
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
django.setup()

from analytics.clickhouse_service import get_ch_client
from azure.storage.blob import ContainerClient

ACCOUNT_NAME   = 'stmygoalposreports'
CONTAINER_NAME = 'sales-reports'
SAS_TOKEN      = 'sp=racwl&st=2026-08-11T03:51:43Z&se=2026-12-31T18:29:43Z&spr=https&sv=2026-02-06&sr=c&sig=b5URyZCBQKQU3rwuqxY5z2vqyKNrsDKIPABLQ%2FFyywQ%3D'
container_url  = f'https://{ACCOUNT_NAME}.blob.core.windows.net/{CONTAINER_NAME}?{SAS_TOKEN}'
container_client = ContainerClient.from_container_url(container_url)

ch = get_ch_client()
if not ch:
    print('ERROR: Cannot connect to ClickHouse')
    sys.exit(1)

SALES_TABLE   = 'azure_sales_report'
INVOICE_TABLE = 'azure_invoice_report'

ITEM_BLOB = 'item_wise_sales_report/item_wise_sales_report_02-09-2026_03_00_02_263096.csv'
INV_BLOB  = 'invoice_wise_sales_report/invoice_wise_sales_report_02-09-2026_03_00_03_258083.csv'
DATA_DATE = '2026-09-01'
LABEL     = 'Sep 01, 2026'

SALES_COLS = ['date', 'invoice_no', 'branch', 'item_code', 'imei_batch',
              'qty', 'mop', 'discount', 'buyback', 'sold_price', 'taxable']

INV_COLS  = ['date', 'time', 'invoice_no', 'branch', 'rbm', 'bdm',
             'customer_mobile', 'customer_pincode', 'customer_gstin',
             'customer_type', 'sales_staff_code', 'billing_staff_code',
             'invoice_total', 'discount', 'buyback', 'deductions',
             'exchange', 'financier_code', 'financier_name', 'scheme', 'loan_amount']
INV_STR   = {'time', 'invoice_no', 'branch', 'rbm', 'bdm', 'customer_mobile',
             'customer_pincode', 'customer_gstin', 'customer_type',
             'sales_staff_code', 'billing_staff_code', 'financier_code', 'financier_name', 'scheme'}
INV_FLOAT = {'invoice_total', 'discount', 'buyback', 'deductions', 'exchange', 'loan_amount'}

RENAME_ITEM = {
    'Date': 'date', 'Invoice No': 'invoice_no', 'Invoice No.': 'invoice_no',
    'Branch': 'branch', 'Item Code': 'item_code',
    'IMEI/Batch': 'imei_batch', 'IMEI/Batch No': 'imei_batch', 'IMEI/Batch No.': 'imei_batch',
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
def parse_dates(series):
    return pd.to_datetime(series, format='%d-%m-%Y', errors='coerce').dt.normalize()
def to_utc_dt(ts):
    return datetime(ts.year, ts.month, ts.day, 0, 0, 0, tzinfo=timezone.utc)

print('=' * 65)
print(f'  Loading {LABEL} data into ClickHouse')
print(f'  Blob file date: 02-09-2026')
print('=' * 65)

s_before = ch.query(f"SELECT count() FROM {SALES_TABLE}   WHERE toDate(date)='{DATA_DATE}'").result_rows[0][0]
i_before = ch.query(f"SELECT count() FROM {INVOICE_TABLE} WHERE toDate(date)='{DATA_DATE}'").result_rows[0][0]
print(f'\n  Existing rows for {DATA_DATE} BEFORE insert:')
print(f'    {SALES_TABLE}:   {s_before:,}')
print(f'    {INVOICE_TABLE}: {i_before:,}')

if s_before > 0 or i_before > 0:
    print(f'\n  [WARNING] Data already exists for {DATA_DATE}!')
    print(f'  Deleting existing rows first...')
    ch.command(f"ALTER TABLE {SALES_TABLE}   DELETE WHERE toDate(date) = '{DATA_DATE}'")
    ch.command(f"ALTER TABLE {INVOICE_TABLE} DELETE WHERE toDate(date) = '{DATA_DATE}'")
    time.sleep(20)

print(f'\n  [1] Downloading item-wise blob...')
raw = container_client.get_blob_client(ITEM_BLOB).download_blob().readall()
df  = pd.read_csv(io.BytesIO(raw))
print(f'      Raw rows: {len(df):,}')

df.rename(columns={k: v for k, v in RENAME_ITEM.items() if k in df.columns}, inplace=True)
df['date'] = parse_dates(df['date'])
df['invoice_no'] = safe_str(df['invoice_no'])
df['branch']     = safe_str(df['branch'])
df['item_code']  = safe_str(df['item_code'])
if 'imei_batch' not in df.columns: df['imei_batch'] = ''
df['imei_batch'] = df['imei_batch'].fillna('').astype(str).str.strip()
df['qty']        = safe_float(df['qty'])
df['mop']        = safe_float(df['mop'])
df['discount']   = safe_float(df['discount'])
if 'buyback' not in df.columns: df['buyback'] = 0.0
df['buyback']    = safe_float(df['buyback'])
df['sold_price'] = safe_float(df['sold_price'])
df['taxable']    = safe_float(df['taxable'])

df = df[SALES_COLS].dropna(subset=['date'])
df = df[df['invoice_no'].str.strip() != '']

rows = [
    (to_utc_dt(r.date), r.invoice_no, r.branch, r.item_code,
     r.imei_batch, r.qty, r.mop, r.discount, r.buyback, r.sold_price, r.taxable)
    for r in df.itertuples(index=False)
]
ch.insert(SALES_TABLE, rows, column_names=SALES_COLS)
print(f'      [OK] Inserted {len(rows):,} rows -> {SALES_TABLE}')

print(f'\n  [2] Downloading invoice-wise blob...')
raw2 = container_client.get_blob_client(INV_BLOB).download_blob().readall()
df2  = pd.read_csv(io.BytesIO(raw2))
print(f'      Raw rows: {len(df2):,}')

df2.rename(columns={k: v for k, v in RENAME_INV.items() if k in df2.columns}, inplace=True)
for c in INV_COLS:
    if c not in df2.columns:
        df2[c] = '' if c in INV_STR else 0.0

df2['date'] = parse_dates(df2['date'])
for c in INV_STR:
    if c in df2.columns: df2[c] = safe_str(df2[c])
for c in INV_FLOAT:
    if c in df2.columns: df2[c] = safe_float(df2[c])

df2 = df2[INV_COLS].dropna(subset=['date'])
df2 = df2[df2['invoice_no'].str.strip() != '']

rows2 = [
    (to_utc_dt(r.date), r.time, r.invoice_no, r.branch, r.rbm, r.bdm,
     r.customer_mobile, r.customer_pincode, r.customer_gstin, r.customer_type,
     r.sales_staff_code, r.billing_staff_code, r.invoice_total, r.discount,
     r.buyback, r.deductions, r.exchange, r.financier_code, r.financier_name,
     r.scheme, r.loan_amount)
    for r in df2.itertuples(index=False)
]
ch.insert(INVOICE_TABLE, rows2, column_names=INV_COLS)
print(f'      [OK] Inserted {len(rows2):,} rows -> {INVOICE_TABLE}')

print(f'\n  Waiting 15s for inserts to commit...')
time.sleep(15)

ms = ch.query(f"SELECT max(toDate(date)) FROM {SALES_TABLE}").result_rows[0][0]
mi = ch.query(f"SELECT max(toDate(date)) FROM {INVOICE_TABLE}").result_rows[0][0]
print(f'\n  Max date in {SALES_TABLE}   : {ms}')
print(f'  Max date in {INVOICE_TABLE} : {mi}')
print('DONE')
