import json
from django.views import View
from django.shortcuts import render
from django.contrib.auth.mixins import LoginRequiredMixin
from django.views.generic import TemplateView

try:
    from analytics.clickhouse_service import get_ch_client
    _CH_AVAILABLE = True
except Exception:
    _CH_AVAILABLE = False


def _ch():
    return get_ch_client()


def fmt_cr(val):
    """Format a rupee value as Cr/L/K string."""
    v = float(val or 0)
    if v >= 1e7:
        return f"{v/1e7:.1f} Cr"
    if v >= 1e5:
        return f"{v/1e5:.1f} L"
    if v >= 1e3:
        return f"{v/1e3:.1f} K"
    return str(int(v))


# ─────────────────────────────────────────────────
# 1. Executive Dashboard
# ─────────────────────────────────────────────────
class ExecutiveDashboardView(LoginRequiredMixin, View):
    template_name = 'dashboard/portal/executive_dashboard.html'

    def get(self, request):
        ctx = {}
        try:
            if _CH_AVAILABLE:
                ch = _ch()
                row = ch.query("""
                    SELECT sum(invoice_total), count(distinct customer_mobile),
                           count(distinct invoice_no),
                           sum(invoice_total)/count(distinct invoice_no),
                           count(distinct branch)
                    FROM azure_invoice_report
                    WHERE toDate(date) >= today() - 30 AND invoice_total > 0
                """).result_rows[0]
                ctx.update({
                    'sales_curr': round(float(row[0] or 0), 2),
                    'custs_curr': int(row[1] or 0),
                    'inv_curr': int(row[2] or 0),
                    'aov': round(float(row[3] or 0), 2),
                    'branches_count': int(row[4] or 0),
                })
                prev = ch.query("""
                    SELECT sum(invoice_total), count(distinct customer_mobile), count(distinct invoice_no)
                    FROM azure_invoice_report
                    WHERE toDate(date) >= today() - 60 AND toDate(date) < today() - 30 AND invoice_total > 0
                """).result_rows[0]
                ctx.update({'sales_prev': round(float(prev[0] or 0), 2), 'custs_prev': int(prev[1] or 0), 'inv_prev': int(prev[2] or 0)})

                nc = ch.query("""
                    SELECT count(distinct customer_mobile) FROM azure_invoice_report
                    WHERE toDate(date) >= today() - 30
                    AND customer_mobile NOT IN (SELECT distinct customer_mobile FROM azure_invoice_report WHERE toDate(date) < today() - 30)
                """).result_rows[0][0]
                ctx['new_custs'] = int(nc or 0)

                trend = ch.query("""
                    SELECT formatDateTime(toStartOfMonth(date),'%b %Y') as m,
                           sum(invoice_total), count(distinct invoice_no)
                    FROM azure_invoice_report
                    WHERE toDate(date) >= today() - 395 AND invoice_total > 0
                    GROUP BY m ORDER BY min(date) ASC
                """).result_rows
                ctx['trend_labels']   = json.dumps([r[0] for r in trend])
                ctx['trend_values']   = json.dumps([round(float(r[1] or 0), 2) for r in trend])
                ctx['trend_invoices'] = json.dumps([int(r[2] or 0) for r in trend])

                cats = ch.query("""
                    SELECT i.product, sum(s.sold_price * s.qty) as rev
                    FROM azure_sales_report s JOIN item_master i ON s.item_code = i.item_code
                    WHERE i.product IN ('MOBILE','TV','AIR CONDITIONER','WASHING MACHINES','REFRIGERATORS','LAPTOP')
                    AND toDate(s.date) >= today() - 30
                    GROUP BY i.product ORDER BY rev DESC
                """).result_rows
                ctx['cat_labels'] = json.dumps([r[0] for r in cats])
                ctx['cat_values'] = json.dumps([round(float(r[1] or 0), 2) for r in cats])

                branches = ch.query("""
                    SELECT branch, sum(invoice_total) as rev, count(distinct invoice_no)
                    FROM azure_invoice_report
                    WHERE toDate(date) >= today() - 30 AND invoice_total > 0
                    GROUP BY branch ORDER BY rev DESC LIMIT 5
                """).result_rows
                ctx['branch_names'] = json.dumps([r[0] for r in branches])
                ctx['branch_revs']  = json.dumps([round(float(r[1] or 0), 2) for r in branches])
                ctx['branch_invs']  = json.dumps([int(r[2] or 0) for r in branches])
        except Exception as e:
            for k in ['trend_labels','trend_values','trend_invoices','cat_labels','cat_values','branch_names','branch_revs','branch_invs']:
                ctx.setdefault(k, '[]')
        return render(request, self.template_name, ctx)


# ─────────────────────────────────────────────────
# 2. Customer Intelligence
# ─────────────────────────────────────────────────
class CustomerIntelligenceView(LoginRequiredMixin, View):
    template_name = 'dashboard/portal/customer_intelligence.html'

    def get(self, request):
        ctx = {}
        try:
            if _CH_AVAILABLE:
                ch = _ch()
                row = ch.query("""
                    SELECT count(distinct customer_mobile),
                           countIf(distinct customer_mobile, toDate(date) >= today() - 90),
                           countIf(distinct customer_mobile, toDate(date) < today() - 90)
                    FROM azure_invoice_report WHERE invoice_total > 0
                """).result_rows[0]
                ctx.update({'total_customers': int(row[0] or 0), 'active_customers': int(row[1] or 0), 'dormant_customers': int(row[2] or 0)})

                nc = ch.query("""
                    SELECT count(distinct customer_mobile) FROM azure_invoice_report
                    WHERE toDate(date) >= today() - 30
                    AND customer_mobile NOT IN (SELECT distinct customer_mobile FROM azure_invoice_report WHERE toDate(date) < today() - 30)
                """).result_rows[0][0]
                ctx['new_customers'] = int(nc or 0)

                avg = ch.query("""
                    SELECT avg(total_spend), avg(freq), avg(aov) FROM (
                        SELECT customer_mobile, sum(invoice_total) as total_spend,
                               count(distinct invoice_no) as freq,
                               sum(invoice_total)/count(distinct invoice_no) as aov
                        FROM azure_invoice_report WHERE invoice_total > 0 GROUP BY customer_mobile)
                """).result_rows[0]
                ctx.update({'avg_clv': round(float(avg[0] or 0), 2), 'avg_freq': round(float(avg[1] or 0), 1), 'avg_aov': round(float(avg[2] or 0), 2)})

                repeat = ch.query("""
                    SELECT countIf(freq >= 2) / count(*) * 100 FROM (
                        SELECT customer_mobile, count(distinct invoice_no) as freq
                        FROM azure_invoice_report WHERE invoice_total > 0 GROUP BY customer_mobile)
                """).result_rows[0][0]
                ctx['repeat_rate'] = round(float(repeat or 0), 1)

                monthly = ch.query("""
                    SELECT formatDateTime(toStartOfMonth(date),'%b %Y') as m,
                           count(distinct customer_mobile), sum(invoice_total)
                    FROM azure_invoice_report WHERE toDate(date) >= today() - 365 AND invoice_total > 0
                    GROUP BY m ORDER BY min(date) ASC
                """).result_rows
                ctx['monthly_labels']  = json.dumps([r[0] for r in monthly])
                ctx['monthly_custs']   = json.dumps([int(r[1] or 0) for r in monthly])
                ctx['monthly_revenue'] = json.dumps([round(float(r[2] or 0) / 100000, 2) for r in monthly])

                top = ch.query("""
                    SELECT customer_mobile, sum(invoice_total) as spend,
                           count(distinct invoice_no) as visits, max(toDate(date)) as last_visit
                    FROM azure_invoice_report WHERE invoice_total > 0 AND length(customer_mobile) >= 8
                    GROUP BY customer_mobile ORDER BY spend DESC LIMIT 10
                """).result_rows
                ctx['top_customers'] = json.dumps([{
                    'mobile': r[0][-4:].rjust(10, '*'), 'spend': round(float(r[1] or 0), 2),
                    'visits': int(r[2] or 0), 'last_visit': str(r[3])
                } for r in top])
        except Exception as e:
            ctx['error'] = str(e)
            for k in ['monthly_labels','monthly_custs','monthly_revenue','top_customers']:
                ctx.setdefault(k, '[]')
        return render(request, self.template_name, ctx)


# ─────────────────────────────────────────────────
# 3. Customer Segmentation
# ─────────────────────────────────────────────────
class CustomerSegmentationView(LoginRequiredMixin, View):
    template_name = 'dashboard/portal/customer_segmentation.html'

    def get(self, request):
        ctx = {}
        try:
            if _CH_AVAILABLE:
                ch = _ch()
                rfm = ch.query("""
                    SELECT
                        countIf(recency <= 30 AND freq >= 5 AND spend >= 100000) as champions,
                        countIf(recency <= 60 AND freq >= 3) as loyal,
                        countIf(recency <= 30 AND freq <= 2) as new_custs,
                        countIf(recency > 60 AND recency <= 120 AND freq >= 2) as at_risk,
                        countIf(recency > 120 AND recency <= 180) as dormant,
                        countIf(recency > 180) as lost
                    FROM (
                        SELECT customer_mobile,
                               today() - max(toDate(date)) as recency,
                               count(distinct invoice_no) as freq,
                               sum(invoice_total) as spend
                        FROM azure_invoice_report WHERE invoice_total > 0
                        GROUP BY customer_mobile
                    )
                """).result_rows[0]
                ctx.update({
                    'champions': int(rfm[0] or 0),
                    'loyal': int(rfm[1] or 0),
                    'new_seg': int(rfm[2] or 0),
                    'at_risk': int(rfm[3] or 0),
                    'dormant_seg': int(rfm[4] or 0),
                    'lost': int(rfm[5] or 0),
                })
                total = sum([int(x or 0) for x in rfm]) or 1
                ctx['seg_labels'] = json.dumps(['Champions','Loyal','New','At Risk','Dormant','Lost'])
                ctx['seg_values'] = json.dumps([int(x or 0) for x in rfm])
                ctx['seg_pcts']   = json.dumps([round(int(x or 0)/total*100, 1) for x in rfm])
        except Exception as e:
            ctx['error'] = str(e)
            for k in ['seg_labels','seg_values','seg_pcts']:
                ctx.setdefault(k, '[]')
        return render(request, self.template_name, ctx)


# ─────────────────────────────────────────────────
# 4. Sales Intelligence
# ─────────────────────────────────────────────────
class SalesIntelligenceView(LoginRequiredMixin, View):
    template_name = 'dashboard/portal/sales_intelligence.html'

    def get(self, request):
        ctx = {}
        try:
            if _CH_AVAILABLE:
                ch = _ch()
                row = ch.query("""
                    SELECT sum(invoice_total), count(distinct invoice_no),
                           sum(invoice_total)/count(distinct invoice_no),
                           count(distinct customer_mobile), count(distinct branch)
                    FROM azure_invoice_report
                    WHERE toDate(date) >= today() - 30 AND invoice_total > 0
                """).result_rows[0]
                ctx.update({
                    'sales_30d': round(float(row[0] or 0), 2),
                    'inv_30d': int(row[1] or 0),
                    'aov_30d': round(float(row[2] or 0), 2),
                    'custs_30d': int(row[3] or 0),
                    'branches_30d': int(row[4] or 0),
                })
                prev = ch.query("""
                    SELECT sum(invoice_total) FROM azure_invoice_report
                    WHERE toDate(date) >= today() - 60 AND toDate(date) < today() - 30 AND invoice_total > 0
                """).result_rows[0][0]
                ctx['sales_prev'] = round(float(prev or 0), 2)
                growth = ((ctx['sales_30d'] - ctx['sales_prev']) / ctx['sales_prev'] * 100) if ctx['sales_prev'] else 0
                ctx['growth_pct'] = round(growth, 1)

                cats = ch.query("""
                    SELECT i.product, sum(s.sold_price * s.qty) as rev, sum(s.qty) as qty
                    FROM azure_sales_report s JOIN item_master i ON s.item_code = i.item_code
                    WHERE i.product IN ('MOBILE','TV','AIR CONDITIONER','WASHING MACHINES','REFRIGERATORS','LAPTOP','TABLET')
                    AND toDate(s.date) >= today() - 30
                    GROUP BY i.product ORDER BY rev DESC
                """).result_rows
                ctx['cat_labels'] = json.dumps([r[0] for r in cats])
                ctx['cat_revs']   = json.dumps([round(float(r[1] or 0) / 100000, 2) for r in cats])
                ctx['cat_qtys']   = json.dumps([int(r[2] or 0) for r in cats])

                daily = ch.query("""
                    SELECT toDate(date) as d, sum(invoice_total)
                    FROM azure_invoice_report
                    WHERE toDate(date) >= today() - 30 AND invoice_total > 0
                    GROUP BY d ORDER BY d ASC
                """).result_rows
                ctx['daily_labels'] = json.dumps([str(r[0]) for r in daily])
                ctx['daily_values'] = json.dumps([round(float(r[1] or 0) / 100000, 2) for r in daily])

                branches = ch.query("""
                    SELECT branch, sum(invoice_total) as rev, count(distinct invoice_no),
                           count(distinct customer_mobile)
                    FROM azure_invoice_report
                    WHERE toDate(date) >= today() - 30 AND invoice_total > 0
                    GROUP BY branch ORDER BY rev DESC LIMIT 10
                """).result_rows
                ctx['branch_data'] = json.dumps([{
                    'branch': r[0], 'rev': round(float(r[1] or 0), 2),
                    'inv': int(r[2] or 0), 'custs': int(r[3] or 0)
                } for r in branches])
        except Exception as e:
            ctx['error'] = str(e)
            for k in ['cat_labels','cat_revs','cat_qtys','daily_labels','daily_values','branch_data']:
                ctx.setdefault(k, '[]')
        return render(request, self.template_name, ctx)


# ─────────────────────────────────────────────────
# 5. Product Intelligence
# ─────────────────────────────────────────────────
class ProductIntelligenceView(LoginRequiredMixin, View):
    template_name = 'dashboard/portal/product_intelligence.html'

    def get(self, request):
        ctx = {}
        try:
            if _CH_AVAILABLE:
                ch = _ch()
                top = ch.query("""
                    SELECT i.product, i.brand, sum(s.sold_price * s.qty) as rev,
                           sum(s.qty) as qty, count(distinct s.invoice_no) as inv_count
                    FROM azure_sales_report s JOIN item_master i ON s.item_code = i.item_code
                    WHERE i.product IN ('MOBILE','TV','AIR CONDITIONER','WASHING MACHINES','REFRIGERATORS','LAPTOP','TABLET')
                    AND toDate(s.date) >= today() - 30
                    GROUP BY i.product, i.brand ORDER BY rev DESC LIMIT 15
                """).result_rows
                ctx['products'] = json.dumps([{
                    'product': r[0], 'brand': r[1],
                    'rev': round(float(r[2] or 0), 2),
                    'qty': int(r[3] or 0),
                    'inv_count': int(r[4] or 0)
                } for r in top])

                # Total products sold
                totals = ch.query("""
                    SELECT count(distinct s.item_code), sum(s.qty), sum(s.sold_price * s.qty)
                    FROM azure_sales_report s
                    WHERE toDate(s.date) >= today() - 30
                """).result_rows[0]
                ctx.update({
                    'total_skus': int(totals[0] or 0),
                    'total_qty': int(totals[1] or 0),
                    'total_rev': round(float(totals[2] or 0), 2),
                })
        except Exception as e:
            ctx['error'] = str(e)
            ctx.setdefault('products', '[]')
        return render(request, self.template_name, ctx)


# ─────────────────────────────────────────────────
# 6. Branch Intelligence
# ─────────────────────────────────────────────────
class BranchIntelligenceView(LoginRequiredMixin, View):
    template_name = 'dashboard/portal/branch_intelligence.html'

    def get(self, request):
        ctx = {}
        try:
            if _CH_AVAILABLE:
                ch = _ch()
                branches = ch.query("""
                    SELECT branch, sum(invoice_total) as rev,
                           count(distinct invoice_no) as inv,
                           count(distinct customer_mobile) as custs,
                           sum(invoice_total)/count(distinct invoice_no) as aov
                    FROM azure_invoice_report
                    WHERE toDate(date) >= today() - 30 AND invoice_total > 0
                    GROUP BY branch ORDER BY rev DESC
                """).result_rows
                ctx['branches'] = json.dumps([{
                    'branch': r[0], 'rev': round(float(r[1] or 0), 2),
                    'inv': int(r[2] or 0), 'custs': int(r[3] or 0),
                    'aov': round(float(r[4] or 0), 2)
                } for r in branches])
                ctx['total_branches'] = len(branches)
                ctx['top_branch'] = branches[0][0] if branches else '—'
                ctx['top_branch_rev'] = round(float(branches[0][1] or 0), 2) if branches else 0

                prev = ch.query("""
                    SELECT branch, sum(invoice_total) FROM azure_invoice_report
                    WHERE toDate(date) >= today() - 60 AND toDate(date) < today() - 30 AND invoice_total > 0
                    GROUP BY branch
                """).result_rows
                prev_map = {r[0]: float(r[1] or 0) for r in prev}
                ctx['branches_with_growth'] = json.dumps([{
                    'branch': r[0], 'rev': round(float(r[1] or 0), 2),
                    'inv': int(r[2] or 0), 'custs': int(r[3] or 0),
                    'aov': round(float(r[4] or 0), 2),
                    'growth': round(((float(r[1] or 0) - prev_map.get(r[0], 0)) / prev_map.get(r[0], 1) * 100), 1) if prev_map.get(r[0]) else 0
                } for r in branches])
        except Exception as e:
            ctx['error'] = str(e)
            for k in ['branches','branches_with_growth']:
                ctx.setdefault(k, '[]')
        return render(request, self.template_name, ctx)


# ─────────────────────────────────────────────────
# Shell views for remaining modules
# ─────────────────────────────────────────────────
class RecommendationEngineView(LoginRequiredMixin, View):
    template_name = 'dashboard/portal/recommendation_engine.html'

    def get(self, request):
        ctx = {}
        try:
            if _CH_AVAILABLE:
                ch = _ch()
                pairs = ch.query("""
                    SELECT a.product as prod1, b.product as prod2, count(*) as pair_count
                    FROM (
                        SELECT s.invoice_no, i.product FROM azure_sales_report s
                        JOIN item_master i ON s.item_code = i.item_code
                        WHERE i.product IN ('MOBILE','TV','AIR CONDITIONER','WASHING MACHINES','REFRIGERATORS','LAPTOP','TABLET')
                        AND toDate(s.date) >= today() - 90
                    ) a
                    JOIN (
                        SELECT s.invoice_no, i.product FROM azure_sales_report s
                        JOIN item_master i ON s.item_code = i.item_code
                        WHERE i.product IN ('MOBILE','TV','AIR CONDITIONER','WASHING MACHINES','REFRIGERATORS','LAPTOP','TABLET')
                        AND toDate(s.date) >= today() - 90
                    ) b ON a.invoice_no = b.invoice_no AND a.product < b.product
                    GROUP BY prod1, prod2 ORDER BY pair_count DESC LIMIT 10
                """).result_rows
                ctx['pairs'] = json.dumps([{'prod1': r[0], 'prod2': r[1], 'count': int(r[2])} for r in pairs])
                ctx['total_pairs'] = len(pairs)
                ctx['top_pair'] = f"{pairs[0][0]} + {pairs[0][1]}" if pairs else '—'
                ctx['top_pair_count'] = int(pairs[0][2]) if pairs else 0

                top_cats = ch.query("""
                    SELECT i.product, sum(s.sold_price * s.qty) as rev, sum(s.qty) as qty
                    FROM azure_sales_report s JOIN item_master i ON s.item_code = i.item_code
                    WHERE i.product IN ('MOBILE','TV','AIR CONDITIONER','WASHING MACHINES','REFRIGERATORS','LAPTOP','TABLET')
                    AND toDate(s.date) >= today() - 30
                    GROUP BY i.product ORDER BY rev DESC LIMIT 7
                """).result_rows
                ctx['top_cats'] = json.dumps([{'product': r[0], 'rev': round(float(r[1] or 0), 2), 'qty': int(r[2] or 0)} for r in top_cats])
        except Exception as e:
            ctx['error'] = str(e)
            for k in ['pairs', 'top_cats']:
                ctx.setdefault(k, '[]')
        return render(request, self.template_name, ctx)


class InventoryIntelligenceView(LoginRequiredMixin, View):
    template_name = 'dashboard/portal/inventory_intelligence.html'

    def get(self, request):
        ctx = {}
        try:
            if _CH_AVAILABLE:
                ch = _ch()
                inv = ch.query("""
                    SELECT i.product, i.brand,
                           sum(s.qty) as sold_30d,
                           sum(s.sold_price * s.qty) as rev_30d,
                           count(distinct s.invoice_no) as inv_count
                    FROM azure_sales_report s JOIN item_master i ON s.item_code = i.item_code
                    WHERE toDate(s.date) >= today() - 30
                    AND i.product IN ('MOBILE','TV','AIR CONDITIONER','WASHING MACHINES','REFRIGERATORS','LAPTOP','TABLET')
                    GROUP BY i.product, i.brand ORDER BY sold_30d DESC LIMIT 20
                """).result_rows
                ctx['inventory'] = json.dumps([{
                    'product': r[0], 'brand': r[1] or '—',
                    'sold_30d': int(r[2] or 0),
                    'rev_30d': round(float(r[3] or 0), 2),
                    'inv_count': int(r[4] or 0)
                } for r in inv])

                totals = ch.query("""
                    SELECT sum(s.qty) as total_units, sum(s.sold_price * s.qty) as total_rev,
                           count(distinct s.item_code) as total_skus
                    FROM azure_sales_report s
                    WHERE toDate(s.date) >= today() - 30
                """).result_rows[0]
                ctx['total_units'] = int(totals[0] or 0)
                ctx['total_rev'] = round(float(totals[1] or 0), 2)
                ctx['total_skus'] = int(totals[2] or 0)

                # Velocity by category (sold per day)
                velocity = ch.query("""
                    SELECT i.product,
                           sum(s.qty) / 30.0 as units_per_day,
                           sum(s.qty) as total_units
                    FROM azure_sales_report s JOIN item_master i ON s.item_code = i.item_code
                    WHERE toDate(s.date) >= today() - 30
                    AND i.product IN ('MOBILE','TV','AIR CONDITIONER','WASHING MACHINES','REFRIGERATORS','LAPTOP','TABLET')
                    GROUP BY i.product ORDER BY units_per_day DESC
                """).result_rows
                ctx['velocity_labels'] = json.dumps([r[0] for r in velocity])
                ctx['velocity_values'] = json.dumps([round(float(r[1] or 0), 1) for r in velocity])
        except Exception as e:
            ctx['error'] = str(e)
            for k in ['inventory', 'velocity_labels', 'velocity_values']:
                ctx.setdefault(k, '[]')
        return render(request, self.template_name, ctx)


class PromotionIntelligenceView(LoginRequiredMixin, View):
    template_name = 'dashboard/portal/promotion_intelligence.html'

    def get(self, request):
        ctx = {}
        try:
            if _CH_AVAILABLE:
                ch = _ch()
                promo = ch.query("""
                    SELECT
                        count(distinct invoice_no) as total_inv,
                        countIf(discount > 0) as discounted_inv,
                        sum(discount) as total_discount,
                        avg(discount) as avg_discount,
                        sum(invoice_total) as total_rev,
                        sum(discount) / (sum(invoice_total) + sum(discount)) * 100 as discount_pct
                    FROM azure_invoice_report
                    WHERE toDate(date) >= today() - 30 AND invoice_total > 0
                """).result_rows[0]
                ctx.update({
                    'total_inv': int(promo[0] or 0),
                    'discounted_inv': int(promo[1] or 0),
                    'total_discount': round(float(promo[2] or 0), 2),
                    'avg_discount': round(float(promo[3] or 0), 2),
                    'total_rev': round(float(promo[4] or 0), 2),
                    'discount_pct': round(float(promo[5] or 0), 2),
                })
                ctx['discount_rate'] = round(int(promo[1] or 0) / int(promo[0] or 1) * 100, 1)

                disc_branch = ch.query("""
                    SELECT branch, sum(discount) as total_disc,
                           count(distinct invoice_no) as inv_count,
                           avg(discount) as avg_disc
                    FROM azure_invoice_report
                    WHERE toDate(date) >= today() - 30 AND discount > 0
                    GROUP BY branch ORDER BY total_disc DESC LIMIT 10
                """).result_rows
                ctx['disc_branches'] = json.dumps([{
                    'branch': r[0], 'total_disc': round(float(r[1] or 0), 2),
                    'inv_count': int(r[2] or 0), 'avg_disc': round(float(r[3] or 0), 2)
                } for r in disc_branch])

                daily_disc = ch.query("""
                    SELECT toDate(date) as d, sum(discount) as disc, sum(invoice_total) as rev
                    FROM azure_invoice_report
                    WHERE toDate(date) >= today() - 30 AND invoice_total > 0
                    GROUP BY d ORDER BY d ASC
                """).result_rows
                ctx['daily_disc_labels'] = json.dumps([str(r[0]) for r in daily_disc])
                ctx['daily_disc_values'] = json.dumps([round(float(r[1] or 0) / 1000, 2) for r in daily_disc])
                ctx['daily_rev_values']  = json.dumps([round(float(r[2] or 0) / 100000, 2) for r in daily_disc])
        except Exception as e:
            ctx['error'] = str(e)
            for k in ['disc_branches', 'daily_disc_labels', 'daily_disc_values', 'daily_rev_values']:
                ctx.setdefault(k, '[]')
        return render(request, self.template_name, ctx)


class AIInsightsCenterView(LoginRequiredMixin, View):
    template_name = 'dashboard/portal/ai_insights_center.html'

    def get(self, request):
        ctx = {}
        try:
            if _CH_AVAILABLE:
                ch = _ch()
                # Revenue anomalies
                anomalies = ch.query("""
                    SELECT d, rev, avg_rev, round(rev / avg_rev, 2) as ratio
                    FROM (
                        SELECT toDate(date) as d, sum(invoice_total) as rev,
                               avg(sum(invoice_total)) OVER () as avg_rev
                        FROM azure_invoice_report WHERE toDate(date) >= today() - 90 AND invoice_total > 0
                        GROUP BY d
                    )
                    WHERE rev > avg_rev * 1.5
                    ORDER BY ratio DESC LIMIT 10
                """).result_rows
                ctx['anomalies'] = json.dumps([{'date': str(r[0]), 'rev': round(float(r[1] or 0), 2),
                    'avg': round(float(r[2] or 0), 2), 'ratio': float(r[3] or 0)} for r in anomalies])

                # Top growth branches
                growth = ch.query("""
                    SELECT curr.branch,
                           curr.rev as curr_rev, prev.rev as prev_rev,
                           round((curr.rev - prev.rev) / prev.rev * 100, 1) as growth_pct
                    FROM (
                        SELECT branch, sum(invoice_total) as rev FROM azure_invoice_report
                        WHERE toDate(date) >= today() - 30 AND invoice_total > 0 GROUP BY branch
                    ) curr
                    JOIN (
                        SELECT branch, sum(invoice_total) as rev FROM azure_invoice_report
                        WHERE toDate(date) >= today() - 60 AND toDate(date) < today() - 30 AND invoice_total > 0 GROUP BY branch
                    ) prev ON curr.branch = prev.branch
                    ORDER BY growth_pct DESC LIMIT 5
                """).result_rows
                ctx['growth_branches'] = json.dumps([{'branch': r[0], 'curr': round(float(r[1] or 0), 2),
                    'prev': round(float(r[2] or 0), 2), 'growth': float(r[3] or 0)} for r in growth])

                # Declining branches
                decline = ch.query("""
                    SELECT curr.branch,
                           curr.rev as curr_rev, prev.rev as prev_rev,
                           round((curr.rev - prev.rev) / prev.rev * 100, 1) as growth_pct
                    FROM (
                        SELECT branch, sum(invoice_total) as rev FROM azure_invoice_report
                        WHERE toDate(date) >= today() - 30 AND invoice_total > 0 GROUP BY branch
                    ) curr
                    JOIN (
                        SELECT branch, sum(invoice_total) as rev FROM azure_invoice_report
                        WHERE toDate(date) >= today() - 60 AND toDate(date) < today() - 30 AND invoice_total > 0 GROUP BY branch
                    ) prev ON curr.branch = prev.branch
                    ORDER BY growth_pct ASC LIMIT 5
                """).result_rows
                ctx['decline_branches'] = json.dumps([{'branch': r[0], 'curr': round(float(r[1] or 0), 2),
                    'prev': round(float(r[2] or 0), 2), 'growth': float(r[3] or 0)} for r in decline])

                # Overall summary KPIs for insights
                kpi = ch.query("""
                    SELECT sum(invoice_total), count(distinct customer_mobile),
                           count(distinct invoice_no)
                    FROM azure_invoice_report WHERE toDate(date) >= today() - 30 AND invoice_total > 0
                """).result_rows[0]
                ctx.update({'ins_revenue': round(float(kpi[0] or 0), 2), 'ins_custs': int(kpi[1] or 0), 'ins_invoices': int(kpi[2] or 0)})
                ctx['anomaly_count'] = len(anomalies)
        except Exception as e:
            ctx['error'] = str(e)
            for k in ['anomalies', 'growth_branches', 'decline_branches']:
                ctx.setdefault(k, '[]')
        return render(request, self.template_name, ctx)


class ReportsExportsView(LoginRequiredMixin, View):
    template_name = 'dashboard/portal/reports_exports.html'

    def get(self, request):
        ctx = {}
        try:
            if _CH_AVAILABLE:
                ch = _ch()
                summary = ch.query("""
                    SELECT sum(invoice_total), count(distinct customer_mobile), count(distinct invoice_no),
                           count(distinct branch), min(toDate(date)), max(toDate(date))
                    FROM azure_invoice_report WHERE invoice_total > 0
                """).result_rows[0]
                ctx.update({
                    'rpt_total_rev': round(float(summary[0] or 0), 2),
                    'rpt_total_custs': int(summary[1] or 0),
                    'rpt_total_inv': int(summary[2] or 0),
                    'rpt_branches': int(summary[3] or 0),
                    'rpt_date_from': str(summary[4]),
                    'rpt_date_to': str(summary[5]),
                })
        except Exception as e:
            ctx['error'] = str(e)
        ctx['reports_list'] = [
            {'title': 'Executive Summary', 'icon': 'bi-file-earmark-text-fill', 'color': '#1e40af', 'desc': 'Revenue, customers, AOV, growth and top-branch overview.'},
            {'title': 'Customer Intelligence', 'icon': 'bi-people-fill', 'color': '#15803d', 'desc': 'CLV, repeat rate, top customers and monthly cohort analysis.'},
            {'title': 'Sales Intelligence', 'icon': 'bi-graph-up-arrow', 'color': '#f97316', 'desc': 'Daily sales, category breakdown and branch-level performance.'},
            {'title': 'Product Intelligence', 'icon': 'bi-box-seam-fill', 'color': '#b45309', 'desc': 'Top SKUs, category revenue mix and brand performance.'},
            {'title': 'Customer Segmentation', 'icon': 'bi-pie-chart-fill', 'color': '#7c3aed', 'desc': 'RFM-based Champion, Loyal, At-Risk and Dormant segments.'},
            {'title': 'Branch Performance', 'icon': 'bi-shop', 'color': '#6366f1', 'desc': 'Branch rankings, growth rates and month-on-month comparison.'},
            {'title': 'Promotion & Discount', 'icon': 'bi-tags-fill', 'color': '#ef4444', 'desc': 'Total discounts, discount rate by branch and daily trends.'},
            {'title': 'Recommendation Engine', 'icon': 'bi-stars', 'color': '#d97706', 'desc': 'Product pair affinities and cross-sell opportunity report.'},
            {'title': 'Inventory Velocity', 'icon': 'bi-boxes', 'color': '#0e7490', 'desc': 'Daily velocity, brand-level stock movement and SKU analysis.'},
        ]
        return render(request, self.template_name, ctx)


class DataManagementView(LoginRequiredMixin, View):
    template_name = 'dashboard/portal/data_management.html'

    def get(self, request):
        ctx = {}
        try:
            if _CH_AVAILABLE:
                ch = _ch()
                quality = ch.query("""
                    SELECT count(*) as total_rows, min(toDate(date)) as min_date,
                           max(toDate(date)) as max_date,
                           countIf(customer_mobile = '') as missing_mobile,
                           countIf(invoice_total <= 0) as zero_inv,
                           count(distinct branch) as branches
                    FROM azure_invoice_report
                """).result_rows[0]
                ctx.update({
                    'total_records': int(quality[0] or 0),
                    'date_from': str(quality[1]),
                    'date_to': str(quality[2]),
                    'missing_mobile': int(quality[3] or 0),
                    'zero_invoices': int(quality[4] or 0),
                    'total_branches': int(quality[5] or 0),
                })
                total = int(quality[0] or 1)
                ctx['quality_score'] = round((1 - (int(quality[3] or 0) + int(quality[4] or 0)) / total) * 100, 1)

                sales_count = ch.query("SELECT count(*) FROM azure_sales_report").result_rows[0][0]
                ctx['sales_records'] = int(sales_count or 0)
                item_count = ch.query("SELECT count(*) FROM item_master").result_rows[0][0]
                ctx['item_records'] = int(item_count or 0)
        except Exception as e:
            ctx['error'] = str(e)
        return render(request, self.template_name, ctx)


class ModelManagementView(LoginRequiredMixin, View):
    template_name = 'dashboard/portal/model_management.html'

    def get(self, request):
        ctx = {
            'models': [
                {'name': 'LSTM Sales Forecaster', 'status': 'Active', 'accuracy': '91.2%', 'last_trained': 'Aug 15, 2026', 'type': 'Deep Learning'},
                {'name': 'Prophet Trend Model', 'status': 'Active', 'accuracy': '87.5%', 'last_trained': 'Aug 15, 2026', 'type': 'Time Series'},
                {'name': 'XGBoost Revenue Model', 'status': 'Active', 'accuracy': '89.1%', 'last_trained': 'Aug 14, 2026', 'type': 'Gradient Boosting'},
                {'name': 'K-Means Customer Segmentation', 'status': 'Active', 'accuracy': '—', 'last_trained': 'Aug 12, 2026', 'type': 'Clustering'},
                {'name': 'Apriori Association Rules', 'status': 'Active', 'accuracy': '—', 'last_trained': 'Aug 10, 2026', 'type': 'Association Mining'},
                {'name': 'Collaborative Filter (Recos)', 'status': 'Training', 'accuracy': '84.3%', 'last_trained': 'Aug 8, 2026', 'type': 'Collaborative Filtering'},
            ]
        }
        return render(request, self.template_name, ctx)


class SettingsPortalView(LoginRequiredMixin, TemplateView):
    template_name = 'dashboard/portal/settings_portal.html'

