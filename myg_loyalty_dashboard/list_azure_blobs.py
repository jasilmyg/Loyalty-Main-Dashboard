"""
list_azure_blobs.py
Lists all blobs in the Azure container so we can see which files
are available for Sep 6 - Sep 11, 2026.
"""
import os, sys, django
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'myg_loyalty_dashboard.settings')
django.setup()

from azure.storage.blob import ContainerClient

ACCOUNT_NAME   = "stmygoalposreports"
CONTAINER_NAME = "sales-reports"
SAS_TOKEN      = "sp=racwl&st=2026-08-11T03:51:43Z&se=2026-12-31T18:29:43Z&spr=https&sv=2026-02-06&sr=c&sig=b5URyZCBQKQU3rwuqxY5z2vqyKNrsDKIPABLQ%2FFyywQ%3D"
ACCOUNT_URL    = f"https://{ACCOUNT_NAME}.blob.core.windows.net"
container_url  = f"{ACCOUNT_URL}/{CONTAINER_NAME}?{SAS_TOKEN}"

container_client = ContainerClient.from_container_url(container_url)

print("Listing blobs containing 'sep' or dates from Sep 6-11...")
blobs = list(container_client.list_blobs())

# Filter for files that are recent (contain 09 or 202609 in name)
all_names = sorted([b.name for b in blobs if b.name.endswith('.csv')])

# Show files containing Sep references
print(f"\nTotal CSV files: {len(all_names)}")
print("\n--- Files with '09' in name (recent) ---")
for n in all_names:
    fname = n.split('/')[-1]
    if '09' in fname or 'Sep' in fname.lower() or 'sep' in fname.lower():
        print(f"  {n}")

print("\n--- Last 20 files overall ---")
for n in all_names[-20:]:
    print(f"  {n}")
