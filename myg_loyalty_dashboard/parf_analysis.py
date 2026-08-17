"""
MY PARF Comprehensive Analysis — All data from azure_sales_report + item_master
"""
import os; os.environ.setdefault('DJANGO_SETTINGS_MODULE','myg_loyalty_dashboard.settings')
import django; django.setup()
from analytics.clickhouse_service import get_ch_client
ch = get_ch_client()

SEP = '=' * 70

print(SEP)
print('  MY PARF — COMPREHENSIVE SALES ANALYSIS REPORT')
print('  Data up to: Aug 16, 2026')
print(SEP)

# ── 1. Overall Summary ────────────────────────────────────────
print('\n[1] OVERALL SUMMARY')
print('-' * 50)
r = ch.query("""
    SELECT
        sum(s.sold_price)        AS total_revenue,
        sum(s.qty)               AS total_qty,
        countDistinct(s.invoice_no) AS total_invoices,
        countDistinct(s.branch)  AS branches_selling,
        avg(s.sold_price/s.qty)  AS avg_selling_price,
        min(toDate(s.date))      AS first_sale,
        max(toDate(s.date))      AS last_sale
    FROM azure_sales_report s
    JOIN item_master m ON s.item_code = m.item_code
    WHERE m.brand = 'MY PARF'
      AND s.sold_price > 0
      AND toDate(s.date) != '1970-01-01'
""").result_rows[0]
print(f'  Total Revenue        : Rs. {float(r[0]):>15,.0f}  ({float(r[0])/1e7:.2f} Cr)')
print(f'  Total Qty Sold       : {int(r[1]):>15,} units')
print(f'  Total Invoices       : {int(r[2]):>15,}')
print(f'  Branches Selling     : {int(r[3]):>15,}')
print(f'  Avg Selling Price    : Rs. {float(r[4]):>12,.0f}')
print(f'  First Sale           : {r[5]}')
print(f'  Last Sale            : {r[6]}')

# ── 2. Product-wise Breakdown ─────────────────────────────────
print('\n[2] TOP PRODUCTS BY REVENUE')
print('-' * 70)
rows = ch.query("""
    SELECT
        m.item_name,
        m.item_code,
        m.mrp,
        sum(s.sold_price)          AS revenue,
        sum(s.qty)                 AS qty,
        countDistinct(s.invoice_no) AS invoices,
        avg(s.sold_price/s.qty)    AS avg_price,
        (m.mrp - avg(s.sold_price/s.qty)) / m.mrp * 100 AS discount_pct
    FROM azure_sales_report s
    JOIN item_master m ON s.item_code = m.item_code
    WHERE m.brand = 'MY PARF' AND s.sold_price > 0 AND toDate(s.date) != '1970-01-01'
    GROUP BY m.item_name, m.item_code, m.mrp
    ORDER BY revenue DESC
    LIMIT 15
""").result_rows
print(f'  {"ITEM NAME":<35} {"MRP":>7} {"AVG PRICE":>10} {"DISC%":>6} {"QTY":>7} {"REVENUE":>14}')
print('  ' + '-' * 65)
for r in rows:
    name = str(r[0])[:34] if r[0] else r[1]
    print(f'  {name:<35} {float(r[2]):>7,.0f} {float(r[6]):>10,.0f} {float(r[7]):>5.1f}% {int(r[4]):>7,} Rs.{float(r[3]):>11,.0f}')

# ── 3. Monthly Trend ──────────────────────────────────────────
print('\n[3] MONTHLY SALES TREND')
print('-' * 65)
rows = ch.query("""
    SELECT
        formatDateTime(toStartOfMonth(s.date), '%Y-%m') AS month,
        sum(s.sold_price)           AS revenue,
        sum(s.qty)                  AS qty,
        countDistinct(s.invoice_no) AS invoices,
        avg(s.sold_price/s.qty)     AS avg_price
    FROM azure_sales_report s
    JOIN item_master m ON s.item_code = m.item_code
    WHERE m.brand = 'MY PARF' AND s.sold_price > 0 AND toDate(s.date) != '1970-01-01'
    GROUP BY month
    ORDER BY month ASC
""").result_rows
prev_rev = 0
print(f'  {"MONTH":<12} {"REVENUE":>14} {"MOM%":>7} {"QTY":>8} {"INVOICES":>10} {"AVG PRICE":>10}')
print('  ' + '-' * 60)
for r in rows:
    rev = float(r[1])
    mom = ((rev - prev_rev) / prev_rev * 100) if prev_rev > 0 else 0
    arrow = '+' if mom >= 0 else ''
    print(f'  {r[0]:<12} Rs.{rev:>11,.0f} {arrow}{mom:>6.1f}% {int(r[2]):>8,} {int(r[3]):>10,} Rs.{float(r[4]):>7,.0f}')
    prev_rev = rev

# ── 4. Branch-wise Breakdown ──────────────────────────────────
print('\n[4] TOP 15 BRANCHES BY REVENUE')
print('-' * 65)
rows = ch.query("""
    SELECT
        s.branch,
        sum(s.sold_price)           AS revenue,
        sum(s.qty)                  AS qty,
        countDistinct(s.invoice_no) AS invoices,
        avg(s.sold_price/s.qty)     AS avg_price
    FROM azure_sales_report s
    JOIN item_master m ON s.item_code = m.item_code
    WHERE m.brand = 'MY PARF' AND s.sold_price > 0 AND toDate(s.date) != '1970-01-01'
    GROUP BY s.branch
    ORDER BY revenue DESC
    LIMIT 15
""").result_rows
total_rev = sum(float(r[1]) for r in rows)
print(f'  {"BRANCH":<35} {"REVENUE":>14} {"SHARE%":>7} {"QTY":>7} {"INV":>7}')
print('  ' + '-' * 65)
for r in rows:
    pct = float(r[1]) / total_rev * 100 if total_rev else 0
    print(f'  {str(r[0]):<35} Rs.{float(r[1]):>11,.0f} {pct:>6.1f}% {int(r[2]):>7,} {int(r[3]):>7,}')

# ── 5. Price Tier Analysis ────────────────────────────────────
print('\n[5] PRICE TIER BREAKDOWN (MRP)')
print('-' * 50)
rows = ch.query("""
    SELECT
        multiIf(m.mrp < 500, 'Below Rs.500',
                m.mrp < 1000, 'Rs.500-999',
                m.mrp < 1500, 'Rs.1000-1499',
                m.mrp < 2000, 'Rs.1500-1999',
                'Rs.2000+') AS tier,
        count(DISTINCT m.item_code)  AS sku_count,
        sum(s.qty)                   AS qty,
        sum(s.sold_price)            AS revenue
    FROM azure_sales_report s
    JOIN item_master m ON s.item_code = m.item_code
    WHERE m.brand = 'MY PARF' AND s.sold_price > 0 AND toDate(s.date) != '1970-01-01'
    GROUP BY tier
    ORDER BY revenue DESC
""").result_rows
print(f'  {"PRICE TIER":<20} {"SKUs":>6} {"QTY":>8} {"REVENUE":>14}')
print('  ' + '-' * 50)
for r in rows:
    print(f'  {str(r[0]):<20} {int(r[1]):>6} {int(r[2]):>8,} Rs.{float(r[3]):>11,.0f}')

# ── 6. YTD 2026 vs 2025 Comparison ───────────────────────────
print('\n[6] YEAR COMPARISON (2025 vs 2026 YTD)')
print('-' * 55)
for year in [2025, 2026]:
    r = ch.query(f"""
        SELECT sum(s.sold_price), sum(s.qty), countDistinct(s.invoice_no)
        FROM azure_sales_report s
        JOIN item_master m ON s.item_code = m.item_code
        WHERE m.brand = 'MY PARF' AND s.sold_price > 0
          AND toYear(s.date) = {year}
          AND toDate(s.date) != '1970-01-01'
    """).result_rows[0]
    print(f'  {year}  Rev=Rs.{float(r[0]):>12,.0f}  Qty={int(r[1]):>8,}  Inv={int(r[2]):>8,}')

# ── 7. Best Selling Day / Weekday ─────────────────────────────
print('\n[7] SALES BY DAY OF WEEK')
print('-' * 50)
rows = ch.query("""
    SELECT
        toDayOfWeek(s.date) AS dow,
        ['Mon','Tue','Wed','Thu','Fri','Sat','Sun'][toDayOfWeek(s.date)] AS day_name,
        sum(s.sold_price) AS revenue,
        sum(s.qty) AS qty
    FROM azure_sales_report s
    JOIN item_master m ON s.item_code = m.item_code
    WHERE m.brand = 'MY PARF' AND s.sold_price > 0 AND toDate(s.date) != '1970-01-01'
    GROUP BY dow, day_name
    ORDER BY dow
""").result_rows
for r in rows:
    bar = '#' * int(float(r[2]) / max(float(x[2]) for x in rows) * 30)
    print(f'  {r[1]}  {bar:<30}  Rs.{float(r[2]):>10,.0f}  qty={int(r[3]):>6,}')

# ── 8. Recent 30 Days Trend ───────────────────────────────────
print('\n[8] LAST 30 DAYS — DAILY TREND')
print('-' * 65)
rows = ch.query("""
    SELECT
        toDate(s.date)              AS day,
        sum(s.sold_price)           AS revenue,
        sum(s.qty)                  AS qty,
        countDistinct(s.invoice_no) AS invoices
    FROM azure_sales_report s
    JOIN item_master m ON s.item_code = m.item_code
    WHERE m.brand = 'MY PARF' AND s.sold_price > 0
      AND toDate(s.date) >= today() - 30
      AND toDate(s.date) != '1970-01-01'
    GROUP BY day
    ORDER BY day ASC
""").result_rows
print(f'  {"DATE":<13} {"REVENUE":>13} {"QTY":>6} {"INVOICES":>9}')
print('  ' + '-' * 45)
for r in rows:
    print(f'  {str(r[0]):<13} Rs.{float(r[1]):>10,.0f} {int(r[2]):>6,} {int(r[3]):>9,}')

print()
print(SEP)
print('  END OF MY PARF ANALYSIS REPORT')
print(SEP)
