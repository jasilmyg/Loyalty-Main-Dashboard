"""
OSG Integration Mapper Service
Compares an uploaded OnSiteGo integration report with ClickHouse data
and generates a complete reconciled report in the exact OSG format.
"""

import os
import io
import re
from datetime import datetime
from collections import defaultdict

import openpyxl
from openpyxl.styles import PatternFill, Font, Alignment, Border, Side

# ── Category file path ────────────────────────────────────────────────────────
CATEGORIES_XLSX = os.path.join(
    os.path.dirname(__file__), '..', 'osg eligible item categories.xlsx'
)

# ── Exact OSG integration report columns ─────────────────────────────────────
# Exact 28-column order matching OSG_Integration_Complete_Sep14_123.xlsx
EXACT_COLS = [
    'Date', 'Invoice No', 'Customer', 'Store Code', 'Store Name', 'Region',
    'Serial No', 'Category', 'Brand', 'Quantity', 'Model', 'Plan Type',
    'EWS Qty', 'Item Rate', 'Plan Price', 'Email', 'Mobile No',
    'Manufacturer Warranty', 'Retailer SKU', 'OnsiteGo SKU',
    'Duration (Year)', 'Total Coverage',
    'Comment', 'Return Flag', 'Return against invoice No.',
    'Primary Invoice No', 'OSG Code',
]

SKIP_C = {
    'CARRY BAG', 'CARRY BAG 5%', 'STATIONERY', 'SMART CHOICE', 'BLANKET',
    'CROCKERY', 'GLASSWARE', 'DELIVERY CHARGES', 'SCRATCH CARD', 'PAPER BAG',
    'SRVCHRG', 'GIFT ITEMS', 'PILLOW', 'COOKWARE', 'TAWA',
    'PRESSURE COOKER', 'APPACHATTY',
}

# ── Retail branch filter (exclude warehouse/godown/HO) ───────────────────────
RETAIL_BRANCH_FILTER = """
    AND branch NOT IN (
        SELECT code FROM branch_master
        WHERE UPPER(branch_type) IN ('WAREHOUSE','GODOWN','HEAD OFFICE','HO')
    )
"""


# ─────────────────────────────────────────────────────────────────────────────
class OSGMapper:
    def __init__(self, ch_client):
        self.ch = ch_client
        self._load_categories()
        self._load_branch_map()
        self._load_customer_names()

    # ── One-time loads ────────────────────────────────────────────────────────
    def _load_categories(self):
        """Load plan→category mapping from official OSG categories Excel."""
        self.osg_plan_cats = {}
        current_plan = None
        try:
            wb = openpyxl.load_workbook(CATEGORIES_XLSX)
            ws = wb.active
            for row in ws.iter_rows(min_row=1, values_only=True):
                val = str(row[0] or '').strip()
                if not val:
                    continue
                if 'Warranty' in val or val in ('AC Warranty', 'TV Warranty'):
                    current_plan = val
                    self.osg_plan_cats[current_plan] = set()
                elif current_plan:
                    self.osg_plan_cats[current_plan].add(val.upper().strip())
        except Exception as e:
            pass  # will use fallback sets

        def _merge(*keys):
            result = set()
            for k in keys:
                for plan, cats in self.osg_plan_cats.items():
                    if k.upper() in plan.upper():
                        result |= cats
            return result

        self.ALL_HAEW_C  = _merge('HAEW')
        self.RWSA_C      = _merge('RWSA')
        self.ACEW_C      = _merge('AC Warranty') or {'AC', 'AC INDOOR', 'AC OUTDOOR'}
        self.TTC_C       = _merge('TV Warranty')  or {'TV', 'TV 18 %'}
        self.HAEW_REF_C  = _merge('Ref/WM')
        self.HAEW_CHOP_C = _merge('Chop/Blend', 'Chopper')
        self.HAEW_HOB_C  = _merge('HOB')
        self.HAEW_AUD_C  = _merge('HT/Sound', 'Audio')
        self.HAEW_VAC_C  = _merge('Vacuum', 'Groom')
        self.HAEW_DRY_C  = _merge('Dryer/MW', 'DishW')
        self.HAEW_WAT_C  = _merge('Water Cooler', 'Geyser')
        self.HAEW_PUR_C  = _merge('Air Purifier', 'WaterPurifier', 'Purifier')

    def _load_branch_map(self):
        rows = self.ch.query("SELECT code, branch_name FROM branch_master WHERE code != ''").result_rows
        self.branch_map = {r[0].strip().upper(): r[1].strip() for r in rows}

    def _load_customer_names(self):
        rows = self.ch.query("""
            SELECT user_phone, concat(trim(firstname), ' ', trim(lastname))
            FROM loyalty_user_data WHERE user_phone != '' AND firstname != ''
        """).result_rows
        self.cust_name_map = {}
        for phone_raw, name in rows:
            phone = (phone_raw or '').strip()
            name  = (name or '').strip()
            if not phone or not name:
                continue
            # Store by full number
            self.cust_name_map[phone] = name
            # Also index last 10 digits (handles +91XXXXXXXXXX stored numbers)
            if len(phone) > 10:
                self.cust_name_map[phone[-10:]] = name

    # ── Category eligibility ──────────────────────────────────────────────────
    def elig(self, osg_name):
        oc = osg_name.upper()
        if 'ACEW'    in oc or 'AC ' in oc or ' AC' in oc: return self.ACEW_C
        if 'TTC'     in oc or 'TV ' in oc or ' TV' in oc or 'TV(' in oc: return self.TTC_C
        if 'RWSA'    in oc: return self.RWSA_C
        if 'REF/WM'  in oc or 'REF'     in oc: return self.HAEW_REF_C
        if 'CHOP'    in oc or 'BLEND'   in oc: return self.HAEW_CHOP_C
        if 'HOB'     in oc or 'CHIMNEY' in oc: return self.HAEW_HOB_C
        if 'HT'      in oc or 'SOUND'   in oc: return self.HAEW_AUD_C
        if 'VACUUM'  in oc or 'GROOM'   in oc: return self.HAEW_VAC_C
        if 'DRYER'   in oc or 'MW'      in oc: return self.HAEW_DRY_C
        if 'WATER'   in oc or 'GEYSER'  in oc: return self.HAEW_WAT_C
        if 'PURIFIER'in oc or 'PURIF'   in oc: return self.HAEW_PUR_C
        return self.ALL_HAEW_C

    # ── Slab parsing ──────────────────────────────────────────────────────────
    @staticmethod
    def parse_slab(osg_name):
        if 'Slab :' not in osg_name:
            return 0, 999999
        slab_str = osg_name.split('Slab :')[-1].strip().split('|')[0].strip().split(')')[0].strip()
        try:
            parts = slab_str.replace('K', '000').split('-')
            lo = int(parts[0].strip())
            hi = int(parts[1].strip()) if len(parts) > 1 else 999999
            return lo, hi
        except Exception:
            return 0, 999999

    @staticmethod
    def parse_dur(osg_name):
        if 'Dur :' not in osg_name:
            return '', '1'
        dur_str = osg_name.split('Dur :')[-1].strip().split(')')[0].strip()
        dur_ext = dur_str.split('+')[-1].strip() if '+' in dur_str else dur_str
        mfr_war = dur_str.split('+')[0].strip() if '+' in dur_str else '1'
        return dur_ext, mfr_war

    # ── Main processing entry point ───────────────────────────────────────────
    def process(self, integ_file_bytes, report_date_str):
        """
        Args:
            integ_file_bytes: bytes of the uploaded integration report Excel
            report_date_str:  'YYYY-MM-DD' string for the sale date to analyse
        Returns:
            dict with keys: summary, activate_rows, sr_rows, registered_rows, all_rows
        """
        report_date = report_date_str  # e.g. '2026-09-14'

        # 1. Load integration report
        integ_data, integ_invs = self._load_integ_report(integ_file_bytes)

        # 2. Fetch CH data for the given date
        ch_osg, inv_info, inv_products, imei_by_inv = self._fetch_ch_data(
            report_date, list(integ_invs)
        )

        # 3. Fetch OSG item names
        osg_codes  = list({r[2].strip() for r in ch_osg})
        osg_names  = self._fetch_osg_names(osg_codes)

        # 4. Classify invoices
        ch_invs      = {r[0].strip() for r in ch_osg}
        sr_invs      = {i for i in ch_invs if re.match(r'.*-SR-.*', i)}
        missing_invs = ch_invs - integ_invs - sr_invs

        # 5. Group OSG rows by invoice
        missing_osg_by_inv = defaultdict(list)
        sr_osg_by_inv      = defaultdict(list)
        for r in ch_osg:
            inv = r[0].strip()
            if inv in missing_invs:
                missing_osg_by_inv[inv].append(r)
            elif inv in sr_invs:
                sr_osg_by_inv[inv].append(r)

        # 6. Build rows
        activate_rows = []
        for inv_no in sorted(missing_invs):
            for osg_r in missing_osg_by_inv[inv_no]:
                row = self._make_row(
                    inv_no, osg_r, osg_names, inv_info, inv_products,
                    imei_by_inv, is_sr=False
                )
                if row:
                    activate_rows.append(row)

        sr_rows = []
        for inv_no in sorted(sr_invs):
            for osg_r in sr_osg_by_inv.get(inv_no, []):
                row = self._make_row(
                    inv_no, osg_r, osg_names, inv_info, inv_products,
                    imei_by_inv, is_sr=True
                )
                if row:
                    sr_rows.append(row)

        registered_rows = []
        for r in integ_data:
            row = {col: r.get(col, '') or '' for col in EXACT_COLS}
            row['_status'] = 'REGISTERED'
            row['_osg_code'] = ''
            # Fill empty comments for Returned rows
            inv_no   = str(row.get('Invoice No', '')).strip()
            ret_flag = str(row.get('Return Flag', '')).strip()
            if ret_flag == 'Returned' and not str(row.get('Comment', '')).strip():
                row['Comment'] = (
                    f'WARRANTY RETURNED — Invoice {inv_no} marked as Returned in OnSiteGo. '
                    f'Return against: {row.get("Return against invoice No.", "")}. '
                    f'Verify cancellation status with OnSiteGo.'
                )
            registered_rows.append(row)

        all_rows = activate_rows + sr_rows + registered_rows

        summary = {
            'date': report_date,
            'total':       len(all_rows),
            'activate':    len(activate_rows),
            'sr_returns':  len(sr_rows),
            'registered':  len(registered_rows),
            'ch_total':    len(ch_invs),
        }

        return {
            'summary': summary,
            'activate_rows':    activate_rows,
            'sr_rows':          sr_rows,
            'registered_rows':  registered_rows,
            'all_rows':         all_rows,
        }

    # ── CH data fetchers ──────────────────────────────────────────────────────
    def _load_integ_report(self, file_bytes):
        """Load the uploaded OSG integration report.
        The file may have merged header cells and multi-row headers — handle safely."""
        wb = openpyxl.load_workbook(io.BytesIO(file_bytes), data_only=True)
        ws = wb.active

        def safe_val(cell):
            """Return cell value safely even for MergedCell objects."""
            try:
                return cell.value
            except AttributeError:
                return None

        # Scan rows 1-8 to find the actual header row (contains 'Invoice No')
        hdr_row_idx = 1
        for r_idx in range(1, 9):
            row_vals = [safe_val(ws.cell(r_idx, c)) for c in range(1, ws.max_column + 1)]
            if any('Invoice No' in str(v or '') for v in row_vals):
                hdr_row_idx = r_idx
                break

        hdrs = [safe_val(ws.cell(hdr_row_idx, c)) for c in range(1, ws.max_column + 1)]

        data = []
        for row in ws.iter_rows(min_row=hdr_row_idx + 1, values_only=False):
            row_vals = [safe_val(cell) for cell in row]
            if any(v for v in row_vals):
                data.append(dict(zip(hdrs, row_vals)))

        invs = {str(r.get('Invoice No') or '').strip() for r in data if r.get('Invoice No')}
        return data, invs


    def _fetch_ch_data(self, report_date, integ_inv_list):
        # OSG items for the date
        ch_osg = self.ch.query(f"""
            SELECT invoice_no, branch, item_code, qty, sold_price
            FROM azure_sales_report
            WHERE item_code LIKE 'OSG%'
              AND toDate(date) = toDate('{report_date}')
              AND invoice_no NOT LIKE '%SMC%'
              AND invoice_no NOT LIKE '%EI%'
        """).result_rows

        all_invs = list({r[0].strip() for r in ch_osg})
        if not all_invs:
            return ch_osg, {}, {}, {}

        inv_sql = "'" + "','".join(i.replace("'", "''") for i in all_invs) + "'"

        # Invoice-level info (branch, mobile, date)
        inv_rows = self.ch.query(f"""
            SELECT invoice_no, toDate(date), branch, customer_mobile
            FROM azure_invoice_report
            WHERE invoice_no IN ({inv_sql})
        """).result_rows
        inv_info = {
            r[0].strip(): {'date': r[1], 'branch': r[2], 'mobile': r[3]}
            for r in inv_rows
        }

        # Product items
        prod_rows = self.ch.query(f"""
            SELECT s.invoice_no, s.item_code, s.sold_price, m.item_name,
                   m.item_category, m.brand
            FROM azure_sales_report s
            LEFT JOIN item_master m ON s.item_code = m.item_code
            WHERE s.invoice_no IN ({inv_sql})
              AND s.item_code NOT LIKE 'OSG%'
              AND s.item_code NOT LIKE 'STY%'
              AND s.item_code NOT LIKE 'DLC%'
              AND s.sold_price != 0
        """).result_rows

        inv_products = defaultdict(list)
        for r in prod_rows:
            inv_products[r[0].strip()].append({
                'item_code': r[1],
                'sold_price': float(r[2] or 0),
                'name': str(r[3] or ''),
                'category': str(r[4] or '').upper().strip(),
                'brand': str(r[5] or ''),
            })

        # IMEI / Serial numbers
        # Exclude: OSG, STY (scratch), SC (Smart Choice fake serials),
        #          DLC (delivery), negative-price returns, and NULL IMEI
        imei_rows = self.ch.query(f"""
            SELECT s.invoice_no, s.item_code, s.imei_batch, m.item_category, s.sold_price
            FROM azure_sales_report s
            LEFT JOIN item_master m ON s.item_code = m.item_code
            WHERE s.invoice_no IN ({inv_sql})
              AND s.item_code NOT LIKE 'OSG%'
              AND s.item_code NOT LIKE 'STY%'
              AND s.item_code NOT LIKE 'SC%'
              AND s.item_code NOT LIKE 'DLC%'
              AND isNotNull(s.imei_batch)
              AND s.imei_batch != ''
              AND s.sold_price > 0
            ORDER BY s.invoice_no, s.sold_price DESC
        """).result_rows

        imei_by_inv = defaultdict(list)
        for r in imei_rows:
            imei = (r[2] or '').strip()
            if not imei:
                continue
            imei_by_inv[r[0].strip()].append({
                'item_code': r[1],
                'imei': imei,
                'category': (r[3] or '').upper().strip(),
                'price': float(r[4] or 0),
            })

        return ch_osg, inv_info, inv_products, imei_by_inv

    def _fetch_osg_names(self, codes):
        if not codes:
            return {}
        sql = "'" + "','".join(c.replace("'", "''") for c in codes) + "'"
        rows = self.ch.query(f"SELECT item_code, item_name FROM item_master WHERE item_code IN ({sql})").result_rows
        return {r[0].strip(): r[1].strip() for r in rows}

    # ── IMEI lookup ───────────────────────────────────────────────────────────
    def _get_imei(self, imei_by_inv, inv_no, cat, matched_code=''):
        """Return the best IMEI for a given invoice.
        Priority: 1) exact item_code match  2) same category  3) highest-price item
        Only uses real product IMEIs (SC/fake serials already excluded in query).
        """
        items = imei_by_inv.get(inv_no, [])
        if not items:
            return ''
        # Tier 1: match by exact item code
        for item in items:
            if item['item_code'] == matched_code and item['imei']:
                return item['imei']
        # Tier 2: match by product category
        for item in items:
            if item['category'] == cat and item['imei']:
                return item['imei']
        for item in items:
            if item['imei']:
                return item['imei']
        return ''

    # ── Row builder ───────────────────────────────────────────────────────────
    def _make_row(self, inv_no, osg_r, osg_names, inv_info, inv_products,
                  imei_by_inv, mobile_products=None, is_sr=False):
        osg_code  = str(osg_r[2]).strip()
        osg_qty   = abs(int(osg_r[3] or 0))
        osg_price = abs(float(osg_r[4] or 0))
        osg_name  = osg_names.get(osg_code, osg_code)
        osg_cat   = osg_name.split(':')[0].strip() if ':' in osg_name else 'HAEW'

        info     = inv_info.get(inv_no, {})
        b_code   = str(info.get('branch', '')).upper()
        b_name   = self.branch_map.get(b_code, b_code)
        mobile   = str(info.get('mobile', ''))
        cust     = self.cust_name_map.get(mobile.strip(), '')
        inv_date = info.get('date', '')
        date_str = inv_date.strftime('%d/%m/%Y') if hasattr(inv_date, 'strftime') else str(inv_date)
        dur_ext, mfr_war = self.parse_dur(osg_name)

        if mobile_products and mobile:
            products = mobile_products.get(mobile, [])
        else:
            products = inv_products.get(inv_no, [])
            for p in products:
                if 'invoice_no' not in p:
                    p['invoice_no'] = inv_no

        cat = brand = model = serial = ''
        item_rate = 0
        comment   = ''
        return_flag = 'Not Returned'
        ret_against = ''
        primary_inv = inv_no

        if is_sr:
            return_flag = 'Returned'
            
            # Find the returned appliance on THIS SR invoice
            sr_appls = [p for p in products if p['sold_price'] < 0 and p.get('invoice_no') == inv_no]
            
            best_orig = None
            if sr_appls:
                # Get the most expensive returned item
                best_sr = min(sr_appls, key=lambda x: x['sold_price'])
                cat, brand, model = best_sr['category'], best_sr['brand'], best_sr['name']
                item_rate = abs(best_sr['sold_price'])
                matched_code = best_sr['item_code']
                
                # Try to find the ORIGINAL purchase of this exact item
                orig_appls = [p for p in products if p['item_code'] == matched_code and p['sold_price'] == item_rate and p.get('invoice_no') != inv_no]
                if orig_appls:
                    best_orig = orig_appls[-1]  # Take the most recent one if multiple
                    ret_against = best_orig.get('invoice_no')
                    primary_inv = ret_against
                    serial = self._get_imei(imei_by_inv, primary_inv, cat, matched_code)
                else:
                    ret_against = inv_no
                    serial = self._get_imei(imei_by_inv, inv_no, cat, matched_code)
            else:
                ret_against = inv_no
                
            if best_orig:
                comment = f'🔴 SALES RETURN — Original invoice: {ret_against}.'
            else:
                comment = f'⚠️ SALES RETURN — Could not find original purchase.'
        else:
            # Product matching — new customer-wide logic
            elig_cats = self.elig(osg_name)
            slab_lo, slab_hi = self.parse_slab(osg_name)
            matched_code = ''

            # Helper to find matches based on a price field
            def find_matches(price_field):
                matches = []
                if elig_cats:
                    for p in products:
                        if p['category'] in elig_cats and p['sold_price'] > 0:
                            if slab_hi < 999999:
                                if slab_lo <= p.get(price_field, 0) <= slab_hi:
                                    matches.append(p)
                            else:
                                matches.append(p)
                return matches

            # Helper to process a list of matches
            def process_matches(matches):
                if len(matches) == 1:
                    return matches[0], ''
                elif len(matches) > 1:
                    inv_matches = [p for p in matches if p.get('invoice_no') == inv_no]
                    if len(inv_matches) == 1:
                        return inv_matches[0], ''
                return None, 'multiple' if len(matches) > 1 else 'zero'

            # 1. Try mapping with sold_price
            matched_p, err_type = process_matches(find_matches('sold_price'))

            # 2. If no unique match, fallback to mop
            if not matched_p:
                matched_p_mop, err_type_mop = process_matches(find_matches('mop'))
                if matched_p_mop:
                    matched_p = matched_p_mop
                else:
                    # If mop also fails, prefer the error type from sold_price if it was multiple
                    err_type = err_type if err_type == 'multiple' else err_type_mop

            # 3. Finalize
            if matched_p:
                cat, brand, model = matched_p['category'], matched_p['brand'], matched_p['name']
                item_rate = matched_p['sold_price']
                matched_code = matched_p['item_code']
                primary_inv = matched_p.get('invoice_no', inv_no)
                comment = ''  # Perfect match
            else:
                if err_type == 'multiple':
                    comment = '❌ Needs review — multiple eligible appliances found for this customer'
                else:
                    comment = '❌ Needs review — no eligible appliance found for this customer'

            serial = self._get_imei(imei_by_inv, primary_inv, cat, matched_code)

        return {
            'Date':                      date_str,
            'Invoice No':                inv_no,
            'Customer':                  cust,
            'Store Code':                b_code,
            'Store Name':                b_name,
            'Region':                    '',
            'Serial No':                 serial,
            'Category':                  cat,
            'Brand':                     brand,
            'Quantity':                  osg_qty,
            'Model':                     (model or '')[:100],
            'Plan Type':                 osg_cat,
            'EWS Qty':                   osg_qty,
            'Item Rate':                 round(item_rate, 2),
            'Plan Price':                round(osg_price, 2),
            'Email':                     '',
            'Mobile No':                 mobile,
            'Manufacturer Warranty':     mfr_war,
            'Retailer SKU':              osg_name,
            'OnsiteGo SKU':              '',
            'Duration (Year)':           dur_ext,
            'Total Coverage':            '',
            'Comment':                   comment,
            'Return Flag':               return_flag,
            'Return against invoice No.': ret_against,
            'Primary Invoice No':        primary_inv,
            'OSG Code':                  osg_code,
            '_status':                   'RETURN' if is_sr else 'ACTIVATE',
            '_osg_code':                 osg_code,
        }

    # ── Excel export ──────────────────────────────────────────────────────────
    def to_excel(self, result):
        """Generate Excel in exact format matching OSG_Integration_Complete_Sep14_123.xlsx.
        Layout per sheet:
          Row 1 : Title string
          Row 2 : Date/Generated line
          Row 3 : blank
          Row 4 : Color legend
          Row 5 : blank
          Row 6 : Column headers (Status + 28 data cols)
          Row 7+: Data
        """
        wb = openpyxl.Workbook()
        wb.remove(wb.active)

        import datetime as _dt

        HDR_FILL = PatternFill('solid', fgColor='FF1565C0')   # dark-blue header
        HDR_FONT = Font(color='FFFFFFFF', bold=True, size=10, name='Calibri')

        # Status fill / font exactly as in target file
        STATUS_STYLE = {
            'ACTIVATE':   {'fill': PatternFill('solid', fgColor='FFC8E6C9'), 'font_color': 'FF1B5E20'},
            'RETURN':     {'fill': PatternFill('solid', fgColor='FFFFE0B2'), 'font_color': 'FFE65100'},
            'REGISTERED': {'fill': PatternFill('solid', fgColor='FFE3F2FD'), 'font_color': 'FF0D47A1'},
        }
        REG_RETURNED_FILL = PatternFill('solid', fgColor='FFFFCDD2')  # pink for registered+returned

        # Legend fills matching target (rows 4 legend boxes)
        LEG_REG  = PatternFill('solid', fgColor='FFE3F2FD')
        LEG_ACT  = PatternFill('solid', fgColor='FFC8E6C9')
        LEG_RET  = PatternFill('solid', fgColor='FFFFE0B2')

        gen_time   = _dt.datetime.now().strftime('%d %b %Y %I:%M %p IST')
        report_date = result['summary']['date']
        try:
            dt_obj     = _dt.datetime.strptime(report_date, '%Y-%m-%d')
            date_label = dt_obj.strftime('%d %B %Y')
            month_label = dt_obj.strftime('%B %Y')
        except Exception:
            date_label = report_date
            month_label = report_date

        def _c(ws, row, col, value='', fill=None, font=None, align=None, bold=False, size=9, color='FF000000'):
            cell = ws.cell(row, col, value)
            if fill:
                cell.fill = fill
            if font:
                cell.font = font
            else:
                cell.font = Font(bold=bold, size=size, name='Calibri', color=color)
            if align:
                cell.alignment = align
            return cell

        def write_sheet(ws, rows, sheet_title, date_line):
            total = len(rows)
            act   = sum(1 for r in rows if r.get('_status') == 'ACTIVATE')
            ret   = sum(1 for r in rows if r.get('_status') == 'RETURN')
            reg   = sum(1 for r in rows if r.get('_status') == 'REGISTERED')

            # ── Row 1: Title ─────────────────────────────────────────────────
            _c(ws, 1, 1, sheet_title,
               fill=PatternFill('solid', fgColor='FFE3F2FD'),
               bold=True, size=11, color='FF0D47A1')

            # ── Row 2: Date + Generated + counts ────────────────────────────
            line2 = (f'Date: {date_label}   |   Generated: {gen_time}   |   '
                     f'Total: {total}   Registered: {reg}   To Activate: {act}   SR Returns: {ret}')
            _c(ws, 2, 1, line2, bold=False, size=9, color='FF555555')

            # ── Row 3: blank ─────────────────────────────────────────────────

            # ── Row 4: Color legend ──────────────────────────────────────────
            _c(ws, 4, 1, 'Color Legend:', bold=True, size=9)
            _c(ws, 4, 3, 'REGISTERED (Blue) = Already in OnSiteGo',
               fill=LEG_REG, bold=True, size=9, color='FF0D47A1')
            _c(ws, 4, 6, 'TO ACTIVATE (Green) = Send to OnSiteGo',
               fill=LEG_ACT, bold=True, size=9, color='FF1B5E20')
            _c(ws, 4, 11, 'SR RETURN (Orange) = Returned warranties - no action on OnSiteGo',
               fill=LEG_RET, bold=True, size=9, color='FFE65100')

            # ── Row 5: blank ─────────────────────────────────────────────────

            # ── Row 6: Column headers ────────────────────────────────────────
            all_cols = ['Status'] + EXACT_COLS
            for ci, col in enumerate(all_cols, 1):
                c = ws.cell(6, ci, col)
                c.fill = HDR_FILL
                c.font = HDR_FONT
                c.alignment = Alignment(horizontal='center', vertical='center', wrap_text=True)
            ws.row_dimensions[6].height = 30

            # ── Rows 7+: Data ────────────────────────────────────────────────
            for ri, row in enumerate(rows, 7):
                status   = row.get('_status', 'ACTIVATE')
                style    = STATUS_STYLE.get(status, STATUS_STYLE['ACTIVATE'])
                ret_flag = str(row.get('Return Flag', '')).strip()
                row_fill = style['fill']
                fcolor   = style['font_color']

                # Registered rows that are returned get pink highlight
                if status == 'REGISTERED' and ret_flag == 'Returned':
                    row_fill = REG_RETURNED_FILL
                    fcolor   = 'FFB71C1C'

                # Status cell
                c = ws.cell(ri, 1, status)
                c.fill = row_fill
                c.font = Font(bold=True, size=9, name='Calibri', color=fcolor)
                c.alignment = Alignment(horizontal='center', vertical='top')

                # Data cells — map old field names to new column names
                for ci, col in enumerate(EXACT_COLS, 2):
                    # Handle renamed columns
                    val = row.get(col, '')
                    if col == 'OnsiteGo SKU':
                        val = row.get('OnsiteGo SKU', '') or row.get('OnSiteGo SKU', '')
                    elif col == 'Duration (Year)':
                        val = row.get('Duration (Year)', '') or row.get('Duration', '')
                    elif col == 'OSG Code':
                        val = row.get('OSG Code', '') or row.get('_osg_code', '')

                    c = ws.cell(ri, ci, val if val is not None else '')
                    c.fill = row_fill
                    c.font = Font(size=9, name='Calibri', color=fcolor)
                    c.alignment = Alignment(
                        wrap_text=(col in ('Comment', 'Retailer SKU', 'Model')),
                        vertical='top'
                    )

            # ── Column widths ────────────────────────────────────────────────
            ws.column_dimensions['A'].width = 14   # Status
            widths = {
                'Date': 12, 'Invoice No': 22, 'Customer': 22, 'Store Code': 10,
                'Store Name': 24, 'Region': 12, 'Serial No': 22,
                'Category': 22, 'Brand': 14, 'Quantity': 8, 'Model': 38,
                'Plan Type': 12, 'EWS Qty': 8, 'Item Rate': 12, 'Plan Price': 11,
                'Email': 22, 'Mobile No': 14, 'Manufacturer Warranty': 10,
                'Retailer SKU': 48, 'OnsiteGo SKU': 14,
                'Duration (Year)': 10, 'Total Coverage': 10,
                'Comment': 55, 'Return Flag': 14,
                'Return against invoice No.': 24, 'Primary Invoice No': 22,
                'OSG Code': 12,
            }
            for ci, col in enumerate(EXACT_COLS, 2):
                ws.column_dimensions[
                    openpyxl.utils.get_column_letter(ci)
                ].width = widths.get(col, 14)

            ws.freeze_panes = 'B7'

        summary         = result['summary']
        all_rows        = result['all_rows']
        activate_rows   = result['activate_rows']
        sr_rows         = result['sr_rows']
        registered_rows = result['registered_rows']

        write_sheet(
            wb.create_sheet('Complete Report'),
            all_rows,
            'OSG Complete Integration Report | ' + month_label,
            date_label,
        )
        write_sheet(
            wb.create_sheet('To Activate ({})'.format(len(activate_rows))),
            activate_rows,
            'OSG Missing Warranties \u2014 To Activate on OnSiteGo | ' + month_label,
            date_label,
        )
        write_sheet(
            wb.create_sheet('SR Returns ({})'.format(len(sr_rows))),
            sr_rows,
            'OSG Sales Return Cases \u2014 Original Warranties Never Registered | ' + month_label,
            date_label,
        )
        write_sheet(
            wb.create_sheet('Registered ({})'.format(len(registered_rows))),
            registered_rows,
            'OSG Already Registered \u2014 Integration Report | ' + month_label,
            date_label,
        )

        # Tab colours
        wb.worksheets[0].sheet_properties.tabColor = '0F3460'
        wb.worksheets[1].sheet_properties.tabColor = '2E7D32'
        wb.worksheets[2].sheet_properties.tabColor = 'E65100'
        wb.worksheets[3].sheet_properties.tabColor = '1565C0'

        buf = io.BytesIO()
        wb.save(buf)
        buf.seek(0)
        return buf.read()
