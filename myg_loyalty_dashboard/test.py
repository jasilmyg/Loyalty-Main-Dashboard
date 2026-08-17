import os, django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'myg_loyalty_dashboard.settings')
django.setup()

from analytics.clickhouse_service import get_ch_client
import traceback

try:
    client = get_ch_client()
    # Get monthly total sales to see YoY growth and historical context
    query = """
    SELECT 
        toYYYYMM(parsed_date) AS month,
        SUM(total_value) AS total_sales
    FROM sales_data
    GROUP BY month
    ORDER BY month
    """
    rows = client.query(query)
    for r in rows.result_rows:
        print(r)
except Exception as e:
    print("Error:", e)
    traceback.print_exc()









