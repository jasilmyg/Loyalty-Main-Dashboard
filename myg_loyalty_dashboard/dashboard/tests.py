import io
from types import SimpleNamespace
from unittest.mock import patch

from django.core.cache import cache
from django.template.loader import get_template
from django.test import RequestFactory, SimpleTestCase
from openpyxl import load_workbook

from analytics.starrocks_service import translate_ch_to_sr_sql
from dashboard.dashboard_api_logic import (
    GLOBAL_PRICE_SLABS,
    _build_price_slab_multiif_sql,
    build_api_response,
    build_price_slab_filter_sql,
    generate_dashboard_excel,
    get_price_slab_config_dict,
    parse_price_slab_tokens,
)
from dashboard.growth_degrowth_logic import build_growth_degrowth_response


class _FakeClickHouseClient:
    """Minimal mock ClickHouse client recording executed SQL queries."""

    def __init__(self, period_rows=None, ps_rows=None, cust_rows=None, gd_rows=None, nr_rows=None):
        self.queries = []
        self.period_rows = period_rows or []
        self.ps_rows = ps_rows or []
        self.cust_rows = cust_rows or []
        self.gd_rows = gd_rows or []
        self.nr_rows = nr_rows or []

    def query(self, sql):
        self.queries.append(sql)
        if 'FROM branch_master' in sql and 'branch_name' in sql:
            return SimpleNamespace(result_rows=[('COK', 'KOCHI'), ('TVM', 'TRIVANDRUM')])
        if 'AS slab_no' in sql:
            return SimpleNamespace(result_rows=self.ps_rows)
        if 'AS true_unique_cust' in sql:
            return SimpleNamespace(result_rows=self.cust_rows)
        if 'AS first_purchase_ever' in sql:
            return SimpleNamespace(result_rows=self.nr_rows)
        if 'AS sale_value' in sql:
            return SimpleNamespace(result_rows=self.gd_rows)
        if 'AS inv_count' in sql:
            return SimpleNamespace(result_rows=self.period_rows)
        return SimpleNamespace(result_rows=[])


class PriceSegmentTests(SimpleTestCase):
    def setUp(self):
        cache.clear()
        self.factory = RequestFactory()

    def test_price_slab_config_has_split_slabs_and_all_key(self):
        cfg = get_price_slab_config_dict()
        self.assertIn('ALL', cfg)
        slabs = cfg['ALL']
        self.assertEqual(len(slabs), 10)

        self.assertEqual(slabs[0]['slab_no'], 1)
        self.assertEqual(slabs[0]['segment'], 'Basic / Essentials')
        self.assertEqual(slabs[0]['range'], '₹0 – ₹2,999')
        self.assertEqual(slabs[0]['min_price'], 0)
        self.assertEqual(slabs[0]['max_price'], 3000)
        self.assertTrue(slabs[0]['min_inclusive'])
        self.assertFalse(slabs[0]['max_inclusive'])

        self.assertEqual(slabs[1]['slab_no'], 2)
        self.assertEqual(slabs[1]['segment'], 'Entry-Level')
        self.assertEqual(slabs[1]['range'], '₹3,000 – ₹9,999')
        self.assertEqual(slabs[1]['min_price'], 3000)
        self.assertEqual(slabs[1]['max_price'], 10000)
        self.assertTrue(slabs[1]['min_inclusive'])
        self.assertFalse(slabs[1]['max_inclusive'])

        self.assertEqual(slabs[2]['slab_no'], 3)
        self.assertEqual(slabs[2]['segment'], 'Budget / Mass Market')
        self.assertEqual(slabs[2]['range'], '₹10,000 – ₹19,999')
        self.assertTrue(slabs[2]['min_inclusive'])
        self.assertFalse(slabs[2]['max_inclusive'])

        self.assertEqual(slabs[9]['slab_no'], 10)
        self.assertEqual(slabs[9]['segment'], 'Ultra-Luxury')
        self.assertIsNone(slabs[9]['max_price'])

    def test_parse_price_slab_tokens_supports_global_and_prefixed(self):
        self.assertEqual(parse_price_slab_tokens('1,2'), [(None, 1), (None, 2)])
        self.assertEqual(parse_price_slab_tokens('ALL:1, ALL:10'), [(None, 1), (None, 10)])
        self.assertEqual(parse_price_slab_tokens('MOBILE:2, TV:4'), [('MOBILE', 2), ('TV', 4)])
        self.assertEqual(parse_price_slab_tokens('0, 11, bad, 3'), [(None, 3)])
        self.assertEqual(parse_price_slab_tokens(''), [])

    def test_build_price_slab_filter_sql_applies_to_all_products(self):
        sql_s1 = build_price_slab_filter_sql('1')
        self.assertIsNotNone(sql_s1)
        self.assertNotIn("m.product", sql_s1)
        self.assertIn("< 3000", sql_s1)

        sql_s2 = build_price_slab_filter_sql('2')
        self.assertNotIn("m.product", sql_s2)
        self.assertIn(">= 3000", sql_s2)
        self.assertIn("< 10000", sql_s2)

        sql_s3 = build_price_slab_filter_sql('3')
        self.assertIn(">= 10000", sql_s3)
        self.assertIn("< 20000", sql_s3)

        sql_s10 = build_price_slab_filter_sql('10')
        self.assertIn(">= 150000", sql_s10)

        sql_multi = build_price_slab_filter_sql('1,2')
        self.assertIn(" OR ", sql_multi)
        self.assertNotIn("MOBILE", sql_multi)

        sql_prod_scoped = build_price_slab_filter_sql('LAPTOP:4')
        self.assertIn("= 'LAPTOP'", sql_prod_scoped)
        self.assertIn(">= 20000", sql_prod_scoped)
        self.assertIn("< 35000", sql_prod_scoped)

    def test_multiif_sql_boundaries_and_starrocks_translation(self):
        expr = _build_price_slab_multiif_sql("unit_price")
        self.assertTrue(expr.startswith("multiIf("))
        self.assertIn("unit_price < 3000, 1", expr)
        self.assertIn("unit_price < 10000, 2", expr)
        self.assertIn("unit_price < 20000, 3", expr)
        self.assertTrue(expr.endswith(", 10)"))

        sr_sql = translate_ch_to_sr_sql(f"SELECT {expr} AS slab_no FROM t")
        self.assertIn("CASE WHEN", sr_sql)
        self.assertIn("ELSE 10 END", sr_sql)

        # Verify Python boundary equivalence across all slab transitions
        def eval_slab(price):
            for s_no, _seg, _rng, mn, mx, mn_inc, mx_inc in GLOBAL_PRICE_SLABS:
                low_ok = (price >= mn) if mn_inc else (price > mn)
                high_ok = True if mx is None else ((price <= mx) if mx_inc else (price < mx))
                if low_ok and high_ok:
                    return s_no
            return None

        expected_cases = [
            (0, 1), (1500, 1), (2999, 1), (2999.99, 1),
            (3000, 2), (3001, 2), (9999, 2), (9999.99, 2),
            (10000, 3), (10001, 3), (19999, 3),
            (20000, 4), (34999, 4),
            (35000, 5), (50000, 6), (60000, 7),
            (80000, 8), (100000, 9), (150000, 10), (250000, 10),
        ]
        for price, expected_slab in expected_cases:
            self.assertEqual(eval_slab(price), expected_slab, f"Failed for price={price}")

    def test_build_api_response_and_excel_work_for_all_products(self):
        period_rows = [
            ('EARBUDS', 'BOAT', 'COK', 10, 15000.0, 10, 500.0, 2, 9),
            ('TV', 'LG', 'COK', 2, 18000.0, 2, 0.0, 0, 2),
            ('LAPTOP', 'HP', 'TVM', 1, 45000.0, 1, 1000.0, 1, 1),
            ('MOBILE', 'SAMSUNG', 'COK', 3, 24000.0, 3, 0.0, 0, 3),
        ]
        ps_rows = [
            ('EARBUDS', 1, 'BOAT', 'COK', 10, 15000.0, 500.0, 2, 9),
            ('TV', 2, 'LG', 'COK', 2, 18000.0, 0.0, 0, 2),
            ('LAPTOP', 5, 'HP', 'TVM', 1, 45000.0, 1000.0, 1, 1),
            ('MOBILE', 2, 'SAMSUNG', 'COK', 3, 24000.0, 0.0, 0, 3),
        ]
        cust_rows = [('COK', 12), ('TVM', 1)]
        fake_ch = _FakeClickHouseClient(period_rows=period_rows, ps_rows=ps_rows, cust_rows=cust_rows)

        with patch('dashboard.dashboard_api_logic._get_ch', return_value=fake_ch):
            req = self.factory.get('/api/v1/enterprise-dashboard/?type=monthly&base=4&comp=5&base_year=2026&comp_year=2026&price_slab=1,2')
            resp = build_api_response(req)

            # SQL queries must NOT restrict to MOBILE when price_slab=1,2 is passed
            data_queries = [q for q in fake_ch.queries if 'azure_sales_report' in q]
            self.assertTrue(len(data_queries) >= 4)
            for q in data_queries:
                self.assertNotIn("= 'MOBILE'", q)
                self.assertIn("< 3000", q)
                self.assertIn("< 10000", q)

            self.assertIn('ALL', resp['price_slab_config'])
            self.assertEqual(len(resp['price_slab_config']['ALL']), 10)
            self.assertIn('ALL', resp['price_segments'])
            slabs = resp['price_segments']['ALL']['slabs']
            self.assertEqual(len(slabs), 10)
            self.assertEqual(slabs[0]['segment'], 'Basic / Essentials')
            self.assertEqual(slabs[0]['c_rev'], 15000.0)
            self.assertEqual(slabs[1]['segment'], 'Entry-Level')
            self.assertEqual(slabs[1]['c_rev'], 42000.0)  # TV (18000) + MOBILE (24000)

            # Excel export (Category tab)
            buf, fname = generate_dashboard_excel(req)
            self.assertTrue(fname.endswith('.xlsx'))
            wb = load_workbook(io.BytesIO(buf.getvalue()))
            self.assertIn('13_All Products Price Slabs', wb.sheetnames)

            # Excel export (Price Segment tab)
            req_ps = self.factory.get('/api/v1/enterprise-dashboard/export/?type=monthly&base=4&comp=5&base_year=2026&comp_year=2026&price_slab=1,2&tab=price_segment')
            buf_ps, fname_ps = generate_dashboard_excel(req_ps)
            self.assertTrue(fname_ps.startswith('Price_Segmentation_'))
            wb_ps = load_workbook(io.BytesIO(buf_ps.getvalue()))
            self.assertIn('All Products Price Slabs', wb_ps.sheetnames)
            self.assertIn('All Products Slab Brands', wb_ps.sheetnames)

    def test_growth_degrowth_applies_price_slab_to_all_products(self):
        fake_ch = _FakeClickHouseClient(
            gd_rows=[('COK', 50000.0, 5, 4)],
            nr_rows=[('COK', '9999999999', '2026-05-01')],
        )
        with patch('dashboard.growth_degrowth_logic._get_ch', return_value=fake_ch):
            req = self.factory.get('/api/v1/enterprise-dashboard/growth-degrowth/?type=monthly&base=4&comp=5&base_year=2026&comp_year=2026&price_slab=1,2')
            resp = build_growth_degrowth_response(req)
            self.assertEqual(len(resp['branches']), 1)
            self.assertEqual(resp['branches'][0]['branch'], 'KOCHI')

            sales_queries = [q for q in fake_ch.queries if 'azure_sales_report' in q]
            self.assertEqual(len(sales_queries), 4)
            for q in sales_queries:
                self.assertNotIn("= 'MOBILE'", q)
                self.assertIn("< 3000", q)
                self.assertIn("< 10000", q)

    def test_product_filter_combines_with_price_slab(self):
        fake_ch = _FakeClickHouseClient(
            period_rows=[('TV', 'LG', 'COK', 2, 18000.0, 2, 0.0, 0, 2)],
            ps_rows=[('TV', 2, 'LG', 'COK', 2, 18000.0, 0.0, 0, 2)],
            cust_rows=[('COK', 2)],
        )
        with patch('dashboard.dashboard_api_logic._get_ch', return_value=fake_ch):
            req = self.factory.get('/api/v1/enterprise-dashboard/?type=monthly&base=4&comp=5&base_year=2026&comp_year=2026&product=TV&price_slab=2')
            resp = build_api_response(req)
            data_queries = [q for q in fake_ch.queries if 'azure_sales_report' in q]
            self.assertEqual(len(data_queries), 6)
            for q in data_queries:
                self.assertIn("m.product IN ('TV')", q)
                self.assertIn(">= 3000", q)
                self.assertIn("< 10000", q)
            self.assertEqual(resp['price_segments']['ALL']['slabs'][1]['c_rev'], 18000.0)

    def test_enterprise_dashboard_template_compiles_with_all_product_slabs(self):
        tpl = get_template('dashboard/enterprise_dashboard.html')
        html = tpl.render({}, request=self.factory.get('/enterprise-dashboard/'))
        self.assertIn('Basic / Essentials', html)
        self.assertIn('₹0 – ₹2,999', html)
        self.assertIn('Entry-Level', html)
        self.assertIn('₹3,000 – ₹9,999', html)
        self.assertIn('₹10,000 – ₹19,999', html)
        self.assertIn("'ALL':", html)
        self.assertNotIn("No slabs...", html)
