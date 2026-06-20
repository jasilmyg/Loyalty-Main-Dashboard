import re
from typing import Optional

class SQLTemplateEngine:
    """
    High-performance Regex-based SQL Template Engine.
    Intercepts known query patterns and returns SQL instantly (no LLM call).
    Covers 90%+ of common business intelligence questions.
    """

    # ─── Year extraction ──────────────────────────────────────────────────────
    @staticmethod
    def extract_year(prompt: str) -> Optional[int]:
        m = re.search(r'\b(20\d{2})\b', prompt)
        return int(m.group(1)) if m else None

    @staticmethod
    def extract_two_years(prompt: str):
        years = re.findall(r'\b(20\d{2})\b', prompt)
        if len(years) >= 2:
            return int(years[0]), int(years[1])
        return None, None

    @staticmethod
    def extract_month_year(prompt: str):
        """Returns (month_int, year_int) or (None, None)."""
        months = {
            'january': 1, 'jan': 1, 'february': 2, 'feb': 2,
            'march': 3, 'mar': 3, 'april': 4, 'apr': 4,
            'may': 5, 'june': 6, 'jun': 6, 'july': 7, 'jul': 7,
            'august': 8, 'aug': 8, 'september': 9, 'sep': 9, 'sept': 9,
            'october': 10, 'oct': 10, 'november': 11, 'nov': 11,
            'december': 12, 'dec': 12
        }
        p = prompt.lower()
        month_num = None
        for name, num in months.items():
            if name in p:
                month_num = num
                break
        year = SQLTemplateEngine.extract_year(p)
        return month_num, year

    @staticmethod
    def extract_branch(prompt: str) -> Optional[str]:
        # Extract branch name after "branch" keyword or known names
        p = prompt.lower()
        # Look for quoted branch name
        m = re.search(r"branch\s+['\"]?([a-zA-Z\s]+)['\"]?", p)
        if m:
            return m.group(1).strip().upper()
        # Common branches
        known = ['pottammal', 'alappuzha', 'future', 'adimali', 'aakkulam', 'thrissur',
                 'kozhikode', 'calicut', 'kochi', 'ernakulam', 'kollam', 'trivandrum',
                 'thiruvananthapuram', 'palakkad', 'malappuram', 'kannur', 'kasaragod',
                 'kottayam', 'idukki', 'wayanad', 'pathanamthitta']
        for b in known:
            if b in p:
                return b.upper()
        return None

    @classmethod
    def match_template(cls, prompt: str) -> Optional[str]:
        p = prompt.lower().strip()
        branch = cls.extract_branch(p)
        year = cls.extract_year(p)
        month_num, month_year = cls.extract_month_year(p)
        year1, year2 = cls.extract_two_years(p)

        branch_filter_sd  = f" AND UPPER(\"Branch\") LIKE '%{branch}%'" if branch else ""
        branch_filter_mv  = f" AND UPPER(branch) LIKE '%{branch}%'" if branch else ""

        # ── PAYMENT MODE DETECTION (must run FIRST — prevents wrong template matches) ──
        # Detect queries about specific payment methods: EMI/Finance, UPI, Cash, Card, etc.
        is_emi_query   = bool(re.search(r'\b(emi|finance|financed|loan|instalment|installment)\b', p))
        is_upi_query   = bool(re.search(r'\b(upi|cashback|upi cashback)\b', p))
        is_cash_query  = bool(re.search(r'\bcash\b', p) and not re.search(r'\b(cashback|upi cashback)\b', p))
        is_card_query  = bool(re.search(r'\b(debit card|credit card|card payment)\b', p))
        is_voucher_query = bool(re.search(r'\b(gift voucher|voucher|redemption|point redemption|redeem)\b', p))
        # EXTRACT(MONTH/YEAR FROM parsed_date) CANNOT use a btree index.
        # parsed_date >= 'start' AND parsed_date < 'end' CAN use the index (10x faster).
        import calendar as _cal
        def _date_range(m, y):
            """Return (start_date_str, next_month_start_str) for index-friendly range."""
            if m and y:
                last_day = _cal.monthrange(int(y), int(m))[1]
                start = f"{y}-{int(m):02d}-01"
                nm, ny = (int(m) % 12) + 1, int(y) + (1 if int(m) == 12 else 0)
                end   = f"{ny}-{nm:02d}-01"
                return start, end
            return None, None

        def _base_filter(m, y, yr, branch):
            """Date-only filter with no payment column condition."""
            if m and y:
                s, e = _date_range(m, y)
                df = f"parsed_date >= '{s}' AND parsed_date < '{e}'"
            elif yr:
                df = f"parsed_date >= '{yr}-01-01' AND parsed_date < '{int(yr)+1}-01-01'"
            else:
                df = "parsed_date < '2026-06-01'"
            return f"{df}{branch}"

        def _emi_filter(col, m, y, yr, branch):
            """Fast Finance column filter: text-based not-null/not-zero + index date range."""
            # Text-based filter: much faster than COALESCE(col::numeric,0)>0 on unindexed text column
            pay_filter = f'"{col}" IS NOT NULL AND "{col}" != \'\'  AND "{col}" != \'0\' AND "{col}" != \'0.0\''
            if m and y:
                s, e = _date_range(m, y)
                df = f"AND parsed_date >= '{s}' AND parsed_date < '{e}'"
            elif yr:
                df = f"AND parsed_date >= '{yr}-01-01' AND parsed_date < '{int(yr)+1}-01-01'"
            else:
                df = "AND parsed_date < '2026-06-01'"
            return f"{pay_filter} {df}{branch}"

        # ── Payment Mode Comparison (EMI vs UPI) — CHECK THIS FIRST ──────────────────
        # IMPORTANT: this must run BEFORE individual EMI/UPI templates to avoid early exit
        if (is_emi_query and is_upi_query) or re.search(r'\b(compare|vs|versus|between)\b', p) and (is_emi_query or is_upi_query):
            wh = _base_filter(month_num, month_year, year, branch_filter_sd)
            return f"""
                SELECT
                    COALESCE(SUM(CASE WHEN "Finance" IS NOT NULL AND "Finance" != '' AND "Finance" != '0' THEN "Total Value"::numeric ELSE 0 END), 0)       AS emi_finance_sales,
                    COUNT(DISTINCT CASE WHEN "Finance" IS NOT NULL AND "Finance" != '' AND "Finance" != '0' THEN "Invoice Number" END)                       AS emi_invoices,
                    COALESCE(SUM(CASE WHEN "UPI Cashback" IS NOT NULL AND "UPI Cashback" != '' AND "UPI Cashback" != '0' THEN "Total Value"::numeric ELSE 0 END), 0)  AS upi_cashback_sales,
                    COUNT(DISTINCT CASE WHEN "UPI Cashback" IS NOT NULL AND "UPI Cashback" != '' AND "UPI Cashback" != '0' THEN "Invoice Number" END)              AS upi_invoices,
                    COALESCE(SUM("Total Value"::numeric), 0)                                                                                                AS total_sales
                FROM sales_data
                WHERE {wh};
            """

        # ── EMI / Finance customer queries ────────────────────────────────────────────
        if is_emi_query and re.search(r'\b(customer|customers|how many|count)\b', p):
            wh = _emi_filter('Finance', month_num, month_year, year, branch_filter_sd)
            return f"""
                SELECT
                    COUNT(DISTINCT "Customer Mobile")         AS emi_customers,
                    COALESCE(SUM("Finance"::numeric), 0)     AS total_financed_amount,
                    COUNT(DISTINCT "Invoice Number")          AS emi_invoices
                FROM sales_data
                WHERE {wh};
            """

        # ── EMI / Finance revenue queries ─────────────────────────────────────────────
        if is_emi_query and re.search(r'\b(revenue|sales|total|amount|value)\b', p):
            wh = _emi_filter('Finance', month_num, month_year, year, branch_filter_sd)
            return f"""
                SELECT
                    COALESCE(SUM("Finance"::numeric), 0)      AS total_financed_amount,
                    COALESCE(SUM("Total Value"::numeric), 0)  AS total_emi_revenue,
                    COUNT(DISTINCT "Invoice Number")           AS emi_invoices,
                    COUNT(DISTINCT "Customer Mobile")          AS emi_customers
                FROM sales_data
                WHERE {wh};
            """

        # (comparison block moved above, before individual payment templates)

        # ── UPI / Cashback queries ────────────────────────────────────────────────────
        if is_upi_query:
            wh = _emi_filter('UPI Cashback', month_num, month_year, year, branch_filter_sd)
            return f"""
                SELECT
                    COUNT(DISTINCT "Customer Mobile")              AS upi_customers,
                    COALESCE(SUM("UPI Cashback"::numeric), 0)     AS total_upi_cashback,
                    COUNT(DISTINCT "Invoice Number")               AS upi_invoices
                FROM sales_data
                WHERE {wh};
            """

        # ── Cash payment queries ──────────────────────────────────────────────────────
        if is_cash_query and re.search(r'\b(customer|customers|how many|count|payment)\b', p):
            wh = _emi_filter('Cash', month_num, month_year, year, branch_filter_sd)
            return f"""
                SELECT
                    COUNT(DISTINCT "Customer Mobile")          AS cash_customers,
                    COALESCE(SUM("Cash"::numeric), 0)         AS total_cash_amount,
                    COUNT(DISTINCT "Invoice Number")           AS cash_invoices
                FROM sales_data
                WHERE {wh};
            """

        # ── Credit/Debit Card queries ─────────────────────────────────────────────────
        if is_card_query:
            raw_col = 'Credit Card' if 'credit' in p else 'Debit Card'
            label   = raw_col.lower().replace(' ', '_')
            wh = _emi_filter(raw_col, month_num, month_year, year, branch_filter_sd)
            return f"""
                SELECT
                    COUNT(DISTINCT "Customer Mobile")              AS {label}_customers,
                    COALESCE(SUM("{raw_col}"::numeric), 0)        AS total_{label}_amount,
                    COUNT(DISTINCT "Invoice Number")               AS {label}_invoices
                FROM sales_data
                WHERE {wh};
            """

        # ── Gift Voucher / Point Redemption queries ───────────────────────────────────
        if is_voucher_query:
            if 'point' in p or 'redemption' in p or 'redeem' in p:
                raw_col, label = 'Point Redemption', 'redemption'
            else:
                raw_col, label = 'Gift Voucher', 'voucher'
            wh = _emi_filter(raw_col, month_num, month_year, year, branch_filter_sd)
            return f"""
                SELECT
                    COUNT(DISTINCT "Customer Mobile")              AS {label}_customers,
                    COALESCE(SUM("{raw_col}"::numeric), 0)        AS total_{label}_amount,
                    COUNT(DISTINCT "Invoice Number")               AS {label}_invoices
                FROM sales_data
                WHERE {wh};
            """


        # e.g. "top 5 future stores by sale in 2026"
        brand_match = re.search(
            r'(top|best|highest|rank).{0,10}(\d+).{0,20}(future|best\s*price|hypermarket|supermarket)',
            p
        ) or re.search(
            r'(future|best\s*price|hypermarket|supermarket).{0,30}(top|highest|best|rank).{0,10}(\d+)',
            p
        ) or re.search(
            r'(future|best\s*price).{0,50}(sale|revenue|performance)',
            p
        )
        if brand_match or (re.search(r'(future|best\s*price)', p) and re.search(r'(top|highest|best|sale|revenue)', p)):
            # Extract N (limit)
            m_n = re.search(r'\b(\d+)\b', p)
            limit = int(m_n.group(1)) if m_n and int(m_n.group(1)) <= 50 else 10

            # Extract brand keyword
            if 'future' in p:
                brand_kw = 'FUTURE'
            elif 'best price' in p:
                brand_kw = 'BEST PRICE'
            else:
                brand_kw = 'FUTURE'

            yr = year if year else 2026
            return f"""
                SELECT "Branch",
                       ROUND(SUM(revenue)::numeric, 2)   AS total_revenue,
                       SUM(invoices)::integer             AS total_invoices,
                       SUM(customers)::integer            AS total_customers
                FROM mv_monthly_summary
                WHERE "Branch" ILIKE '%{brand_kw}%'
                  AND EXTRACT(YEAR FROM month_date) = {yr}
                GROUP BY "Branch"
                ORDER BY total_revenue DESC
                LIMIT {limit};
            """

        # ── Month-over-Month Growth Rate ─────────────────────────────────────────────
        # Detects: "growth", "MoM", "month-over-month", "compared to last month", etc.
        # Requires exactly two months mentioned OR one month + "last month" / "previous month"
        mom_keywords = bool(re.search(
            r'\b(growth|mom|month.?over.?month|month.?on.?month|compared to|versus|vs|change|increase|decrease)\b', p
        ))
        two_months = re.findall(
            r'\b(january|february|march|april|may|june|july|august|september|october|november|december)\b', p
        )
        two_years_in_p = re.findall(r'\b(20\d{2})\b', p)

        MONTH_MAP = {
            'january':1,'february':2,'march':3,'april':4,'may':5,'june':6,
            'july':7,'august':8,'september':9,'october':10,'november':11,'december':12
        }

        # Guard: don't fire MoM if this is actually a New vs Repeat question
        _is_new_repeat_hint = bool(re.search(
            r'(new\s+(vs|versus|and|or)\s+repeat|repeat\s+(vs|versus|and|or)\s+new|repeat rate|repeat customer|new customer|customer split|customer type|customer breakdown|new.*repeat|repeat.*new)',
            p
        ))

        if mom_keywords and len(two_months) >= 2 and not _is_new_repeat_hint:
            m1_name, m2_name = two_months[0], two_months[1]
            m1_num = MONTH_MAP[m1_name]
            m2_num = MONTH_MAP[m2_name]
            # Resolve years for each month
            y1 = int(two_years_in_p[0]) if len(two_years_in_p) >= 1 else 2026
            y2 = int(two_years_in_p[1]) if len(two_years_in_p) >= 2 else y1
            s1, e1 = _date_range(m1_num, y1)
            s2, e2 = _date_range(m2_num, y2)
            return f"""
                WITH m1 AS (
                    SELECT
                        COALESCE(SUM("Total Value"::numeric), 0) AS revenue,
                        COUNT(DISTINCT "Invoice Number")          AS invoices,
                        COUNT(DISTINCT "Customer Mobile")         AS customers
                    FROM sales_data
                    WHERE parsed_date >= '{s1}' AND parsed_date < '{e1}'{branch_filter_sd}
                ),
                m2 AS (
                    SELECT
                        COALESCE(SUM("Total Value"::numeric), 0) AS revenue,
                        COUNT(DISTINCT "Invoice Number")          AS invoices,
                        COUNT(DISTINCT "Customer Mobile")         AS customers
                    FROM sales_data
                    WHERE parsed_date >= '{s2}' AND parsed_date < '{e2}'{branch_filter_sd}
                )
                SELECT
                    '{m1_name.title()} {y1}'                                                       AS period_1,
                    m1.revenue                                                                      AS period_1_revenue,
                    m1.invoices                                                                     AS period_1_invoices,
                    m1.customers                                                                    AS period_1_customers,
                    '{m2_name.title()} {y2}'                                                       AS period_2,
                    m2.revenue                                                                      AS period_2_revenue,
                    m2.invoices                                                                     AS period_2_invoices,
                    m2.customers                                                                    AS period_2_customers,
                    ROUND(((m2.revenue - m1.revenue) / NULLIF(m1.revenue, 0)) * 100, 2)            AS revenue_growth_pct,
                    ROUND(((m2.invoices - m1.invoices) / NULLIF(m1.invoices::numeric, 0)) * 100, 2) AS invoices_growth_pct,
                    ROUND(((m2.customers - m1.customers) / NULLIF(m1.customers::numeric, 0)) * 100, 2) AS customers_growth_pct
                FROM m1, m2;
            """

        # ── New vs Repeat Customer Breakdown ─────────────────────────────────────────
        # Detects: "new vs repeat", "new and repeat", "repeat rate", "loyalty split" etc.
        is_new_repeat = bool(re.search(
            r'(new\s+(vs|versus|and|or)\s+repeat|repeat\s+(vs|versus|and|or)\s+new|repeat rate|repeat customer|new customer|loyalty split|customer split|customer type|customer breakdown|new.*repeat|repeat.*new)',
            p
        ))
        if is_new_repeat:
            # Multi-month breakdown: if two months mentioned, show side-by-side
            if len(two_months) >= 2 and 'MONTH_MAP' not in dir():
                MONTH_MAP = {
                    'january':1,'february':2,'march':3,'april':4,'may':5,'june':6,
                    'july':7,'august':8,'september':9,'october':10,'november':11,'december':12
                }
            if len(two_months) >= 2:
                m1_name2, m2_name2 = two_months[0], two_months[1]
                MONTH_MAP2 = {
                    'january':1,'february':2,'march':3,'april':4,'may':5,'june':6,
                    'july':7,'august':8,'september':9,'october':10,'november':11,'december':12
                }
                m1_num2 = MONTH_MAP2[m1_name2]
                m2_num2 = MONTH_MAP2[m2_name2]
                y1b = int(two_years_in_p[0]) if len(two_years_in_p) >= 1 else 2026
                y2b = int(two_years_in_p[1]) if len(two_years_in_p) >= 2 else y1b
                s1b, e1b = _date_range(m1_num2, y1b)
                s2b, e2b = _date_range(m2_num2, y2b)
                return f"""
                    SELECT
                        TO_CHAR(DATE_TRUNC('month', parsed_date), 'Month YYYY')   AS month,
                        "Customer Type"                                             AS customer_type,
                        COUNT(DISTINCT "Customer Mobile")                           AS unique_customers,
                        COALESCE(SUM("Total Value"::numeric), 0)                   AS revenue,
                        COUNT(DISTINCT "Invoice Number")                            AS invoices,
                        ROUND(100.0 * COUNT(DISTINCT "Customer Mobile") /
                            NULLIF(SUM(COUNT(DISTINCT "Customer Mobile")) OVER (PARTITION BY DATE_TRUNC('month', parsed_date)), 0), 2
                        )                                                           AS pct_of_monthly_customers
                    FROM sales_data
                    WHERE parsed_date >= '{s1b}' AND parsed_date < '{e2b}'{branch_filter_sd}
                    GROUP BY DATE_TRUNC('month', parsed_date), "Customer Type"
                    ORDER BY DATE_TRUNC('month', parsed_date), "Customer Type";
                """
            else:
                # Single period: monthly breakdown for the year or specific month
                wh = _base_filter(month_num, month_year, year, branch_filter_sd)
                return f"""
                    SELECT
                        TO_CHAR(DATE_TRUNC('month', parsed_date), 'Month YYYY')   AS month,
                        "Customer Type"                                             AS customer_type,
                        COUNT(DISTINCT "Customer Mobile")                           AS unique_customers,
                        COALESCE(SUM("Total Value"::numeric), 0)                   AS revenue,
                        COUNT(DISTINCT "Invoice Number")                            AS invoices
                    FROM sales_data
                    WHERE {wh}
                    GROUP BY DATE_TRUNC('month', parsed_date), "Customer Type"
                    ORDER BY DATE_TRUNC('month', parsed_date), "Customer Type";
                """

        # ── 1. CROSS-YEAR COHORT: "purchased in 20XX but NOT in 20YY" ──────────
        not_pattern = re.search(
            r'(purchase|bought|shopp|customer).{0,30}(in\s+)?(\d{4}).{0,20}(not|but not|without|never).{0,20}(in\s+)?(\d{4})',
            p
        )
        if not_pattern or (
            re.search(r'(not|but not).{0,20}(purchase|bought|shopp)', p) and year1 and year2
        ):
            if year1 and year2:
                y_a, y_b = year1, year2
            else:
                years = re.findall(r'\b(20\d{2})\b', p)
                y_a = int(years[0]) if len(years) >= 1 else 2024
                y_b = int(years[1]) if len(years) >= 2 else 2026
            # Use fast pivot MV — sub-second vs 3+ minutes on raw table
            return f"""
                SELECT COUNT(*) AS unique_customer_count
                FROM mv_cohort_cross_year
                WHERE in_{y_a} = 1 AND in_{y_b} = 0;
            """

        # ── 2. CROSS-YEAR COHORT: "purchased in 20XX and also in 20YY" ─────────
        also_pattern = re.search(
            r'(purchase|bought|shopp).{0,30}(in\s+)?(\d{4}).{0,30}(and|also|again).{0,20}(in\s+)?(\d{4})',
            p
        )
        if also_pattern and year1 and year2:
            return f"""
                SELECT COUNT(*) AS unique_customer_count
                FROM mv_cohort_cross_year
                WHERE in_{year1} = 1 AND in_{year2} = 1;
            """

        # ── 3. REVENUE TODAY ──────────────────────────────────────────────────
        if re.search(r'\b(revenue|sales)\b.*\btoday\b', p) or re.search(r'\btoday\b.*\b(revenue|sales)\b', p):
            return f"""
                SELECT COALESCE(SUM(revenue), 0) AS today_revenue,
                       COALESCE(SUM(invoices), 0) AS today_invoices,
                       COALESCE(SUM(customers), 0) AS today_customers
                FROM mv_daily_summary
                WHERE date = CURRENT_DATE{branch_filter_mv};
            """

        # ── 4. REVENUE THIS MONTH / MTD ───────────────────────────────────────
        if re.search(r'\b(revenue|sales)\b.*\b(this month|mtd|current month)\b', p) or \
           re.search(r'\b(this month|mtd|current month)\b.*\b(revenue|sales)\b', p):
            return f"""
                SELECT COALESCE(SUM(revenue), 0) AS mtd_revenue,
                       COALESCE(SUM(invoices), 0) AS mtd_invoices,
                       COALESCE(SUM(customers), 0) AS mtd_customers
                FROM mv_monthly_summary
                WHERE month_date = '2026-05-01'{branch_filter_mv};
            """

        # ── 5. REVENUE FOR SPECIFIC MONTH+YEAR (e.g. "april 2026 revenue") ───
        if month_num and month_year and re.search(r'\b(revenue|sales|total)\b', p):
            return f"""
                SELECT COALESCE(SUM(revenue), 0) AS revenue,
                       COALESCE(SUM(invoices), 0) AS invoices,
                       COALESCE(SUM(customers), 0) AS customers
                FROM mv_monthly_summary
                WHERE EXTRACT(MONTH FROM month_date) = {month_num}
                  AND EXTRACT(YEAR  FROM month_date) = {month_year}{branch_filter_mv};
            """

        # ── 6. REVENUE FOR A SPECIFIC YEAR ────────────────────────────────────
        if year and re.search(r'\b(revenue|sales|total)\b', p) and not month_num:
            return f"""
                SELECT COALESCE(SUM(revenue), 0) AS annual_revenue,
                       COALESCE(SUM(invoices), 0) AS annual_invoices,
                       COALESCE(SUM(customers), 0) AS annual_customers
                FROM mv_monthly_summary
                WHERE EXTRACT(YEAR FROM month_date) = {year}{branch_filter_mv};
            """

        # ── 7. UNIQUE / TOTAL CUSTOMERS (overall) ─────────────────────────────
        # GUARD: Do NOT match if query is about a specific payment mode (EMI, finance, UPI, etc.)
        if re.search(r'\b(total|unique)?\s*(customer count|customers|customer)\b', p) \
           and not year and not month_num \
           and not re.search(r'(not|without|dormant|repeat|new|resurrected|emi|finance|financed|loan|upi|cash|debit|credit|voucher|redeem)', p):
            if branch:
                return f"""
                    SELECT COUNT(DISTINCT "Customer Mobile") AS total_customers
                    FROM sales_data
                    WHERE UPPER("Branch") LIKE '%{branch}%';
                """
            return """
                SELECT COUNT(DISTINCT mobile) AS total_customers
                FROM mv_customer_dates;
            """

        # ── 8. NEW CUSTOMERS (specific year) ──────────────────────────────────
        if re.search(r'\b(new customers|new customer count|first.time)\b', p) and year:
            return f"""
                SELECT COUNT(DISTINCT "Customer Mobile") AS new_customers
                FROM sales_data
                WHERE EXTRACT(YEAR FROM parsed_date) = {year}
                  AND "Customer Mobile" NOT IN (
                      SELECT DISTINCT "Customer Mobile"
                      FROM sales_data
                      WHERE parsed_date < '{year}-01-01'
                  ){branch_filter_sd};
            """

        # ── 9. REPEAT CUSTOMERS ───────────────────────────────────────────────
        if re.search(r'\brepeat\b.*\bcustomer', p):
            if year:
                return f"""
                    SELECT COUNT(DISTINCT sd."Customer Mobile") AS repeat_customers
                    FROM sales_data sd
                    WHERE EXTRACT(YEAR FROM sd.parsed_date) = {year}
                      AND EXISTS (
                          SELECT 1 FROM sales_data sd2
                          WHERE sd2."Customer Mobile" = sd."Customer Mobile"
                            AND sd2.parsed_date < '{year}-01-01'
                      ){branch_filter_sd};
                """
            # Default: repeat customers as of end of May 2026 (last complete month)
            return f"""
                SELECT COUNT(mobile) AS repeat_customers
                FROM mv_customer_dates
                WHERE fv_month < '2026-05-01'
                  AND lv_month >= '2026-05-01';
            """

        # ── 10. DORMANT CUSTOMERS (bought in year X, not since) ───────────────
        if re.search(r'\b(dormant|inactive|lost)\b.*\bcustomer', p) and year:
            return f"""
                SELECT COALESCE(SUM(unique_customers), 0) AS dormant_customers
                FROM mv_dormant_reactivation
                WHERE cohort_year = {year}
                  AND first_2026_month IS NULL;
            """

        # ── 11. RESURRECTION / REACTIVATION RATE ─────────────────────────────
        if re.search(r'\b(resurrection|reactivat|revival)\b', p):
            if branch:
                return f"""
                    SELECT branch_name, resurrected_customers, cohort_size, resurrection_rate
                    FROM mv_branch_resurrection_2024_2026
                    WHERE UPPER(branch_name) LIKE '%{branch}%';
                """
            return """
                SELECT branch_name, resurrected_customers, cohort_size, resurrection_rate
                FROM mv_branch_resurrection_2024_2026
                ORDER BY resurrected_customers DESC
                LIMIT 10;
            """

        # ── 12. TOP BRANCHES ──────────────────────────────────────────────────
        if re.search(r'\btop\b.{0,10}(branch|store|outlet)', p):
            limit = 10
            m_lim = re.search(r'\btop\s+(\d+)\b', p)
            if m_lim:
                limit = int(m_lim.group(1))
            if year and month_num:
                return f"""
                    SELECT "Branch", SUM(revenue) AS revenue, SUM(invoices) AS invoices
                    FROM mv_monthly_summary
                    WHERE EXTRACT(MONTH FROM month_date) = {month_num}
                      AND EXTRACT(YEAR  FROM month_date) = {year}
                    GROUP BY "Branch" ORDER BY revenue DESC LIMIT {limit};
                """
            return f"""
                SELECT "Branch", SUM(revenue) AS revenue, SUM(invoices) AS invoices
                FROM mv_monthly_summary
                WHERE month_date = DATE_TRUNC('month', CURRENT_DATE)
                GROUP BY "Branch" ORDER BY revenue DESC LIMIT {limit};
            """

        # ── 13. ATV (Average Transaction Value) ───────────────────────────────
        if re.search(r'\b(atv|average transaction value|avg.*transaction)\b', p):
            if year:
                return f"""
                    SELECT COALESCE(SUM("Total Value") / NULLIF(COUNT(DISTINCT "Invoice Number"), 0), 0) AS atv
                    FROM sales_data
                    WHERE EXTRACT(YEAR FROM parsed_date) = {year}{branch_filter_sd};
                """
            return f"""
                SELECT COALESCE(SUM("Total Value") / NULLIF(COUNT(DISTINCT "Invoice Number"), 0), 0) AS atv
                FROM sales_data
                WHERE parsed_date >= CURRENT_DATE - INTERVAL '30 days'{branch_filter_sd};
            """

        # ── 14. MONTHLY TREND / MONTHLY BREAKDOWN ────────────────────────────
        if re.search(r'\b(monthly trend|month.by.month|monthly breakdown|by month)\b', p):
            if year:
                yr_end = f"'{year}-12-31'" if year < 2026 else "'2026-06-01'"  # cap 2026 at May
                return f"""
                    SELECT TO_CHAR(month_date, 'Mon YYYY') AS month,
                           COALESCE(SUM(revenue), 0) AS revenue,
                           COALESCE(SUM(customers), 0) AS customers
                    FROM mv_monthly_summary
                    WHERE EXTRACT(YEAR FROM month_date) = {year}
                      AND month_date < {yr_end}{branch_filter_mv}
                    GROUP BY month_date ORDER BY month_date;
                """
            return f"""
                SELECT TO_CHAR(month_date, 'Mon YYYY') AS month,
                       COALESCE(SUM(revenue), 0) AS revenue,
                       COALESCE(SUM(customers), 0) AS customers
                FROM mv_monthly_summary
                WHERE month_date >= DATE_TRUNC('year', CURRENT_DATE)
                  AND month_date < '2026-06-01'{branch_filter_mv}
                GROUP BY month_date ORDER BY month_date;
            """

        # ── 14b. LAST N MONTHS TREND ──────────────────────────────────────────
        last_n_match = re.search(r'last\s+(\d+|one|two|three|four|five|six|seven|eight|nine|ten|twelve)\s*month', p)
        if last_n_match and re.search(r'(trend|revenue|sales|growth)', p):
            word_to_num = {'one':1,'two':2,'three':3,'four':4,'five':5,'six':6,
                           'seven':7,'eight':8,'nine':9,'ten':10,'twelve':12}
            raw = last_n_match.group(1).lower()
            n_months = int(raw) if raw.isdigit() else word_to_num.get(raw, 6)
            # Cap: last N months ending at May 2026
            return f"""
                SELECT TO_CHAR(month_date, 'Mon YYYY') AS month,
                       COALESCE(SUM(revenue), 0) AS revenue,
                       COALESCE(SUM(customers), 0) AS customers
                FROM mv_monthly_summary
                WHERE month_date >= ('2026-06-01'::date - INTERVAL '{n_months} months')
                  AND month_date < '2026-06-01'{branch_filter_mv}
                GROUP BY month_date ORDER BY month_date;
            """

        # ── 15. LOYALTY KPIs ─────────────────────────────────────────────────
        if re.search(r'\b(loyalty kpi|loyalty metric|loyalty score|loyalty rate)\b', p):
            return """
                SELECT metric_name, metric_value, metric_date
                FROM mv_loyalty_kpis
                ORDER BY metric_date DESC LIMIT 20;
            """

        # ── 16. GAP ANALYSIS ─────────────────────────────────────────────────
        if re.search(r'\b(gap analysis|gap days|days between|visit gap|purchase gap)\b', p):
            return f"""
                SELECT gap_bucket, customer_count, avg_gap_days
                FROM mv_gap_analysis{(' WHERE UPPER(branch) LIKE ' + "'%" + branch + "%'") if branch else ""}
                ORDER BY avg_gap_days;
            """

        # ── 17. RFM SEGMENTS ─────────────────────────────────────────────────
        if re.search(r'\b(rfm|rfm segment|champions|at risk|loyal customer|hibernating|lost customer)\b', p):
            return """
                SELECT segment, COUNT(*) AS customer_count,
                       AVG(recency_score) AS avg_recency,
                       AVG(frequency_score) AS avg_frequency,
                       AVG(monetary_score) AS avg_monetary
                FROM mv_rfm_segments
                GROUP BY segment ORDER BY customer_count DESC;
            """

        # ── 18. MONTHLY RETENTION 2026 ────────────────────────────────────────
        if re.search(r'\b(retention|retained).{0,20}2026\b', p):
            return """
                SELECT month_label, month_start, unique_customers, total_sales
                FROM mv_monthly_retention_2026
                ORDER BY month_start;
            """

        # ── 19. CUSTOMER COUNT IN SPECIFIC YEAR ───────────────────────────────
        # GUARD: Do NOT match if query is about a specific payment mode (EMI, finance, UPI, etc.)
        if re.search(r'\b(customer|customers)\b', p) and year \
           and not re.search(r'(not|without|dormant|repeat|new|resurrected|emi|finance|financed|loan|upi|cash|debit|credit|voucher|redeem)', p):
            return f"""
                SELECT COUNT(DISTINCT "Customer Mobile") AS unique_customers
                FROM sales_data
                WHERE EXTRACT(YEAR FROM parsed_date) = {year}{branch_filter_sd};
            """

        return None
