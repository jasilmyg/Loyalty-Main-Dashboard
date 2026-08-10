"""
ch_rebuild_tables.py
=====================
STEP 1: Drop item_wise_sales_data + invoice_wise_sales_data from ClickHouse
STEP 2: Recreate both tables with clean schema
STEP 3: Find & load all source Excel files into correct tables
        - Files with item-level columns -> item_wise_sales_data
        - Files with invoice-level columns -> invoice_wise_sales_data
"""
import os, sys, glob, django
import pandas as pd
import numpy as np

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'myg_loyalty_dashboard.settings')
django.setup()

from analytics.clickhouse_service import get_ch_client

client = get_ch_client()
if not client:
    print("ERROR: Cannot connect to ClickHouse")
    sys.exit(1)

# ── Column signatures to classify Excel files ─────────────────────────────────
# Item-wise: has Item Code + Sold Price / MOP
ITEM_WISE_MUST   = {'item code', 'item_code'}
ITEM_WISE_EXTRA  = {'sold price', 'sold_price', 'mop', 'imei', 'imei/batch', 'taxable'}

# Invoice-wise: has Invoice Total or Customer Bill To
INV_WISE_MUST    = {'invoice total', 'invoice_total', 'customer bill to no', 'customer_bill_to_no'}
INV_WISE_EXTRA   = {'billing staff', 'sales staff', 'financier code', 'loan amount', 'customer type'}

ITEM_TABLE = "item_wise_sales_data"
INV_TABLE  = "invoice_wise_sales_data"

print("=" * 65)
print("  ClickHouse Table Rebuild: Item-Wise & Invoice-Wise")
print("=" * 65)

# ════════════════════════════════════════════════════════════════════
# STEP 1: Drop existing tables
# ════════════════════════════════════════════════════════════════════
print("\n[STEP 1] Dropping existing tables...")

for tbl in [ITEM_TABLE, INV_TABLE]:
    row = client.query(
        f"SELECT count() FROM system.tables WHERE database=currentDatabase() AND name='{tbl}'"
    ).result_rows[0][0]
    if row:
        count = client.query(f"SELECT count() FROM {tbl}").result_rows[0][0]
        client.command(f"DROP TABLE IF EXISTS {tbl}")
        print(f"  Dropped: {tbl}  ({count:,} rows removed)")
    else:
        print(f"  Not found (skipped): {tbl}")

# ════════════════════════════════════════════════════════════════════
# STEP 2: Recreate tables with clean schema
# ════════════════════════════════════════════════════════════════════
print("\n[STEP 2] Recreating tables with clean schema...")

client.command(f"""
CREATE TABLE {ITEM_TABLE} (
    date        String,
    invoice_no  String,
    branch      String,
    item_code   String,
    imei_batch  String,
    qty         Int64,
    mop         Float64,
    discount    Float64,
    buyback     Float64,
    sold_price  Float64,
    taxable     Float64
) ENGINE = SharedMergeTree('/clickhouse/tables/{{uuid}}/{{shard}}', '{{replica}}')
ORDER BY (branch, date, invoice_no)
SETTINGS index_granularity = 8192
""")
print(f"  Created: {ITEM_TABLE}")

client.command(f"""
CREATE TABLE {INV_TABLE} (
    date                      String,
    time                      String,
    invoice_no                String,
    branch                    String,
    rbm                       String,
    bdm                       String,
    customer_bill_to_no       Int64,
    customer_bill_to_pincode  Int64,
    customer_bill_to_gstin    String,
    customer_type             String,
    sales_staff_code          String,
    billing_staff_code        String,
    invoice_total             Float64,
    discount                  Float64,
    buyback                   Float64,
    deductions__indirect_     Float64,
    exchange                  Float64,
    financier_code            String,
    financier_name            String,
    scheme                    String,
    loan_amount               Float64
) ENGINE = SharedMergeTree('/clickhouse/tables/{{uuid}}/{{shard}}', '{{replica}}')
ORDER BY (branch, date, invoice_no)
SETTINGS index_granularity = 8192
""")
print(f"  Created: {INV_TABLE}")

# ════════════════════════════════════════════════════════════════════
# STEP 3: Find all Excel files and classify them
# ════════════════════════════════════════════════════════════════════
print("\n[STEP 3] Scanning for Excel source files...")

SEARCH_DIRS = [
    r"C:\Users\jasil_myg\Desktop\myG Loyalty Main Dashboard\myg_loyalty_dashboard",
    r"C:\Users\jasil_myg\Desktop\myG Loyalty Main Dashboard\project_folder",
    r"C:\Users\jasil_myg\Desktop\myG Loyalty Main Dashboard",
]

ITEM_COLS_MAP = {
    'Date': 'date', 'Invoice No': 'invoice_no', 'Invoice No.': 'invoice_no',
    'Branch': 'branch', 'Item Code': 'item_code', 'IMEI/Batch': 'imei_batch',
    'IMEI/Batch No': 'imei_batch', 'IMEI/Batch No.': 'imei_batch',
    'QTY': 'qty', 'Qty': 'qty',
    'MOP': 'mop', 'Discount': 'discount', 'Buyback': 'buyback',
    'Sold Price': 'sold_price', 'Taxable': 'taxable',
}

INV_COLS_MAP = {
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
    'Deductions__indirect_': 'deductions__indirect_',
    'Exchange': 'exchange',
    'Financier Code': 'financier_code',
    'Financier Name': 'financier_name',
    'Scheme': 'scheme', 'Loan Amount': 'loan_amount',
}

# DSR = regular sales file -> goes to PostgreSQL sales_data, NOT here
DSR_SKIP_PATTERNS = ['dsr ', 'dsr_', 'she start', 'responses', 'store and district',
                     'monthly_analysis', 'marine drive']

def is_dsr_file(name):
    nl = name.lower()
    return any(p in nl for p in DSR_SKIP_PATTERNS)

def classify_excel(path):
    """Read header row and decide which table this file belongs to."""
    name = os.path.basename(path)
    if is_dsr_file(name):
        return 'skip_dsr', []
    try:
        df = pd.read_excel(path, nrows=1, engine='calamine')
        cols_lower = {c.strip().lower() for c in df.columns if c}
        # Must have at least one MUST column
        if cols_lower & ITEM_WISE_MUST:
            return 'item', df.columns.tolist()
        if cols_lower & INV_WISE_MUST:
            return 'invoice', df.columns.tolist()
        # Check extra signatures
        item_score = len(cols_lower & ITEM_WISE_EXTRA)
        inv_score  = len(cols_lower & INV_WISE_EXTRA)
        if item_score >= 2 and item_score >= inv_score:
            return 'item', df.columns.tolist()
        if inv_score >= 2:
            return 'invoice', df.columns.tolist()
        return 'unknown', df.columns.tolist()
    except Exception as e:
        return 'error', [str(e)]

all_xlsx = []
for d in SEARCH_DIRS:
    if os.path.exists(d):
        all_xlsx += glob.glob(os.path.join(d, "*.xlsx"))

seen_paths = set()
item_files    = []
invoice_files = []
unknown_files = []

for path in all_xlsx:
    rp = os.path.realpath(path)
    if rp in seen_paths:
        continue
    seen_paths.add(rp)
    name = os.path.basename(path)
    # Skip tiny test files
    if os.path.getsize(path) < 50_000:
        continue
    kind, cols = classify_excel(path)
    size_mb = os.path.getsize(path) / 1024 / 1024
    print(f"  [{kind.upper():8s}] {name:55s} ({size_mb:.1f} MB)  cols={cols[:5]}")
    if kind == 'item':
        item_files.append(path)
    elif kind == 'invoice':
        invoice_files.append(path)
    else:
        unknown_files.append((path, cols))

print(f"\n  Item-wise files   : {len(item_files)}")
print(f"  Invoice-wise files: {len(invoice_files)}")
print(f"  Unknown files     : {len(unknown_files)}")

if unknown_files:
    print("\n  Unknown file columns (manual check needed):")
    for p, c in unknown_files:
        print(f"    {os.path.basename(p)}: {c}")

# ════════════════════════════════════════════════════════════════════
# STEP 4: Upload item-wise files
# ════════════════════════════════════════════════════════════════════
def upload_item_file(path):
    name = os.path.basename(path)
    print(f"\n  -> Reading: {name}")
    df = pd.read_excel(path, engine='calamine')
    print(f"     Raw rows: {len(df):,}   Cols: {df.columns.tolist()[:8]}")

    # Rename columns
    df = df.rename(columns={k: v for k, v in ITEM_COLS_MAP.items() if k in df.columns})

    # Keep only valid CH columns
    ch_cols = ['date','invoice_no','branch','item_code','imei_batch','qty','mop','discount','buyback','sold_price','taxable']
    available = [c for c in ch_cols if c in df.columns]
    df = df[available].copy()

    # Type coercion
    for c in ['qty']:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors='coerce').fillna(0).astype(int)
    for c in ['mop','discount','buyback','sold_price','taxable']:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors='coerce').fillna(0.0)
    for c in ['date','invoice_no','branch','item_code','imei_batch']:
        if c in df.columns:
            df[c] = df[c].astype(str).str.strip().replace('nan','')

    # Drop rows with no invoice_no
    if 'invoice_no' in df.columns:
        df = df[df['invoice_no'].str.strip() != '']

    # Add missing columns as defaults
    for c in ch_cols:
        if c not in df.columns:
            df[c] = '' if c in ['date','invoice_no','branch','item_code','imei_batch'] else 0

    df = df[ch_cols]

    if len(df) == 0:
        print(f"     No valid rows — skipping.")
        return 0

    rows_list = [tuple(row) for row in df.itertuples(index=False, name=None)]
    client.insert(ITEM_TABLE, rows_list, column_names=ch_cols)
    print(f"     [OK] Inserted {len(df):,} rows into {ITEM_TABLE}")
    return len(df)

def upload_invoice_file(path):
    name = os.path.basename(path)
    print(f"\n  -> Reading: {name}")
    df = pd.read_excel(path, engine='calamine')
    print(f"     Raw rows: {len(df):,}   Cols: {df.columns.tolist()[:8]}")

    # Rename columns
    df = df.rename(columns={k: v for k, v in INV_COLS_MAP.items() if k in df.columns})

    ch_cols = ['date','time','invoice_no','branch','rbm','bdm',
               'customer_bill_to_no','customer_bill_to_pincode','customer_bill_to_gstin',
               'customer_type','sales_staff_code','billing_staff_code',
               'invoice_total','discount','buyback','deductions__indirect_',
               'exchange','financier_code','financier_name','scheme','loan_amount']
    available = [c for c in ch_cols if c in df.columns]
    df = df[available].copy()

    # Type coercion
    for c in ['customer_bill_to_no','customer_bill_to_pincode']:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors='coerce').fillna(0).astype(int)
    for c in ['invoice_total','discount','buyback','deductions__indirect_','exchange','loan_amount']:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors='coerce').fillna(0.0)
    str_cols = ['date','time','invoice_no','branch','rbm','bdm','customer_bill_to_gstin',
                'customer_type','sales_staff_code','billing_staff_code',
                'financier_code','financier_name','scheme']
    for c in str_cols:
        if c in df.columns:
            df[c] = df[c].astype(str).str.strip().replace('nan','')

    if 'invoice_no' in df.columns:
        df = df[df['invoice_no'].str.strip() != '']

    # Add defaults for missing cols
    for c in ch_cols:
        if c not in df.columns:
            if c in str_cols:
                df[c] = ''
            elif c in ['customer_bill_to_no','customer_bill_to_pincode']:
                df[c] = 0
            else:
                df[c] = 0.0

    df = df[ch_cols]

    if len(df) == 0:
        print(f"     No valid rows — skipping.")
        return 0

    rows_list = [tuple(row) for row in df.itertuples(index=False, name=None)]
    client.insert(INV_TABLE, rows_list, column_names=ch_cols)
    print(f"     [OK] Inserted {len(df):,} rows into {INV_TABLE}")
    return len(df)

print("\n[STEP 4] Uploading item-wise files...")
total_item = 0
for f in item_files:
    total_item += upload_item_file(f)

print("\n[STEP 5] Uploading invoice-wise files...")
total_inv = 0
for f in invoice_files:
    total_inv += upload_invoice_file(f)

# ════════════════════════════════════════════════════════════════════
# Final summary
# ════════════════════════════════════════════════════════════════════
print("\n" + "=" * 65)
print("  Final counts:")
ic = client.query(f"SELECT count() FROM {ITEM_TABLE}").result_rows[0][0]
invc = client.query(f"SELECT count() FROM {INV_TABLE}").result_rows[0][0]
print(f"  {ITEM_TABLE:45s}: {ic:>10,} rows")
print(f"  {INV_TABLE:45s}: {invc:>10,} rows")
print("\n  Done! Both tables rebuilt from source Excel files.")
print("=" * 65)
