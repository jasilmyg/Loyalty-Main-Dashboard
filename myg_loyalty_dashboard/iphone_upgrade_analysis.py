import os, sys
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

print('='*70)
print('IPHONE/SAMSUNG UPGRADE ANALYSIS — JAS LOYALISTS (69,728)')
print('='*70)

# 1. What phones did they buy in JAS 2024?
t = time.time()
r1 = client.query(f"""
    SELECT im.item_name,
           countDistinct(inv.customer_mobile) AS custs,
           round(sum(sr.sold_price), 0) AS revenue
    FROM azure_sales_report sr
    INNER JOIN item_master im ON sr.item_code = im.item_code
    INNER JOIN azure_invoice_report inv ON sr.invoice_no = inv.invoice_no
    WHERE inv.customer_mobile IN ({POOL})
      AND toDate(inv.date) BETWEEN toDate('{JAS24_S}') AND toDate('{JAS24_E}')
      AND (lower(im.item_name) LIKE '%iphone%'
           OR lower(im.item_name) LIKE '%samsung%'
           OR lower(im.item_name) LIKE '%galaxy%'
           OR lower(im.item_name) LIKE '%vivo%'
           OR lower(im.item_name) LIKE '%oppo%'
           OR lower(im.item_name) LIKE '%realme%'
           OR lower(im.item_name) LIKE '%oneplus%'
           OR lower(im.item_name) LIKE '%redmi%'
           OR lower(im.item_name) LIKE '%xiaomi%'
           OR im.category = 'TELECOM')
    GROUP BY im.item_name
    ORDER BY custs DESC
    LIMIT 30
""")
print(f'\n[A] PHONES BOUGHT IN JAS 2024 (their first qualifying season):')
print(f'  {"Product":60} {"Custs":>7} {"Revenue":>14}')
for row in r1.result_rows:
    print(f'  {str(row[0])[:60]:60} {int(row[1]):>7,}  Rs.{int(row[2]):>10,.0f}')
print(f'  Time: {time.time()-t:.1f}s')

# 2. What phones did they buy in JAS 2025?
t = time.time()
r2 = client.query(f"""
    SELECT im.item_name,
           countDistinct(inv.customer_mobile) AS custs,
           round(sum(sr.sold_price), 0) AS revenue
    FROM azure_sales_report sr
    INNER JOIN item_master im ON sr.item_code = im.item_code
    INNER JOIN azure_invoice_report inv ON sr.invoice_no = inv.invoice_no
    WHERE inv.customer_mobile IN ({POOL})
      AND toDate(inv.date) BETWEEN toDate('{JAS25_S}') AND toDate('{JAS25_E}')
      AND (lower(im.item_name) LIKE '%iphone%'
           OR lower(im.item_name) LIKE '%samsung%'
           OR lower(im.item_name) LIKE '%galaxy%'
           OR lower(im.item_name) LIKE '%vivo%'
           OR lower(im.item_name) LIKE '%oppo%'
           OR lower(im.item_name) LIKE '%realme%'
           OR lower(im.item_name) LIKE '%oneplus%'
           OR im.category = 'TELECOM')
    GROUP BY im.item_name
    ORDER BY custs DESC
    LIMIT 30
""")
print(f'\n[B] PHONES BOUGHT IN JAS 2025 (their second qualifying season):')
print(f'  {"Product":60} {"Custs":>7} {"Revenue":>14}')
for row in r2.result_rows:
    print(f'  {str(row[0])[:60]:60} {int(row[1]):>7,}  Rs.{int(row[2]):>10,.0f}')
print(f'  Time: {time.time()-t:.1f}s')

# 3. iPhone model breakdown — JAS 2024
t = time.time()
r3 = client.query(f"""
    SELECT
        multiIf(lower(im.item_name) LIKE '%iphone 16%', 'iPhone 16 series',
                lower(im.item_name) LIKE '%iphone 15%', 'iPhone 15 series',
                lower(im.item_name) LIKE '%iphone 14%', 'iPhone 14 series',
                lower(im.item_name) LIKE '%iphone 13%', 'iPhone 13 series',
                lower(im.item_name) LIKE '%iphone 12%', 'iPhone 12 series',
                lower(im.item_name) LIKE '%iphone 11%', 'iPhone 11 series',
                lower(im.item_name) LIKE '%iphone 17%', 'iPhone 17 series',
                lower(im.item_name) LIKE '%iphone%', 'Other iPhone',
                'Non-iPhone Phone') AS model_group,
        countDistinct(inv.customer_mobile) AS custs
    FROM azure_sales_report sr
    INNER JOIN item_master im ON sr.item_code = im.item_code
    INNER JOIN azure_invoice_report inv ON sr.invoice_no = inv.invoice_no
    WHERE inv.customer_mobile IN ({POOL})
      AND toDate(inv.date) BETWEEN toDate('{JAS24_S}') AND toDate('{JAS24_E}')
      AND im.category = 'TELECOM'
    GROUP BY model_group ORDER BY custs DESC
""")
print(f'\n[C] iPHONE MODEL GROUPS — BOUGHT IN JAS 2024:')
for row in r3.result_rows:
    pct = int(row[1])/69728*100
    bar = '#'*int(pct*2)
    print(f'  {str(row[0]):25}: {int(row[1]):>7,}  ({pct:.1f}%)  {bar}')
print(f'  Time: {time.time()-t:.1f}s')

# 4. iPhone model breakdown — JAS 2025
t = time.time()
r4 = client.query(f"""
    SELECT
        multiIf(lower(im.item_name) LIKE '%iphone 16%', 'iPhone 16 series',
                lower(im.item_name) LIKE '%iphone 15%', 'iPhone 15 series',
                lower(im.item_name) LIKE '%iphone 14%', 'iPhone 14 series',
                lower(im.item_name) LIKE '%iphone 13%', 'iPhone 13 series',
                lower(im.item_name) LIKE '%iphone 12%', 'iPhone 12 series',
                lower(im.item_name) LIKE '%iphone 11%', 'iPhone 11 series',
                lower(im.item_name) LIKE '%iphone 17%', 'iPhone 17 series',
                lower(im.item_name) LIKE '%iphone%', 'Other iPhone',
                'Non-iPhone Phone') AS model_group,
        countDistinct(inv.customer_mobile) AS custs
    FROM azure_sales_report sr
    INNER JOIN item_master im ON sr.item_code = im.item_code
    INNER JOIN azure_invoice_report inv ON sr.invoice_no = inv.invoice_no
    WHERE inv.customer_mobile IN ({POOL})
      AND toDate(inv.date) BETWEEN toDate('{JAS25_S}') AND toDate('{JAS25_E}')
      AND im.category = 'TELECOM'
    GROUP BY model_group ORDER BY custs DESC
""")
print(f'\n[D] iPHONE MODEL GROUPS — BOUGHT IN JAS 2025:')
for row in r4.result_rows:
    pct = int(row[1])/69728*100
    bar = '#'*int(pct*2)
    print(f'  {str(row[0]):25}: {int(row[1]):>7,}  ({pct:.1f}%)  {bar}')
print(f'  Time: {time.time()-t:.1f}s')

# 5. MOST RECENT phone purchase per customer — what do they currently own?
t = time.time()
r5 = client.query(f"""
    SELECT
        multiIf(lower(last_phone) LIKE '%iphone 17%', 'iPhone 17 - Already has (NO upgrade needed)',
                lower(last_phone) LIKE '%iphone 16%', 'iPhone 16 - Could upgrade to 17',
                lower(last_phone) LIKE '%iphone 15%', 'iPhone 15 - Strong upgrade candidate',
                lower(last_phone) LIKE '%iphone 14%', 'iPhone 14 - Prime upgrade candidate',
                lower(last_phone) LIKE '%iphone 13%', 'iPhone 13 - Overdue upgrade (3 yrs)',
                lower(last_phone) LIKE '%iphone 12%', 'iPhone 12 - Very overdue (4 yrs)',
                lower(last_phone) LIKE '%iphone 11%', 'iPhone 11 - Critical upgrade (5+ yrs)',
                lower(last_phone) LIKE '%iphone%', 'Other iPhone',
                lower(last_phone) LIKE '%galaxy%', 'Samsung Galaxy - potential switcher',
                lower(last_phone) LIKE '%samsung%', 'Samsung (other)',
                lower(last_phone) LIKE '%vivo%', 'VIVO user',
                lower(last_phone) LIKE '%oppo%', 'OPPO user',
                lower(last_phone) LIKE '%realme%', 'REALME user',
                'Other/Unknown') AS upgrade_status,
        count() AS custs
    FROM (
        SELECT inv.customer_mobile,
               argMax(im.item_name, inv.date) AS last_phone
        FROM azure_sales_report sr
        INNER JOIN item_master im ON sr.item_code = im.item_code
        INNER JOIN azure_invoice_report inv ON sr.invoice_no = inv.invoice_no
        WHERE inv.customer_mobile IN ({POOL})
          AND im.category = 'TELECOM'
          AND inv.invoice_total > 0
        GROUP BY inv.customer_mobile
    ) t
    GROUP BY upgrade_status ORDER BY custs DESC
""")
print(f'\n[E] CURRENT PHONE STATUS (most recent phone purchased):')
print(f'  {"Upgrade Status":50} {"Custs":>8} {"% of loyalists"}')
for row in r5.result_rows:
    pct = int(row[1])/69728*100
    print(f'  {str(row[0]):50} {int(row[1]):>8,}  ({pct:.1f}%)')
print(f'  Time: {time.time()-t:.1f}s')

# 6. Among those who bought in JAS 2025 specifically — what did they buy most?
t = time.time()
r6 = client.query(f"""
    SELECT inv.branch,
           countDistinct(inv.customer_mobile) AS phone_buyers
    FROM azure_sales_report sr
    INNER JOIN item_master im ON sr.item_code = im.item_code
    INNER JOIN azure_invoice_report inv ON sr.invoice_no = inv.invoice_no
    WHERE inv.customer_mobile IN ({POOL})
      AND toDate(inv.date) BETWEEN toDate('{JAS25_S}') AND toDate('{JAS25_E}')
      AND im.category = 'TELECOM'
    GROUP BY inv.branch ORDER BY phone_buyers DESC LIMIT 15
""")
print(f'\n[F] BRANCHES WHERE THEY BOUGHT PHONES IN JAS 2025:')
for row in r6.result_rows:
    print(f'  {str(row[0]):12} {int(row[1]):>6,} customers bought phones')
print(f'  Time: {time.time()-t:.1f}s')

# 7. What % of them bought a phone at all vs accessories only
t = time.time()
r7 = client.query(f"""
    SELECT countDistinct(inv.customer_mobile) AS phone_buyers
    FROM azure_sales_report sr
    INNER JOIN item_master im ON sr.item_code = im.item_code
    INNER JOIN azure_invoice_report inv ON sr.invoice_no = inv.invoice_no
    WHERE inv.customer_mobile IN ({POOL})
      AND toDate(inv.date) BETWEEN toDate('{JAS25_S}') AND toDate('{JAS25_E}')
      AND im.category = 'TELECOM'
""")
phone_buyers = int(r7.result_rows[0][0])
non_phone = 69728 - phone_buyers
print(f'\n[G] JAS 2025 PURCHASE BREAKDOWN:')
print(f'  Bought a phone in JAS 2025        : {phone_buyers:>8,} ({phone_buyers/69728*100:.1f}%) - PHONE BUYERS')
print(f'  Did NOT buy phone in JAS 2025     : {non_phone:>8,} ({non_phone/69728*100:.1f}%) - accessories/other only')

print(f'\n{"="*70}')
print('SUMMARY')
print(f'{"="*70}')
print(f'Total JAS Loyalists analysed: 69,728')
print(f'Phone buyers in JAS 2025: {phone_buyers:,}')
