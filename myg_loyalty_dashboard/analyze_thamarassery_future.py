import os, django
import pandas as pd
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'myg_loyalty_dashboard.settings')
django.setup()
from django.db import connection

query = """
WITH BranchFirstVisits AS (
    SELECT "Customer Mobile", MIN(parsed_date) as first_visit
    FROM sales_data
    WHERE "Branch" = 'THAMARASSERY FUTURE'
    GROUP BY "Customer Mobile"
),
DailyVisits AS (
    SELECT DISTINCT parsed_date as visit_date, "Customer Mobile"
    FROM sales_data
    WHERE "Branch" = 'THAMARASSERY FUTURE'
      AND parsed_date IN ('2026-07-03', '2026-07-04')
)
SELECT 
    d.visit_date,
    COUNT(DISTINCT d."Customer Mobile") as total_customers,
    COUNT(DISTINCT CASE WHEN b.first_visit = d.visit_date THEN d."Customer Mobile" END) as new_customers,
    COUNT(DISTINCT CASE WHEN b.first_visit < d.visit_date THEN d."Customer Mobile" END) as repeat_customers
FROM DailyVisits d
JOIN BranchFirstVisits b ON d."Customer Mobile" = b."Customer Mobile"
GROUP BY d.visit_date
ORDER BY d.visit_date;
"""

df = pd.read_sql(query, connection)
print("Thamarassery Future Database Analysis (July 3 & 4):")
print(df.to_string(index=False))
