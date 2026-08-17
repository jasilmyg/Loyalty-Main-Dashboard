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

    print("Creating table azure_sales_report...")
    create_table_sql = """
    CREATE TABLE IF NOT EXISTS azure_sales_report (
        date DateTime,
        invoice_no String,
        branch String,
        item_code String,
        imei_batch Nullable(String),
        qty Float32,
        mop Float32,
        discount Float32,
        buyback Float32,
        sold_price Float32,
        taxable Float32
    ) ENGINE = MergeTree()
    ORDER BY (date, branch, item_code)
    """
    client.command(create_table_sql)

    connection_string = 'BlobEndpoint=https://stmygoalposreports.blob.core.windows.net/;SharedAccessSignature=sp=racwl&st=2026-08-11T03:51:43Z&se=2026-12-31T18:29:43Z&spr=https&sv=2026-02-06&sr=c&sig=b5URyZCBQKQU3rwuqxY5z2vqyKNrsDKIPABLQ%2FFyywQ%3D'
    
    print("Clearing existing data (if any)...")
    client.command("TRUNCATE TABLE IF EXISTS azure_sales_report")

    print("Ingesting data from Azure Blob Storage. This may take a moment...")
    insert_sql = f"""
    INSERT INTO azure_sales_report
    SELECT 
        `Date` as date,
        `Invoice No` as invoice_no,
        `Branch` as branch,
        `Item Code` as item_code,
        `IMEI/Batch` as imei_batch,
        `Qty` as qty,
        `MOP` as mop,
        `Discount` as discount,
        `Buyback` as buyback,
        `Sold Price` as sold_price,
        `Taxable` as taxable
    FROM azureBlobStorage(
        '{connection_string}',
        'sales-reports',
        'item_wise_sales_report/*.csv',
        'CSVWithNames'
    )
    """
    
    start_time = time.time()
    try:
        client.command(insert_sql)
        elapsed = time.time() - start_time
        
        count = client.query("SELECT count(*) FROM azure_sales_report").result_rows[0][0]
        print(f"Successfully ingested {count} rows in {elapsed:.2f} seconds.")
    except Exception as e:
        print(f"Error during ingestion: {e}")

if __name__ == '__main__':
    import_data()
