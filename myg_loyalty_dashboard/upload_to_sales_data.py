import os, sys, django
import pandas as pd
import numpy as np

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'myg_loyalty_dashboard.settings')
django.setup()

from analytics.clickhouse_service import get_ch_client

client = get_ch_client()
table_name = "sales_data"

print(f"Uploading to {table_name}")

excel_path = r"C:\Users\jasil_myg\Desktop\myG Loyalty Main Dashboard\myg_loyalty_dashboard\ai_agent\agents\DSR JULY 2026 PART 3.xlsx"
df = pd.read_excel(excel_path)
print(f"Read {len(df)} rows from Excel")

# Column Mapping
COL_MAP = {
    'Slno': 'slno',
    'Date': 'sale_date_text',
    'Time': 'sale_time',
    'Invoice Number': 'invoice_number',
    'Enq/Job No.': 'enq_job_no',
    'RBM': 'rbm',
    'BDM': 'bdm',
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
    'EMI': 'emi',
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

df = df.rename(columns=COL_MAP)

# Extra columns
df['source_file'] = 'DSR JULY 2026 PART 3.xlsx'

# parsed_date
df['parsed_date'] = pd.to_datetime(df['sale_date_text'], format='%d-%m-%Y', errors='coerce')
df['parsed_date'] = df['parsed_date'].dt.date

# Fetch max uid to continue sequence
max_uid_res = client.query(f"SELECT max(uid) FROM {table_name}").result_rows
max_uid = max_uid_res[0][0] if max_uid_res and max_uid_res[0][0] is not None else 0
print(f"Current max UID: {max_uid}")
df['uid'] = range(max_uid + 1, max_uid + 1 + len(df))

# Match schema types
# 'total_value' -> Float64
df['total_value'] = pd.to_numeric(df['total_value'], errors='coerce').fillna(0.0)

# All others (except parsed_date, uid, total_value) should be string
string_cols = [
    'slno', 'sale_date_text', 'sale_time', 'invoice_number', 'enq_job_no',
    'rbm', 'bdm', 'branch', 'staff_code', 'staff', 'customer_name',
    'customer_mobile', 'financier', 'finance', 'delivery_order_no',
    'cash', 'debit_card', 'credit_card', 'benow', 'advance_receipt',
    'bharath_qr', 'paytm_qr', 'pine_labs_qr', 'upi_cashback', 'card_reward',
    'card_cashback', 'gift_voucher', 'approved_credit', 'emi', 'customer_type',
    'exchange', 'discount', 'indirect_discount', 'buyback', 'addition', 'deduction',
    'point_redemption', 'myg_online_coupon', 'source_file'
]

for col in string_cols:
    if col in df.columns:
        # Convert to string and handle NaN and float representation of ints
        df[col] = df[col].fillna('')
        df[col] = df[col].apply(lambda x: str(int(x)) if isinstance(x, float) and x.is_integer() else str(x))
        df[col] = df[col].replace({'nan': '', 'None': '', '<NA>': ''})
        # Ensure it's explicitly string type
        df[col] = df[col].astype(str)
    else:
        df[col] = ''

# Drop unwanted columns
ch_cols = string_cols + ['total_value', 'parsed_date', 'uid']
df = df[ch_cols]

count_before = client.query(f"SELECT count() FROM {table_name}").result_rows[0][0]
print(f"Rows before: {count_before}")

# Insert data
rows_list = [tuple(row) for row in df.itertuples(index=False, name=None)]
client.insert(table_name, rows_list, column_names=ch_cols)

count_after = client.query(f"SELECT count() FROM {table_name}").result_rows[0][0]
print(f"Rows after: {count_after} (+{count_after - count_before})")
