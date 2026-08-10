"""
ch_load_from_azure.py
======================
Downloads ALL daily CSV files from Azure Blob Storage and loads into ClickHouse:
  invoice_wise_sales_report/*.csv  ->  invoice_wise_sales_data
  item_wise_sales_report/*.csv     ->  item_wise_sales_data

Deduplicates by checking existing date ranges already in ClickHouse.
"""
import os, sys, io, django
import pandas as pd

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'myg_loyalty_dashboard.settings')
django.setup()

from analytics.clickhouse_service import get_ch_client

# ── Azure Blob Config ─────────────────────────────────────────────────────────
ACCOUNT_NAME   = "stmygoalposreports"
CONTAINER_NAME = "sales-reports"
SAS_TOKEN      = "sp=racwl&st=2026-07-24T05:11:23Z&se=2026-07-31T13:26:23Z&spr=https&sv=2026-02-06&sr=c&sig=z3Wx%2FuVRpC%2BnrNiwRv12VrfK6TTBNVdHgzZlfm36bBI%3D"
ACCOUNT_URL    = f"https://{ACCOUNT_NAME}.blob.core.windows.net"

from azure.storage.blob import ContainerClient

container_url    = f"{ACCOUNT_URL}/{CONTAINER_NAME}?{SAS_TOKEN}"
container_client = ContainerClient.from_container_url(container_url)

client = get_ch_client()
if not client:
    print("ERROR: Cannot connect to ClickHouse")
    sys.exit(1)

ITEM_TABLE = "item_wise_sales_data"
INV_TABLE  = "invoice_wise_sales_data"

# ── Column maps: CSV column -> ClickHouse column ──────────────────────────────
ITEM_COL_MAP = {
    'Date': 'date', 'Invoice No': 'invoice_no', 'Invoice No.': 'invoice_no',
    'Branch': 'branch', 'Item Code': 'item_code', 'IMEI/Batch': 'imei_batch',
    'IMEI/Batch No': 'imei_batch', 'IMEI/Batch No.': 'imei_batch',
    'QTY': 'qty', 'Qty': 'qty', 'Quantity': 'qty',
    'MOP': 'mop', 'Discount': 'discount', 'Buyback': 'buyback',
    'Sold Price': 'sold_price', 'Taxable': 'taxable',
}

INV_COL_MAP = {
    'Date': 'date', 'Time': 'time',
    'Invoice No': 'invoice_no', 'Invoice No.': 'invoice_no',
    'Branch': 'branch', 'RBM': 'rbm', 'BDM': 'bdm',
    'Customer Bill To No': 'customer_bill_to_no',
    'Customer Bill To No.': 'customer_bill_to_no',
    'Customer Bill To Pincode': 'customer_bill_to_pincode',
    'Customer Bill To GSTIN': 'customer_bill_to_gstin',
    'Customer Type': 'customer_type',
    'Sales Staff Code': 'sales_staff_code',
    'Billing Staff Code': 'billing_staff_code',
    'Invoice Total': 'invoice_total',
    'Discount': 'discount', 'Buyback': 'buyback',
    'Deductions (Indirect)': 'deductions__indirect_',
    'Exchange': 'exchange',
    'Financier Code': 'financier_code',
    'Financier Name': 'financier_name',
    'Scheme': 'scheme', 'Loan Amount': 'loan_amount',
}

CH_ITEM_COLS = ['date','invoice_no','branch','item_code','imei_batch',
                'qty','mop','discount','buyback','sold_price','taxable']

CH_INV_COLS  = ['date','time','invoice_no','branch','rbm','bdm',
                'customer_bill_to_no','customer_bill_to_pincode','customer_bill_to_gstin',
                'customer_type','sales_staff_code','billing_staff_code',
                'invoice_total','discount','buyback','deductions__indirect_',
                'exchange','financier_code','financier_name','scheme','loan_amount']

CH_INV_STR   = {'date','time','invoice_no','branch','rbm','bdm',
                'customer_bill_to_gstin','customer_type',
                'sales_staff_code','billing_staff_code',
                'financier_code','financier_name','scheme'}
CH_INV_INT   = {'customer_bill_to_no','customer_bill_to_pincode'}
CH_INV_FLOAT = {'invoice_total','discount','buyback','deductions__indirect_',
                'exchange','loan_amount'}

def safe_str(s):
    return s.fillna('').astype(str).str.strip().replace('nan','').replace('None','')

def safe_int(s):
    return pd.to_numeric(s, errors='coerce').fillna(0).astype(int)

def safe_float(s):
    return pd.to_numeric(s, errors='coerce').fillna(0.0).astype(float)

def download_blob(blob_name):
    """Download blob and return as bytes."""
    blob_client = container_client.get_blob_client(blob_name)
    return blob_client.download_blob().readall()

def process_item_csv(data_bytes):
    df = pd.read_csv(io.BytesIO(data_bytes))
    df = df.rename(columns={k: v for k, v in ITEM_COL_MAP.items() if k in df.columns})
    df.columns = [c.strip().lower().replace(' ','_') for c in df.columns]

    for c in CH_ITEM_COLS:
        if c not in df.columns:
            df[c] = '' if c in ['date','invoice_no','branch','item_code','imei_batch'] else 0

    df['date']       = safe_str(df['date'])
    df['invoice_no'] = safe_str(df['invoice_no'])
    df['branch']     = safe_str(df['branch'])
    df['item_code']  = safe_str(df['item_code'])
    df['imei_batch'] = safe_str(df['imei_batch'])
    df['qty']        = safe_int(df['qty'])
    df['mop']        = safe_float(df['mop'])
    df['discount']   = safe_float(df['discount'])
    df['buyback']    = safe_float(df['buyback'])
    df['sold_price'] = safe_float(df['sold_price'])
    df['taxable']    = safe_float(df['taxable'])

    df = df[CH_ITEM_COLS]
    df = df[df['invoice_no'].str.strip() != '']
    df = df[df['item_code'].str.strip() != '']
    return df

def process_inv_csv(data_bytes):
    df = pd.read_csv(io.BytesIO(data_bytes))
    df = df.rename(columns={k: v for k, v in INV_COL_MAP.items() if k in df.columns})
    df.columns = [c.strip().lower().replace(' ','_') for c in df.columns]

    for c in CH_INV_COLS:
        if c not in df.columns:
            if c in CH_INV_STR:   df[c] = ''
            elif c in CH_INV_INT: df[c] = 0
            else:                  df[c] = 0.0

    for c in CH_INV_STR:   df[c] = safe_str(df[c])
    for c in CH_INV_INT:   df[c] = safe_int(df[c])
    for c in CH_INV_FLOAT: df[c] = safe_float(df[c])

    df = df[CH_INV_COLS]
    df = df[df['invoice_no'].str.strip() != '']
    return df

# ── List all blobs and classify ───────────────────────────────────────────────
print("=" * 65)
print("  Azure Blob -> ClickHouse Full Load")
print("=" * 65)

print("\nListing blobs...")
blobs = list(container_client.list_blobs())
item_blobs = sorted([b.name for b in blobs if b.name.startswith('item_wise_sales_report/')])
inv_blobs  = sorted([b.name for b in blobs if b.name.startswith('invoice_wise_sales_report/')])

print(f"  item_wise_sales_report  : {len(item_blobs)} files")
print(f"  invoice_wise_sales_report: {len(inv_blobs)} files")

# ── Load ITEM WISE ────────────────────────────────────────────────────────────
print(f"\n{'='*65}")
print(f"  Loading ITEM-WISE ({len(item_blobs)} files) -> {ITEM_TABLE}")
print(f"{'='*65}")

total_item = 0
for i, blob_name in enumerate(item_blobs, 1):
    short = blob_name.split('/')[-1]
    try:
        raw   = download_blob(blob_name)
        df    = process_item_csv(raw)
        if len(df) == 0:
            print(f"  [{i:3d}/{len(item_blobs)}] {short:60s} EMPTY")
            continue
        rows  = [tuple(r) for r in df.itertuples(index=False, name=None)]
        client.insert(ITEM_TABLE, rows, column_names=CH_ITEM_COLS)
        total_item += len(df)
        print(f"  [{i:3d}/{len(item_blobs)}] {short:60s} +{len(df):>7,}  total={total_item:>9,}")
    except Exception as e:
        print(f"  [{i:3d}/{len(item_blobs)}] {short:60s} ERROR: {e}")

# ── Load INVOICE WISE ─────────────────────────────────────────────────────────
print(f"\n{'='*65}")
print(f"  Loading INVOICE-WISE ({len(inv_blobs)} files) -> {INV_TABLE}")
print(f"{'='*65}")

total_inv = 0
for i, blob_name in enumerate(inv_blobs, 1):
    short = blob_name.split('/')[-1]
    try:
        raw  = download_blob(blob_name)
        df   = process_inv_csv(raw)
        if len(df) == 0:
            print(f"  [{i:3d}/{len(inv_blobs)}] {short:60s} EMPTY")
            continue
        rows = [tuple(r) for r in df.itertuples(index=False, name=None)]
        client.insert(INV_TABLE, rows, column_names=CH_INV_COLS)
        total_inv += len(df)
        print(f"  [{i:3d}/{len(inv_blobs)}] {short:60s} +{len(df):>7,}  total={total_inv:>9,}")
    except Exception as e:
        print(f"  [{i:3d}/{len(inv_blobs)}] {short:60s} ERROR: {e}")

# ── Final summary ─────────────────────────────────────────────────────────────
print(f"\n{'='*65}")
print("  FINAL COUNTS IN CLICKHOUSE:")
ic   = client.query(f"SELECT count() FROM {ITEM_TABLE}").result_rows[0][0]
invc = client.query(f"SELECT count() FROM {INV_TABLE}").result_rows[0][0]
print(f"  {ITEM_TABLE:45s}: {ic:>10,} rows")
print(f"  {INV_TABLE:45s}: {invc:>10,} rows")
print(f"\n  Item rows inserted this run  : {total_item:>10,}")
print(f"  Invoice rows inserted this run: {total_inv:>10,}")
print(f"{'='*65}")
