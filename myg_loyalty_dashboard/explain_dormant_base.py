import os, sys, django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'myg_loyalty_dashboard.settings')
django.setup()
from django.db import connection

print("=" * 65)
print("  2024 Cohort Dormant Base 10,61,484 vs AI Answer 12,89,594")
print("=" * 65)

with connection.cursor() as cur:

    cur.execute("SELECT COUNT(*) FROM mv_cohort_cross_year WHERE in_2024 = 1")
    a = cur.fetchone()[0]
    print(f"\nA. Purchased in 2024 (any time):             {a:>10,}")

    cur.execute("SELECT COUNT(*) FROM mv_cohort_cross_year WHERE in_2024 = 1 AND in_2026 = 0")
    b = cur.fetchone()[0]
    print(f"B. Purchased 2024, NOT in 2026 (AI ans):     {b:>10,}  << 12,89,594")

    cur.execute("SELECT COUNT(*) FROM mv_cohort_cross_year WHERE in_2024 = 1 AND in_2025 = 0 AND in_2026 = 0")
    d = cur.fetchone()[0]
    print(f"C. Purchased 2024, NOT in 2025, NOT 2026:    {d:>10,}")

    cur.execute("SELECT COUNT(*) FROM mv_cohort_cross_year WHERE in_2024 = 1 AND in_2025 = 0")
    e = cur.fetchone()[0]
    print(f"D. Purchased 2024, NOT in 2025 at all:       {e:>10,}")

    cur.execute("SELECT COALESCE(SUM(unique_customers), 0) FROM mv_dormant_reactivation WHERE cohort_year = 2024")
    f = cur.fetchone()[0]
    print(f"E. mv_dormant_reactivation 2024:             {f:>10,}  << Dashboard 10,61,484")

    # check what columns exist in mv_dormant_reactivation
    cur.execute("""
        SELECT a.attname FROM pg_attribute a
        JOIN pg_class c ON a.attrelid = c.oid
        WHERE c.relname = 'mv_dormant_reactivation' AND a.attnum > 0 AND NOT a.attisdropped
        ORDER BY a.attnum
    """)
    cols = [r[0] for r in cur.fetchall()]
    print(f"\nmv_dormant_reactivation columns: {cols}")

    cur.execute("SELECT * FROM mv_dormant_reactivation LIMIT 3")
    rows = cur.fetchall()
    print(f"Sample rows:")
    for r in rows:
        print(f"  {r}")

    print()
    print("=" * 65)
    target = 1061484
    print(f"  Dashboard shows: 10,61,484")
    print(f"  AI Answer shows: 12,89,594")
    print()
    print(f"  A matches dashboard: {a == target}")
    print(f"  B matches dashboard: {b == target}")
    print(f"  C matches dashboard: {d == target}")
    print(f"  D matches dashboard: {e == target}")
    print(f"  E matches dashboard: {f == target}")
    print("=" * 65)
