import os, django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'myg_loyalty_dashboard.settings')
django.setup()
from django.db import connection

print("Fixing parsed_date for rows where Date is in YYYY-MM-DD HH:MM:SS format...")
with connection.cursor() as cur:
    # Fix rows where Date looks like '2026-06-27 00:00:00' (YYYY-MM-DD format)
    cur.execute(r"""
        UPDATE sales_data
        SET parsed_date = ("Date"::timestamp)::date
        WHERE parsed_date IS NULL
          AND "Date" ~ '^\d{4}-\d{2}-\d{2}';
    """)
    fixed_ymd = cur.rowcount
    print(f"  -> Fixed {fixed_ymd:,} rows (YYYY-MM-DD format)")

    # Also fix any remaining NULL rows where Date is DD-MM-YYYY format
    cur.execute(r"""
        UPDATE sales_data
        SET parsed_date = to_date("Date", 'DD-MM-YYYY')
        WHERE parsed_date IS NULL
          AND "Date" ~ '^\d{2}-\d{2}-\d{4}';
    """)
    fixed_dmy = cur.rowcount
    print(f"  -> Fixed {fixed_dmy:,} rows (DD-MM-YYYY format)")

    connection.commit()

    # Verify
    cur.execute("SELECT COUNT(*) FROM sales_data WHERE parsed_date IS NULL;")
    remaining = cur.fetchone()[0]
    print(f"  -> Remaining NULL parsed_date rows: {remaining:,}")

    cur.execute("SELECT MAX(parsed_date) FROM sales_data;")
    print(f"  -> New MAX parsed_date: {cur.fetchone()[0]}")

    cur.execute("SELECT MAX(parsed_date) FROM sales_data WHERE UPPER(TRIM(\"Branch\")) = 'FALNIR FUTURE';")
    print(f"  -> Falnir MAX parsed_date: {cur.fetchone()[0]}")

print("\nDone! Now running the Falnir analysis...")
