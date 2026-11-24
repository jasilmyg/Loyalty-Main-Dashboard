"""
Views & REST APIs for Demand & Inventory Intelligence (100% ClickHouse-Backed).
"""

from __future__ import annotations

import io
import json
import traceback

import openpyxl
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import HttpResponse, JsonResponse
from django.views import View
from django.views.generic import TemplateView

from .demand_intelligence_service import (
    STOCK_RISK_TIERS,
    _get_reference_dates,
    get_demand_filter_options,
    get_demand_intelligence_data,
    get_single_product_deep_dive,
    get_sku_options,
    get_stock_risk_export_rows,
)
from .demand_promo_calendar import (
    CAMPAIGN_TYPE_META,
    CANONICAL_DISTRICTS,
    KNOWN_PRODUCT_LINES,
    STORE_FORMAT_LABELS,
    VisionUnavailableError,
    _get_branch_catalog_cached,
    delete_campaign,
    estimate_store_planned_lift_from_ch,
    extract_images_from_zip,
    get_item_categories_catalog,
    list_campaigns,
    parse_whatsapp_offer,
    parse_whatsapp_offers,
    recalibrate_campaigns_from_clickhouse,
    save_campaign,
    save_campaigns_bulk,
    search_item_groups_catalog,
)
from .demand_promo_learning import (
    build_backtest_report_xlsx,
    build_campaign_import_template_xlsx,
    get_campaign_hierarchy_breakdown,
    parse_campaigns_from_spreadsheet,
    run_campaign_backtest_scorecard,
)


class DemandIntelligencePageView(LoginRequiredMixin, TemplateView):
    template_name = 'dashboard/demand_intelligence.html'

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        try:
            opts = get_demand_filter_options()
        except Exception:
            opts = {
                'ref_date': '',
                'ref_date_display': 'Latest',
                'stock_date_display': 'Latest',
                'macro_categories': [],
                'products': [],
                'brands': [],
                'product_meta': {},
                'districts': [],
                'branches': [],
            }
        ctx['filter_options'] = opts
        ctx['filter_options_json'] = json.dumps(opts)
        return ctx


class CampaignOffersPageView(LoginRequiredMixin, TemplateView):
    """Dedicated sidebar workspace for WhatsApp & Campaign Offer Calendar wired live into Demand Intelligence forecasts."""

    template_name = 'dashboard/campaign_offers.html'

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        try:
            opts = get_demand_filter_options()
        except Exception:
            opts = {
                'ref_date': '',
                'ref_date_display': 'Latest',
                'stock_date_display': 'Latest',
                'products': [],
                'brands': [],
                'districts': [],
                'branches': [],
            }
        if not opts.get('branches'):
            bm_cat = _get_branch_catalog_cached()
            opts['branches'] = [
                {'code': code, 'name': bname, 'district': dist}
                for code, bname, dist in bm_cat
            ]
        ctx['filter_options'] = opts
        ctx['canonical_districts'] = list(CANONICAL_DISTRICTS)
        ctx['store_format_options'] = [{'key': k, 'label': v} for k, v in STORE_FORMAT_LABELS.items()]
        return ctx


class DemandIntelligenceAPIView(LoginRequiredMixin, View):
    def get(self, request):
        try:
            limit_raw = request.GET.get('limit', '250')
            try:
                limit = int(limit_raw)
            except ValueError:
                limit = 250

            data = get_demand_intelligence_data(
                granularity=request.GET.get('granularity', 'sku').strip(),
                macro_category=request.GET.get('macro_category', '').strip(),
                product_filter=request.GET.get('product', '').strip(),
                brand_filter=request.GET.get('brand', '').strip(),
                district_filter=request.GET.get('district', '').strip(),
                branch_filter=request.GET.get('branch', '').strip(),
                movement_filter=request.GET.get('movement', '').strip(),
                trend_filter=request.GET.get('trend', '').strip(),
                model_tier_filter=request.GET.get('model_tier', '').strip(),
                forecast_engine=request.GET.get('engine', 'hybrid').strip(),
                search_query=request.GET.get('search', '').strip(),
                selected_id=request.GET.get('selected_id', '').strip(),
                sort_by=request.GET.get('sort_by', 'd30').strip(),
                limit=limit,
            )
            return JsonResponse(data)
        except Exception as e:
            traceback.print_exc()
            return JsonResponse({'error': str(e)}, status=500)


class DemandProductDeepDiveAPIView(LoginRequiredMixin, View):
    def post(self, request):
        try:
            body = json.loads(request.body.decode('utf-8') or '{}')
            data = get_single_product_deep_dive(
                selected_id=str(body.get('selected_id', '')).strip(),
                granularity=str(body.get('granularity', 'sku')).strip(),
                macro_category=str(body.get('macro_category', '')).strip(),
                product_filter=str(body.get('product', '')).strip(),
                brand_filter=str(body.get('brand', '')).strip(),
                district_filter=str(body.get('district', '')).strip(),
                branch_filter=str(body.get('branch', '')).strip(),
                forecast_engine=str(body.get('engine', 'hybrid')).strip(),
                row_meta=body.get('row_meta'),
            )
            return JsonResponse(data)
        except Exception as e:
            traceback.print_exc()
            return JsonResponse({'error': str(e)}, status=500)

    def get(self, request):
        try:
            data = get_single_product_deep_dive(
                selected_id=request.GET.get('selected_id', '').strip(),
                granularity=request.GET.get('granularity', 'sku').strip(),
                macro_category=request.GET.get('macro_category', '').strip(),
                product_filter=request.GET.get('product', '').strip(),
                brand_filter=request.GET.get('brand', '').strip(),
                district_filter=request.GET.get('district', '').strip(),
                branch_filter=request.GET.get('branch', '').strip(),
                forecast_engine=request.GET.get('engine', 'hybrid').strip(),
                row_meta=None,
            )
            return JsonResponse(data)
        except Exception as e:
            traceback.print_exc()
            return JsonResponse({'error': str(e)}, status=500)


class DemandSkuOptionsAPIView(LoginRequiredMixin, View):
    """Header 'Quick SKU select': SKUs of a product line (best sellers first) with typeahead search."""

    def get(self, request):
        try:
            try:
                limit = int(request.GET.get('limit', '60'))
            except ValueError:
                limit = 60
            data = get_sku_options(
                product_filter=request.GET.get('product', '').strip(),
                brand_filter=request.GET.get('brand', '').strip(),
                macro_category=request.GET.get('macro_category', '').strip(),
                district_filter=request.GET.get('district', '').strip(),
                branch_filter=request.GET.get('branch', '').strip(),
                query=request.GET.get('q', '').strip(),
                limit=limit,
            )
            return JsonResponse(data)
        except Exception as e:
            traceback.print_exc()
            return JsonResponse({'error': str(e)}, status=500)


class DemandIntelligenceExportAPIView(LoginRequiredMixin, View):
    """Exports full Demand, Velocity, FSN, Forecast & Stock Coverage report to Excel (.xlsx)."""

    def get(self, request):
        try:
            payload = get_demand_intelligence_data(
                granularity=request.GET.get('granularity', 'sku').strip(),
                macro_category=request.GET.get('macro_category', '').strip(),
                product_filter=request.GET.get('product', '').strip(),
                brand_filter=request.GET.get('brand', '').strip(),
                district_filter=request.GET.get('district', '').strip(),
                branch_filter=request.GET.get('branch', '').strip(),
                movement_filter=request.GET.get('movement', '').strip(),
                trend_filter=request.GET.get('trend', '').strip(),
                model_tier_filter=request.GET.get('model_tier', '').strip(),
                forecast_engine=request.GET.get('engine', 'hybrid').strip(),
                search_query=request.GET.get('search', '').strip(),
                sort_by=request.GET.get('sort_by', 'd30').strip(),
                limit=1000,
            )

            wb = openpyxl.Workbook()
            ws1 = wb.active
            ws1.title = "Product Demand & Forecast"

            header_fill = PatternFill(start_color="0F172A", end_color="0F172A", fill_type="solid")
            header_font = Font(color="FFFFFF", bold=True, size=10)
            title_font = Font(color="0F172A", bold=True, size=13)
            sub_font = Font(color="64748B", italic=True, size=9)
            thin_border = Border(
                left=Side(style='thin', color='E2E8F0'),
                right=Side(style='thin', color='E2E8F0'),
                top=Side(style='thin', color='E2E8F0'),
                bottom=Side(style='thin', color='E2E8F0'),
            )

            meta = payload.get('meta', {})
            ws1.cell(row=1, column=1, value="myG — Demand & Inventory Intelligence Report (ClickHouse Live)").font = title_font
            ws1.cell(
                row=2,
                column=1,
                value=(
                    f"Anchor Sales Date: {meta.get('ref_date_display', '')} | "
                    f"Stock Snapshot: {meta.get('stock_date_display', '')} | "
                    f"Forecast Horizon: {meta.get('forecast_range_display', '')} | "
                    f"AI Engine: {meta.get('forecast_engine_label', 'Hybrid AI Ensemble')}"
                ),
            ).font = sub_font

            cols = [
                ("Code / SKU", 'code', 16),
                ("Product / SKU Name", 'name', 38),
                ("Category", 'macro_category', 14),
                ("Product Line", 'product_line', 20),
                ("Brand", 'brand', 16),
                ("Item Category", 'item_category', 20),
                ("Model / Spec Bucket", 'spec_bucket_label', 34),
                ("Price Band", 'price_band_label', 20),
                ("Lifecycle Stage", 'lifecycle_stage', 20),
                ("Seasonal / Lifecycle Adj %", '_arch_adj_pct', 20),
                ("AI Model Tier", 'model_tier_label', 21),
                ("Daily Velocity", 'daily_velocity', 14),
                ("Weekly Velocity", 'weekly_velocity', 15),
                ("Monthly Velocity", 'monthly_velocity', 16),
                ("7-Day Demand", 'd7', 13),
                ("15-Day Demand", 'd15', 13),
                ("30-Day Demand", 'd30', 14),
                ("60-Day Demand", 'd60', 14),
                ("90-Day Demand", 'd90', 14),
                ("Growth % (30d)", 'growth_pct', 14),
                ("Demand Trend", 'trend_status', 14),
                ("FSN Movement", 'movement_class', 15),
                ("Forecast Next 7d", 'forecast_7d', 15),
                ("Forecast Next 15d", 'forecast_15d', 16),
                ("Forecast Next 30d", 'forecast_30d', 16),
                ("Current Stock", 'current_stock', 14),
                ("In-Transit", 'transit_qty', 12),
                ("Coverage (Days)", 'stock_coverage_days', 15),
                ("30d Replenish Qty", 'replenish_qty_30d', 17),
            ]

            for c_idx, (title, _key, width) in enumerate(cols, 1):
                cell = ws1.cell(row=4, column=c_idx, value=title)
                cell.fill = header_fill
                cell.font = header_font
                cell.alignment = Alignment(horizontal='center' if c_idx > 9 else 'left', vertical='center')
                cell.border = thin_border
                ws1.column_dimensions[openpyxl.utils.get_column_letter(c_idx)].width = width

            for r_offset, item in enumerate(payload.get('products', []), 5):
                for c_idx, (_title, key, _w) in enumerate(cols, 1):
                    if key == '_arch_adj_pct':
                        if item.get('archetype1_active'):
                            val = item.get('archetype1_seasonal_adj_pct', 0.0)
                        elif item.get('archetype2_active'):
                            val = item.get('archetype2_adj_pct', 0.0)
                        elif item.get('archetype3_active'):
                            val = item.get('archetype3_adj_pct', 0.0)
                        elif item.get('archetype4_active'):
                            val = item.get('archetype4_adj_pct', 0.0)
                        elif item.get('archetype5_active'):
                            val = item.get('archetype5_adj_pct', 0.0)
                        elif item.get('archetype6_active'):
                            val = item.get('archetype6_tag', '')
                        else:
                            val = ''
                    else:
                        val = item.get(key, '')
                    if key == 'stock_coverage_days':
                        if item.get('is_virtual_vas'):
                            val = 'Digital / VAS'
                        elif val == 999:
                            val = 'Non-Moving'
                    c = ws1.cell(row=r_offset, column=c_idx, value=val)
                    c.border = thin_border
                    if c_idx > 9 and isinstance(val, (int, float)):
                        c.alignment = Alignment(horizontal='right', vertical='center')

            # Sheet 2: Seasonal & Festival Drivers
            ws2 = wb.create_sheet(title="Festival & Seasonal Drivers")
            ws2.cell(row=1, column=1, value="Seasonal & Festival Demand Lift Patterns (ClickHouse Historical)").font = title_font
            s2_cols = [("Event / Driver", 30), ("Demand Lift (%)", 18), ("Multiplier", 14), ("Next Window", 20), ("Avg Daily Units", 18)]
            for idx, (h_txt, w) in enumerate(s2_cols, 1):
                c = ws2.cell(row=3, column=idx, value=h_txt)
                c.fill = header_fill
                c.font = header_font
                ws2.column_dimensions[openpyxl.utils.get_column_letter(idx)].width = w

            for r_i, drv in enumerate(payload.get('festival_drivers', []), 4):
                ws2.cell(row=r_i, column=1, value=drv.get('label'))
                ws2.cell(row=r_i, column=2, value=drv.get('lift_pct'))
                ws2.cell(row=r_i, column=3, value=drv.get('multiplier'))
                ws2.cell(row=r_i, column=4, value=drv.get('next_date'))
                ws2.cell(row=r_i, column=5, value=drv.get('avg_daily_units'))

            # Sheet 3: Location Forecast Heatmap
            ws3 = wb.create_sheet(title="Location Forecast (30d)")
            ws3.cell(row=1, column=1, value="Forecast by Branch / Location (Next 30 Days)").font = title_font
            s3_cols = [("Branch", 28), ("District", 20), ("Wk 1 (1-7d)", 14), ("Wk 2 (8-15d)", 14), ("Wk 3 (16-22d)", 14), ("Wk 4 (23-30d)", 14), ("30d Forecast", 15), ("Current Stock", 15), ("Coverage Days", 15)]
            for idx, (h_txt, w) in enumerate(s3_cols, 1):
                c = ws3.cell(row=3, column=idx, value=h_txt)
                c.fill = header_fill
                c.font = header_font
                ws3.column_dimensions[openpyxl.utils.get_column_letter(idx)].width = w

            for r_i, loc in enumerate(payload.get('location_heatmap', []), 4):
                ws3.cell(row=r_i, column=1, value=loc.get('location'))
                ws3.cell(row=r_i, column=2, value=loc.get('district'))
                ws3.cell(row=r_i, column=3, value=loc.get('wk1'))
                ws3.cell(row=r_i, column=4, value=loc.get('wk2'))
                ws3.cell(row=r_i, column=5, value=loc.get('wk3'))
                ws3.cell(row=r_i, column=6, value=loc.get('wk4'))
                ws3.cell(row=r_i, column=7, value=loc.get('total_30d'))
                ws3.cell(row=r_i, column=8, value=loc.get('current_stock'))
                ws3.cell(row=r_i, column=9, value=loc.get('coverage_days'))

            # Sheet 4: Inter-Store Stock Transfer Routes (Strictly Same-District Only · Forecast + Safety Stock Protected)
            xfer = payload.get('inter_store_transfers') or {}
            xk = xfer.get('kpis') or {}
            ws4 = wb.create_sheet(title="Inter-Store Transfer Plan")
            ws4.cell(
                row=1,
                column=1,
                value="myG — Same-District Inter-Store Stock Transfer Plan (Forecast + Safety Stock Protected)",
            ).font = title_font
            ws4.cell(
                row=2,
                column=1,
                value=(
                    f"Logistics Rule: Strictly Same-District Only (100% Local Transfers · Zero Cross-District Freight) | "
                    f"Transfer-Ready SKUs: {xk.get('transferable_products_count', 0)} | "
                    f"Recommended Transfer Qty: {xk.get('recommended_transfer_qty', 0)} units | "
                    f"Expected Ageing Reduction (60+d): {xk.get('expected_ageing_reduction_units', 0)} units (₹{xk.get('expected_ageing_reduction_val', 0):,.0f}) | "
                    f"Potential Sales Opportunity: ₹{xk.get('potential_sales_opportunity_val', 0):,.0f}"
                ),
            ).font = sub_font

            s4_cols = [
                ("SKU Code", 'sku_code', 15),
                ("Product / SKU Name", 'sku_name', 36),
                ("Brand", 'brand', 15),
                ("Product Line", 'product_line', 18),
                ("District", 'from_district', 18),
                ("From Store (Excess)", 'from_store', 26),
                ("From Current Stock", 'from_stock_before', 17),
                ("From Protected (Fc+SS)", 'from_protected_stock', 20),
                ("To Store (Deficit)", 'to_store', 26),
                ("To Current Stock", 'to_stock_before', 16),
                ("To 15d Forecast", 'to_fc_15d', 15),
                ("To Safety Stock", 'to_safety_stock', 15),
                ("Recommended Transfer Qty", 'transfer_qty', 22),
                ("Logistics Scope", 'route_scope', 16),
                ("Ageing Reduced (60+d)", 'ageing_reduced_units', 19),
                ("Ageing Value Reduced (₹)", 'ageing_reduced_val', 21),
                ("Sales Opportunity (₹)", 'sales_opportunity_val', 20),
            ]
            for c_idx, (h_txt, _k, w) in enumerate(s4_cols, 1):
                c = ws4.cell(row=4, column=c_idx, value=h_txt)
                c.fill = header_fill
                c.font = header_font
                c.border = thin_border
                ws4.column_dimensions[openpyxl.utils.get_column_letter(c_idx)].width = w

            same_dist_routes = [
                rt for rt in xfer.get('transfer_routes', [])
                if rt.get('from_district') and rt.get('from_district') == rt.get('to_district')
            ]
            for r_offset, rt in enumerate(same_dist_routes, 5):
                for c_idx, (_h, k, _w) in enumerate(s4_cols, 1):
                    v = rt.get(k, '')
                    c = ws4.cell(row=r_offset, column=c_idx, value=v)
                    c.border = thin_border
                    if isinstance(v, (int, float)):
                        c.alignment = Alignment(horizontal='right', vertical='center')

            focus_mode = request.GET.get('focus', '').strip().lower()
            if focus_mode == 'transfers':
                wb.active = wb.sheetnames.index("Inter-Store Transfer Plan")

            buf = io.BytesIO()
            wb.save(buf)
            buf.seek(0)
            ref_slug = meta.get('ref_date', 'live').replace('-', '_')
            fname_prefix = (
                "myG_InterStore_Stock_Transfer_Plan"
                if focus_mode == 'transfers'
                else "myG_Demand_Inventory_Intelligence"
            )
            resp = HttpResponse(
                buf.getvalue(),
                content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            )
            resp['Content-Disposition'] = f'attachment; filename="{fname_prefix}_{ref_slug}.xlsx"'
            return resp
        except Exception as e:
            traceback.print_exc()
            return JsonResponse({'error': str(e)}, status=500)


class DemandDeadStockBranchExportAPIView(LoginRequiredMixin, View):
    """Downloads the complete 4-sheet Dead Stock + Branch Excel workbook (all 4,832 SKUs · 1.3L units · 12,122 branch rows)."""

    def get(self, request):
        import os
        try:
            prebuilt_path = os.path.abspath(
                os.path.join(
                    os.path.dirname(__file__),
                    '..', '..', 'scratch',
                    'Dead_Stock_Complete_Report_With_Branch_02-Nov-2026.xlsx',
                )
            )
            if os.path.exists(prebuilt_path):
                with open(prebuilt_path, 'rb') as fh:
                    data = fh.read()
                resp = HttpResponse(
                    data,
                    content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                )
                resp['Content-Disposition'] = (
                    'attachment; filename="Dead_Stock_Complete_Report_With_Branch_02-Nov-2026.xlsx"'
                )
                return resp
            return JsonResponse({'error': 'Dead stock report file not found.'}, status=404)
        except Exception as e:
            traceback.print_exc()
            return JsonResponse({'error': str(e)}, status=500)



class DemandStockRiskExportAPIView(LoginRequiredMixin, View):
    """
    "Export this list (CSV)" for the Non-Moving / Dead / At-Risk Stock Watch.
    Returns EVERY SKU of the selected tier + stock mode (the on-screen table is capped at the top 150).
    Query params: mode (all|saleable|non_saleable), tier (dead|non_moving|fresh_unsold|high_risk|at_risk)
    plus the dashboard location / category filters.
    """

    MODE_LABELS = {'all': 'All Stock', 'saleable': 'Saleable Only', 'non_saleable': 'Non-Saleable Only'}
    HEADER = [
        'Stock Mode', 'Risk Tier', 'Code', 'Product / SKU', 'Product Line', 'Brand', 'Stock Units',
        'Saleable Units', 'Non-Saleable Units', 'Displayed Units', 'Damaged Units', 'Advance Units',
        'Stock Value (MOP)', 'Stock Age (days)', 'Days Since Last Sale', '30d Sales', '60d Sales',
        '90d Sales', 'Stock Cover (days)', 'Demand Trend', 'Next 30d Forecast', 'Suggested Action',
    ]

    def get(self, request):
        import csv
        from datetime import date
        try:
            mode = request.GET.get('mode', 'all').strip()
            tier = request.GET.get('tier', 'dead').strip()
            rows, _summary = get_stock_risk_export_rows(
                mode=mode,
                tier=tier,
                granularity=request.GET.get('granularity', 'sku').strip(),
                macro_category=request.GET.get('macro_category', '').strip(),
                product_filter=request.GET.get('product', '').strip(),
                brand_filter=request.GET.get('brand', '').strip(),
                district_filter=request.GET.get('district', '').strip(),
                branch_filter=request.GET.get('branch', '').strip(),
                forecast_engine=request.GET.get('engine', 'hybrid').strip(),
            )
            mode_label = self.MODE_LABELS.get(mode, 'All Stock')
            tier_label = next((lbl for k, lbl, _d in STOCK_RISK_TIERS if k == tier), tier)

            buf = io.StringIO()
            w = csv.writer(buf, quoting=csv.QUOTE_ALL, lineterminator='\r\n')
            w.writerow(self.HEADER)
            for r in rows:
                w.writerow([
                    mode_label, tier_label, r['code'], r['name'], r['product_line'], r['brand'],
                    r['stock'], r['saleable_stock'], r['non_saleable_stock'], r['displayed_stock'],
                    r['damaged_stock'], r['advance_stock'], int(r['stock_val']), r['age_days'],
                    '>90' if r['days_since_sale'] is None else r['days_since_sale'],
                    r['d30'], r['d60'], r['d90'],
                    'No demand' if r['cover_days'] is None else r['cover_days'],
                    r['trend'], r['forecast_30d'], r['action'],
                ])

            resp = HttpResponse('\ufeff' + buf.getvalue(), content_type='text/csv; charset=utf-8')
            fname = f"stock_risk_{mode}_{tier}_{date.today().isoformat()}.csv"
            resp['Content-Disposition'] = f'attachment; filename="{fname}"'
            resp['X-Row-Count'] = str(len(rows))
            return resp
        except Exception as e:
            traceback.print_exc()
            return JsonResponse({'error': str(e)}, status=500)


class DemandCampaignsAPIView(LoginRequiredMixin, View):
    """
    CRUD API for `demand_promo_calendar` (WhatsApp Channel / Marketing Offers wired into forecasts).
      - GET    : List saved campaigns with live/upcoming/completed status + realized ClickHouse lift.
      - POST   : Create or update a campaign (or soft-delete if `action == 'delete'`).
      - DELETE : Soft-delete a campaign by `?campaign_id=...` or JSON body `{"campaign_id": "..."}`.
    """

    def handle_no_permission(self):
        return JsonResponse({'ok': False, 'error': 'Session expired — please sign in again.'}, status=401)

    def get(self, request):
        try:
            from analytics.clickhouse_service import get_ch_client
            ch = get_ch_client()
            ref_dt, _ = _get_reference_dates(ch) if ch is not None else (None, None)
            include_lift = request.GET.get('realized', '1').strip() != '0'
            items = list_campaigns(ch=ch, include_realized_lift=include_lift, ref_dt=ref_dt)
            bm_cat = _get_branch_catalog_cached(ch=ch)
            return JsonResponse({
                'ok': True,
                'ref_date': ref_dt.strftime('%Y-%m-%d') if ref_dt else None,
                'campaigns': items,
                'active_count': sum(1 for c in items if c.get('status') in ('live', 'upcoming')),
                'campaign_types': [
                    {'key': k, 'label': v['label'], 'color': v['color'], 'lifts': v['lifts'], 'default_cooldown': v['default_cooldown']}
                    for k, v in CAMPAIGN_TYPE_META.items()
                ],
                'known_products': list(KNOWN_PRODUCT_LINES),
                'canonical_districts': list(CANONICAL_DISTRICTS),
                'store_format_options': [{'key': k, 'label': v} for k, v in STORE_FORMAT_LABELS.items()],
                'branches': [
                    {'code': code, 'name': bname, 'district': dist}
                    for code, bname, dist in bm_cat
                ],
                'item_categories': get_item_categories_catalog(ch=ch, limit=400),
            })
        except Exception as e:
            traceback.print_exc()
            return JsonResponse({'ok': False, 'error': str(e)}, status=500)

    def post(self, request):
        try:
            up = request.FILES.get('file') or request.FILES.get('sheet')
            if not up:
                return JsonResponse({'ok': False, 'error': 'Please choose an Excel (.xlsx) or CSV (.csv) file.'}, status=400)
            if up.size > self.MAX_SHEET_BYTES:
                return JsonResponse({'ok': False, 'error': 'Spreadsheet exceeds 20 MB limit.'}, status=400)
            mode = (request.POST.get('mode') or 'preview').strip().lower()
            auto_est = (request.POST.get('auto_estimate') or '1').strip() != '0'
            parsed = parse_campaigns_from_spreadsheet(
                file_bytes=up.read(),
                filename=getattr(up, 'name', 'offers.xlsx') or 'offers.xlsx',
                auto_estimate=auto_est,
            )
            if mode == 'save':
                user_lbl = getattr(request.user, 'username', '') or 'excel_import'
                saved = save_campaigns_bulk(parsed['campaigns'], user_label=user_lbl)
                return JsonResponse({
                    'ok': True,
                    'mode': 'save',
                    'saved_count': len(saved),
                    'historical_count': parsed.get('historical_count', 0),
                    'warnings': parsed.get('warnings', []),
                })
            return JsonResponse({
                'ok': True,
                'mode': 'preview',
                'campaigns': parsed['campaigns'],
                'count': parsed['count'],
                'historical_count': parsed.get('historical_count', 0),
                'warnings': parsed.get('warnings', []),
            })
        except ValueError as ve:
            return JsonResponse({'ok': False, 'error': str(ve)}, status=400)
        except Exception as e:
            traceback.print_exc()
            return JsonResponse({'ok': False, 'error': str(e)}, status=500)


class DemandCampaignBacktestAPIView(LoginRequiredMixin, View):
    """Leak-Free Predicted vs. Actual Sale Value Backtest Scorecard (JSON or `?export=xlsx`)."""

    def handle_no_permission(self):
        return JsonResponse({'ok': False, 'error': 'Session expired — please sign in again.'}, status=401)

    def get(self, request):
        try:
            from analytics.clickhouse_service import get_ch_client
            ch = get_ch_client()
            ref_dt, _ = _get_reference_dates(ch) if ch is not None else (None, None)
            scorecard = run_campaign_backtest_scorecard(ch=ch, ref_dt=ref_dt)
            if (request.GET.get('export') or '').strip().lower() == 'xlsx':
                data = build_backtest_report_xlsx(scorecard)
                resp = HttpResponse(
                    data,
                    content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                )
                ref_slug = (scorecard.get('ref_date') or 'live').replace('-', '_')
                resp['Content-Disposition'] = f'attachment; filename="myG_Campaign_Predicted_vs_Actual_{ref_slug}.xlsx"'
                return resp
            return JsonResponse({'ok': True, **scorecard})
        except Exception as e:
            traceback.print_exc()
            return JsonResponse({'ok': False, 'error': str(e)}, status=500)


class DemandCampaignBreakdownAPIView(LoginRequiredMixin, View):
    """ClickHouse Product -> Item Category -> Item Group Predicted vs. Actual Sale Value breakdown for one campaign."""

    def handle_no_permission(self):
        return JsonResponse({'ok': False, 'error': 'Session expired — please sign in again.'}, status=401)

    def get(self, request):
        try:
            cid = (request.GET.get('campaign_id') or '').strip()
            if not cid:
                return JsonResponse({'ok': False, 'error': 'campaign_id is required.'}, status=400)
            from analytics.clickhouse_service import get_ch_client
            ch = get_ch_client()
            ref_dt, _ = _get_reference_dates(ch) if ch is not None else (None, None)
            data = get_campaign_hierarchy_breakdown(campaign_id=cid, ch=ch, ref_dt=ref_dt)
            return JsonResponse({'ok': True, **data})
        except ValueError as ve:
            return JsonResponse({'ok': False, 'error': str(ve)}, status=404)
        except Exception as e:
            traceback.print_exc()
            return JsonResponse({'ok': False, 'error': str(e)}, status=500)

    def post(self, request):
        try:
            body = json.loads(request.body.decode('utf-8') or '{}')
            user_lbl = getattr(request.user, 'username', '') or 'dashboard'
            action = str(body.get('action') or '').strip().lower()
            if action == 'delete':
                ok = delete_campaign(str(body.get('campaign_id') or ''), user_label=user_lbl)
                return JsonResponse({'ok': ok})
            if action == 'estimate_lift':
                est = estimate_store_planned_lift_from_ch(
                    campaign_type=body.get('campaign_type') or 'weekend_dhamaka',
                    offer_strength=body.get('offer_strength') or 'medium',
                    campaign_name=body.get('campaign_name') or '',
                    start_date=body.get('start_date'),
                    end_date=body.get('end_date'),
                    product_lines=body.get('product_lines') or 'ALL',
                    item_categories=body.get('item_categories') or 'ALL',
                    item_groups=body.get('item_groups') or 'ALL',
                    brands=body.get('brands') or 'ALL',
                    districts=body.get('districts') or body.get('district') or 'ALL',
                    store_formats=body.get('store_formats') or 'ALL',
                    branches=body.get('branches') or 'ALL',
                )
                return JsonResponse({'ok': True, 'estimate': est})
            if action == 'recalibrate_all':
                cids = body.get('campaign_ids') if isinstance(body.get('campaign_ids'), list) else None
                updated = recalibrate_campaigns_from_clickhouse(campaign_ids=cids, user_label=user_lbl)
                return JsonResponse({'ok': True, 'count': len(updated), 'updated': updated})
            if isinstance(body.get('campaigns'), list):
                saved_many = save_campaigns_bulk(body['campaigns'], user_label=user_lbl)
                return JsonResponse({'ok': True, 'campaigns': saved_many, 'count': len(saved_many)})
            saved = save_campaign(body, user_label=user_lbl)
            return JsonResponse({'ok': True, 'campaign': saved})
        except ValueError as ve:
            return JsonResponse({'ok': False, 'error': str(ve)}, status=400)
        except Exception as e:
            traceback.print_exc()
            return JsonResponse({'ok': False, 'error': str(e)}, status=500)

    def delete(self, request):
        try:
            cid = request.GET.get('campaign_id', '').strip()
            if not cid and request.body:
                body = json.loads(request.body.decode('utf-8') or '{}')
                cid = str(body.get('campaign_id') or '').strip()
            user_lbl = getattr(request.user, 'username', '') or 'dashboard'
            ok = delete_campaign(cid, user_label=user_lbl)
            return JsonResponse({'ok': ok})
        except Exception as e:
            traceback.print_exc()
            return JsonResponse({'ok': False, 'error': str(e)}, status=500)


class DemandCampaignParseAPIView(LoginRequiredMixin, View):
    """
    AI WhatsApp Offer Parser (Option 4): accepts pasted WhatsApp message text, 1..20 uploaded/pasted
    offer poster images (or chunked batches unpacked client-side from large ZIPs), and/or server-streamed
    `.zip` archive(s) up to 500 MB / 250 posters, returning structured campaign objects.
    """

    MAX_IMAGES = 20
    MAX_ZIP_FILES = 10
    MAX_TOTAL_IMAGES = 500
    MAX_IMG_BYTES = 25 * 1024 * 1024              # 25 MB per direct image
    MAX_ZIP_BYTES = 2 * 1024 * 1024 * 1024        # 2 GB per ZIP file (streamed from disk)
    MAX_TOTAL_BYTES = 2 * 1024 * 1024 * 1024 + 100 * 1024 * 1024  # ~2.1 GB total upload

    def handle_no_permission(self):
        return JsonResponse({'ok': False, 'error': 'Session expired — please sign in again.'}, status=401)

    @staticmethod
    def _is_zip_upload(up) -> bool:
        name = str(getattr(up, 'name', '') or '').lower()
        ct = str(getattr(up, 'content_type', '') or '').lower()
        return name.endswith('.zip') or ('zip' in ct)

    def post(self, request):
        import base64 as _b64
        try:
            raw_text = ''
            img_pairs = []
            store_hints = []
            total_bytes = 0
            zip_count = 0
            truncated_imgs = 0
            skipped_oversized = 0

            def _decode_b64_item(b64_str, mime_hint='image/jpeg'):
                mime = mime_hint or 'image/jpeg'
                s = str(b64_str or '').strip()
                if not s:
                    return None
                if ',' in s and s.startswith('data:'):
                    hdr, s = s.split(',', 1)
                    if ':' in hdr and ';' in hdr:
                        mime = hdr.split(':', 1)[1].split(';', 1)[0].strip()
                raw_b = _b64.b64decode(s)
                if len(raw_b) > self.MAX_IMG_BYTES:
                    raise ValueError('Each poster image must be under 15 MB.')
                return (raw_b, mime, '')

            ctype = (request.content_type or '').lower()
            if 'multipart/form-data' in ctype:
                raw_text = str(request.POST.get('text') or '')
                raw_hints = str(request.POST.get('store_hints') or '').strip()
                if raw_hints:
                    try:
                        parsed_h = json.loads(raw_hints)
                        if isinstance(parsed_h, list):
                            store_hints = [str(x or '').strip() for x in parsed_h]
                    except Exception:
                        store_hints = [x.strip() for x in raw_hints.split(',')]
                raw_files = (
                    list(request.FILES.getlist('images'))
                    + list(request.FILES.getlist('image'))
                    + list(request.FILES.getlist('zip_files'))
                    + list(request.FILES.getlist('zip_file'))
                )
                direct_imgs = [f for f in raw_files if not self._is_zip_upload(f)]
                zip_uploads = [f for f in raw_files if self._is_zip_upload(f)]

                if len(direct_imgs) > self.MAX_IMAGES:
                    return JsonResponse({'ok': False, 'error': f'Maximum {self.MAX_IMAGES} direct poster images allowed per batch (or pack up to {self.MAX_TOTAL_IMAGES} in a .ZIP file).'}, status=400)
                if len(zip_uploads) > self.MAX_ZIP_FILES:
                    return JsonResponse({'ok': False, 'error': f'Maximum {self.MAX_ZIP_FILES} ZIP files allowed per upload.'}, status=400)

                for up in direct_imgs:
                    if up.size > self.MAX_IMG_BYTES:
                        return JsonResponse({'ok': False, 'error': f'Image "{up.name}" exceeds 25 MB limit.'}, status=400)
                    total_bytes += up.size
                    if total_bytes > self.MAX_TOTAL_BYTES:
                        return JsonResponse({'ok': False, 'error': 'Total upload size exceeds 2 GB limit.'}, status=400)
                    img_pairs.append((up.read(), getattr(up, 'content_type', '') or 'image/jpeg', getattr(up, 'name', '') or ''))

                for zup in zip_uploads:
                    if zup.size > self.MAX_ZIP_BYTES:
                        return JsonResponse({'ok': False, 'error': f'ZIP file "{zup.name}" exceeds 2 GB limit.'}, status=400)
                    total_bytes += zup.size
                    if total_bytes > self.MAX_TOTAL_BYTES:
                        return JsonResponse({'ok': False, 'error': 'Total upload size exceeds 2 GB limit.'}, status=400)
                    room = self.MAX_TOTAL_IMAGES - len(img_pairs)
                    if room <= 0:
                        truncated_imgs += 1
                        continue
                    # Stream directly from Django's uploaded file object instead of reading full ZIP into RAM
                    z_src = getattr(zup, 'file', None) or zup
                    z_imgs, z_meta = extract_images_from_zip(z_src, max_images=room)
                    zip_count += 1
                    img_pairs.extend(z_imgs)
                    truncated_imgs += int(z_meta.get('truncated', 0))
                    skipped_oversized += int(z_meta.get('skipped_oversized', 0))

                if zip_uploads and not img_pairs and not raw_text.strip():
                    return JsonResponse({
                        'ok': False,
                        'error': 'No supported poster images (JPG, PNG, WEBP) were found inside the uploaded ZIP archive.',
                    }, status=400)
            else:
                body = json.loads(request.body.decode('utf-8') or '{}')
                raw_text = str(body.get('text') or '')
                json_imgs = body.get('images')
                if isinstance(json_imgs, list) and json_imgs:
                    if len(json_imgs) > self.MAX_IMAGES:
                        return JsonResponse({'ok': False, 'error': f'Maximum {self.MAX_IMAGES} poster images allowed per upload.'}, status=400)
                    for item in json_imgs:
                        if isinstance(item, dict):
                            pair = _decode_b64_item(item.get('base64') or item.get('data'), item.get('mime') or 'image/jpeg')
                        else:
                            pair = _decode_b64_item(item, 'image/jpeg')
                        if pair:
                            total_bytes += len(pair[0])
                            if total_bytes > self.MAX_TOTAL_BYTES:
                                return JsonResponse({'ok': False, 'error': 'Total uploaded images exceed 600 MB limit.'}, status=400)
                            img_pairs.append(pair)
                elif body.get('image_base64'):
                    pair = _decode_b64_item(body.get('image_base64'), body.get('image_mime') or 'image/jpeg')
                    if pair:
                        img_pairs.append(pair)

            res = parse_whatsapp_offers(raw_text=raw_text, images=img_pairs, store_hints=store_hints)
            camps = res.get('campaigns') or []
            warn_parts = [w for w in [res.get('warning', '')] if w]
            if truncated_imgs > 0:
                warn_parts.append(f'Processed first {self.MAX_TOTAL_IMAGES} posters from ZIP ({truncated_imgs} extra image(s) skipped).')
            if skipped_oversized > 0:
                warn_parts.append(f'Skipped {skipped_oversized} oversized/unreadable image(s) in ZIP.')
            return JsonResponse({
                'ok': True,
                'campaigns': camps,
                'parsed': camps[0] if camps else {},
                'parser_mode': res.get('parser_mode', ''),
                'image_count': res.get('image_count', len(img_pairs)),
                'zip_count': zip_count,
                'truncated_count': truncated_imgs,
                'warning': ' '.join(warn_parts),
            })
        except VisionUnavailableError as vue:
            return JsonResponse({'ok': False, 'retryable': True, 'error': str(vue)}, status=503)
        except ValueError as ve:
            return JsonResponse({'ok': False, 'error': str(ve)}, status=400)
        except Exception as e:
            traceback.print_exc()
            return JsonResponse({'ok': False, 'error': str(e)}, status=500)


class DemandCampaignItemLookupAPIView(LoginRequiredMixin, View):
    """Live searchable lookup for ClickHouse `item_master.item_category` and `item_master.item_group`."""

    def handle_no_permission(self):
        return JsonResponse({'ok': False, 'error': 'Session expired — please sign in again.'}, status=401)

    def get(self, request):
        try:
            level = (request.GET.get('level') or 'group').strip().lower()
            q = (request.GET.get('q') or '').strip()
            products = request.GET.get('products') or 'ALL'
            categories = request.GET.get('categories') or 'ALL'
            limit = int(request.GET.get('limit') or 60)
            if level == 'category':
                cats = get_item_categories_catalog(limit=600)
                q_up = q.upper()
                p_set = {x.strip().upper() for x in str(products).split(',') if x.strip() and x.strip().upper() != 'ALL'}
                filtered = [
                    it for it in cats
                    if (not p_set or it['product'] in p_set)
                    and (not q_up or q_up in it['item_category'] or q_up in it['product'])
                ]
                return JsonResponse({'ok': True, 'items': filtered[:limit]})
            items = search_item_groups_catalog(query=q, product_lines=products, item_categories=categories, limit=limit)
            return JsonResponse({'ok': True, 'items': items})
        except Exception as e:
            traceback.print_exc()
            return JsonResponse({'ok': False, 'error': str(e)}, status=500)


class DemandCampaignTemplateAPIView(LoginRequiredMixin, View):
    """Download the 4-sheet colleague Excel template (Fill Sheet + SOP + Live Branches + Live Products/Categories)."""

    def get(self, request):
        try:
            data = build_campaign_import_template_xlsx()
            resp = HttpResponse(
                data,
                content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            )
            resp['Content-Disposition'] = 'attachment; filename="myG_Campaign_Offers_Import_Template.xlsx"'
            return resp
        except Exception as e:
            traceback.print_exc()
            return JsonResponse({'ok': False, 'error': str(e)}, status=500)


class DemandCampaignSpreadsheetImportAPIView(LoginRequiredMixin, View):
    """Bulk import previous or upcoming campaigns from colleague-uploaded `.xlsx` or `.csv` files."""

    MAX_SHEET_BYTES = 20 * 1024 * 1024

    def handle_no_permission(self):
        return JsonResponse({'ok': False, 'error': 'Session expired — please sign in again.'}, status=401)

    def post(self, request):
        try:
            up = request.FILES.get('file') or request.FILES.get('sheet')
            if not up:
                return JsonResponse({'ok': False, 'error': 'Please choose an Excel (.xlsx) or CSV (.csv) file.'}, status=400)
            if up.size > self.MAX_SHEET_BYTES:
                return JsonResponse({'ok': False, 'error': 'Spreadsheet exceeds 20 MB limit.'}, status=400)
            mode = (request.POST.get('mode') or 'preview').strip().lower()
            auto_est = (request.POST.get('auto_estimate') or '1').strip() != '0'
            parsed = parse_campaigns_from_spreadsheet(
                file_bytes=up.read(),
                filename=getattr(up, 'name', 'offers.xlsx') or 'offers.xlsx',
                auto_estimate=auto_est,
            )
            if mode == 'save':
                user_lbl = getattr(request.user, 'username', '') or 'excel_import'
                saved = save_campaigns_bulk(parsed['campaigns'], user_label=user_lbl)
                return JsonResponse({
                    'ok': True,
                    'mode': 'save',
                    'saved_count': len(saved),
                    'historical_count': parsed.get('historical_count', 0),
                    'warnings': parsed.get('warnings', []),
                })
            return JsonResponse({
                'ok': True,
                'mode': 'preview',
                'campaigns': parsed['campaigns'],
                'count': parsed['count'],
                'historical_count': parsed.get('historical_count', 0),
                'warnings': parsed.get('warnings', []),
            })
        except ValueError as ve:
            return JsonResponse({'ok': False, 'error': str(ve)}, status=400)
        except Exception as e:
            traceback.print_exc()
            return JsonResponse({'ok': False, 'error': str(e)}, status=500)


class DemandCampaignBacktestAPIView(LoginRequiredMixin, View):
    """Leak-Free Predicted vs. Actual Sale Value Backtest Scorecard (JSON or `?export=xlsx`)."""

    def handle_no_permission(self):
        return JsonResponse({'ok': False, 'error': 'Session expired — please sign in again.'}, status=401)

    def get(self, request):
        try:
            from analytics.clickhouse_service import get_ch_client
            ch = get_ch_client()
            ref_dt, _ = _get_reference_dates(ch) if ch is not None else (None, None)
            scorecard = run_campaign_backtest_scorecard(ch=ch, ref_dt=ref_dt)
            if (request.GET.get('export') or '').strip().lower() == 'xlsx':
                data = build_backtest_report_xlsx(scorecard)
                resp = HttpResponse(
                    data,
                    content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                )
                ref_slug = (scorecard.get('ref_date') or 'live').replace('-', '_')
                resp['Content-Disposition'] = f'attachment; filename="myG_Campaign_Predicted_vs_Actual_{ref_slug}.xlsx"'
                return resp
            return JsonResponse({'ok': True, **scorecard})
        except Exception as e:
            traceback.print_exc()
            return JsonResponse({'ok': False, 'error': str(e)}, status=500)


class DemandCampaignBreakdownAPIView(LoginRequiredMixin, View):
    """ClickHouse Product -> Item Category -> Item Group Predicted vs. Actual Sale Value breakdown for one campaign."""

    def handle_no_permission(self):
        return JsonResponse({'ok': False, 'error': 'Session expired — please sign in again.'}, status=401)

    def get(self, request):
        try:
            cid = (request.GET.get('campaign_id') or '').strip()
            if not cid:
                return JsonResponse({'ok': False, 'error': 'campaign_id is required.'}, status=400)
            from analytics.clickhouse_service import get_ch_client
            ch = get_ch_client()
            ref_dt, _ = _get_reference_dates(ch) if ch is not None else (None, None)
            data = get_campaign_hierarchy_breakdown(campaign_id=cid, ch=ch, ref_dt=ref_dt)
            return JsonResponse({'ok': True, **data})
        except ValueError as ve:
            return JsonResponse({'ok': False, 'error': str(ve)}, status=404)
        except Exception as e:
            traceback.print_exc()
            return JsonResponse({'ok': False, 'error': str(e)}, status=500)
