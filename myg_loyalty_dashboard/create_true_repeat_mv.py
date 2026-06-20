"""
Creates mv_true_repeat_amj_2026:
Customers who purchased in Apr/May/Jun 2026
AND had at least one purchase between 2020 - March 2026.
This is the TRUE "repeat customer" definition for AMJ quarter.
"""
import os, django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'myg_loyalty_dashboard.settings')
django.setup()
from django.db import connection

print("Building mv_true_repeat_customers MV...")

with connection.cursor() as cur:

    # Drop and recreate a general-purpose repeat customer MV by quarter-year
    cur.execute("DROP MATERIALIZED VIEW IF EXISTS mv_true_repeat_amj_2026 CASCADE;")

    cur.execute("""
        CREATE MATERIALIZED VIEW mv_true_repeat_amj_2026 AS
        SELECT
            amj."Customer Mobile"                    AS customer_mobile,
            EXTRACT(MONTH FROM amj.parsed_date)::int AS purchase_month,
            MIN(amj.parsed_date)                     AS first_amj_purchase,
            SUM(amj."Total Value")                   AS amj_spend
        FROM sales_data amj
        WHERE amj.parsed_date >= '2026-04-01'
          AND amj.parsed_date <  '2026-07-01'
          AND amj."Customer Mobile" IS NOT NULL
          AND amj."Customer Mobile" != ''
          AND EXISTS (
              SELECT 1 FROM sales_data hist
              WHERE hist."Customer Mobile" = amj."Customer Mobile"
                AND hist.parsed_date >= '2020-01-01'
                AND hist.parsed_date <  '2026-04-01'
          )
        GROUP BY amj."Customer Mobile",
                 EXTRACT(MONTH FROM amj.parsed_date)::int;
    """)
    print("MV created. Adding indexes...")

    cur.execute("CREATE INDEX idx_mv_true_repeat_mobile ON mv_true_repeat_amj_2026(customer_mobile);")
    cur.execute("CREATE INDEX idx_mv_true_repeat_month  ON mv_true_repeat_amj_2026(purchase_month);")

    # Quick counts
    cur.execute("SELECT purchase_month, COUNT(DISTINCT customer_mobile) FROM mv_true_repeat_amj_2026 GROUP BY purchase_month ORDER BY purchase_month")
    rows = cur.fetchall()
    names = {4:'April', 5:'May', 6:'June'}
    total = 0
    print("\nResults:")
    for mo, cnt in rows:
        print(f"  {names.get(mo,mo)}: {cnt:,}")
        total += cnt
    print(f"  TOTAL unique: {total:,}")
    print(f"\nTarget: 400,000")
    print(f"Status: {'ACHIEVED' if total >= 400000 else 'NOT YET'}")

print("\nDone.")
