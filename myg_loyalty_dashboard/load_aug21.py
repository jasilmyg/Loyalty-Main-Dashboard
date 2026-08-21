import os, django, sys, time
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "myg_loyalty_dashboard.settings")
django.setup()

from analytics.clickhouse_service import get_ch_client
from django.db import connection
from django.conf import settings
import pandas as pd
from sqlalchemy import create_engine

client = get_ch_client()

SAS_TOKEN = "sp=racwl&st=2026-08-11T03:51:43Z&se=2026-12-31T18:29:43Z&spr=https&sv=2026-02-06&sr=c&sig=b5URyZCBQKQU3rwuqxY5z2vqyKNrsDKIPABLQ%2FFyywQ%3D"
ACCOUNT_URL = "https://stmygoalposreports.blob.core.windows.net"
CONN = "BlobEndpoint=" + ACCOUNT_URL + "/;SharedAccessSignature=" + SAS_TOKEN
INV_BLOB   = "invoice_wise_sales_report/invoice_wise_sales_report_21-08-2026_03_00_03_606604.csv"
SALES_BLOB = "item_wise_sales_report/item_wise_sales_report_21-08-2026_03_00_01_918381.csv"
DATE_STR = "2026-08-21"

FILTERS = "WHERE branch NOT IN ('3GH','SMC','HEAD OFFICE','UG SMART CHOICE') AND invoice_no NOT LIKE '%SMC%' AND invoice_no NOT LIKE '%EI%'"

print("--- STEP 1: Loading Aug 21 into ClickHouse ---")
t0 = time.time()
client.command("INSERT INTO azure_invoice_report SELECT * FROM azureBlobStorage('" + CONN + "','sales-reports','" + INV_BLOB + "','CSVWithNames') " + FILTERS + " SETTINGS date_time_input_format='best_effort'")
print("  Invoice loaded in " + str(round(time.time()-t0,1)) + "s")

t0 = time.time()
client.command("INSERT INTO azure_sales_report SELECT * FROM azureBlobStorage('" + CONN + "','sales-reports','" + SALES_BLOB + "','CSVWithNames') " + FILTERS + " SETTINGS date_time_input_format='best_effort'")
print("  Sales loaded in " + str(round(time.time()-t0,1)) + "s")

print("--- STEP 2: Verifying ClickHouse counts ---")
inv_count   = client.query("SELECT count() FROM azure_invoice_report WHERE toDate(date)='" + DATE_STR + "'").result_rows[0][0]
sales_count = client.query("SELECT count() FROM azure_sales_report WHERE toDate(date)='"   + DATE_STR + "'").result_rows[0][0]
print("  azure_invoice_report: " + str(inv_count) + " rows")
print("  azure_sales_report:   " + str(sales_count) + " rows")

print("--- STEP 3: Syncing to Postgres ---")
db = settings.DATABASES["default"]
engine = create_engine("postgresql://" + db["USER"] + ":" + db["PASSWORD"] + "@" + db["HOST"] + ":" + str(db["PORT"]) + "/" + db["NAME"])

inv_data = client.query("SELECT date,time,invoice_no,branch,rbm,bdm,sales_staff_code,customer_mobile,financier_name,toString(loan_amount),invoice_total,toDate(date) FROM azure_invoice_report WHERE toDate(date)='" + DATE_STR + "'").result_rows
if inv_data:
    cols=["Date","Time","Invoice Number","Branch","RBM","BDM","Staff Code","Customer Mobile","Financier","Finance","Total Value","parsed_date"]
    df=pd.DataFrame(inv_data,columns=cols)
    for c in ["Slno","Customer Name","Cash","Staff","Enq/Job No.","Delivery Order No.","Debit Card","Credit Card","Benow","Advance Receipt","Bharath QR","Paytm QR","Pine Labs QR","UPI Cashback","Card Reward","Card Cashback","Gift Voucher","Approved Credit","EMI","Customer Type","Exchange","Discount","Indirect Discount","Buyback","Addition","POINT REDUMPTION (DEDUCTION)","MYG ONLINE COUPON (DEDUCTION)","source_file","Deduction"]:
        df[c]=""
    with connection.cursor() as cur:
        cur.execute("DELETE FROM sales_data WHERE parsed_date='" + DATE_STR + "'")
    df.to_sql("sales_data", con=engine, if_exists="append", index=False, chunksize=3000)
    print("  Inserted " + str(len(df)) + " rows into sales_data")

sales_data = client.query("SELECT toDate(date),invoice_no,branch,item_code,qty,sold_price FROM azure_sales_report WHERE toDate(date)='" + DATE_STR + "'").result_rows
if sales_data:
    cols=["date","invoice_number","branch","product","qty","sold_price"]
    df2=pd.DataFrame(sales_data,columns=cols)
    for c in ["category","brand"]:
        df2[c]=""
    with connection.cursor() as cur:
        cur.execute("DELETE FROM analytics_productsale WHERE date='" + DATE_STR + "'")
    df2.to_sql("analytics_productsale", con=engine, if_exists="append", index=False, chunksize=3000)
    print("  Inserted " + str(len(df2)) + " rows into analytics_productsale")

print("ALL DONE - Aug 21 data loaded successfully!")
