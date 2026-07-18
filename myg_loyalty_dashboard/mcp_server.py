import os
import sys
import django
from mcp.server.fastmcp import FastMCP
from typing import List, Dict, Any

# Bootstrap Django
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'myg_loyalty_dashboard.settings')
django.setup()

from django.apps import apps
from django.db import connection

# Determine port from Render environment variables
port = int(os.environ.get("PORT", 8001))

# Create FastMCP server
mcp = FastMCP("myg-portal", host="0.0.0.0", port=port)

@mcp.tool()
def list_django_models() -> List[Dict[str, str]]:
    """
    Returns a list of all available Django models in the portal.
    Useful to understand what models exist in the system.
    """
    models_info = []
    for model in apps.get_models():
        models_info.append({
            "app_label": model._meta.app_label,
            "model_name": model.__name__,
            "db_table": model._meta.db_table
        })
    return models_info

@mcp.tool()
def get_schema(app_label: str = None, model_name: str = None) -> List[Dict[str, Any]]:
    """
    Retrieves the database schema for the specified Django model.
    If no model is specified, it returns a summary of all tables.
    Returns the fields, their types, and column names.
    """
    if app_label and model_name:
        try:
            model = apps.get_model(app_label, model_name)
            fields_info = []
            for field in model._meta.fields:
                fields_info.append({
                    "name": field.name,
                    "column": field.column,
                    "type": field.get_internal_type(),
                    "null": field.null,
                    "primary_key": field.primary_key
                })
            return fields_info
        except LookupError:
            return [{"error": f"Model '{model_name}' not found in app '{app_label}'."}]
    else:
        # Return all tables and their columns using raw SQL for Postgres
        with connection.cursor() as cursor:
            cursor.execute("""
                SELECT table_name, column_name, data_type 
                FROM information_schema.columns 
                WHERE table_schema = 'public'
            """)
            rows = cursor.fetchall()
            
            schema = {}
            for row in rows:
                table, col, dtype = row
                if table not in schema:
                    schema[table] = []
                schema[table].append({"column": col, "type": dtype})
            
            return [{"table": k, "columns": v} for k, v in schema.items()]

@mcp.tool()
def execute_readonly_query(sql: str) -> List[Dict[str, Any]]:
    """
    Executes a raw read-only SQL query on the portal's database.
    WARNING: Only SELECT and WITH statements are allowed.
    Returns the query results as a list of dictionaries.
    """
    sql_upper = sql.strip().upper()
    if not (sql_upper.startswith("SELECT") or sql_upper.startswith("WITH")):
        return [{"error": "Only SELECT or WITH queries are allowed for security reasons."}]
    
    # Extra safety check against forbidden keywords
    forbidden_keywords = ["INSERT", "UPDATE", "DELETE", "DROP", "ALTER", "TRUNCATE", "GRANT", "REVOKE", "EXECUTE"]
    for kw in forbidden_keywords:
        # Check for keyword surrounded by spaces or newlines to avoid matching partial words
        import re
        if re.search(r'\b' + kw + r'\b', sql_upper):
            return [{"error": f"Forbidden keyword detected: {kw}. Only read-only operations are permitted."}]

    try:
        with connection.cursor() as cursor:
            cursor.execute(sql)
            columns = [col[0] for col in cursor.description] if cursor.description else []
            rows = cursor.fetchall()
            
            # Format results
            results = []
            for row in rows:
                row_dict = {}
                for idx, col in enumerate(columns):
                    # Convert non-serializable objects (like datetime, Decimal) to string
                    val = row[idx]
                    row_dict[col] = str(val) if val is not None else None
                results.append(row_dict)
                
            # Limit results to 1000 rows to prevent overwhelming the response
            if len(results) > 1000:
                results = results[:1000]
                results.append({"_warning": "Results truncated to 1000 rows."})
                
            return results
    except Exception as e:
        return [{"error": str(e)}]

if __name__ == "__main__":
    import uvicorn
    from starlette.middleware.cors import CORSMiddleware
    
    # Create the ASGI app
    app = mcp.sse_app()
    
    # Add CORS middleware so Gemini UI can connect
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )
    
    # Run the server
    uvicorn.run(app, host="0.0.0.0", port=port)
