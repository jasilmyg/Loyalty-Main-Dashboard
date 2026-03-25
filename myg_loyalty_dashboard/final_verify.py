import duckdb

conn = duckdb.connect(r'C:\Users\jasil_myg\Desktop\myG Loyalty Main Dashboard\myg_loyalty_dashboard\analytics.duckdb', read_only=True)

# Correctly using the new harmonized mobile expression from services.py
mobile_expr = 'CAST(TRY_CAST("Customer Mobile" AS DOUBLE) AS BIGINT)'

print('--- Results for 9544551586 ---')
rows = conn.execute(f'''
    SELECT 
        {mobile_expr} as mobile,
        COUNT(DISTINCT "Date") as visits
    FROM sales_data 
    WHERE "Customer Mobile" LIKE '%9544551586%'
    GROUP BY {mobile_expr}
''').fetchall()

for r in rows:
    print(f"Mobile: {r[0]}, Visits: {r[1]}")
    
print('\n--- Results for 9947286860 ---')
rows = conn.execute(f'''
    SELECT 
        {mobile_expr} as mobile,
        COUNT(DISTINCT "Date") as visits
    FROM sales_data 
    WHERE "Customer Mobile" LIKE '%9947286860%'
    GROUP BY {mobile_expr}
''').fetchall()

for r in rows:
    print(f"Mobile: {r[0]}, Visits: {r[1]}")
