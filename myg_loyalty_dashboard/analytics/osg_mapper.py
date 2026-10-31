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
EXACT_COLS = [
    'Date', 'Invoice No', 'Customer', 'Store Code', 'Store Name', 'Region',
    'Serial No', 'Category', 'Brand', 'Quantity', 'Model', 'Plan Type',
    'EWS Qty', 'Item Rate', 'Plan Price', 'Email', 'Mobile No',
    'Manufacturer Warranty', 'Duration', 'Retailer SKU', 'OnSiteGo SKU',
    'Total Coverage', 'Primary Invoice No', 'Return against invoice No.',
    'Return Flag', 'Comment',
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
        self.cust_name_map = {r[0].strip(): r[1].strip() for r in rows}

    # ── Category eligibility ──────────────────────────────────────────────────
    def elig(self, osg_cat):
        oc = osg_cat.upper()
        if 'ACEW'    in oc: return self.ACEW_C
        if 'TTC'     in oc or 'TV' in oc: return self.TTC_C
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
        wb = openpyxl.load_workbook(io.BytesIO(file_bytes))
        ws = wb.active
        hdrs = [ws.cell(1, c).value for c in range(1, ws.max_column + 1)]
        data = []
        for row in ws.iter_rows(min_row=2, values_only=True):
            if any(v for v in row):
                data.append(dict(zip(hdrs, row)))
        invs = {str(r.get('Invoice No') or '').strip() for r in data if r.get('Invoice No')}
        return data, invs

    def _fetch_ch_data(self, report_date, integ_inv_list):
        from analytics.clickhouse_service import ch_exclude_filter as _excl
        _EX = _excl()   # no alias
        _ES = _excl('s')  # alias s
        # OSG items for the date (SMC/EI invoices + 3GH/HEAD OFFICE/UG SMART CHOICE excluded)
        ch_osg = self.ch.query(f"""
            SELECT invoice_no, branch, item_code, qty, sold_price
            FROM azure_sales_report
            WHERE item_code LIKE 'OSG%'
              AND toDate(date) = toDate('{report_date}')
              AND {_EX}
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
              AND {_EX}
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
              AND {_ES}
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
        imei_rows = self.ch.query(f"""
            SELECT s.invoice_no, s.item_code, s.imei_batch, m.item_category, s.sold_price
            FROM azure_sales_report s
            LEFT JOIN item_master m ON s.item_code = m.item_code
            WHERE s.invoice_no IN ({inv_sql})
              AND s.item_code NOT LIKE 'OSG%'
              AND s.item_code NOT LIKE 'STY%'
              AND s.imei_batch != ''
              AND {_ES}
            ORDER BY s.invoice_no, s.sold_price DESC
        """).result_rows

        imei_by_inv = defaultdict(list)
        for r in imei_rows:
            imei_by_inv[r[0].strip()].append({
                'item_code': r[1],
                'imei': (r[2] or '').strip(),
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
        items = imei_by_inv.get(inv_no, [])
        for item in items:
            if item['item_code'] == matched_code and item['imei']:
                return item['imei']
        for item in items:
            if item['category'] == cat and item['imei']:
                return item['imei']
        for item in items:
            if item['imei']:
                return item['imei']
        return ''

    # ── Row builder ───────────────────────────────────────────────────────────
    def _make_row(self, inv_no, osg_r, osg_names, inv_info, inv_products,
                  imei_by_inv, is_sr=False):
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

        products = inv_products.get(inv_no, [])

        cat = brand = model = serial = ''
        item_rate = 0
        comment   = ''
        return_flag = 'Not Returned'
        ret_against = ''
        primary_inv = inv_no

        if is_sr:
            return_flag = 'Returned'
            # For SR invoices find the original invoice (negative qty items)
            orig_items = [p for p in products if p['sold_price'] < 0]
            if orig_items:
                best = min(orig_items, key=lambda x: x['sold_price'])  # most negative = highest value returned
                cat, brand, model = best['category'], best['brand'], best['name']
                item_rate = abs(best['sold_price'])
                serial = self._get_imei(imei_by_inv, inv_no, cat, best['item_code'])
            comment = (
                f'SALES RETURN — OSG warranty {osg_code} returned on {inv_no}. '
                f'Original warranty was not yet registered on OnSiteGo. '
                f'Action: Verify with OnSiteGo if cancellation is needed.'
            )
            ret_against = inv_no  # Will be refined if orig inv found in data
        else:
            # Product matching — 3-tier logic
            elig_cats = self.elig(osg_cat)
            slab_lo, slab_hi = self.parse_slab(osg_name)
            matched_code = ''

            # Tier 1: Slab range match
            if slab_hi < 999999:
                for p in sorted(products, key=lambda x: x['sold_price'], reverse=True):
                    if (p['category'] in elig_cats
                            and p['sold_price'] > 0
                            and slab_lo <= p['sold_price'] < slab_hi):
                        cat, brand, model = p['category'], p['brand'], p['name']
                        item_rate    = p['sold_price']
                        matched_code = p['item_code']
                        comment = f"Slab match [{slab_lo//1000}K-{slab_hi//1000}K] → {cat} {brand}"
                        break

            # Tier 2: Category match
            if not cat:
                for p in sorted(products, key=lambda x: x['sold_price'], reverse=True):
                    if p['category'] in elig_cats and p['sold_price'] > 0:
                        cat, brand, model = p['category'], p['brand'], p['name']
                        item_rate    = p['sold_price']
                        matched_code = p['item_code']
                        comment = f"Category match → {cat} {brand}"
                        break

            # Tier 3: Highest value fallback
            if not cat:
                valid = [p for p in products
                         if p['category'] not in SKIP_C and p['sold_price'] > 0]
                if valid:
                    best = max(valid, key=lambda x: x['sold_price'])
                    cat, brand, model = best['category'], best['brand'], best['name']
                    item_rate    = best['sold_price']
                    matched_code = best['item_code']
                    comment = '⚠️ Matched by highest value — verify manually'
                elif not products:
                    comment = '⚠️ STANDALONE INVOICE — No main product found on this invoice. Warranty billed separately.'

            serial = self._get_imei(imei_by_inv, inv_no, cat, matched_code)

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
            'Duration':                  dur_ext,
            'Retailer SKU':              osg_name,
            'OnSiteGo SKU':              osg_code,
            'Total Coverage':            '',
            'Primary Invoice No':        primary_inv,
            'Return against invoice No.': ret_against,
            'Return Flag':               return_flag,
            'Comment':                   comment,
            '_status':                   'RETURN' if is_sr else 'ACTIVATE',
            '_osg_code':                 osg_code,
        }

    # ── Excel export ──────────────────────────────────────────────────────────
    def to_excel(self, result):
        """Convert processed result to an Excel workbook (bytes)."""
        wb = openpyxl.Workbook()
        wb.remove(wb.active)

        HDR_FILL = PatternFill('solid', fgColor='FF0F3460')
        HDR_FONT = Font(color='FFFFFFFF', bold=True, size=10, name='Calibri')

        STATUS_STYLE = {
            'ACTIVATE':   {'font_color': 'FF1B5E20', 'fill': PatternFill('solid', fgColor='FFC8E6C9')},
            'RETURN':     {'font_color': 'FFB71C1C', 'fill': PatternFill('solid', fgColor='FFFFCDD2')},
            'REGISTERED': {'font_color': 'FF0D47A1', 'fill': PatternFill('solid', fgColor='FFBBDEFB')},
        }

        LEGEND_FILLS = {
            'REGISTERED': PatternFill('solid', fgColor='FF4FC3F7'),
            'ACTIVATE':   PatternFill('solid', fgColor='FF81C784'),
            'RETURN':     PatternFill('solid', fgColor='FFEF9A9A'),
        }

        def write_sheet(ws, rows, title):
            # Legend
            ws.merge_cells('A1:B1')
            ws['A1'] = title
            ws['A1'].font = Font(bold=True, size=11, name='Calibri')
            ws['A1'].fill = PatternFill('solid', fgColor='FF0F3460')
            ws['A1'].font = Font(bold=True, size=11, color='FFFFFFFF', name='Calibri')

            for col_i, (label, fill) in enumerate([
                ('REGISTERED (Blue) – Already In OnSite', LEGEND_FILLS['REGISTERED']),
                ('TO ACTIVATE (Green) – Send to OnSite', LEGEND_FILLS['ACTIVATE']),
                ('SR RETURN (Orange) – Returned Warranty', LEGEND_FILLS['RETURN']),
            ], start=3):
                c = ws.cell(1, col_i, label)
                c.fill = fill
                c.font = Font(bold=True, size=9, name='Calibri')

            # Status summary
            total = len(rows)
            reg   = sum(1 for r in rows if r.get('_status') == 'REGISTERED')
            act   = sum(1 for r in rows if r.get('_status') == 'ACTIVATE')
            ret   = sum(1 for r in rows if r.get('_status') == 'RETURN')
            ws['A2'] = f'Total: {total}   |   Registered: {reg}   |   To Activate: {act}   |   SR Returns: {ret}'
            ws['A2'].font = Font(bold=True, size=10, name='Calibri')

            # Status col + EXACT_COLS headers
            all_cols = ['Status'] + EXACT_COLS
            for ci, col in enumerate(all_cols, 1):
                c = ws.cell(6, ci, col)
                c.fill = HDR_FILL
                c.font = HDR_FONT
                c.alignment = Alignment(horizontal='center', vertical='center', wrap_text=True)

            # Data rows
            for ri, row in enumerate(rows, 7):
                status = row.get('_status', 'ACTIVATE')
                style  = STATUS_STYLE.get(status, STATUS_STYLE['ACTIVATE'])
                ret_flag = str(row.get('Return Flag', '')).strip()
                row_fill = style['fill']
                if status == 'REGISTERED' and ret_flag == 'Returned':
                    row_fill = PatternFill('solid', fgColor='FFFFA726')

                ws.cell(ri, 1, status).fill = row_fill
                ws.cell(ri, 1).font = Font(
                    color=style['font_color'], bold=True, size=9, name='Calibri'
                )
                for ci, col in enumerate(EXACT_COLS, 2):
                    val = row.get(col, '')
                    c = ws.cell(ri, ci, val)
                    c.fill = row_fill
                    c.font = Font(size=9, name='Calibri',
                                  color=style['font_color'])
                    c.alignment = Alignment(wrap_text=(col == 'Comment'),
                                            vertical='top')

            # Column widths
            ws.column_dimensions['A'].width = 14
            widths = {
                'Date': 12, 'Invoice No': 22, 'Customer': 22, 'Store Code': 12,
                'Store Name': 22, 'Region': 12, 'Serial No': 22,
                'Category': 22, 'Brand': 16, 'Quantity': 8, 'Model': 40,
                'Plan Type': 12, 'EWS Qty': 8, 'Item Rate': 12, 'Plan Price': 12,
                'Email': 22, 'Mobile No': 14, 'Manufacturer Warranty': 10,
                'Duration': 10, 'Retailer SKU': 45, 'OnSiteGo SKU': 14,
                'Total Coverage': 10, 'Primary Invoice No': 22,
                'Return against invoice No.': 22, 'Return Flag': 14,
                'Comment': 55,
            }
            for ci, col in enumerate(EXACT_COLS, 2):
                ws.column_dimensions[
                    openpyxl.utils.get_column_letter(ci)
                ].width = widths.get(col, 14)

            ws.freeze_panes = 'B7'

        summary = result['summary']
        all_rows        = result['all_rows']
        activate_rows   = result['activate_rows']
        sr_rows         = result['sr_rows']
        registered_rows = result['registered_rows']

        write_sheet(wb.create_sheet('Complete Report'),
                    all_rows,
                    f'OSG Complete Integration Report | {summary["date"]}')
        write_sheet(wb.create_sheet(f'To Activate ({len(activate_rows)})'),
                    activate_rows,
                    f'To Activate — {len(activate_rows)} missing warranties | {summary["date"]}')
        write_sheet(wb.create_sheet(f'SR Returns ({len(sr_rows)})'),
                    sr_rows,
                    f'Sales Return Warranties — {len(sr_rows)} cases | {summary["date"]}')
        write_sheet(wb.create_sheet(f'Registered ({len(registered_rows)})'),
                    registered_rows,
                    f'OSG Already Registered — Integration Report | {summary["date"]}')

        # Tab colours
        wb.worksheets[0].sheet_properties.tabColor = '0F3460'
        wb.worksheets[1].sheet_properties.tabColor = '2E7D32'
        wb.worksheets[2].sheet_properties.tabColor = 'B71C1C'
        wb.worksheets[3].sheet_properties.tabColor = '1565C0'

        buf = io.BytesIO()
        wb.save(buf)
        buf.seek(0)
        return buf.read()
