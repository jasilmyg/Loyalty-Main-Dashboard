"""
load_dsr_july_30_31.py
=======================
Loads DSR JULY PART 30-31.xlsx into ClickHouse sales_data table.

Filters applied (as instructed):
  1. Remove rows where Invoice Number contains 'SMC' or 'EI'
  2. Remove rows where Branch is 'HEAD OFFICE' or 'UG SMART CHOICE'
  3. Deduplicates against existing data (won't re-insert same invoice+date)

After loading: clears AI pipeline cache so dashboard reflects latest data.
"""

import os, sys, django
import pandas as pd
import numpy as np
from datetime import datetime, date

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'myg_loyalty_dashboard.settings')
django.setup()

from analytics.clickhouse_service import get_ch_client

EXCEL_PATH = r'C:\Users\jasil_myg\Desktop\myG Loyalty Main Dashboard\project_folder\DSR JULY PART 30-31.xlsx'
SOURCE_FILE = 'DSR JULY PART 30-31.xlsx'
TABLE       = 'sales_data'

# â”€â”€ Column mapping: Excel â†’ ClickHouse â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
COL_MAP = {
    'Slno':                          'slno',
    'Date':                          'sale_date_text',
    'Time':                          'sale_time',
    'Invoice Number':                'invoice_number',
    'Enq/Job No.':                   'enq_job_no',
    'RBM':                           'rbm',
    'BDM':                           'bdm',
    'Branch':                        'branch',
    'Staff Code':                    'staff_code',
    'Staff':                         'staff',
    'Customer Name':                 'customer_name',
    'Customer Mobile':               'customer_mobile',
    'Financier':                     'financier',
    'Finance':                       'finance',
    'Delivery Order No.':            'delivery_order_no',
    'Cash':                          'cash',
    'Debit Card':                    'debit_card',
    'Credit Card':                   'credit_card',
    'Benow':                         'benow',
    'Advance Receipt':               'advance_receipt',
    'Bharath QR':                    'bharath_qr',
    'Paytm QR':                      'paytm_qr',
    'Pine Labs QR':                  'pine_labs_qr',
    'UPI Cashback':                  'upi_cashback',
    'Card Reward':                   'card_reward',
    'Card Cashback':                 'card_cashback',
    'Gift Voucher':                  'gift_voucher',
    'Approved Credit':               'approved_credit',
    'EMI':                           'emi',
    'Customer Type':                 'customer_type',
    'Total Value':                   'total_value',
    'Exchange':                      'exchange',
    'Discount':                      'discount',
    'Indirect Discount':             'indirect_discount',
    'Buyback':                       'buyback',
    'Addition':                      'addition',
    'Deduction':                     'deduction',
    'POINT REDUMPTION (DEDUCTION)':  'point_redemption',
    # 'PNWLA 500 (DEDUCTION)' â€” no CH column, dropped
    'MYG ONLINE COUPON (DEDUCTION)': 'myg_online_coupon',
}

STRING_COLS = [
    'slno','sale_date_text','sale_time','invoice_number','enq_job_no',
    'rbm','bdm','branch','staff_code','staff','customer_name','customer_mobile',
    'financier','finance','delivery_order_no','cash','debit_card','credit_card',
    'benow','advance_receipt','bharath_qr','paytm_qr','pine_labs_qr',
    'upi_cashback','card_reward','card_cashback','gift_voucher','approved_credit',
    'emi','customer_type','exchange','discount','indirect_discount','buyback',
    'addition','deduction','point_redemption','myg_online_coupon','source_file',
]

def parse_date(val):
    if pd.isna(val):
        return date(1970, 1, 1)
    s = str(val).strip()
    for fmt in ('%d-%m-%Y', '%Y-%m-%d', '%d/%m/%Y', '%m/%d/%Y'):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            pass
    return date(1970, 1, 1)

def main():
    client = get_ch_client()

    # â”€â”€ 1. Read Excel â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    print(f"\n{'='*60}")
    print(f"  Loading: {EXCEL_PATH}")
    print(f"{'='*60}")

    df = pd.read_excel(EXCEL_PATH, dtype=str)
    print(f"  Raw rows read        : {len(df):,}")

    # â”€â”€ 2. Apply Filters â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    # Filter A: Remove rows where Invoice Number contains SMC or EI
    inv_col = 'Invoice Number'
    before = len(df)
    mask_smc_ei = df[inv_col].str.upper().str.contains(r'SMC|/EI/|-EI-|\bEI\b', na=False, regex=True)
    df = df[~mask_smc_ei]
    print(f"  Removed (SMC/EI inv) : {before - len(df):,}  â†’ {len(df):,} remaining")

    # Filter B: Remove HEAD OFFICE and UG SMART CHOICE branches
    before = len(df)
    excluded_branches = ['HEAD OFFICE', 'UG SMART CHOICE']
    mask_branch = df['Branch'].str.strip().str.upper().isin([b.upper() for b in excluded_branches])
    df = df[~mask_branch]
    print(f"  Removed (HO/UGSC)    : {before - len(df):,}  â†’ {len(df):,} remaining")

    # â”€â”€ 3. Rename columns â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    df = df.rename(columns=COL_MAP)
    # Drop unmapped Excel columns (e.g. PNWLA 500)
    mapped_ch_cols = list(COL_MAP.values())
    df = df[[c for c in mapped_ch_cols if c in df.columns]]

    # â”€â”€ 4. Add derived columns â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    df['source_file'] = SOURCE_FILE
    df['parsed_date'] = df['sale_date_text'].apply(parse_date)

    # uid = hash of invoice_number + sale_date_text (stable dedup key)
    df['uid'] = df.apply(
        lambda r: hash(str(r.get('invoice_number','')) + str(r.get('sale_date_text',''))) & 0x7FFFFFFFFFFFFFFF,
        axis=1
    )

    # â”€â”€ 5. Coerce types â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    for col in STRING_COLS:
        if col in df.columns:
            df[col] = df[col].fillna('').astype(str).str.strip()

    df['total_value'] = pd.to_numeric(df.get('total_value', 0), errors='coerce').fillna(0.0)
    df['uid'] = df['uid'].astype('int64')

    # â”€â”€ 6. Deduplication â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    dates_in_file = df['parsed_date'].unique().tolist()
    dates_str = ", ".join(f"'{d}'" for d in dates_in_file)

    print(f"\n  Dates in file        : {[str(d) for d in dates_in_file]}")

    existing_q = f"SELECT DISTINCT invoice_number FROM {TABLE} WHERE parsed_date IN ({dates_str})"
    existing_result = client.query(existing_q)
    existing_invoices = {r[0] for r in existing_result.result_rows}
    print(f"  Existing invoices    : {len(existing_invoices):,} (for these dates)")

    before = len(df)
    df = df[~df['invoice_number'].isin(existing_invoices)]
    print(f"  Duplicates skipped   : {before - len(df):,}")
    print(f"  NEW rows to insert   : {len(df):,}")

    if len(df) == 0:
        print("\n  âœ… Nothing new to insert â€” all rows already exist in ClickHouse.")
        return

    # â”€â”€ 7. Show branch distribution â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    print("\n  Branch distribution of new rows:")
    for branch, count in df['branch'].value_counts().items():
        print(f"    {branch:<35} {count:>5,}")

    # â”€â”€ 8. Insert into ClickHouse â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    row_count_before = client.query(f"SELECT count() FROM {TABLE}").result_rows[0][0]

    # Build column list in CH table order
    ch_columns = [
        'slno','sale_date_text','sale_time','invoice_number','enq_job_no',
        'rbm','bdm','branch','staff_code','staff','customer_name','customer_mobile',
        'financier','finance','delivery_order_no','cash','debit_card','credit_card',
        'benow','advance_receipt','bharath_qr','paytm_qr','pine_labs_qr',
        'upi_cashback','card_reward','card_cashback','gift_voucher','approved_credit',
        'emi','customer_type','total_value','exchange','discount','indirect_discount',
        'buyback','addition','deduction','point_redemption','myg_online_coupon',
        'source_file','parsed_date','uid'
    ]

    # Ensure all columns exist in df
    for col in ch_columns:
        if col not in df.columns:
            df[col] = '' if col in STRING_COLS else 0

    data_to_insert = df[ch_columns].values.tolist()

    print(f"\n  Inserting {len(data_to_insert):,} rows into [{TABLE}]...")
    client.insert(TABLE, data_to_insert, column_names=ch_columns)

    row_count_after = client.query(f"SELECT count() FROM {TABLE}").result_rows[0][0]
    print(f"\n  âœ… INSERT complete!")
    print(f"     Before : {row_count_before:,}")
    print(f"     After  : {row_count_after:,}")
    print(f"     Added  : {row_count_after - row_count_before:,}")

    # â”€â”€ 9. Clear AI pipeline cache â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    cache_path = os.path.join('analytics', 'model_cache', 'campaign_intelligence.json')
    if os.path.exists(cache_path):
        os.remove(cache_path)
        print(f"\n  ðŸ—‘ï¸  AI cache cleared â†’ models will rebuild on next visit.")

    print(f"\n{'='*60}")
    print(f"  ðŸŽ‰ Done! Dashboard will reflect July 30-31 data now.")
    print(f"{'='*60}\n")

if __name__ == '__main__':
    main()
