import os, django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'myg_loyalty_dashboard.settings')
django.setup()
from django.db import connection

print("Checking all MV definitions for issues...\n")

with connection.cursor() as cur:
    # Get all MV definitions
    cur.execute("SELECT matviewname, definition FROM pg_matviews ORDER BY matviewname;")
    mvs = cur.fetchall()

    regex_mvs = []
    v_sales_mvs = []
    good_mvs = []

    for name, defn in mvs:
        has_regex = ("~ '^[0-9]" in defn or "to_date" in defn.lower() or
                     "CASE" in defn and '"Date"' in defn and "date" in defn.lower())
        uses_v_sales = "v_sales_data" in defn

        if uses_v_sales:
            v_sales_mvs.append(name)
        elif has_regex and "parsed_date" not in defn:
            regex_mvs.append(name)
        else:
            good_mvs.append(name)

    print(f"=== MVs using v_sales_data (broken date cast): {len(v_sales_mvs)} ===")
    for m in v_sales_mvs:
        print(f"  - {m}")

    print(f"\n=== MVs with regex date parsing (slow): {len(regex_mvs)} ===")
    for m in regex_mvs:
        print(f"  - {m}")

    print(f"\n=== Good MVs (using parsed_date): {len(good_mvs)} ===")
    for m in good_mvs:
        print(f"  - {m}")

    # Also check v_sales_data definition
    print("\n=== v_sales_data view definition ===")
    cur.execute("SELECT definition FROM pg_views WHERE viewname = 'v_sales_data';")
    row = cur.fetchone()
    if row:
        print(row[0][:800])
    else:
        print("v_sales_data view not found!")
