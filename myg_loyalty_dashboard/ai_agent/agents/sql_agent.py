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
        from ai_agent.services.schema_catalog import SchemaCatalogService
        
        # 1. Fetch Top 3 Relevant Tables using pgvector
        relevant_tables = SchemaCatalogService.search_relevant_tables(user_prompt, top_k=3)
        
        schema_text = ""
        for t in relevant_tables:
            schema_text += f"\nTable: {t['table']}\nDescription: {t['description']}\nColumns: {', '.join(t['columns'])}\n"
            if t['table'] == 'v_sales_data' or t['table'] == 'sales_data':
                try:
                    from ai_agent.services.sales_data_dictionary import SALES_DATA_DICTIONARY
                    import json
                    schema_text += f"\nDETAILED DATA DICTIONARY FOR {t['table']}:\n{json.dumps(SALES_DATA_DICTIONARY, indent=2)}\n"
                except Exception as e:
                    print("Could not load dictionary:", e)
            
        if not schema_text:
            schema_text = "No relevant tables found. Default to v_sales_data."

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
2. Generate PostgreSQL SQL.
3. Use only required columns.
4. Never use SELECT *.
5. Calculate revenue using Total Value.
6. Calculate customer count using DISTINCT Customer Mobile.
7. Calculate invoice count using DISTINCT Invoice Number.
8. Group by Branch, Staff, RBM, or BDM when relevant.
9. Return exact values from the database.
10. Provide business insights after retrieving data (if applicable).
11. Calculate Average Lifetime Value (LTV) using: `SUM("Total Value") / NULLIF(COUNT(DISTINCT "Customer Mobile"), 0)`
12. Calculate Average Ticket Value (ATV) using: `SUM("Total Value") / NULLIF(COUNT(DISTINCT "Invoice Number"), 0)`
Always explain assumptions.

Schema (Only use these tables):
{schema_text}

CRITICAL SYSTEM RULES (DO NOT IGNORE):
1. You MUST use the exact column names specified for each table. If a table has a column named `mobile`, you MUST use `mobile`. NEVER hallucinate `"Customer Mobile"` unless it is explicitly listed for that specific table. Failure to use the exact column names will crash the database.
2. Only use the exact columns explicitly listed in the schema above. Do NOT invent or guess columns.
3. If filtering by text, always use UPPER(column_name) LIKE UPPER('%value%') or ILIKE to prevent case sensitivity.
4. When asked "how many" or to count something: If the table has a pre-aggregated column like "unique_customers", "customers", or "cohort_size", you MUST SUM that specific column (e.g. `SUM(customers)`). ONLY use COUNT(*) or COUNT(DISTINCT column) if the table contains raw unaggregated rows.
5. When using SUM() on numbers, always wrap it in COALESCE(SUM(...), 0) so it doesn't return NULL.
6. If the user asks for a metric that already exists as a column in a materialized view (e.g. resurrection_rate), just SELECT it. Do not attempt to calculate it with SUM/COUNT.
7. IMPORTANT: NEVER use text matching (LIKE/ILIKE) on DATE columns! If filtering a DATE column by a specific year (e.g. 2024), use `date_column >= '2024-01-01' AND date_column < '2025-01-01'`. If filtering by a month (e.g. April 2026), use `EXTRACT(MONTH FROM date_column) = 4 AND EXTRACT(YEAR FROM date_column) = 2026`.
8. IMPORTANT: If the user asks for a "trend" (like revenue trend or sales trend) without specifying a time interval, ALWAYS aggregate the data by MONTH using DATE_TRUNC('month', date_column) AND include a GROUP BY DATE_TRUNC('month', date_column) to show a smooth monthly trend, not a noisy daily one.
9. IF YOU CANNOT generate a SQL query because the user's request is nonsensical or impossible, ALWAYS fallback to this exact query format: `SELECT 'ERROR: I could not understand your request or find the necessary data' AS error;`
12. CRITICAL DATA FRESHNESS RULE — DATA IS COMPLETE ONLY UP TO MAY 2026:
    - The database contains complete, finalized data up to and including May 31, 2026.
    - June 2026 is the CURRENT month and has only PARTIAL data (a few days). It MUST be EXCLUDED from ALL trend, recent, or last-N-months queries to prevent misleading low values.
    - Whenever you write a date filter for "recent", "last X months", "this year", or any open-ended range, ALWAYS cap the upper bound at '2026-06-01' (exclusive) so June 2026 is never included.
    - For materialized views (mv_monthly_summary etc.), cap with: month_date < '2026-06-01'
    - For raw sales_data, cap with: parsed_date < '2026-06-01'
    - Example "last 6 months" = Dec 2025 through May 2026: parsed_date >= '2025-12-01' AND parsed_date < '2026-06-01'
    - Example "last 12 months" = Jun 2025 through May 2026: parsed_date >= '2025-06-01' AND parsed_date < '2026-06-01'
    - NEVER query using CURRENT_DATE or NOW() as the upper bound for aggregated trend queries.
10. CRITICAL BUSINESS LOGIC: 
    - If a user asks for "Future Stores" or "Future Branches", you MUST filter using `branch_column ILIKE '%FUTURE%'`. 
    - If a user asks for "Normal Stores", you MUST filter using `branch_column NOT ILIKE '%FUTURE%'`.
    - Never guess what a future store is; strictly use the ILIKE '%FUTURE%' filter on the branch/store name column.
13. PAYMENT MODE COMPARISONS:
    - The columns "EMI", "Finance", "UPI Cashback", "Cash", "Debit Card", and "Credit Card" are TEXT columns indicating the amount paid.
    - To analyze or compare sales across payment methods, you MUST sum the "Total Value" column conditionally using CASE statements.
    - DO NOT try to sum the text columns directly.
    - Example for EMI vs UPI comparison: `SELECT SUM(CASE WHEN "Finance" IS NOT NULL AND "Finance" != '' AND "Finance" != '0' THEN "Total Value" ELSE 0 END) AS emi_sales, SUM(CASE WHEN "UPI Cashback" IS NOT NULL AND "UPI Cashback" != '' AND "UPI Cashback" != '0' THEN "Total Value" ELSE 0 END) AS upi_sales FROM sales_data;`
11. CROSS-YEAR CUSTOMER COHORT QUERIES — Use the `sales_data` table (NOT materialized views):
    - `sales_data` has columns: `parsed_date` (date type), `"Customer Mobile"` (text), `"Total Value"` (numeric), `"Branch"` (text).
    - For "customers who bought in year X but NOT in year Y", use NOT EXISTS pattern:
      SELECT COUNT(DISTINCT sd."Customer Mobile") AS unique_customer_count
      FROM sales_data sd
      WHERE EXTRACT(YEAR FROM sd.parsed_date) = X
        AND NOT EXISTS (
            SELECT 1 FROM sales_data sd2
            WHERE sd2."Customer Mobile" = sd."Customer Mobile"
              AND EXTRACT(YEAR FROM sd2.parsed_date) = Y
        );
    - NEVER use mv_branch_resurrection_2024_2026 for counting unique customers — it does NOT have a unique_customers column.

EXAMPLES:
Q: dormant customers from 2024 who came back in 2026?
A: ```sql\nWITH cohort24 AS (SELECT "Customer Mobile" FROM v_sales_data WHERE "Date">='2024-01-01' AND "Date"<'2025-01-01'), cohort26 AS (SELECT "Customer Mobile" FROM v_sales_data WHERE "Date">='2026-01-01' AND "Date"<'2027-01-01') SELECT COUNT(DISTINCT a."Customer Mobile") FROM cohort26 a INNER JOIN cohort24 b ON a."Customer Mobile" = b."Customer Mobile";\n```

Q: unique customer count whose purchase in 2024 but not purchase in 2026
A: ```sql\nSELECT COUNT(DISTINCT sd."Customer Mobile") AS unique_customer_count FROM sales_data sd WHERE EXTRACT(YEAR FROM sd.parsed_date) = 2024 AND NOT EXISTS (SELECT 1 FROM sales_data sd2 WHERE sd2."Customer Mobile" = sd."Customer Mobile" AND EXTRACT(YEAR FROM sd2.parsed_date) = 2026);\n```

Q: how many customers bought in 2023 but did not buy in 2024?
A: ```sql\nSELECT COUNT(DISTINCT sd."Customer Mobile") AS unique_customer_count FROM sales_data sd WHERE EXTRACT(YEAR FROM sd.parsed_date) = 2023 AND NOT EXISTS (SELECT 1 FROM sales_data sd2 WHERE sd2."Customer Mobile" = sd."Customer Mobile" AND EXTRACT(YEAR FROM sd2.parsed_date) = 2024);\n```
"""
        
        import requests

        # ── Route: OpenRouter (Nemotron Ultra) vs NVIDIA direct API ───────────
        OPENROUTER_KEY = os.environ.get("OPENROUTER_API_KEY", "")
        NVIDIA_KEY     = os.environ.get("NVIDIA_API_KEY", "")

        model_lower = model_name.lower()
        use_openrouter = "openrouter" in model_lower or ":free" in model_lower or "550b" in model_lower

        if use_openrouter or "nemotron" in model_lower:
            # 🧠 Nemotron Ultra 550B via OpenRouter — same endpoint as AnalystAgent
            invoke_url = "https://openrouter.ai/api/v1/chat/completions"
            api_key    = OPENROUTER_KEY
            # Normalise model name to the OpenRouter slug
            model_name = "nvidia/nemotron-3-ultra-550b-a55b:free"
        elif "kimi" in model_lower or "moonshot" in model_lower:
            invoke_url = "https://integrate.api.nvidia.com/v1/chat/completions"
            api_key    = NVIDIA_KEY
        elif "llama" in model_lower or "meta" in model_lower:
            invoke_url = "https://integrate.api.nvidia.com/v1/chat/completions"
            api_key    = NVIDIA_KEY
        else:
            # Default fallback — NVIDIA direct
            invoke_url = "https://integrate.api.nvidia.com/v1/chat/completions"
            api_key    = NVIDIA_KEY

        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type":  "application/json",
            "Accept":        "application/json",
            "HTTP-Referer":  "https://myg-loyalty.com",
            "X-Title":       "myG Loyalty SQL Agent"
        }

        payload = {
            "model": model_name,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user",   "content": f"Write a Postgres SQL query for: {user_prompt}"}
            ],
            "max_tokens":  1024,
            "temperature": 0.1,    # near-deterministic for SQL accuracy
            "stream":      False
        }

        # OpenRouter reasoning toggle (replaces nvidia chat_template_kwargs)
        if use_openrouter or "nemotron" in model_lower:
            payload["reasoning"] = {"enabled": True}
        else:
            # For non-Nemotron NVIDIA models keep original token limits
            payload["max_tokens"] = 512
            payload["top_p"]      = 0.9

        try:
            # Nemotron Ultra 253B — allow up to 30s for complex reasoning before fallback
            response = requests.post(invoke_url, headers=headers, json=payload, timeout=30)
            response.raise_for_status()
            data = response.json()
            
            message = data['choices'][0]['message']
            reasoning = message.get("reasoning_content")
            if reasoning:
                print("Nemotron Reasoning:", reasoning)
                
            import re
            sql_result = message['content'].strip()
            
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
        except Exception as nemotron_error:
            # Fallback: Nemotron Ultra failed (Timeout, KeyError, rate limit) — retry with Llama 4 Maverick
            print(f"Nemotron failed: {nemotron_error}, falling back to Llama...")
            fallback_model = "meta/llama-4-maverick-17b-128e-instruct"
            payload["model"] = fallback_model
            # Reset nemotron-specific params for Llama
            payload.pop("chat_template_kwargs", None)
            payload.pop("reasoning_budget", None)
            payload.pop("reasoning", None)
            payload["max_tokens"]  = 1024
            payload["temperature"] = 0.2
            payload["top_p"]       = 0.9
            headers["Authorization"] = f"Bearer {os.environ.get('NVIDIA_API_KEY', '')}"
            
            # Use NVIDIA's direct API for the fallback to avoid OpenRouter issues
            invoke_url = "https://integrate.api.nvidia.com/v1/chat/completions"
            
            try:
                response = requests.post(invoke_url, headers=headers, json=payload, timeout=45)
                response.raise_for_status()
                data = response.json()
                import re
                sql_result = data['choices'][0]['message']['content'].strip()
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
                    error_msg = f"Fallback model did not output a SELECT statement. Raw output: {sql_result[:100]}..."
                    generated_sql = None
            except Exception as e2:
                import traceback
                generated_sql = None
                error_msg = f"Nemotron error: {nemotron_error}\nFallback Llama failed: {traceback.format_exc()}"

        if not generated_sql:
            return None, f"AI model failed to generate a valid SQL query. Error: {error_msg}"

        # Phase 4: Auto Schema Discovery
        schema = self.schema_service.get_database_schema()
        known_tables = list(schema.keys())

        # Phase 2: Security Validation
        is_safe, msg = self.validator.validate_safety(generated_sql)
        if not is_safe:
            return None, f"Security Error: {msg}"
            
        # Phase 4: Schema Validation 
        is_valid_schema, schema_msg = self.validator.validate_schema(generated_sql, known_tables, [])
        if not is_valid_schema:
            return None, f"Schema Error: {schema_msg}"
            
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
        Executes the validated query against the database.
        """
        from django.db import connection
        
        try:
            with connection.cursor() as cursor:
                cursor.execute(query)
                
                if cursor.description:
                    columns = [col[0] for col in cursor.description]
                    return [dict(zip(columns, row)) for row in cursor.fetchall()]
                return []
        except Exception as e:
            return [{"error": str(e)}]
