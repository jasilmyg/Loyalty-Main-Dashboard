"""
engine.py
=========
Market Basket Analysis Precomputation Engine.

Source tables (ClickHouse):
  - azure_sales_report      : date, invoice_no, branch, item_code, qty, mop,
                               discount, buyback, sold_price, taxable
  - azure_invoice_report    : date, invoice_no, branch, customer_mobile,
                               sales_staff_code, billing_staff_code, invoice_total
  - item_master             : item_code, item_name, brand, category,
                               item_category, product, mop, mrp
  - branch_master           : code, branch_name, district

All heavy ML runs once; results written to mb_* ClickHouse tables.
"""

from __future__ import annotations

import logging
import hashlib
import numpy as np
import pandas as pd
from datetime import datetime, timedelta
from collections import defaultdict
from itertools import combinations
from typing import Optional

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# 0.  HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def _get_ch():
    from analytics.clickhouse_service import get_ch_client
    return get_ch_client()


def _df(rows, columns):
    return pd.DataFrame(rows, columns=columns)


def _safe_insert(client, table: str, df: pd.DataFrame) -> None:
    """Insert a DataFrame into ClickHouse, dropping existing data first."""
    if df.empty:
        logger.warning(f"[MB Engine] No data to insert into {table}")
        return
    try:
        client.command(f"TRUNCATE TABLE IF EXISTS {table}")
        client.insert_df(table, df)
        logger.info(f"[MB Engine] Inserted {len(df):,} rows → {table}")
    except Exception as e:
        logger.error(f"[MB Engine] Failed to insert into {table}: {e}")
        raise


# ─────────────────────────────────────────────────────────────────────────────
# 1.  DATA LOADING
# ─────────────────────────────────────────────────────────────────────────────

class DataLoader:
    """Loads and prepares transaction data from ClickHouse."""

    ITEM_QUERY = """
        SELECT
            s.invoice_no,
            s.branch,
            s.item_code,
            s.qty,
            s.sold_price,
            s.mop,
            s.discount,
            s.taxable,
            toDate(s.date) AS sale_date,
            i.item_name,
            i.brand,
            i.category,
            i.item_category,
            i.product
        FROM azure_sales_report s
        LEFT JOIN item_master i ON s.item_code = i.item_code
        WHERE s.qty > 0
          AND s.sold_price > 0
          AND s.invoice_no != ''
          AND s.item_code != ''
          {date_filter}
        ORDER BY s.invoice_no
    """

    INVOICE_QUERY = """
        SELECT
            invoice_no,
            branch,
            customer_mobile,
            sales_staff_code,
            billing_staff_code,
            invoice_total,
            toDate(date) AS sale_date
        FROM azure_invoice_report
        WHERE invoice_no != ''
        {date_filter}
    """

    BRANCH_QUERY = """
        SELECT code, branch_name, district FROM branch_master
    """

    def __init__(self, client, days_back: int = 730):
        self.client = client
        self.days_back = days_back
        self._cutoff = (datetime.now() - timedelta(days=days_back)).strftime('%Y-%m-%d')

    def load_items(self) -> pd.DataFrame:
        date_filter = f"AND s.date >= '{self._cutoff}'"
        sql = self.ITEM_QUERY.format(date_filter=date_filter)
        logger.info("[MB Engine] Loading item-level sales from ClickHouse…")
        rows = self.client.query(sql).result_rows
        cols = ['invoice_no', 'branch', 'item_code', 'qty', 'sold_price',
                'mop', 'discount', 'taxable', 'sale_date',
                'item_name', 'brand', 'category', 'item_category', 'product']
        df = _df(rows, cols)
        # Clean up
        df['item_name'] = df['item_name'].fillna(df['item_code'])
        df['brand']     = df['brand'].fillna('UNKNOWN')
        df['category']  = df['category'].fillna('OTHERS')
        df['item_category'] = df['item_category'].fillna('GENERAL')
        logger.info(f"[MB Engine] Loaded {len(df):,} item rows, {df['invoice_no'].nunique():,} invoices")
        return df

    def load_invoices(self) -> pd.DataFrame:
        date_filter = f"AND date >= '{self._cutoff}'"
        sql = self.INVOICE_QUERY.format(date_filter=date_filter)
        rows = self.client.query(sql).result_rows
        cols = ['invoice_no', 'branch', 'customer_mobile', 'sales_staff_code',
                'billing_staff_code', 'invoice_total', 'sale_date']
        return _df(rows, cols)

    def load_branches(self) -> pd.DataFrame:
        rows = self.client.query(self.BRANCH_QUERY).result_rows
        return _df(rows, ['code', 'branch_name', 'district'])


# ─────────────────────────────────────────────────────────────────────────────
# 2.  BASKET BUILDER — one-hot encode transactions
# ─────────────────────────────────────────────────────────────────────────────

class BasketBuilder:
    def __init__(self, items_df: pd.DataFrame, min_basket_size: int = 2):
        self.items_df = items_df
        self.min_basket_size = min_basket_size

    def build(self) -> tuple[pd.DataFrame, pd.DataFrame]:
        """
        Returns:
          basket_df  : binary item-presence matrix  (invoice × item_code)
          invoice_df : invoice-level summary
        """
        logger.info("[MB Engine] Building basket matrix…")
        df = self.items_df.copy()

        # Basket summary
        invoice_summary = (
            df.groupby('invoice_no')
              .agg(
                  n_items=('item_code', 'nunique'),
                  basket_value=('sold_price', 'sum'),
                  branch=('branch', 'first'),
                  sale_date=('sale_date', 'first'),
              )
              .reset_index()
        )

        # Binary basket matrix
        basket_df = (
            df.groupby(['invoice_no', 'item_code'])['qty']
              .sum()
              .unstack(fill_value=0)
              .clip(upper=1)   # binary
              .astype(bool)
        )

        logger.info(f"[MB Engine] Basket matrix: {basket_df.shape[0]:,} invoices × {basket_df.shape[1]:,} items")
        return basket_df, invoice_summary


# ─────────────────────────────────────────────────────────────────────────────
# 3.  ASSOCIATION RULES
# ─────────────────────────────────────────────────────────────────────────────

class AssociationRuleEngine:
    """Runs Apriori, FP-Growth, ECLAT and computes all rule metrics."""

    def __init__(
        self,
        basket_df: pd.DataFrame,
        item_meta: pd.DataFrame,
        min_support: float = 0.001,
        min_confidence: float = 0.05,
        min_lift: float = 1.0,
        max_rules: int = 5000,
    ):
        self.basket_df = basket_df
        self.item_meta = item_meta   # item_code → name, brand, category
        self.min_support = min_support
        self.min_confidence = min_confidence
        self.min_lift = min_lift
        self.max_rules = max_rules

    def _meta(self, code, field, default=''):
        row = self.item_meta.get(code, {})
        return row.get(field, default)

    def _build_rules_df(self, rules_df: pd.DataFrame, algorithm: str) -> pd.DataFrame:
        """Convert mlxtend rules output into standardised format."""
        rows = []
        for _, row in rules_df.iterrows():
            for a in row['antecedents']:
                for b in row['consequents']:
                    if a == b:
                        continue
                    rule_id = hashlib.md5(f"{algorithm}{a}{b}".encode()).hexdigest()[:16]
                    rows.append({
                        'rule_id':      rule_id,
                        'item_a':       a,
                        'item_b':       b,
                        'item_a_name':  self._meta(a, 'item_name', a),
                        'item_b_name':  self._meta(b, 'item_name', b),
                        'category_a':   self._meta(a, 'category', 'OTHERS'),
                        'category_b':   self._meta(b, 'category', 'OTHERS'),
                        'brand_a':      self._meta(a, 'brand', ''),
                        'brand_b':      self._meta(b, 'brand', ''),
                        'support':      float(row.get('support', 0)),
                        'confidence':   float(row.get('confidence', 0)),
                        'lift':         float(row.get('lift', 0)),
                        'leverage':     float(row.get('leverage', 0)),
                        'conviction':   float(row.get('conviction', 0)),
                        'algorithm':    algorithm,
                    })
        return pd.DataFrame(rows)

    def run_apriori(self) -> pd.DataFrame:
        try:
            from mlxtend.frequent_patterns import apriori, association_rules
            logger.info("[MB Engine] Running Apriori…")
            freq = apriori(self.basket_df, min_support=self.min_support, use_colnames=True, max_len=2)
            rules = association_rules(freq, metric='lift', min_threshold=self.min_lift)
            rules = rules[rules['confidence'] >= self.min_confidence]
            rules = rules.nlargest(self.max_rules, 'lift')
            return self._build_rules_df(rules, 'apriori')
        except Exception as e:
            logger.error(f"[MB Engine] Apriori failed: {e}")
            return pd.DataFrame()

    def run_fpgrowth(self) -> pd.DataFrame:
        try:
            from mlxtend.frequent_patterns import fpgrowth, association_rules
            logger.info("[MB Engine] Running FP-Growth…")
            freq = fpgrowth(self.basket_df, min_support=self.min_support, use_colnames=True, max_len=2)
            rules = association_rules(freq, metric='lift', min_threshold=self.min_lift)
            rules = rules[rules['confidence'] >= self.min_confidence]
            rules = rules.nlargest(self.max_rules, 'lift')
            return self._build_rules_df(rules, 'fpgrowth')
        except Exception as e:
            logger.error(f"[MB Engine] FP-Growth failed: {e}")
            return pd.DataFrame()

    def run_eclat(self) -> pd.DataFrame:
        """Custom bitset-based ECLAT for pairwise support."""
        logger.info("[MB Engine] Running ECLAT (pairwise)…")
        try:
            arr = self.basket_df.values.astype(bool)
            cols = list(self.basket_df.columns)
            n_txn = arr.shape[0]

            # Build tidsets
            tidsets = {cols[i]: set(np.where(arr[:, i])[0]) for i in range(len(cols))}
            item_support = {c: len(t) / n_txn for c, t in tidsets.items()}

            # Filter by min support
            frequent = {c: t for c, t in tidsets.items() if item_support[c] >= self.min_support}
            freq_items = list(frequent.keys())

            rows = []
            for a, b in combinations(freq_items, 2):
                ab_len = len(frequent[a] & frequent[b])
                sup    = ab_len / n_txn
                if sup < self.min_support:
                    continue
                conf_ab = sup / item_support[a] if item_support[a] > 0 else 0
                conf_ba = sup / item_support[b] if item_support[b] > 0 else 0
                lift    = sup / (item_support[a] * item_support[b]) if item_support[a] * item_support[b] > 0 else 0
                if lift < self.min_lift:
                    continue
                for (ia, ib, conf) in [(a, b, conf_ab), (b, a, conf_ba)]:
                    if conf < self.min_confidence:
                        continue
                    rule_id = hashlib.md5(f"eclat{ia}{ib}".encode()).hexdigest()[:16]
                    rows.append({
                        'rule_id':     rule_id,
                        'item_a':      ia,
                        'item_b':      ib,
                        'item_a_name': self._meta(ia, 'item_name', ia),
                        'item_b_name': self._meta(ib, 'item_name', ib),
                        'category_a':  self._meta(ia, 'category', 'OTHERS'),
                        'category_b':  self._meta(ib, 'category', 'OTHERS'),
                        'brand_a':     self._meta(ia, 'brand', ''),
                        'brand_b':     self._meta(ib, 'brand', ''),
                        'support':     round(sup, 6),
                        'confidence':  round(conf, 6),
                        'lift':        round(lift, 6),
                        'leverage':    round(sup - item_support[ia] * item_support[ib], 6),
                        'conviction':  round((1 - item_support[ib]) / (1 - conf), 6) if conf < 1 else 99.0,
                        'algorithm':   'eclat',
                    })

            df = pd.DataFrame(rows)
            if not df.empty:
                df = df.nlargest(self.max_rules, 'lift')
            logger.info(f"[MB Engine] ECLAT found {len(df):,} rules")
            return df
        except Exception as e:
            logger.error(f"[MB Engine] ECLAT failed: {e}")
            return pd.DataFrame()


# ─────────────────────────────────────────────────────────────────────────────
# 4.  CROSS-SELL OPPORTUNITY ENGINE
# ─────────────────────────────────────────────────────────────────────────────

class CrossSellOpportunityEngine:
    """
    For each product pair (A→B), compute:
      actual_attach_rate, expected_attach_rate, gap, missed_units, missed_revenue
    Expected attach rate = median attach rate of B in category_b across all branches.
    """

    # Known CE → accessory mappings (used to boost expected rate)
    CE_ACCESSORY_MAP = {
        'TV':            ['SOUNDBAR', 'WALL MOUNT', 'HDMI', 'SPEAKER'],
        'LAPTOP':        ['MOUSE', 'BAG', 'KEYBOARD', 'HEADPHONE'],
        'MOBILE':        ['CASE', 'PROTECTOR', 'EARBUDS', 'CHARGER'],
        'REFRIGERATOR':  ['STABILIZER', 'VOLTAGE STABILIZER'],
        'AC':            ['STABILIZER'],
        'WASHING MACHINE': ['STAND', 'STABILIZER'],
        'CAMERA':        ['MEMORY CARD', 'BAG', 'TRIPOD'],
    }

    def __init__(self, items_df: pd.DataFrame, item_meta: dict, margin_pct: float = 0.12):
        self.items_df  = items_df
        self.item_meta = item_meta
        self.margin_pct = margin_pct

    def compute(self) -> pd.DataFrame:
        logger.info("[MB Engine] Computing cross-sell opportunities…")
        df = self.items_df.copy()

        # Build invoice → items sets
        inv_items = df.groupby('invoice_no')['item_code'].apply(set)

        # Build invoice → item prices
        inv_prices = df.groupby(['invoice_no', 'item_code'])['sold_price'].sum()

        # For each pair in co-occurrence, compute attach rate
        pair_stats: dict = defaultdict(lambda: {'co': 0, 'a_total': 0, 'revenue_b': 0.0})

        # Count total invoices per item
        item_invoices: dict = defaultdict(int)
        for inv, items in inv_items.items():
            for item in items:
                item_invoices[item] += 1

        # Count co-occurrences
        for inv, items in inv_items.items():
            items_list = list(items)
            for a, b in combinations(items_list, 2):
                pair_stats[(a, b)]['co'] += 1
                pair_stats[(b, a)]['co'] += 1

        # Expected rates: per category_b → median attach across all pairs
        category_rates: dict = defaultdict(list)
        for (a, b), stats in pair_stats.items():
            a_total = item_invoices.get(a, 0)
            if a_total == 0:
                continue
            rate = stats['co'] / a_total
            cat_b = self.item_meta.get(b, {}).get('category', 'OTHERS')
            category_rates[cat_b].append(rate)

        cat_expected = {cat: float(np.median(rates)) for cat, rates in category_rates.items()}

        # Build opportunity rows
        rows = []
        for (a, b), stats in pair_stats.items():
            a_total = item_invoices.get(a, 0)
            if a_total < 50:   # skip very rare items
                continue
            actual_rate    = stats['co'] / a_total if a_total > 0 else 0
            cat_b          = self.item_meta.get(b, {}).get('category', 'OTHERS')
            expected_rate  = max(cat_expected.get(cat_b, actual_rate * 1.5), actual_rate * 1.1)
            gap            = expected_rate - actual_rate
            if gap <= 0:
                continue
            missed_units   = int(gap * a_total)
            price_b        = self.item_meta.get(b, {}).get('mop', 0) or 0
            missed_revenue = missed_units * price_b
            missed_margin  = missed_revenue * self.margin_pct
            opportunity    = gap * np.log1p(a_total) * (price_b / 1000 if price_b > 0 else 1)

            rows.append({
                'product_a_code':       a,
                'product_a_name':       self.item_meta.get(a, {}).get('item_name', a),
                'product_b_code':       b,
                'product_b_name':       self.item_meta.get(b, {}).get('item_name', b),
                'category_a':           self.item_meta.get(a, {}).get('category', 'OTHERS'),
                'category_b':           cat_b,
                'brand_a':              self.item_meta.get(a, {}).get('brand', ''),
                'brand_b':              self.item_meta.get(b, {}).get('brand', ''),
                'total_txn_with_a':     a_total,
                'actual_attach_rate':   round(actual_rate, 6),
                'expected_attach_rate': round(expected_rate, 6),
                'gap':                  round(gap, 6),
                'missed_units':         missed_units,
                'missed_revenue':       round(missed_revenue, 2),
                'missed_margin':        round(missed_margin, 2),
                'opportunity_score':    round(opportunity, 4),
            })

        df_out = pd.DataFrame(rows)
        if not df_out.empty:
            df_out = df_out.nlargest(10000, 'opportunity_score')
        logger.info(f"[MB Engine] Cross-sell opportunities: {len(df_out):,} pairs")
        return df_out


# ─────────────────────────────────────────────────────────────────────────────
# 5.  ITEM-ITEM COLLABORATIVE FILTERING (cosine similarity)
# ─────────────────────────────────────────────────────────────────────────────

class CollaborativeFilteringEngine:
    def __init__(self, basket_df: pd.DataFrame, item_meta: dict, top_n: int = 10):
        self.basket_df = basket_df
        self.item_meta = item_meta
        self.top_n     = top_n

    def build_similarity_matrix(self) -> pd.DataFrame:
        """Build item-item cosine similarity from basket matrix."""
        from sklearn.metrics.pairwise import cosine_similarity
        logger.info("[MB Engine] Building item-item cosine similarity matrix…")
        arr   = self.basket_df.values.T.astype(float)
        sim   = cosine_similarity(arr)
        items = list(self.basket_df.columns)
        return pd.DataFrame(sim, index=items, columns=items)

    def get_recommendations(self, customer_history: list, sim_df: pd.DataFrame) -> list:
        """Get top-N recommendations for a customer given their purchase history."""
        scores = pd.Series(dtype=float)
        for item in customer_history:
            if item in sim_df.index:
                row = sim_df.loc[item].drop(labels=customer_history, errors='ignore')
                scores = scores.add(row, fill_value=0)
        return scores.nlargest(self.top_n).index.tolist()


# ─────────────────────────────────────────────────────────────────────────────
# 6.  ITEM2VEC (Word2Vec on transaction sequences)
# ─────────────────────────────────────────────────────────────────────────────

class Item2VecEngine:
    def __init__(self, items_df: pd.DataFrame, item_meta: dict,
                 vector_size: int = 64, window: int = 5, min_count: int = 5):
        self.items_df   = items_df
        self.item_meta  = item_meta
        self.vector_size = vector_size
        self.window     = window
        self.min_count  = min_count
        self.model      = None

    def train(self) -> bool:
        try:
            from gensim.models import Word2Vec
            logger.info("[MB Engine] Training Item2Vec (Word2Vec)…")
            sequences = (
                self.items_df
                    .sort_values(['invoice_no', 'item_code'])
                    .groupby('invoice_no')['item_code']
                    .apply(list)
                    .tolist()
            )
            self.model = Word2Vec(
                sentences=sequences,
                vector_size=self.vector_size,
                window=self.window,
                min_count=self.min_count,
                workers=4,
                epochs=10,
            )
            logger.info(f"[MB Engine] Item2Vec vocab size: {len(self.model.wv):,}")
            return True
        except Exception as e:
            logger.error(f"[MB Engine] Item2Vec failed: {e}")
            return False

    def similar_items(self, item_code: str, top_n: int = 10) -> list:
        if self.model is None or item_code not in self.model.wv:
            return []
        return [(item, float(score))
                for item, score in self.model.wv.most_similar(item_code, topn=top_n)]


# ─────────────────────────────────────────────────────────────────────────────
# 7.  SEQUENTIAL PATTERN ENGINE (next-product analysis)
# ─────────────────────────────────────────────────────────────────────────────

class SequentialPatternEngine:
    WINDOWS = [7, 30, 90]  # days

    def __init__(self, items_df: pd.DataFrame, invoices_df: pd.DataFrame, item_meta: dict):
        self.items_df    = items_df
        self.invoices_df = invoices_df
        self.item_meta   = item_meta

    def compute(self) -> pd.DataFrame:
        logger.info("[MB Engine] Computing sequential next-product patterns…")
        try:
            # Merge to get customer_mobile + sale_date per item
            merged = self.items_df.merge(
                self.invoices_df[['invoice_no', 'customer_mobile', 'sale_date']].rename(
                    columns={'sale_date': 'inv_date'}),
                on='invoice_no', how='inner'
            )
            merged = merged[merged['customer_mobile'].str.len() == 10]
            merged['inv_date'] = pd.to_datetime(merged['inv_date'])

            # Customer × item purchase timeline
            customer_timeline = (
                merged.groupby(['customer_mobile', 'item_code', 'inv_date'])
                      .size().reset_index(name='n')
                      .sort_values(['customer_mobile', 'inv_date'])
            )

            rows = []
            for window in self.WINDOWS:
                logger.info(f"[MB Engine]   Sequential window: {window} days")
                # Build: for each (customer, item_a) → find item_b bought within window days AFTER
                pair_counts: dict = defaultdict(lambda: defaultdict(int))
                anchor_counts: dict = defaultdict(int)

                customers = customer_timeline['customer_mobile'].unique()
                for cust in customers:
                    cust_df = customer_timeline[customer_timeline['customer_mobile'] == cust].copy()
                    if len(cust_df) < 2:
                        continue
                    for i, row_a in cust_df.iterrows():
                        anchor = row_a['item_code']
                        date_a = row_a['inv_date']
                        anchor_counts[anchor] += 1
                        future = cust_df[
                            (cust_df['inv_date'] > date_a) &
                            (cust_df['inv_date'] <= date_a + timedelta(days=window))
                        ]
                        for _, row_b in future.iterrows():
                            next_item = row_b['item_code']
                            if next_item != anchor:
                                pair_counts[anchor][next_item] += 1

                for anchor, nexts in pair_counts.items():
                    a_total = anchor_counts.get(anchor, 1)
                    for next_item, cnt in nexts.items():
                        prob = cnt / a_total
                        if prob < 0.01 or cnt < 5:
                            continue
                        rows.append({
                            'anchor_item_code': anchor,
                            'anchor_item_name': self.item_meta.get(anchor, {}).get('item_name', anchor),
                            'next_item_code':   next_item,
                            'next_item_name':   self.item_meta.get(next_item, {}).get('item_name', next_item),
                            'days_window':      window,
                            'probability':      round(prob, 6),
                            'support':          round(cnt / max(a_total, 1), 6),
                            'n_customers':      cnt,
                        })

            df = pd.DataFrame(rows) if rows else pd.DataFrame()
            logger.info(f"[MB Engine] Sequential patterns: {len(df):,} rows")
            return df
        except Exception as e:
            logger.error(f"[MB Engine] Sequential patterns failed: {e}")
            return pd.DataFrame()


# ─────────────────────────────────────────────────────────────────────────────
# 8.  CUSTOMER RECOMMENDATION ENGINE (Hybrid CF + Item2Vec)
# ─────────────────────────────────────────────────────────────────────────────

class HybridRecommender:
    def __init__(
        self,
        items_df: pd.DataFrame,
        invoices_df: pd.DataFrame,
        cf_engine: CollaborativeFilteringEngine,
        i2v_engine: Item2VecEngine,
        item_meta: dict,
        top_n: int = 5,
        sample_customers: int = 5000,
    ):
        self.items_df   = items_df
        self.invoices_df = invoices_df
        self.cf_engine  = cf_engine
        self.i2v_engine = i2v_engine
        self.item_meta  = item_meta
        self.top_n      = top_n
        self.sample_customers = sample_customers

    def compute(self, sim_df: Optional[pd.DataFrame] = None) -> pd.DataFrame:
        logger.info("[MB Engine] Computing hybrid customer recommendations…")
        try:
            # Customer purchase history (last 2 years)
            merged = self.items_df.merge(
                self.invoices_df[['invoice_no', 'customer_mobile']],
                on='invoice_no', how='inner'
            )
            merged = merged[merged['customer_mobile'].str.len() == 10]

            cust_history = merged.groupby('customer_mobile')['item_code'].apply(list)

            # Sample to avoid OOM
            if len(cust_history) > self.sample_customers:
                cust_history = cust_history.sample(self.sample_customers, random_state=42)

            rows = []
            for mobile, history in cust_history.items():
                history = list(set(history))
                if len(history) < 2:
                    continue

                # CF scores
                cf_scores: dict = {}
                if sim_df is not None:
                    for item in history:
                        if item in sim_df.index:
                            row = sim_df.loc[item].drop(labels=history, errors='ignore')
                            for rec_item, score in row.items():
                                cf_scores[rec_item] = cf_scores.get(rec_item, 0) + float(score)

                # Item2Vec scores
                i2v_scores: dict = {}
                if self.i2v_engine.model is not None:
                    for item in history:
                        for rec_item, score in self.i2v_engine.similar_items(item, top_n=20):
                            if rec_item not in history:
                                i2v_scores[rec_item] = i2v_scores.get(rec_item, 0) + score

                # Hybrid: weighted average
                all_items = set(list(cf_scores.keys()) + list(i2v_scores.keys()))
                hybrid: dict = {}
                for it in all_items:
                    cf_s  = cf_scores.get(it, 0)
                    i2v_s = i2v_scores.get(it, 0)
                    hybrid[it] = 0.5 * cf_s + 0.5 * i2v_s

                top = sorted(hybrid.items(), key=lambda x: -x[1])[:self.top_n]

                for rank, (rec_item, score) in enumerate(top, 1):
                    meta   = self.item_meta.get(rec_item, {})
                    price  = meta.get('mop', 0) or 0
                    reason = "Frequently bought together" if cf_scores.get(rec_item, 0) > i2v_scores.get(rec_item, 0) else "Similar purchase pattern"
                    rows.append({
                        'customer_mobile':       str(mobile),
                        'recommended_item_code': rec_item,
                        'item_name':             meta.get('item_name', rec_item),
                        'category':              meta.get('category', 'OTHERS'),
                        'brand':                 meta.get('brand', ''),
                        'rank':                  rank,
                        'purchase_probability':  round(min(score / 10, 0.99), 4),
                        'confidence':            round(min(score / 5, 0.99), 4),
                        'lift':                  round(1 + score, 4),
                        'expected_margin':        round(price * 0.12, 2),
                        'recommendation_reason':  reason,
                        'algorithm':             'hybrid_cf_i2v',
                    })

            df = pd.DataFrame(rows)
            logger.info(f"[MB Engine] Customer recommendations: {len(df):,} rows")
            return df
        except Exception as e:
            logger.error(f"[MB Engine] Customer recommendations failed: {e}")
            return pd.DataFrame()


# ─────────────────────────────────────────────────────────────────────────────
# 9.  BRANCH & SALESPERSON PERFORMANCE
# ─────────────────────────────────────────────────────────────────────────────

class PerformanceEngine:
    def __init__(self, items_df: pd.DataFrame, invoices_df: pd.DataFrame, branches_df: pd.DataFrame):
        self.items_df    = items_df
        self.invoices_df = invoices_df
        self.branches_df = branches_df

    def compute_branch_performance(self) -> pd.DataFrame:
        logger.info("[MB Engine] Computing branch performance…")
        inv_items = (
            self.items_df
                .groupby('invoice_no')
                .agg(n_items=('item_code', 'nunique'),
                     basket_value=('sold_price', 'sum'),
                     branch=('branch', 'first'))
                .reset_index()
        )
        grp = (
            inv_items.groupby('branch')
                     .agg(
                         total_invoices=('invoice_no', 'count'),
                         multi_item_invoices=('n_items', lambda x: (x > 1).sum()),
                         crosssell_revenue=('basket_value', lambda x: x[inv_items.loc[x.index, 'n_items'] > 1].sum()),
                         avg_basket_value=('basket_value', 'mean'),
                         avg_items=('n_items', 'mean'),
                     )
                     .reset_index()
        )
        grp['attach_rate'] = grp['multi_item_invoices'] / grp['total_invoices'].clip(lower=1)
        company_avg        = grp['attach_rate'].median()
        grp['missed_revenue'] = (
            grp['total_invoices'] * (company_avg - grp['attach_rate']).clip(lower=0) * grp['avg_basket_value']
        )
        grp['rank'] = grp['attach_rate'].rank(ascending=False, method='first').astype(int)

        # Join branch names
        branch_map = dict(zip(self.branches_df['code'], self.branches_df['branch_name']))
        grp['branch_name'] = grp['branch'].map(branch_map).fillna(grp['branch'])

        return grp

    def compute_salesperson_performance(self) -> pd.DataFrame:
        logger.info("[MB Engine] Computing salesperson performance…")
        merged = self.items_df.merge(
            self.invoices_df[['invoice_no', 'sales_staff_code']],
            on='invoice_no', how='inner'
        )
        inv_items = (
            merged.groupby(['invoice_no', 'sales_staff_code'])
                  .agg(n_items=('item_code', 'nunique'),
                       basket_value=('sold_price', 'sum'),
                       branch=('branch', 'first'))
                  .reset_index()
        )
        grp = (
            inv_items.groupby(['sales_staff_code', 'branch'])
                     .agg(
                         total_invoices=('invoice_no', 'count'),
                         multi_item_invoices=('n_items', lambda x: (x > 1).sum()),
                         crosssell_revenue=('basket_value', lambda x: x[inv_items.loc[x.index, 'n_items'] > 1].sum()),
                         avg_basket_value=('basket_value', 'mean'),
                     )
                     .reset_index()
        )
        grp = grp[grp['sales_staff_code'] != '']
        grp['attach_rate'] = grp['multi_item_invoices'] / grp['total_invoices'].clip(lower=1)
        grp['rank'] = grp['attach_rate'].rank(ascending=False, method='first').astype(int)

        branch_map = {}
        if not self.branches_df.empty:
            branch_map = dict(zip(self.branches_df['code'], self.branches_df['branch_name']))
        grp['branch_name'] = grp['branch'].map(branch_map).fillna(grp['branch'])
        return grp


# ─────────────────────────────────────────────────────────────────────────────
# 10.  BASKET KPIs
# ─────────────────────────────────────────────────────────────────────────────

def compute_basket_kpis(items_df: pd.DataFrame, opportunities_df: pd.DataFrame,
                         rules_df: pd.DataFrame) -> pd.DataFrame:
    logger.info("[MB Engine] Computing basket KPIs…")
    inv_items = (
        items_df.groupby('invoice_no')
                .agg(n_items=('item_code', 'nunique'),
                     basket_value=('sold_price', 'sum'))
                .reset_index()
    )
    total_txn     = len(inv_items)
    avg_value     = float(inv_items['basket_value'].mean())
    avg_items     = float(inv_items['n_items'].mean())
    single_pct    = float((inv_items['n_items'] == 1).sum() / total_txn * 100)
    multi_pct     = float((inv_items['n_items'] > 1).sum() / total_txn * 100)
    attach_rate   = float((inv_items['n_items'] > 1).sum() / total_txn * 100)

    crosssell_rev = float(
        items_df[items_df['invoice_no'].isin(
            inv_items[inv_items['n_items'] > 1]['invoice_no']
        )]['sold_price'].sum()
    )
    missed_rev    = float(opportunities_df['missed_revenue'].sum()) if not opportunities_df.empty else 0
    missed_margin = float(opportunities_df['missed_margin'].sum()) if not opportunities_df.empty else 0

    return pd.DataFrame([{
        'total_transactions':   total_txn,
        'avg_basket_value':     round(avg_value, 2),
        'avg_items_per_basket': round(avg_items, 3),
        'single_item_pct':      round(single_pct, 2),
        'multi_item_pct':       round(multi_pct, 2),
        'accessory_attach_rate': round(attach_rate, 2),
        'crosssell_revenue':    round(crosssell_rev, 2),
        'missed_revenue':       round(missed_rev, 2),
        'missed_margin':        round(missed_margin, 2),
        'total_rules':          len(rules_df),
        'total_opportunities':  len(opportunities_df),
    }])


# ─────────────────────────────────────────────────────────────────────────────
# 11.  MAIN ORCHESTRATOR
# ─────────────────────────────────────────────────────────────────────────────

def run_full_precompute(days_back: int = 730, quick: bool = False) -> dict:
    """
    Full precomputation pipeline. Call this from management command or admin UI.
    quick=True uses last 180 days for faster iteration.
    """
    from market_basket.ch_tables import create_all_tables

    client = _get_ch()
    if client is None:
        raise RuntimeError("Cannot connect to ClickHouse")

    # Ensure tables exist
    create_all_tables(client)

    actual_days = 180 if quick else days_back
    loader = DataLoader(client, days_back=actual_days)

    # Load raw data
    items_df    = loader.load_items()
    invoices_df = loader.load_invoices()
    branches_df = loader.load_branches()

    if items_df.empty:
        logger.error("[MB Engine] No item data loaded — aborting")
        return {'status': 'error', 'message': 'No item data'}

    # Build item metadata lookup
    item_meta: dict = {}
    for _, row in items_df[['item_code','item_name','brand','category','item_category']].drop_duplicates('item_code').iterrows():
        item_meta[row['item_code']] = {
            'item_name':    row['item_name'],
            'brand':        row['brand'],
            'category':     row['category'],
            'item_category': row['item_category'],
            'mop':          float(items_df[items_df['item_code']==row['item_code']]['mop'].iloc[0]) if row['item_code'] in items_df['item_code'].values else 0,
        }

    # Build basket matrix (use top-N items to avoid memory explosion)
    TOP_ITEMS = 2000 if not quick else 500
    top_items = items_df['item_code'].value_counts().head(TOP_ITEMS).index.tolist()
    items_filtered = items_df[items_df['item_code'].isin(top_items)]

    builder = BasketBuilder(items_filtered)
    basket_df, _ = builder.build()

    results = {}

    # ── Association Rules ──────────────────────────────────────────────────────
    logger.info("[MB Engine] === Association Rules Phase ===")
    rule_engine = AssociationRuleEngine(basket_df, item_meta,
                                         min_support=0.002, min_confidence=0.05)
    apriori_rules  = rule_engine.run_apriori()
    fpgrowth_rules = rule_engine.run_fpgrowth()
    eclat_rules    = rule_engine.run_eclat()

    all_rules = pd.concat([apriori_rules, fpgrowth_rules, eclat_rules], ignore_index=True)
    if not all_rules.empty:
        all_rules['computed_at'] = datetime.now()
        _safe_insert(client, 'mb_association_rules', all_rules)
    results['rules'] = len(all_rules)

    # ── Cross-Sell Opportunities ───────────────────────────────────────────────
    logger.info("[MB Engine] === Cross-Sell Opportunity Phase ===")
    opp_engine = CrossSellOpportunityEngine(items_filtered, item_meta)
    opp_df = opp_engine.compute()
    if not opp_df.empty:
        opp_df['computed_at'] = datetime.now()
        _safe_insert(client, 'mb_cross_sell_opportunities', opp_df)
    results['opportunities'] = len(opp_df)

    # ── Branch & Salesperson Performance ──────────────────────────────────────
    logger.info("[MB Engine] === Performance Phase ===")
    perf_engine = PerformanceEngine(items_df, invoices_df, branches_df)
    branch_df = perf_engine.compute_branch_performance()
    if not branch_df.empty:
        branch_df['computed_at'] = datetime.now()
        _safe_insert(client, 'mb_branch_performance', branch_df)
    results['branches'] = len(branch_df)

    staff_df = perf_engine.compute_salesperson_performance()
    if not staff_df.empty:
        staff_df['computed_at'] = datetime.now()
        _safe_insert(client, 'mb_salesperson_performance', staff_df)
    results['staff'] = len(staff_df)

    # ── Item2Vec ────────────────────────────────────────────────────────────────
    logger.info("[MB Engine] === Item2Vec Phase ===")
    i2v = Item2VecEngine(items_df, item_meta)
    i2v.train()

    # ── Collaborative Filtering ─────────────────────────────────────────────────
    logger.info("[MB Engine] === Collaborative Filtering Phase ===")
    cf = CollaborativeFilteringEngine(basket_df, item_meta)
    sim_df = None
    try:
        sim_df = cf.build_similarity_matrix()
    except Exception as e:
        logger.error(f"[MB Engine] CF similarity failed: {e}")

    # ── Customer Recommendations ───────────────────────────────────────────────
    logger.info("[MB Engine] === Customer Recommendation Phase ===")
    recommender = HybridRecommender(items_df, invoices_df, cf, i2v, item_meta,
                                     sample_customers=3000 if not quick else 500)
    rec_df = recommender.compute(sim_df)
    if not rec_df.empty:
        rec_df['computed_at'] = datetime.now()
        _safe_insert(client, 'mb_customer_recommendations', rec_df)
    results['recommendations'] = len(rec_df)

    # ── Sequential Patterns ────────────────────────────────────────────────────
    logger.info("[MB Engine] === Sequential Pattern Phase ===")
    seq_engine = SequentialPatternEngine(items_df, invoices_df, item_meta)
    seq_df = seq_engine.compute()
    if not seq_df.empty:
        seq_df['computed_at'] = datetime.now()
        _safe_insert(client, 'mb_sequential_patterns', seq_df)
    results['sequential'] = len(seq_df)

    # ── KPIs ──────────────────────────────────────────────────────────────────
    logger.info("[MB Engine] === KPI Phase ===")
    kpi_df = compute_basket_kpis(items_df, opp_df, all_rules)
    kpi_df['computed_at'] = datetime.now()
    _safe_insert(client, 'mb_basket_kpis', kpi_df)

    logger.info(f"[MB Engine] Precomputation complete: {results}")
    return {'status': 'success', 'results': results}
