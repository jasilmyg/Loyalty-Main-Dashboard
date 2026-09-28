"""
load_sep26_27_blob.py
=====================
Loads Sep 26 and Sep 27, 2026 data into ClickHouse:
  - azure_sales_report   (from item_wise_sales_report blobs)
  - azure_invoice_report (from invoice_wise_sales_report blobs)

Blob files (generated at 3am next day):
  Sep 26 data  -> files dated 27-09-2026
  Sep 27 data  -> files dated 28-09-2026

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

# Blob pairs: (item_wise_blob, invoice_wise_blob, actual_data_label)
BLOB_PAIRS = [
    (
        "item_wise_sales_report/item_wise_sales_report_27-09-2026_03_00_02_004281.csv",
        "invoice_wise_sales_report/invoice_wise_sales_report_27-09-2026_03_00_02_773460.csv",
        "Sep 26, 2026"
    ),
    (
        "item_wise_sales_report/item_wise_sales_report_28-09-2026_03_00_01_953769.csv",
        "invoice_wise_sales_report/invoice_wise_sales_report_28-09-2026_03_00_02_587169.csv",
        "Sep 27, 2026"
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
    return len(rows)

total_sales    = 0
total_invoices = 0

for item_blob, inv_blob, label in BLOB_PAIRS:
    print(f"\n{'='*60}")
    print(f"  Processing: {label}")
    print(f"{'='*60}")

    # --- 1. azure_sales_report ---
    fname = item_blob.split('/')[-1]
    print(f"\n  [1/2] {SALES_TABLE} <- {fname}")
    raw = cc.get_blob_client(item_blob).download_blob().readall()
    df  = pd.read_csv(io.BytesIO(raw))
    print(f"    Raw rows  : {len(df):,}  |  Cols: {list(df.columns)}")

    rename = {'Date':'date','Invoice No':'invoice_no','Invoice No.':'invoice_no',
              'Branch':'branch','Item Code':'item_code',
              'IMEI/Batch':'imei_batch','IMEI/Batch No':'imei_batch',
              'Qty':'qty','QTY':'qty','Quantity':'qty',
              'MOP':'mop','Discount':'discount','Buyback':'buyback',
              'Sold Price':'sold_price','Taxable':'taxable'}
    df.rename(columns={k:v for k,v in rename.items() if k in df.columns}, inplace=True)

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
    df['taxable']    = safe_float(df.get('taxable', pd.Series(dtype=float)))
    for c in SALES_COLS:
        if c not in df.columns: df[c] = 0.0
    df = df[SALES_COLS].dropna(subset=['date'])
    df = df[df['invoice_no'].str.strip() != '']

    n = insert_with_dedup(SALES_TABLE, SALES_COLS, df, label)
    total_sales += n
    print(f"    Inserted  : {n:,} rows into {SALES_TABLE}")

    # --- 2. azure_invoice_report ---
    fname2 = inv_blob.split('/')[-1]
    print(f"\n  [2/2] {INVOICE_TABLE} <- {fname2}")
    raw = cc.get_blob_client(inv_blob).download_blob().readall()
    df2 = pd.read_csv(io.BytesIO(raw))
    print(f"    Raw rows  : {len(df2):,}  |  Cols: {list(df2.columns)}")

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
    df2.rename(columns={k:v for k,v in rename2.items() if k in df2.columns}, inplace=True)

    for c in INV_COLS:
        if c not in df2.columns: df2[c] = '' if c in INV_STR else 0.0
    df2['date'] = parse_dt(df2['date'])
    for c in INV_STR:
        if c in df2.columns: df2[c] = safe_str(df2[c])
    for c in INV_FLOAT:
        if c in df2.columns: df2[c] = safe_float(df2[c])
    df2 = df2[INV_COLS].dropna(subset=['date'])
    df2 = df2[df2['invoice_no'].str.strip() != '']

    n2 = insert_with_dedup(INVOICE_TABLE, INV_COLS, df2, label)
    total_invoices += n2
    print(f"    Inserted  : {n2:,} rows into {INVOICE_TABLE}")

# --- Final Verification ---
print(f"\n{'='*60}")
print(f"  SUMMARY")
print(f"{'='*60}")
print(f"  {SALES_TABLE}   : +{total_sales:,} rows inserted")
print(f"  {INVOICE_TABLE} : +{total_invoices:,} rows inserted")

r1 = client.query(f"SELECT max(date), count() FROM {SALES_TABLE}").result_rows[0]
r2 = client.query(f"SELECT max(date), count() FROM {INVOICE_TABLE}").result_rows[0]
print(f"\n  {SALES_TABLE}   : max_date={r1[0]}  total={r1[1]:,}")
print(f"  {INVOICE_TABLE} : max_date={r2[0]}  total={r2[1]:,}")

for d in ['2026-09-26', '2026-09-27']:
    c1 = client.query(f"SELECT count() FROM {SALES_TABLE} WHERE toDate(date)='{d}'").result_rows[0][0]
    c2 = client.query(f"SELECT count() FROM {INVOICE_TABLE} WHERE toDate(date)='{d}'").result_rows[0][0]
    print(f"\n  {d}:  sales={c1:,}  invoices={c2:,}")

print(f"\n{'='*60}")
print(f"  Done! Sep 26-27 data now in ClickHouse.")
print(f"{'='*60}\n")
