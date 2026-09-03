"""
B. Sales — Annual Turnover by Category + Average Bill Value
Last 2 years: Aug 2024–Aug 2025 (Y1) and Aug 2025–Aug 2026 (Y2)
"""
import os, sys, django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'myg_loyalty_dashboard.settings')
sys.path.insert(0, '.')
django.setup()
from analytics.clickhouse_service import get_ch_client
ch = get_ch_client()

Y2_S, Y2_E = '2025-08-29', '2026-08-29'
Y1_S, Y1_E = '2024-08-29', '2025-08-28'

# Business category → product name mapping
CATEGORIES = {
    'Mobile':          ["'MOBILE'"],
    'IT':              ["'LAPTOP'","'IT PRODUCT'","'IT ACCESSORIES'","'TABLET'","'PRINTER'","'DESKTOP'","'MONITOR'","'LAPTOP BAG'"],
    'TV':              ["'TV'"],
    'AC':              ["'AIR CONDITIONER'","'AC OUTDOOR'","'STABILIZER'"],
    'Refrigerator':    ["'REFRIGERATORS'","'FREEZER'"],
    'Washing Machine': ["'WASHING MACHINES'","'DRYER'"],
    'Kitchen/HA':      ["'MICROWAVE OVEN'","'SMALL APPLIANCES'","'HOME APPLIANCES'","'CROCKERY'","'DISH WASHER'","'HOUSE HOLD'","'HA ACCESSORIES'"],
    'Accessories':     ["'EAR WEARABLES'","'ACCESSORIES'","'AUDIO'","'SMART WATCH'","'HOME THEATRE'","'GLAMSHIELD'","'STORAGE DEVICES'","'GAMING'","'PERSONAL CARE'","'FRAGRANCE'","'OFFER KIT'","'CAMERA'"],
}

def fetch(s, e, prod_list):
    prods_sql = ', '.join(prod_list)
    r = ch.query(f"""
        SELECT
            sum(toFloat64(s.sold_price)) AS total_sales,
            sum(toFloat64(s.qty))        AS total_qty,
            countDistinct(s.invoice_no)  AS invoices
        FROM azure_sales_report s
        LEFT JOIN item_master m ON s.item_code = m.item_code
        WHERE toDate(s.date) BETWEEN '{s}' AND '{e}'
          AND toDate(s.date) != '1970-01-01'
          AND m.product IN ({prods_sql})
    """).result_rows[0]
    return float(r[0] or 0), float(r[1] or 0), int(r[2] or 0)

def fetch_overall(s, e):
    r = ch.query(f"""
        SELECT
            sum(toFloat64(s.sold_price)) AS total_sales,
            sum(toFloat64(s.qty))        AS total_qty,
            countDistinct(s.invoice_no)  AS invoices
        FROM azure_sales_report s
        WHERE toDate(s.date) BETWEEN '{s}' AND '{e}'
          AND toDate(s.date) != '1970-01-01'
    """).result_rows[0]
    return float(r[0] or 0), float(r[1] or 0), int(r[2] or 0)

print("Fetching data... (may take 1-2 min)")

results = {}
for cat, prods in CATEGORIES.items():
    print(f"  → {cat}...")
    y1 = fetch(Y1_S, Y1_E, prods)
    y2 = fetch(Y2_S, Y2_E, prods)
    results[cat] = {'y1': y1, 'y2': y2}

print("  → Overall totals...")
ov1 = fetch_overall(Y1_S, Y1_E)
ov2 = fetch_overall(Y2_S, Y2_E)

# ─── Print Report ────────────────────────────────────────────────
print()
print("=" * 115)
print("  B. SALES — Annual Turnover by Category")
print(f"  Year 1: {Y1_S} → {Y1_E}   |   Year 2: {Y2_S} → {Y2_E}")
print("=" * 115)
print(f"{'Category':<20} {'Y1 QTY':>12} {'Y1 Sales (Cr)':>16} {'Y1 Share':>9} {'Y2 QTY':>12} {'Y2 Sales (Cr)':>16} {'Y2 Share':>9} {'YoY Δ':>8}")
print("-" * 115)

# Calculate totals for listed categories only (for share %)
tot_y1_sales = sum(r['y1'][0] for r in results.values())
tot_y2_sales = sum(r['y2'][0] for r in results.values())
tot_y1_qty   = sum(r['y1'][1] for r in results.values())
tot_y2_qty   = sum(r['y2'][1] for r in results.values())

for cat, r in results.items():
    y1s, y1q, y1i = r['y1']
    y2s, y2q, y2i = r['y2']
    y1_share = y1s / tot_y1_sales * 100 if tot_y1_sales > 0 else 0
    y2_share = y2s / tot_y2_sales * 100 if tot_y2_sales > 0 else 0
    yoy = (y2s - y1s) / y1s * 100 if y1s > 0 else 0
    print(f"  {cat:<18} {y1q:>12,.0f} {y1s/1e7:>14,.2f} Cr {y1_share:>7.1f}% {y2q:>12,.0f} {y2s/1e7:>14,.2f} Cr {y2_share:>7.1f}% {yoy:>+7.1f}%")

print("-" * 115)
print(f"  {'Listed Total':<18} {tot_y1_qty:>12,.0f} {tot_y1_sales/1e7:>14,.2f} Cr {'':>9} {tot_y2_qty:>12,.0f} {tot_y2_sales/1e7:>14,.2f} Cr {'':>9}")
print(f"  {'Overall (all)' :<18} {ov1[1]:>12,.0f} {ov1[0]/1e7:>14,.2f} Cr {'':>9} {ov2[1]:>12,.0f} {ov2[0]/1e7:>14,.2f} Cr {'':>9}")
print("=" * 115)

print("\n  1. AVERAGE BILL VALUE")
print("-" * 60)
print(f"  {'Category':<20}  {'Y1 Avg Bill':>16}  {'Y2 Avg Bill':>16}")
print("-" * 60)
for cat, r in results.items():
    y1s, y1q, y1i = r['y1']
    y2s, y2q, y2i = r['y2']
    avg1 = y1s / y1i if y1i > 0 else 0
    avg2 = y2s / y2i if y2i > 0 else 0
    print(f"  {cat:<20}  ₹{avg1:>14,.0f}  ₹{avg2:>14,.0f}")
print("-" * 60)
avg1_ov = ov1[0] / ov1[2] if ov1[2] > 0 else 0
avg2_ov = ov2[0] / ov2[2] if ov2[2] > 0 else 0
print(f"  {'Overall (all)':<20}  ₹{avg1_ov:>14,.0f}  ₹{avg2_ov:>14,.0f}")
print("=" * 60)
