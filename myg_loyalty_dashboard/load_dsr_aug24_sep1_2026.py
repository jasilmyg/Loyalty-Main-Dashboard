import os, sys, django
import pandas as pd

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'myg_loyalty_dashboard.settings')
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
django.setup()

from analytics.clickhouse_service import get_ch_client

client = get_ch_client()
if not client:
    print('ERROR: Cannot connect to ClickHouse')
    sys.exit(1)

TABLE = 'sales_data'
EXCEL = r'C:\Users\jasil_myg\Desktop\myG Loyalty Main Dashboard\myg_loyalty_dashboard\DSR Aug 24-1 sep 2026.xlsx'
SOURCE_FILE = 'DSR Aug 24-1 sep 2026.xlsx'

# Actual CH column names (from DESCRIBE sales_data):
# Slno, Date, Time, invoice_number, enq_job_no, RBM, BDM, branch, staff_code,
# staff, customer_name, customer_mobile, financier, finance, delivery_order_no,
# cash, debit_card, credit_card, benow, advance_receipt, bharath_qr, paytm_qr,
# pine_labs_qr, upi_cashback, card_reward, card_cashback, gift_voucher,
# approved_credit, EMI, customer_type, total_value, exchange, discount,
# indirect_discount, buyback, addition, deduction, point_redemption,
# myg_online_coupon, source_file, parsed_date, uid

COL_MAP = {
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
    'MYG ONLINE COUPON (DEDUCTION)': 'myg_online_coupon',
}

STRING_COLS = [
    'Slno', 'Date', 'Time', 'invoice_number', 'enq_job_no',
    'RBM', 'BDM', 'branch', 'staff_code', 'staff', 'customer_name',
    'customer_mobile', 'financier', 'finance', 'delivery_order_no',
    'cash', 'debit_card', 'credit_card', 'benow', 'advance_receipt',
    'bharath_qr', 'paytm_qr', 'pine_labs_qr', 'upi_cashback', 'card_reward',
    'card_cashback', 'gift_voucher', 'approved_credit', 'EMI', 'customer_type',
    'exchange', 'discount', 'indirect_discount', 'buyback', 'addition', 'deduction',
    'point_redemption', 'myg_online_coupon', 'source_file'
]
CH_COLS = STRING_COLS + ['total_value', 'parsed_date', 'uid']

print('=' * 65)
print('  Loading: ' + SOURCE_FILE)
print('  Target : ' + TABLE)
print('=' * 65)

print('\n[1] Reading Excel...')
df = pd.read_excel(EXCEL, engine='calamine')
print('    Read ' + str(len(df)) + ' rows')

df = df.rename(columns=COL_MAP)
df['source_file'] = SOURCE_FILE

# parsed_date from Date column (DD-MM-YYYY string)
df['parsed_date'] = pd.to_datetime(df['Date'], format='%d-%m-%Y', errors='coerce').dt.date
valid = df['parsed_date'].notna()
df = df[valid].copy()
print('[2] Valid rows: ' + str(len(df)) + ' | Range: ' + str(df['parsed_date'].min()) + ' to ' + str(df['parsed_date'].max()))

print('\n[3] Dedup check...')
existing_res = client.query('SELECT DISTINCT invoice_number FROM ' + TABLE)
existing_invoices = set(r[0] for r in existing_res.result_rows)
before = len(df)
df = df[~df['invoice_number'].astype(str).isin(existing_invoices)].copy()
after = len(df)
print('    ' + str(before) + ' -> ' + str(after) + ' new rows (' + str(before-after) + ' dupes skipped)')

if after == 0:
    print('All data already in DB. Exiting.')
    sys.exit(0)

max_uid = int(client.query('SELECT max(uid) FROM ' + TABLE).result_rows[0][0] or 0)
df['uid'] = range(max_uid + 1, max_uid + 1 + len(df))
print('[4] UIDs: ' + str(max_uid+1) + ' to ' + str(max_uid+len(df)))

df['total_value'] = pd.to_numeric(df['total_value'], errors='coerce').fillna(0.0)
for col in STRING_COLS:
    if col in df.columns:
        df[col] = df[col].fillna('').apply(lambda x: str(int(x)) if isinstance(x, float) and x == x and x.is_integer() else str(x)).replace({'nan':'','None':'','<NA>':''}).astype(str)
    else:
        df[col] = ''
df = df[CH_COLS]

c_before = client.query('SELECT count() FROM ' + TABLE).result_rows[0][0]
print('\n[5] Rows BEFORE: ' + str(c_before))

BATCH = 50000
done = 0
for i in range(0, len(df), BATCH):
    batch = df.iloc[i:i+BATCH]
    client.insert(TABLE, [tuple(r) for r in batch.itertuples(index=False, name=None)], column_names=CH_COLS)
    done += len(batch)
    print('    Batch ' + str(i//BATCH+1) + ': ' + str(done) + '/' + str(len(df)))

c_after = client.query('SELECT count() FROM ' + TABLE).result_rows[0][0]
print('\n[6] Rows AFTER: ' + str(c_after) + ' (+' + str(c_after - c_before) + ')')

print('\n[7] Date verification:')
for d in ['2026-08-24','2026-08-25','2026-08-26','2026-08-27','2026-08-28','2026-08-29','2026-08-30','2026-08-31','2026-09-01']:
    cnt = client.query("SELECT count() FROM sales_data WHERE parsed_date = '" + d + "'").result_rows[0][0]
    print('    [' + ('OK' if cnt > 0 else 'MISSING') + '] ' + d + ': ' + str(cnt))

new_max = client.query('SELECT max(parsed_date) FROM ' + TABLE).result_rows[0][0]
print('\nNew max date: ' + str(new_max))
print('DONE')
