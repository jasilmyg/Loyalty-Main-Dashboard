import os, django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'myg_loyalty_dashboard.settings')
django.setup()
from django.db import connection

print("=== TRUE REPEAT CUSTOMERS: AMJ 2026 ===")
print("Definition: Purchased in Apr/May/Jun 2026")
print("           AND purchased at least once in 2020 - March 2026")
print()

with connection.cursor() as cur:

    # ── Total AMJ 2026 unique customers (all, including new) ─────────────────
    cur.execute("""
        SELECT COUNT(DISTINCT "Customer Mobile")
        FROM sales_data
        WHERE parsed_date >= '2026-04-01'
          AND parsed_date <  '2026-07-01'
          AND "Customer Mobile" IS NOT NULL
          AND "Customer Mobile" != ''
    """)
    total_amj = cur.fetchone()[0]

    # ── TRUE REPEAT: purchased AMJ 2026 + has history before Apr 2026 ────────
    cur.execute("""
        SELECT COUNT(DISTINCT amj."Customer Mobile") AS true_repeat_customers
        FROM sales_data amj
        WHERE amj.parsed_date >= '2026-04-01'
          AND amj.parsed_date <  '2026-07-01'
          AND amj."Customer Mobile" IS NOT NULL
          AND amj."Customer Mobile" != ''
          AND EXISTS (
              SELECT 1
              FROM sales_data hist
              WHERE hist."Customer Mobile" = amj."Customer Mobile"
                AND hist.parsed_date >= '2020-01-01'
                AND hist.parsed_date <  '2026-04-01'
          )
    """)
    true_repeat = cur.fetchone()[0]

    # ── NEW customers in AMJ 2026 (never purchased before Apr 2026) ───────────
    new_customers = total_amj - true_repeat

    # ── Month-by-month breakdown of TRUE REPEAT ───────────────────────────────
    cur.execute("""
        SELECT
            EXTRACT(MONTH FROM amj.parsed_date)::int AS mo,
            COUNT(DISTINCT amj."Customer Mobile") AS repeat_customers
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
        GROUP BY mo
        ORDER BY mo
    """)
    monthly = cur.fetchall()

    # ── AMJ 2025 true repeat (same logic, baseline comparison) ───────────────
    cur.execute("""
        SELECT COUNT(DISTINCT amj."Customer Mobile")
        FROM sales_data amj
        WHERE amj.parsed_date >= '2025-04-01'
          AND amj.parsed_date <  '2025-07-01'
          AND amj."Customer Mobile" IS NOT NULL
          AND amj."Customer Mobile" != ''
          AND EXISTS (
              SELECT 1 FROM sales_data hist
              WHERE hist."Customer Mobile" = amj."Customer Mobile"
                AND hist.parsed_date >= '2020-01-01'
                AND hist.parsed_date <  '2025-04-01'
          )
    """)
    amj_2025_repeat = cur.fetchone()[0]

    # ── AMJ 2024 true repeat ──────────────────────────────────────────────────
    cur.execute("""
        SELECT COUNT(DISTINCT amj."Customer Mobile")
        FROM sales_data amj
        WHERE amj.parsed_date >= '2024-04-01'
          AND amj.parsed_date <  '2024-07-01'
          AND amj."Customer Mobile" IS NOT NULL
          AND amj."Customer Mobile" != ''
          AND EXISTS (
              SELECT 1 FROM sales_data hist
              WHERE hist."Customer Mobile" = amj."Customer Mobile"
                AND hist.parsed_date >= '2020-01-01'
                AND hist.parsed_date <  '2024-04-01'
          )
    """)
    amj_2024_repeat = cur.fetchone()[0]

    # ── Print results ─────────────────────────────────────────────────────────
    names = {4: 'April', 5: 'May', 6: 'June'}
    print(f"Month-by-Month TRUE Repeat Customers in AMJ 2026:")
    for mo, cnt in monthly:
        print(f"  {names.get(mo, mo):<8} 2026:  {cnt:>10,}")
    print(f"  {'TOTAL':<8} 2026:  {true_repeat:>10,}")
    print()
    print(f"Total unique AMJ 2026 customers:   {total_amj:>10,}")
    print(f"TRUE REPEAT (had history before):  {true_repeat:>10,}")
    print(f"NEW customers (first time ever):   {new_customers:>10,}")
    print(f"Repeat rate:                       {(true_repeat/total_amj)*100:>9.1f}%")
    print()
    print(f"--- Benchmark ---")
    print(f"AMJ 2024 true repeat:  {amj_2024_repeat:>10,}")
    print(f"AMJ 2025 true repeat:  {amj_2025_repeat:>10,}")
    print(f"AMJ 2026 true repeat:  {true_repeat:>10,}  (Apr+May only)")
    print()
    target = 400000
    print(f"TARGET:  {target:>10,}")
    print(f"ACTUAL:  {true_repeat:>10,}")
    if true_repeat >= target:
        print(f"STATUS:  TARGET ACHIEVED! (+{true_repeat - target:,} surplus)")
    else:
        print(f"STATUS:  {target - true_repeat:,} more needed to hit target")
        if not any(mo == 6 for mo, _ in monthly):
            avg = true_repeat / max(len(monthly), 1)
            projected = true_repeat + avg
            print(f"         With June projected (~{avg:,.0f}): {projected:,.0f}")
            if projected >= target:
                print("         ON TRACK to achieve target!")
