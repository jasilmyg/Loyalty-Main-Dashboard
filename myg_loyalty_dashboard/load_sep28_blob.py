"""
load_sep28_blob.py
==================
Loads Sep 28, 2026 data into ClickHouse:
  - azure_sales_report   (from item_wise_sales_report blobs)
  - azure_invoice_report (from invoice_wise_sales_report blobs)

Blob files (generated at 3am next day):
  Sep 28 data -> files dated 29-09-2026

Deduplication: skips rows already existing for those dates.
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
ACCOUNT_URL    = f"https://{ACCOUNT_NAME}.blob.core.windows.net"
container_url  = f"{ACCOUNT_URL}/{CONTAINER_NAME}?{SAS_TOKEN}"
cc             = ContainerClient.from_container_url(container_url)
client         = get_ch_client()

# Sep 28 data -> blob files dated 29-09-2026
BLOB_PAIRS = [
    (
        "item_wise_sales_report/item_wise_sales_report_29-09-2026_03_00_02_542933.csv",
        "invoice_wise_sales_report/invoice_wise_sales_report_29-09-2026_03_00_03_227191.csv",
        "Sep 28, 2026"
    ),
]

SALES_TABLE   = "azure_sales_report"
INVOICE_TABLE = "azure_invoice_report"

SALES_COLS = ['date','invoice_no','branch','item_code','imei_batch',
              'qty','mop','discount','buyback','sold_price','taxable']
INV_COLS   = ['date','time','invoice_no','branch','rbm','bdm',
              'customer_mobile','customer_pincode','customer_gstin',
              'customer_type','sales_staff_code','billing_staff_code',
              'invoice_total','discount','buyback','deductions',
              'exchange','financier_code','financier_name','scheme','loan_amount']
INV_STR    = {'time','invoice_no','branch','rbm','bdm','customer_mobile',
              'customer_pincode','customer_gstin','customer_type',
              'sales_staff_code','billing_staff_code','financier_code','financier_name','scheme'}
INV_FLOAT  = {'invoice_total','discount','buyback','deductions','exchange','loan_amount'}

def safe_str(s):   return s.fillna('').astype(str).str.strip().replace({'nan':'','None':''})
def safe_float(s): return pd.to_numeric(s, errors='coerce').fillna(0.0).astype(float)
def parse_dt(s):   return pd.to_datetime(s, format='%d-%m-%Y', errors='coerce')

def insert_with_dedup(table, cols, df, label):
    dates_in  = df['date'].dt.date.unique().tolist()
    dates_str = ", ".join(f"'{d}'" for d in dates_in)
    existing  = client.query(
        f"SELECT DISTINCT invoice_no FROM {table} WHERE toDate(date) IN ({dates_str})"
    ).result_rows
    existing_invs = {r[0] for r in existing}
    before = len(df)
    df = df[~df['invoice_no'].isin(existing_invs)]
    print(f"    Existing : {len(existing_invs):,} | Skipped : {before-len(df):,} | New : {len(df):,}")
    if len(df) == 0:
        print(f"    [OK] Already up to date for {label}.")
        return 0
    rows = [tuple(r) for r in df.itertuples(index=False, name=None)]
    client.insert(table, rows, column_names=cols)
    print(f"    [OK] Inserted {len(rows):,} rows into {table} for {label}")
    return len(rows)

print("=" * 60)
print("  Loading Sep 28, 2026 Data from Azure Blob")
print("=" * 60)

total_sales = 0
total_inv   = 0

for sales_blob, inv_blob, label in BLOB_PAIRS:
    print(f"\n--- {label} ---")

    # ── Sales (item_wise) ────────────────────────────────────────
    print(f"  [Sales] Downloading {sales_blob.split('/')[-1]} ...")
    data = cc.get_blob_client(sales_blob).download_blob().readall()
    df_s = pd.read_csv(io.BytesIO(data))
    df_s.columns = [c.strip().lower().replace(' ','_') for c in df_s.columns]

    # Map to CH columns
    col_map_s = {
        'date':'date', 'invoice_no':'invoice_no', 'invoice_number':'invoice_no',
        'branch':'branch', 'item_code':'item_code', 'imei_batch_no':'imei_batch',
        'imei/batch_no':'imei_batch', 'imei_batch':'imei_batch',
        'qty':'qty', 'quantity':'qty', 'mop':'mop', 'discount':'discount',
        'buyback':'buyback', 'sold_price':'sold_price', 'taxable':'taxable',
        'taxable_amount':'taxable',
    }
    df_s = df_s.rename(columns={k:v for k,v in col_map_s.items() if k in df_s.columns})
    for c in SALES_COLS:
        if c not in df_s.columns: df_s[c] = ''
    df_s = df_s[SALES_COLS].copy()
    df_s['date']       = parse_dt(df_s['date'].astype(str))
    df_s['invoice_no'] = safe_str(df_s['invoice_no'])
    df_s['branch']     = safe_str(df_s['branch'])
    df_s['item_code']  = safe_str(df_s['item_code'])
    df_s['imei_batch'] = safe_str(df_s['imei_batch'])
    df_s['qty']        = safe_float(df_s['qty'])
    df_s['mop']        = safe_float(df_s['mop'])
    df_s['discount']   = safe_float(df_s['discount'])
    df_s['buyback']    = safe_float(df_s['buyback'])
    df_s['sold_price'] = safe_float(df_s['sold_price'])
    df_s['taxable']    = safe_float(df_s['taxable'])
    df_s = df_s[df_s['date'].notna()]
    print(f"    Rows in CSV: {len(df_s):,}  |  Date range: {df_s['date'].min().date()} to {df_s['date'].max().date()}")
    total_sales += insert_with_dedup(SALES_TABLE, SALES_COLS, df_s, label)

    # ── Invoice (invoice_wise) ───────────────────────────────────
    print(f"  [Invoice] Downloading {inv_blob.split('/')[-1]} ...")
    data = cc.get_blob_client(inv_blob).download_blob().readall()
    df_i = pd.read_csv(io.BytesIO(data))
    df_i.columns = [c.strip().lower().replace(' ','_') for c in df_i.columns]

    col_map_i = {
        'date':'date', 'time':'time', 'invoice_no':'invoice_no', 'invoice_number':'invoice_no',
        'branch':'branch', 'rbm':'rbm', 'bdm':'bdm',
        'customer_mobile':'customer_mobile', 'mobile':'customer_mobile',
        'customer_pincode':'customer_pincode', 'pincode':'customer_pincode',
        'customer_gstin':'customer_gstin', 'gstin':'customer_gstin',
        'customer_type':'customer_type', 'type':'customer_type',
        'sales_staff_code':'sales_staff_code', 'billing_staff_code':'billing_staff_code',
        'invoice_total':'invoice_total', 'total':'invoice_total',
        'discount':'discount', 'buyback':'buyback', 'deductions':'deductions',
        'exchange':'exchange', 'financier_code':'financier_code',
        'financier_name':'financier_name', 'scheme':'scheme', 'loan_amount':'loan_amount',
    }
    df_i = df_i.rename(columns={k:v for k,v in col_map_i.items() if k in df_i.columns})
    for c in INV_COLS:
        if c not in df_i.columns: df_i[c] = ''
    df_i = df_i[INV_COLS].copy()
    df_i['date'] = parse_dt(df_i['date'].astype(str))
    for c in INV_STR:
        df_i[c] = safe_str(df_i[c])
    for c in INV_FLOAT:
        df_i[c] = safe_float(df_i[c])
    df_i = df_i[df_i['date'].notna()]
    print(f"    Rows in CSV: {len(df_i):,}  |  Date range: {df_i['date'].min().date()} to {df_i['date'].max().date()}")
    total_inv += insert_with_dedup(INVOICE_TABLE, INV_COLS, df_i, label)

# ── Final verification ────────────────────────────────────────
print()
print("=" * 60)
print("  Final Verification")
print("=" * 60)
res = client.query("""
    SELECT 'azure_sales_report' AS tbl, max(toDate(date)) AS max_date, count() AS rows FROM azure_sales_report
    UNION ALL
    SELECT 'azure_invoice_report', max(toDate(date)), count() FROM azure_invoice_report
""").result_rows
for r in res:
    print(f"  {r[0]:25s}  max_date={r[1]}  rows={r[2]:,}")

print()
print(f"  Total inserted -> sales: {total_sales:,}  invoice: {total_inv:,}")
print("=" * 60)
print("  DONE. Sep 28, 2026 data loaded successfully!")
print("=" * 60)
