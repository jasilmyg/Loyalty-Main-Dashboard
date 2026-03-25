import duckdb

DUCKDB_PATH = r'C:\Users\jasil_myg\Desktop\myG Loyalty Main Dashboard\myg_loyalty_dashboard\analytics.duckdb'
SQLITE_PATH = r'C:/Users/jasil_myg/Desktop/myG Loyalty Main Dashboard/project_folder/combined_data.db'

print('--- Searching DuckDB ---')
try:
    conn_duck = duckdb.connect(DUCKDB_PATH, read_only=True)
    res_duck = conn_duck.execute('SELECT "Date", "Invoice Number", "Customer Mobile" FROM sales_data WHERE "Invoice Number" = ?', ['25-I-KLMR-2741']).fetchall()
    print('Found in DuckDB:', len(res_duck))
    for r in res_duck: print(r)
except Exception as e:
    print('Error:', e)

print('\n--- Searching SQLite ---')
try:
    conn_lite = duckdb.connect()
    conn_lite.execute(f"ATTACH '{SQLITE_PATH}' AS sqlite_db (TYPE SQLITE);")
    res_lite = conn_lite.execute('SELECT "Date", "Invoice Number", "Customer Mobile" FROM sqlite_db.sales_data WHERE "Invoice Number" = ?', ['25-I-KLMR-2741']).fetchall()
    print('Found in SQLite:', len(res_lite))
    for r in res_lite: print(r)
except Exception as e:
    print('Error:', e)
