import os
import sys

# --- RENDER DEPLOYMENT FIX ---
# Auto-install missing packages if they are not in the Render environment
try:
    from mcp.server.fastmcp import FastMCP
    import clickhouse_connect
except ImportError:
    print("Missing packages detected! Auto-installing mcp and clickhouse-connect...")
    import subprocess
    subprocess.check_call([sys.executable, "-m", "pip", "install", "--upgrade", "mcp[cli]>=1.0.0", "clickhouse-connect>=0.7.0"])
    print("Auto-install complete.")
    # Re-import after install
    from mcp.server.fastmcp import FastMCP

import django
from mcp.server.fastmcp import FastMCP
from typing import List, Dict, Any, Optional

# Bootstrap Django
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'myg_loyalty_dashboard.settings')
# Allow synchronous Django DB calls from FastMCP's async context
os.environ["DJANGO_ALLOW_ASYNC_UNSAFE"] = "true"
django.setup()

# Determine port from Render environment variables
port = int(os.environ.get("PORT", 8001))

# Create FastMCP server
mcp = FastMCP(
    "myg-portal",
    host="0.0.0.0",
    port=port,
    instructions="""
    You are connected to the myG Loyalty Portal database (ClickHouse) — a retail loyalty program platform for myG stores in Kerala, India.
    
    When the user asks any question about sales, revenue, customers, stores, branches, bills, or loyalty data,
    you MUST use the tools from this portal to answer from the live database.
    Do NOT search Google Drive, Gmail, or any other source for these answers.
    
    Key database tables:
    - sales_data: All transaction records with total_value, parsed_date, branch, staff, customer_name, customer_mobile etc.
    - item_wise_sales_data: Product-level sales with date, invoice_no, branch, item_code, imei_batch, qty, sold_price
    - invoice_wise_sales_data: Invoice level details
    
    Always use the appropriate tool based on what the user is asking.
    """
)


def _run_query(sql: str) -> List[Dict[str, Any]]:
    """Internal helper to run a SQL query safely on ClickHouse."""
    import re
    sql_upper = sql.strip().upper()
    if not (sql_upper.startswith("SELECT") or sql_upper.startswith("WITH") or sql_upper.startswith("DESCRIBE") or sql_upper.startswith("SHOW")):
        return [{"error": "Only SELECT/DESCRIBE/SHOW queries allowed."}]
    forbidden = ["INSERT", "UPDATE", "DELETE", "DROP", "ALTER", "TRUNCATE", "GRANT", "REVOKE"]
    for kw in forbidden:
        if re.search(r'\b' + kw + r'\b', sql_upper):
            return [{"error": f"Forbidden keyword: {kw}"}]
    try:
        from analytics.clickhouse_service import get_ch_client
        client = get_ch_client()
        if not client:
             return [{"error": "ClickHouse connection failed"}]
             
        result = client.query(sql)
        columns = result.column_names
        rows = result.result_rows
        
        results = []
        for row in rows:
            row_dict = {}
            for idx, col in enumerate(columns):
                val = row[idx]
                row_dict[col] = str(val) if val is not None else None
            results.append(row_dict)
        if len(results) > 1000:
            results = results[:1000]
            results.append({"_warning": "Results truncated to 1000 rows."})
        return results
    except Exception as e:
        return [{"error": str(e)}]


@mcp.tool()
def get_total_sales(start_date: str, end_date: str) -> Dict[str, Any]:
    """
    Get the total sales revenue (sum of total_value) for a given date range from the myG portal database.
    Use this tool when the user asks about total sales, total revenue, total billing amount for any period.
    
    Parameters:
        start_date: Start date in YYYY-MM-DD format (e.g. '2026-06-01')
        end_date: End date in YYYY-MM-DD format (e.g. '2026-06-30')
    
    Examples of questions that trigger this tool:
    - "What is the total sale in June 2026?"
    - "Give me revenue for April 2026"
    - "How much sales happened in Q2 2026?"
    """
    sql = f"""
        SELECT 
            COUNT(*) as total_bills,
            SUM(total_value) as total_revenue,
            MIN(parsed_date) as from_date,
            MAX(parsed_date) as to_date
        FROM sales_data
        WHERE parsed_date >= '{start_date}' AND parsed_date <= '{end_date}'
    """
    results = _run_query(sql)
    if results and "error" not in results[0]:
        row = results[0]
        return {
            "total_revenue": row.get("total_revenue"),
            "total_bills": row.get("total_bills"),
            "from_date": row.get("from_date"),
            "to_date": row.get("to_date"),
            "note": "Total revenue in Indian Rupees from myG loyalty portal database."
        }
    return results[0] if results else {"error": "No results"}


@mcp.tool()
def get_sales_by_branch(start_date: str, end_date: str) -> List[Dict[str, Any]]:
    """
    Get sales revenue broken down by branch/store for a given date range from the myG portal database.
    Use this when the user asks about sales per branch, store-wise sales, or branch performance.
    
    Parameters:
        start_date: Start date in YYYY-MM-DD format
        end_date: End date in YYYY-MM-DD format
    
    Examples:
    - "Which branch had the highest sales in June 2026?"
    - "Give me store-wise sales for April 2026"
    - "Branch performance for Q2 2026"
    """
    sql = f"""
        SELECT 
            branch,
            COUNT(*) as total_bills,
            SUM(total_value) as total_revenue
        FROM sales_data
        WHERE parsed_date >= '{start_date}' AND parsed_date <= '{end_date}'
        GROUP BY branch
        ORDER BY SUM(total_value) DESC
    """
    return _run_query(sql)


@mcp.tool()
def get_daily_sales(start_date: str, end_date: str) -> List[Dict[str, Any]]:
    """
    Get day-by-day sales revenue for a given date range from the myG portal database.
    Use this when the user asks about daily sales trends, day-wise revenue, or sales trend over time.
    
    Parameters:
        start_date: Start date in YYYY-MM-DD format
        end_date: End date in YYYY-MM-DD format
    
    Examples:
    - "Show me daily sales for June 2026"
    - "What were the sales each day in April?"
    - "Give me the sales trend for Q2 2026"
    """
    sql = f"""
        SELECT 
            parsed_date as date,
            COUNT(*) as total_bills,
            SUM(total_value) as total_revenue
        FROM sales_data
        WHERE parsed_date >= '{start_date}' AND parsed_date <= '{end_date}'
        GROUP BY parsed_date
        ORDER BY parsed_date
    """
    return _run_query(sql)


@mcp.tool()
def get_top_products(start_date: str, end_date: str, limit: int = 10) -> List[Dict[str, Any]]:
    """
    Get the top-selling products by revenue for a given date range from the myG portal database.
    Use this when the user asks about best-selling products, top products, product performance.
    
    Parameters:
        start_date: Start date in YYYY-MM-DD format
        end_date: End date in YYYY-MM-DD format
        limit: Number of top products to return (default 10)
    
    Examples:
    - "What are the top 10 products sold in June 2026?"
    - "Best selling products in April 2026"
    - "Which product category performed best in Q2?"
    """
    sql = f"""
        SELECT 
            item_code,
            SUM(qty) as total_qty,
            SUM(sold_price) as total_revenue
        FROM item_wise_sales_data
        WHERE date >= '{start_date}' AND date <= '{end_date}'
        GROUP BY item_code
        ORDER BY SUM(sold_price) DESC
        LIMIT {limit}
    """
    return _run_query(sql)


@mcp.tool()
def get_customer_count(start_date: str, end_date: str) -> Dict[str, Any]:
    """
    Get the total number of unique customers and total transactions for a given date range.
    Use this when the user asks about customer count, footfall, number of customers visited.
    
    Parameters:
        start_date: Start date in YYYY-MM-DD format
        end_date: End date in YYYY-MM-DD format
    
    Examples:
    - "How many customers visited in June 2026?"
    - "Customer count for April 2026"
    - "Total footfall in Q2 2026"
    """
    sql = f"""
        SELECT 
            COUNT(*) as total_transactions,
            COUNT(DISTINCT customer_mobile) as unique_customers
        FROM sales_data
        WHERE parsed_date >= '{start_date}' AND parsed_date <= '{end_date}'
        AND customer_mobile IS NOT NULL AND customer_mobile != ''
    """
    results = _run_query(sql)
    if results and "error" not in results[0]:
        return results[0]
    return results[0] if results else {"error": "No results"}


@mcp.tool()
def get_sales_by_staff(start_date: str, end_date: str, limit: int = 10) -> List[Dict[str, Any]]:
    """
    Get sales performance broken down by staff member for a given date range.
    Use this when the user asks about staff performance, top salesperson, executive-wise sales.
    
    Parameters:
        start_date: Start date in YYYY-MM-DD format
        end_date: End date in YYYY-MM-DD format
        limit: Number of top staff to return (default 10)
    
    Examples:
    - "Top performing staff in June 2026"
    - "Who sold the most in April 2026?"
    - "Staff performance for Q2 2026"
    """
    sql = f"""
        SELECT 
            staff,
            branch,
            COUNT(*) as total_bills,
            SUM(total_value) as total_revenue
        FROM sales_data
        WHERE parsed_date >= '{start_date}' AND parsed_date <= '{end_date}'
        AND staff IS NOT NULL AND staff != ''
        GROUP BY staff, branch
        ORDER BY SUM(total_value) DESC
        LIMIT {limit}
    """
    return _run_query(sql)


@mcp.tool()
def execute_custom_query(sql: str) -> List[Dict[str, Any]]:
    """
    Execute a custom read-only SQL SELECT query on the myG portal ClickHouse database.
    Only use this tool when the user explicitly provides a SQL query, or when none of the 
    other specific tools cover the user's requirement.
    Only SELECT, WITH, DESCRIBE and SHOW queries are allowed for security.
    
    Main tables available in ClickHouse:
    - sales_data: slno, parsed_date, sale_time, invoice_number, branch, staff, 
      customer_name, customer_mobile, total_value, etc.
    - item_wise_sales_data: date, invoice_no, branch, item_code, imei_batch, qty, mop, discount, sold_price, taxable
    - invoice_wise_sales_data: date, time, invoice_no, branch, rbm, bdm, customer_bill_to_no, invoice_total...
    """
    return _run_query(sql)


from starlette.middleware.cors import CORSMiddleware
from starlette.responses import JSONResponse
from starlette.routing import Route

# Gemini custom connected apps require streamable-http transport (NOT SSE)
app = mcp.streamable_http_app()

async def health_check(request):
    return JSONResponse({"status": "ok", "mcp": "myg-portal"})
    
app.routes.insert(0, Route("/", health_check, methods=["GET"]))

# Add CORS middleware so Gemini UI can connect from gemini.google.com
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

if __name__ == "__main__":
    import uvicorn
    # Run the server — Render injects PORT automatically
    uvicorn.run(app, host="0.0.0.0", port=port)

