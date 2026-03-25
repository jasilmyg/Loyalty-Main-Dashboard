import duckdb

DUCKDB_PATH = r'C:\Users\jasil_myg\Desktop\myG Loyalty Main Dashboard\myg_loyalty_dashboard\analytics.duckdb'
SQLITE_PATH = r'C:/Users/jasil_myg/Desktop/myG Loyalty Main Dashboard/project_folder/combined_data.db'

print("=== DuckDB (Native) ===")
try:
    conn_duck = duckdb.connect(DUCKDB_PATH, read_only=True)
    count_duck = conn_duck.execute('SELECT COUNT(*) FROM sales_data').fetchone()[0]
    max_date_duck = conn_duck.execute('SELECT MAX("Date") FROM sales_data').fetchone()[0]
    print(f"Total rows:  {count_duck:,}")
    print(f"Latest Date: {max_date_duck}")
except Exception as e:
    print('Error reading DuckDB:', e)

print("\n=== SQLite DB ===")
try:
    conn_lite = duckdb.connect()
    conn_lite.execute(f"ATTACH '{SQLITE_PATH}' AS sqlite_db (TYPE SQLITE);")
    count_lite = conn_lite.execute('SELECT COUNT(*) FROM sqlite_db.sales_data').fetchone()[0]
    max_date_lite = conn_lite.execute('SELECT MAX("Date") FROM sqlite_db.sales_data').fetchone()[0]
    print(f"Total rows:  {count_lite:,}")
    print(f"Latest Date: {max_date_lite}")
except Exception as e:
    print('Error reading SQLite:', e)

if count_duck == count_lite:
    print("\n✅ MATCH: Both databases have the exact same number of rows.")
else:
    print(f"\n❌ MISMATCH: Difference of {abs(count_duck - count_lite):,} rows.")
