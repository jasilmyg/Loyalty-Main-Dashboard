"""
views.py — Market Basket & Cross-Sell Dashboard Views
All data is READ from precomputed ClickHouse mb_* tables (sub-second queries).
"""
import json
import logging
from datetime import datetime

from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import JsonResponse
from django.views.generic import TemplateView, View

from analytics.clickhouse_service import get_ch_client

logger = logging.getLogger(__name__)


def _ch():
    return get_ch_client()


def _rows(client, sql: str, params: dict = None) -> list:
    try:
        return client.query(sql, parameters=params or {}).result_rows
    except Exception as e:
        logger.error(f"[MB Views] Query error: {e}\nSQL: {sql}")
        return []


# ─── Page View ────────────────────────────────────────────────────────────────

class MarketBasketDashboardView(LoginRequiredMixin, TemplateView):
    template_name = "dashboard/market_basket.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        # Pass last computed time
        ch = _ch()
        if ch:
            rows = _rows(ch, "SELECT max(computed_at) FROM mb_basket_kpis")
            ctx['last_computed'] = rows[0][0] if rows and rows[0][0] else None
        return ctx


# ─── KPI Cards ────────────────────────────────────────────────────────────────

class MarketBasketKPIsAPIView(LoginRequiredMixin, View):
    def get(self, request):
        ch = _ch()
        if not ch:
            return JsonResponse({'error': 'ClickHouse unavailable'}, status=503)

        rows = _rows(ch, """
            SELECT
                total_transactions, avg_basket_value, avg_items_per_basket,
                single_item_pct, multi_item_pct, accessory_attach_rate,
                crosssell_revenue, missed_revenue, missed_margin,
                total_rules, total_opportunities, computed_at
            FROM mb_basket_kpis
            ORDER BY computed_at DESC
            LIMIT 1
        """)
        if not rows:
            return JsonResponse({'ready': False})

        r = rows[0]
        return JsonResponse({
            'ready': True,
            'total_transactions':    int(r[0]),
            'avg_basket_value':      float(r[1]),
            'avg_items_per_basket':  float(r[2]),
            'single_item_pct':       float(r[3]),
            'multi_item_pct':        float(r[4]),
            'accessory_attach_rate': float(r[5]),
            'crosssell_revenue':     float(r[6]),
            'missed_revenue':        float(r[7]),
            'missed_margin':         float(r[8]),
            'total_rules':           int(r[9]),
            'total_opportunities':   int(r[10]),
            'computed_at':           str(r[11]),
        })


# ─── Top Association Rules ────────────────────────────────────────────────────

class MarketBasketAssociationsAPIView(LoginRequiredMixin, View):
    def get(self, request):
        ch = _ch()
        if not ch:
            return JsonResponse({'error': 'ClickHouse unavailable'}, status=503)

        algorithm = request.GET.get('algorithm', '')
        limit      = int(request.GET.get('limit', 50))
        algo_filter = f"AND algorithm = '{algorithm}'" if algorithm else ""

        rows = _rows(ch, f"""
            SELECT
                item_a, item_b, item_a_name, item_b_name,
                category_a, category_b, brand_a, brand_b,
                support, confidence, lift, leverage, conviction, algorithm
            FROM mb_association_rules
            WHERE lift >= 1.0 {algo_filter}
            ORDER BY lift DESC
            LIMIT {limit}
        """)

        data = [{
            'item_a': r[0], 'item_b': r[1],
            'item_a_name': r[2], 'item_b_name': r[3],
            'category_a': r[4], 'category_b': r[5],
            'brand_a': r[6], 'brand_b': r[7],
            'support':    round(float(r[8]) * 100, 3),
            'confidence': round(float(r[9]) * 100, 2),
            'lift':       round(float(r[10]), 3),
            'leverage':   round(float(r[11]), 6),
            'conviction': round(float(r[12]), 3),
            'algorithm':  r[13],
        } for r in rows]

        return JsonResponse({'associations': data, 'total': len(data)})


# ─── Cross-Sell Opportunities ─────────────────────────────────────────────────

class MarketBasketOpportunitiesAPIView(LoginRequiredMixin, View):
    def get(self, request):
        ch = _ch()
        if not ch:
            return JsonResponse({'error': 'ClickHouse unavailable'}, status=503)

        category  = request.GET.get('category', '')
        limit     = int(request.GET.get('limit', 50))
        cat_filter = f"AND category_a = '{category}'" if category else ""

        rows = _rows(ch, f"""
            SELECT
                product_a_code, product_a_name, product_b_code, product_b_name,
                category_a, category_b, brand_a, brand_b,
                total_txn_with_a, actual_attach_rate, expected_attach_rate,
                gap, missed_units, missed_revenue, missed_margin, opportunity_score
            FROM mb_cross_sell_opportunities
            WHERE opportunity_score > 0 {cat_filter}
            ORDER BY opportunity_score DESC
            LIMIT {limit}
        """)

        data = [{
            'product_a_code':       r[0],
            'product_a_name':       r[1],
            'product_b_code':       r[2],
            'product_b_name':       r[3],
            'category_a':           r[4],
            'category_b':           r[5],
            'brand_a':              r[6],
            'brand_b':              r[7],
            'total_txn_with_a':     int(r[8]),
            'actual_attach_rate':   round(float(r[9]) * 100, 2),
            'expected_attach_rate': round(float(r[10]) * 100, 2),
            'gap':                  round(float(r[11]) * 100, 2),
            'missed_units':         int(r[12]),
            'missed_revenue':       round(float(r[13]), 2),
            'missed_margin':        round(float(r[14]), 2),
            'opportunity_score':    round(float(r[15]), 2),
        } for r in rows]

        return JsonResponse({'opportunities': data, 'total': len(data)})


# ─── Product Relationship Network (graph data for Plotly) ─────────────────────

class MarketBasketNetworkAPIView(LoginRequiredMixin, View):
    def get(self, request):
        ch = _ch()
        if not ch:
            return JsonResponse({'error': 'ClickHouse unavailable'}, status=503)

        limit = int(request.GET.get('limit', 50))
        rows = _rows(ch, f"""
            SELECT
                item_a_name, item_b_name, lift, confidence, category_a, category_b
            FROM mb_association_rules
            WHERE lift > 2.0
            ORDER BY lift DESC
            LIMIT {limit}
        """)

        nodes_set = {}
        edges = []
        for r in rows:
            for name, cat in [(r[0], r[4]), (r[1], r[5])]:
                if name not in nodes_set:
                    nodes_set[name] = {'id': name, 'category': cat, 'size': 0}
                nodes_set[name]['size'] += 1
            edges.append({
                'from': r[0], 'to': r[1],
                'lift': round(float(r[2]), 2),
                'confidence': round(float(r[3]) * 100, 1),
            })

        return JsonResponse({
            'nodes': list(nodes_set.values()),
            'edges': edges,
        })


# ─── Category Cross-Sell Matrix ───────────────────────────────────────────────

class MarketBasketCategoryMatrixAPIView(LoginRequiredMixin, View):
    def get(self, request):
        ch = _ch()
        if not ch:
            return JsonResponse({'error': 'ClickHouse unavailable'}, status=503)

        rows = _rows(ch, """
            SELECT
                category_a, category_b,
                round(avg(confidence) * 100, 2) as avg_confidence,
                round(avg(lift), 3)              as avg_lift,
                count()                          as rule_count
            FROM mb_association_rules
            WHERE category_a != '' AND category_b != ''
              AND category_a != category_b
            GROUP BY category_a, category_b
            ORDER BY avg_lift DESC
            LIMIT 200
        """)

        categories = sorted(set(
            [r[0] for r in rows] + [r[1] for r in rows]
        ))

        # Build matrix
        matrix = {c: {c2: 0.0 for c2 in categories} for c in categories}
        for r in rows:
            matrix[r[0]][r[1]] = float(r[2])

        return JsonResponse({
            'categories': categories,
            'matrix':     [[matrix[c1].get(c2, 0) for c2 in categories] for c1 in categories],
            'details':    [{'cat_a': r[0], 'cat_b': r[1], 'confidence': float(r[2]),
                             'lift': float(r[3]), 'rules': int(r[4])} for r in rows],
        })


# ─── Branch Performance ───────────────────────────────────────────────────────

class MarketBasketBranchPerfAPIView(LoginRequiredMixin, View):
    def get(self, request):
        ch = _ch()
        if not ch:
            return JsonResponse({'error': 'ClickHouse unavailable'}, status=503)

        rows = _rows(ch, """
            SELECT
                branch, branch_name, total_invoices, multi_item_invoices,
                attach_rate, crosssell_revenue, missed_revenue, avg_basket_value,
                avg_items, rank
            FROM mb_branch_performance
            ORDER BY attach_rate DESC
            LIMIT 100
        """)

        data = [{
            'branch':          r[0],
            'branch_name':     r[1],
            'total_invoices':  int(r[2]),
            'multi_item_invoices': int(r[3]),
            'attach_rate':     round(float(r[4]) * 100, 2),
            'crosssell_revenue': round(float(r[5]), 2),
            'missed_revenue':   round(float(r[6]), 2),
            'avg_basket_value': round(float(r[7]), 2),
            'avg_items':        round(float(r[8]), 2),
            'rank':             int(r[9]),
        } for r in rows]

        return JsonResponse({'branches': data, 'total': len(data)})


# ─── Salesperson Performance ──────────────────────────────────────────────────

class MarketBasketSalespersonPerfAPIView(LoginRequiredMixin, View):
    def get(self, request):
        ch = _ch()
        if not ch:
            return JsonResponse({'error': 'ClickHouse unavailable'}, status=503)

        branch_filter = request.GET.get('branch', '')
        b_filter      = f"AND branch = '{branch_filter}'" if branch_filter else ""

        rows = _rows(ch, f"""
            SELECT
                staff_code, branch, branch_name, total_invoices,
                multi_item_invoices, attach_rate, crosssell_revenue,
                avg_basket_value, rank
            FROM mb_salesperson_performance
            WHERE 1=1 {b_filter}
            ORDER BY attach_rate DESC
            LIMIT 100
        """)

        data = [{
            'staff_code':      r[0],
            'branch':          r[1],
            'branch_name':     r[2],
            'total_invoices':  int(r[3]),
            'multi_item_invoices': int(r[4]),
            'attach_rate':     round(float(r[5]) * 100, 2),
            'crosssell_revenue': round(float(r[6]), 2),
            'avg_basket_value':  round(float(r[7]), 2),
            'rank':            int(r[8]),
        } for r in rows]

        return JsonResponse({'salespersons': data, 'total': len(data)})


# ─── Customer Recommendations ─────────────────────────────────────────────────

class MarketBasketCustomerRecsAPIView(LoginRequiredMixin, View):
    def get(self, request):
        ch = _ch()
        if not ch:
            return JsonResponse({'error': 'ClickHouse unavailable'}, status=503)

        mobile = request.GET.get('mobile', '').strip()
        if not mobile:
            return JsonResponse({'error': 'mobile required'}, status=400)

        rows = _rows(ch, f"""
            SELECT
                recommended_item_code, item_name, category, brand,
                rank, purchase_probability, confidence, lift,
                expected_margin, recommendation_reason, algorithm
            FROM mb_customer_recommendations
            WHERE customer_mobile = '{mobile}'
            ORDER BY rank ASC
            LIMIT 10
        """)

        # Also get customer purchase history
        hist_rows = _rows(ch, f"""
            SELECT DISTINCT s.item_code, m.item_name, m.category, m.brand
            FROM azure_invoice_report i
            JOIN azure_sales_report s ON i.invoice_no = s.invoice_no
            LEFT JOIN item_master m ON s.item_code = m.item_code
            WHERE i.customer_mobile = '{mobile}'
              AND s.qty > 0
            ORDER BY i.date DESC
            LIMIT 20
        """)

        history = [{'item_code': r[0], 'item_name': r[1] or r[0],
                    'category': r[2] or '', 'brand': r[3] or ''}
                   for r in hist_rows]

        recs = [{
            'item_code':            r[0],
            'item_name':            r[1],
            'category':             r[2],
            'brand':                r[3],
            'rank':                 int(r[4]),
            'purchase_probability': round(float(r[5]) * 100, 1),
            'confidence':           round(float(r[6]) * 100, 1),
            'lift':                 round(float(r[7]), 2),
            'expected_margin':      round(float(r[8]), 2),
            'reason':               r[9],
            'algorithm':            r[10],
        } for r in rows]

        return JsonResponse({
            'mobile':  mobile,
            'history': history,
            'recommendations': recs,
            'found': len(recs) > 0,
        })


# ─── Product Recommendations (FBT + Sequential) ───────────────────────────────

class MarketBasketProductRecsAPIView(LoginRequiredMixin, View):
    def get(self, request):
        ch = _ch()
        if not ch:
            return JsonResponse({'error': 'ClickHouse unavailable'}, status=503)

        item_code = request.GET.get('item_code', '').strip()
        if not item_code:
            return JsonResponse({'error': 'item_code required'}, status=400)

        # Frequently bought together
        fbt_rows = _rows(ch, f"""
            SELECT
                item_b, item_b_name, category_b, brand_b,
                support, confidence, lift, algorithm
            FROM mb_association_rules
            WHERE item_a = '{item_code}'
            ORDER BY lift DESC
            LIMIT 10
        """)

        # Sequential patterns
        seq_rows = _rows(ch, f"""
            SELECT
                next_item_code, next_item_name, days_window,
                probability, n_customers
            FROM mb_sequential_patterns
            WHERE anchor_item_code = '{item_code}'
            ORDER BY days_window, probability DESC
            LIMIT 20
        """)

        # Item info
        info_rows = _rows(ch, f"""
            SELECT item_name, brand, category, item_category, mop, mrp
            FROM item_master WHERE item_code = '{item_code}' LIMIT 1
        """)

        item_info = {}
        if info_rows:
            r = info_rows[0]
            item_info = {
                'item_code': item_code,
                'item_name': r[0], 'brand': r[1],
                'category': r[2], 'item_category': r[3],
                'mop': float(r[4]), 'mrp': float(r[5]),
            }

        return JsonResponse({
            'item_info': item_info,
            'frequently_bought_together': [{
                'item_code': r[0], 'item_name': r[1],
                'category': r[2], 'brand': r[3],
                'support':    round(float(r[4]) * 100, 3),
                'confidence': round(float(r[5]) * 100, 2),
                'lift':       round(float(r[6]), 3),
                'algorithm':  r[7],
            } for r in fbt_rows],
            'sequential': [{
                'next_item_code': r[0],
                'next_item_name': r[1],
                'days_window':    int(r[2]),
                'probability':    round(float(r[3]) * 100, 1),
                'n_customers':    int(r[4]),
            } for r in seq_rows],
        })


# ─── Sequential Patterns ──────────────────────────────────────────────────────

class MarketBasketSequentialAPIView(LoginRequiredMixin, View):
    def get(self, request):
        ch = _ch()
        if not ch:
            return JsonResponse({'error': 'ClickHouse unavailable'}, status=503)

        window = int(request.GET.get('days', 30))
        limit  = int(request.GET.get('limit', 50))

        rows = _rows(ch, f"""
            SELECT
                anchor_item_code, anchor_item_name,
                next_item_code, next_item_name,
                days_window, probability, n_customers
            FROM mb_sequential_patterns
            WHERE days_window = {window}
            ORDER BY probability DESC
            LIMIT {limit}
        """)

        return JsonResponse({'patterns': [{
            'anchor_code': r[0], 'anchor_name': r[1],
            'next_code':   r[2], 'next_name':   r[3],
            'days_window': int(r[4]),
            'probability': round(float(r[5]) * 100, 1),
            'n_customers': int(r[6]),
        } for r in rows]})


# ─── AI Insights ──────────────────────────────────────────────────────────────

class MarketBasketAIInsightsAPIView(LoginRequiredMixin, View):
    def get(self, request):
        ch = _ch()
        if not ch:
            return JsonResponse({'error': 'ClickHouse unavailable'}, status=503)

        insights = []

        # Top lift rule insight
        top_rule = _rows(ch, """
            SELECT item_a_name, item_b_name, lift, confidence
            FROM mb_association_rules
            ORDER BY lift DESC LIMIT 1
        """)
        if top_rule:
            r = top_rule[0]
            insights.append({
                'type': 'association',
                'icon': 'bi-link-45deg',
                'color': '#0891b2',
                'title': 'Strongest Product Association',
                'text': f'"{r[0]}" and "{r[1]}" have a very strong association with a Lift of {float(r[2]):.1f}x and {float(r[3])*100:.0f}% confidence. Customers who buy {r[0]} are {float(r[2]):.1f}x more likely to also buy {r[1]}.',
            })

        # Biggest missed opportunity
        top_opp = _rows(ch, """
            SELECT product_a_name, product_b_name, gap, missed_revenue, missed_margin
            FROM mb_cross_sell_opportunities
            ORDER BY missed_revenue DESC LIMIT 1
        """)
        if top_opp:
            r = top_opp[0]
            insights.append({
                'type': 'opportunity',
                'icon': 'bi-cash-stack',
                'color': '#dc2626',
                'title': 'Biggest Missed Cross-Sell Revenue',
                'text': f'The "{r[0]}" → "{r[1]}" cross-sell has the largest gap of {float(r[2])*100:.1f}%. Closing this gap could generate ₹{float(r[3]):,.0f} additional revenue and ₹{float(r[4]):,.0f} margin.',
            })

        # Underperforming branch
        branch_data = _rows(ch, """
            SELECT branch_name, attach_rate
            FROM mb_branch_performance
            ORDER BY computed_at DESC
            LIMIT 100
        """)
        if len(branch_data) > 1:
            rates = [float(r[1]) for r in branch_data]
            avg_rate = sum(rates) / len(rates)
            worst = min(branch_data, key=lambda x: float(x[1]))
            best  = max(branch_data, key=lambda x: float(x[1]))
            insights.append({
                'type': 'branch',
                'icon': 'bi-shop',
                'color': '#d97706',
                'title': 'Branch Cross-Sell Performance Gap',
                'text': f'"{worst[0]}" has an accessory attach rate of {float(worst[1])*100:.1f}%, significantly below the company average of {avg_rate*100:.1f}%. "{best[0]}" is the top performer at {float(best[1])*100:.1f}%. Sharing best practices could lift overall performance.',
            })

        # Top sequential pattern
        seq = _rows(ch, """
            SELECT anchor_item_name, next_item_name, days_window, probability, n_customers
            FROM mb_sequential_patterns
            WHERE days_window = 30
            ORDER BY n_customers DESC LIMIT 1
        """)
        if seq:
            r = seq[0]
            insights.append({
                'type': 'sequential',
                'icon': 'bi-arrow-right-circle',
                'color': '#7c3aed',
                'title': 'Strong Sequential Purchase Pattern',
                'text': f'{int(r[4]):,} customers who bought "{r[0]}" went on to purchase "{r[1]}" within {int(r[2])} days — a {float(r[3])*100:.0f}% follow-up rate. Targeted follow-up messages after the anchor purchase could capture this revenue.',
            })

        # KPI summary
        kpi = _rows(ch, "SELECT missed_revenue, missed_margin, total_opportunities FROM mb_basket_kpis ORDER BY computed_at DESC LIMIT 1")
        if kpi:
            r = kpi[0]
            insights.append({
                'type': 'summary',
                'icon': 'bi-graph-up-arrow',
                'color': '#059669',
                'title': 'Total Cross-Sell Opportunity',
                'text': f'Analysis identified {int(r[2]):,} cross-sell opportunities with a combined missed revenue potential of ₹{float(r[0]):,.0f} and margin potential of ₹{float(r[1]):,.0f}. Addressing even 20% of these gaps could transform business performance.',
            })

        return JsonResponse({'insights': insights})


# ─── Precompute Trigger (admin only) ──────────────────────────────────────────

class MarketBasketPrecomputeAPIView(LoginRequiredMixin, View):
    def post(self, request):
        if not request.user.is_superuser and request.user.username != 'mygadmin':
            return JsonResponse({'error': 'Permission denied'}, status=403)

        quick = request.GET.get('quick', 'false').lower() == 'true'

        import threading
        def run():
            try:
                from market_basket.engine import run_full_precompute
                run_full_precompute(quick=quick)
            except Exception as e:
                logger.error(f"[MB Precompute] Error: {e}")

        t = threading.Thread(target=run, daemon=True)
        t.start()

        return JsonResponse({
            'status': 'started',
            'message': f'Precomputation started in background (quick={quick}). Check back in a few minutes.',
        })
