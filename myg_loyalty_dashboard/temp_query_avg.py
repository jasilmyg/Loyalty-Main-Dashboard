import os
import sys
import django

sys.path.insert(0, '.')
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'myg_loyalty_dashboard.settings')
django.setup()

from django.db import connection

sql_overall = """
    SELECT AVG("Total Value") 
    FROM sales_data
    WHERE parsed_date >= '2026-06-01' AND parsed_date <= '2026-06-30'
"""

sql_branches = """
    SELECT 
        "Branch",
        AVG("Total Value") as branch_avg
    FROM sales_data
    WHERE parsed_date >= '2026-06-01' AND parsed_date <= '2026-06-30'
    GROUP BY "Branch"
    HAVING AVG("Total Value") > (SELECT AVG("Total Value") FROM sales_data WHERE parsed_date >= '2026-06-01' AND parsed_date <= '2026-06-30')
    ORDER BY AVG("Total Value") DESC
    LIMIT 3;
"""

with connection.cursor() as cursor:
    cursor.execute(sql_overall)
    overall_avg = cursor.fetchone()[0]
    print(f'COMPANY OVERALL AVERAGE: Rs {overall_avg:,.2f}')
    print('-' * 40)
    
    cursor.execute(sql_branches)
    rows = cursor.fetchall()
    for idx, row in enumerate(rows, 1):
        print(f'{idx}. {row[0]} | Branch Avg: Rs {row[1]:,.2f}')
