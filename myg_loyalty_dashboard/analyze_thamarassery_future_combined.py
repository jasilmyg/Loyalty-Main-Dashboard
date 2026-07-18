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
CombinedVisits AS (
    SELECT DISTINCT "Customer Mobile"
    FROM sales_data
    WHERE "Branch" = 'THAMARASSERY FUTURE'
      AND parsed_date IN ('2026-07-03', '2026-07-04')
)
SELECT 
    'July 3 & 4 (Combined)' as visit_date,
    COUNT(DISTINCT c."Customer Mobile") as total_customers,
    COUNT(DISTINCT CASE WHEN b.first_visit >= '2026-07-03' THEN c."Customer Mobile" END) as new_customers,
    COUNT(DISTINCT CASE WHEN b.first_visit < '2026-07-03' THEN c."Customer Mobile" END) as repeat_customers
FROM CombinedVisits c
JOIN BranchFirstVisits b ON c."Customer Mobile" = b."Customer Mobile";
"""

df = pd.read_sql(query, connection)
print("Thamarassery Future Database Analysis (Combined):")
print(df.to_string(index=False))
