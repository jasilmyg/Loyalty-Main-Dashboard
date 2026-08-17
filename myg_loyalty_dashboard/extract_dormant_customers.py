import os, sys, csv, time
sys.stdout.reconfigure(encoding='utf-8')
sys.path.insert(0, '.')
os.environ['DJANGO_SETTINGS_MODULE'] = 'myg_loyalty_dashboard.settings'
import django; django.setup()
from analytics.clickhouse_service import get_ch_client
client = get_ch_client()

TODAY        = '2026-08-14'
JAS25_S      = '2025-07-01'
JAS25_E      = '2025-09-30'
JAS26_S      = '2026-07-01'
DORMANT_FROM = '2025-10-01'   # zero purchases from Oct 2025 onward

print('='*70)
print('STEP 1 — Extracting 5,14,313 DORMANT customers')
print('='*70)

# ── POOL: Bought in JAS 2025, NOT in JAS 2026, NOT after Sep 2025 ─────────
DORMANT_POOL = f"""
    SELECT DISTINCT customer_mobile FROM azure_invoice_report
    WHERE toDate(date) BETWEEN toDate('{JAS25_S}') AND toDate('{JAS25_E}')
      AND invoice_total > 0
      AND length(customer_mobile) = 10
      AND customer_mobile NOT IN ('1313131313','0000000000','9999999999')
      AND customer_mobile NOT IN (
          SELECT DISTINCT customer_mobile FROM azure_invoice_report
          WHERE toDate(date) >= toDate('{DORMANT_FROM}')
            AND toDate(date) <= toDate('{TODAY}')
            AND invoice_total > 0
      )
"""

t = time.time()
rc = client.query(f'SELECT count() FROM ({DORMANT_POOL})')
total = int(rc.result_rows[0][0])
print(f'  Confirmed dormant pool: {total:,}  ({time.time()-t:.1f}s)')

# ── STEP 2: Full behavioral profile per customer ───────────────────────────
print('\nSTEP 2 — Building full behavioral profile per customer...')
t = time.time()

r = client.query(f"""
    SELECT
        inv.customer_mobile,

        /* ── JAS 2025 purchase (qualifying event) ── */
        countDistinct(CASE WHEN toDate(inv.date) BETWEEN toDate('{JAS25_S}') AND toDate('{JAS25_E}')
                           THEN inv.invoice_no END)                           AS jas25_invoices,
        round(sumIf(inv.invoice_total,
                    toDate(inv.date) BETWEEN toDate('{JAS25_S}') AND toDate('{JAS25_E}')), 0)
                                                                              AS jas25_spend,
        toString(toDate(maxIf(inv.date,
                    toDate(inv.date) BETWEEN toDate('{JAS25_S}') AND toDate('{JAS25_E}'))))
                                                                              AS jas25_last_date,
        argMaxIf(inv.branch, inv.date,
                    toDate(inv.date) BETWEEN toDate('{JAS25_S}') AND toDate('{JAS25_E}'))
                                                                              AS jas25_branch,
        argMaxIf(inv.financier_name, inv.date,
                    toDate(inv.date) BETWEEN toDate('{JAS25_S}') AND toDate('{JAS25_E}'))
                                                                              AS jas25_financier,

        /* ── Lifetime stats ── */
        countDistinct(inv.invoice_no)                                         AS lifetime_invoices,
        round(sum(inv.invoice_total), 0)                                      AS lifetime_spend,
        round(avg(inv.invoice_total), 0)                                      AS avg_basket,
        toString(toDate(min(inv.date)))                                        AS first_purchase_date,
        toString(toDate(max(inv.date)))                                        AS last_purchase_date,
        dateDiff('day', toDate(max(inv.date)), toDate('{TODAY}'))             AS dormant_days,
        dateDiff('day', toDate(min(inv.date)), toDate('{TODAY}'))             AS tenure_days,
        countDistinct(toYYYYMM(inv.date))                                     AS active_months,
        argMax(inv.branch, inv.date)                                           AS preferred_branch,
        argMax(inv.financier_name, inv.date)                                   AS preferred_financier,
        argMax(inv.customer_type, inv.date)                                    AS customer_type,

        /* ── Seasonal activity flags ── */
        countDistinctIf(inv.invoice_no,
            toDate(inv.date) BETWEEN toDate('2024-07-01') AND toDate('2024-09-30'))
                                                                              AS jas24_invoices,
        round(sumIf(inv.invoice_total,
            toDate(inv.date) BETWEEN toDate('2024-07-01') AND toDate('2024-09-30')), 0)
                                                                              AS jas24_spend,
        countDistinctIf(inv.invoice_no,
            toDate(inv.date) BETWEEN toDate('2024-10-01') AND toDate('2024-12-31'))
                                                                              AS ond24_invoices,
        countDistinctIf(inv.invoice_no,
            toDate(inv.date) BETWEEN toDate('2025-01-01') AND toDate('2025-03-31'))
                                                                              AS jfm25_invoices,
        countDistinctIf(inv.invoice_no,
            toDate(inv.date) BETWEEN toDate('2025-04-01') AND toDate('2025-06-30'))
                                                                              AS amj25_invoices,

        /* ── Reactivation priority score (0-100) ──
              Higher = easier to reactivate
              Based on: recency, spend, frequency, seasonal loyalty
        */
        least(100, toInt32(
            /* recent before JAS25: still "warm" mentally */
            (CASE WHEN countDistinctIf(inv.invoice_no,
                         toDate(inv.date) BETWEEN toDate('2025-04-01') AND toDate('2025-06-30')) > 0
                  THEN 25 ELSE 0 END) +
            /* JAS 2024 buyer too = true loyalist */
            (CASE WHEN countDistinctIf(inv.invoice_no,
                         toDate(inv.date) BETWEEN toDate('2024-07-01') AND toDate('2024-09-30')) > 0
                  THEN 20 ELSE 0 END) +
            /* High lifetime spend */
            (CASE WHEN sum(inv.invoice_total) >= 100000 THEN 20
                  WHEN sum(inv.invoice_total) >= 50000  THEN 15
                  WHEN sum(inv.invoice_total) >= 20000  THEN 10
                  WHEN sum(inv.invoice_total) >= 5000   THEN 5
                  ELSE 0 END) +
            /* High frequency */
            (CASE WHEN countDistinct(inv.invoice_no) >= 10 THEN 20
                  WHEN countDistinct(inv.invoice_no) >= 5  THEN 15
                  WHEN countDistinct(inv.invoice_no) >= 3  THEN 10
                  WHEN countDistinct(inv.invoice_no) >= 2  THEN 5
                  ELSE 0 END) +
            /* Dormancy not too long */
            (CASE WHEN dateDiff('day', toDate(max(inv.date)), toDate('{TODAY}')) <= 300 THEN 15
                  WHEN dateDiff('day', toDate(max(inv.date)), toDate('{TODAY}')) <= 330 THEN 10
                  ELSE 0 END)
        ))                                                                    AS reactivation_score

    FROM azure_invoice_report inv
    WHERE inv.customer_mobile IN ({DORMANT_POOL})
      AND inv.invoice_total > 0
    GROUP BY inv.customer_mobile
    ORDER BY reactivation_score DESC, jas25_spend DESC
""")

rows = r.result_rows
print(f'  Fetched {len(rows):,} customer profiles  ({time.time()-t:.1f}s)')

# ── STEP 3: Get last product purchased per customer ────────────────────────
print('\nSTEP 3 — Fetching last product bought by each customer...')
t = time.time()
r_prod = client.query(f"""
    SELECT inv.customer_mobile,
           argMax(im.item_name, inv.date)  AS last_product,
           argMax(im.category, inv.date)   AS last_category,
           argMax(im.brand, inv.date)      AS last_brand
    FROM azure_sales_report sr
    INNER JOIN item_master im ON sr.item_code = im.item_code
    INNER JOIN azure_invoice_report inv ON sr.invoice_no = inv.invoice_no
    WHERE inv.customer_mobile IN ({DORMANT_POOL})
      AND inv.invoice_total > 0
    GROUP BY inv.customer_mobile
""")
product_map = {row[0]: (row[1], row[2], row[3]) for row in r_prod.result_rows}
print(f'  Product data fetched for {len(product_map):,} customers  ({time.time()-t:.1f}s)')

# ── STEP 4: Score label + reactivation strategy ───────────────────────────
def score_label(s):
    if s >= 70: return 'PRIORITY-1 (Call Today)'
    if s >= 50: return 'PRIORITY-2 (Call This Week)'
    if s >= 30: return 'PRIORITY-3 (SMS + WhatsApp)'
    return 'PRIORITY-4 (Festival Offer Needed)'

def spend_tier(v):
    if v >= 500000: return 'VIP Rs.5L+'
    if v >= 100000: return 'Premium Rs.1L-5L'
    if v >= 50000:  return 'High Rs.50K-1L'
    if v >= 20000:  return 'Mid Rs.20K-50K'
    if v >= 5000:   return 'Low Rs.5K-20K'
    return 'Entry <Rs.5K'

def reactivation_message(row_dict):
    s = row_dict['reactivation_score']
    fin = row_dict['preferred_financier']
    brand = row_dict['last_brand']
    cat = row_dict['last_category']
    if s >= 70:
        return 'Personal call from Branch Manager — high lifetime value'
    if 'BAJAJ' in str(fin).upper():
        return 'No-Cost EMI offer via Bajaj Finance — proven payment preference'
    if str(cat).upper() == 'TELECOM':
        return 'Phone upgrade offer — JAS season is their buying window'
    if s >= 30:
        return 'WhatsApp message: JAS special offer + loyalty points'
    return 'Festive season SMS + discount coupon to break dormancy'

# ── STEP 5: Save CSV ────────────────────────────────────────────────────────
print('\nSTEP 4 — Writing CSV file...')
t = time.time()

HEADERS = [
    # Identity
    'customer_mobile',
    # Reactivation intelligence
    'reactivation_score', 'priority_level', 'reactivation_message',
    # JAS 2025 details (why they qualify)
    'jas25_invoices', 'jas25_spend', 'jas25_last_date',
    'jas25_branch', 'jas25_financier',
    # Dormancy
    'dormant_days', 'last_purchase_date',
    # Lifetime behaviour
    'lifetime_invoices', 'lifetime_spend', 'avg_basket_size',
    'first_purchase_date', 'tenure_days', 'active_months', 'spend_tier',
    # Seasonal history
    'jas24_invoices', 'jas24_spend',
    'ond24_invoices', 'jfm25_invoices', 'amj25_invoices',
    'is_jas_loyalist',  # bought BOTH JAS 2024 + JAS 2025
    # Preferences
    'preferred_branch', 'preferred_financier', 'customer_type',
    # Last product
    'last_product_purchased', 'last_product_category', 'last_brand',
]

out_path = 'analytics/dormant_customers_514313.csv'

counts = {'P1':0,'P2':0,'P3':0,'P4':0}
with open(out_path, 'w', newline='', encoding='utf-8-sig') as f:
    writer = csv.writer(f)
    writer.writerow(HEADERS)
    for row in rows:
        (mobile, jas25_inv, jas25_spend, jas25_date, jas25_branch, jas25_fin,
         lt_inv, lt_spend, avg_basket, first_dt, last_dt, dorm_days, tenure,
         active_mo, pref_branch, pref_fin, ctype,
         jas24_inv, jas24_spend, ond24_inv, jfm25_inv, amj25_inv,
         score) = row

        prod, cat, brand = product_map.get(mobile, ('', '', ''))
        is_loyalist = 'YES' if jas24_inv > 0 else 'NO'
        priority = score_label(score)
        message  = reactivation_message({
            'reactivation_score': score,
            'preferred_financier': pref_fin,
            'last_brand': brand,
            'last_category': cat,
        })
        tier = spend_tier(lt_spend)

        # tally
        if score >= 70:   counts['P1'] += 1
        elif score >= 50: counts['P2'] += 1
        elif score >= 30: counts['P3'] += 1
        else:             counts['P4'] += 1

        writer.writerow([
            mobile,
            score, priority, message,
            jas25_inv, int(jas25_spend), jas25_date, jas25_branch, jas25_fin,
            dorm_days, last_dt,
            lt_inv, int(lt_spend), int(avg_basket),
            first_dt, tenure, active_mo, tier,
            jas24_inv, int(jas24_spend),
            ond24_inv, jfm25_inv, amj25_inv,
            is_loyalist,
            pref_branch, pref_fin, ctype,
            prod[:80] if prod else '', cat, brand,
        ])

print(f'  Saved {len(rows):,} rows to {out_path}  ({time.time()-t:.1f}s)')
print(f'\n  PRIORITY BREAKDOWN:')
print(f'  P1 (Call Today):       {counts["P1"]:>8,}')
print(f'  P2 (Call This Week):   {counts["P2"]:>8,}')
print(f'  P3 (SMS/WhatsApp):     {counts["P3"]:>8,}')
print(f'  P4 (Incentive Needed): {counts["P4"]:>8,}')
print(f'  TOTAL:                 {sum(counts.values()):>8,}')

# ── STEP 5: Quick stats for validation ────────────────────────────────────
print('\nSTEP 5 — Quick validation stats:')
import statistics
scores = [row[-1] for row in rows]
spends = [int(row[6]) for row in rows]
dorms  = [row[11] for row in rows]
print(f'  Avg reactivation score : {statistics.mean(scores):.1f}')
print(f'  Avg JAS25 spend        : Rs.{statistics.mean(spends):,.0f}')
print(f'  Avg dormant days       : {statistics.mean(dorms):.0f}')
print(f'  Median dormant days    : {statistics.median(dorms):.0f}')
print(f'\nFile ready: {out_path}')
print('Done!')
