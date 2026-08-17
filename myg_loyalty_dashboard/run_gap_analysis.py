import os, sys, time, json
sys.stdout.reconfigure(encoding='utf-8')
sys.path.insert(0, '.')
os.environ['DJANGO_SETTINGS_MODULE'] = 'myg_loyalty_dashboard.settings'
import django; django.setup()
from analytics.clickhouse_service import get_ch_client
client = get_ch_client()

TODAY = '2026-08-14'
JAS26_START = '2026-07-01'
JAS25_S, JAS25_E = '2025-07-01', '2025-09-30'
JAS24_S, JAS24_E = '2024-07-01', '2024-09-30'
AMJ26_S, AMJ26_E = '2026-04-01', '2026-06-30'

EXCL = f"""
    customer_mobile NOT IN (
        SELECT DISTINCT customer_mobile FROM azure_invoice_report
        WHERE toDate(date) >= toDate('{JAS26_START}')
          AND toDate(date) <= toDate('{TODAY}') AND invoice_total > 0
    )
"""

# Spend profile
r = client.query(f'''
    SELECT
        countDistinct(customer_mobile),
        round(avg(invoice_total),2),
        round(sum(invoice_total)/1e7,3),
        round(quantile(0.5)(invoice_total),2),
        round(quantile(0.75)(invoice_total),2)
    FROM azure_invoice_report
    WHERE toDate(date) BETWEEN toDate('{JAS25_S}') AND toDate('{JAS25_E}')
      AND invoice_total > 0 AND length(customer_mobile) = 10
      AND customer_mobile NOT IN ('1313131313','0000000000','9999999999')
      AND {EXCL}
''')
row = r.result_rows[0]
print('SPEND PROFILE of JAS 2025 buyers not yet in JAS 2026:')
print(f'  Unique customers : {row[0]:,}')
print(f'  Avg spend (JAS25): Rs.{row[1]:,}')
print(f'  Total revenue    : Rs.{row[2]:.1f} Cr')
print(f'  Median spend     : Rs.{row[3]:,}')
print(f'  P75 spend        : Rs.{row[4]:,}')

# Top 20 hottest customers
r2 = client.query(f'''
    SELECT
        customer_mobile,
        branch,
        max(invoice_total) AS last_spend,
        count() AS total_jas25_purchases,
        toString(max(toDate(date))) AS last_purchase
    FROM azure_invoice_report
    WHERE toDate(date) BETWEEN toDate('{JAS25_S}') AND toDate('{JAS25_E}')
      AND invoice_total > 0 AND length(customer_mobile) = 10
      AND customer_mobile NOT IN ('1313131313','0000000000','9999999999')
      AND {EXCL}
      AND customer_mobile IN (
          SELECT DISTINCT customer_mobile FROM azure_invoice_report
          WHERE toDate(date) BETWEEN toDate('{AMJ26_S}') AND toDate('{AMJ26_E}')
            AND invoice_total > 0
      )
    GROUP BY customer_mobile, branch
    ORDER BY last_spend DESC
    LIMIT 20
''')
print()
print('TOP 20 HOTTEST TARGETS (JAS 2025 buyer + AMJ 2026 active + NOT in JAS 2026):')
print(f'  {"#":>2}  {"Mobile":12}  {"Branch":20}  {"JAS25 Spend":>12}  {"Purchases":>9}  Last Purchase')
for i, row in enumerate(r2.result_rows, 1):
    mob = row[0]
    masked = mob[:3] + 'XXXXX' + mob[-3:]
    print(f'  {i:2}. {masked:12}  {row[1]:20}  Rs.{row[2]:>10,.0f}  {row[3]:>9}  {row[4]}')

# Financier breakdown
r3 = client.query(f'''
    SELECT financier_name, count() AS cnt
    FROM azure_invoice_report
    WHERE toDate(date) BETWEEN toDate('{JAS25_S}') AND toDate('{JAS25_E}')
      AND invoice_total > 0 AND length(customer_mobile) = 10
      AND customer_mobile NOT IN ('1313131313','0000000000','9999999999')
      AND {EXCL}
    GROUP BY financier_name ORDER BY cnt DESC LIMIT 10
''')
print()
print('PURCHASE TYPE of missing customers:')
for row in r3.result_rows:
    ptype = row[0] if row[0] else 'CASH/DIRECT'
    print(f'  {ptype}: {row[1]:,}')

# Branch breakdown (already done, include it)
r4 = client.query(f'''
    SELECT branch, countDistinct(customer_mobile) AS cnt, round(avg(invoice_total),0) AS avg_spend
    FROM azure_invoice_report
    WHERE toDate(date) BETWEEN toDate('{JAS25_S}') AND toDate('{JAS25_E}')
      AND invoice_total > 0 AND length(customer_mobile) = 10
      AND customer_mobile NOT IN ('1313131313','0000000000','9999999999')
      AND {EXCL}
    GROUP BY branch ORDER BY cnt DESC LIMIT 15
''')
print()
print('BRANCH BREAKDOWN - JAS 2025 buyers not in JAS 2026:')
print(f'  {"Branch":25}  {"Missing Customers":>17}  {"Avg JAS25 Spend":>16}')
for row in r4.result_rows:
    print(f'  {row[0]:25}  {row[1]:>17,}  Rs.{row[2]:>14,.0f}')

# Save full results to JSON for the dashboard
all_results = {
    'generated_at': TODAY,
    'target': 529364,
    'achieved': 186870,
    'gap': 342494,
    'days_left': 47,
    'jas25_not_jas26': 685104,
    'jas24_not_jas26': 545919,
    'jas_loyalists': 69728,
    'amj26_not_jas26': 529426,
    'hottest_leads': 65910,
    'spend_profile': {
        'avg_spend': float(r.result_rows[0][1]) if hasattr(r, 'result_rows') else 0
    },
    'branch_breakdown': [
        {'branch': row[0], 'missing_customers': row[1], 'avg_spend': float(row[2])}
        for row in r4.result_rows
    ],
    'top_targets': [
        {
            'mobile': row[0][:3] + 'XXXXX' + row[0][-3:],
            'mobile_full': row[0],  # full for CSV export
            'branch': row[1],
            'last_spend': float(row[2]),
            'purchases': row[3],
            'last_purchase': str(row[4]),
            'segment': 'HOTTEST',
            'priority': i
        }
        for i, row in enumerate(r2.result_rows, 1)
    ],
    'financier_breakdown': [
        {'type': row[0] or 'CASH', 'count': row[1]}
        for row in r3.result_rows
    ]
}
with open('analytics/jas26_gap_analysis.json', 'w', encoding='utf-8') as f:
    json.dump(all_results, f, indent=2, ensure_ascii=False)
print()
print('Saved to analytics/jas26_gap_analysis.json')
