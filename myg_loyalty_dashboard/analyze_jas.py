import os, django
import pandas as pd
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'myg_loyalty_dashboard.settings')
django.setup()
from django.db import connection

results = []
for year in range(2020, 2026):
    print(f"Processing year {year}...")
    query = f"""
        SELECT 
            EXTRACT(MONTH FROM s.parsed_date) as month,
            COUNT(DISTINCT s."Customer Mobile") as total_customers,
            COUNT(DISTINCT CASE WHEN CAST(c.first_visit AS DATE) < MAKE_DATE({year}, 7, 1) THEN s."Customer Mobile" END) as repeat_customers
        FROM sales_data s
        JOIN mv_customer_summary c ON s."Customer Mobile" = c.mobile
        WHERE s.parsed_date >= '{year}-07-01' AND s.parsed_date < '{year}-10-01'
        GROUP BY EXTRACT(MONTH FROM s.parsed_date)
        ORDER BY month
    """
    df_year = pd.read_sql(query, connection)
    df_year['year'] = year
    results.append(df_year)

df = pd.concat(results, ignore_index=True)

print(df.to_string())

# Summarize by year
yearly = df.groupby('year').sum().reset_index()
yearly['repeat_pct'] = (yearly['repeat_customers'] / yearly['total_customers']) * 100

print("\nYearly Summary:")
print(yearly.to_string())

# Calculate YoY growth for repeat customers
yearly['repeat_growth'] = yearly['repeat_customers'].pct_change() * 100
print("\nYoY Growth for Repeat Customers:")
print(yearly[['year', 'repeat_customers', 'repeat_growth']].to_string())
