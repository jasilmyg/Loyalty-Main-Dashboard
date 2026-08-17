import os
import django
import time

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'myg_loyalty_dashboard.settings')
django.setup()

from analytics.clickhouse_service import get_ch_client

def import_data():
    client = get_ch_client()
    if not client:
        print("Failed to get ClickHouse client")
        return

    print("Creating table azure_invoice_report...")
    create_table_sql = """
    CREATE TABLE IF NOT EXISTS azure_invoice_report (
        date DateTime,
        time String,
        invoice_no String,
        branch String,
        rbm String,
        bdm String,
        customer_mobile String,
        customer_pincode String,
        customer_gstin String,
        customer_type String,
        sales_staff_code String,
        billing_staff_code String,
        invoice_total Float32,
        discount Float32,
        buyback Float32,
        deductions Float32,
        exchange Float32,
        financier_code String,
        financier_name String,
        scheme String,
        loan_amount Float32
    ) ENGINE = MergeTree()
    ORDER BY (date, branch, invoice_no)
    """
    client.command(create_table_sql)

    connection_string = 'BlobEndpoint=https://stmygoalposreports.blob.core.windows.net/;SharedAccessSignature=sp=racwl&st=2026-08-11T03:51:43Z&se=2026-12-31T18:29:43Z&spr=https&sv=2026-02-06&sr=c&sig=b5URyZCBQKQU3rwuqxY5z2vqyKNrsDKIPABLQ%2FFyywQ%3D'
    
    print("Clearing existing data (if any)...")
    client.command("TRUNCATE TABLE IF EXISTS azure_invoice_report")

    print("Ingesting data from Azure Blob Storage. This may take a moment...")
    insert_sql = f"""
    INSERT INTO azure_invoice_report
    SELECT 
        `Date` as date,
        `Time` as time,
        `Invoice No` as invoice_no,
        `Branch` as branch,
        `RBM` as rbm,
        `BDM` as bdm,
        `Customer Bill To No` as customer_mobile,
        `Customer Bill To Pincode` as customer_pincode,
        `Customer Bill to GSTIN` as customer_gstin,
        `Customer Type` as customer_type,
        `Sales Staff Code` as sales_staff_code,
        `Billing Staff Code` as billing_staff_code,
        `Invoice Total` as invoice_total,
        `Discount` as discount,
        `Buyback` as buyback,
        `Deductions (Indirect)` as deductions,
        `Exchange` as exchange,
        `Financier Code` as financier_code,
        `Financier Name` as financier_name,
        `Scheme` as scheme,
        `Loan Amount` as loan_amount
    FROM azureBlobStorage(
        '{connection_string}',
        'sales-reports',
        'invoice_wise_sales_report/*.csv',
        'CSVWithNames'
    )
    """
    
    start_time = time.time()
    try:
        client.command(insert_sql)
        elapsed = time.time() - start_time
        
        count = client.query("SELECT count(*) FROM azure_invoice_report").result_rows[0][0]
        print(f"Successfully ingested {count} rows in {elapsed:.2f} seconds.")
    except Exception as e:
        print(f"Error during ingestion: {e}")

if __name__ == '__main__':
    import_data()
