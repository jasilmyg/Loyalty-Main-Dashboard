import os, sys, json
sys.stdout.reconfigure(encoding='utf-8')
sys.path.insert(0, '.')
os.environ['DJANGO_SETTINGS_MODULE'] = 'myg_loyalty_dashboard.settings'
import django; django.setup()
from analytics.clickhouse_service import get_ch_client
import time
client = get_ch_client()

TODAY = '2026-08-14'
JAS24_S, JAS24_E = '2024-07-01', '2024-09-30'
JAS25_S, JAS25_E = '2025-07-01', '2025-09-30'
JAS26_S = '2026-07-01'

# BASE POOL — JAS Loyalists: bought JAS 2024 AND JAS 2025, NOT in JAS 2026
LOYALIST_POOL = f"""
    SELECT DISTINCT customer_mobile FROM azure_invoice_report
    WHERE toDate(date) BETWEEN toDate('{JAS24_S}') AND toDate('{JAS24_E}')
      AND invoice_total > 0 AND length(customer_mobile)=10
      AND customer_mobile NOT IN ('1313131313','0000000000','9999999999')
      AND customer_mobile IN (
          SELECT DISTINCT customer_mobile FROM azure_invoice_report
          WHERE toDate(date) BETWEEN toDate('{JAS25_S}') AND toDate('{JAS25_E}')
            AND invoice_total > 0
      )
      AND customer_mobile NOT IN (
          SELECT DISTINCT customer_mobile FROM azure_invoice_report
          WHERE toDate(date) >= toDate('{JAS26_S}')
            AND toDate(date) <= toDate('{TODAY}')
            AND invoice_total > 0
      )
"""

print('='*65)
print('JAS LOYALISTS — DEEP BEHAVIORAL ANALYSIS')
print('Customers who bought JAS 2024 + JAS 2025, NOT yet JAS 2026')
print('='*65)

# 1. Overall count
t = time.time()
r = client.query(f'SELECT countDistinct(customer_mobile) FROM azure_invoice_report WHERE customer_mobile IN ({LOYALIST_POOL})')
total = r.result_rows[0][0]
print(f'\n[1] TOTAL LOYALISTS: {total:,}  ({time.time()-t:.1f}s)')

# 2. Full RFM profile — lifetime stats per customer
t = time.time()
r2 = client.query(f"""
    SELECT
        countDistinct(invoice_no)                       AS total_invoices,
        round(sum(invoice_total), 0)                    AS lifetime_spend,
        round(avg(invoice_total), 0)                    AS avg_basket,
        round(max(invoice_total), 0)                    AS max_basket,
        dateDiff('day', toDate(min(date)), toDate('{TODAY}')) AS tenure_days,
        dateDiff('day', toDate(max(date)), toDate('{TODAY}')) AS recency_days,
        countDistinct(toYYYYMM(date))                   AS active_months
    FROM azure_invoice_report
    WHERE customer_mobile IN ({LOYALIST_POOL})
      AND invoice_total > 0
""")
row = r2.result_rows[0]
print(f'\n[2] LIFETIME PURCHASE BEHAVIOUR:')
print(f'    Total transactions       : {row[0]:>12,}')
print(f'    Total lifetime spend     : Rs.{row[1]:>10,.0f}')
print(f'    Avg basket size (ATV)    : Rs.{row[2]:>10,.0f}')
print(f'    Max single purchase      : Rs.{row[3]:>10,.0f}')
print(f'    Avg customer tenure      : {row[4]//365} yrs {(row[4]%365)//30} months')
print(f'    Avg recency (days ago)   : {row[5]:>12,} days')
print(f'    Avg active months        : {row[6]:>12,}')
print(f'    Time: {time.time()-t:.1f}s')

# 3. RFM bucket distribution
t = time.time()
r3 = client.query(f"""
    SELECT
        countDistinct(invoice_no) AS freq_bucket,
        count() AS cust_count
    FROM (
        SELECT customer_mobile,
               countDistinct(invoice_no) AS freq_bucket
        FROM azure_invoice_report
        WHERE customer_mobile IN ({LOYALIST_POOL})
          AND invoice_total > 0
        GROUP BY customer_mobile
    )
    GROUP BY freq_bucket
    ORDER BY freq_bucket
""")
print(f'\n[3] PURCHASE FREQUENCY DISTRIBUTION:')
buckets = {'1':0,'2-3':0,'4-5':0,'6-10':0,'11-20':0,'21+':0}
for row in r3.result_rows:
    f, c = int(row[0]), int(row[1])
    if f==1: buckets['1'] += c
    elif f<=3: buckets['2-3'] += c
    elif f<=5: buckets['4-5'] += c
    elif f<=10: buckets['6-10'] += c
    elif f<=20: buckets['11-20'] += c
    else: buckets['21+'] += c
for k,v in buckets.items():
    bar = '#'*int(v/max(buckets.values())*30)
    print(f'    {k:>6} purchases : {v:>7,}  {bar}')
print(f'    Time: {time.time()-t:.1f}s')

# 4. Recency buckets
t = time.time()
r4 = client.query(f"""
    SELECT
        multiIf(recency_days <= 30,  'Within 30 days',
                recency_days <= 90,  '31-90 days',
                recency_days <= 180, '91-180 days',
                recency_days <= 365, '181-365 days',
                'Over 1 year') AS recency_bucket,
        count() AS cnt
    FROM (
        SELECT customer_mobile,
               dateDiff('day', toDate(max(date)), toDate('{TODAY}')) AS recency_days
        FROM azure_invoice_report
        WHERE customer_mobile IN ({LOYALIST_POOL})
          AND invoice_total > 0
        GROUP BY customer_mobile
    )
    GROUP BY recency_bucket
    ORDER BY cnt DESC
""")
print(f'\n[4] RECENCY DISTRIBUTION (last purchase):')
for row in r4.result_rows:
    pct = row[1]/total*100
    print(f'    {row[0]:20}: {row[1]:>8,}  ({pct:.1f}%)')
print(f'    Time: {time.time()-t:.1f}s')

# 5. Top categories bought (using azure_sales_report + item_master)
t = time.time()
r5 = client.query(f"""
    SELECT
        im.category,
        countDistinct(sr.invoice_no) AS invoices,
        round(sum(sr.sold_price), 0) AS revenue,
        round(sum(sr.qty), 0)        AS qty
    FROM azure_sales_report sr
    JOIN item_master im ON sr.item_code = im.item_code
    WHERE sr.invoice_no IN (
        SELECT DISTINCT invoice_no FROM azure_invoice_report
        WHERE customer_mobile IN ({LOYALIST_POOL})
          AND invoice_total > 0
    )
    GROUP BY im.category
    ORDER BY revenue DESC
    LIMIT 15
""")
print(f'\n[5] TOP CATEGORIES PURCHASED:')
print(f'    {"Category":30} {"Invoices":>10} {"Revenue":>14} {"Qty":>8}')
for row in r5.result_rows:
    print(f'    {str(row[0]):30} {int(row[1]):>10,} Rs.{int(row[2]):>12,.0f} {int(row[3]):>8,}')
print(f'    Time: {time.time()-t:.1f}s')

# 6. Top brands
t = time.time()
r6 = client.query(f"""
    SELECT
        im.brand,
        countDistinct(sr.invoice_no) AS invoices,
        round(sum(sr.sold_price), 0) AS revenue
    FROM azure_sales_report sr
    JOIN item_master im ON sr.item_code = im.item_code
    WHERE sr.invoice_no IN (
        SELECT DISTINCT invoice_no FROM azure_invoice_report
        WHERE customer_mobile IN ({LOYALIST_POOL})
          AND invoice_total > 0
    )
    GROUP BY im.brand
    ORDER BY revenue DESC
    LIMIT 15
""")
print(f'\n[6] TOP BRANDS PURCHASED:')
for i, row in enumerate(r6.result_rows, 1):
    print(f'    {i:>2}. {str(row[0]):30} {int(row[1]):>8,} invoices  Rs.{int(row[2]):>12,.0f}')
print(f'    Time: {time.time()-t:.1f}s')

# 7. Branch distribution
t = time.time()
r7 = client.query(f"""
    SELECT branch, countDistinct(customer_mobile) AS custs, round(sum(invoice_total),0) AS rev
    FROM azure_invoice_report
    WHERE customer_mobile IN ({LOYALIST_POOL})
      AND invoice_total > 0
    GROUP BY branch ORDER BY custs DESC LIMIT 20
""")
print(f'\n[7] TOP BRANCHES (where these customers shop):')
for row in r7.result_rows:
    print(f'    {str(row[0]):10} {int(row[1]):>7,} customers  Rs.{int(row[2]):>12,.0f}')
print(f'    Time: {time.time()-t:.1f}s')

# 8. Financier / payment method
t = time.time()
r8 = client.query(f"""
    SELECT financier_name, countDistinct(customer_mobile) AS custs, round(sum(invoice_total),0) AS rev
    FROM azure_invoice_report
    WHERE customer_mobile IN ({LOYALIST_POOL})
      AND invoice_total > 0 AND financier_name != ''
    GROUP BY financier_name ORDER BY custs DESC LIMIT 15
""")
print(f'\n[8] PAYMENT/FINANCIER PREFERENCE:')
for row in r8.result_rows:
    print(f'    {str(row[0]):30} {int(row[1]):>7,} customers  Rs.{int(row[2]):>12,.0f}')
print(f'    Time: {time.time()-t:.1f}s')

# 9. Spend tier segmentation
t = time.time()
r9 = client.query(f"""
    SELECT
        multiIf(lifetime_spend < 5000,    'Under Rs.5K',
                lifetime_spend < 20000,   'Rs.5K-20K',
                lifetime_spend < 50000,   'Rs.20K-50K',
                lifetime_spend < 100000,  'Rs.50K-1L',
                lifetime_spend < 500000,  'Rs.1L-5L',
                'Rs.5L+') AS tier,
        count() AS custs,
        round(avg(lifetime_spend),0) AS avg_spend
    FROM (
        SELECT customer_mobile, sum(invoice_total) AS lifetime_spend
        FROM azure_invoice_report
        WHERE customer_mobile IN ({LOYALIST_POOL})
          AND invoice_total > 0
        GROUP BY customer_mobile
    )
    GROUP BY tier ORDER BY avg_spend DESC
""")
print(f'\n[9] LIFETIME SPEND TIERS:')
for row in r9.result_rows:
    print(f'    {str(row[0]):20}: {int(row[1]):>7,} customers  avg=Rs.{int(row[2]):>10,.0f}')
print(f'    Time: {time.time()-t:.1f}s')

# 10. Cross-category analysis — what do they buy BESIDES JAS products?
t = time.time()
r10 = client.query(f"""
    SELECT
        im.product,
        countDistinct(sr.invoice_no) AS invoices,
        round(sum(sr.sold_price), 0) AS revenue
    FROM azure_sales_report sr
    JOIN item_master im ON sr.item_code = im.item_code
    WHERE sr.invoice_no IN (
        SELECT DISTINCT invoice_no FROM azure_invoice_report
        WHERE customer_mobile IN ({LOYALIST_POOL})
          AND invoice_total > 0
    )
    GROUP BY im.product
    ORDER BY revenue DESC LIMIT 20
""")
print(f'\n[10] TOP PRODUCT LINES:')
for i, row in enumerate(r10.result_rows, 1):
    print(f'    {i:>2}. {str(row[0]):35} {int(row[1]):>8,} invoices  Rs.{int(row[2]):>12,.0f}')
print(f'    Time: {time.time()-t:.1f}s')

# 11. Quarterly activity heatmap
t = time.time()
r11 = client.query(f"""
    SELECT
        toString(toYear(date)) AS yr,
        toString(toQuarter(date)) AS qtr,
        countDistinct(customer_mobile) AS custs,
        countDistinct(invoice_no) AS invoices,
        round(sum(invoice_total), 0) AS revenue
    FROM azure_invoice_report
    WHERE customer_mobile IN ({LOYALIST_POOL})
      AND invoice_total > 0
      AND toDate(date) >= toDate('2023-01-01')
    GROUP BY yr, qtr
    ORDER BY yr, qtr
""")
QTR_NAMES = {'1':'JFM','2':'AMJ','3':'JAS','4':'OND'}
print(f'\n[11] QUARTERLY ACTIVITY HEATMAP:')
print(f'    {"Period":10} {"Custs":>8} {"Invoices":>10} {"Revenue":>14}')
for row in r11.result_rows:
    yr, qtr = row[0], row[1]
    lbl = f'{QTR_NAMES.get(qtr,"Q"+qtr)} {yr}'
    print(f'    {lbl:10} {int(row[2]):>8,} {int(row[3]):>10,}  Rs.{int(row[4]):>12,.0f}')
print(f'    Time: {time.time()-t:.1f}s')

# 12. Customer type
t = time.time()
r12 = client.query(f"""
    SELECT customer_type, countDistinct(customer_mobile) AS custs
    FROM azure_invoice_report
    WHERE customer_mobile IN ({LOYALIST_POOL})
      AND invoice_total > 0 AND customer_type != ''
    GROUP BY customer_type ORDER BY custs DESC
""")
print(f'\n[12] CUSTOMER TYPE:')
for row in r12.result_rows:
    pct = int(row[1])/total*100
    print(f'    {str(row[0]):30}: {int(row[1]):>7,}  ({pct:.1f}%)')
print(f'    Time: {time.time()-t:.1f}s')

# 13. Already active in AMJ 2026 (warm this season)?
t = time.time()
r13 = client.query(f"""
    SELECT countDistinct(customer_mobile)
    FROM azure_invoice_report
    WHERE customer_mobile IN ({LOYALIST_POOL})
      AND toDate(date) BETWEEN toDate('2026-04-01') AND toDate('2026-06-30')
      AND invoice_total > 0
""")
amj26 = r13.result_rows[0][0]
print(f'\n[13] RECENT ACTIVITY:')
print(f'    Active in AMJ 2026 (Apr-Jun): {amj26:,} of {total:,} ({amj26/total*100:.1f}%) - WARM leads')

r13b = client.query(f"""
    SELECT countDistinct(customer_mobile)
    FROM azure_invoice_report
    WHERE customer_mobile IN ({LOYALIST_POOL})
      AND toDate(date) BETWEEN toDate('2025-10-01') AND toDate('2026-06-30')
      AND invoice_total > 0
""")
post_jas25 = r13b.result_rows[0][0]
print(f'    Active after JAS 2025 (Oct-Jun): {post_jas25:,} ({post_jas25/total*100:.1f}%)')
print(f'    Completely inactive since JAS25: {total-post_jas25:,} ({(total-post_jas25)/total*100:.1f}%) - need incentive')
print(f'    Time: {time.time()-t:.1f}s')

print(f'\n{"="*65}')
print('ANALYSIS COMPLETE')
print(f'{"="*65}')
