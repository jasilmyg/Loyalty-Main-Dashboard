"""
ch_tables.py
============
DDL for all Market Basket precomputed ClickHouse analytical tables.
Uses ENGINE = MergeTree (compatible with ClickHouse Cloud).
"""

TABLES = {

    # ── 1. Association Rules (Apriori / FP-Growth / ECLAT output) ──────────────
    "mb_association_rules": """
        CREATE TABLE IF NOT EXISTS mb_association_rules (
            rule_id         String,
            item_a          String,
            item_b          String,
            item_a_name     String,
            item_b_name     String,
            category_a      String,
            category_b      String,
            brand_a         String,
            brand_b         String,
            support         Float64,
            confidence      Float64,
            lift            Float64,
            leverage        Float64,
            conviction      Float64,
            algorithm       String,
            computed_at     DateTime DEFAULT now()
        ) ENGINE = MergeTree()
        ORDER BY (algorithm, lift)
        SETTINGS index_granularity = 8192
    """,

    # ── 2. Cross-Sell Opportunity Matrix ────────────────────────────────────────
    "mb_cross_sell_opportunities": """
        CREATE TABLE IF NOT EXISTS mb_cross_sell_opportunities (
            product_a_code          String,
            product_a_name          String,
            product_b_code          String,
            product_b_name          String,
            category_a              String,
            category_b              String,
            brand_a                 String,
            brand_b                 String,
            total_txn_with_a        Int64,
            actual_attach_rate      Float64,
            expected_attach_rate    Float64,
            gap                     Float64,
            missed_units            Int64,
            missed_revenue          Float64,
            missed_margin           Float64,
            opportunity_score       Float64,
            computed_at             DateTime DEFAULT now()
        ) ENGINE = MergeTree()
        ORDER BY (opportunity_score)
        SETTINGS index_granularity = 8192
    """,

    # ── 3. Customer Recommendations ────────────────────────────────────────────
    "mb_customer_recommendations": """
        CREATE TABLE IF NOT EXISTS mb_customer_recommendations (
            customer_mobile         String,
            recommended_item_code   String,
            item_name               String,
            category                String,
            brand                   String,
            rank                    Int32,
            purchase_probability    Float64,
            confidence              Float64,
            lift                    Float64,
            expected_margin         Float64,
            recommendation_reason   String,
            algorithm               String,
            computed_at             DateTime DEFAULT now()
        ) ENGINE = MergeTree()
        ORDER BY (customer_mobile, rank)
        SETTINGS index_granularity = 8192
    """,

    # ── 4. Sequential Patterns (Next-Product) ──────────────────────────────────
    "mb_sequential_patterns": """
        CREATE TABLE IF NOT EXISTS mb_sequential_patterns (
            anchor_item_code    String,
            anchor_item_name    String,
            next_item_code      String,
            next_item_name      String,
            days_window         Int32,
            probability         Float64,
            support             Float64,
            n_customers         Int64,
            computed_at         DateTime DEFAULT now()
        ) ENGINE = MergeTree()
        ORDER BY (anchor_item_code, days_window, probability)
        SETTINGS index_granularity = 8192
    """,

    # ── 5. Basket KPIs ─────────────────────────────────────────────────────────
    "mb_basket_kpis": """
        CREATE TABLE IF NOT EXISTS mb_basket_kpis (
            computed_at             DateTime DEFAULT now(),
            total_transactions      Int64,
            avg_basket_value        Float64,
            avg_items_per_basket    Float64,
            single_item_pct         Float64,
            multi_item_pct          Float64,
            accessory_attach_rate   Float64,
            crosssell_revenue       Float64,
            missed_revenue          Float64,
            missed_margin           Float64,
            total_rules             Int64,
            total_opportunities     Int64
        ) ENGINE = MergeTree()
        ORDER BY computed_at
        SETTINGS index_granularity = 8192
    """,

    # ── 6. Branch Performance ──────────────────────────────────────────────────
    "mb_branch_performance": """
        CREATE TABLE IF NOT EXISTS mb_branch_performance (
            branch              String,
            branch_name         String,
            total_invoices      Int64,
            multi_item_invoices Int64,
            attach_rate         Float64,
            crosssell_revenue   Float64,
            missed_revenue      Float64,
            avg_basket_value    Float64,
            avg_items           Float64,
            rank                Int32,
            computed_at         DateTime DEFAULT now()
        ) ENGINE = MergeTree()
        ORDER BY (attach_rate)
        SETTINGS index_granularity = 8192
    """,

    # ── 7. Salesperson Performance ────────────────────────────────────────────
    "mb_salesperson_performance": """
        CREATE TABLE IF NOT EXISTS mb_salesperson_performance (
            staff_code          String,
            branch              String,
            branch_name         String,
            total_invoices      Int64,
            multi_item_invoices Int64,
            attach_rate         Float64,
            crosssell_revenue   Float64,
            avg_basket_value    Float64,
            rank                Int32,
            computed_at         DateTime DEFAULT now()
        ) ENGINE = MergeTree()
        ORDER BY (attach_rate)
        SETTINGS index_granularity = 8192
    """,
}


def create_all_tables(client) -> dict:
    """
    Create all Market Basket analytical tables in ClickHouse.
    Returns dict of {table_name: 'created'|'already_exists'|'error'}.
    """
    results = {}
    for name, ddl in TABLES.items():
        try:
            client.command(ddl)
            results[name] = "created"
            print(f"[MB Tables] ✓ {name}")
        except Exception as e:
            if "already exists" in str(e).lower():
                results[name] = "already_exists"
            else:
                results[name] = f"error: {e}"
                print(f"[MB Tables] ✗ {name}: {e}")
    return results


def drop_all_tables(client) -> None:
    """Drop all Market Basket tables (for rebuild)."""
    for name in TABLES:
        try:
            client.command(f"DROP TABLE IF EXISTS {name}")
            print(f"[MB Tables] Dropped: {name}")
        except Exception as e:
            print(f"[MB Tables] Error dropping {name}: {e}")
