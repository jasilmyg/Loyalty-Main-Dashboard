import sys, os, django
sys.stdout.reconfigure(encoding='utf-8')
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'myg_loyalty_dashboard.settings')
django.setup()
from django.db import connection

print("\n" + "=" * 90)
print("  FUTURE STORES — TOTAL SALES: APRIL, MAY & JUNE 2026")
print("=" * 90)

# ── 0. Total distinct Future store count ──────────────────────────────────────
with connection.cursor() as cur:
    cur.execute("""
        SELECT COUNT(DISTINCT "Branch") AS total_stores
        FROM mv_monthly_summary
        WHERE "Branch" ILIKE '%FUTURE%'
          AND EXTRACT(YEAR FROM month_date) = 2026
          AND EXTRACT(MONTH FROM month_date) IN (4, 5, 6);
    """)
    total_store_count = cur.fetchone()[0]

print(f"\n  Total Future Stores Active in Q2 2026: {total_store_count}")

# ── 1. Per-store, per-month breakdown ──────────────────────────────────────────
with connection.cursor() as cur:
    cur.execute("""
        SELECT
            "Branch",
            EXTRACT(MONTH FROM month_date)::int  AS month_num,
            TO_CHAR(month_date, 'Month')          AS month_name,
            SUM(revenue)                          AS revenue,
            SUM(invoices)                         AS invoices,
            SUM(customers)                        AS customers
        FROM mv_monthly_summary
        WHERE "Branch" ILIKE '%FUTURE%'
          AND EXTRACT(YEAR  FROM month_date) = 2026
          AND EXTRACT(MONTH FROM month_date) IN (4, 5, 6)
        GROUP BY "Branch", month_num, month_name, month_date
        ORDER BY "Branch", month_num;
    """)
    rows = cur.fetchall()

if not rows:
    print("\n  ⚠  No data found for Apr-Jun 2026 in FUTURE stores.\n")
else:
    # Organise by store
    from collections import defaultdict
    store_data = defaultdict(dict)      # store → {month_num: (rev, inv, cust)}
    all_months  = {4: "April", 5: "May", 6: "June"}

    for branch, mnum, mname, rev, inv, cust in rows:
        store_data[branch][mnum] = (float(rev or 0), int(inv or 0), int(cust or 0))

    # ── Print per-store breakdown ──────────────────────────────────────────────
    print(f"\n{'Store':<35} {'Month':<10} {'Revenue':>14} {'Invoices':>10} {'Customers':>10}")
    print("-" * 80)

    store_totals = {}  # store → total_rev

    for branch in sorted(store_data.keys()):
        store_rev = 0
        for mnum, mname in sorted(all_months.items()):
            if mnum in store_data[branch]:
                rev, inv, cust = store_data[branch][mnum]
                store_rev += rev
                print(f"{branch:<35} {mname:<10} Rs.{rev:>10,.0f}   {inv:>8,}   {cust:>8,}")
            else:
                print(f"{branch:<35} {mname:<10} {'-':>14} {'-':>10} {'-':>10}")
        store_totals[branch] = store_rev
        print(f"{'':>45} {'----------':>14}")
        print(f"{'  >> Store Total':<45} Rs.{store_rev:>10,.0f}")
        print()

    # ── Month-wise grand totals ────────────────────────────────────────────────
    print("=" * 80)
    print("  GRAND TOTALS — ALL FUTURE STORES COMBINED")
    print("=" * 80)
    month_rev    = defaultdict(float)
    month_inv    = defaultdict(int)
    month_cust   = defaultdict(int)
    month_stores = defaultdict(int)   # count of stores active each month

    for branch, months in store_data.items():
        for mnum, (rev, inv, cust) in months.items():
            month_rev[mnum]    += rev
            month_inv[mnum]    += inv
            month_cust[mnum]   += cust
            month_stores[mnum] += 1

    grand_rev  = 0.0
    grand_inv  = 0
    grand_cust = 0

    print(f"\n{'Month':<12} {'Stores':>8} {'Revenue':>18} {'Invoices':>12} {'Customers':>12}")
    print("-" * 67)
    for mnum, mname in sorted(all_months.items()):
        rev    = month_rev.get(mnum, 0)
        inv    = month_inv.get(mnum, 0)
        cust   = month_cust.get(mnum, 0)
        stores = month_stores.get(mnum, 0)
        grand_rev  += rev
        grand_inv  += inv
        grand_cust += cust
        print(f"{mname:<12} {stores:>8}   Rs.{rev:>12,.0f}   {inv:>10,}   {cust:>10,}")

    print("-" * 67)
    print(f"{'TOTAL':<12} {total_store_count:>8}   Rs.{grand_rev:>12,.0f}   {grand_inv:>10,}   {grand_cust:>10,}")
    print()
    print(f"  >> Total Future Stores (Q2 2026)  : {total_store_count}")
    print(f"  >> Grand Total Revenue            : Rs. {grand_rev:,.0f}  ({grand_rev/1e7:.2f} Cr)")
    print(f"  >> Total Invoices                 : {grand_inv:,}")
    print(f"  >> Total Customers                : {grand_cust:,}")
    print("=" * 90 + "\n")
