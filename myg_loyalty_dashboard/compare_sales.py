import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'myg_loyalty_dashboard.settings')
django.setup()

from django.db import connection

query = """
SELECT 
    SUM(CASE WHEN "EMI" IS NOT NULL AND "EMI" != '' AND "EMI" != '0' THEN "Total Value" ELSE 0 END) AS emi_sales,
    SUM(CASE WHEN "UPI Cashback" IS NOT NULL AND "UPI Cashback" != '' AND "UPI Cashback" != '0' THEN "Total Value" ELSE 0 END) AS upi_sales
FROM sales_data
WHERE parsed_date >= '2025-01-01' AND parsed_date < '2026-01-01';
"""

with connection.cursor() as cursor:
    cursor.execute(query)
    emi_sales, upi_sales = cursor.fetchone()
    
    print("Sales Comparison 2025:")
    print(f"Total Sales via EMI/Finance: Rs. {float(emi_sales or 0):,.2f}")
    print(f"Total Sales via UPI/Cashback: Rs. {float(upi_sales or 0):,.2f}")
