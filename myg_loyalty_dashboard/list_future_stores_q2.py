import sys, os, django
sys.stdout.reconfigure(encoding='utf-8')
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'myg_loyalty_dashboard.settings')
django.setup()
from django.db import connection

with connection.cursor() as cur:
    cur.execute("""
        SELECT
            ROW_NUMBER() OVER (ORDER BY "Branch") AS no,
            "Branch",
            COUNT(DISTINCT EXTRACT(MONTH FROM month_date)) AS months_active,
            SUM(revenue)   AS total_revenue,
            SUM(invoices)  AS total_invoices,
            SUM(customers) AS total_customers
        FROM mv_monthly_summary
        WHERE "Branch" ILIKE '%FUTURE%'
          AND EXTRACT(YEAR FROM month_date) = 2026
          AND EXTRACT(MONTH FROM month_date) IN (4, 5, 6)
        GROUP BY "Branch"
        ORDER BY "Branch";
    """)
    rows = cur.fetchall()

print()
print('=' * 105)
print('  ALL FUTURE STORES LIST — Q2 2026 (April + May + June)')
print(f'  Total Stores: {len(rows)}')
print('=' * 105)
print(f'{"No.":<5} {"Store Name":<42} {"Months":^8} {"Q2 Revenue":>18} {"Invoices":>10} {"Customers":>10}')
print('-' * 105)

for no, branch, months, rev, inv, cust in rows:
    months_str = f'{int(months)}/3'
    flag = ' *' if int(months) < 3 else ''
    print(f'{int(no):<5} {branch:<42} {months_str:^8} Rs.{float(rev):>12,.0f}   {int(inv):>8,}   {int(cust):>8,}{flag}')

print('=' * 105)
print(f'  Total: {len(rows)} Future Stores   (* = opened mid-quarter, active < 3 months)')
print('=' * 105)
