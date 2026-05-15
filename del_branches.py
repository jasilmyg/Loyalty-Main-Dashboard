import duckdb, sqlite3

SQLITE = r'C:\Users\jasil_myg\Desktop\myG Loyalty Main Dashboard\project_folder\combined_data.db'
DUCK = r'C:\Users\jasil_myg\Desktop\myG Loyalty Main Dashboard\myg_loyalty_dashboard\analytics.duckdb'

# Step 1: DuckDB fast columnar delete
print("Deleting from DuckDB...", flush=True)
d = duckdb.connect(DUCK)
b = d.execute('SELECT COUNT(*) FROM sales_data').fetchone()[0]
d.execute("""DELETE FROM sales_data WHERE upper("Branch") LIKE '%HEAD OFFICE%' OR upper("Branch") LIKE '%UG%SMART%'""")
a = d.execute('SELECT COUNT(*) FROM sales_data').fetchone()[0]
d.close()
print(f"DuckDB: {b:,} -> {a:,}  (deleted {b-a:,})", flush=True)

# Step 2: SQLite delete
print("Deleting from SQLite...", flush=True)
c = sqlite3.connect(SQLITE)
c.execute("""DELETE FROM sales_data WHERE upper([Branch]) LIKE '%HEAD OFFICE%' OR upper([Branch]) LIKE '%UG%SMART%'""")
c.commit()
c.close()
print("SQLite: done.", flush=True)
print("ALL DONE.", flush=True)
