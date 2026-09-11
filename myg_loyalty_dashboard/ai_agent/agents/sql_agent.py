import os
import requests
from ..services.query_validator import QueryValidator
from ..services.schema_service import SchemaService
from ..services.knowledge_base import DATABASE_DICTIONARY
from ..services.security_service import SecurityService
class SQLAgent:
    def __init__(self):
        self.validator = QueryValidator()
        self.schema_service = SchemaService()
        self.knowledge_base = DATABASE_DICTIONARY
        self.model = "gpt-4" # Placeholder for actual LLM integration

    def generate_query(self, user_prompt: str, user_context: dict, model_name: str = "nvidia/nemotron-3-ultra-550b-a55b:free") -> tuple[str, str]:
        """
        Translates Natural Language to SQL.
        Returns (generated_sql, error_msg)
        """
        schema_text = """
Table: azure_invoice_report
Description: Core invoice-level sales data. Use for revenue, discounts, and customer details.
Columns: date (DateTime), time (String), invoice_no (String), branch (String), rbm (String), bdm (String), customer_mobile (String), customer_pincode (String), customer_gstin (String), customer_type (String), sales_staff_code (String), billing_staff_code (String), invoice_total (Float32), discount (Float32), buyback (Float32), deductions (Float32), exchange (Float32), financier_code (String), financier_name (String), scheme (String), loan_amount (Float32)

Table: azure_sales_report
Description: Item-level sales data. Join with item_master to get product details. Join with azure_invoice_report using invoice_no for full transaction.
Columns: date (DateTime), invoice_no (String), branch (String), item_code (String), imei_batch (Nullable(String)), qty (Float32), mop (Float32), discount (Float32), buyback (Float32), sold_price (Float32), taxable (Float32)

Table: item_master
Description: Product and category master list. Join on item_code.
Columns: item_code (String), product (String), brand (String), category (String), item_name (String), item_group (String), item_category (String), hsn (String), tax_percent (Float32), mop (Float32), mrp (Float32)

Table: branch_master
Description: Store location details. Join on branch_name or code.
Columns: code (String), branch_name (String), rbm (String), bdm (String), address (String), district (String), pincode (String), email (String), gst_no (String), store_type (String), phone_no (String), category (String), mapped_warehouse (String)
"""

        # Phase 2: Live LLM Generation using NVIDIA API
        system_prompt = f"""
You are MYG Loyalty Business Intelligence AI.
You have access to sales transaction data.

Column meanings:
- Date = Transaction date
- Invoice Number = Unique invoice
- Customer Mobile = Unique customer identifier
- RBM = Regional Business Manager
- BDM = Business Development Manager
- Branch = Store name
- Staff = Sales executive
- Customer Type = New or Repeat customer
- Total Value = Gross sale value
- Discount = Discount amount
- Exchange = Exchange amount
- Finance = Financed amount
- Cash = Cash payment
- Debit Card = Debit card payment
- Credit Card = Credit card payment
- UPI Cashback = Cashback amount
- Point Redemption = Loyalty points redeemed
- Gift Voucher = Voucher value

When answering questions:
1. Understand the business intent.
2. Generate ClickHouse SQL (NOT PostgreSQL).
3. Use only required columns.
4. Never use SELECT *.
5. Calculate revenue using invoice_total (from azure_invoice_report).
6. Calculate customer count using DISTINCT customer_mobile.
7. Calculate invoice count using DISTINCT invoice_no.
8. Group by branch, rbm, or bdm when relevant.
9. Return exact values from the database.
10. Provide business insights after retrieving data (if applicable).
11. Calculate Average Lifetime Value (LTV) using: `SUM(invoice_total) / NULLIF(COUNT(DISTINCT customer_mobile), 0)`
12. Calculate Average Ticket Value (ATV) using: `SUM(invoice_total) / NULLIF(COUNT(DISTINCT invoice_no), 0)`
Always explain assumptions.

Schema (Only use these tables):
{schema_text}

CRITICAL SYSTEM RULES (DO NOT IGNORE):
1. You are generating ClickHouse SQL. Use ClickHouse functions ONLY:
   - For monthly grouping: `toStartOfMonth(date)` (NOT DATE_TRUNC)
   - For date formatting: `formatDateTime(toStartOfMonth(date), '%Y-%m')` (NOT TO_CHAR)
   - For ILIKE: use `lower(column) LIKE lower('%value%')` (ClickHouse has no ILIKE)
   - For date arithmetic: `date >= '2025-12-01'` (use string dates directly)
   - NEVER use PostgreSQL-specific syntax: no `::numeric`, no `EXTRACT()`, no `INTERVAL 'N months'`, no `DATE_TRUNC`, no `TO_CHAR`, no `ILIKE`
2. NEVER end the query with a semicolon (;). ClickHouse rejects multi-statements.
3. Only use the exact columns explicitly listed in the schema above. Do NOT invent or guess columns.
4. If filtering by text, use `lower(column) LIKE lower('%value%')` for case-insensitive matching.
5. When using SUM() on numbers, always wrap it in `COALESCE(SUM(...), 0)` so it doesn't return NULL.
6. NEVER use text matching on DATE columns. Use direct date comparisons: `date >= '2024-01-01' AND date < '2025-01-01'`
7. IMPORTANT: If the user asks for a "trend", ALWAYS aggregate by MONTH using `toStartOfMonth(date)` with GROUP BY.
8. IF YOU CANNOT generate a SQL query, fallback to: `SELECT 'ERROR: I could not understand your request' AS error`
9. CRITICAL DATA FRESHNESS RULE — DATA IS AVAILABLE UP TO SEPTEMBER 5, 2026:
   - The database has complete data up to and including September 5, 2026.
   - September 2026 (after Sep 5) and beyond has NO data yet.
   - For trend queries, cap at '2026-09-06' (exclusive) to include up to Sep 5.
   - Example "last 6 months" = Mar 2026 through Sep 5 2026: `date >= '2026-03-01' AND date < '2026-09-06'`
   - Example "last 12 months" = Sep 2025 through Sep 5 2026: `date >= '2025-09-01' AND date < '2026-09-06'`
   - NEVER use today() or now() as the upper bound for trend queries — always use '2026-09-06'.
10. CRITICAL BUSINESS LOGIC:
    - "Future Stores"/"Future Branches": filter `lower(branch) LIKE '%future%'`
    - "Normal Stores": filter `lower(branch) NOT LIKE '%future%'`
13. PAYMENT MODE COMPARISONS:
    - The columns "EMI", "Finance", "UPI Cashback", "Cash", "Debit Card", and "Credit Card" are TEXT columns indicating the amount paid.
    - To analyze or compare sales across payment methods, you MUST sum the "Total Value" column conditionally using CASE statements.
    - DO NOT try to sum the text columns directly.
    - Example for EMI vs UPI comparison: `SELECT SUM(CASE WHEN "Finance" IS NOT NULL AND "Finance" != '' AND "Finance" != '0' THEN "Total Value" ELSE 0 END) AS emi_sales, SUM(CASE WHEN "UPI Cashback" IS NOT NULL AND "UPI Cashback" != '' AND "UPI Cashback" != '0' THEN "Total Value" ELSE 0 END) AS upi_sales FROM sales_data;`
11. COHORT QUERIES (customers who bought in year X but NOT in year Y):
    Use azure_invoice_report with customer_mobile column:
    SELECT COUNT(DISTINCT customer_mobile) AS unique_customer_count
    FROM azure_invoice_report
    WHERE toYear(date) = X
      AND customer_mobile NOT IN (
          SELECT DISTINCT customer_mobile
          FROM azure_invoice_report
          WHERE toYear(date) = Y
      )

EXAMPLES:
Q: dormant customers from 2024 who came back in 2026?
A: ```sql\nWITH cohort24 AS (SELECT "Customer Mobile" FROM v_sales_data WHERE "Date">='2024-01-01' AND "Date"<'2025-01-01'), cohort26 AS (SELECT "Customer Mobile" FROM v_sales_data WHERE "Date">='2026-01-01' AND "Date"<'2027-01-01') SELECT COUNT(DISTINCT a."Customer Mobile") FROM cohort26 a INNER JOIN cohort24 b ON a."Customer Mobile" = b."Customer Mobile";\n```

Q: unique customer count whose purchase in 2024 but not purchase in 2026
A: ```sql\nSELECT COUNT(DISTINCT sd."Customer Mobile") AS unique_customer_count FROM sales_data sd WHERE EXTRACT(YEAR FROM sd.parsed_date) = 2024 AND NOT EXISTS (SELECT 1 FROM sales_data sd2 WHERE sd2."Customer Mobile" = sd."Customer Mobile" AND EXTRACT(YEAR FROM sd2.parsed_date) = 2026);\n```

Q: how many customers bought in 2023 but did not buy in 2024?
A: ```sql\nSELECT COUNT(DISTINCT sd."Customer Mobile") AS unique_customer_count FROM sales_data sd WHERE EXTRACT(YEAR FROM sd.parsed_date) = 2023 AND NOT EXISTS (SELECT 1 FROM sales_data sd2 WHERE sd2."Customer Mobile" = sd."Customer Mobile" AND EXTRACT(YEAR FROM sd2.parsed_date) = 2024);\n```
"""
        
        # ── Route: Experiential Labs API (gpt-6-astra) ───────────
        from openai import OpenAI
        XPL_API_KEY = "xpl_3f8ef2aa59f0cae1539e2118782e5f6dd34c993f"
        
        try:
            client = OpenAI(
                base_url="https://api.experientiallabs.ai/v1",
                api_key=XPL_API_KEY
            )
            
            response = client.chat.completions.create(
                model="gpt-6-astra",
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user",   "content": f"Write a Postgres SQL query for: {user_prompt}"}
                ],
                max_tokens=1024,
                temperature=0.1,
                stream=False,
                timeout=45
            )
            
            sql_result = response.choices[0].message.content.strip()
            
            import re
            
            # Robust JSON extraction to handle weird API wrappers returning JSON strings
            try:
                import json
                while True:
                    parsed = json.loads(sql_result)
                    if isinstance(parsed, dict) and "text" in parsed:
                        sql_result = parsed["text"].strip()
                    else:
                        break
            except Exception:
                pass
            
            sql_match = re.search(r'```(?:sql|postgresql)?\s*(.*?)\s*```', sql_result, re.DOTALL | re.IGNORECASE)
            if sql_match:
                generated_sql = sql_match.group(1).strip()
            else:
                select_match = re.search(r'\b(SELECT|WITH)\b.*', sql_result, re.IGNORECASE | re.DOTALL)
                if select_match:
                    generated_sql = select_match.group(0).strip()
                else:
                    generated_sql = sql_result.replace("```sql", "").replace("```postgresql", "").replace("```", "").strip()
                
            error_msg = None
            if not generated_sql.upper().startswith("SELECT") and not generated_sql.upper().startswith("WITH"):
                # If everything failed, inject a safe fallback SQL
                generated_sql = "SELECT 'ERROR: Could not generate SQL for this prompt. Try rephrasing.' AS result;"
        except Exception as e:
            generated_sql = None
            error_msg = f"gpt-6-astra error: {str(e)}"

        if not generated_sql:
            return None, f"AI model failed to generate a valid SQL query. Error: {error_msg}"

        # Phase 2: Security Validation
        is_safe, msg = self.validator.validate_safety(generated_sql)
        if not is_safe:
            return None, f"Security Error: {msg}"
            
        # Bypass Phase 4 schema validation because ClickHouse tables aren't in Django PG catalog
            
        # Phase 5: Role Security (RLS)
        is_rls_valid, final_query, rls_msg = SecurityService.enforce_row_level_security(generated_sql, user_context)
        if not is_rls_valid:
            return None, rls_msg
            
        # Phase 6: Post-generation SQL Optimization
        from ..services.sql_optimizer import SQLOptimizer
        optimized_query = SQLOptimizer.optimize_query(final_query)
        
        return optimized_query, None

    def execute_query(self, query: str) -> list:
        """
        Executes the validated query against the ClickHouse database.
        """
        from analytics.clickhouse_service import get_ch_client
        
        # ClickHouse HTTP client does NOT allow trailing semicolons (multi-statement guard)
        query = query.strip().rstrip(';').strip()
        
        try:
            client = get_ch_client()
            result = client.query(query)
            
            # Form dict list from result_columns and result_rows
            columns = result.column_names
            rows = result.result_rows
            
            output = []
            for row in rows:
                output.append(dict(zip(columns, row)))
            return output
        except Exception as e:
            return [{"error": str(e)}]
