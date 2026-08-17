import os, sys, csv, time
sys.stdout.reconfigure(encoding='utf-8')
sys.path.insert(0, '.')
os.environ['DJANGO_SETTINGS_MODULE'] = 'myg_loyalty_dashboard.settings'
import django; django.setup()
from analytics.clickhouse_service import get_ch_client
client = get_ch_client()

print('='*65)
print('MY PARF PERFUME — CUSTOMER DATA EXTRACTION')
print('='*65)

# 1. Count all MY PARF items
t = time.time()
r = client.query("""
    SELECT count() AS item_count, countDistinct(item_code) AS unique_items
    FROM item_master
    WHERE brand = 'MY PARF'
""")
row = r.result_rows[0]
print(f'\nTotal MY PARF items in catalog: {row[1]} variants ({row[0]} records)')

# 2. All MY PARF item codes
r_codes = client.query("""
    SELECT item_code, item_name FROM item_master WHERE brand = 'MY PARF'
""")
item_codes = [row[0] for row in r_codes.result_rows]
item_names = {row[0]: row[1] for row in r_codes.result_rows}
print(f'Item codes: {item_codes[:5]}...')

codes_str = "','".join(item_codes)

# 3. Total sales of MY PARF
t = time.time()
r2 = client.query(f"""
    SELECT
        count() AS total_line_items,
        countDistinct(invoice_no) AS total_invoices,
        round(sum(qty), 0) AS total_qty,
        round(sum(sold_price), 2) AS total_revenue,
        round(avg(sold_price), 2) AS avg_price,
        min(toDate(date)) AS first_sale,
        max(toDate(date)) AS last_sale
    FROM azure_sales_report
    WHERE item_code IN ('{codes_str}')
""")
row2 = r2.result_rows[0]
print(f'\nMY PARF Sales Summary:')
print(f'  Total line items  : {row2[0]:,}')
print(f'  Total invoices    : {row2[1]:,}')
print(f'  Total qty sold    : {row2[2]:,.0f} units')
print(f'  Total revenue     : Rs.{row2[3]:,.0f}')
print(f'  Avg price         : Rs.{row2[4]:,.0f}')
print(f'  First sale        : {row2[5]}')
print(f'  Last sale         : {row2[6]}')
print(f'  Time: {time.time()-t:.1f}s')

# 4. Join with invoice report to get customer mobiles
t = time.time()
r3 = client.query(f"""
    SELECT
        inv.customer_mobile,
        inv.branch,
        toString(toDate(inv.date)) AS purchase_date,
        inv.invoice_no,
        sr.item_code,
        sr.qty,
        sr.sold_price,
        sr.mop,
        inv.financier_name,
        inv.customer_type
    FROM azure_sales_report sr
    JOIN azure_invoice_report inv ON sr.invoice_no = inv.invoice_no
    WHERE sr.item_code IN ('{codes_str}')
      AND inv.invoice_total > 0
      AND length(inv.customer_mobile) = 10
      AND inv.customer_mobile NOT IN ('1313131313','0000000000','9999999999')
    ORDER BY inv.date DESC
    LIMIT 500000
""")
print(f'\nTotal records with customer mobile: {len(r3.result_rows):,}  ({time.time()-t:.1f}s)')

# 5. Unique customers
mobiles = set(row[0] for row in r3.result_rows)
print(f'Unique customers who bought MY PARF: {len(mobiles):,}')

# 6. Save full data to CSV
csv_path = 'analytics/my_parf_customers.csv'
headers = ['customer_mobile','branch','purchase_date','invoice_no','item_code',
           'item_name','qty','sold_price','mop','financier','customer_type']

with open(csv_path, 'w', encoding='utf-8-sig', newline='') as f:
    writer = csv.writer(f)
    writer.writerow(headers)
    for row in r3.result_rows:
        writer.writerow([
            row[0], row[1], row[2], row[3], row[4],
            item_names.get(row[4], ''), row[5], row[6], row[7], row[8], row[9]
        ])
print(f'\nSaved full data to {csv_path}')

# 7. Summary CSV (one row per customer)
import pandas as pd
df = pd.DataFrame(r3.result_rows,
    columns=['mobile','branch','date','invoice_no','item_code','qty','sold_price','mop','financier','ctype'])
df['item_name'] = df['item_code'].map(item_names)
df['date'] = pd.to_datetime(df['date'])

cust_summary = df.groupby('mobile').agg(
    total_purchases=('invoice_no','nunique'),
    total_qty=('qty','sum'),
    total_spend=('sold_price','sum'),
    avg_spend=('sold_price','mean'),
    first_purchase=('date','min'),
    last_purchase=('date','max'),
    branch=('branch','last'),
    financier=('financier','last'),
).reset_index()
cust_summary.columns = ['customer_mobile','total_invoices','total_qty','total_spend',
                         'avg_spend','first_purchase','last_purchase','branch','financier']

summary_path = 'analytics/my_parf_customers_summary.csv'
cust_summary.to_csv(summary_path, index=False, encoding='utf-8-sig')
print(f'Saved summary (1 row/customer) to {summary_path}')

# 8. Top scents
print('\nTop 10 MY PARF products by qty sold:')
top_items = df.groupby('item_code')['qty'].sum().sort_values(ascending=False).head(10)
for code, qty in top_items.items():
    print(f'  {item_names.get(code, code):60} {qty:,.0f} units')

# 9. Monthly trend
print('\nMonthly sales (last 12 months):')
df['month'] = df['date'].dt.to_period('M')
monthly = df.groupby('month').agg(
    invoices=('invoice_no','nunique'),
    qty=('qty','sum'),
    revenue=('sold_price','sum')
).tail(12)
for period, row in monthly.iterrows():
    print(f'  {str(period):10}  {row["invoices"]:>6} invoices  {row["qty"]:>8,.0f} qty  Rs.{row["revenue"]:>10,.0f}')

print('\nDone!')
