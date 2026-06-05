import re
from typing import Optional

class SQLTemplateEngine:
    """
    High-performance Regex-based SQL Template Engine for instantaneous KPI retrieval.
    Avoids LLM generation entirely for known business definitions.
    """

    @staticmethod
    def extract_branch(prompt: str) -> Optional[str]:
        # Simple extraction logic for known branches
        branches = ['pottammal', 'alappuzha', 'future']
        for b in branches:
            if b in prompt.lower():
                return b.upper()
        return None

    @classmethod
    def match_template(cls, prompt: str) -> Optional[str]:
        p = prompt.lower().strip()
        branch = cls.extract_branch(p)
        branch_filter = f" AND UPPER(\"Branch\") LIKE '%{branch}%'" if branch else ""
        branch_filter_mv = f" AND UPPER(branch) LIKE '%{branch}%'" if branch else ""

        # 1. Revenue Today
        if re.search(r'\b(revenue|sales)\b.*\btoday\b', p) or re.search(r'\btoday\b.*\b(revenue|sales)\b', p):
            return f"""
                SELECT COALESCE(SUM(revenue), 0) as today_revenue 
                FROM mv_daily_summary 
                WHERE date = CURRENT_DATE{branch_filter_mv};
            """

        # 2. Revenue This Month
        if re.search(r'\b(revenue|sales)\b.*\b(this month|mtd)\b', p) or re.search(r'\b(this month|mtd)\b.*\b(revenue|sales)\b', p):
            return f"""
                SELECT COALESCE(SUM(revenue), 0) as mtd_revenue 
                FROM mv_monthly_summary 
                WHERE month = DATE_TRUNC('month', CURRENT_DATE){branch_filter_mv};
            """

        # 3. Total Customer Count
        if re.search(r'\b(total|unique)?\s*(customer count|customers)\b', p) and '202' not in p:
            if branch:
                return f"""
                    SELECT COUNT(DISTINCT "Customer Mobile") as total_customers 
                    FROM v_sales_data
                    WHERE UPPER("Branch") LIKE '%{branch}%';
                """
            return """
                SELECT COUNT(DISTINCT mobile) as total_customers 
                FROM mv_customer_dates;
            """

        # 4. ATV (Average Transaction Value)
        if re.search(r'\b(atv|average transaction value)\b', p):
            return f"""
                SELECT COALESCE(SUM("Total Value") / NULLIF(COUNT(DISTINCT "Invoice Number"), 0), 0) as atv 
                FROM v_sales_data 
                WHERE "Date" >= CURRENT_DATE - INTERVAL '30 days'{branch_filter};
            """

        # 5. Top Branches (MTD)
        if re.search(r'\btop\b.*\bbranches\b', p):
            return """
                SELECT branch, SUM(revenue) as total_revenue
                FROM mv_monthly_summary
                WHERE month = DATE_TRUNC('month', CURRENT_DATE)
                GROUP BY branch
                ORDER BY total_revenue DESC
                LIMIT 5;
            """

        # 6. Repeat Customer Count
        if re.search(r'\b(repeat)\b.*\b(customers)\b', p):
            return f"""
                SELECT COUNT(mobile) as repeat_customers
                FROM mv_customer_dates
                WHERE fv_month < DATE_TRUNC('month', CURRENT_DATE)
                  AND lv_month >= DATE_TRUNC('month', CURRENT_DATE);
            """

        return None
