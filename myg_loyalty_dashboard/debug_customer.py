import duckdb, os, sys

DUCKDB_PATH  = r'C:\Users\jasil_myg\Desktop\myG Loyalty Main Dashboard\myg_loyalty_dashboard\analytics.duckdb'
SQLITE_PATH  = r'C:/Users/jasil_myg/Desktop/myG Loyalty Main Dashboard/project_folder/combined_data.db'

if os.path.exists(DUCKDB_PATH):
    conn = duckdb.connect(DUCKDB_PATH, read_only=True)
    using_native = True
    table = 'sales_data'
    print('Using DuckDB native')
else:
    conn = duckdb.connect()
    conn.execute(f"ATTACH '{SQLITE_PATH}' AS sqlite_db (TYPE SQLITE);")
    using_native = False
    table = 'sqlite_db.sales_data'
    print('Using SQLite via DuckDB')

MOBILE = '9947286860'

# 1. Raw rows
print('\n--- Raw rows ---')
rows = conn.execute(f'SELECT "Date", "Invoice Number", "Customer Name" FROM {table} WHERE "Customer Mobile" = ? ORDER BY "Date"', [MOBILE]).fetchall()
for r in rows:
    print(r)

# 2. Date column dtype
print('\n--- Date column dtype ---')
dtype = conn.execute(f'SELECT typeof("Date") FROM {table} LIMIT 1').fetchone()
print('typeof Date:', dtype)

# 3. COUNT DISTINCT "Date"
print('\n--- COUNT DISTINCT "Date" ---')
r = conn.execute(f'SELECT COUNT(DISTINCT "Date") FROM {table} WHERE "Customer Mobile" = ?', [MOBILE]).fetchone()
print('Distinct raw Date values:', r[0])

# 4. Distinct date values
print('\n--- Distinct Date values ---')
rows2 = conn.execute(f'SELECT DISTINCT "Date" FROM {table} WHERE "Customer Mobile" = ?', [MOBILE]).fetchall()
for r in rows2:
    print(repr(r[0]))

# 5. CAST to DATE
print('\n--- CAST to DATE ---')
try:
    r3 = conn.execute(f'SELECT COUNT(DISTINCT CAST("Date" AS DATE)) FROM {table} WHERE "Customer Mobile" = ?', [MOBILE]).fetchone()
    print('Distinct dates (CAST AS DATE):', r3[0])
    rows3 = conn.execute(f'SELECT DISTINCT CAST("Date" AS DATE) FROM {table} WHERE "Customer Mobile" = ?', [MOBILE]).fetchall()
    for r in rows3:
        print(repr(r[0]))
except Exception as e:
    print('Error casting to DATE:', e)

# 6. Check same date is being produced by both expressions
if not using_native:
    date_expr = "COALESCE(TRY_STRPTIME(CAST(\"Date\" AS VARCHAR), '%d-%m-%Y'), TRY_CAST(\"Date\" AS TIMESTAMP))"
    print('\n--- Via TRY_STRPTIME ---')
    try:
        rows4 = conn.execute(f'SELECT DISTINCT {date_expr} FROM {table} WHERE "Customer Mobile" = ?', [MOBILE]).fetchall()
        cnt   = conn.execute(f'SELECT COUNT(DISTINCT {date_expr}) FROM {table} WHERE "Customer Mobile" = ?', [MOBILE]).fetchone()
        for r in rows4:
            print(repr(r[0]))
        print('COUNT DISTINCT:', cnt[0])
    except Exception as e:
        print('Error:', e)
