"""Final fix: re-insert Aug 14 rows. No more deletes - just insert."""
import os, io
os.environ.setdefault('DJANGO_SETTINGS_MODULE','myg_loyalty_dashboard.settings')
import django; django.setup()
import pandas as pd
from datetime import datetime
from azure.storage.blob import ContainerClient
from analytics.clickhouse_service import get_ch_client
from django.core.cache import cache

SAS_URL = "https://stmygoalposreports.blob.core.windows.net/sales-reports?sp=racwl&st=2026-08-11T03:51:43Z&se=2026-12-31T18:29:43Z&spr=https&sv=2026-02-06&sr=c&sig=b5URyZCBQKQU3rwuqxY5z2vqyKNrsDKIPABLQ%2FFyywQ%3D"
cc = ContainerClient.from_container_url(SAS_URL)
ch = get_ch_client()

def safe_str(s):   return s.fillna('').astype(str).str.strip().replace('nan','').replace('None','')
def safe_float(s): return pd.to_numeric(s, errors='coerce').fillna(0.0).astype(float)
def parse_date_col(s):
    parsed = pd.to_datetime(s, dayfirst=True, errors='coerce').fillna(pd.Timestamp('1970-01-01'))
    return list(parsed.dt.to_pydatetime())

inv_schema = {r[0]: r[1] for r in ch.query('DESCRIBE TABLE azure_invoice_report').result_rows}
INV_COL_MAP = {
    'Date':'date','Time':'time','Invoice No':'invoice_no','Invoice No.':'invoice_no',
    'Branch':'branch','RBM':'rbm','BDM':'bdm',
    'Customer Bill To No':'customer_mobile','Customer Bill To No.':'customer_mobile',
    'Customer Mobile':'customer_mobile','Customer Bill To Pincode':'customer_pincode',
    'Customer Bill To GSTIN':'customer_gstin','Customer Type':'customer_type',
    'Sales Staff Code':'sales_staff_code','Billing Staff Code':'billing_staff_code',
    'Invoice Total':'invoice_total','Discount':'discount','Buyback':'buyback',
    'Deductions (Indirect)':'deductions','Exchange':'exchange',
    'Financier Code':'financier_code','Financier Name':'financier_name',
    'Scheme':'scheme','Loan Amount':'loan_amount',
}

def process_inv(raw_bytes):
    df = pd.read_csv(io.BytesIO(raw_bytes))
    df = df.rename(columns={k: v for k, v in INV_COL_MAP.items() if k in df.columns})
    df.columns = [c.strip().lower().replace(' ','_') for c in df.columns]
    result = {}
    for col, dtype in inv_schema.items():
        if col not in df.columns:
            if 'Int' in dtype or 'UInt' in dtype: result[col] = 0
            elif 'Float' in dtype: result[col] = 0.0
            elif 'DateTime' in dtype: result[col] = [datetime(1970,1,1)] * len(df)
            else: result[col] = ''
        elif col == 'date' or 'DateTime' in dtype:
            result[col] = parse_date_col(df[col])
        elif 'Int' in dtype or 'UInt' in dtype:
            result[col] = pd.to_numeric(df[col], errors='coerce').fillna(0).astype(int)
        elif 'Float' in dtype: result[col] = safe_float(df[col])
        else: result[col] = safe_str(df[col])
    return pd.DataFrame(result)[list(inv_schema.keys())]

# Insert Aug 14 from blob 15-08-2026
print('Inserting Aug 14 from blob 15-08-2026...')
raw = cc.get_blob_client('invoice_wise_sales_report/invoice_wise_sales_report_15-08-2026_03_00_03_422021.csv').download_blob().readall()
df = process_inv(raw)
df = df[df['invoice_no'].astype(str).str.strip() != '']
date_series = pd.Series([d.date() if hasattr(d,'date') else d for d in df['date']])
aug14 = df[date_series.values == datetime(2026,8,14).date()]
print(f'  Aug 14 rows: {len(aug14):,}')
ch.insert('azure_invoice_report', aug14.values.tolist(), column_names=list(inv_schema.keys()))
print(f'  Inserted successfully!')

# Verify
import time; time.sleep(3)
r = ch.query("SELECT count(), sum(invoice_total) FROM azure_invoice_report WHERE toDate(date) = '2026-08-14'").result_rows[0]
print(f'  Verify Aug 14: rows={int(r[0]):,}  rev={float(r[1])/1e7:.2f}Cr')

# Full check
print()
print('=== Aug 10-16 Final State ===')
rows = ch.query("SELECT toDate(date), count(), sum(invoice_total) FROM azure_invoice_report WHERE toDate(date) >= '2026-08-10' AND toDate(date) <= '2026-08-16' AND toDate(date) != '1970-01-01' GROUP BY toDate(date) ORDER BY toDate(date)").result_rows
for r in rows:
    print(f'  {r[0]}  rows={int(r[1]):,}  rev={float(r[2])/1e7:.2f}Cr  OK')

cache.clear()
print('\nDjango cache cleared! Refresh Daily New vs Repeat page.')
