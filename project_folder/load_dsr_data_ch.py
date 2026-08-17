import pandas as pd
import numpy as np
import sys
import os

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'myg_loyalty_dashboard')))
from analytics.clickhouse_service import get_ch_client

print('Reading Excel file...')
file_path = 'C:\\Users\\jasil_myg\\Desktop\\myG Loyalty Main Dashboard\\project_folder\\DSR 11-14 AUG 2026.xlsx'
df = pd.read_excel(file_path)

print(f'Original rows: {len(df)}')

# Filter Invoice Number (remove SMC and EI)
df = df[~df['Invoice Number'].astype(str).str.contains('SMC|EI', na=False, case=False)]
print(f'Rows after Invoice filter: {len(df)}')

# Filter Branch (remove HEAD OFFICE and UG SMART CHOICE)
df = df[~df['Branch'].astype(str).str.strip().str.upper().isin(['HEAD OFFICE', 'UG SMART CHOICE'])]
print(f'Rows after Branch filter: {len(df)}')

col_map = {
    'Slno': 'Slno',
    'Date': 'Date',
    'Time': 'Time',
    'Invoice Number': 'invoice_number',
    'Enq/Job No.': 'enq_job_no',
    'RBM': 'RBM',
    'BDM': 'BDM',
    'Branch': 'branch',
    'Staff Code': 'staff_code',
    'Staff': 'staff',
    'Customer Name': 'customer_name',
    'Customer Mobile': 'customer_mobile',
    'Financier': 'financier',
    'Finance': 'finance',
    'Delivery Order No.': 'delivery_order_no',
    'Cash': 'cash',
    'Debit Card': 'debit_card',
    'Credit Card': 'credit_card',
    'Benow': 'benow',
    'Advance Receipt': 'advance_receipt',
    'Bharath QR': 'bharath_qr',
    'Paytm QR': 'paytm_qr',
    'Pine Labs QR': 'pine_labs_qr',
    'UPI Cashback': 'upi_cashback',
    'Card Reward': 'card_reward',
    'Card Cashback': 'card_cashback',
    'Gift Voucher': 'gift_voucher',
    'Approved Credit': 'approved_credit',
    'EMI': 'EMI',
    'Customer Type': 'customer_type',
    'Total Value': 'total_value',
    'Exchange': 'exchange',
    'Discount': 'discount',
    'Indirect Discount': 'indirect_discount',
    'Buyback': 'buyback',
    'Addition': 'addition',
    'Deduction': 'deduction',
    'POINT REDUMPTION (DEDUCTION)': 'point_redemption',
    'MYG ONLINE COUPON (DEDUCTION)': 'myg_online_coupon'
}

df = df.rename(columns=col_map)
df['source_file'] = 'DSR 11-14 AUG 2026.xlsx'

# Ensure correct data types
# Float column
df['total_value'] = pd.to_numeric(df['total_value'], errors='coerce').fillna(0.0).astype(float)

# String columns (fill NA with '')
str_cols = [c for c in df.columns if c not in ['total_value', 'parsed_date', 'uid']]
for c in str_cols:
    df[c] = df[c].fillna('').astype(str).replace('nan', '').replace('None', '')

# Date parsing
df['parsed_date'] = pd.to_datetime(df['Date'], dayfirst=True, errors='coerce')
# Replace NaT with '1970-01-01' for ClickHouse compatibility
df['parsed_date'] = df['parsed_date'].fillna(pd.Timestamp('1970-01-01'))
df['uid'] = np.random.randint(1000000, 99999999, size=len(df), dtype=np.int64)

# Reorder to match ClickHouse exact schema or insert specific columns
columns_to_insert = [
    'Slno', 'Date', 'Time', 'invoice_number', 'enq_job_no', 'RBM', 'BDM', 'branch', 
    'staff_code', 'staff', 'customer_name', 'customer_mobile', 'financier', 'finance', 
    'delivery_order_no', 'cash', 'debit_card', 'credit_card', 'benow', 'advance_receipt', 
    'bharath_qr', 'paytm_qr', 'pine_labs_qr', 'upi_cashback', 'card_reward', 'card_cashback', 
    'gift_voucher', 'approved_credit', 'EMI', 'customer_type', 'total_value', 'exchange', 
    'discount', 'indirect_discount', 'buyback', 'addition', 'deduction', 'point_redemption', 
    'myg_online_coupon', 'source_file', 'parsed_date', 'uid'
]

df_insert = df[columns_to_insert]

print('Connecting to ClickHouse...')
c = get_ch_client()

print('Inserting data...')
# clickhouse_connect insert_df
c.insert_df('sales_data', df_insert)

print('Done!')
