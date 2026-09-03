"""
load_aug29_aug30_data.py
========================
Loads data into ClickHouse azure_sales_report & azure_invoice_report:
  - Aug 29, 2026 data  ← from blob files dated 30-08-2026
  - Aug 30, 2026 data  ← from blob files dated 31-08-2026

Blob files:
  item_wise_sales_report/item_wise_sales_report_30-08-2026_03_00_02_812386.csv
  invoice_wise_sales_report/invoice_wise_sales_report_30-08-2026_03_00_03_821420.csv
  item_wise_sales_report/item_wise_sales_report_31-08-2026_03_00_02_751833.csv
  invoice_wise_sales_report/invoice_wise_sales_report_31-08-2026_03_00_04_179739.csv
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

SALES_TABLE   = "azure_sales_report"
INVOICE_TABLE = "azure_invoice_report"

# Blob file → actual data date mapping
LOADS = [
    {
        'label':      'Aug 29, 2026',
        'data_date':  '2026-08-29',
        'item_blob':  'item_wise_sales_report/item_wise_sales_report_30-08-2026_03_00_02_812386.csv',
        'inv_blob':   'invoice_wise_sales_report/invoice_wise_sales_report_30-08-2026_03_00_03_821420.csv',
    },
    {
        'label':      'Aug 30, 2026',
        'data_date':  '2026-08-30',
        'item_blob':  'item_wise_sales_report/item_wise_sales_report_31-08-2026_03_00_02_751833.csv',
        'inv_blob':   'invoice_wise_sales_report/invoice_wise_sales_report_31-08-2026_03_00_04_179739.csv',
    },
]

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

def safe_str(s):   return s.fillna('').astype(str).str.strip().replace({'nan': '', 'None': ''})
def safe_float(s): return pd.to_numeric(s, errors='coerce').fillna(0.0).astype(float)
def parse_dt(s):   return pd.to_datetime(s, format='%d-%m-%Y', errors='coerce')

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
    'Customer Bill To GSTIN': 'customer_gstin',
    'Customer Type': 'customer_type',
    'Sales Staff Code': 'sales_staff_code', 'Billing Staff Code': 'billing_staff_code',
    'Invoice Total': 'invoice_total', 'Discount': 'discount', 'Buyback': 'buyback',
    'Deductions (Indirect)': 'deductions', 'Exchange': 'exchange',
    'Financier Code': 'financier_code', 'Financier Name': 'financier_name',
    'Scheme': 'scheme', 'Loan Amount': 'loan_amount'
}

print("=" * 65)
print("  Loading Aug 29 & Aug 30, 2026 data into ClickHouse")
print("=" * 65)

# Check existing counts before loading
for load in LOADS:
    d = load['data_date']
    s = client.query(f"SELECT count() FROM {SALES_TABLE}   WHERE toDate(date) = '{d}'").result_rows[0][0]
    i = client.query(f"SELECT count() FROM {INVOICE_TABLE} WHERE toDate(date) = '{d}'").result_rows[0][0]
    print(f"\n  {load['label']} — existing rows BEFORE insert:")
    print(f"    {SALES_TABLE}:   {s:,}")
    print(f"    {INVOICE_TABLE}: {i:,}")

print()

# ── Load each date ────────────────────────────────────────────────────────────
for load in LOADS:
    label     = load['label']
    data_date = load['data_date']

    print("=" * 65)
    print(f"  Loading: {label}  (blob files dated {data_date[8:10]+'-'+data_date[5:7]+'-'+data_date[:4]}+1)")
    print("=" * 65)

    # ── Sales (item-wise) ─────────────────────────────────────────────────
    print(f"\n  [1] Downloading item-wise blob for {label}...")
    raw = container_client.get_blob_client(load['item_blob']).download_blob().readall()
    df  = pd.read_csv(io.BytesIO(raw))
    print(f"      Raw rows: {len(df):,}  | Cols: {list(df.columns)}")

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
    print(f"      Dates in data: {sorted(df['date'].dt.date.unique())}")
    print(f"      Rows to insert: {len(df):,}")

    rows = [(r.date.to_pydatetime(), r.invoice_no, r.branch, r.item_code, r.imei_batch,
             r.qty, r.mop, r.discount, r.buyback, r.sold_price, r.taxable)
            for r in df.itertuples(index=False)]
    client.insert(SALES_TABLE, rows, column_names=SALES_COLS)
    print(f"      ✅ Inserted {len(rows):,} rows → {SALES_TABLE}")

    # ── Invoice (invoice-wise) ────────────────────────────────────────────
    print(f"\n  [2] Downloading invoice-wise blob for {label}...")
    raw = container_client.get_blob_client(load['inv_blob']).download_blob().readall()
    df2 = pd.read_csv(io.BytesIO(raw))
    print(f"      Raw rows: {len(df2):,}  | Cols: {list(df2.columns)}")

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
    print(f"      Dates in data: {sorted(df2['date'].dt.date.unique())}")
    print(f"      Rows to insert: {len(df2):,}")

    rows2 = [(r.date.to_pydatetime(), r.time, r.invoice_no, r.branch, r.rbm, r.bdm,
              r.customer_mobile, r.customer_pincode, r.customer_gstin, r.customer_type,
              r.sales_staff_code, r.billing_staff_code, r.invoice_total, r.discount,
              r.buyback, r.deductions, r.exchange, r.financier_code, r.financier_name,
              r.scheme, r.loan_amount)
             for r in df2.itertuples(index=False)]
    client.insert(INVOICE_TABLE, rows2, column_names=INV_COLS)
    print(f"      ✅ Inserted {len(rows2):,} rows → {INVOICE_TABLE}")

# ── Final verification ────────────────────────────────────────────────────────
print("\n" + "=" * 65)
print("  FINAL VERIFICATION")
print("=" * 65)
for d in ['2026-08-29', '2026-08-30']:
    s = client.query(f"SELECT count() FROM {SALES_TABLE}   WHERE toDate(date) = '{d}'").result_rows[0][0]
    i = client.query(f"SELECT count() FROM {INVOICE_TABLE} WHERE toDate(date) = '{d}'").result_rows[0][0]
    print(f"\n  {d}:")
    print(f"    {SALES_TABLE}:   {s:,} rows")
    print(f"    {INVOICE_TABLE}: {i:,} rows")

r1 = client.query(f"SELECT max(toDate(date)) FROM {SALES_TABLE}").result_rows[0][0]
r2 = client.query(f"SELECT max(toDate(date)) FROM {INVOICE_TABLE}").result_rows[0][0]
print(f"\n  Max date in {SALES_TABLE}:   {r1}")
print(f"  Max date in {INVOICE_TABLE}: {r2}")
print("=" * 65)
