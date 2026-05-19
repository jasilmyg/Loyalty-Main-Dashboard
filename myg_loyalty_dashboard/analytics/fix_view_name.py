import os, psycopg2

conn = psycopg2.connect(
    host='db-postgresql-blr1-90397-do-user-3146770-0.e.db.ondigitalocean.com',
    port=25060, dbname='defaultdb', user='doadmin',
    password=os.environ['PGPASSWORD'], sslmode='require', connect_timeout=10
)
conn.autocommit = True
cur = conn.cursor()

# CREATE OR REPLACE VIEW keeps all existing columns in same order, just adds new ones at end.
# Existing columns order: Invoice Number, Branch, Staff, RBM, BDM, Total Value, Date, Customer Mobile
# We append: Customer Name, Staff Code at the end.
print("Replacing v_sales_data to include Customer Name and Staff Code...")
cur.execute(r"""
    CREATE OR REPLACE VIEW v_sales_data AS
    SELECT
        "Invoice Number",
        "Branch",
        "Staff",
        "RBM",
        "BDM",
        "Total Value",
        CASE
            WHEN "Date" ~ '^\d{4}-\d{2}-\d{2}( \d{2}:\d{2}:\d{2})?$'
                THEN TO_DATE(SUBSTRING("Date" FROM 1 FOR 10), 'YYYY-MM-DD')
            WHEN "Date" ~ '^\d{2}-\d{2}-\d{4}$'
                THEN TO_DATE("Date", 'DD-MM-YYYY')
            ELSE NULL
        END AS "Date",
        CASE
            WHEN "Customer Mobile" ~ '^\d+\.\d+$'
                THEN SPLIT_PART("Customer Mobile", '.', 1)
            ELSE "Customer Mobile"
        END AS "Customer Mobile",
        "Customer Name",
        "Staff Code"
    FROM sales_data;
""")
print("View updated successfully!")

# Verify
cur.execute("""
    SELECT column_name FROM information_schema.columns
    WHERE table_name = 'v_sales_data'
    ORDER BY ordinal_position
""")
print("New v_sales_data columns:", [r[0] for r in cur.fetchall()])

cur.close()
conn.close()
