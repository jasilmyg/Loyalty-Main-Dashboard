from rest_framework.views import APIView
from rest_framework.response import Response
from .clickhouse_service import get_ch_client, ch_exclude_filter
import logging

logger = logging.getLogger(__name__)

# Exclusion note: SMC/EI invoice rows + HEAD OFFICE / UG SMART CHOICE / 3GH
# branch rows are excluded from all dashboard API responses at query time.
# ClickHouse tables are NOT modified.
_EX   = ch_exclude_filter()    # no alias  (s.* queries)
_EX_S = ch_exclude_filter('s') # alias 's' (FROM azure_sales_report s)
_EX_I = ch_exclude_filter('ai') # alias 'ai' (FROM azure_invoice_report ai)

class AzureCategoryPerformanceAPI(APIView):
    def get(self, request):
        try:
            client = get_ch_client()
            # Get top categories by revenue
            query_cat = f"""
                SELECT 
                    i.category as category, 
                    sum(s.sold_price) as total_sales
                FROM azure_sales_report s
                JOIN item_master i ON s.item_code = i.item_code
                WHERE {_EX_S}
                GROUP BY category
                ORDER BY total_sales DESC
                LIMIT 10
            """
            categories = client.query(query_cat).result_rows
            
            # Get top brands
            query_brands = f"""
                SELECT 
                    i.brand as brand, 
                    sum(s.sold_price) as total_sales
                FROM azure_sales_report s
                JOIN item_master i ON s.item_code = i.item_code
                WHERE {_EX_S}
                GROUP BY brand
                ORDER BY total_sales DESC
                LIMIT 10
            """
            brands = client.query(query_brands).result_rows

            return Response({
                'status': 'success',
                'categories': [{'category': c[0], 'sales': float(c[1])} for c in categories],
                'brands': [{'brand': b[0], 'sales': float(b[1])} for b in brands]
            })
        except Exception as e:
            logger.error(f"Error in AzureCategoryPerformanceAPI: {e}")
            return Response({'status': 'error', 'message': str(e)}, status=500)

class AzureCustomerCohortsAPI(APIView):
    def get(self, request):
        try:
            client = get_ch_client()
            # Simple purchase frequency distribution
            query = f"""
                SELECT 
                    purchase_count,
                    count(*) as customer_count
                FROM (
                    SELECT 
                        customer_mobile,
                        count(DISTINCT invoice_no) as purchase_count
                    FROM azure_invoice_report
                    WHERE customer_mobile != ''
                      AND {_EX}
                    GROUP BY customer_mobile
                )
                WHERE purchase_count <= 10
                GROUP BY purchase_count
                ORDER BY purchase_count
            """
            frequency = client.query(query).result_rows
            
            return Response({
                'status': 'success',
                'frequency': [{'purchase_count': f[0], 'customers': f[1]} for f in frequency]
            })
        except Exception as e:
            return Response({'status': 'error', 'message': str(e)}, status=500)

class AzureBranchPerformanceAPI(APIView):
    def get(self, request):
        try:
            client = get_ch_client()
            query = f"""
                SELECT 
                    branch,
                    count(DISTINCT invoice_no) as invoices,
                    sum(invoice_total) as total_revenue,
                    sum(discount) as total_discount
                FROM azure_invoice_report
                WHERE {_EX}
                GROUP BY branch
                ORDER BY total_revenue DESC
                LIMIT 20
            """
            branches = client.query(query).result_rows
            
            return Response({
                'status': 'success',
                'branches': [{
                    'branch': b[0], 
                    'invoices': b[1], 
                    'revenue': float(b[2]),
                    'discount': float(b[3])
                } for b in branches]
            })
        except Exception as e:
            return Response({'status': 'error', 'message': str(e)}, status=500)

class AzureFinancierTrendsAPI(APIView):
    def get(self, request):
        try:
            client = get_ch_client()
            query = f"""
                SELECT 
                    financier_name,
                    sum(loan_amount) as total_loan,
                    count(*) as txn_count
                FROM azure_invoice_report
                WHERE financier_name != ''
                  AND {_EX}
                GROUP BY financier_name
                ORDER BY txn_count DESC
                LIMIT 10
            """
            financiers = client.query(query).result_rows
            
            return Response({
                'status': 'success',
                'financiers': [{
                    'financier': f[0], 
                    'loan_amount': float(f[1]),
                    'txns': f[2]
                } for f in financiers]
            })
        except Exception as e:
            return Response({'status': 'error', 'message': str(e)}, status=500)

class AzureInventoryVelocityAPI(APIView):
    def get(self, request):
        try:
            client = get_ch_client()
            query = f"""
                SELECT 
                    i.item_name as item,
                    sum(s.qty) as total_qty,
                    sum(s.sold_price) as total_revenue
                FROM azure_sales_report s
                JOIN item_master i ON s.item_code = i.item_code
                WHERE i.product NOT IN ('STATIONERY ITEMS', 'SERVICE', 'GLAMSHIELD', 'LAPTOP BAG', 'GIFT ITEMS', 'CROCKERY', 'RECHARGE')
                  AND i.category != 'OTHERS'
                  AND {_EX_S}
                GROUP BY item
                ORDER BY total_qty DESC
                LIMIT 10
            """
            velocity = client.query(query).result_rows
            
            return Response({
                'status': 'success',
                'velocity': [{
                    'item': v[0], 
                    'qty': float(v[1]),
                    'revenue': float(v[2])
                } for v in velocity]
            })
        except Exception as e:
            return Response({'status': 'error', 'message': str(e)}, status=500)

