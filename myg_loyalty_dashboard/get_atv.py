import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'myg_loyalty_dashboard.settings')
django.setup()

from django.db import connection

query = """
SELECT 
    CASE WHEN "Branch" ILIKE '%FUTURE%' THEN 'Future Stores' ELSE 'Normal Stores' END AS store_format,
    SUM("Total Value") / NULLIF(COUNT(DISTINCT "Invoice Number"), 0) AS atv
FROM sales_data
WHERE parsed_date >= '2026-01-01' AND parsed_date < '2027-01-01'
GROUP BY 1;
"""

with connection.cursor() as cursor:
    cursor.execute(query)
    rows = cursor.fetchall()
    
    print("ATV 2026:")
    for row in rows:
        store_format, atv = row
        print(f"{store_format}: Rs. {float(atv):,.2f}")
