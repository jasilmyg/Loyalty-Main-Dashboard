import os, sys, django
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'myg_loyalty_dashboard.settings')
django.setup()

from django.db import connection

def analyze_customers():
    # Let's first verify the exact branch name
    with connection.cursor() as cur:
        cur.execute("""
            SELECT DISTINCT "Branch" FROM sales_data WHERE "Branch" ILIKE '%Balussery%'
        """)
        branches = cur.fetchall()
        print("Matching Branches in DB:", [b[0] for b in branches])

    # We need to find new vs repeat customers for Balussery Future on 24th, 25th, and 26th June 2026
    # Base data: 2020-01-01 to 2026-06-23
    
    target_dates = ['2026-06-24', '2026-06-25', '2026-06-26']
    
    for target_date in target_dates:
        query = f"""
        WITH target_day_customers AS (
            SELECT DISTINCT "Customer Mobile"
            FROM sales_data
            WHERE parsed_date = '{target_date}'
              AND "Branch" ILIKE '%Balussery Future%'
              AND "Customer Mobile" IS NOT NULL
        ),
        past_customers AS (
            SELECT DISTINCT "Customer Mobile"
            FROM sales_data
            WHERE parsed_date < '{target_date}'
              AND "Customer Mobile" IS NOT NULL
        )
        SELECT 
            COUNT(t."Customer Mobile") AS total_unique_customers,
            SUM(CASE WHEN p."Customer Mobile" IS NOT NULL THEN 1 ELSE 0 END) AS repeat_customers,
            SUM(CASE WHEN p."Customer Mobile" IS NULL THEN 1 ELSE 0 END) AS new_customers
        FROM target_day_customers t
        LEFT JOIN past_customers p ON t."Customer Mobile" = p."Customer Mobile"
        """
        
        with connection.cursor() as cur:
            cur.execute(query)
            result = cur.fetchone()
            
            total = result[0] or 0
            repeat = result[1] or 0
            new = result[2] or 0
            
            print(f"\\nDate: {target_date} | Branch: Balussery Future")
            print(f"Total Unique Customers: {total}")
            print(f"Repeat Customers: {repeat}")
            print(f"New Customers: {new}")

if __name__ == "__main__":
    analyze_customers()
