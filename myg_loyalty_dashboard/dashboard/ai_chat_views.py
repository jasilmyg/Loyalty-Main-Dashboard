"""
AI Chat view using Gemini API (new google-genai SDK) with direct database function calling.
Provides a natural language interface to query the myG portal database.
"""
import json
import os
import re
from django.views import View
from django.http import JsonResponse
from django.shortcuts import render
from django.views.decorators.csrf import csrf_exempt
from django.utils.decorators import method_decorator
from django.db import connection


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
DB_SYSTEM_PROMPT = """You are an AI assistant for myG Loyalty Portal — a retail loyalty program platform for myG stores in Kerala, India.
You have access to a live PostgreSQL database via the query_database function.

When the user asks any question about sales, revenue, customers, branches, staff, or products:
- ALWAYS use query_database to get real-time data from the database
- NEVER guess, estimate, or make up numbers
- After getting data, present it clearly in a readable format with ₹ symbol for amounts

Main database tables:
1. sales_data — All transaction records
   Columns: "Slno", "Date" (text), "Time", "Invoice Number", "Branch", "Staff", 
   "Customer Name", "Customer Mobile", "Total Value" (numeric — the sale amount),
   parsed_date (date — ALWAYS use this for date filtering, NOT "Date")
   
2. analytics_productsale — Product-level sales  
   Columns: id, date (date), invoice_number, branch, product, category, brand, qty (int), sold_price (numeric)

IMPORTANT SQL rules:
- Column names with spaces MUST be double-quoted: "Total Value", "Branch", "Staff", "Invoice Number"
- Date filtering: WHERE parsed_date >= '2026-06-01' AND parsed_date <= '2026-06-30'
- June 2026: parsed_date >= '2026-06-01' AND parsed_date <= '2026-06-30'
- April 2026: parsed_date >= '2026-04-01' AND parsed_date <= '2026-04-30'
- May 2026: parsed_date >= '2026-05-01' AND parsed_date <= '2026-05-31'
- Q2 2026: parsed_date >= '2026-04-01' AND parsed_date <= '2026-06-30'
- Total revenue = SUM("Total Value") from sales_data
- Currency is Indian Rupees (₹)
"""

QUERY_DB_TOOL = {
    "name": "query_database",
    "description": (
        "Execute a read-only SQL SELECT query on the myG loyalty portal PostgreSQL database. "
        "Use this to answer ANY question about sales, revenue, customers, branches, staff, or products. "
        "Always use this instead of guessing. Returns columns and rows."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "sql": {
                "type": "string",
                "description": "A valid PostgreSQL SELECT query. Use double quotes for column names with spaces."
            }
        },
        "required": ["sql"]
    }
}


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

            # Use new google-genai SDK
            from google import genai
            from google.genai import types

            client = genai.Client(api_key=api_key)

            # Build conversation history
            contents = []
            for msg in chat_history[-10:]:  # Keep last 10 messages for context
                role = "user" if msg['role'] == 'user' else "model"
                contents.append(types.Content(role=role, parts=[types.Part(text=msg['content'])]))

            # Add current user message
            contents.append(types.Content(role="user", parts=[types.Part(text=user_message)]))

            # Configure tools
            tools = [types.Tool(function_declarations=[types.FunctionDeclaration(**QUERY_DB_TOOL)])]

            config = types.GenerateContentConfig(
                system_instruction=DB_SYSTEM_PROMPT,
                tools=tools,
                temperature=0.1,  # Low temperature for factual DB answers
            )

            sql_queries_run = []
            max_iterations = 5

            for _ in range(max_iterations):
                response = client.models.generate_content(
                    model="gemini-2.0-flash",
                    contents=contents,
                    config=config,
                )

                # Check for function calls
                function_calls = []
                for part in response.candidates[0].content.parts:
                    if hasattr(part, 'function_call') and part.function_call is not None:
                        function_calls.append(part.function_call)

                if not function_calls:
                    # No function calls, extract final text
                    final_text = ""
                    for part in response.candidates[0].content.parts:
                        if hasattr(part, 'text') and part.text:
                            final_text += part.text
                    return JsonResponse({
                        'response': final_text,
                        'queries_run': sql_queries_run
                    })

                # Execute function calls and build responses
                # First add the model's function call turn to contents
                contents.append(response.candidates[0].content)

                # Build function response parts
                function_response_parts = []
                for fc in function_calls:
                    if fc.name == "query_database":
                        sql = fc.args.get("sql", "")
                        sql_queries_run.append(sql)
                        result = _run_db_query(sql)
                        function_response_parts.append(
                            types.Part(
                                function_response=types.FunctionResponse(
                                    name="query_database",
                                    response={"result": json.dumps(result)}
                                )
                            )
                        )

                # Add function responses to contents
                contents.append(types.Content(role="user", parts=function_response_parts))

            return JsonResponse({'error': 'Max iterations reached'}, status=500)

        except Exception as e:
            import traceback
            return JsonResponse({'error': str(e), 'traceback': traceback.format_exc()}, status=500)
