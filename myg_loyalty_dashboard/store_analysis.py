"""
9. Average Sales per Store
10. Top 20 Stores Contribution
Last 2 years: Y1 (Aug 2024–Aug 2025) & Y2 (Aug 2025–Aug 2026)
"""
import os, sys, django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'myg_loyalty_dashboard.settings')
sys.path.insert(0, '.')
django.setup()
from analytics.clickhouse_service import get_ch_client
ch = get_ch_client()

Y2_S, Y2_E = '2025-08-29', '2026-08-29'
Y1_S, Y1_E = '2024-08-29', '2025-08-28'

def store_data(s, e):
    rows = ch.query(f"""
        SELECT
            s.branch                            AS branch_code,
            b.branch_name                       AS branch_name,
            b.store_type                        AS store_type,
            sum(toFloat64(s.sold_price))        AS total_sales,
            sum(toFloat64(s.qty))               AS total_qty,
            countDistinct(s.invoice_no)         AS invoices
        FROM azure_sales_report s
        LEFT JOIN branch_master b ON s.branch = b.code
        WHERE toDate(s.date) BETWEEN '{s}' AND '{e}'
          AND toDate(s.date) != '1970-01-01'
          AND s.branch != ''
        GROUP BY branch_code, branch_name, store_type
        HAVING total_sales > 0
        ORDER BY total_sales DESC
    """).result_rows
    # Filter to retail branches only (exclude WAREHOUSE, HEAD OFFICE)
    rows = [r for r in rows if str(r[2]).upper() not in ('WAREHOUSE','HEAD OFFICE','')]
    return rows

print("Fetching store data...")
r1 = store_data(Y1_S, Y1_E)
r2 = store_data(Y2_S, Y2_E)

def display(rows, period_label, year_tag):
    total_stores = len(rows)
    total_sales  = sum(r[3] for r in rows)
    total_qty    = sum(r[4] for r in rows)
    total_inv    = sum(r[5] for r in rows)
    avg_sales    = total_sales / total_stores if total_stores else 0
    avg_qty      = total_qty   / total_stores if total_stores else 0
    avg_inv      = total_inv   / total_stores if total_stores else 0
    avg_bill     = total_sales / total_inv    if total_inv    else 0

    print("\n" + "=" * 110)
    print(f"  {period_label}")
    print("=" * 110)

    print(f"\n  ─── 9. AVERAGE SALES PER STORE ─────────────────────────────")
    print(f"    Active Stores               : {total_stores:>8,}")
    print(f"    Total Revenue               : ₹{total_sales/1e7:>10,.2f} Cr")
    print(f"    Total Qty Sold              : {total_qty:>12,.0f} units")
    print(f"    Total Invoices              : {total_inv:>12,}")
    print(f"    ──────────────────────────────────────────────────")
    print(f"    Avg Revenue  / Store / Year : ₹{avg_sales/1e5:>10,.2f} L  (₹{avg_sales:,.0f})")
    print(f"    Avg Qty      / Store / Year : {avg_qty:>12,.0f} units")
    print(f"    Avg Invoices / Store / Year : {avg_inv:>12,.0f}")
    print(f"    Avg Bill Value (overall)    : ₹{avg_bill:>10,.0f}")

    print(f"\n  ─── 10. TOP 20 STORES CONTRIBUTION ─────────────────────────")
    print(f"  {'Rank':<5} {'Code':<8} {'Store Name':<35} {'Sales (Cr)':>12} {'Qty':>12} {'Invoices':>10} {'Share':>8} {'Cum.%':>9} {'Avg Bill':>12}")
    print("  " + "-" * 110)

    top20_sales = sum(r[3] for r in rows[:20])
    cum = 0
    for i, r in enumerate(rows[:20], 1):
        code   = str(r[0])[:7]
        name   = str(r[1] or r[0])[:33]
        sales  = float(r[3])
        qty    = float(r[4])
        inv    = int(r[5])
        share  = sales / total_sales * 100
        cum   += share
        ab     = sales / inv if inv else 0
        print(f"  {i:<5} {code:<8} {name:<35} {sales/1e7:>10,.2f} Cr {qty:>11,.0f} {inv:>10,} {share:>7.2f}% {cum:>8.2f}% ₹{ab:>10,.0f}")

    print("  " + "-" * 110)
    top20_pct = top20_sales / total_sales * 100
    rest_pct  = 100 - top20_pct
    print(f"  Top 20 stores : ₹{top20_sales/1e7:,.2f} Cr  = {top20_pct:.1f}% of total revenue")
    print(f"  Remaining {total_stores-20} stores : ₹{(total_sales-top20_sales)/1e7:,.2f} Cr  = {rest_pct:.1f}% of total revenue")

display(r1, f"YEAR 1: {Y1_S} → {Y1_E}", "Y1")
display(r2, f"YEAR 2: {Y2_S} → {Y2_E}", "Y2")

# ── YoY comparison for top 10 stores ────────────────────────────────────────
print("\n\n" + "=" * 90)
print("  YoY: Top 20 Stores (Y2 ranked) — Revenue Comparison")
print("=" * 90)
d1 = {r[0]: r for r in r1}
d2 = {r[0]: r for r in r2}
total_y1 = sum(r[3] for r in r1)
total_y2 = sum(r[3] for r in r2)
print(f"  {'Rank':<5} {'Store':<35} {'Y1 Sales (Cr)':>14} {'Y2 Sales (Cr)':>14} {'YoY Δ':>8} {'Y2 Share':>9}")
print("  " + "-" * 90)
for i, r2row in enumerate(r2[:20], 1):
    code = r2row[0]
    name = str(r2row[1] or code)[:32]
    s2   = float(r2row[3])
    s1   = float(d1[code][3]) if code in d1 else 0
    yoy  = (s2 - s1) / s1 * 100 if s1 > 0 else float('inf')
    share2 = s2 / total_y2 * 100
    yoy_str = f"{yoy:+.1f}%" if s1 > 0 else "  NEW"
    print(f"  {i:<5} {name:<35} {s1/1e7:>12,.2f} Cr {s2/1e7:>12,.2f} Cr {yoy_str:>8} {share2:>8.2f}%")
print("=" * 90)
