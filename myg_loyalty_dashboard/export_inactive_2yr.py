import os, django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'myg_loyalty_dashboard.settings')
django.setup()
from analytics.clickhouse_service import get_ch_client
import pandas as pd
from datetime import datetime

client = get_ch_client()

CUTOFF = '2024-08-31'
TODAY  = '2026-08-31'

print("Fetching inactive customers (last 2 years) from azure_invoice_report...")
print(f"Cutoff: on or before {CUTOFF}  |  Total expected: ~22,14,635\n")

# ─── Full data fetch ──────────────────────────────────────────────────────────
res = client.query(f"""
    SELECT
        customer_mobile,
        last_purchase,
        first_purchase,
        total_visits,
        total_spent,
        toYear(last_purchase)   AS last_year
    FROM (
        SELECT
            customer_mobile,
            max(toDate(date))               AS last_purchase,
            min(toDate(date))               AS first_purchase,
            count(DISTINCT invoice_no)      AS total_visits,
            sum(invoice_total)              AS total_spent
        FROM azure_invoice_report
        WHERE length(customer_mobile) = 10
          AND customer_mobile != ''
          AND customer_mobile NOT IN ('1313131313','0000000000','9999999999')
          AND toDate(date) != toDate('1970-01-01')
          AND invoice_total > 0
        GROUP BY customer_mobile
    )
    WHERE last_purchase <= toDate('{CUTOFF}')
    ORDER BY last_purchase DESC
""")

print(f"Records fetched: {len(res.result_rows):,}")

# ─── Build DataFrame ──────────────────────────────────────────────────────────
df = pd.DataFrame(res.result_rows, columns=[
    'Customer Mobile', 'Last Purchase Date', 'First Purchase Date',
    'Total Visits', 'Total Spent (Rs)', 'Last Purchase Year'
])

df['Days Since Last Purchase'] = (pd.Timestamp(TODAY) - pd.to_datetime(df['Last Purchase Date'])).dt.days
df['Total Spent (Rs)']         = df['Total Spent (Rs)'].round(2)

# ─── Summary breakdown ────────────────────────────────────────────────────────
print("\n  Breakdown by Last Purchase Year:")
print(f"  {'Year':8s} {'Count':>12s} {'Avg Spent':>14s}")
print(f"  {'-'*36}")
for yr, grp in df.groupby('Last Purchase Year', sort=False):
    print(f"  {yr:8}   {len(grp):>10,}   ₹{grp['Total Spent (Rs)'].mean():>12,.2f}")

print(f"\n  Days inactive range: {df['Days Since Last Purchase'].min()} – {df['Days Since Last Purchase'].max()} days")
print(f"  Avg Spent per customer: ₹{df['Total Spent (Rs)'].mean():,.2f}")
print(f"  Avg Visits per customer: {df['Total Visits'].mean():.2f}")

# ─── Export to Excel ──────────────────────────────────────────────────────────
out_path = r"C:\Users\jasil_myg\Desktop\myG Loyalty Main Dashboard\myg_loyalty_dashboard\Inactive_Customers_Last2Years.xlsx"
print(f"\nExporting to Excel... ({len(df):,} rows)")

with pd.ExcelWriter(out_path, engine='xlsxwriter') as writer:
    wb  = writer.book

    # ── Formats ──────────────────────────────────────────────────────────────
    hdr_fmt  = wb.add_format({'bold': True, 'bg_color': '#1E3A5F', 'font_color': 'white',
                               'border': 1, 'align': 'center', 'valign': 'vcenter', 'text_wrap': True})
    num_fmt  = wb.add_format({'num_format': '#,##0', 'border': 1})
    money_fmt= wb.add_format({'num_format': '₹#,##0.00', 'border': 1})
    date_fmt = wb.add_format({'num_format': 'dd-mmm-yyyy', 'border': 1})
    cell_fmt = wb.add_format({'border': 1})
    alt_fmt  = wb.add_format({'bg_color': '#EBF5FF', 'border': 1})
    alt_num  = wb.add_format({'bg_color': '#EBF5FF', 'num_format': '#,##0', 'border': 1})
    alt_mon  = wb.add_format({'bg_color': '#EBF5FF', 'num_format': '₹#,##0.00', 'border': 1})
    alt_date = wb.add_format({'bg_color': '#EBF5FF', 'num_format': 'dd-mmm-yyyy', 'border': 1})

    # ── Sheet 1: Summary ─────────────────────────────────────────────────────
    ws_sum = wb.add_worksheet('Summary')
    ws_sum.set_column('A:A', 28)
    ws_sum.set_column('B:B', 18)

    title_fmt = wb.add_format({'bold': True, 'font_size': 14, 'bg_color': '#1E3A5F',
                                'font_color': 'white', 'align': 'center', 'valign': 'vcenter'})
    sub_fmt   = wb.add_format({'bold': True, 'bg_color': '#2E6DA4', 'font_color': 'white',
                                'border': 1, 'align': 'left'})
    val_fmt   = wb.add_format({'border': 1, 'num_format': '#,##0'})
    val_str   = wb.add_format({'border': 1})
    val_pct   = wb.add_format({'border': 1, 'num_format': '0.0%'})
    val_money = wb.add_format({'border': 1, 'num_format': '₹#,##0.00'})

    ws_sum.merge_range('A1:B1', 'INACTIVE CUSTOMERS — LAST 2 YEARS REPORT', title_fmt)
    ws_sum.set_row(0, 30)
    ws_sum.write('A2', 'Report Generated', sub_fmt)
    ws_sum.write('B2', datetime.now().strftime('%d-%b-%Y %H:%M'), val_str)
    ws_sum.write('A3', 'Cutoff Date (inactive from)', sub_fmt)
    ws_sum.write('B3', CUTOFF, val_str)
    ws_sum.write('A4', 'Total Inactive Customers', sub_fmt)
    ws_sum.write('B4', len(df), val_fmt)
    ws_sum.write('A5', 'Avg Total Spent (Rs)', sub_fmt)
    ws_sum.write('B5', df['Total Spent (Rs)'].mean(), val_money)
    ws_sum.write('A6', 'Avg Visits', sub_fmt)
    ws_sum.write('B6', round(df['Total Visits'].mean(), 2), wb.add_format({'border': 1}))
    ws_sum.write('A7', 'Min Days Inactive', sub_fmt)
    ws_sum.write('B7', int(df['Days Since Last Purchase'].min()), val_fmt)
    ws_sum.write('A8', 'Max Days Inactive', sub_fmt)
    ws_sum.write('B8', int(df['Days Since Last Purchase'].max()), val_fmt)

    ws_sum.write('A10', 'BREAKDOWN BY LAST PURCHASE YEAR', title_fmt)
    ws_sum.merge_range('A10:B10', 'BREAKDOWN BY LAST PURCHASE YEAR', title_fmt)
    ws_sum.write('A11', 'Last Purchase Year', hdr_fmt)
    ws_sum.write('B11', 'Customer Count', hdr_fmt)
    r = 11
    for yr, grp in df.groupby('Last Purchase Year'):
        ws_sum.write(r, 0, int(yr), val_fmt)
        ws_sum.write(r, 1, len(grp), val_fmt)
        r += 1

    # ── Sheet 2: Full Data ────────────────────────────────────────────────────
    ws = wb.add_worksheet('Inactive Customers')
    ws.freeze_panes(1, 0)
    ws.autofilter(0, 0, 0, len(df.columns) - 1)

    col_widths = [18, 16, 16, 12, 18, 15, 22]
    for i, w in enumerate(col_widths):
        ws.set_column(i, i, w)

    headers = list(df.columns)
    for ci, h in enumerate(headers):
        ws.write(0, ci, h, hdr_fmt)

    for ri, row in enumerate(df.itertuples(index=False), start=1):
        even = ri % 2 == 0
        cf   = alt_fmt  if even else cell_fmt
        cn   = alt_num  if even else num_fmt
        cm   = alt_mon  if even else money_fmt
        cd   = alt_date if even else date_fmt

        ws.write(ri, 0, str(row[0]),  cf)   # Mobile
        ws.write(ri, 1, str(row[1]),  cf)   # Last Purchase Date
        ws.write(ri, 2, str(row[2]),  cf)   # First Purchase Date
        ws.write(ri, 3, row[3],       cn)   # Total Visits
        ws.write(ri, 4, row[4],       cm)   # Total Spent
        ws.write(ri, 5, int(row[5]),  cn)   # Last Year
        ws.write(ri, 6, int(row[6]),  cn)   # Days Inactive

    ws_sum.set_column('A:B', 28)

print(f"✅  Excel saved: {out_path}")
print(f"    Rows: {len(df):,}  |  Columns: {len(df.columns)}")
