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

    def generate_query(self, user_prompt: str, user_context: dict, model_name: str = "google/gemma-3n-e4b-it") -> tuple[str, str]:
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
10. CRITICAL BUSINESS LOGIC: 
    - If a user asks for "Future Stores" or "Future Branches", you MUST filter using `branch_column ILIKE '%FUTURE%'`. 
    - If a user asks for "Normal Stores", you MUST filter using `branch_column NOT ILIKE '%FUTURE%'`.
    - Never guess what a future store is; strictly use the ILIKE '%FUTURE%' filter on the branch/store name column.

EXAMPLES:
Q: dormant customers from 2024 who came back in 2026?
A: ```sql\nWITH cohort24 AS (SELECT "Customer Mobile" FROM v_sales_data WHERE "Date">='2024-01-01' AND "Date"<'2025-01-01'), cohort26 AS (SELECT "Customer Mobile" FROM v_sales_data WHERE "Date">='2026-01-01' AND "Date"<'2027-01-01') SELECT COUNT(DISTINCT a."Customer Mobile") FROM cohort26 a INNER JOIN cohort24 b ON a."Customer Mobile" = b."Customer Mobile";\n```
"""
        
        import requests

        invoke_url = "https://integrate.api.nvidia.com/v1/chat/completions"

        # ── NVIDIA API Key Routing (one key per model family) ──────────────────
        model_lower = model_name.lower()

        if "kimi" in model_lower or "moonshot" in model_lower:
            # 🌙 Moonshot AI — Kimi K2.6
            api_key = "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"

        elif "llama-3.1" in model_lower or "llama_fast" in model_lower:
            # ⚡ Meta — Llama 3.1 8B (Fast)
            api_key = "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"

        elif "llama" in model_lower or "meta" in model_lower:
            # 🦙 Meta — Llama 4 Maverick
            api_key = "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"
            
        elif "nemotron" in model_lower:
            # 🧠 NVIDIA — Nemotron 3 Nano Omni 30b Reasoning
            api_key = os.environ.get("NVIDIA_API_KEY", "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")

        else:
            # 🔴 Default — Google Gemma 3n
            api_key = "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"

        headers = {
            "Authorization": f"Bearer {api_key}",
            "Accept": "application/json"
        }
        
        payload = {
            "model": model_name,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": f"Write a Postgres query for: {user_prompt}"}
            ],
            "max_tokens": 512,
            "temperature": 0.20,
            "stream": False
        }
        
        if "nemotron" in model_lower:
            payload["max_tokens"] = 65536
            payload["temperature"] = 0.6
            payload["top_p"] = 0.95
            payload["chat_template_kwargs"] = {"enable_thinking": True}
            payload["reasoning_budget"] = 16384

        try:
            response = requests.post(invoke_url, headers=headers, json=payload, timeout=60)
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
        except requests.exceptions.Timeout:
            # Fallback to Llama 3.1 8B if Kimi times out
            fallback_model = "meta/llama-3.1-8b-instruct"
            payload["model"] = fallback_model
            headers["Authorization"] = "Bearer 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"
            try:
                response = requests.post(invoke_url, headers=headers, json=payload, timeout=30)
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
                    error_msg = f"Model did not output a SELECT statement. Raw output: {sql_result[:100]}..."
                    generated_sql = None
            except Exception as e2:
                import traceback
                generated_sql = None
                error_msg = f"Fallback model failed: {traceback.format_exc()}"
        except Exception as e:
            import traceback
            generated_sql = None
            error_msg = f"{str(e)}\nTraceback: {traceback.format_exc()}"

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
