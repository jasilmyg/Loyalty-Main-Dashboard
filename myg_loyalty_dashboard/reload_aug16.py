"""Load Aug 17 blob files (containing Aug 16 sales data) into both azure tables."""
import os, io
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'myg_loyalty_dashboard.settings')
import django; django.setup()

import pandas as pd
from datetime import datetime
from azure.storage.blob import ContainerClient
from analytics.clickhouse_service import get_ch_client

SAS_URL = "https://stmygoalposreports.blob.core.windows.net/sales-reports?sp=racwl&st=2026-08-11T03:51:43Z&se=2026-12-31T18:29:43Z&spr=https&sv=2026-02-06&sr=c&sig=b5URyZCBQKQU3rwuqxY5z2vqyKNrsDKIPABLQ%2FFyywQ%3D"

cc = ContainerClient.from_container_url(SAS_URL)
ch = get_ch_client()

def safe_str(s):   return s.fillna('').astype(str).str.strip().replace('nan','').replace('None','')
def safe_int(s):   return pd.to_numeric(s, errors='coerce').fillna(0).astype(int)
def safe_float(s): return pd.to_numeric(s, errors='coerce').fillna(0.0).astype(float)
def parse_date_col(s):
    parsed = pd.to_datetime(s, dayfirst=True, errors='coerce').fillna(pd.Timestamp('1970-01-01'))
    return list(parsed.dt.to_pydatetime())

def download_blob(name):
    return cc.get_blob_client(name).download_blob().readall()

inv_schema   = {r[0]: r[1] for r in ch.query('DESCRIBE TABLE azure_invoice_report').result_rows}
sales_schema = {r[0]: r[1] for r in ch.query('DESCRIBE TABLE azure_sales_report').result_rows}

INV_COL_MAP = {
    'Date': 'date', 'Time': 'time',
    'Invoice No': 'invoice_no', 'Invoice No.': 'invoice_no',
    'Branch': 'branch', 'RBM': 'rbm', 'BDM': 'bdm',
    'Customer Bill To No': 'customer_mobile', 'Customer Bill To No.': 'customer_mobile',
    'Customer Mobile': 'customer_mobile',
    'Customer Bill To Pincode': 'customer_pincode',
    'Customer Bill To GSTIN': 'customer_gstin',
    'Customer Type': 'customer_type',
    'Sales Staff Code': 'sales_staff_code', 'Billing Staff Code': 'billing_staff_code',
    'Invoice Total': 'invoice_total', 'Discount': 'discount', 'Buyback': 'buyback',
    'Deductions (Indirect)': 'deductions', 'Exchange': 'exchange',
    'Financier Code': 'financier_code', 'Financier Name': 'financier_name',
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

def process_df(df, col_map, schema):
    df = df.rename(columns={k: v for k, v in col_map.items() if k in df.columns})
    df.columns = [c.strip().lower().replace(' ', '_') for c in df.columns]
    result = {}
    for col, dtype in schema.items():
        if col not in df.columns:
            if 'Int' in dtype or 'UInt' in dtype: result[col] = 0
            elif 'Float' in dtype: result[col] = 0.0
            elif 'DateTime' in dtype: result[col] = [datetime(1970,1,1)] * len(df)
            else: result[col] = ''
        elif col == 'date' or 'DateTime' in dtype:
            result[col] = parse_date_col(df[col])
        elif 'Int' in dtype or 'UInt' in dtype: result[col] = safe_int(df[col])
        elif 'Float' in dtype: result[col] = safe_float(df[col])
        else: result[col] = safe_str(df[col])
    return pd.DataFrame(result)[list(schema.keys())]

# Aug 17 blob = Aug 16 sales data
INV_BLOB   = 'invoice_wise_sales_report/invoice_wise_sales_report_17-08-2026_03_00_05_797321.csv'
SALES_BLOB = 'item_wise_sales_report/item_wise_sales_report_17-08-2026_03_00_02_899139.csv'

print('Loading Aug 17 blob -> azure_invoice_report...')
raw = download_blob(INV_BLOB)
df  = pd.read_csv(io.BytesIO(raw))
print(f'  Rows: {len(df)} | Date sample: {df["Date"].head(3).tolist()}')
df  = process_df(df, INV_COL_MAP, inv_schema)
df  = df[df['invoice_no'].astype(str).str.strip() != '']
rows = df.values.tolist()
ch.insert('azure_invoice_report', rows, column_names=list(inv_schema.keys()))
print(f'  Inserted {len(rows):,} rows | Date range: {min(df["date"])} -> {max(df["date"])}')

print()
print('Loading Aug 17 blob -> azure_sales_report...')
raw = download_blob(SALES_BLOB)
df  = pd.read_csv(io.BytesIO(raw))
print(f'  Rows: {len(df)} | Date sample: {df["Date"].head(3).tolist()}')
df  = process_df(df, SALES_COL_MAP, sales_schema)
df  = df[df['invoice_no'].astype(str).str.strip() != '']
rows = df.values.tolist()
ch.insert('azure_sales_report', rows, column_names=list(sales_schema.keys()))
print(f'  Inserted {len(rows):,} rows | Date range: {min(df["date"])} -> {max(df["date"])}')

print()
r1 = ch.query('SELECT count(), max(date) FROM azure_invoice_report').result_rows[0]
r2 = ch.query('SELECT count(), max(date) FROM azure_sales_report').result_rows[0]
print(f'azure_invoice_report: {int(r1[0]):,} rows | last={r1[1]}')
print(f'azure_sales_report  : {int(r2[0]):,} rows | last={r2[1]}')
print('Done!')
