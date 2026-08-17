"""
Upload DSR 15-16 AUG.xlsx to ClickHouse sales_data table.
Filters applied:
  - Remove rows where Invoice Number contains 'SMC' or 'EI'
  - Remove rows where Branch is 'HEAD OFFICE' or 'UG SMART CHOICE'
"""

import pandas as pd
import numpy as np
from analytics.clickhouse_service import get_ch_client
from datetime import datetime

EXCEL_PATH = r'C:\Users\jasil_myg\Desktop\myG Loyalty Main Dashboard\myg_loyalty_dashboard\DSR 15-16 AUG.xlsx'

print("=" * 60)
print("DSR Upload Script — 15-16 AUG 2026")
print("=" * 60)

# ── Load Excel ──────────────────────────────────────────────
print("\n[1] Loading Excel file...")
df = pd.read_excel(EXCEL_PATH, dtype=str)
df.columns = df.columns.str.strip()
print(f"    Loaded {len(df):,} rows")

# ── Filter: Remove SMC / EI in Invoice Number ────────────────
inv_before = len(df)
df = df[~df['Invoice Number'].str.contains('SMC|EI', case=False, na=False)]
print(f"\n[2] Removed SMC/EI invoices: {inv_before - len(df):,} rows removed -> {len(df):,} remaining")

# ── Filter: Remove HEAD OFFICE / UG SMART CHOICE branches ────
branch_before = len(df)
exclude_branches = ['HEAD OFFICE', 'UG SMART CHOICE']
df = df[~df['Branch'].str.upper().str.strip().isin([b.upper() for b in exclude_branches])]
print(f"[3] Removed HEAD OFFICE / UG SMART CHOICE: {branch_before - len(df):,} rows removed -> {len(df):,} remaining")

# ── Column mapping (Excel → ClickHouse) ──────────────────────
col_map = {
    'Slno':                           'Slno',
    'Date':                           'Date',
    'Time':                           'Time',
    'Invoice Number':                 'invoice_number',
    'Enq/Job No.':                    'enq_job_no',
    'RBM':                            'RBM',
    'BDM':                            'BDM',
    'Branch':                         'branch',
    'Staff Code':                     'staff_code',
    'Staff':                          'staff',
    'Customer Name':                  'customer_name',
    'Customer Mobile':                'customer_mobile',
    'Financier':                      'financier',
    'Finance':                        'finance',
    'Delivery Order No.':             'delivery_order_no',
    'Cash':                           'cash',
    'Debit Card':                     'debit_card',
    'Credit Card':                    'credit_card',
    'Benow':                          'benow',
    'Advance Receipt':                'advance_receipt',
    'Bharath QR':                     'bharath_qr',
    'Paytm QR':                       'paytm_qr',
    'Pine Labs QR':                   'pine_labs_qr',
    'UPI Cashback':                   'upi_cashback',
    'Card Reward':                    'card_reward',
    'Card Cashback':                  'card_cashback',
    'Gift Voucher':                   'gift_voucher',
    'Approved Credit':                'approved_credit',
    'EMI':                            'EMI',
    'Customer Type':                  'customer_type',
    'Total Value':                    'total_value',
    'Exchange':                       'exchange',
    'Discount':                       'discount',
    'Indirect Discount':              'indirect_discount',
    'Buyback':                        'buyback',
    'Addition':                       'addition',
    'Deduction':                      'deduction',
    'POINT REDUMPTION (DEDUCTION)':   'point_redemption',
    'MYG ONLINE COUPON (DEDUCTION)':  'myg_online_coupon',
}

# ── Rename columns ────────────────────────────────────────────
df = df.rename(columns=col_map)

# ── Add metadata columns ──────────────────────────────────────
df['source_file'] = 'DSR 15-16 AUG.xlsx'

# parsed_date from Date column (DD-MM-YYYY format)
def parse_date(s):
    try:
        return datetime.strptime(str(s).strip(), '%d-%m-%Y').date()
    except:
        return None

df['parsed_date'] = df['Date'].apply(parse_date)

# uid = row hash for deduplication
df['uid'] = pd.util.hash_pandas_object(df[['Date', 'invoice_number', 'Slno']], index=False).astype('int64')

# total_value as float
df['total_value'] = pd.to_numeric(df['total_value'], errors='coerce').fillna(0.0)

# All other columns to string, fill NaN with empty string
str_cols = [c for c in df.columns if c not in ('total_value', 'parsed_date', 'uid')]
df[str_cols] = df[str_cols].fillna('').astype(str)

# Drop rows where parsed_date failed
bad_dates = df['parsed_date'].isna().sum()
if bad_dates > 0:
    print(f"[!] Dropping {bad_dates} rows with unparseable dates")
    df = df.dropna(subset=['parsed_date'])

print(f"\n[4] Final rows to insert: {len(df):,}")
print(f"    Date range: {df['parsed_date'].min()} -> {df['parsed_date'].max()}")
print(f"    Branches: {df['branch'].nunique()} unique")

# ── Insert to ClickHouse ──────────────────────────────────────
print("\n[5] Connecting to ClickHouse and inserting...")
ch = get_ch_client()

CH_COLS = [
    'Slno', 'Date', 'Time', 'invoice_number', 'enq_job_no', 'RBM', 'BDM',
    'branch', 'staff_code', 'staff', 'customer_name', 'customer_mobile',
    'financier', 'finance', 'delivery_order_no', 'cash', 'debit_card',
    'credit_card', 'benow', 'advance_receipt', 'bharath_qr', 'paytm_qr',
    'pine_labs_qr', 'upi_cashback', 'card_reward', 'card_cashback',
    'gift_voucher', 'approved_credit', 'EMI', 'customer_type', 'total_value',
    'exchange', 'discount', 'indirect_discount', 'buyback', 'addition',
    'deduction', 'point_redemption', 'myg_online_coupon', 'source_file',
    'parsed_date', 'uid'
]

insert_df = df[CH_COLS]

# Insert in batches of 10,000
BATCH = 10000
total = len(insert_df)
inserted = 0

for i in range(0, total, BATCH):
    batch = insert_df.iloc[i:i+BATCH]
    data = batch.values.tolist()
    ch.insert('sales_data', data, column_names=CH_COLS)
    inserted += len(batch)
    print(f"    Inserted {inserted:,} / {total:,}", end='\r')

print(f"\n\n[6] Done! {inserted:,} rows inserted into sales_data.")

# ── Verify ────────────────────────────────────────────────────
verify = ch.query("""
    SELECT count(*), max(parseDateTimeBestEffort(Date))
    FROM sales_data
    WHERE source_file = 'DSR 15-16 AUG.xlsx'
""").result_rows[0]
print(f"[7] Verification: {int(verify[0]):,} rows from this file, last date = {verify[1]}")
print("\n✓ Upload complete!")
