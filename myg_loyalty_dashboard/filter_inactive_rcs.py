import os, django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'myg_loyalty_dashboard.settings')
django.setup()
from analytics.clickhouse_service import get_ch_client
import pandas as pd
import math
from datetime import datetime

client   = get_ch_client()
CUTOFF   = '2024-08-31'
TODAY    = '2026-08-31'
RCS_PATH = r"C:\Users\jasil_myg\Desktop\myG Loyalty Main Dashboard\myg_loyalty_dashboard\350 RCS LOYALTY POINTS DATA.xlsx"
OUT_PATH = r"C:\Users\jasil_myg\Desktop\myG Loyalty Main Dashboard\myg_loyalty_dashboard\Inactive_Customers_Filtered.xlsx"

# ── Step 1: Load ALL RCS mobiles (header=None → includes first row number) ────
print("Loading RCS file (header=None)...")
df_rcs  = pd.read_excel(RCS_PATH, engine='calamine', header=None, dtype=str)
rcs_set = set(df_rcs.iloc[:, 0].str.strip().str.replace(r'\.0$', '', regex=True).dropna())
print(f"  RCS mobiles to exclude: {len(rcs_set):,}")

# ── Step 2: Query Azure DB — compute toYear INSIDE subquery (before toString) ─
print("\nQuerying azure_invoice_report for all inactive customers...")
res = client.query(f"""
    SELECT
        customer_mobile,
        toString(last_purchase)    AS last_purchase_str,
        toString(first_purchase)   AS first_purchase_str,
        total_visits,
        total_spent,
        last_year
    FROM (
        SELECT
            customer_mobile,
            max(toDate(date))            AS last_purchase,
            min(toDate(date))            AS first_purchase,
            count(DISTINCT invoice_no)   AS total_visits,
            sum(invoice_total)           AS total_spent,
            toYear(max(toDate(date)))    AS last_year
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
print(f"  Total inactive fetched : {len(res.result_rows):,}")

# ── Step 3: DataFrame + pandas filter ────────────────────────────────────────
df = pd.DataFrame(res.result_rows, columns=[
    'Customer Mobile', 'Last Purchase Date', 'First Purchase Date',
    'Total Visits', 'Total Spent (Rs)', 'Last Purchase Year'
])
before  = len(df)
df      = df[~df['Customer Mobile'].isin(rcs_set)].copy()
removed = before - len(df)
total   = len(df)
print(f"  RCS removed            : {removed:,}")
print(f"  Remaining              : {total:,}")

df['Days Since Last Purchase'] = (pd.Timestamp(TODAY) - pd.to_datetime(df['Last Purchase Date'])).dt.days
df['Total Spent (Rs)']         = pd.to_numeric(df['Total Spent (Rs)'], errors='coerce').round(2)
df['Total Visits']             = pd.to_numeric(df['Total Visits'],     errors='coerce').fillna(0).astype(int)
df['Last Purchase Year']       = pd.to_numeric(df['Last Purchase Year'],errors='coerce').fillna(0).astype(int)

# ── Step 4: Write Excel (3 data sheets + Summary) ────────────────────────────
n_sheets = 3
chunk    = math.ceil(total / n_sheets)
HEADERS  = list(df.columns)
COL_WIDTHS = [18, 16, 16, 12, 18, 15, 22]

print(f"\nWriting Excel ({total:,} rows, {n_sheets} sheets)...")
print(f"  Output: {OUT_PATH}")

with pd.ExcelWriter(OUT_PATH, engine='xlsxwriter') as writer:
    wb = writer.book
    hdr_fmt  = wb.add_format({'bold': True, 'bg_color': '#1E3A5F', 'font_color': 'white',
                               'border': 1, 'align': 'center', 'valign': 'vcenter', 'text_wrap': True})
    cell_fmt = wb.add_format({'border': 1, 'font_size': 9})
    alt_fmt  = wb.add_format({'bg_color': '#EBF5FF', 'border': 1, 'font_size': 9})
    num_fmt  = wb.add_format({'num_format': '#,##0', 'border': 1, 'font_size': 9})
    alt_num  = wb.add_format({'bg_color': '#EBF5FF', 'num_format': '#,##0', 'border': 1, 'font_size': 9})
    mon_fmt  = wb.add_format({'num_format': '0.00', 'border': 1, 'font_size': 9})
    alt_mon  = wb.add_format({'bg_color': '#EBF5FF', 'num_format': '0.00', 'border': 1, 'font_size': 9})
    tab_colors = ['#1E88E5', '#43A047', '#E53935']

    for idx in range(n_sheets):
        start = idx * chunk
        end   = min(start + chunk, total)
        part  = df.iloc[start:end]
        sname = f"Part {idx+1} ({start+1}-{end})"
        ws = wb.add_worksheet(sname)
        ws.set_tab_color(tab_colors[idx])
        ws.freeze_panes(1, 0)
        ws.autofilter(0, 0, 0, len(HEADERS) - 1)
        ws.set_row(0, 20)
        for ci, w in enumerate(COL_WIDTHS): ws.set_column(ci, ci, w)
        for ci, h in enumerate(HEADERS):    ws.write(0, ci, h, hdr_fmt)

        print(f"  Writing '{sname}': {len(part):,} rows...")
        for ri, row in enumerate(part.itertuples(index=False), start=1):
            even = ri % 2 == 0
            cf, cn, cm = (alt_fmt, alt_num, alt_mon) if even else (cell_fmt, num_fmt, mon_fmt)
            ws.write(ri, 0, str(row[0]),   cf)
            ws.write(ri, 1, str(row[1]),   cf)
            ws.write(ri, 2, str(row[2]),   cf)
            ws.write(ri, 3, int(row[3]),   cn)
            ws.write(ri, 4, float(row[4]), cm)
            ws.write(ri, 5, int(row[5]),   cn)
            ws.write(ri, 6, int(row[6]),   cn)
        print(f"  ✅ Part {idx+1} done.")

    # Summary sheet
    ws_s = wb.add_worksheet('Summary')
    ws_s.set_tab_color('#FF8F00')
    ws_s.set_column('A:A', 34); ws_s.set_column('B:B', 20)
    title_fmt = wb.add_format({'bold': True, 'font_size': 13, 'bg_color': '#1E3A5F',
                                'font_color': 'white', 'align': 'center', 'valign': 'vcenter'})
    lbl_fmt   = wb.add_format({'bold': True, 'bg_color': '#2E6DA4', 'font_color': 'white', 'border': 1})
    val_fmt   = wb.add_format({'border': 1, 'num_format': '#,##0'})
    val_str   = wb.add_format({'border': 1})
    yr_hdr    = wb.add_format({'bold': True, 'bg_color': '#1E3A5F', 'font_color': 'white',
                                'border': 1, 'align': 'center'})
    yr_val    = wb.add_format({'border': 1, 'num_format': '#,##0'})

    ws_s.merge_range('A1:B1', 'INACTIVE CUSTOMERS — RCS EXCLUDED (FILTERED)', title_fmt)
    ws_s.set_row(0, 28)
    for i, (lbl, val, fmt) in enumerate([
        ('Report Generated',             datetime.now().strftime('%d-%b-%Y %H:%M'), val_str),
        ('Inactive Cutoff Date',          CUTOFF,   val_str),
        ('Original Inactive Customers',   before,   val_fmt),
        ('RCS Customers Removed',         removed,  val_fmt),
        ('Remaining Inactive Customers',  total,    val_fmt),
        ('Avg Spent (Rs)',                round(df['Total Spent (Rs)'].mean(), 2), val_str),
        ('Avg Visits',                    round(df['Total Visits'].mean(), 2),     val_str),
        ('Min Days Inactive',             int(df['Days Since Last Purchase'].min()), val_fmt),
        ('Max Days Inactive',             int(df['Days Since Last Purchase'].max()), val_fmt),
    ], start=1):
        ws_s.write(i, 0, lbl, lbl_fmt); ws_s.write(i, 1, val, fmt)

    ws_s.write(11, 0, 'Last Purchase Year', yr_hdr)
    ws_s.write(11, 1, 'Customer Count',     yr_hdr)
    for r, (yr, grp) in enumerate(df.groupby('Last Purchase Year'), start=12):
        ws_s.write(r, 0, int(yr), yr_val); ws_s.write(r, 1, len(grp), yr_val)

print(f"\n✅ Done!")
print(f"   File    : {OUT_PATH}")
print(f"   Original: {before:,}  |  Removed: {removed:,}  |  Final: {total:,}")
