"""
Q13 FINAL — Mobile Buyers Cross-Category Conversion
Excludes all internal/non-retail items (stationery, scheme, service, demo, etc.)
Groups results into business categories
"""
import os, sys, django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'myg_loyalty_dashboard.settings')
sys.path.insert(0, '.')
django.setup()
from analytics.clickhouse_service import get_ch_client
ch = get_ch_client()

# All internal / non-retail products to exclude
EXCLUDE = """'STATIONERY ITEMS','SCHEME','GDOT CARE','D SPARE','OSG WARRANTY',
             'SERVICE','TOTAL SECURITY','LG AMC','SERVICE CHARGES',
             'DEMO','DEMO LAPTOP','DEMO ACCESSORIES','MYG DOMO','MYG VERSE',
             'DIY','CONTRACT WORK','CEGI','RIG','PROTECT MAX','CARE PLUS',
             'MOBILE ANTIVIRUS','DEMO ACCESSORIES','HA ACCESSORIES','CCTV','MONITOR'"""

# Business category mapping
CAT_MAP = {
    'Glamshield / Screen Protection': ['GLAMSHIELD'],
    'Ear Wearables':                  ['EAR WEARABLES'],
    'Accessories (Mobile/General)':   ['ACCESSORIES','IT ACCESSORIES','LAPTOP BAG'],
    'Smart Watch':                    ['SMART WATCH'],
    'Audio / Home Theatre':           ['AUDIO','HOME THEATRE'],
    'Small Appliances / Kitchen HA':  ['SMALL APPLIANCES','HOME APPLIANCES','MICROWAVE OVEN','CROCKERY','DISH WASHER','HOUSE HOLD'],
    'TV':                             ['TV'],
    'Laptop / IT':                    ['LAPTOP','TABLET','PRINTER','DESKTOP','IT PRODUCT','STORAGE DEVICES','GAMING','CAMERA'],
    'AC':                             ['AIR CONDITIONER','AC OUTDOOR','STABILIZER'],
    'Washing Machine':                ['WASHING MACHINES','DRYER'],
    'Refrigerator':                   ['REFRIGERATORS','FREEZER'],
    'Smart Choice / Offers':          ['SMART CHOICE','OFFER KIT'],
    'Personal Care / Fragrance':      ['PERSONAL CARE','FRAGRANCE'],
    'Gift Items':                     ['GIFT ITEMS'],
}

print("Fetching final mobile cross-category report... (~2 min)")

# Total mobile buyers
total_mobile = ch.query("""
    SELECT countDistinct(ai.customer_mobile)
    FROM azure_invoice_report ai
    INNER JOIN azure_sales_report sr ON ai.invoice_no = sr.invoice_no
    INNER JOIN item_master m         ON sr.item_code = m.item_code
    WHERE m.product = 'MOBILE'
      AND length(trim(ai.customer_mobile)) >= 10
      AND toDate(ai.date) BETWEEN '2021-01-01' AND '2026-08-29'
      AND toDate(ai.date) != '1970-01-01'
""").result_rows[0][0]

# Per-product cross-buyers (already filtered to retail products)
raw = ch.query(f"""
    WITH mobile_first AS (
        SELECT
            ai.customer_mobile               AS mobile,
            min(toDate(ai.date))             AS first_mobile_date
        FROM azure_invoice_report ai
        INNER JOIN azure_sales_report sr ON ai.invoice_no = sr.invoice_no
        INNER JOIN item_master m         ON sr.item_code = m.item_code
        WHERE m.product = 'MOBILE'
          AND length(trim(ai.customer_mobile)) >= 10
          AND toDate(ai.date) BETWEEN '2021-01-01' AND '2026-08-29'
          AND toDate(ai.date) != '1970-01-01'
        GROUP BY ai.customer_mobile
    )
    SELECT
        m2.product                        AS product,
        countDistinct(ai2.customer_mobile) AS buyers
    FROM azure_invoice_report ai2
    INNER JOIN azure_sales_report sr2 ON ai2.invoice_no = sr2.invoice_no
    INNER JOIN item_master m2         ON sr2.item_code = m2.item_code
    INNER JOIN mobile_first mf        ON ai2.customer_mobile = mf.mobile
    WHERE m2.product NOT IN ({EXCLUDE})
      AND m2.product != 'MOBILE'
      AND toDate(ai2.date) > mf.first_mobile_date
      AND toDate(ai2.date) != '1970-01-01'
      AND length(trim(ai2.customer_mobile)) >= 10
    GROUP BY product
    ORDER BY buyers DESC
""").result_rows

# Map raw products to business categories
prod_to_buyers = {str(r[0]): int(r[1]) for r in raw}
cat_results = {}
for cat_name, prods in CAT_MAP.items():
    buyers = 0
    for p in prods:
        buyers += prod_to_buyers.get(p, 0)
    cat_results[cat_name] = buyers

# Total unique cross-shoppers (any retail product after mobile)
total_cross = ch.query(f"""
    WITH mobile_first AS (
        SELECT
            ai.customer_mobile               AS mobile,
            min(toDate(ai.date))             AS first_mobile_date
        FROM azure_invoice_report ai
        INNER JOIN azure_sales_report sr ON ai.invoice_no = sr.invoice_no
        INNER JOIN item_master m         ON sr.item_code = m.item_code
        WHERE m.product = 'MOBILE'
          AND length(trim(ai.customer_mobile)) >= 10
          AND toDate(ai.date) BETWEEN '2021-01-01' AND '2026-08-29'
          AND toDate(ai.date) != '1970-01-01'
        GROUP BY ai.customer_mobile
    )
    SELECT countDistinct(ai2.customer_mobile)
    FROM azure_invoice_report ai2
    INNER JOIN azure_sales_report sr2 ON ai2.invoice_no = sr2.invoice_no
    INNER JOIN item_master m2         ON sr2.item_code = m2.item_code
    INNER JOIN mobile_first mf        ON ai2.customer_mobile = mf.mobile
    WHERE m2.product NOT IN ({EXCLUDE})
      AND m2.product != 'MOBILE'
      AND toDate(ai2.date) > mf.first_mobile_date
      AND toDate(ai2.date) != '1970-01-01'
      AND length(trim(ai2.customer_mobile)) >= 10
""").result_rows[0][0]

# Print final report
print()
print("=" * 80)
print("  Q13 FINAL — Mobile Buyers Cross-Category Conversion (Retail Only)")
print("=" * 80)
print(f"  Total Mobile Buyers (2021–2026)     : {total_mobile:>10,}")
print(f"  Cross-shopped (retail categories)   : {total_cross:>10,}  ({total_cross/total_mobile*100:.1f}%)")
print(f"  Mobile-only buyers (no cross-sell)  : {total_mobile-total_cross:>10,}  ({(total_mobile-total_cross)/total_mobile*100:.1f}%)")
print()
print(f"  {'Business Category':<38} {'Buyers':>10} {'% of Mobile Buyers':>20} {'% of Cross-buyers':>18}")
print("  " + "-" * 90)

sorted_cats = sorted(cat_results.items(), key=lambda x: x[1], reverse=True)
for cat, buyers in sorted_cats:
    if buyers == 0:
        continue
    pct_mob   = buyers / total_mobile * 100
    pct_cross = buyers / total_cross  * 100
    print(f"  {cat:<38} {buyers:>10,} {pct_mob:>19.1f}% {pct_cross:>17.1f}%")

print("=" * 80)
print()
print("  NOTE: A customer can appear in multiple categories (multi-category buyer)")
print("        Percentages add up to >100% intentionally.")
