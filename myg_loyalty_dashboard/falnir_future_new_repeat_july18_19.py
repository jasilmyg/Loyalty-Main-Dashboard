"""
falnir_future_new_repeat_july18_19.py
=======================================
New vs Repeat Customer Analysis for Falnir Future branch.

Period:    July 18 - July 19, 2026
Base Data: Falnir Future customers ONLY, up to July 17, 2026

Logic:
  REPEAT = customer mobile appeared at Falnir Future BEFORE July 18
  NEW    = customer mobile never purchased at Falnir Future before July 18
  Each customer counted once per day (deduplicated by mobile per day).
  Blank / short mobiles excluded.

Branch Code: FLF (FALNIR FUTURE, Dakshina Kannada)
Source:      ClickHouse -> azure_invoice_report
"""

import clickhouse_connect
from collections import defaultdict

# Connect
print("Connecting to ClickHouse...")
client = clickhouse_connect.get_client(
    host="pdhsuv47ec.ap-south-1.aws.clickhouse.cloud",
    port=8443,
    username="default",
    password="ZFlujj9SA_Iei",
    secure=True,
    connect_timeout=30,
    send_receive_timeout=120,
)
print("Connected.")
print()

TABLE = "azure_invoice_report"
BRANCH = "FLF"
BASE_END   = "2026-07-17"
PERIOD_START = "2026-07-18"
PERIOD_END   = "2026-07-19"

# ------- Step 1: Build base set (Falnir Future customers up to July 17) -------
print(f"Fetching Falnir Future base customers (up to {BASE_END})...")
sql_base = (
    "SELECT DISTINCT customer_mobile "
    "FROM " + TABLE + " "
    "WHERE branch = '" + BRANCH + "' "
    "  AND toDate(date) <= toDate('" + BASE_END + "') "
    "  AND customer_mobile != '' "
    "  AND customer_mobile != '0' "
    "  AND length(customer_mobile) >= 7"
)
base_rows = client.query(sql_base).result_rows
base_mobiles = {r[0].strip() for r in base_rows if r[0].strip()}
print(f"  --> {len(base_mobiles):,} unique Falnir Future customers in base (up to {BASE_END})")
print()

# ------- Step 2: Fetch July 18-19 data (Falnir Future only) -------------------
print(f"Fetching Falnir Future period data ({PERIOD_START} to {PERIOD_END})...")
sql_period = (
    "SELECT toDate(date) AS day, customer_mobile "
    "FROM " + TABLE + " "
    "WHERE branch = '" + BRANCH + "' "
    "  AND toDate(date) >= toDate('" + PERIOD_START + "') "
    "  AND toDate(date) <= toDate('" + PERIOD_END + "') "
    "  AND customer_mobile != '' "
    "  AND customer_mobile != '0' "
    "  AND length(customer_mobile) >= 7 "
    "ORDER BY day, customer_mobile"
)
period_rows = client.query(sql_period).result_rows

# Group by day
day_customers = defaultdict(set)
for row in period_rows:
    day_customers[str(row[0])].add(row[1].strip())

total_period_mobiles = set()
for mobs in day_customers.values():
    total_period_mobiles.update(mobs)

print(f"  --> {len(total_period_mobiles):,} unique Falnir Future customers in July 18-19")
print()

# ------- Step 3: Overall period summary ----------------------------------------
repeat_period = len(total_period_mobiles & base_mobiles)
new_period    = len(total_period_mobiles - base_mobiles)
total_period  = repeat_period + new_period
new_pct_p    = round(new_period    / total_period * 100, 2) if total_period > 0 else 0.0
repeat_pct_p = round(repeat_period / total_period * 100, 2) if total_period > 0 else 0.0

print("=" * 65)
print("  FALNIR FUTURE — NEW vs REPEAT ANALYSIS")
print("  Branch: FLF | Period: July 18-19, 2026")
print("  Base Data: Falnir Future only, up to July 17, 2026")
print("=" * 65)
print()
print("  OVERALL PERIOD SUMMARY (July 18-19 combined)")
print("  " + "-" * 55)
print(f"  {'Total Customers':<25} : {total_period:>8,}")
print(f"  {'New Customers':<25} : {new_period:>8,}   ({new_pct_p:.1f}%)")
print(f"  {'Repeat Customers':<25} : {repeat_period:>8,}   ({repeat_pct_p:.1f}%)")
print()

# ------- Step 4: Day-by-day breakdown ------------------------------------------
print("  DAY-BY-DAY BREAKDOWN")
print("  {:<12} {:>8} {:>9} {:>7}  {:>9} {:>8}".format(
    "Date", "Total", "New", "New%", "Repeat", "Repeat%"))
print("  " + "-" * 55)

for day_str in sorted(day_customers.keys()):
    mobs   = day_customers[day_str]
    total  = len(mobs)
    repeat = len(mobs & base_mobiles)
    new    = total - repeat
    new_pct    = round(new    / total * 100, 2) if total > 0 else 0.0
    repeat_pct = round(repeat / total * 100, 2) if total > 0 else 0.0
    print("  {:<12} {:>8,} {:>9,} {:>6.1f}%  {:>9,} {:>7.1f}%".format(
        day_str, total, new, new_pct, repeat, repeat_pct))

print()
print("  Note: Base = Falnir Future (FLF) purchase history up to July 17.")
print("        NEW    = first-ever purchase at Falnir Future.")
print("        REPEAT = has previous purchase at Falnir Future before July 18.")
print()
print("Done.")
