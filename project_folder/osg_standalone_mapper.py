import os
import sys
import pandas as pd
from collections import defaultdict

# Add project root to sys path
_proj_dir = os.path.dirname(os.path.abspath(__file__))
if _proj_dir not in sys.path:
    sys.path.insert(0, _proj_dir)

# Add django app root to sys path to import analytics
_django_dir = os.path.join(os.path.dirname(_proj_dir), 'myg_loyalty_dashboard')
if _django_dir not in sys.path:
    sys.path.insert(0, _django_dir)

import django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'myg_loyalty_dashboard.settings')
django.setup()

from analytics.clickhouse_service import get_ch_client
from services.osg_mapper import OSGMapper

def read_osg_excel(file_path):
    df = pd.read_excel(file_path)
    # Find the header row dynamically
    for idx, row in df.iterrows():
        row_vals = [str(x).strip().lower() for x in row.values]
        if 'invoice no' in row_vals or 'primary invoice no' in row_vals or 'invoice number' in row_vals:
            df.columns = df.iloc[idx]
            df = df.iloc[idx+1:].reset_index(drop=True)
            df = df.dropna(how='all')
            break
    return df

def run_mapper():
    data_dir = os.path.join(_proj_dir, 'OSG MAPPER DATA')
    osg_comb_file = os.path.join(data_dir, 'OSG COMBINED SEP 1-23.xlsx')
    osg_integ_file = os.path.join(data_dir, 'OSG Integration Report Sep 1-23.xlsx')
    output_file = os.path.join(data_dir, 'FINAL_MAPPED_OUTPUT_WITH_EXCHANGE_ALERT.xlsx')
    
    print(f"Reading {osg_comb_file}...")
    try:
        comb_df = read_osg_excel(osg_comb_file)
    except Exception as e:
        print(f"Error reading {osg_comb_file}: {e}")
        return
        
    print(f"Reading {osg_integ_file}...")
    try:
        final_df = read_osg_excel(osg_integ_file)
    except Exception as e:
        print(f"Error reading {osg_integ_file}: {e}")
        return
    
    # ── Phase A: Mapping ──
    print("Applying return cancellation logic on comb_df...")
    rows_to_keep = []
    if 'Customer Mobile' in comb_df.columns and 'Item Code' in comb_df.columns and 'QTY' in comb_df.columns:
        for (mobile, item_code), group in comb_df.groupby(['Customer Mobile', 'Item Code']):
            group['QTY'] = pd.to_numeric(group['QTY'], errors='coerce').fillna(0)
            positives = group[group['QTY'] > 0].index.tolist()
            negatives = group[group['QTY'] < 0].index.tolist()
            while negatives and positives:
                negatives.pop(0)
                positives.pop(0)
            rows_to_keep.extend(positives)
            rows_to_keep.extend(negatives)
        filtered_comb = comb_df.loc[rows_to_keep].copy()
    else:
        filtered_comb = comb_df.copy()
    
    ch_osg = []
    for _, r in filtered_comb.iterrows():
        inv = str(r.get('Invoice Number', '')).strip()
        branch = str(r.get('Branch', '')).strip()
        code = str(r.get('Item Code', '')).strip()
        qty = r.get('QTY', 1)
        price = r.get('Sold Price', 0)
        if inv:
            ch_osg.append((inv, branch, code, qty, price))
            
    all_invs = list({r[0] for r in ch_osg})
    print(f"Found {len(all_invs)} unique invoices in OSG COMBINED.")
    
    ch = get_ch_client()
    mapper = OSGMapper(ch)
    
    inv_info = {}
    inv_products = defaultdict(list)
    imei_by_inv = defaultdict(list)
    mobile_products = defaultdict(list)
    
    if all_invs:
        print("Fetching data from ClickHouse (azure_invoice_report, azure_sales_report, item_master)...")
        inv_sql = "'" + "','".join(i.replace("'", "''") for i in all_invs) + "'"
        
        # Fetch info
        inv_rows = ch.query(f"SELECT invoice_no, toDate(date), branch, customer_mobile FROM azure_invoice_report WHERE invoice_no IN ({inv_sql})").result_rows
        for r in inv_rows:
            inv_info[r[0].strip()] = {'date': r[1], 'branch': r[2], 'mobile': r[3]}
        
        # Fallback info from comb_df
        for _, r in filtered_comb.iterrows():
            inv = str(r.get('Invoice Number', '')).strip()
            if inv not in inv_info:
                inv_info[inv] = {
                    'date': r.get('Date', ''),
                    'branch': str(r.get('Branch', '')).strip(),
                    'mobile': str(r.get('Customer Mobile', '')).strip()
                }
                
        mobiles = list({info['mobile'] for info in inv_info.values() if info.get('mobile')})
        if mobiles:
            print("Fetching complete purchase history for OSG COMB customers...")
            # Chunking mobiles if too many
            chunk_size = 5000
            for i in range(0, len(mobiles), chunk_size):
                mob_chunk = mobiles[i:i+chunk_size]
                mob_sql = "'" + "','".join(m.replace("'", "''") for m in mob_chunk) + "'"
                cust_prod_rows = ch.query(f"SELECT i.customer_mobile, s.invoice_no, s.item_code, s.sold_price, m.item_name, m.item_category, m.brand, s.mop FROM azure_sales_report s JOIN azure_invoice_report i ON s.invoice_no = i.invoice_no LEFT JOIN item_master m ON s.item_code = m.item_code WHERE i.customer_mobile IN ({mob_sql}) AND s.item_code NOT LIKE 'OSG%' AND s.item_code NOT LIKE 'STY%' AND s.item_code NOT LIKE 'DLC%' AND s.sold_price != 0").result_rows
                for r in cust_prod_rows:
                    mobile_products[r[0].strip()].append({
                        'invoice_no': r[1].strip(),
                        'item_code': r[2], 'sold_price': float(r[3] or 0),
                        'name': str(r[4] or ''), 'category': str(r[5] or '').upper().strip(), 'brand': str(r[6] or ''),
                        'mop': float(r[7] or 0)
                    })

        # Fetch products by invoice as fallback
        print("Fetching product data by invoice...")
        prod_rows = ch.query(f"SELECT s.invoice_no, s.item_code, s.sold_price, m.item_name, m.item_category, m.brand, s.mop FROM azure_sales_report s LEFT JOIN item_master m ON s.item_code = m.item_code WHERE s.invoice_no IN ({inv_sql}) AND s.item_code NOT LIKE 'OSG%' AND s.item_code NOT LIKE 'STY%' AND s.item_code NOT LIKE 'DLC%' AND s.sold_price != 0").result_rows
        for r in prod_rows:
            inv_products[r[0].strip()].append({
                'item_code': r[1], 'sold_price': float(r[2] or 0),
                'name': str(r[3] or ''), 'category': str(r[4] or '').upper().strip(), 'brand': str(r[5] or ''),
                'mop': float(r[6] or 0)
            })
            
        # Fetch IMEIs
        print("Fetching IMEI data...")
        imei_rows = ch.query(f"SELECT s.invoice_no, s.item_code, s.imei_batch, m.item_category, s.sold_price FROM azure_sales_report s LEFT JOIN item_master m ON s.item_code = m.item_code WHERE s.invoice_no IN ({inv_sql}) AND s.item_code NOT LIKE 'OSG%' AND s.item_code NOT LIKE 'STY%' AND s.item_code NOT LIKE 'SC%' AND s.item_code NOT LIKE 'DLC%' AND isNotNull(s.imei_batch) AND s.imei_batch != '' AND s.sold_price > 0 ORDER BY s.invoice_no, s.sold_price DESC").result_rows
        for r in imei_rows:
            imei = str(r[2] or '').strip()
            if imei:
                imei_by_inv[r[0].strip()].append({
                    'item_code': r[1], 'imei': imei, 'category': str(r[3] or '').upper().strip(), 'price': float(r[4] or 0)
                })

    osg_codes = list({r[2] for r in ch_osg})
    osg_names = mapper._fetch_osg_names(osg_codes)
    
    EXACT_COLS_26 = ['Date', 'Invoice No', 'Customer', 'Store Code', 'Store Name', 'Region', 'Serial No', 'Category', 'Brand', 'Quantity', 'Model', 'Plan Type', 'EWS Qty', 'Item Rate', 'Plan Price', 'Email', 'Mobile No', 'Manufacturer Warranty', 'Duration', 'Retailer SKU', 'OnSiteGo SKU', 'Total Coverage', 'Primary Invoice No', 'Return against invoice No.', 'Return Flag', 'Comment']
    
    # Format final_df columns
    if 'Duration (Year)' in final_df.columns:
        final_df.rename(columns={'Duration (Year)': 'Duration'}, inplace=True)
    if 'OnsiteGo SKU' in final_df.columns:
        final_df.rename(columns={'OnsiteGo SKU': 'OnSiteGo SKU'}, inplace=True)
    for col in EXACT_COLS_26:
        if col not in final_df.columns:
            final_df[col] = ''
    final_df = final_df[EXACT_COLS_26]
            
    # Group Integration Report rows by Invoice
    integ_by_inv = defaultdict(list)
    for row in final_df.to_dict('records'):
        inv = str(row.get('Invoice No', '')).strip()
        if not inv or inv == 'nan':
            inv = str(row.get('Primary Invoice No', '')).strip()
        if inv and inv != 'nan':
            integ_by_inv[inv].append(row)

    print("Mapping warranties based on OSG COMBINED...")
    missing_mapped_count = 0
    all_generated_rows = []
    
    for r in ch_osg:
        inv = str(r[0]).strip()
        is_sr = '-SR-' in inv
        mobile = inv_info.get(inv, {}).get('mobile', '')
        
        exchange_warning = ""
        if mobile and mobile in mobile_products:
            sr_invoices = [p['invoice_no'] for p in mobile_products[mobile] if '-SR-' in p['invoice_no']]
            if sr_invoices:
                unique_srs = list(set(sr_invoices))
                exchange_warning = f" ⚠️ EXCHANGE ALERT: Customer returned appliance(s) on {', '.join(unique_srs)}. Verify registered IMEI is updated!"

        # If this instance is covered in the Integration Report, pull its data
        if inv in integ_by_inv and len(integ_by_inv[inv]) > 0:
            integ_row = integ_by_inv[inv].pop(0)
            row_dict = {col: integ_row.get(col, '') for col in EXACT_COLS_26}
            row_dict['Comment'] = '✅ ALREADY REGISTERED'
            if exchange_warning and 'EXCHANGE ALERT' not in str(row_dict.get('Comment', '')):
                row_dict['Comment'] = str(row_dict['Comment']) + exchange_warning
            all_generated_rows.append(row_dict)
        else:
            # It's missing from the Integration Report, so we map it!
            row_dict = mapper._make_row(inv, r, osg_names, inv_info, inv_products, imei_by_inv, mobile_products=mobile_products, is_sr=is_sr)
            if row_dict:
                missing_mapped_count += 1
                if is_sr:
                    orig_inv = str(row_dict.get('Return against invoice No.', '')).strip()
                    if orig_inv and orig_inv != inv:
                        if orig_inv in integ_by_inv:
                            row_dict['Comment'] += f' 🚨 URGENT: Original warranty {orig_inv} IS REGISTERED in OnSiteGo! MUST BE CANCELLED!'
                        else:
                            row_dict['Comment'] += f' ✅ Note: Original warranty {orig_inv} was never registered. Safe to ignore.'
                
                if exchange_warning and not is_sr and 'EXCHANGE ALERT' not in str(row_dict.get('Comment', '')):
                    row_dict['Comment'] = str(row_dict['Comment']) + exchange_warning
                    
                all_generated_rows.append(row_dict)
                
    generated_report = pd.DataFrame(all_generated_rows)
    for col in EXACT_COLS_26:
        if col not in generated_report.columns:
            generated_report[col] = ''
    generated_report = generated_report[EXACT_COLS_26]
    
    print(f"Total rows in final report: {len(generated_report)}")
    print(f"Missing mapped warranties appended: {missing_mapped_count}")
    
    # Save the output
    print(f"Saving to {output_file}...")
    generated_report.to_excel(output_file, index=False)
    print("Done!")

if __name__ == '__main__':
    run_mapper()
