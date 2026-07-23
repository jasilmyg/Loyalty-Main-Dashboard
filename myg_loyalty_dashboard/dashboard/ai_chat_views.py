"""
AI Chat view using Gemini API with direct database function calling.
Provides a natural language interface to query the myG portal database.
"""
import json
import os
import re
from pathlib import Path
from dotenv import load_dotenv
from django.views import View
from django.http import JsonResponse
from django.shortcuts import render
from django.views.decorators.csrf import csrf_exempt
from django.utils.decorators import method_decorator
from django.db import connection

import google.generativeai as genai

# Ensure .env is loaded (fallback in case server process didn't inherit it)
_BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(_BASE_DIR / '.env', override=False)


def _run_db_query(sql: str):
    """Run a safe read-only SQL query and return results."""
    sql_upper = sql.strip().upper()
    if not (sql_upper.startswith("SELECT") or sql_upper.startswith("WITH")):
        return {"error": "Only SELECT queries allowed."}
    forbidden = ["INSERT", "UPDATE", "DELETE", "DROP", "ALTER", "TRUNCATE", "GRANT", "REVOKE"]
    for kw in forbidden:
        if re.search(r'\b' + kw + r'\b', sql_upper):
            return {"error": f"Forbidden keyword: {kw}"}
    try:
        with connection.cursor() as cursor:
            cursor.execute(sql)
            columns = [col[0] for col in cursor.description] if cursor.description else []
            rows = cursor.fetchmany(500)
            return {
                "columns": columns,
                "rows": [[str(v) if v is not None else None for v in row] for row in rows],
                "row_count": len(rows)
            }
    except Exception as e:
        return {"error": str(e)}


# Database schema context for Gemini
DB_SCHEMA = """
You are an AI assistant for myG Loyalty Portal — a retail loyalty program platform for myG stores in Kerala, India.
You have access to a PostgreSQL database. When the user asks any question about sales, revenue, customers, 
branches, staff, or products — you MUST use the query_database function to get the answer from the live database.
Do NOT guess or make up data. Always query the database for factual answers.

Main database tables:
1. sales_data — All transaction records
   Columns: "Slno", "Date" (text), "Time", "Invoice Number", "Branch", "Staff", "Customer Name", 
   "Customer Mobile", "Financier", "Finance", "Cash", "Total Value" (numeric), 
   "Exchange", "Discount", "Customer Type", parsed_date (date — USE THIS for date filtering)

2. analytics_productsale — Product-level sales
   Columns: id, date (date), invoice_number, branch, product, category, brand, qty (int), sold_price (numeric)

Important notes:
- For date filtering, always use parsed_date on sales_data (e.g. WHERE parsed_date >= '2026-06-01')
- Column names with spaces in sales_data must be quoted: "Total Value", "Branch", "Staff", etc.
- June 2026 = parsed_date >= '2026-06-01' AND parsed_date <= '2026-06-30'
- Q2 2026 = parsed_date >= '2026-04-01' AND parsed_date <= '2026-06-30'
- Always SUM("Total Value") for total revenue from sales_data
- Currency is Indian Rupees (₹)
"""


class AIChatView(View):
    """Render the AI Chat page."""
    def get(self, request):
        return render(request, 'dashboard/ai_chat.html')


@method_decorator(csrf_exempt, name='dispatch')
class AIChatAPIView(View):
    """Handle AI chat API requests using Gemini with function calling."""

    def post(self, request):
        try:
            data = json.loads(request.body)
            user_message = data.get('message', '').strip()
            chat_history = data.get('history', [])

            if not user_message:
                return JsonResponse({'error': 'No message provided'}, status=400)

            api_key = os.environ.get('GEMINI_API_KEY', '')
            if not api_key:
                return JsonResponse({'error': 'Gemini API key not configured'}, status=500)

            genai.configure(api_key=api_key)

            # Define the database query tool for Gemini
            query_db_tool = genai.protos.Tool(
                function_declarations=[
                    genai.protos.FunctionDeclaration(
                        name="query_database",
                        description=(
                            "Execute a read-only SQL SELECT query on the myG portal database to get real-time data. "
                            "Use this for any question about sales, revenue, customers, branches, staff, products, "
                            "or any business data. Always use this instead of guessing."
                        ),
                        parameters=genai.protos.Schema(
                            type=genai.protos.Type.OBJECT,
                            properties={
                                "sql": genai.protos.Schema(
                                    type=genai.protos.Type.STRING,
                                    description="A valid PostgreSQL SELECT query to execute against the database."
                                )
                            },
                            required=["sql"]
                        )
                    )
                ]
            )

            # USING gemini-1.5-flash-latest to avoid Free Tier Quota Exhaustion!
            model = genai.GenerativeModel(
                model_name="gemini-2.0-flash-lite",
                tools=[query_db_tool],
                system_instruction=DB_SCHEMA
            )

            # Build history for multi-turn conversation
            history = []
            for msg in chat_history:
                role = "user" if msg['role'] == 'user' else "model"
                history.append({"role": role, "parts": [msg['content']]})

            chat = model.start_chat(history=history)

            # Send user message
            response = chat.send_message(user_message)

            # Handle function calls (Gemini asking to query DB)
            max_iterations = 5
            iteration = 0
            sql_queries_run = []

            while iteration < max_iterations:
                iteration += 1
                # Check if Gemini wants to call a function
                function_calls = []
                for candidate in response.candidates:
                    for part in candidate.content.parts:
                        if hasattr(part, 'function_call') and part.function_call.name:
                            function_calls.append(part.function_call)

                if not function_calls:
                    break  # No more function calls, we have the final text response

                # Execute all requested function calls
                function_responses = []
                for fc in function_calls:
                    if fc.name == "query_database":
                        sql = fc.args.get("sql", "")
                        sql_queries_run.append(sql)
                        result = _run_db_query(sql)
                        function_responses.append(
                            genai.protos.Part(
                                function_response=genai.protos.FunctionResponse(
                                    name="query_database",
                                    response={"result": json.dumps(result)}
                                )
                            )
                        )

                # Send function results back to Gemini
                response = chat.send_message(function_responses)

            # Extract final text response
            final_text = ""
            for candidate in response.candidates:
                for part in candidate.content.parts:
                    if hasattr(part, 'text') and part.text:
                        final_text += part.text

            return JsonResponse({
                'response': final_text,
                'queries_run': sql_queries_run
            })

        except Exception as e:
            import traceback
            return JsonResponse({'error': str(e), 'traceback': traceback.format_exc()}, status=500)
