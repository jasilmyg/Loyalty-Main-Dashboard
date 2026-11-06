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
    get_demand_filter_options,
    get_demand_intelligence_data,
    get_single_product_deep_dive,
    get_sku_options,
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
                'districts': [],
                'branches': [],
            }
        ctx['filter_options'] = opts
        ctx['filter_options_json'] = json.dumps(opts)
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
                cell.alignment = Alignment(horizontal='center' if c_idx > 5 else 'left', vertical='center')
                cell.border = thin_border
                ws1.column_dimensions[openpyxl.utils.get_column_letter(c_idx)].width = width

            for r_offset, item in enumerate(payload.get('products', []), 5):
                for c_idx, (_title, key, _w) in enumerate(cols, 1):
                    val = item.get(key, '')
                    if key == 'stock_coverage_days' and val == 999:
                        val = 'Non-Moving'
                    c = ws1.cell(row=r_offset, column=c_idx, value=val)
                    c.border = thin_border
                    if c_idx > 5 and isinstance(val, (int, float)):
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


