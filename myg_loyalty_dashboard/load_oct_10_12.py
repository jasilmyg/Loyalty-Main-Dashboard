import os, sys, io
import pandas as pd
import django

sys.path.insert(0, '.')
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'myg_loyalty_dashboard.settings')
django.setup()

from analytics.clickhouse_service import get_ch_client
from azure.storage.blob import ContainerClient

ACCOUNT_NAME   = 'stmygoalposreports'
CONTAINER_NAME = 'sales-reports'
SAS_TOKEN      = 'sp=racwl&st=2026-08-11T03:51:43Z&se=2026-12-31T18:29:43Z&spr=https&sv=2026-02-06&sr=c&sig=b5URyZCBQKQU3rwuqxY5z2vqyKNrsDKIPABLQ%2FFyywQ%3D'
ACCOUNT_URL    = f'https://{ACCOUNT_NAME}.blob.core.windows.net'

container_url    = f'{ACCOUNT_URL}/{CONTAINER_NAME}?{SAS_TOKEN}'
container_client = ContainerClient.from_container_url(container_url)
client = get_ch_client()

target_blobs = [
    'invoice_wise_sales_report/invoice_wise_sales_report_11-10-2026_03_00_02_578153.csv',
    'invoice_wise_sales_report/invoice_wise_sales_report_12-10-2026_03_00_02_435181.csv',
    'invoice_wise_sales_report/invoice_wise_sales_report_13-10-2026_03_00_02_869767.csv',
    'item_wise_sales_report/item_wise_sales_report_11-10-2026_03_00_01_648921.csv',
    'item_wise_sales_report/item_wise_sales_report_12-10-2026_03_00_01_799068.csv',
    'item_wise_sales_report/item_wise_sales_report_13-10-2026_03_00_02_201614.csv',
]

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
                'customer_mobile','customer_pincode','customer_gstin',
                'customer_type','sales_staff_code','billing_staff_code',
                'invoice_total','discount','buyback','deductions',
                'exchange','financier_code','financier_name','scheme','loan_amount']

def map_cols(df, col_map):
    rename_dict = {}
    for c in df.columns:
        c_clean = str(c).strip()
        if c_clean in col_map:
            rename_dict[c] = col_map[c_clean]
    df.rename(columns=rename_dict, inplace=True)
    
    # standardize phone number
    if 'customer_bill_to_no' in df.columns:
        df.rename(columns={'customer_bill_to_no': 'customer_mobile'}, inplace=True)
    if 'customer_bill_to_pincode' in df.columns:
        df.rename(columns={'customer_bill_to_pincode': 'customer_pincode'}, inplace=True)
    if 'customer_bill_to_gstin' in df.columns:
        df.rename(columns={'customer_bill_to_gstin': 'customer_gstin'}, inplace=True)
    if 'deductions__indirect_' in df.columns:
        df.rename(columns={'deductions__indirect_': 'deductions'}, inplace=True)

def fill_types(df, is_inv):
    for c in df.columns:
        df[c] = df[c].fillna('')
    df['date'] = pd.to_datetime(df['date'], format='%d-%m-%Y', errors='coerce')
    
    if is_inv:
        float_cols = ['invoice_total','discount','buyback','deductions','exchange','loan_amount']
    else:
        float_cols = ['qty','mop','discount','buyback','sold_price','taxable']
        
    for c in float_cols:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c].astype(str).str.replace(',',''), errors='coerce').fillna(0.0)

for b in target_blobs:
    print(f"Downloading {b}...")
    blob_client = container_client.get_blob_client(b)
    stream = blob_client.download_blob().readall()
    df = pd.read_csv(io.BytesIO(stream))
    
    is_inv = 'invoice_wise' in b
    table_name = 'azure_invoice_report' if is_inv else 'azure_sales_report'
    
    print(f"  Parsed {len(df)} rows. Mapping columns...")
    map_cols(df, INV_COL_MAP if is_inv else ITEM_COL_MAP)
    fill_types(df, is_inv)

    # Duplicate guard: skip if any of this file's dates already exist in the table
    file_dates = sorted({d.strftime('%Y-%m-%d') for d in df['date'].dropna()})
    existing = client.query(
        f"SELECT count() FROM {table_name} WHERE toDate(date) IN ({','.join(repr(d) for d in file_dates)})"
    ).result_rows[0][0]
    if existing:
        print(f"  SKIPPED: {table_name} already has {existing} rows for {file_dates}")
        continue
    
    # Filter only required columns
    cols = CH_INV_COLS if is_inv else CH_ITEM_COLS
    df = df[[c for c in cols if c in df.columns]]
    
    # Ensure all CH cols exist
    for c in cols:
        if c not in df.columns:
            df[c] = 0.0 if c in ['qty','mop','discount','buyback','sold_price','taxable','invoice_total','deductions','exchange','loan_amount'] else ''
            
    # Clean up types for insert
    if is_inv:
        float_cols = ['invoice_total','discount','buyback','deductions','exchange','loan_amount']
    else:
        float_cols = ['qty','mop','discount','buyback','sold_price','taxable']

    for c in cols:
        if c == 'date': continue
        if c in float_cols:
            df[c] = df[c].fillna(0.0).astype(float)
        else:
            # Force all other columns to string to prevent ClickHouse connect AttributeError
            df[c] = df[c].astype(str).replace('nan', '').replace('NaN', '').replace('None', '')
            # Clean up floats that were converted to strings (e.g. 12345.0 -> 12345)
            if c in ['customer_mobile', 'customer_pincode']:
                df[c] = df[c].str.replace(r'\.0$', '', regex=True)

    
    print(f"  Inserting into {table_name}...")
    client.insert_df(table_name, df)
    print(f"  Done.")

print("All loaded.")
