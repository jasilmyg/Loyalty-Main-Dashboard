import os, django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'myg_loyalty_dashboard.settings')
django.setup()
from django.db import connection
from analytics.clickhouse_service import get_ch_client
import pandas as pd
from sqlalchemy import create_engine

# Setup Postgres connection using SQLAlchemy for easy dataframe insertion
from django.conf import settings
db = settings.DATABASES['default']
engine = create_engine(f"postgresql://{db['USER']}:{db['PASSWORD']}@{db['HOST']}:{db['PORT']}/{db['NAME']}")

ch = get_ch_client()

print("Fetching Aug 19 data from ClickHouse azure_invoice_report...")
inv_data = ch.query("""
    SELECT 
        date AS "Date",
        time AS "Time",
        invoice_no AS "Invoice Number",
        branch AS "Branch",
        rbm AS "RBM",
        bdm AS "BDM",
        sales_staff_code AS "Staff Code",
        customer_mobile AS "Customer Mobile",
        financier_name AS "Financier",
        toString(loan_amount) AS "Finance",
        invoice_total AS "Total Value",
        toDate(date) AS parsed_date
    FROM azure_invoice_report 
    WHERE toDate(date) = '2026-08-19'
""").result_rows

if inv_data:
    cols = ["Date", "Time", "Invoice Number", "Branch", "RBM", "BDM", "Staff Code", "Customer Mobile", "Financier", "Finance", "Total Value", "parsed_date"]
    df_inv = pd.DataFrame(inv_data, columns=cols)
    
    # Add dummy columns for Postgres schema compatibility
    for c in ['Slno', 'Customer Name', 'Cash', 'Staff', 'Enq/Job No.', 'Delivery Order No.', 'Debit Card', 'Credit Card', 'Benow', 'Advance Receipt', 'Bharath QR', 'Paytm QR', 'Pine Labs QR', 'UPI Cashback', 'Card Reward', 'Card Cashback', 'Gift Voucher', 'Approved Credit', 'EMI', 'Customer Type', 'Exchange', 'Discount', 'Indirect Discount', 'Buyback', 'Addition', 'POINT REDUMPTION (DEDUCTION)', 'MYG ONLINE COUPON (DEDUCTION)', 'source_file', 'Deduction']:
        df_inv[c] = ''
    
    print(f"Found {len(df_inv)} invoice rows. Inserting into Postgres sales_data...")
    # Delete existing Aug 19 data first just to be safe
    with connection.cursor() as cur:
        cur.execute("DELETE FROM sales_data WHERE parsed_date = '2026-08-19'")
    
    df_inv.to_sql('sales_data', con=engine, if_exists='append', index=False, chunksize=3000)
    print("Inserted successfully.")
else:
    print("No invoice data found for Aug 19.")

print("Fetching Aug 19 data from ClickHouse azure_sales_report...")
sales_data = ch.query("""
    SELECT 
        toDate(date) AS date,
        invoice_no AS invoice_number,
        branch,
        item_code AS product,
        qty,
        sold_price
    FROM azure_sales_report 
    WHERE toDate(date) = '2026-08-19'
""").result_rows

if sales_data:
    cols = ['date', 'invoice_number', 'branch', 'product', 'qty', 'sold_price']
    df_sales = pd.DataFrame(sales_data, columns=cols)
    for c in ['category', 'brand']:
        df_sales[c] = ''
    
    print(f"Found {len(df_sales)} sales rows. Inserting into Postgres analytics_productsale...")
    with connection.cursor() as cur:
        cur.execute("DELETE FROM analytics_productsale WHERE date = '2026-08-19'")
    
    df_sales.to_sql('analytics_productsale', con=engine, if_exists='append', index=False, chunksize=3000)
    print("Inserted successfully.")
else:
    print("No sales data found for Aug 19.")

print("All Postgres base tables updated!")
