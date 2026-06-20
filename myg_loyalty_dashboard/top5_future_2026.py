import os, django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'myg_loyalty_dashboard.settings')
django.setup()
from django.db import connection

print("=== Top 5 FUTURE Stores by Revenue in 2026 ===\n")
with connection.cursor() as cur:
    cur.execute("""
        SELECT "Branch",
               SUM(revenue)  AS total_revenue,
               SUM(invoices) AS total_invoices,
               SUM(customers) AS total_customers
        FROM mv_monthly_summary
        WHERE "Branch" ILIKE '%FUTURE%'
          AND EXTRACT(YEAR FROM month_date) = 2026
        GROUP BY "Branch"
        ORDER BY total_revenue DESC
        LIMIT 5;
    """)
    rows = cur.fetchall()
    print(f"{'Rank':<5} {'Branch':<35} {'Revenue (Cr)':<16} {'Invoices':<12} {'Customers'}")
    print("-" * 80)
    for i, (branch, rev, inv, cust) in enumerate(rows, 1):
        print(f"{i:<5} {branch:<35} Rs.{rev/1e7:>10.2f} Cr   {int(inv):>10,}   {int(cust):>10,}")
