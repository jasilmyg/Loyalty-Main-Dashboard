import duckdb

conn = duckdb.connect(r'C:\Users\jasil_myg\Desktop\myG Loyalty Main Dashboard\myg_loyalty_dashboard\analytics.duckdb', read_only=True)

mobile_expr = 'CAST(TRY_CAST("Customer Mobile" AS DOUBLE) AS BIGINT)'

print('\n--- Grouped rows ---')
rows = conn.execute(f'''
    SELECT 
        {mobile_expr} as mobile,
        COUNT(DISTINCT "Date") as visits
    FROM sales_data 
    WHERE "Customer Mobile" LIKE '%9947286860%'
    GROUP BY {mobile_expr}
''').fetchall()

for r in rows:
    print(r)
