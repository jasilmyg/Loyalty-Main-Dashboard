import os, sys, io, django, time
import pandas as pd
from datetime import datetime, timezone

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "myg_loyalty_dashboard.settings")
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

DAYS = [
    ("2026-09-02", "item_wise_sales_report/item_wise_sales_report_03-09-2026_03_00_02_031066.csv",
                   "invoice_wise_sales_report/invoice_wise_sales_report_03-09-2026_03_00_04_164225.csv"),
    ("2026-09-03", "item_wise_sales_report/item_wise_sales_report_04-09-2026_03_00_02_200008.csv",
                   "invoice_wise_sales_report/invoice_wise_sales_report_04-09-2026_03_00_04_012619.csv"),
    ("2026-09-04", "item_wise_sales_report/item_wise_sales_report_05-09-2026_03_00_02_037603.csv",
                   "invoice_wise_sales_report/invoice_wise_sales_report_05-09-2026_03_00_03_601180.csv"),
    ("2026-09-05", "item_wise_sales_report/item_wise_sales_report_06-09-2026_03_00_02_718785.csv",
                   "invoice_wise_sales_report/invoice_wise_sales_report_06-09-2026_03_00_04_481982.csv"),
]

SALES_COLS = ["date","invoice_no","branch","item_code","imei_batch","qty","mop","discount","buyback","sold_price","taxable"]
INV_COLS   = ["date","time","invoice_no","branch","rbm","bdm","customer_mobile","customer_pincode","customer_gstin","customer_type","sales_staff_code","billing_staff_code","invoice_total","discount","buyback","deductions","exchange","financier_code","financier_name","scheme","loan_amount"]
INV_STR    = {"time","invoice_no","branch","rbm","bdm","customer_mobile","customer_pincode","customer_gstin","customer_type","sales_staff_code","billing_staff_code","financier_code","financier_name","scheme"}
INV_FLOAT  = {"invoice_total","discount","buyback","deductions","exchange","loan_amount"}
RENAME_ITEM = {"Date":"date","Invoice No":"invoice_no","Invoice No.":"invoice_no","Branch":"branch","Item Code":"item_code","IMEI/Batch":"imei_batch","IMEI/Batch No":"imei_batch","IMEI/Batch No.":"imei_batch","Qty":"qty","QTY":"qty","Quantity":"qty","MOP":"mop","Discount":"discount","Buyback":"buyback","Sold Price":"sold_price","Taxable":"taxable"}
RENAME_INV  = {"Date":"date","Time":"time","Invoice No":"invoice_no","Invoice No.":"invoice_no","Branch":"branch","RBM":"rbm","BDM":"bdm","Customer Bill To No":"customer_mobile","Customer Bill To No.":"customer_mobile","Customer Bill To Pincode":"customer_pincode","Customer Bill To GSTIN":"customer_gstin","Customer Bill to GSTIN":"customer_gstin","Customer Type":"customer_type","Sales Staff Code":"sales_staff_code","Billing Staff Code":"billing_staff_code","Invoice Total":"invoice_total","Discount":"discount","Buyback":"buyback","Deductions (Indirect)":"deductions","Exchange":"exchange","Financier Code":"financier_code","Financier Name":"financier_name","Scheme":"scheme","Loan Amount":"loan_amount"}

def safe_str(s):   return s.fillna("").astype(str).str.strip().replace({"nan":"","None":""})
def safe_float(s): return pd.to_numeric(s, errors="coerce").fillna(0.0).astype(float)
def parse_dates(s): return pd.to_datetime(s, format="%d-%m-%Y", errors="coerce").dt.normalize()
def to_utc(ts):     return datetime(ts.year, ts.month, ts.day, tzinfo=timezone.utc)

for DATA_DATE, ITEM_BLOB, INV_BLOB in DAYS:
    print(f"\n{'='*60}\n  {DATA_DATE}\n{'='*60}")
    sb = ch.query(f"SELECT count() FROM {SALES_TABLE}   WHERE toDate(date)='{DATA_DATE}'").result_rows[0][0]
    ib = ch.query(f"SELECT count() FROM {INVOICE_TABLE} WHERE toDate(date)='{DATA_DATE}'").result_rows[0][0]
    print(f"  Existing: sales={sb:,} inv={ib:,}")
    if sb>0 or ib>0:
        print("  Deleting existing rows...")
        ch.command(f"ALTER TABLE {SALES_TABLE}   DELETE WHERE toDate(date)='{DATA_DATE}'")
        ch.command(f"ALTER TABLE {INVOICE_TABLE} DELETE WHERE toDate(date)='{DATA_DATE}'")
        time.sleep(20)
    
    raw=container_client.get_blob_client(ITEM_BLOB).download_blob().readall()
    df=pd.read_csv(io.BytesIO(raw))
    df.rename(columns={k:v for k,v in RENAME_ITEM.items() if k in df.columns},inplace=True)
    df["date"]=parse_dates(df["date"]); df["invoice_no"]=safe_str(df["invoice_no"]); df["branch"]=safe_str(df["branch"]); df["item_code"]=safe_str(df["item_code"])
    if "imei_batch" not in df.columns: df["imei_batch"]=""
    df["imei_batch"]=df["imei_batch"].fillna("").astype(str).str.strip()
    for c in ["qty","mop","discount","sold_price","taxable"]: df[c]=safe_float(df[c])
    if "buyback" not in df.columns: df["buyback"]=0.0
    df["buyback"]=safe_float(df["buyback"])
    df=df[SALES_COLS].dropna(subset=["date"]); df=df[df["invoice_no"].str.strip()!=""]
    rows=[(to_utc(r.date),r.invoice_no,r.branch,r.item_code,r.imei_batch,r.qty,r.mop,r.discount,r.buyback,r.sold_price,r.taxable) for r in df.itertuples(index=False)]
    ch.insert(SALES_TABLE,rows,column_names=SALES_COLS)
    print(f"  Sales: {len(rows):,} rows inserted")
    
    raw2=container_client.get_blob_client(INV_BLOB).download_blob().readall()
    df2=pd.read_csv(io.BytesIO(raw2))
    df2.rename(columns={k:v for k,v in RENAME_INV.items() if k in df2.columns},inplace=True)
    for c in INV_COLS:
        if c not in df2.columns: df2[c]="" if c in INV_STR else 0.0
    df2["date"]=parse_dates(df2["date"])
    for c in INV_STR:
        if c in df2.columns: df2[c]=safe_str(df2[c])
    for c in INV_FLOAT:
        if c in df2.columns: df2[c]=safe_float(df2[c])
    df2=df2[INV_COLS].dropna(subset=["date"]); df2=df2[df2["invoice_no"].str.strip()!=""]
    rows2=[(to_utc(r.date),r.time,r.invoice_no,r.branch,r.rbm,r.bdm,r.customer_mobile,r.customer_pincode,r.customer_gstin,r.customer_type,r.sales_staff_code,r.billing_staff_code,r.invoice_total,r.discount,r.buyback,r.deductions,r.exchange,r.financier_code,r.financier_name,r.scheme,r.loan_amount) for r in df2.itertuples(index=False)]
    ch.insert(INVOICE_TABLE,rows2,column_names=INV_COLS)
    print(f"  Invoices: {len(rows2):,} rows inserted")
    time.sleep(10)

ms=ch.query(f"SELECT max(toDate(date)) FROM {SALES_TABLE}").result_rows[0][0]
mi=ch.query(f"SELECT max(toDate(date)) FROM {INVOICE_TABLE}").result_rows[0][0]
print(f"\nFINAL MAX DATE -> sales: {ms} | invoices: {mi}")
print("ALL DONE")
