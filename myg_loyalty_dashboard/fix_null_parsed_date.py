import os, django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'myg_loyalty_dashboard.settings')
django.setup()

from django.db import connection
with connection.cursor() as cur:
    cur.execute("SELECT COUNT(*) FROM sales_data WHERE parsed_date IS NULL")
    null_count = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM sales_data")
    total = cur.fetchone()[0]
    print(f"Total rows: {total:,}")
    print(f"Rows with parsed_date=NULL: {null_count:,}")
    
    if null_count > 0:
        cur.execute('SELECT "Date" FROM sales_data WHERE parsed_date IS NULL LIMIT 5')
        samples = cur.fetchall()
        print("Sample Date values for NULL rows:")
        for s in samples:
            print(f"  {repr(s[0])}")
        
        # Fix them
        print("\nFixing NULL parsed_date values...")
        cur.execute("""
            UPDATE sales_data
            SET parsed_date = CASE
                WHEN ("Date"::text) ~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}'
                    THEN TO_DATE(SUBSTRING(("Date"::text), 1, 10), 'YYYY-MM-DD')
                WHEN ("Date"::text) ~ '^[0-9]{2}-[0-9]{2}-[0-9]{4}'
                    THEN TO_DATE(("Date"::text), 'DD-MM-YYYY')
                WHEN ("Date"::text) ~ '^[0-9]{2}/[0-9]{2}/[0-9]{4}'
                    THEN TO_DATE(("Date"::text), 'DD/MM/YYYY')
                WHEN ("Date"::text) ~ '^[0-9]{4}/[0-9]{2}/[0-9]{2}'
                    THEN TO_DATE(SUBSTRING(("Date"::text), 1, 10), 'YYYY/MM/DD')
                ELSE NULL
            END
            WHERE parsed_date IS NULL;
        """)
        print(f"Updated! Rows affected: {cur.rowcount:,}")
        
        # Verify
        cur.execute("SELECT COUNT(*) FROM sales_data WHERE parsed_date IS NULL")
        remaining = cur.fetchone()[0]
        print(f"Remaining NULL rows after fix: {remaining:,}")
    else:
        print("All rows already have parsed_date set.")
