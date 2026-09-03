import os, django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'myg_loyalty_dashboard.settings')
django.setup()
from analytics.clickhouse_service import get_ch_client
import pandas as pd
from datetime import datetime
import math

client = get_ch_client()

CUTOFF = '2024-08-31'
TODAY  = '2026-08-31'

print("Fetching 22,14,635 inactive customers from azure_invoice_report...")

res = client.query(f"""
    SELECT
        customer_mobile,
        toString(last_purchase)     AS last_purchase_str,
        toString(first_purchase)    AS first_purchase_str,
        total_visits,
        total_spent,
        toYear(last_purchase)       AS last_year
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

df = pd.DataFrame(res.result_rows, columns=[
    'Customer Mobile', 'Last Purchase Date', 'First Purchase Date',
    'Total Visits', 'Total Spent (Rs)', 'Last Purchase Year'
])
# Ensure date columns are strings
df['Last Purchase Date']  = df['Last Purchase Date'].astype(str)
df['First Purchase Date'] = df['First Purchase Date'].astype(str)
df['Days Since Last Purchase'] = (pd.Timestamp(TODAY) - pd.to_datetime(df['Last Purchase Date'])).dt.days
df['Total Spent (Rs)']         = df['Total Spent (Rs)'].round(2)
df['Total Visits']             = df['Total Visits'].astype(int)
df['Last Purchase Year']       = df['Last Purchase Year'].astype(int)

total    = len(df)
n_sheets = 3
chunk    = math.ceil(total / n_sheets)

print(f"\nTotal rows   : {total:,}")
print(f"Rows/sheet   : ~{chunk:,}")
print(f"Sheets       : {n_sheets}")

out_path = r"C:\Users\jasil_myg\Desktop\myG Loyalty Main Dashboard\myg_loyalty_dashboard\Inactive_Customers_3Sheets.xlsx"
print(f"\nWriting Excel: {out_path}")

HEADERS = list(df.columns)
COL_WIDTHS = [18, 16, 16, 12, 18, 15, 22]

with pd.ExcelWriter(out_path, engine='xlsxwriter') as writer:
    wb = writer.book

    # ── Shared formats ────────────────────────────────────────────────────────
    hdr_fmt   = wb.add_format({'bold': True, 'bg_color': '#1E3A5F', 'font_color': 'white',
                                'border': 1, 'align': 'center', 'valign': 'vcenter', 'text_wrap': True})
    cell_fmt  = wb.add_format({'border': 1, 'font_size': 9})
    alt_fmt   = wb.add_format({'bg_color': '#EBF5FF', 'border': 1, 'font_size': 9})
    num_fmt   = wb.add_format({'num_format': '#,##0', 'border': 1, 'font_size': 9})
    alt_num   = wb.add_format({'bg_color': '#EBF5FF', 'num_format': '#,##0', 'border': 1, 'font_size': 9})
    mon_fmt   = wb.add_format({'num_format': '0.00', 'border': 1, 'font_size': 9})
    alt_mon   = wb.add_format({'bg_color': '#EBF5FF', 'num_format': '0.00', 'border': 1, 'font_size': 9})

    tab_colors = ['#1E88E5', '#43A047', '#E53935']

    for sheet_idx in range(n_sheets):
        start = sheet_idx * chunk
        end   = min(start + chunk, total)
        part  = df.iloc[start:end].copy()

        sheet_name = f"Part {sheet_idx+1} ({start+1}-{end})"
        ws = wb.add_worksheet(sheet_name)
        ws.set_tab_color(tab_colors[sheet_idx])
        ws.freeze_panes(1, 0)
        ws.autofilter(0, 0, 0, len(HEADERS) - 1)
        ws.set_row(0, 20)

        # Column widths
        for ci, w in enumerate(COL_WIDTHS):
            ws.set_column(ci, ci, w)

        # Header row
        for ci, h in enumerate(HEADERS):
            ws.write(0, ci, h, hdr_fmt)

        print(f"  Writing sheet {sheet_idx+1}: rows {start+1:,} – {end:,} ({len(part):,} records)...")

        # Data rows
        for ri, row in enumerate(part.itertuples(index=False), start=1):
            even = ri % 2 == 0
            cf   = alt_fmt if even else cell_fmt
            cn   = alt_num if even else num_fmt
            cm   = alt_mon if even else mon_fmt

            ws.write(ri, 0, str(row[0]),  cf)   # Customer Mobile
            ws.write(ri, 1, str(row[1]),  cf)   # Last Purchase Date
            ws.write(ri, 2, str(row[2]),  cf)   # First Purchase Date
            ws.write(ri, 3, int(row[3]),  cn)   # Total Visits
            ws.write(ri, 4, float(row[4]), cm)  # Total Spent
            ws.write(ri, 5, int(row[5]),  cn)   # Last Purchase Year
            ws.write(ri, 6, int(row[6]),  cn)   # Days Since Last Purchase

        print(f"  ✅ Sheet {sheet_idx+1} done.")

    # ── Summary sheet (first sheet position via worksheet ordering trick) ────
    ws_s = wb.add_worksheet('Summary')
    ws_s.set_tab_color('#FF8F00')
    ws_s.set_column('A:A', 32)
    ws_s.set_column('B:B', 20)

    title_fmt = wb.add_format({'bold': True, 'font_size': 13, 'bg_color': '#1E3A5F',
                                'font_color': 'white', 'align': 'center', 'valign': 'vcenter'})
    lbl_fmt   = wb.add_format({'bold': True, 'bg_color': '#2E6DA4', 'font_color': 'white', 'border': 1})
    val_fmt   = wb.add_format({'border': 1, 'num_format': '#,##0'})
    val_str   = wb.add_format({'border': 1})
    val_mon   = wb.add_format({'border': 1, 'num_format': '₹#,##0.00'})
    yr_hdr    = wb.add_format({'bold': True, 'bg_color': '#1E3A5F', 'font_color': 'white', 'border': 1, 'align': 'center'})
    yr_val    = wb.add_format({'border': 1, 'num_format': '#,##0'})
    yr_mon    = wb.add_format({'border': 1, 'num_format': '₹#,##0.00'})

    ws_s.merge_range('A1:B1', 'INACTIVE CUSTOMERS — LAST 2 YEARS', title_fmt)
    ws_s.set_row(0, 28)
    rows_meta = [
        ('Report Generated',         datetime.now().strftime('%d-%b-%Y %H:%M'), val_str),
        ('Inactive Since (Cutoff)',   CUTOFF,  val_str),
        ('Total Inactive Customers',  total,   val_fmt),
        ('Sheets in this File',       3,       val_fmt),
        ('Rows per Sheet (~)',        chunk,   val_fmt),
        ('Avg Spent per Customer',    df['Total Spent (Rs)'].mean(),   val_mon),
        ('Avg Visits per Customer',   round(df['Total Visits'].mean(), 2), val_str),
        ('Min Days Inactive',         int(df['Days Since Last Purchase'].min()), val_fmt),
        ('Max Days Inactive',         int(df['Days Since Last Purchase'].max()), val_fmt),
    ]
    for i, (lbl, val, fmt) in enumerate(rows_meta, start=1):
        ws_s.write(i, 0, lbl, lbl_fmt)
        ws_s.write(i, 1, val, fmt)

    ws_s.write(11, 0, 'Last Purchase Year', yr_hdr)
    ws_s.write(11, 1, 'Customer Count',     yr_hdr)
    r = 12
    for yr, grp in df.groupby('Last Purchase Year'):
        ws_s.write(r, 0, int(yr),   yr_val)
        ws_s.write(r, 1, len(grp),  yr_val)
        r += 1

print(f"\n✅  Done! Excel saved:")
print(f"    {out_path}")
print(f"    Total rows: {total:,}  across 3 data sheets + 1 Summary sheet")
