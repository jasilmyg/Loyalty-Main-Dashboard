import os
import django
import requests
import xml.etree.ElementTree as ET
from urllib.parse import urlencode

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'myg_loyalty_dashboard.settings')
django.setup()

from analytics.clickhouse_service import get_ch_client

def backfill():
    client = get_ch_client()
    
    print("Creating azure_ingestion_log table...")
    client.command("""
        CREATE TABLE IF NOT EXISTS azure_ingestion_log (
            file_name String,
            ingested_at DateTime DEFAULT now()
        ) ENGINE = MergeTree()
        ORDER BY file_name
    """)

    sas_url = 'https://stmygoalposreports.blob.core.windows.net/sales-reports?restype=container&comp=list&sp=racwl&st=2026-08-11T03:51:43Z&se=2026-12-31T18:29:43Z&spr=https&sv=2026-02-06&sr=c&sig=b5URyZCBQKQU3rwuqxY5z2vqyKNrsDKIPABLQ%2FFyywQ%3D'
    
    print("Fetching files from Azure Blob...")
    r = requests.get(sas_url)
    if r.status_code != 200:
        print("Failed to fetch from Azure")
        return

    root = ET.fromstring(r.text)
    blobs = root.find('Blobs')
    files_to_insert = []
    
    for blob in blobs.findall('Blob'):
        name = blob.find('Name').text
        if name.endswith('.csv'):
            files_to_insert.append([name])

    print(f"Found {len(files_to_insert)} CSV files. Backfilling log...")
    
    # Check if already backfilled
    count = client.query("SELECT count(*) FROM azure_ingestion_log").result_rows[0][0]
    if count > 0:
        print(f"Table already contains {count} rows. Skipping backfill to prevent duplicates.")
    else:
        client.insert('azure_ingestion_log', files_to_insert, column_names=['file_name'])
        print(f"Successfully backfilled {len(files_to_insert)} files into azure_ingestion_log.")

if __name__ == "__main__":
    backfill()
