import os
import requests
from django.db import connection
from ai_agent.models import SchemaVector
from django.conf import settings

class SchemaCatalogService:
    # Manual descriptions for critical tables to ensure perfect routing
    TABLE_DESCRIPTIONS = {
        "v_sales_data": "Raw customer sales transactions, invoices, branch codes, dates, and amounts. Use for specific granular filters.",
        "mv_customer_dates": "Customer first visit and last visit dates. Use for total customer counts and repeat customers.",
        "mv_customer_active_years": "Customer active years and yearly spend. Use 'mobile' for customer ID. Use 'yearly_spend' for total spend in a specific year.",
        "mv_customer_cohort_years": "Cohort year over year activity and retention. Use for cohort analysis.",
        "mv_daily_summary": "Daily revenue and invoice counts aggregated by branch. Use for 'today' or 'yesterday' queries.",
        "mv_monthly_summary": "Monthly revenue and invoice counts. Use for ALL queries asking for total revenue, sales, or invoice counts in a specific month (e.g., 'april 2026', 'last month') or MTD. The date column is exactly 'month_date'. When counting customers, always use SUM(customers).",
        "mv_branch_summary": "All-time branch performance metrics.",
        "mv_rfm_summary": "RFM data. Includes 'Customer Mobile', 'monetary_value' (lifetime purchase value), and 'segment'. Use for total purchase value, lifetime spend, and customer segments.",
        "mv_dormant_reactivation": "Dormant reactivation counts. Use to find count of customers whose last purchase was in a specific year (filter using cohort_year) and didn't purchase recently (filter using first_2026_month IS NULL). When counting, always use SUM(unique_customers). Do not complain about missing columns.",
        "mv_branch_resurrection_2024_2026": "Branch resurrection metrics, returnees, and reactivation rates specifically for the 2024 cohort returning in 2026. Do NOT filter by cohort year or year index, this view is already pre-filtered for 2024 cohort.",
        "mv_gap_analysis": "Loyalty & Gap analysis data. Use to analyze the average gap days between customer visits and the distribution of customers across gap buckets (e.g., '1-7 Days', '8-15 Days', etc.). Use when user asks about gap analysis, gap days, or time between purchases.",
        "mv_rfm_segments": "Detailed RFM segmentation of customers. Use when the user asks about RFM segments (e.g., 'Champions', 'At Risk', 'Loyal Customers'), recency, frequency, or monetary value segments. Can be used to find the count of customers per segment.",
        "mv_cohort_retention": "Cohort retention analysis. Use to analyze retention curves and customer cohort behavior over time. Tracks how many customers from a specific acquisition month (cohort_month) returned in subsequent months.",
        "mv_monthly_retention_2026": "Monthly retention data specifically for the year 2026. Use to track how many base customers (acquired before 2026) were retained each month in 2026.",
        "mv_action_engine": "Action Engine for campaign and target analysis. Use to identify target customer lists for campaigns, such as identifying dropping off customers, inactive users, or highly active users based on their recent visit patterns and spend.",
        "mv_loyalty_kpis": "Retail Loyalty KPIs. Use to track high-level loyalty metrics, customer activity ratios, and loyalty program performance.",
        "mv_customer_propensity": "Customer analytics and propensity modeling. Contains customer features used to predict churn propensity and purchase probability."
    }

    @staticmethod
    def get_embedding(text: str) -> list:
        invoke_url = "https://integrate.api.nvidia.com/v1/embeddings"
        api_key = "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"
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Accept": "application/json"
        }
        payload = {
            "input": [text],
            "model": "nvidia/nv-embedqa-e5-v5",
            "encoding_format": "float",
            "input_type": "query"
        }
        response = requests.post(invoke_url, headers=headers, json=payload, timeout=30)
        if response.status_code == 200:
            return response.json()['data'][0]['embedding']
        return []

    @classmethod
    def rebuild_catalog(cls):
        print("Rebuilding Schema Catalog...")
        
        # 1. Fetch tables and materialized view columns
        with connection.cursor() as cursor:
            cursor.execute("""
                SELECT c.relname as table_name, a.attname as column_name
                FROM pg_attribute a
                JOIN pg_class c ON a.attrelid = c.oid
                WHERE c.relname = ANY(%s)
                  AND a.attnum > 0 
                  AND NOT a.attisdropped;
            """, [list(cls.TABLE_DESCRIPTIONS.keys())])
            
            schema_data = {}
            for row in cursor.fetchall():
                t_name, c_name = row[0], row[1]
                if t_name not in schema_data:
                    schema_data[t_name] = []
                schema_data[t_name].append(c_name)

        # 2. Update DB with Embeddings
        for table, cols in schema_data.items():
            desc = cls.TABLE_DESCRIPTIONS.get(table, "Database table containing data.")
            # We embed the table name, description, and column names for maximum semantic search hit rate
            embed_text = f"Table: {table}. Description: {desc}. Columns: {', '.join(cols)}."
            
            embedding = cls.get_embedding(embed_text)
            if embedding:
                obj, created = SchemaVector.objects.update_or_create(
                    table_name=table,
                    defaults={
                        'description': desc,
                        'columns_json': cols,
                        'embedding': embedding
                    }
                )
                print(f"Success Indexed {table} ({len(cols)} columns)")
            else:
                print(f"Failed to embed {table}")
                
        print("Schema Catalog rebuild complete!")

    @classmethod
    def search_relevant_tables(cls, prompt: str, top_k=3):
        prompt_embedding = cls.get_embedding(prompt)
        if not prompt_embedding:
            return []
            
        # Format the embedding vector for raw SQL pgvector L2 distance `<->`
        emb_str = "[" + ",".join(map(str, prompt_embedding)) + "]"
        
        # Use raw SQL to sort by distance
        with connection.cursor() as cursor:
            cursor.execute(f"""
                SELECT table_name, description, columns_json
                FROM ai_schema_vectors
                ORDER BY embedding <-> %s
                LIMIT %s;
            """, [emb_str, top_k])
            
            results = []
            import json
            for row in cursor.fetchall():
                cols = row[2]
                if isinstance(cols, str):
                    try:
                        cols = json.loads(cols)
                    except Exception:
                        pass
                results.append({
                    "table": row[0],
                    "description": row[1],
                    "columns": cols
                })
            return results
