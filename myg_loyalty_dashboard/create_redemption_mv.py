import os, django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'myg_loyalty_dashboard.settings')
django.setup()

from django.db import connection

try:
    with connection.cursor() as cur:
        # Drop if exists
        cur.execute("DROP MATERIALIZED VIEW IF EXISTS mv_redemption_analysis;")
        print("Dropped old MV if it existed.")
        
        # Create new MV
        query = """
        CREATE MATERIALIZED VIEW mv_redemption_analysis AS
        SELECT 
            TO_CHAR(DATE_TRUNC('month', parsed_date), 'Mon-YY') AS month_label,
            DATE_TRUNC('month', parsed_date) AS month_start,
            COUNT(DISTINCT "Customer Mobile") AS redeemed_customer_count,
            SUM(NULLIF(REPLACE(REPLACE("POINT REDUMPTION (DEDUCTION)", ',', ''), ' ', ''), '')::numeric) AS redeemed_point_value,
            SUM("Total Value") AS redeemed_sale_value,
            (SUM(NULLIF(REPLACE(REPLACE("POINT REDUMPTION (DEDUCTION)", ',', ''), ' ', ''), '')::numeric) / NULLIF(SUM("Total Value"), 0)) * 100 AS pct_loyalty_discount,
            (SUM("Total Value") / NULLIF(COUNT(DISTINCT "Customer Mobile"), 0)) AS asp
        FROM sales_data
        WHERE NULLIF(REPLACE(REPLACE("POINT REDUMPTION (DEDUCTION)", ',', ''), ' ', ''), '')::numeric > 0 
          AND parsed_date IS NOT NULL 
          AND parsed_date >= '2025-01-01'
        GROUP BY DATE_TRUNC('month', parsed_date), TO_CHAR(DATE_TRUNC('month', parsed_date), 'Mon-YY')
        ORDER BY month_start ASC;
        """
        cur.execute(query)
        print("Successfully created mv_redemption_analysis.")
        
        # Create unique index for concurrent refreshing later
        cur.execute("CREATE UNIQUE INDEX idx_redemption_mv_month ON mv_redemption_analysis (month_start);")
        print("Created unique index on mv_redemption_analysis.")
except Exception as e:
    import traceback
    print(f"Error: {e}")
    traceback.print_exc()
