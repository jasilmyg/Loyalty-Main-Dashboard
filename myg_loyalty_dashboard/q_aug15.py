import os; os.environ.setdefault('DJANGO_SETTINGS_MODULE','myg_loyalty_dashboard.settings')
import django; django.setup()
from analytics.clickhouse_service import get_ch_client
ch = get_ch_client()

# Check mutations
try:
    muts = ch.query("SELECT mutation_id, command, is_done, parts_to_do FROM system.mutations WHERE table = 'azure_invoice_report' ORDER BY create_time DESC LIMIT 5").result_rows
    print('Mutations on azure_invoice_report:')
    for m in muts:
        print(f'  id={m[0]}  done={m[2]}  parts_todo={m[3]}  cmd={str(m[1])[:60]}')
except Exception as e:
    print(f'Could not query mutations: {e}')

# Direct count for Aug 14
r = ch.query("SELECT count(), sum(invoice_total) FROM azure_invoice_report WHERE toDate(date) = '2026-08-14'").result_rows[0]
print(f'Aug 14 count: {int(r[0]):,}  rev={float(r[1])/1e7:.2f}Cr')

# Full Aug 10-16 picture
rows = ch.query("SELECT toDate(date), count(), sum(invoice_total) FROM azure_invoice_report WHERE toDate(date) >= '2026-08-10' AND toDate(date) <= '2026-08-16' AND toDate(date) != '1970-01-01' GROUP BY toDate(date) ORDER BY toDate(date)").result_rows
print()
print('Aug 10-16 summary:')
for r in rows:
    print(f'  {r[0]}  rows={int(r[1]):,}  rev={float(r[2])/1e7:.2f}Cr')
