import sys
try:
    from azure.storage.blob import ContainerClient
except ImportError:
    import subprocess
    subprocess.check_call([sys.executable, "-m", "pip", "install", "azure-storage-blob"])
    from azure.storage.blob import ContainerClient
import io
import csv

ACCOUNT_NAME   = "stmygoalposreports"
CONTAINER_NAME = "sales-reports"
SAS_TOKEN      = "sp=racwl&st=2026-08-11T03:51:43Z&se=2026-12-31T18:29:43Z&spr=https&sv=2026-02-06&sr=c&sig=b5URyZCBQKQU3rwuqxY5z2vqyKNrsDKIPABLQ%2FFyywQ%3D"
ACCOUNT_URL    = f"https://{ACCOUNT_NAME}.blob.core.windows.net"

container_url    = f"{ACCOUNT_URL}/{CONTAINER_NAME}?{SAS_TOKEN}"
container_client = ContainerClient.from_container_url(container_url)

# Check the 28-08-2026 file
blob_name = "item_wise_sales_report/item_wise_sales_report_28-08-2026_03_00_02_297323.csv"

print(f"Downloading first few KB of: {blob_name}")
blob_client = container_client.get_blob_client(blob_name)

# Download just the first 5000 bytes to check headers + first rows
data = blob_client.download_blob(offset=0, length=5000).readall()
text = data.decode('utf-8', errors='replace')

lines = text.splitlines()
print(f"\nFirst line (headers):\n{lines[0]}")
print(f"\nFirst 5 data rows:")
for line in lines[1:6]:
    print(line)
