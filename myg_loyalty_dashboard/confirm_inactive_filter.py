import os, django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'myg_loyalty_dashboard.settings')
django.setup()
from analytics.clickhouse_service import get_ch_client
import pandas as pd

client   = get_ch_client()
CUTOFF   = '2024-08-31'
RCS_PATH = r"C:\Users\jasil_myg\Desktop\myG Loyalty Main Dashboard\myg_loyalty_dashboard\350 RCS LOYALTY POINTS DATA.xlsx"
FLT_PATH = r"C:\Users\jasil_myg\Desktop\myG Loyalty Main Dashboard\myg_loyalty_dashboard\Inactive_Customers_Filtered.xlsx"

print("=" * 65)
print("  CONFIRMATION CHECK")
print("=" * 65)

# ── A: RCS file count ─────────────────────────────────────────────────────────
print("\n[A] RCS File (header=None — includes all rows)")
df_rcs  = pd.read_excel(RCS_PATH, engine='calamine', header=None, dtype=str)
rcs_set = set(df_rcs.iloc[:, 0].str.strip().str.replace(r'\.0$', '', regex=True).dropna())
print(f"    Total RCS mobiles       : {len(rcs_set):>10,}")

# ── B: Azure DB — total inactive ──────────────────────────────────────────────
print("\n[B] Azure DB — Total Inactive Customers (last purchase <= 2024-08-31)")
res_inactive = client.query(f"""
    SELECT count() AS cnt
    FROM (
        SELECT customer_mobile, max(toDate(date)) AS last_purchase
        FROM azure_invoice_report
        WHERE length(customer_mobile) = 10
          AND customer_mobile != ''
          AND customer_mobile NOT IN ('1313131313','0000000000','9999999999')
          AND toDate(date) != toDate('1970-01-01')
          AND invoice_total > 0
        GROUP BY customer_mobile
    )
    WHERE last_purchase <= toDate('{CUTOFF}')
""")
total_inactive = res_inactive.result_rows[0][0]
print(f"    Total inactive in DB    : {total_inactive:>10,}")

# ── C: Azure DB — RCS ∩ Inactive overlap ─────────────────────────────────────
print("\n[C] Azure DB — Overlap: customers in BOTH inactive AND active in 2025/2026")
# Count how many inactive customers also appear in azure with last purchase > CUTOFF
# i.e., they ARE in RCS (active) — check by fetching inactive mobiles and intersecting
res_inact_mobiles = client.query(f"""
    SELECT customer_mobile
    FROM (
        SELECT customer_mobile, max(toDate(date)) AS last_purchase
        FROM azure_invoice_report
        WHERE length(customer_mobile) = 10
          AND customer_mobile != ''
          AND customer_mobile NOT IN ('1313131313','0000000000','9999999999')
          AND toDate(date) != toDate('1970-01-01')
          AND invoice_total > 0
        GROUP BY customer_mobile
    )
    WHERE last_purchase <= toDate('{CUTOFF}')
""")
inactive_mobiles = set(r[0] for r in res_inact_mobiles.result_rows)
overlap = rcs_set & inactive_mobiles
print(f"    Inactive in DB          : {len(inactive_mobiles):>10,}")
print(f"    RCS mobiles             : {len(rcs_set):>10,}")
print(f"    Overlap (RCS ∩ inactive): {len(overlap):>10,}")

# ── D: Expected filtered count ───────────────────────────────────────────────
expected_filtered = len(inactive_mobiles) - len(overlap)
print(f"\n[D] Expected filtered count : {len(inactive_mobiles):,} - {len(overlap):,} = {expected_filtered:,}")

# ── E: Actual filtered file row count ────────────────────────────────────────
print("\n[E] Actual rows in Inactive_Customers_Filtered.xlsx")
xl = pd.ExcelFile(FLT_PATH, engine='openpyxl')
actual_rows = 0
for sheet in xl.sheet_names:
    if sheet == 'Summary':
        continue
    df_part = xl.parse(sheet, usecols=[0])
    actual_rows += len(df_part)
    print(f"    Sheet '{sheet}': {len(df_part):,}")
print(f"    Total rows in file      : {actual_rows:>10,}")

# ── F: Final verdict ──────────────────────────────────────────────────────────
print(f"\n{'='*65}")
print(f"  VERIFICATION RESULT")
print(f"{'='*65}")
print(f"  RCS mobiles            : {len(rcs_set):>10,}  ✅" )
print(f"  Total inactive (DB)    : {total_inactive:>10,}  {'✅' if total_inactive==2214635 else '❌'}")
print(f"  Overlap (removed)      : {len(overlap):>10,}  {'✅' if len(overlap)==1656 else '❌'}")
print(f"  Expected filtered      : {expected_filtered:>10,}  {'✅' if expected_filtered==2212979 else '❌'}")
print(f"  Actual rows in file    : {actual_rows:>10,}  {'✅' if actual_rows==expected_filtered else '❌ MISMATCH'}")
print(f"{'='*65}")
if actual_rows == expected_filtered:
    print("  ✅ ALL COUNTS CONFIRMED — Filtered Excel is correct")
else:
    print(f"  ❌ MISMATCH: expected {expected_filtered:,} but file has {actual_rows:,}")
print(f"{'='*65}\n")
