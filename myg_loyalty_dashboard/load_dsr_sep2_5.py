import os, sys, django
import pandas as pd

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'myg_loyalty_dashboard.settings')
sys.path.insert(0, '.')
django.setup()

from analytics.clickhouse_service import get_ch_client
client = get_ch_client()

TABLE       = 'sales_data'
EXCEL       = 'DSR SEP 2-5 2026.xlsx'
SOURCE_FILE = 'DSR SEP 2-5 2026.xlsx'

COL_MAP = {
    'Slno':'Slno','Date':'Date','Time':'Time','Invoice Number':'invoice_number',
    'Enq/Job No.':'enq_job_no','RBM':'RBM','BDM':'BDM','Branch':'branch',
    'Staff Code':'staff_code','Staff':'staff','Customer Name':'customer_name',
    'Customer Mobile':'customer_mobile','Financier':'financier','Finance':'finance',
    'Delivery Order No.':'delivery_order_no','Cash':'cash','Debit Card':'debit_card',
    'Credit Card':'credit_card','Benow':'benow','Advance Receipt':'advance_receipt',
    'Bharath QR':'bharath_qr','Paytm QR':'paytm_qr','Pine Labs QR':'pine_labs_qr',
    'UPI Cashback':'upi_cashback','Card Reward':'card_reward','Card Cashback':'card_cashback',
    'Gift Voucher':'gift_voucher','Approved Credit':'approved_credit','EMI':'EMI',
    'Customer Type':'customer_type','Total Value':'total_value','Exchange':'exchange',
    'Discount':'discount','Indirect Discount':'indirect_discount','Buyback':'buyback',
    'Addition':'addition','Deduction':'deduction',
    'POINT REDUMPTION (DEDUCTION)':'point_redemption',
    'MYG ONLINE COUPON (DEDUCTION)':'myg_online_coupon',
}
STRING_COLS = ['Slno','Date','Time','invoice_number','enq_job_no','RBM','BDM','branch',
    'staff_code','staff','customer_name','customer_mobile','financier','finance',
    'delivery_order_no','cash','debit_card','credit_card','benow','advance_receipt',
    'bharath_qr','paytm_qr','pine_labs_qr','upi_cashback','card_reward','card_cashback',
    'gift_voucher','approved_credit','EMI','customer_type','exchange','discount',
    'indirect_discount','buyback','addition','deduction','point_redemption',
    'myg_online_coupon','source_file']
CH_COLS = STRING_COLS + ['total_value','parsed_date','uid']

print('[1] Reading Excel...')
df = pd.read_excel(EXCEL, engine='calamine')
print(f'    Read {len(df):,} rows')
df = df.rename(columns=COL_MAP)
df['source_file'] = SOURCE_FILE
df['parsed_date'] = pd.to_datetime(df['Date'], format='%d-%m-%Y', errors='coerce').dt.date
df = df[df['parsed_date'].notna()].copy()
print(f'[2] Valid rows: {len(df):,}  Range: {df["parsed_date"].min()} to {df["parsed_date"].max()}')

print('[3] Dedup check...')
min_date = df['parsed_date'].min().strftime('%Y-%m-%d')
max_date = df['parsed_date'].max().strftime('%Y-%m-%d')
query = f"SELECT DISTINCT invoice_number FROM sales_data WHERE parsed_date >= '{min_date}' AND parsed_date <= '{max_date}'"
existing = set(r[0] for r in client.query(query).result_rows)
before = len(df)
df = df[~df['invoice_number'].astype(str).isin(existing)].copy()
print(f'    {before:,} -> {len(df):,} new rows ({before-len(df):,} dupes skipped)')

if len(df) == 0:
    print('All data already exists. Exiting.')
    sys.exit(0)

max_uid = int(client.query('SELECT max(uid) FROM sales_data').result_rows[0][0] or 0)
df['uid'] = range(max_uid + 1, max_uid + 1 + len(df))
print(f'[4] UIDs: {max_uid+1} to {max_uid+len(df)}')

df['total_value'] = pd.to_numeric(df['total_value'], errors='coerce').fillna(0.0)
for col in STRING_COLS:
    if col in df.columns:
        df[col] = df[col].fillna('').astype(str).replace({'nan':'','None':'','<NA>':''})
    else:
        df[col] = ''
df = df[CH_COLS]

c_before = client.query('SELECT count() FROM sales_data').result_rows[0][0]
print(f'[5] Rows BEFORE: {c_before:,}')

BATCH = 50000
done = 0
for i in range(0, len(df), BATCH):
    batch = df.iloc[i:i+BATCH]
    client.insert(TABLE, [tuple(r) for r in batch.itertuples(index=False, name=None)], column_names=CH_COLS)
    done += len(batch)
    print(f'    Inserted batch {i//BATCH+1}: {done:,}/{len(df):,}')

c_after = client.query('SELECT count() FROM sales_data').result_rows[0][0]
print(f'[6] Rows AFTER: {c_after:,}  (+{c_after - c_before:,} new)')

print('[7] Date verification:')
for d in ['2026-09-02','2026-09-03','2026-09-04','2026-09-05']:
    cnt = client.query("SELECT count() FROM sales_data WHERE parsed_date = '" + d + "'").result_rows[0][0]
    status = 'OK' if cnt > 0 else 'MISSING'
    print(f'    [{status}] {d}: {cnt:,} rows')

new_max = client.query('SELECT max(parsed_date) FROM sales_data').result_rows[0][0]
print(f'New max date: {new_max}')
print('DONE')
