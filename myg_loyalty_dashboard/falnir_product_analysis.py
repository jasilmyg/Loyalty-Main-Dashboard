import os, django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'myg_loyalty_dashboard.settings')
django.setup()
from analytics.clickhouse_service import get_ch_client
client = get_ch_client()

BRANCH   = 'FLF'
PERIOD_A = ['2026-07-28', '2026-07-29']
PERIOD_B = ['2026-08-29', '2026-08-30']

def get_product_data(dates):
    res = client.query("""
        SELECT
            im.product                          AS product,
            sum(s.sold_price)                   AS revenue,
            sum(s.qty)                          AS qty,
            count(DISTINCT s.invoice_no)        AS invoices
        FROM azure_sales_report s
        LEFT JOIN item_master im ON s.item_code = im.item_code
        WHERE s.branch = %(branch)s
          AND toDate(s.date) IN %(dates)s
        GROUP BY product
        ORDER BY revenue DESC
    """, parameters={'branch': BRANCH, 'dates': tuple(dates)})
    return res.result_rows

def get_summary(dates):
    res = client.query("""
        SELECT
            count(DISTINCT invoice_no)  AS invoices,
            sum(sold_price)             AS revenue,
            sum(qty)                    AS qty
        FROM azure_sales_report
        WHERE branch = %(branch)s
          AND toDate(date) IN %(dates)s
    """, parameters={'branch': BRANCH, 'dates': tuple(dates)})
    return res.result_rows[0]

# ─── Fetch data ───────────────────────────────────────────────────────────────
jul_rows = get_product_data(PERIOD_A)
aug_rows = get_product_data(PERIOD_B)
jul_sum  = get_summary(PERIOD_A)
aug_sum  = get_summary(PERIOD_B)

jul_total = jul_sum[1]
aug_total = aug_sum[1]

jul_map = {r[0]: {'rev': r[1], 'qty': r[2], 'inv': r[3]} for r in jul_rows}
aug_map = {r[0]: {'rev': r[1], 'qty': r[2], 'inv': r[3]} for r in aug_rows}
all_products = sorted(
    set(list(jul_map.keys()) + list(aug_map.keys())),
    key=lambda p: -(jul_map.get(p, {}).get('rev', 0) + aug_map.get(p, {}).get('rev', 0))
)

# ─── Print ───────────────────────────────────────────────────────────────────
print("\n" + "="*80)
print("  🏪 FALNIR FUTURE (FLF) — PRODUCT-LEVEL COMPARISON".center(80))
print("  Jul 28–29, 2026   vs   Aug 29–30, 2026".center(80))
print("="*80)

print(f"\n  {'':30s} {'── JUL 28–29 ──':>22s}   {'── AUG 29–30 ──':>22s}   {'CHANGE':>8s}")
print(f"  {'SUMMARY':30s} {'Invoices':>10s} {'Revenue':>12s}   {'Invoices':>10s} {'Revenue':>12s}   {'Rev %':>8s}")
print(f"  {'-'*78}")
print(f"  {'TOTAL':30s} {jul_sum[0]:>10,} {jul_sum[1]:>12,.0f}   {aug_sum[0]:>10,} {aug_sum[1]:>12,.0f}   {((aug_total-jul_total)/jul_total*100):>+7.1f}%")

# ─── Per-product comparison ───────────────────────────────────────────────────
print(f"\n  {'':30s} {'── JUL 28–29 ──':>32s}   {'── AUG 29–30 ──':>32s}   {'':>8s}")
print(f"  {'PRODUCT':30s} {'Revenue':>12s} {'Qty':>6s} {'%Tot':>6s}   {'Revenue':>12s} {'Qty':>6s} {'%Tot':>6s}   {'Rev Δ':>8s}")
print(f"  {'-'*90}")

for prod in all_products:
    j  = jul_map.get(prod, {})
    a  = aug_map.get(prod, {})
    jr = j.get('rev', 0)
    ar = a.get('rev', 0)
    jq = j.get('qty', 0)
    aq = a.get('qty', 0)
    jp = (jr / jul_total * 100) if jul_total else 0
    ap = (ar / aug_total * 100) if aug_total else 0

    if jr == 0 and ar == 0:
        continue
    if jr == 0:
        chg = "🆕 NEW"
    elif ar == 0:
        chg = "❌ GONE"
    else:
        chg = f"{((ar-jr)/jr*100):+.1f}%"

    pname = (prod or 'UNKNOWN')[:30]
    print(f"  {pname:30s} {jr:>12,.0f} {jq:>6,.0f} {jp:>5.1f}%   {ar:>12,.0f} {aq:>6,.0f} {ap:>5.1f}%   {chg:>8s}")

# ─── Insights ─────────────────────────────────────────────────────────────────
print(f"\n{'='*80}")
print("  📊 KEY INSIGHTS".center(80))
print(f"{'='*80}")

# Winners and losers
winners = [(p, aug_map[p]['rev'], jul_map.get(p,{}).get('rev',0)) for p in all_products
           if aug_map.get(p,{}).get('rev',0) > jul_map.get(p,{}).get('rev',0) and jul_map.get(p,{}).get('rev',0)>0]
losers  = [(p, aug_map.get(p,{}).get('rev',0), jul_map[p]['rev']) for p in all_products
           if aug_map.get(p,{}).get('rev',0) < jul_map.get(p,{}).get('rev',0) and aug_map.get(p,{}).get('rev',0)>0]
new_prods = [p for p in all_products if jul_map.get(p,{}).get('rev',0)==0 and aug_map.get(p,{}).get('rev',0)>0]
gone_prods= [p for p in all_products if aug_map.get(p,{}).get('rev',0)==0 and jul_map.get(p,{}).get('rev',0)>0]

print(f"\n  📈 GROWING PRODUCTS (Aug > Jul):")
for p,a,j in sorted(winners, key=lambda x: -(x[1]-x[2]))[:5]:
    print(f"     {p:30s}  Jul: {j:>10,.0f}  →  Aug: {a:>10,.0f}  ({(a-j)/j*100:+.1f}%)")

print(f"\n  📉 DECLINING PRODUCTS (Jul > Aug):")
for p,a,j in sorted(losers, key=lambda x: x[1]-x[2])[:5]:
    print(f"     {p:30s}  Jul: {j:>10,.0f}  →  Aug: {a:>10,.0f}  ({(a-j)/j*100:+.1f}%)")

if new_prods:
    print(f"\n  🆕 NEW IN AUG (not in Jul):")
    for p in new_prods:
        print(f"     {p:30s}  Aug Revenue: {aug_map[p]['rev']:>10,.0f}")

if gone_prods:
    print(f"\n  ❌ DROPPED IN AUG (were in Jul):")
    for p in gone_prods:
        print(f"     {p:30s}  Jul Revenue: {jul_map[p]['rev']:>10,.0f}")

print(f"\n{'='*80}\n")
