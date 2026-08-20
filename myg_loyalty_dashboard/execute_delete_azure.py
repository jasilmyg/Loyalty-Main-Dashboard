import os
import django
import sys
import time

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'myg_loyalty_dashboard.settings')
django.setup()

from analytics.clickhouse_service import get_ch_client
client = get_ch_client()

print("Deleting from azure_sales_report...")
q1 = """
ALTER TABLE azure_sales_report DELETE WHERE 
    invoice_no LIKE '%SMC%' OR 
    invoice_no LIKE '%EI%' OR 
    branch IN ('3GH', 'SMC', 'HEAD OFFICE', 'UG SMART CHOICE')
"""
try:
    client.command(q1)
    print("Delete command issued for azure_sales_report.")
except Exception as e:
    print(f"Error on azure_sales_report: {e}")

print("Deleting from azure_invoice_report...")
q2 = """
ALTER TABLE azure_invoice_report DELETE WHERE 
    invoice_no LIKE '%SMC%' OR 
    invoice_no LIKE '%EI%' OR 
    branch IN ('3GH', 'SMC', 'HEAD OFFICE', 'UG SMART CHOICE')
"""
try:
    client.command(q2)
    print("Delete command issued for azure_invoice_report.")
except Exception as e:
    print(f"Error on azure_invoice_report: {e}")

print("ClickHouse ALTER TABLE DELETE is asynchronous, sleeping for a few seconds to let mutations apply...")
time.sleep(5)

print("Verifying remaining records...")
q3 = """
SELECT count(*) 
FROM azure_sales_report 
WHERE invoice_no LIKE '%SMC%' OR invoice_no LIKE '%EI%' OR branch IN ('3GH', 'SMC', 'HEAD OFFICE', 'UG SMART CHOICE')
"""
print(f"Remaining in azure_sales_report matching conditions: {client.query(q3).result_rows[0][0]}")

q4 = """
SELECT count(*) 
FROM azure_invoice_report 
WHERE invoice_no LIKE '%SMC%' OR invoice_no LIKE '%EI%' OR branch IN ('3GH', 'SMC', 'HEAD OFFICE', 'UG SMART CHOICE')
"""
print(f"Remaining in azure_invoice_report matching conditions: {client.query(q4).result_rows[0][0]}")
