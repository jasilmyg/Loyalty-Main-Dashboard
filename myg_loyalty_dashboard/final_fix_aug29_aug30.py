"""
FINAL FIX: Aug 29 and Aug 30 data load
=======================================
ROOT CAUSE: pandas parses dates as IST midnight (e.g. 2026-08-30 00:00:00 IST)
which becomes 2026-08-29 18:30:00 UTC — causing ClickHouse (UTC) to store it as Aug 29.

FIX: Store dates as naive UTC midnight (no timezone shift):
  2026-08-29 00:00:00 UTC  for Aug 29 data
  2026-08-30 00:00:00 UTC  for Aug 30 data

This matches how all previous dates are stored (e.g. 2026-08-27 00:00:00)
"""
import os, sys, io, django, time
import pandas as pd
from datetime import datetime, timezone

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

def parse_dt_utc_midnight(s):
    """
    Parse date string (dd-mm-yyyy) and return as UTC midnight datetime.
    This ensures 2026-08-30 → datetime(2026,8,30,0,0,0) stored in ClickHouse as 2026-08-30.
    """
    parsed = pd.to_datetime(s, format='%d-%m-%Y', errors='coerce')
    # Normalize to midnight and strip any timezone info so it inserts as UTC midnight
    return parsed.dt.normalize().dt.tz_localize(None)

print("=" * 65)
print("  STEP 1: Delete existing Aug 29 and Aug 30 data")
print("=" * 65)
for d in ['2026-08-29', '2026-08-30']:
    s_before = ch.query(f"SELECT count() FROM {SALES_TABLE}   WHERE toDate(date)='{d}'").result_rows[0][0]
    i_before = ch.query(f"SELECT count() FROM {INVOICE_TABLE} WHERE toDate(date)='{d}'").result_rows[0][0]
    print(f"  {d}: sales={s_before:,}  invoices={i_before:,}")
    ch.command(f"ALTER TABLE {SALES_TABLE}   DELETE WHERE toDate(date)='{d}'")
    ch.command(f"ALTER TABLE {INVOICE_TABLE} DELETE WHERE toDate(date)='{d}'")
    # Also delete rows stored as previous-day due to IST→UTC shift (18:30 UTC = midnight IST)
    # Aug 29 IST midnight = Aug 28 18:30:00 UTC → stored as 2026-08-28 in UTC queries but date=2026-08-28 18:30
    # We need to delete raw datetime range
    ch.command(f"ALTER TABLE {SALES_TABLE}   DELETE WHERE date >= '{d} 18:30:00' AND date < '{d} 18:31:00'")
    ch.command(f"ALTER TABLE {INVOICE_TABLE} DELETE WHERE date >= '{d} 18:30:00' AND date < '{d} 18:31:00'")
print("  Waiting 20s for deletes to propagate...")
time.sleep(20)
for d in ['2026-08-29', '2026-08-30']:
    s = ch.query(f"SELECT count() FROM {SALES_TABLE}   WHERE toDate(date)='{d}'").result_rows[0][0]
    i = ch.query(f"SELECT count() FROM {INVOICE_TABLE} WHERE toDate(date)='{d}'").result_rows[0][0]
    print(f"  After delete — {d}: sales={s:,}  invoices={i:,}")

# Also clean up the 18:30 UTC rows
raw_check = ch.query("""
    SELECT toDate(date), date, count() 
    FROM azure_sales_report 
    WHERE date >= '2026-08-28 18:29:00' AND date <= '2026-08-30 18:31:00'
    GROUP BY date, toDate(date)
    ORDER BY date
""").result_rows
if raw_check:
    print("\n  Residual 18:30 UTC rows found:")
    for r in raw_check:
        print(f"    toDate={r[0]}  raw={r[1]}  count={r[2]:,}")
    for r in raw_check:
        d_raw = str(r[1])
        ch.command(f"ALTER TABLE {SALES_TABLE}   DELETE WHERE date = '{d_raw}'")
        ch.command(f"ALTER TABLE {INVOICE_TABLE} DELETE WHERE date = '{d_raw}'")
    print("  Waiting 15s more...")
    time.sleep(15)

# ── LOADS ─────────────────────────────────────────────────────────────────────
LOADS = [
    {
        'label':     'Aug 29, 2026',
        'data_date': '2026-08-29',
        'item_blob': 'item_wise_sales_report/item_wise_sales_report_30-08-2026_03_00_02_812386.csv',
        'inv_blob':  'invoice_wise_sales_report/invoice_wise_sales_report_30-08-2026_03_00_03_821420.csv',
    },
    {
        'label':     'Aug 30, 2026',
        'data_date': '2026-08-30',
        'item_blob': 'item_wise_sales_report/item_wise_sales_report_31-08-2026_03_00_02_751833.csv',
        'inv_blob':  'invoice_wise_sales_report/invoice_wise_sales_report_31-08-2026_03_00_04_179739.csv',
    },
]

for load in LOADS:
    label     = load['label']
    data_date = load['data_date']

    print(f"\n{'='*65}")
    print(f"  Loading: {label}")
    print(f"{'='*65}")

    # ── Sales ─────────────────────────────────────────────────────────────
    print(f"  [1] item_wise blob...")
    raw = container_client.get_blob_client(load['item_blob']).download_blob().readall()
    df  = pd.read_csv(io.BytesIO(raw))
    df.rename(columns={k: v for k, v in RENAME_ITEM.items() if k in df.columns}, inplace=True)
    
    # KEY FIX: parse date as UTC midnight (no IST offset)
    df['date'] = parse_dt_utc_midnight(df['date'])
    
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
    
    print(f"  Dates in data (IST parsed as UTC): {sorted(df['date'].dt.date.unique())}")
    print(f"  Rows: {len(df):,}")
    
    rows = [(r.date.to_pydatetime().replace(tzinfo=None), r.invoice_no, r.branch, r.item_code,
             r.imei_batch, r.qty, r.mop, r.discount, r.buyback, r.sold_price, r.taxable)
            for r in df.itertuples(index=False)]
    ch.insert(SALES_TABLE, rows, column_names=SALES_COLS)
    print(f"  ✅ Inserted {len(rows):,} → {SALES_TABLE}")

    # ── Invoices ──────────────────────────────────────────────────────────
    print(f"  [2] invoice_wise blob...")
    raw = container_client.get_blob_client(load['inv_blob']).download_blob().readall()
    df2 = pd.read_csv(io.BytesIO(raw))
    df2.rename(columns={k: v for k, v in RENAME_INV.items() if k in df2.columns}, inplace=True)
    for c in INV_COLS:
        if c not in df2.columns:
            df2[c] = '' if c in INV_STR else 0.0
    
    # KEY FIX: parse date as UTC midnight
    df2['date'] = parse_dt_utc_midnight(df2['date'])
    for c in INV_STR:
        if c in df2.columns: df2[c] = safe_str(df2[c])
    for c in INV_FLOAT:
        if c in df2.columns: df2[c] = safe_float(df2[c])
    df2 = df2[INV_COLS].dropna(subset=['date'])
    df2 = df2[df2['invoice_no'].str.strip() != '']
    
    print(f"  Dates in data (IST parsed as UTC): {sorted(df2['date'].dt.date.unique())}")
    print(f"  Rows: {len(df2):,}")
    
    rows2 = [(r.date.to_pydatetime().replace(tzinfo=None), r.time, r.invoice_no, r.branch,
              r.rbm, r.bdm, r.customer_mobile, r.customer_pincode, r.customer_gstin,
              r.customer_type, r.sales_staff_code, r.billing_staff_code,
              r.invoice_total, r.discount, r.buyback, r.deductions, r.exchange,
              r.financier_code, r.financier_name, r.scheme, r.loan_amount)
             for r in df2.itertuples(index=False)]
    ch.insert(INVOICE_TABLE, rows2, column_names=INV_COLS)
    print(f"  ✅ Inserted {len(rows2):,} → {INVOICE_TABLE}")

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

# Check raw stored values
print("\n  Raw date values for Aug 29-30:")
raw_vals = ch.query("""
    SELECT date, count() AS cnt
    FROM azure_sales_report
    WHERE toDate(date) IN ('2026-08-29','2026-08-30')
    GROUP BY date ORDER BY date
""").result_rows
for r in raw_vals:
    print(f"    raw={r[0]}  count={r[1]:,}")

ms = ch.query(f"SELECT max(toDate(date)) FROM {SALES_TABLE}").result_rows[0][0]
mi = ch.query(f"SELECT max(toDate(date)) FROM {INVOICE_TABLE}").result_rows[0][0]
print(f"\n  Max date in {SALES_TABLE}   : {ms}")
print(f"  Max date in {INVOICE_TABLE} : {mi}")
print("=" * 65)
