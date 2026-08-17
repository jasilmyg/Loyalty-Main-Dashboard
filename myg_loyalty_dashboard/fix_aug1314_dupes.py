"""
Remove duplicate rows from azure_invoice_report for Aug 13 and Aug 14.
These dates were loaded twice (from both the 14-08-2026 and 15-08-2026 blob files,
which overlap because blob files for the 15th contain some Aug 13/14 carry-over data).

Fix: Delete ALL rows for Aug 13 and Aug 14, then re-insert from the correct source blob.
"""
import os, io
os.environ.setdefault('DJANGO_SETTINGS_MODULE','myg_loyalty_dashboard.settings')
import django; django.setup()

import pandas as pd
from datetime import datetime
from azure.storage.blob import ContainerClient
from analytics.clickhouse_service import get_ch_client

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

def process_inv(raw_bytes):
    df = pd.read_csv(io.BytesIO(raw_bytes))
    df = df.rename(columns={k: v for k, v in INV_COL_MAP.items() if k in df.columns})
    df.columns = [c.strip().lower().replace(' ', '_') for c in df.columns]
    ch_cols = list(inv_schema.keys())
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
        elif 'Float' in dtype:
            result[col] = safe_float(df[col])
        else:
            result[col] = safe_str(df[col])
    return pd.DataFrame(result)[ch_cols]

# ── Step 1: Delete ALL rows for Aug 13 and Aug 14 ────────────
import time
print('[1] Deleting all rows for Aug 13 and Aug 14 from azure_invoice_report...')
ch.command("ALTER TABLE azure_invoice_report DELETE WHERE toDate(date) IN ('2026-08-13','2026-08-14')")
print('  Waiting 20s for ClickHouse mutation to complete...')
time.sleep(20)

# Verify deletion
r = ch.query("SELECT toDate(date), count() FROM azure_invoice_report WHERE toDate(date) IN ('2026-08-13','2026-08-14') GROUP BY toDate(date)").result_rows
remaining = sum(int(x[1]) for x in r)
print(f'  Rows remaining after delete: {remaining}')
if remaining > 0:
    print('  Still deleting, waiting 15 more seconds...')
    time.sleep(15)

# ── Step 2: Re-insert Aug 13 from blob file 14-08-2026 ───────
print()
print('[2] Re-inserting Aug 13 from invoice_wise_sales_report_14-08-2026 blob...')
blob13 = 'invoice_wise_sales_report/invoice_wise_sales_report_14-08-2026_03_00_02_552474.csv'
raw = cc.get_blob_client(blob13).download_blob().readall()
df  = pd.read_csv(io.BytesIO(raw))
print(f'  Blob rows: {len(df)} | Date sample: {df["Date"].head(3).tolist()}')
df  = process_inv(raw)
df  = df[df['invoice_no'].astype(str).str.strip() != '']
# Filter only Aug 13 rows
# Filter Aug 13 rows using the date column (already datetime objects)
date_series = pd.Series([d.date() if hasattr(d, 'date') else d for d in df['date']])
aug13 = df[date_series.values == datetime(2026,8,13).date()]
print(f'  Aug 13 rows: {len(aug13)}')
if len(aug13) > 0:
    ch.insert('azure_invoice_report', aug13.values.tolist(), column_names=list(inv_schema.keys()))
    print(f'  Inserted {len(aug13):,} rows for Aug 13')

# ── Step 3: Re-insert Aug 14 from blob file 15-08-2026 ───────
print()
print('[3] Re-inserting Aug 14 from invoice_wise_sales_report_15-08-2026 blob...')
blob14 = 'invoice_wise_sales_report/invoice_wise_sales_report_15-08-2026_03_00_03_422021.csv'
raw = cc.get_blob_client(blob14).download_blob().readall()
df  = pd.read_csv(io.BytesIO(raw))
print(f'  Blob rows: {len(df)} | Date sample: {df["Date"].head(3).tolist()}')
df  = process_inv(raw)
df  = df[df['invoice_no'].astype(str).str.strip() != '']
date_series = pd.Series([d.date() if hasattr(d, 'date') else d for d in df['date']])
aug14 = df[date_series.values == datetime(2026,8,14).date()]
print(f'  Aug 14 rows: {len(aug14)}')
if len(aug14) > 0:
    ch.insert('azure_invoice_report', aug14.values.tolist(), column_names=list(inv_schema.keys()))
    print(f'  Inserted {len(aug14):,} rows for Aug 14')

# ── Step 4: Verify ────────────────────────────────────────────
print()
print('[4] Final verification...')
time.sleep(3)
final = ch.query("""
    SELECT toDate(date), count(), countDistinct(invoice_no), sum(invoice_total)
    FROM azure_invoice_report
    WHERE toDate(date) IN ('2026-08-13','2026-08-14')
    GROUP BY toDate(date) ORDER BY toDate(date)
""").result_rows
for r in final:
    print(f'  {r[0]}  rows={int(r[1]):,}  unique_inv={int(r[2]):,}  revenue={float(r[3])/1e7:.2f}Cr')

print()
print('[5] Clearing Django cache...')
from django.core.cache import cache
cache.clear()
print('Cache cleared!')
print()
print('Done! Refresh the Daily New vs Repeat page.')
