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

POOL = f"""
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
total = 69728
print('='*70)
print('JAS LOYALISTS — DEEP ANALYSIS (Part 2)')
print('='*70)

# 3. Frequency distribution — fixed: use outer alias
t = time.time()
r3 = client.query(f"""
    SELECT
        f_bucket,
        count() AS cust_count
    FROM (
        SELECT customer_mobile,
               countDistinct(invoice_no) AS f_bucket
        FROM azure_invoice_report
        WHERE customer_mobile IN ({POOL})
          AND invoice_total > 0
        GROUP BY customer_mobile
    ) t
    GROUP BY f_bucket
    ORDER BY f_bucket
""")
print(f'\n[3] PURCHASE FREQUENCY DISTRIBUTION ({time.time()-t:.1f}s):')
buckets = [('1 purchase',0),('2-3',0),('4-5',0),('6-10',0),('11-20',0),('21-50',0),('51+',0)]
totals = {'1 purchase':0,'2-3':0,'4-5':0,'6-10':0,'11-20':0,'21-50':0,'51+':0}
for row in r3.result_rows:
    f, c = int(row[0]), int(row[1])
    if f==1: totals['1 purchase'] += c
    elif f<=3: totals['2-3'] += c
    elif f<=5: totals['4-5'] += c
    elif f<=10: totals['6-10'] += c
    elif f<=20: totals['11-20'] += c
    elif f<=50: totals['21-50'] += c
    else: totals['51+'] += c
mx = max(totals.values())
for k, v in totals.items():
    bar = '#'*int(v/mx*35) if mx else ''
    pct = v/total*100
    print(f'  {k:15}: {v:>7,}  ({pct:.1f}%)  {bar}')

# 4. Recency distribution
t = time.time()
r4 = client.query(f"""
    SELECT
        multiIf(rec <= 30,  'Within 30 days (HOT)',
                rec <= 90,  '31-90 days',
                rec <= 180, '91-180 days',
                rec <= 365, '181-365 days',
                'Over 1 year (COLD)') AS bucket,
        count() AS cnt
    FROM (
        SELECT customer_mobile,
               dateDiff('day', toDate(max(date)), toDate('{TODAY}')) AS rec
        FROM azure_invoice_report
        WHERE customer_mobile IN ({POOL})
          AND invoice_total > 0
        GROUP BY customer_mobile
    ) t
    GROUP BY bucket ORDER BY cnt DESC
""")
print(f'\n[4] RECENCY (last purchase) ({time.time()-t:.1f}s):')
for row in r4.result_rows:
    pct = int(row[1])/total*100
    print(f'  {str(row[0]):30}: {int(row[1]):>7,}  ({pct:.1f}%)')

# 5. Top categories
t = time.time()
r5 = client.query(f"""
    SELECT
        im.category,
        countDistinct(sr.invoice_no) AS invoices,
        round(sum(sr.sold_price), 0) AS revenue,
        round(sum(sr.qty), 0) AS qty
    FROM azure_sales_report sr
    INNER JOIN item_master im ON sr.item_code = im.item_code
    WHERE sr.invoice_no IN (
        SELECT DISTINCT invoice_no FROM azure_invoice_report
        WHERE customer_mobile IN ({POOL}) AND invoice_total > 0
    )
    GROUP BY im.category ORDER BY revenue DESC LIMIT 15
""")
print(f'\n[5] TOP CATEGORIES PURCHASED ({time.time()-t:.1f}s):')
print(f'  {"Category":35} {"Invoices":>10} {"Revenue":>16} {"Units":>8}')
for row in r5.result_rows:
    print(f'  {str(row[0]):35} {int(row[1]):>10,}  Rs.{int(row[2]):>12,.0f} {int(row[3]):>8,}')

# 6. Top brands
t = time.time()
r6 = client.query(f"""
    SELECT im.brand,
           countDistinct(sr.invoice_no) AS inv,
           round(sum(sr.sold_price),0) AS rev
    FROM azure_sales_report sr
    INNER JOIN item_master im ON sr.item_code = im.item_code
    WHERE sr.invoice_no IN (
        SELECT DISTINCT invoice_no FROM azure_invoice_report
        WHERE customer_mobile IN ({POOL}) AND invoice_total > 0
    )
    GROUP BY im.brand ORDER BY rev DESC LIMIT 15
""")
print(f'\n[6] TOP BRANDS ({time.time()-t:.1f}s):')
for i, row in enumerate(r6.result_rows, 1):
    print(f'  {i:>2}. {str(row[0]):35} {int(row[1]):>8,} invoices  Rs.{int(row[2]):>12,.0f}')

# 7. Branches
t = time.time()
r7 = client.query(f"""
    SELECT branch, countDistinct(customer_mobile) AS custs,
           round(sum(invoice_total),0) AS rev
    FROM azure_invoice_report
    WHERE customer_mobile IN ({POOL}) AND invoice_total > 0
    GROUP BY branch ORDER BY custs DESC LIMIT 20
""")
print(f'\n[7] TOP BRANCHES ({time.time()-t:.1f}s):')
for row in r7.result_rows:
    pct = int(row[1])/total*100
    print(f'  {str(row[0]):12} {int(row[1]):>7,} custs ({pct:.1f}%)  Rs.{int(row[2]):>12,.0f}')

# 8. Financier / payment
t = time.time()
r8 = client.query(f"""
    SELECT financier_name, countDistinct(customer_mobile) AS custs,
           round(sum(invoice_total),0) AS rev
    FROM azure_invoice_report
    WHERE customer_mobile IN ({POOL}) AND invoice_total > 0 AND financier_name != ''
    GROUP BY financier_name ORDER BY custs DESC LIMIT 15
""")
print(f'\n[8] PAYMENT / FINANCIER ({time.time()-t:.1f}s):')
for row in r8.result_rows:
    pct = int(row[1])/total*100
    print(f'  {str(row[0]):30} {int(row[1]):>7,} ({pct:.1f}%)  Rs.{int(row[2]):>12,.0f}')

# 9. Spend tiers
t = time.time()
r9 = client.query(f"""
    SELECT multiIf(ls < 5000,'< Rs.5K',
                   ls < 20000,'Rs.5K-20K',
                   ls < 50000,'Rs.20K-50K',
                   ls < 100000,'Rs.50K-1L',
                   ls < 500000,'Rs.1L-5L',
                   'Rs.5L+') AS tier,
           count() AS custs,
           round(avg(ls),0) AS avg_ls,
           round(sum(ls),0) AS total_ls
    FROM (
        SELECT customer_mobile, sum(invoice_total) AS ls
        FROM azure_invoice_report
        WHERE customer_mobile IN ({POOL}) AND invoice_total > 0
        GROUP BY customer_mobile
    ) t
    GROUP BY tier ORDER BY avg_ls DESC
""")
print(f'\n[9] SPEND TIERS ({time.time()-t:.1f}s):')
for row in r9.result_rows:
    pct = int(row[1])/total*100
    print(f'  {str(row[0]):20}: {int(row[1]):>7,} ({pct:.1f}%)  avg Rs.{int(row[2]):>10,.0f}  total Rs.{int(row[3]):>12,.0f}')

# 10. Quarterly heatmap
t = time.time()
r10 = client.query(f"""
    SELECT toYear(date) AS yr, toQuarter(date) AS qtr,
           countDistinct(customer_mobile) AS custs,
           countDistinct(invoice_no) AS inv,
           round(sum(invoice_total),0) AS rev
    FROM azure_invoice_report
    WHERE customer_mobile IN ({POOL}) AND invoice_total > 0
      AND toDate(date) >= toDate('2023-07-01')
    GROUP BY yr, qtr ORDER BY yr, qtr
""")
QN = {1:'JFM',2:'AMJ',3:'JAS',4:'OND'}
print(f'\n[10] QUARTERLY ACTIVITY HEATMAP ({time.time()-t:.1f}s):')
print(f'  {"Period":10} {"Custs":>8} {"Invoices":>10} {"Revenue":>16}')
for row in r10.result_rows:
    lbl = f'{QN.get(int(row[1]),"Q"+str(row[1]))} {int(row[0])}'
    active_pct = int(row[2])/total*100
    print(f'  {lbl:10} {int(row[2]):>8,} ({active_pct:.0f}%)  {int(row[3]):>10,}  Rs.{int(row[4]):>12,.0f}')

# 11. AMJ 2026 activity
t = time.time()
r11 = client.query(f"""
    SELECT countDistinct(customer_mobile) FROM azure_invoice_report
    WHERE customer_mobile IN ({POOL})
      AND toDate(date) BETWEEN toDate('2026-04-01') AND toDate('2026-06-30')
      AND invoice_total > 0
""")
amj26 = int(r11.result_rows[0][0])
r11b = client.query(f"""
    SELECT countDistinct(customer_mobile) FROM azure_invoice_report
    WHERE customer_mobile IN ({POOL})
      AND toDate(date) BETWEEN toDate('2025-10-01') AND toDate('2026-06-30')
      AND invoice_total > 0
""")
post_jas = int(r11b.result_rows[0][0])
print(f'\n[11] RECENT ACTIVITY STATUS ({time.time()-t:.1f}s):')
print(f'  Active in AMJ 2026  (Apr-Jun 26): {amj26:>8,}  ({amj26/total*100:.1f}%)  WARM - still buying')
print(f'  Active post-JAS25  (Oct-Jun 26): {post_jas:>8,}  ({post_jas/total*100:.1f}%)  ENGAGED')
print(f'  COLD (no purchase since JAS 25):  {total-post_jas:>8,}  ({(total-post_jas)/total*100:.1f}%)  NEED INCENTIVE')

# 12. Top product names (item level)
t = time.time()
r12 = client.query(f"""
    SELECT im.item_name, round(sum(sr.qty),0) AS qty,
           round(sum(sr.sold_price),0) AS rev
    FROM azure_sales_report sr
    INNER JOIN item_master im ON sr.item_code = im.item_code
    WHERE sr.invoice_no IN (
        SELECT DISTINCT invoice_no FROM azure_invoice_report
        WHERE customer_mobile IN ({POOL}) AND invoice_total > 0
    )
    GROUP BY im.item_name ORDER BY rev DESC LIMIT 15
""")
print(f'\n[12] TOP INDIVIDUAL PRODUCTS PURCHASED ({time.time()-t:.1f}s):')
for i, row in enumerate(r12.result_rows, 1):
    print(f'  {i:>2}. {str(row[0])[:60]:60} {int(row[1]):>6,} units  Rs.{int(row[2]):>12,.0f}')

print(f'\n{"="*70}\nANALYSIS COMPLETE\n{"="*70}')
