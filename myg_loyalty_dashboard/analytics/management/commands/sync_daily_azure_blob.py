from django.core.management.base import BaseCommand
import os
import requests
import xml.etree.ElementTree as ET
import time
from analytics.clickhouse_service import get_ch_client
from django.conf import settings

class Command(BaseCommand):
    help = 'Syncs new Azure Blob Storage files into ClickHouse daily.'

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true', help='Check files without importing')

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        
        # In production this will come from render.yaml
        sas_url = os.environ.get('AZURE_BLOB_SAS_URL', getattr(settings, 'AZURE_BLOB_SAS_URL', None))
        
        # Fallback to hardcoded testing token if none provided in env (for local dev)
        if not sas_url:
            self.stdout.write(self.style.WARNING("No AZURE_BLOB_SAS_URL found in env. Falling back to test token."))
            sas_url = 'https://stmygoalposreports.blob.core.windows.net/sales-reports?restype=container&comp=list&sp=racwl&st=2026-08-11T03:51:43Z&se=2026-12-31T18:29:43Z&spr=https&sv=2026-02-06&sr=c&sig=b5URyZCBQKQU3rwuqxY5z2vqyKNrsDKIPABLQ%2FFyywQ%3D'

        client = get_ch_client()
        if not client:
            self.stderr.write(self.style.ERROR("Could not connect to ClickHouse."))
            return

        self.stdout.write("Fetching file list from Azure Blob...")
        
        r = requests.get(sas_url)
        if r.status_code != 200:
            self.stderr.write(self.style.ERROR(f"Failed to fetch Azure blob list: {r.status_code} {r.text}"))
            return

        root = ET.fromstring(r.text)
        blobs = root.find('Blobs')
        all_csv_files = []
        for blob in blobs.findall('Blob'):
            name = blob.find('Name').text
            if name.endswith('.csv'):
                all_csv_files.append(name)

        self.stdout.write(f"Found {len(all_csv_files)} total CSV files in Azure container.")

        # Check ingestion log
        query = "SELECT file_name FROM azure_ingestion_log"
        try:
            result = client.query(query)
            already_ingested = {row[0] for row in result.result_rows}
        except Exception as e:
            self.stderr.write(self.style.ERROR(f"Failed to query ingestion log: {e}"))
            already_ingested = set()

        new_files = [f for f in all_csv_files if f not in already_ingested]
        
        if not new_files:
            self.stdout.write(self.style.SUCCESS("No new files to ingest. System is up to date."))
            return
            
        self.stdout.write(self.style.SUCCESS(f"Found {len(new_files)} new files to ingest."))

        if dry_run:
            self.stdout.write("DRY RUN: Exiting without making changes.")
            return

        # Replace 'restype=container&comp=list&' for the connection string usage
        connection_string = f"BlobEndpoint=https://stmygoalposreports.blob.core.windows.net/;"
        # Extract SAS signature part safely
        if '?' in sas_url:
            sas_query = sas_url.split('?')[1]
            sas_query = sas_query.replace('restype=container&comp=list&', '')
            connection_string += f"SharedAccessSignature={sas_query}"
        
        item_sales_files = [f for f in new_files if f.startswith('item_wise_sales_report/')]
        invoice_sales_files = [f for f in new_files if f.startswith('invoice_wise_sales_report/')]

        # Process item_wise_sales_report
        for file in item_sales_files:
            self.stdout.write(f"Ingesting item sales: {file}...")
            sql = f"""
            INSERT INTO azure_sales_report
            SELECT 
                `Date` as date, `Invoice No` as invoice_no, `Branch` as branch, `Item Code` as item_code,
                `IMEI/Batch` as imei_batch, `Qty` as qty, `MOP` as mop, `Discount` as discount,
                `Buyback` as buyback, `Sold Price` as sold_price, `Taxable` as taxable
            FROM azureBlobStorage('{connection_string}', 'sales-reports', '{file}', 'CSVWithNames')
            WHERE 
                `Invoice No` NOT LIKE '%SMC%' AND 
                `Invoice No` NOT LIKE '%EI%' AND 
                `Branch` NOT IN ('HEAD OFFICE', 'UG SMART CHOICE')
            """
            try:
                start = time.time()
                client.command(sql)
                client.insert('azure_ingestion_log', [[file]], column_names=['file_name'])
                self.stdout.write(self.style.SUCCESS(f"  -> Done in {time.time()-start:.2f}s"))
            except Exception as e:
                self.stderr.write(self.style.ERROR(f"  -> Failed: {e}"))

        # Process invoice_wise_sales_report
        for file in invoice_sales_files:
            self.stdout.write(f"Ingesting invoice sales: {file}...")
            sql = f"""
            INSERT INTO azure_invoice_report
            SELECT 
                `Date` as date, `Time` as time, `Invoice No` as invoice_no, `Branch` as branch, `RBM` as rbm,
                `BDM` as bdm, `Customer Bill To No` as customer_mobile, `Customer Bill To Pincode` as customer_pincode,
                `Customer Bill to GSTIN` as customer_gstin, `Customer Type` as customer_type, `Sales Staff Code` as sales_staff_code,
                `Billing Staff Code` as billing_staff_code, `Invoice Total` as invoice_total, `Discount` as discount,
                `Buyback` as buyback, `Deductions (Indirect)` as deductions, `Exchange` as exchange,
                `Financier Code` as financier_code, `Financier Name` as financier_name, `Scheme` as scheme,
                `Loan Amount` as loan_amount
            FROM azureBlobStorage('{connection_string}', 'sales-reports', '{file}', 'CSVWithNames')
            WHERE 
                `Invoice No` NOT LIKE '%SMC%' AND 
                `Invoice No` NOT LIKE '%EI%' AND 
                `Branch` NOT IN ('HEAD OFFICE', 'UG SMART CHOICE')
            """
            try:
                start = time.time()
                client.command(sql)
                client.insert('azure_ingestion_log', [[file]], column_names=['file_name'])
                self.stdout.write(self.style.SUCCESS(f"  -> Done in {time.time()-start:.2f}s"))
            except Exception as e:
                self.stderr.write(self.style.ERROR(f"  -> Failed: {e}"))

        self.stdout.write(self.style.SUCCESS("Daily sync completed!"))
