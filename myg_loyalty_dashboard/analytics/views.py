from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from django.utils.decorators import method_decorator
from django.views.decorators.cache import cache_page
from .services import AnalyticsService

def get_analytics():
    """Create a fresh AnalyticsService per request to avoid stale DuckDB connections."""
    return AnalyticsService()

def get_filters(request):
    """Extract standard filters from GET parameters."""
    filters = {
        'start_date': request.GET.get('start_date'),
        'end_date': request.GET.get('end_date'),
        'branch': request.GET.get('branch'),
        'staff': request.GET.get('staff'),
        'rbm': request.GET.get('rbm'),
        'bdm': request.GET.get('bdm'),
    }
    
    # Simple Role-Based Access Control logic
    user = request.user
    if user.is_authenticated:
        if user.role == 'Staff' and user.branch:
            filters['branch'] = user.branch
    return filters

class SalesOverviewAPI(APIView):
    permission_classes = [IsAuthenticated]
    # @method_decorator(cache_page(60 * 15))
    def get(self, request):
        data = get_analytics().get_sales_overview(get_filters(request))
        return Response(data)

class CustomerAnalyticsAPI(APIView):
    permission_classes = [IsAuthenticated]
    @method_decorator(cache_page(60 * 15))
    def get(self, request):
        data = get_analytics().get_customer_analytics(get_filters(request))
        return Response(data)

class RFMAnalysisAPI(APIView):
    permission_classes = [IsAuthenticated]
    def get(self, request):
        data = get_analytics().perform_rfm_analysis(get_filters(request))
        return Response(data)

class MonetaryQuintilesAPI(APIView):
    permission_classes = [IsAuthenticated]
    def get(self, request):
        data = get_analytics().get_monetary_quintiles(get_filters(request))
        return Response(data)

class CohortRetentionAPI(APIView):
    permission_classes = [IsAuthenticated]
    @method_decorator(cache_page(60 * 60 * 24)) # Cache for 24 hours
    def get(self, request):
        data = get_analytics().get_cohort_retention()
        return Response(data)

class YearlyCohortAPI(APIView):
    permission_classes = [IsAuthenticated]
    @method_decorator(cache_page(60 * 60 * 24)) # Cache for 24 hours
    def get(self, request):
        data = get_analytics().get_yearly_cohort_analysis()
        return Response(data)

class PaymentAnalyticsAPI(APIView):
    permission_classes = [IsAuthenticated]
    @method_decorator(cache_page(60 * 15))
    def get(self, request):
        data = get_analytics().get_payment_analytics(get_filters(request))
        return Response(data)

class DiscountAnalysisAPI(APIView):
    permission_classes = [IsAuthenticated]
    @method_decorator(cache_page(60 * 15))
    def get(self, request):
        data = get_analytics().get_discount_analysis(get_filters(request))
        return Response(data)

class StaffPerformanceAPI(APIView):
    permission_classes = [IsAuthenticated]
    @method_decorator(cache_page(60 * 15))
    def get(self, request):
        data = get_analytics().get_staff_performance(get_filters(request))
        return Response(data)

class BranchPerformanceAPI(APIView):
    permission_classes = [IsAuthenticated]
    @method_decorator(cache_page(60 * 15))
    def get(self, request):
        data = get_analytics().get_branch_performance(get_filters(request))
        return Response(data)

class FrequencyDistributionAPI(APIView):
    permission_classes = [IsAuthenticated]
    @method_decorator(cache_page(60 * 15))
    def get(self, request):
        data = get_analytics().get_frequency_distribution(get_filters(request))
        return Response(data)

class LoyaltyOverviewAPI(APIView):
    permission_classes = [IsAuthenticated]
    def get(self, request):
        data = get_analytics().get_loyalty_overview_kpis(get_filters(request))
        return Response(data)

class GapAnalysisAPI(APIView):
    permission_classes = [IsAuthenticated]
    def get(self, request):
        data = get_analytics().get_gap_segmentation(get_filters(request))
        return Response(data)

class LoyaltySegmentationAPI(APIView):
    permission_classes = [IsAuthenticated]
    def get(self, request):
        data = get_analytics().get_customer_segmentation_matrix(get_filters(request))
        return Response(data)

class ActionEngineAPI(APIView):
    permission_classes = [IsAuthenticated]
    def get(self, request):
        data = get_analytics().get_action_engine_data(get_filters(request))
        return Response(data)


class BusinessInsightsAPI(APIView):
    permission_classes = [IsAuthenticated]
    @method_decorator(cache_page(60 * 60)) # Cache for 1 hour
    def get(self, request):
        svc = get_analytics()
        insight_type = request.GET.get('type')
        if insight_type == 'cohort':
            data = svc.get_cohort_business_insights()
        else:
            data = svc.get_business_insights(get_filters(request))
        return Response(data)

class RetailLoyaltyReportAPI(APIView):
    permission_classes = [IsAuthenticated]
    @method_decorator(cache_page(60 * 15))
    def get(self, request):
        data = get_analytics().get_retail_loyalty_report(get_filters(request))
        return Response(data)

class RetailLoyaltyAdvancedReportAPI(APIView):
    permission_classes = [IsAuthenticated]
    @method_decorator(cache_page(60 * 15))
    def get(self, request):
        data = get_analytics().get_retail_loyalty_advanced_report(get_filters(request))
        return Response(data)

class BranchesAPI(APIView):
    permission_classes = [IsAuthenticated]
    @method_decorator(cache_page(60 * 60 * 24)) # Cache branches list for 24 hours
    def get(self, request):
        data = get_analytics().get_unique_branches()
        return Response(data)

import io
from django.http import HttpResponse
from django.contrib.auth.decorators import login_required
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment


def _build_xlsx_response(filename, headers, rows):
    """Build a styled .xlsx HttpResponse — plain Django, no DRF wrapping."""
    wb = Workbook()
    ws = wb.active
    ws.title = "Report"

    header_font = Font(bold=True, color="FFFFFF")
    header_fill = PatternFill("solid", fgColor="1E3A5F")
    header_align = Alignment(horizontal="center", vertical="center")

    ws.append(headers)
    for col_idx in range(1, len(headers) + 1):
        cell = ws.cell(row=1, column=col_idx)
        cell.font = header_font
        cell.fill = header_fill
        cell.alignment = header_align

    for row in rows:
        ws.append([str(v) if v is not None else "" for v in row])

    for col in ws.columns:
        max_len = max((len(str(c.value)) for c in col if c.value), default=10)
        ws.column_dimensions[col[0].column_letter].width = min(max_len + 4, 40)

    buffer = io.BytesIO()
    wb.save(buffer)
    xlsx_bytes = buffer.getvalue()

    response = HttpResponse(
        content=xlsx_bytes,
        content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    )
    # RFC 5987 encoding keeps Unicode filenames safe across browsers
    response['Content-Disposition'] = f'attachment; filename="{filename}"'
    response['Content-Length'] = str(len(xlsx_bytes))
    response['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    return response


def export_view(request, module):
    """Plain Django download view — avoids DRF response wrapping that can corrupt binary files."""
    import json, math
    svc = get_analytics()
    filters = get_filters(request)
    where_sql, params = svc._build_where_clause(filters)

    if module == 'customer-frequency':
        rows_data = svc.get_frequency_distribution(filters)
        headers = ['Segment', 'Customers', 'Net Revenue (INR)', 'Customer %', 'Revenue %', 'ASP (INR)']
        rows = [
            (r['segment'], r['customers'], r['net_revenue'],
             r['cust_pct'], r['rev_pct'], r['asp'])
            for r in rows_data
        ]
        return _build_xlsx_response('customer_frequency_report.xlsx', headers, rows)

    elif module in ('rfm', 'rfm-segments'):
        segment = request.GET.get('segment')
        if not segment:
            # Summary Export (The list circled by the user)
            rows_data = svc.get_rfm_segments(filters)
            headers = ['RFM Segment', 'Customer Count', 'Total Revenue (INR)', 'Average Revenue (INR)']
            rows = [
                (r['segment'], r['count'], r['total_revenue'], r['avg_revenue'])
                for r in rows_data
            ]
            return _build_xlsx_response('rfm_summary_report.xlsx', headers, rows)
        else:
            # Detail Export for a specific segment
            # Limit to 100k rows to prevent server crash or Excel limit issues
            query, params = svc.get_rfm_details_query(filters, segment)
            query += " LIMIT 100000"
            data = svc.conn.execute(query, params)
            headers = [d[0] for d in data.description]
            rows = data.fetchall()
            
            filename = f"rfm_{segment.lower().replace(' ', '_')}_details.xlsx"
            return _build_xlsx_response(filename, headers, rows)

    elif module == 'sales':
        table = "sales_data" if svc.using_native else "sqlite_db.sales_data"
        query = f'SELECT * FROM {table} WHERE {where_sql} LIMIT 50000'
        data = svc.conn.execute(query, params)
        headers = [d[0] for d in data.description]
        rows = data.fetchall()
        return _build_xlsx_response('sales_export.xlsx', headers, rows)

    elif module == 'gap-analysis':
        rows_data = svc.get_gap_segmentation(filters)
        headers = ['Gap Range', 'Customers', 'Percentage (%)', 'Avg Gap (Days)', 'Loyalty Signal', 'Priority', 'Action Strategy']
        rows = [
            (r['segment'], r['count'], r['percentage'], r['avg_gap'], r['signal'], r['priority'], r.get('action', ''))
            for r in rows_data
        ]
        return _build_xlsx_response('gap_analysis_report.xlsx', headers, rows)

    elif module == 'segment-customers':
        segment = request.GET.get('segment', '')
        part    = int(request.GET.get('part', '1'))

        if not segment:
            return HttpResponse("segment parameter required", status=400)

        chunk = svc.SEGMENT_CHUNK_SIZE

        # part=0  → return JSON describing how many parts exist
        if part == 0:
            total = svc.count_customers_for_segment(filters, segment)
            total_parts = max(1, math.ceil(total / chunk))
            return HttpResponse(
                json.dumps({"total_parts": total_parts, "total_customers": total}),
                content_type='application/json'
            )

        # part=N → return the actual Excel file for that chunk
        offset  = (part - 1) * chunk
        headers, rows = svc.get_customers_for_segment(filters, segment, offset)

        # Make the filename filesystem-safe
        safe_seg = segment.replace(' ', '-').replace('/', '-')
        filename = f"customers_{safe_seg}_part{part}.xlsx"

        # Friendly column headers for Excel
        display_headers = ['Customer Mobile', 'Customer Name', 'Visits',
                           'Net Revenue (INR)', 'Last Visit Date']
        return _build_xlsx_response(filename, display_headers, rows)

    elif module == 'retail-analytics':
        rows_data = svc.get_retail_loyalty_report(filters)
        headers = ['Month', 'Total Members', 'MoM Members %', 'Visits', 'MoM Visits %', 
                   'New Members', 'Repeat Members', 'Engagement Rate', 'Repeat %', 'Cumulative DB']
        rows = [
            (r['month'], r['total_members'], r['mom_total_members'], r['total_visits'], r['mom_visits'],
             r['new_members'], r['repeat_members'], r['engagement_rate'], r['repeat_pct'], r['db_size'])
            for r in rows_data
        ]
        return _build_xlsx_response('retail_loyalty_analytics_report.xlsx', headers, rows)

    return HttpResponse("Unknown export module", status=400)


# Keep the old DRF class so existing URL patterns still resolve without errors
class ExportAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, module):
        return export_view(request, module)
