import os, django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'myg_loyalty_dashboard.settings')
django.setup()
from django.db import connection

BRANCH = 'FALNIR FUTURE'
dates = ['2026-06-27', '2026-06-28']

with connection.cursor() as cur:

    # ── PER-DAY (each day independent, customer once per day) ─────────────────
    print("PER-DAY BREAKDOWN")
    print("=" * 65)
    day_results = {}
    for date_str in dates:
        cur.execute("""
            WITH day_customers AS (
                SELECT DISTINCT "Customer Mobile" AS mobile
                FROM sales_data
                WHERE parsed_date::date = %s
                  AND UPPER(TRIM("Branch")) = %s
                  AND "Customer Mobile" IS NOT NULL AND "Customer Mobile" != ''
            ),
            first_purchase AS (
                SELECT "Customer Mobile" AS mobile, MIN(parsed_date::date) AS first_date
                FROM sales_data
                WHERE "Customer Mobile" IN (SELECT mobile FROM day_customers)
                GROUP BY "Customer Mobile"
            )
            SELECT
                COUNT(*) AS total,
                COUNT(CASE WHEN first_date = %s THEN 1 END) AS new_c,
                COUNT(CASE WHEN first_date < %s  THEN 1 END) AS repeat_c
            FROM first_purchase;
        """, [date_str, BRANCH, date_str, date_str])

        total, new_c, repeat_c = cur.fetchone()
        day_results[date_str] = (total, new_c, repeat_c)
        pct_new    = round((new_c    / total) * 100, 2) if total else 0
        pct_repeat = round((repeat_c / total) * 100, 2) if total else 0
        print(f"  {date_str}: Total={total}  New={new_c} ({pct_new}%)  Repeat={repeat_c} ({pct_repeat}%)")

    # ── COMBINED (unique customers across BOTH days, counted ONCE) ────────────
    print("\nCOMBINED TOTAL (unique customers across 27 & 28 June)")
    print("=" * 65)
    cur.execute("""
        WITH period_customers AS (
            -- Unique customers who visited on EITHER day (deduplicated)
            SELECT DISTINCT "Customer Mobile" AS mobile
            FROM sales_data
            WHERE parsed_date::date IN ('2026-06-27', '2026-06-28')
              AND UPPER(TRIM("Branch")) = %s
              AND "Customer Mobile" IS NOT NULL AND "Customer Mobile" != ''
        ),
        first_purchase AS (
            -- Their first-ever purchase date across ALL time & branches
            SELECT "Customer Mobile" AS mobile, MIN(parsed_date::date) AS first_date
            FROM sales_data
            WHERE "Customer Mobile" IN (SELECT mobile FROM period_customers)
            GROUP BY "Customer Mobile"
        )
        SELECT
            COUNT(*) AS total,
            -- New = first purchase ever falls within our 2-day window
            COUNT(CASE WHEN first_date >= '2026-06-27' AND first_date <= '2026-06-28' THEN 1 END) AS new_c,
            -- Repeat = had at least one purchase BEFORE 27 June
            COUNT(CASE WHEN first_date < '2026-06-27' THEN 1 END) AS repeat_c
        FROM first_purchase;
    """, [BRANCH])

    total, new_c, repeat_c = cur.fetchone()
    pct_new    = round((new_c    / total) * 100, 2) if total else 0
    pct_repeat = round((repeat_c / total) * 100, 2) if total else 0

    # How many visited BOTH days?
    cur.execute("""
        SELECT COUNT(DISTINCT a."Customer Mobile")
        FROM sales_data a
        JOIN sales_data b ON a."Customer Mobile" = b."Customer Mobile"
        WHERE a.parsed_date::date = '2026-06-27'
          AND b.parsed_date::date = '2026-06-28'
          AND UPPER(TRIM(a."Branch")) = %s
          AND UPPER(TRIM(b."Branch")) = %s
          AND a."Customer Mobile" IS NOT NULL AND a."Customer Mobile" != '';
    """, [BRANCH, BRANCH])
    both_days = cur.fetchone()[0]

    print(f"  Customers who visited on 27 June ONLY  : {day_results['2026-06-27'][0] - both_days}")
    print(f"  Customers who visited on 28 June ONLY  : {day_results['2026-06-28'][0] - both_days}")
    print(f"  Customers who visited on BOTH days     : {both_days}")
    print(f"  -----------------------------------------")
    print(f"  Total UNIQUE customers (27+28 combined): {total}")
    print(f"  New Customer    : {new_c} ({pct_new}%)")
    print(f"  Repeat Customer : {repeat_c} ({pct_repeat}%)")
    print("=" * 65)
