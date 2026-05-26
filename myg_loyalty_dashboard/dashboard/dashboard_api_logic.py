from django.http import JsonResponse
from analytics.models import ProductSale
from django.db.models import Sum, Count
from decimal import Decimal
import pandas as pd
from django.core.cache import cache

def build_api_response(request):
    comp_type = request.GET.get('type', 'monthly')
    base_val = request.GET.get('base', '4') 
    comp_val = request.GET.get('comp', '5') 
    
    # Filters
    cat = request.GET.get('category', '')
    brand = request.GET.get('brand', '')
    branch = request.GET.get('branch', '')
    
    # Check cache first
    cache_key = f"enterprise_api_{comp_type}_{base_val}_{comp_val}_{cat}_{brand}_{branch}"
    cached_data = cache.get(cache_key)
    if cached_data:
        return cached_data

    base_qs = ProductSale.objects.all()
    comp_qs = ProductSale.objects.all()
    
    # We will filter category in Pandas after applying the custom mapping
    
    if brand:
        base_qs = base_qs.filter(brand=brand)
        comp_qs = comp_qs.filter(brand=brand)
    if branch:
        base_qs = base_qs.filter(branch=branch)
        comp_qs = comp_qs.filter(branch=branch)

    # Date ranges
    is_monthly = False
    if comp_type == 'monthly':
        base_qs = base_qs.filter(date__month=int(base_val), date__year=2026)
        comp_qs = comp_qs.filter(date__month=int(comp_val), date__year=2026)
        is_monthly = True
    elif comp_type == 'quarterly':
        def get_months(q):
            if q == 'Q1': return [1,2,3]
            if q == 'Q2': return [4,5,6]
            if q == 'Q3': return [7,8,9]
            if q == 'Q4': return [10,11,12]
            return [1,2,3]
        base_qs = base_qs.filter(date__month__in=get_months(base_val), date__year=2026)
        comp_qs = comp_qs.filter(date__month__in=get_months(comp_val), date__year=2026)
    elif comp_type == 'fy':
        base_qs = base_qs.filter(date__year=int(base_val))
        comp_qs = comp_qs.filter(date__year=int(comp_val))

    # Fetch Base and Comp distinct invoices quickly
    b_inv = base_qs.aggregate(inv=Count('invoice_number', distinct=True))['inv'] or 0
    c_inv = comp_qs.aggregate(inv=Count('invoice_number', distinct=True))['inv'] or 0

    # Fetch highly aggregated data into Pandas for instant in-memory processing
    b_data = list(base_qs.values('date', 'category', 'brand', 'product').annotate(qty=Sum('qty'), sales=Sum('sold_price')))
    c_data = list(comp_qs.values('date', 'category', 'brand', 'product').annotate(qty=Sum('qty'), sales=Sum('sold_price')))
    df_b = pd.DataFrame(b_data)
    df_c = pd.DataFrame(c_data)

    PRODUCT_CAT_MAPPING = {
        'AC OUTDOOR': 'OTHER', 'ACC BGN': 'ACC', 'ACC ZRD': 'ACC', 'AIR CONDITIONER': 'CE',
        'BT SPEAKERS': 'ACC', 'CAMERA': 'OTHER', 'CROCKERY': 'CE', 'DEMO': 'DEMO',
        'DESKTOP': 'OTHER', 'DRIER-DW-FREEZER': 'CE', 'FIXED ASSETS': 'OTHER', 'FOC': 'OTHER',
        'GDP-SPARE': 'OTHER', 'GIFT ITEMS': 'OTHER', 'HOME APPLIANCES': 'CE', 'HOME THEATRE': 'ACC',
        'HVA': 'ACC', 'IT PRODUCT': 'OTHER', 'Items': 'OTHER', 'LAPTOP': 'LAPTOP',
        'LAPTOP BAG': 'OTHER', 'LENS & BODY': 'OTHER', 'MICROWAVE OVEN': 'CE', 'MOBILE': 'MOBILE',
        'MONITOR': 'OTHER', 'MYG DOMO': 'RIG', 'PRINTER': 'RIG', 'PROJECTOR': 'RIG',
        'PROTECTION': 'OTHER', 'RECHARGE': 'OTHER', 'REFRIGERATORS': 'CE', 'RIG': 'RIG',
        'SCHEME': 'OTHER', 'SERVICE': 'OTHER', 'SIM': 'OTHER', 'SIM INVENTORY': 'OTHER',
        'SMALL APPLIANCES': 'CE', 'SMART WATCH': 'ACC', 'SPARE': 'SPARE', 'STANDBY': 'OTHER',
        'STATIONERY ITEMS': 'OTHER', 'TABLET': 'TABLET', 'TOTAL LOSS': 'OTHER', 'TV': 'CE',
        'WASHING MACHINES': 'CE', 'WATCH': 'OTHER', 'P&G': 'ACC', 'IT ACCESSORIES': 'ACC',
        'CARE PLUS': 'OTHER', 'SERVICE CHARGES': 'OTHER', 'DTH': 'OTHER', 'STABILIZER': 'CE',
        'SCRAP': 'OTHER', 'D SPARE': 'SPARE', 'C SPARE': 'SPARE', 'MYG VERSE': 'OTHER',
        'DEMO LAPTOP': 'DEMO', 'EARBUDS': 'ACC', 'RECHARGE INVENTORY': 'OTHER', 'CEGI': 'OTHER',
        'STELLR VOUCHERS': 'RIG', 'DUMMY': 'OTHER', 'DISH WASHER': 'CE', 'DRYER': 'CE',
        'FREEZER': 'CE', 'WET GRINDER': 'CE', 'DEMO ACCESSORIES': 'DEMO', 
        'OSG WARRANTY': 'VALUE ADDED SERVICE', 'CARE PLUS': 'VALUE ADDED SERVICE',
        'LG AMC': 'VALUE ADDED SERVICE', 'PROTECT MAX': 'VALUE ADDED SERVICE',
        'PERFUME': 'ACC', 'CONTRACT WORK': 'OTHER', 'DEMO HA': 'DEMO', 'DIY': 'DEMO',
        'MOBILE ANTIVIRUS': 'OTHER', 'HA ACCESSORIES': 'CE',
        'HOUSE HOLD': 'CE', 'TOTAL SECURITY': 'OTHER', 'KASPERSKY': 'OTHER', 'QUICKHEAL': 'OTHER',
        'CCTV': 'OTHER', 'DETERGENT': 'OTHER', 'ACCESSORIES': 'ACC', 'AUDIO': 'ACC',
        'EAR WEARABLES': 'ACC', 'FRAGRANCE': 'ACC', 'GAMING': 'ACC', 'GLAMSHIELD': 'ACC',
        'OFFER KIT': 'ACC', 'PERSONAL CARE': 'ACC', 'PRINTER ACCESSORIES': 'ACC',
        'STORAGE DEVICES': 'ACC'
    }

    if df_b.empty:
        df_b = pd.DataFrame(columns=['date', 'category', 'brand', 'qty', 'sales', 'product'])
    else:
        df_b['category'] = df_b['product'].map(PRODUCT_CAT_MAPPING).fillna(df_b['category'])
        df_b = df_b[~df_b['category'].isin(['SPARE', 'DEMO', 'OTHERS'])]
        if cat:
            df_b = df_b[df_b['category'] == cat]
        df_b['sales'] = df_b['sales'].astype(float)
        df_b['qty'] = df_b['qty'].astype(int)
        
    if df_c.empty:
        df_c = pd.DataFrame(columns=['date', 'category', 'brand', 'qty', 'sales', 'product'])
    else:
        df_c['category'] = df_c['product'].map(PRODUCT_CAT_MAPPING).fillna(df_c['category'])
        df_c = df_c[~df_c['category'].isin(['SPARE', 'DEMO', 'OTHERS'])]
        if cat:
            df_c = df_c[df_c['category'] == cat]
        df_c['sales'] = df_c['sales'].astype(float)
        df_c['qty'] = df_c['qty'].astype(int)

    # 1. Compute KPIs
    b_sales = float(df_b['sales'].sum())
    c_sales = float(df_c['sales'].sum())
    b_qty = int(df_b['qty'].sum())
    c_qty = int(df_c['qty'].sum())
    
    b_vas = float(df_b[df_b['category'] == 'VALUE ADDED SERVICE']['sales'].sum())
    c_vas = float(df_c[df_c['category'] == 'VALUE ADDED SERVICE']['sales'].sum())
    
    sales_growth = ((c_sales - b_sales) / b_sales * 100) if b_sales > 0 else 0
    qty_growth = ((c_qty - b_qty) / b_qty * 100) if b_qty > 0 else 0
    vas_growth = ((c_vas - b_vas) / b_vas * 100) if b_vas > 0 else 0
    
    b_asp = b_sales / b_qty if b_qty > 0 else 0
    c_asp = c_sales / c_qty if c_qty > 0 else 0
    asp_growth = ((c_asp - b_asp) / b_asp * 100) if b_asp > 0 else 0
    
    kpis = {
        "base": {
            "sales": b_sales,
            "qty": b_qty,
            "vas": b_vas,
            "asp": b_asp,
            "inv": b_inv
        },
        "comp": {
            "sales": c_sales,
            "qty": c_qty,
            "vas": c_vas,
            "asp": c_asp,
            "inv": c_inv
        },
        "growth": {
            "sales": sales_growth,
            "qty": qty_growth,
            "vas": vas_growth,
            "asp": asp_growth
        }
    }
    
    # 2. Compute Trends (Group by Day or Month)
    if is_monthly:
        df_b['t'] = pd.to_datetime(df_b['date']).dt.day if not df_b.empty else []
        df_c['t'] = pd.to_datetime(df_c['date']).dt.day if not df_c.empty else []
    else:
        df_b['t'] = pd.to_datetime(df_b['date']).dt.month if not df_b.empty else []
        df_c['t'] = pd.to_datetime(df_c['date']).dt.month if not df_c.empty else []
        
    trends = {"base": [], "comp": []}
    if not df_b.empty:
        trends['base'] = df_b.groupby('t')[['sales', 'qty']].sum().reset_index().to_dict('records')
    if not df_c.empty:
        trends['comp'] = df_c.groupby('t')[['sales', 'qty']].sum().reset_index().to_dict('records')

    # 3. Compute Categories
    cats = []
    cat_base_dict = {}
    if not df_b.empty:
        cat_base_dict = df_b.groupby('category')['sales'].sum().to_dict()
        
    if not df_c.empty:
        c_cat_df = df_c.groupby('category')[['sales', 'qty']].sum().reset_index().sort_values('sales', ascending=False)
        cats = c_cat_df.to_dict('records')
    
    # 4. Compute Brands
    brand_cats = ['MOBILE', 'LAPTOP', 'TABLET', 'ACC', 'CE', 'TV', 'REFRIGERATORS', 'WASHING MACHINES', 'AIR CONDITIONER']
    brands = {}
    if not df_c.empty:
        for b_cat in brand_cats:
            b_df = df_c[df_c['category'] == b_cat]
            if not b_df.empty:
                brands[b_cat] = b_df.groupby('brand')[['sales', 'qty']].sum().reset_index().sort_values('sales', ascending=False).head(15).to_dict('records')

    # 5. Compute Table
    tbl = []
    if not df_b.empty or not df_c.empty:
        b_tbl = df_b.groupby(['category', 'brand'])[['sales', 'qty']].sum().reset_index().rename(columns={'sales': 'b_rev', 'qty': 'b_qty'}) if not df_b.empty else pd.DataFrame(columns=['category', 'brand', 'b_rev', 'b_qty'])
        c_tbl = df_c.groupby(['category', 'brand'])[['sales', 'qty']].sum().reset_index().rename(columns={'sales': 'c_rev', 'qty': 'c_qty'}) if not df_c.empty else pd.DataFrame(columns=['category', 'brand', 'c_rev', 'c_qty'])
        
        merged = pd.merge(b_tbl, c_tbl, on=['category', 'brand'], how='outer').fillna(0)
        merged['v_rev'] = merged['c_rev'] - merged['b_rev']
        tbl = merged.to_dict('records')

    # 6. Compute Category Scorecard
    scorecard = []
    if not df_b.empty or not df_c.empty:
        b_cat_tbl = df_b.groupby('category')[['sales', 'qty']].sum().reset_index().rename(columns={'sales': 'b_rev', 'qty': 'b_qty'}) if not df_b.empty else pd.DataFrame(columns=['category', 'b_rev', 'b_qty'])
        c_cat_tbl = df_c.groupby('category')[['sales', 'qty']].sum().reset_index().rename(columns={'sales': 'c_rev', 'qty': 'c_qty'}) if not df_c.empty else pd.DataFrame(columns=['category', 'c_rev', 'c_qty'])
        
        merged_cat = pd.merge(b_cat_tbl, c_cat_tbl, on='category', how='outer').fillna(0)
        merged_cat['rev_growth'] = merged_cat.apply(lambda row: ((row['c_rev'] - row['b_rev']) / row['b_rev'] * 100) if row['b_rev'] > 0 else 0, axis=1)
        merged_cat['qty_growth'] = merged_cat.apply(lambda row: ((row['c_qty'] - row['b_qty']) / row['b_qty'] * 100) if row['b_qty'] > 0 else 0, axis=1)
        
        total_c_rev = merged_cat['c_rev'].sum()
        merged_cat['share'] = merged_cat.apply(lambda row: (row['c_rev'] / total_c_rev * 100) if total_c_rev > 0 else 0, axis=1)
        
        merged_cat['b_asp'] = merged_cat.apply(lambda row: (row['b_rev'] / row['b_qty']) if row['b_qty'] > 0 else 0, axis=1)
        merged_cat['c_asp'] = merged_cat.apply(lambda row: (row['c_rev'] / row['c_qty']) if row['c_qty'] > 0 else 0, axis=1)
        
        # Add grand total row
        if not merged_cat.empty:
            grand_total = {
                'category': 'Grand Total',
                'b_rev': float(merged_cat['b_rev'].sum()),
                'c_rev': float(total_c_rev),
                'b_qty': int(merged_cat['b_qty'].sum()),
                'c_qty': int(merged_cat['c_qty'].sum()),
            }
            grand_total['rev_growth'] = ((grand_total['c_rev'] - grand_total['b_rev']) / grand_total['b_rev'] * 100) if grand_total['b_rev'] > 0 else 0
            grand_total['qty_growth'] = ((grand_total['c_qty'] - grand_total['b_qty']) / grand_total['b_qty'] * 100) if grand_total['b_qty'] > 0 else 0
            grand_total['share'] = 100.0
            grand_total['b_asp'] = (grand_total['b_rev'] / grand_total['b_qty']) if grand_total['b_qty'] > 0 else 0
            grand_total['c_asp'] = (grand_total['c_rev'] / grand_total['c_qty']) if grand_total['c_qty'] > 0 else 0
            
            # Sort main categories
            merged_cat = merged_cat.sort_values('c_rev', ascending=False)
            scorecard = merged_cat.to_dict('records')
            scorecard.append(grand_total)

    final_response = {
        "kpis": kpis,
        "trends": trends,
        "categories": cats,
        "categories_base_dict": cat_base_dict,
        "brands": brands,
        "table": tbl,
        "scorecard": scorecard
    }
    
    # Generate detailed table for precise Excel exporting (keeps product level granularity)
    if not df_b.empty or not df_c.empty:
        b_dtbl = df_b.groupby(['category', 'product', 'brand'])[['sales', 'qty']].sum().reset_index().rename(columns={'sales': 'b_rev', 'qty': 'b_qty'}) if not df_b.empty else pd.DataFrame(columns=['category', 'product', 'brand', 'b_rev', 'b_qty'])
        c_dtbl = df_c.groupby(['category', 'product', 'brand'])[['sales', 'qty']].sum().reset_index().rename(columns={'sales': 'c_rev', 'qty': 'c_qty'}) if not df_c.empty else pd.DataFrame(columns=['category', 'product', 'brand', 'c_rev', 'c_qty'])
        merged_dtbl = pd.merge(b_dtbl, c_dtbl, on=['category', 'product', 'brand'], how='outer').fillna(0)
        final_response["detailed_table"] = merged_dtbl.to_dict('records')
    else:
        final_response["detailed_table"] = []
    
    # Cache for 1 hour
    cache.set(cache_key, final_response, timeout=3600)
    
    return final_response

import io
import pandas as pd
import numpy as np
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter

# --- EXCEL STYLING CONSTANTS ---
THIN       = Side(style="thin",   color="000000")
THIN_BDR   = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)
HDR_FONT   = Font(name="Arial", bold=True,  size=9)
BODY_FONT  = Font(name="Arial", bold=False, size=9)
TOT_FONT   = Font(name="Arial", bold=True,  size=9)
NEG_FILL   = PatternFill("solid", fgColor="FFC7CE")
TOT_FILL   = PatternFill("solid", fgColor="BDD7EE")
WHITE_FILL = PatternFill("solid", fgColor="FFFFFF")
CENTER = Alignment(horizontal="center", vertical="center", wrap_text=True)
LEFT   = Alignment(horizontal="left",   vertical="center")
RIGHT  = Alignment(horizontal="right",  vertical="center")
COL_WIDTHS = [24, 12, 12, 11, 11, 2, 12, 12, 11, 11]

def _c(ws, row, col, value=None, font=None, fill=None, align=None, border=None, fmt=None):
    cell = ws.cell(row=row, column=col, value=value)
    if font:   cell.font   = font
    if fill:   cell.fill   = fill
    if align:  cell.alignment = align
    if border: cell.border = border
    if fmt:    cell.number_format = fmt
    return cell

def write_header(ws, row, label_name, b_val, c_val, sc=1):
    def hdr(r, c, v, colspan=1, rowspan=1):
        if colspan > 1 or rowspan > 1:
            ws.merge_cells(start_row=r, start_column=c, end_row=r + rowspan - 1, end_column=c + colspan - 1)
        cell = ws.cell(row=r, column=c, value=v)
        cell.font   = HDR_FONT
        cell.fill   = WHITE_FILL
        cell.border = THIN_BDR
        cell.alignment = CENTER
        return cell
    hdr(row,   sc,    label_name,   rowspan=2)
    hdr(row,   sc+1,  b_val)
    hdr(row,   sc+2,  c_val)
    hdr(row,   sc+3,  "GROWTH %",   rowspan=2)
    hdr(row,   sc+4,  f"SHARE {c_val}")
    ws.cell(row=row,   column=sc+5).border = Border()
    ws.cell(row=row+1, column=sc+5).border = Border()
    hdr(row,   sc+6,  b_val)
    hdr(row,   sc+7,  c_val)
    hdr(row,   sc+8,  "GROWTH %",   rowspan=2)
    hdr(row,   sc+9,  f"SHARE {c_val}")
    hdr(row+1, sc+1, "AMT")
    hdr(row+1, sc+2, "AMT")
    hdr(row+1, sc+4, "AMT")
    hdr(row+1, sc+6, "QTY")
    hdr(row+1, sc+7, "QTY")
    hdr(row+1, sc+9, "QTY")
    return row + 2

def write_rows(ws, df, label_col, row, sc=1):
    for _, r in df.iterrows():
        is_tot = str(r[label_col]).strip().lower() == "grand total"
        fill  = TOT_FILL  if is_tot else WHITE_FILL
        font  = TOT_FONT  if is_tot else BODY_FONT
        def put(col, val, fmt=None, growth=False):
            v = None if (isinstance(val, float) and pd.isna(val)) else val
            cf = _c(ws, row, col, v, font=font, fill=fill, align=RIGHT if col > sc else LEFT, border=THIN_BDR, fmt=fmt)
            if growth and v is not None and not is_tot:
                cf.fill = NEG_FILL if v < 0 else WHITE_FILL
            return cf
        put(sc,    r[label_col])
        put(sc+1,  r["APR_AMT"], '#,##0.00')
        put(sc+2,  r["MAY_AMT"], '#,##0.00')
        put(sc+3,  r["AMT_GRW"], '0.00"%"', growth=True)
        put(sc+4,  r["AMT_SHR"], '0.00"%"')
        ws.cell(row=row, column=sc+5)
        put(sc+6,  r["APR_QTY"], '#,##0')
        put(sc+7,  r["MAY_QTY"], '#,##0')
        put(sc+8,  r["QTY_GRW"], '0.00"%"', growth=True)
        put(sc+9,  r["QTY_SHR"], '0.00"%"')
        row += 1
    return row

def write_table(ws, df, label_col, section_label, b_val, c_val, start_row=1, sc=1):
    row = write_header(ws, start_row, section_label, b_val, c_val, sc)
    row = write_rows(ws, df, label_col, row, sc)
    for i, w in enumerate(COL_WIDTHS, start=sc):
        ws.column_dimensions[get_column_letter(i)].width = w
    ws.freeze_panes = ws.cell(row=start_row + 2, column=sc + 1)
    return row + 1

def generate_dashboard_excel(request):
    try:
        # Utilize the exact same API engine so metrics perfectly match the UI
        data = build_api_response(request)
    except Exception as e:
        print(f"Error fetching data for excel: {e}")
        data = {}

    excel_io = io.BytesIO()
    
    base_val = request.GET.get('base', 'Base').upper()
    comp_val = request.GET.get('comp', 'Comp').upper()
    
    # Friendly names for months if monthly
    if request.GET.get('type', 'monthly') == 'monthly':
        month_names = ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"]
        try:
            base_val = month_names[int(base_val)-1]
            comp_val = month_names[int(comp_val)-1]
        except:
            pass

    with pd.ExcelWriter(excel_io, engine='openpyxl') as writer:
        has_sheets = False
        
        # We'll use the writer's workbook directly since openpyxl engine allows it
        wb = writer.book
        
        def format_df(df, label_col):
            # Formats any raw dataframe into the strictly expected column names
            res = pd.DataFrame()
            res[label_col] = df[label_col]
            res['APR_AMT'] = (df['b_rev'] / 10000000).round(2)
            res['MAY_AMT'] = (df['c_rev'] / 10000000).round(2)
            growth = (df['c_rev'] - df['b_rev']) / df['b_rev'] * 100
            res['AMT_GRW'] = growth.replace([float('inf'), -float('inf')], 0).fillna(0)
            
            tot_rev = df['c_rev'].sum()
            tot_rev = df[df[label_col] == 'Grand Total']['c_rev'].values[0] if 'Grand Total' in df[label_col].values else tot_rev
            res['AMT_SHR'] = ((df['c_rev'] / tot_rev) * 100).fillna(0) if tot_rev > 0 else 0
            
            res['APR_QTY'] = df['b_qty']
            res['MAY_QTY'] = df['c_qty']
            qty_growth = (df['c_qty'] - df['b_qty']) / df['b_qty'] * 100
            res['QTY_GRW'] = qty_growth.replace([float('inf'), -float('inf')], 0).fillna(0)
            
            tot_qty = df['c_qty'].sum()
            tot_qty = df[df[label_col] == 'Grand Total']['c_qty'].values[0] if 'Grand Total' in df[label_col].values else tot_qty
            res['QTY_SHR'] = ((df['c_qty'] / tot_qty) * 100).fillna(0) if tot_qty > 0 else 0
            return res

        if 'scorecard' in data and data['scorecard']:
            sc_df = pd.DataFrame(data['scorecard'])
            if not sc_df.empty:
                sc_df = format_df(sc_df, 'category')
                ws = wb.create_sheet('1_Category Summary')
                write_table(ws, sc_df, 'category', 'PDT CAT', base_val, comp_val)
                has_sheets = True
                
        if 'detailed_table' in data and data['detailed_table']:
            dt_df = pd.DataFrame(data['detailed_table'])
            if not dt_df.empty:
                def make_sheet(df, group_col, sheet_name, entity_name):
                    if df.empty: return False
                    grp = df.groupby(group_col)[['b_rev', 'b_qty', 'c_rev', 'c_qty']].sum().reset_index()
                    grp = grp.sort_values('c_rev', ascending=False)
                    
                    # Add Grand Total row manually
                    gt = {group_col: 'Grand Total', 'b_rev': grp['b_rev'].sum(), 'b_qty': grp['b_qty'].sum(), 'c_rev': grp['c_rev'].sum(), 'c_qty': grp['c_qty'].sum()}
                    grp = pd.concat([grp, pd.DataFrame([gt])], ignore_index=True)
                    
                    fmt_grp = format_df(grp, group_col)
                    ws = wb.create_sheet(sheet_name[:31])
                    write_table(ws, fmt_grp, group_col, entity_name, base_val, comp_val)
                    return True

                has_sheets |= make_sheet(dt_df[dt_df['category'] == 'MOBILE'], 'brand', '2_Mobile Brand', 'MOBILE')
                has_sheets |= make_sheet(dt_df[dt_df['category'] == 'LAPTOP'], 'brand', '3_Laptop Brand', 'LAPTOP')
                has_sheets |= make_sheet(dt_df[dt_df['category'] == 'ACC'], 'brand', '4_ACC Brand', 'ACC')
                has_sheets |= make_sheet(dt_df[dt_df['category'] == 'ACC'], 'product', '5_ACC Category', 'ACC')
                has_sheets |= make_sheet(dt_df[dt_df['category'] == 'CE'], 'product', '6_CE Category', 'CE')
                has_sheets |= make_sheet(dt_df[dt_df['category'] == 'CE'], 'brand', '7_CE Brand', 'CE')
                has_sheets |= make_sheet(dt_df[dt_df['product'] == 'TV'], 'brand', '8_TV Brand', 'Tv')
                has_sheets |= make_sheet(dt_df[dt_df['product'] == 'REFRIGERATORS'], 'brand', '9_Fridge Brand', 'Refrigerators')
                has_sheets |= make_sheet(dt_df[dt_df['product'] == 'WASHING MACHINES'], 'brand', '10_WM Brand', 'Washing Machines')
                has_sheets |= make_sheet(dt_df[dt_df['product'] == 'AIR CONDITIONER'], 'brand', '11_AC Brand', 'Air Conditioner')
                has_sheets |= make_sheet(dt_df[dt_df['category'] == 'VALUE ADDED SERVICE'], 'product', '12_VAS Summary', 'VAS')
        
        if not has_sheets:
            wb.create_sheet('Empty Report')
        
        # Remove default 'Sheet' if it exists
        if 'Sheet' in wb.sheetnames:
            wb.remove(wb['Sheet'])

    excel_io.seek(0)
    filename = request.GET.get('base', 'Base') + "_vs_" + request.GET.get('comp', 'Comp')
    return excel_io, f"Monthly_Analysis_{filename}.xlsx"
