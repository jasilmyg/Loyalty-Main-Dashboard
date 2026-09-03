"""
july_new_repeat_analysis.py
============================
New vs Repeat Customer Count and Percentage for July 2026 date ranges.

Periods and Base Data:
  Period 1: July 4-5   -> base = all data UP TO July 3 (inclusive)
  Period 2: July 9-12  -> base = all data UP TO July 8 (inclusive)
  Period 3: July 16-19 -> base = all data UP TO July 15 (inclusive)

Logic:
  A customer (by mobile) is REPEAT if they appear in the base window.
  A customer is NEW if they do NOT appear in the base window.
  Each customer is counted once per period (deduplicated by mobile).
  Blank, empty, or very short mobiles are excluded.

Source: ClickHouse -> azure_invoice_report
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

# Period definitions: (label, period_start, period_end, base_end)
PERIODS = [
    ("July 4-5",   "2026-07-04", "2026-07-05", "2026-07-03"),
    ("July 9-12",  "2026-07-09", "2026-07-12", "2026-07-08"),
    ("July 16-19", "2026-07-16", "2026-07-19", "2026-07-15"),
]


def get_mobiles(start, end):
    """Return set of distinct valid customer_mobile values within [start, end]."""
    sql = (
        "SELECT DISTINCT customer_mobile "
        "FROM " + TABLE + " "
        "WHERE toDate(date) >= toDate('" + start + "') "
        "  AND toDate(date) <= toDate('" + end + "') "
        "  AND customer_mobile != '' "
        "  AND customer_mobile != '0' "
        "  AND length(customer_mobile) >= 7"
    )
    rows = client.query(sql).result_rows
    return {r[0].strip() for r in rows if r[0].strip()}


# ---- Summary table ----
print("=" * 70)
print("  NEW vs REPEAT CUSTOMER ANALYSIS - JULY 2026")
print("=" * 70)
print()

all_results = []

for label, p_start, p_end, base_end in PERIODS:
    print(f"  [{label}] Fetching base mobiles (up to {base_end})...", end="  ", flush=True)
    base_mobiles = get_mobiles("2020-01-01", base_end)
    print(f"{len(base_mobiles):,} unique customers in base")

    print(f"  [{label}] Fetching period mobiles ({p_start} to {p_end})...", end="  ", flush=True)
    period_mobiles = get_mobiles(p_start, p_end)
    print(f"{len(period_mobiles):,} unique customers in period")

    repeat = len(period_mobiles & base_mobiles)
    new    = len(period_mobiles - base_mobiles)
    total  = new + repeat
    new_pct    = round(new    / total * 100, 2) if total > 0 else 0.0
    repeat_pct = round(repeat / total * 100, 2) if total > 0 else 0.0

    all_results.append((label, base_end, total, new, repeat, new_pct, repeat_pct))
    print()

# Final summary
print()
print("=" * 70)
print("  FINAL SUMMARY")
print("=" * 70)
print()
print("  {:<15} {:<12} {:>8} {:>9} {:>7}  {:>9} {:>8}".format(
    "Period", "Base Until", "Total", "New", "New%", "Repeat", "Repeat%"))
print("  " + "-" * 68)
for label, base_end, total, new, repeat, new_pct, repeat_pct in all_results:
    print("  {:<15} {:<12} {:>8,} {:>9,} {:>6.1f}%  {:>9,} {:>7.1f}%".format(
        label, base_end, total, new, new_pct, repeat, repeat_pct))

# ---- Day-by-day breakdown ----
print()
print("=" * 70)
print("  DAY-BY-DAY BREAKDOWN")
print("=" * 70)
print()

for label, p_start, p_end, base_end in PERIODS:
    print(f"  Period: {label}   (base up to {base_end})")
    print("  {:<12} {:>8} {:>9} {:>7}  {:>9} {:>8}".format(
        "Date", "Total", "New", "New%", "Repeat", "Repeat%"))
    print("  " + "-" * 55)

    base_mobiles = get_mobiles("2020-01-01", base_end)

    sql_daily = (
        "SELECT toDate(date) AS day, customer_mobile "
        "FROM " + TABLE + " "
        "WHERE toDate(date) >= toDate('" + p_start + "') "
        "  AND toDate(date) <= toDate('" + p_end + "') "
        "  AND customer_mobile != '' "
        "  AND customer_mobile != '0' "
        "  AND length(customer_mobile) >= 7 "
        "ORDER BY day, customer_mobile"
    )
    rows = client.query(sql_daily).result_rows

    day_customers = defaultdict(set)
    for row in rows:
        day_customers[str(row[0])].add(row[1].strip())

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

print("Done.")
