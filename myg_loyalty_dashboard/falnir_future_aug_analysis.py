import os, django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'myg_loyalty_dashboard.settings')
django.setup()

from analytics.clickhouse_service import get_ch_client
client = get_ch_client()

# ============================================================
#  Falnir Future (FLF) – New vs Repeat Customer Analysis
#  using azure_invoice_report table
#  Task 1: Aug 14, 15, 16  (base data cut-off: Aug 13)
#  Task 2: Aug 29, 30       (base data cut-off: Aug 28)
# ============================================================

BRANCH = 'FLF'

TASKS = [
    {
        'label'   : 'Aug 14-16 2026  (base up to Aug 13)',
        'dates'   : ['2026-08-14', '2026-08-15', '2026-08-16'],
        'base_end': '2026-08-13',
    },
    {
        'label'   : 'Aug 29-30 2026  (base up to Aug 28)',
        'dates'   : ['2026-08-29', '2026-08-30'],
        'base_end': '2026-08-28',
    },
]

def analyse(task):
    dates    = task['dates']
    base_end = task['base_end']
    label    = task['label']

    print(f"\n{'='*68}")
    print(f"  Falnir Future (FLF)  |  {label}")
    print(f"{'='*68}")

    all_day_totals = {'total': 0, 'new': 0, 'repeat': 0}

    for date_str in dates:
        # Step 1: unique customers who visited FLF on this date
        res = client.query("""
            SELECT DISTINCT customer_mobile
            FROM azure_invoice_report
            WHERE branch = %(branch)s
              AND toDate(date) = %(dt)s
              AND customer_mobile != ''
              AND customer_mobile IS NOT NULL
        """, parameters={'branch': BRANCH, 'dt': date_str})

        day_customers = [r[0] for r in res.result_rows]
        total = len(day_customers)

        if total == 0:
            print(f"  {date_str}  |  Total:    0  |  New:    0 (0.0%)  |  Repeat:    0 (0.0%)")
            continue

        # Step 2: check who had ANY purchase on or before base_end (repeat)
        res2 = client.query("""
            SELECT DISTINCT customer_mobile
            FROM azure_invoice_report
            WHERE customer_mobile IN %(mobiles)s
              AND toDate(date) <= %(base_end)s
              AND customer_mobile != ''
        """, parameters={'mobiles': day_customers, 'base_end': base_end})

        repeat_customers = set(r[0] for r in res2.result_rows)
        repeat_c = len(repeat_customers)
        new_c    = total - repeat_c

        pct_new    = round((new_c    / total) * 100, 1)
        pct_repeat = round((repeat_c / total) * 100, 1)

        all_day_totals['total']  += total
        all_day_totals['new']    += new_c
        all_day_totals['repeat'] += repeat_c

        print(f"  {date_str}  |  Total: {total:>4}  |  New: {new_c:>4} ({pct_new}%)  |  Repeat: {repeat_c:>4} ({pct_repeat}%)")

    # Combined unique across all days
    date_tuple = tuple(dates)
    res_all = client.query("""
        SELECT DISTINCT customer_mobile
        FROM azure_invoice_report
        WHERE branch = %(branch)s
          AND toDate(date) IN %(dates)s
          AND customer_mobile != ''
          AND customer_mobile IS NOT NULL
    """, parameters={'branch': BRANCH, 'dates': date_tuple})

    all_customers = [r[0] for r in res_all.result_rows]
    total_unique  = len(all_customers)

    if total_unique > 0:
        res_rep = client.query("""
            SELECT DISTINCT customer_mobile
            FROM azure_invoice_report
            WHERE customer_mobile IN %(mobiles)s
              AND toDate(date) <= %(base_end)s
              AND customer_mobile != ''
        """, parameters={'mobiles': all_customers, 'base_end': base_end})

        repeat_unique = len(res_rep.result_rows)
        new_unique    = total_unique - repeat_unique

        pct_new    = round((new_unique    / total_unique) * 100, 1)
        pct_repeat = round((repeat_unique / total_unique) * 100, 1)
    else:
        new_unique = repeat_unique = 0
        pct_new = pct_repeat = 0.0

    print(f"  {'-'*65}")
    print(f"  COMBINED (unique)    |  Total: {total_unique:>4}  |  New: {new_unique:>4} ({pct_new}%)  |  Repeat: {repeat_unique:>4} ({pct_repeat}%)")
    print(f"{'='*68}")


for task in TASKS:
    analyse(task)

print("\nDone.\n")
