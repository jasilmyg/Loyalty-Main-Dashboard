"""
Find blob files dated Sep 27 and Sep 28 in the container.
Sep 26 data -> file dated 27-09-2026
Sep 27 data -> file dated 28-09-2026
"""
import os, sys, django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'myg_loyalty_dashboard.settings')
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
django.setup()

from azure.storage.blob import ContainerClient

ACCOUNT_NAME   = "stmygoalposreports"
CONTAINER_NAME = "sales-reports"
SAS_TOKEN      = "sp=racwl&st=2026-08-11T03:51:43Z&se=2026-12-31T18:29:43Z&spr=https&sv=2026-02-06&sr=c&sig=b5URyZCBQKQU3rwuqxY5z2vqyKNrsDKIPABLQ%2FFyywQ%3D"
ACCOUNT_URL    = f"https://{ACCOUNT_NAME}.blob.core.windows.net"
container_url  = f"{ACCOUNT_URL}/{CONTAINER_NAME}?{SAS_TOKEN}"
cc             = ContainerClient.from_container_url(container_url)

print("=== Looking for Sep 27 and Sep 28 dated files ===\n")
target = ['27-09-2026', '28-09-2026']

sales_blobs   = []
invoice_blobs = []

for blob in cc.list_blobs():
    if any(d in blob.name for d in target):
        size_kb = blob.size // 1024
        print(f"  FOUND: {blob.name}  ({size_kb:,} KB)")
        if 'item_wise' in blob.name:
            sales_blobs.append(blob.name)
        elif 'invoice_wise' in blob.name:
            invoice_blobs.append(blob.name)

print(f"\n  Item-wise (sales)   : {len(sales_blobs)} file(s)")
for b in sorted(sales_blobs): print(f"    -> {b}")
print(f"\n  Invoice-wise        : {len(invoice_blobs)} file(s)")
for b in sorted(invoice_blobs): print(f"    -> {b}")
