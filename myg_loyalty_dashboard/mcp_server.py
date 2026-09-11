import os
import sys
import hmac
import hashlib
import time
import json
import base64

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

# ── OAuth 2.0 Credentials (set these as Render env vars) ─────────────────────
# MCP_CLIENT_ID   = e.g. "myg-loyalty-portal"
# MCP_CLIENT_SECRET = e.g. "your-strong-secret-here"
# MCP_TOKEN_SECRET  = random 32+ char string used to sign tokens
CLIENT_ID     = os.environ.get("MCP_CLIENT_ID",     "myg-loyalty-portal")
CLIENT_SECRET = os.environ.get("MCP_CLIENT_SECRET", "myg-secret-2026!")
TOKEN_SECRET  = os.environ.get("MCP_TOKEN_SECRET",  "myg-token-signing-secret-2026")
TOKEN_TTL_SEC = int(os.environ.get("MCP_TOKEN_TTL", 3600))  # 1 hour default


def _make_token(client_id: str) -> str:
    """Create a signed HMAC-SHA256 bearer token."""
    payload = json.dumps({"sub": client_id, "iat": int(time.time()), "exp": int(time.time()) + TOKEN_TTL_SEC})
    payload_b64 = base64.urlsafe_b64encode(payload.encode()).decode().rstrip("=")
    sig = hmac.new(TOKEN_SECRET.encode(), payload_b64.encode(), hashlib.sha256).hexdigest()
    return f"{payload_b64}.{sig}"


def _verify_token(token: str) -> bool:
    """Verify a signed HMAC-SHA256 bearer token."""
    try:
        payload_b64, sig = token.rsplit(".", 1)
        expected_sig = hmac.new(TOKEN_SECRET.encode(), payload_b64.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(sig, expected_sig):
            return False
        # Add padding back
        padded = payload_b64 + "=" * (4 - len(payload_b64) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded).decode())
        if payload.get("exp", 0) < int(time.time()):
            return False  # expired
        return True
    except Exception:
        return False

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
    - azure_invoice_report: Invoice level details (invoice_total, customer_mobile, sales_staff_code, branch, date)
    - azure_sales_report: Product-level sales (date, invoice_no, branch, item_code, qty, sold_price, discount)
    - item_master: Mapping of item_code to product category and brand (columns: item_code, product, brand, category, item_name, item_group, item_category, hsn, tax_percent, mop, mrp)
    - branch_master: Details of store branches (columns: code, branch_name, rbm, bdm, address, district, pincode, email, gst_no, store_type, phone_no, category, mapped_warehouse)
    
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
            COUNT(DISTINCT invoice_no) as total_bills,
            SUM(toFloat64(sold_price)) as total_revenue,
            MIN(date) as from_date,
            MAX(date) as to_date
        FROM azure_sales_report
        WHERE date >= '{{start_date}}' AND date <= '{{end_date}}'
        AND date != '1970-01-01'
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
            COUNT(DISTINCT invoice_no) as total_bills,
            SUM(toFloat64(sold_price)) as total_revenue
        FROM azure_sales_report
        WHERE date >= '{{start_date}}' AND date <= '{{end_date}}'
        AND date != '1970-01-01'
        GROUP BY branch
        ORDER BY total_revenue DESC
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
            date,
            COUNT(DISTINCT invoice_no) as total_bills,
            SUM(toFloat64(sold_price)) as total_revenue
        FROM azure_sales_report
        WHERE date >= '{{start_date}}' AND date <= '{{end_date}}'
        AND date != '1970-01-01'
        GROUP BY date
        ORDER BY date
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
            m.product as product_category,
            m.brand as brand,
            SUM(toFloat64(s.qty)) as total_qty,
            SUM(toFloat64(s.sold_price)) as total_revenue
        FROM azure_sales_report s
        LEFT JOIN item_master m ON s.item_code = m.item_code
        WHERE s.date >= '{{start_date}}' AND s.date <= '{{end_date}}'
        AND s.date != '1970-01-01'
        GROUP BY product_category, brand
        ORDER BY total_revenue DESC
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
            COUNT(DISTINCT invoice_no) as total_transactions,
            COUNT(DISTINCT customer_mobile) as unique_customers
        FROM azure_invoice_report
        WHERE date >= '{{start_date}}' AND date <= '{{end_date}}'
        AND date != '1970-01-01'
        AND customer_mobile != ''
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
            sales_staff_code as staff,
            branch,
            COUNT(DISTINCT invoice_no) as total_bills,
            SUM(toFloat64(invoice_total)) as total_revenue
        FROM azure_invoice_report
        WHERE date >= '{{start_date}}' AND date <= '{{end_date}}'
        AND date != '1970-01-01'
        AND sales_staff_code != ''
        GROUP BY sales_staff_code, branch
        ORDER BY total_revenue DESC
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
    - azure_sales_report: date, invoice_no, branch, item_code, qty, discount, sold_price, taxable
    - azure_invoice_report: date, time, invoice_no, branch, customer_mobile, sales_staff_code, invoice_total
    - item_master: item_code, product, brand, category, item_name, item_group, item_category, hsn, tax_percent, mop, mrp
    - branch_master: code, branch_name, rbm, bdm, address, district, pincode, email, gst_no, store_type, phone_no, category, mapped_warehouse
    - sales_data: (Use azure_sales_report instead unless explicitly needed for legacy point matrix)
    """
    return _run_query(sql)


from starlette.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse, Response, HTMLResponse, RedirectResponse
from starlette.routing import Route
from starlette.requests import Request
import secrets
import urllib.parse

# Streamable HTTP is the modern MCP transport
app = mcp.streamable_http_app()

# ── In-memory auth code store: {code: {client_id, redirect_uri, code_challenge, exp}} ──
_auth_codes: dict = {}

# ── Public routes (no auth needed) ───────────────────────────────────────────
PUBLIC_PATHS = {
    "/", "/health",
    "/.well-known/oauth-authorization-server",
    "/oauth/token",
    "/authorize",
}


class BearerAuthMiddleware(BaseHTTPMiddleware):
    """Validate Bearer token on all /mcp and protected routes."""
    async def dispatch(self, request: Request, call_next):
        if request.url.path in PUBLIC_PATHS:
            return await call_next(request)
        auth = request.headers.get("Authorization", "")
        if not auth.startswith("Bearer "):
            return JSONResponse(
                {"error": "unauthorized", "error_description": "Bearer token required"},
                status_code=401,
            )
        token = auth[7:].strip()
        if not _verify_token(token):
            return JSONResponse(
                {"error": "invalid_token", "error_description": "Token is invalid or expired"},
                status_code=401,
            )
        return await call_next(request)


# ── Endpoint: OAuth metadata ──────────────────────────────────────────────────
async def oauth_metadata(request: Request):
    base = str(request.base_url).rstrip("/")
    return JSONResponse({
        "issuer": base,
        "authorization_endpoint": f"{base}/authorize",
        "token_endpoint": f"{base}/oauth/token",
        "grant_types_supported": ["authorization_code", "client_credentials"],
        "response_types_supported": ["code"],
        "code_challenge_methods_supported": ["S256", "plain"],
        "token_endpoint_auth_methods_supported": ["client_secret_post", "client_secret_basic", "none"],
        "scopes_supported": ["mcp:read"],
    })


# ── Endpoint: Authorization page (GET = show page, POST = approve) ────────────
async def authorize_endpoint(request: Request):
    params = dict(request.query_params)
    client_id      = params.get("client_id", "")
    redirect_uri   = params.get("redirect_uri", "")
    state          = params.get("state", "")
    code_challenge = params.get("code_challenge", "")
    code_challenge_method = params.get("code_challenge_method", "S256")

    if request.method == "GET":
        # Show a styled authorization approval page
        html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Authorize — myG Loyalty Portal</title>
  <style>
    * {{ margin: 0; padding: 0; box-sizing: border-box; }}
    body {{ background: #0b1120; font-family: 'Segoe UI', sans-serif; display: flex; align-items: center; justify-content: center; min-height: 100vh; }}
    .card {{ background: #1e293b; border: 1px solid rgba(245,158,11,.25); border-radius: 16px; padding: 40px 36px; width: 420px; box-shadow: 0 25px 60px rgba(0,0,0,.5); }}
    .logo {{ font-size: 1.5rem; font-weight: 800; color: #f59e0b; letter-spacing: -0.5px; margin-bottom: 6px; }}
    .logo span {{ color: #f1f5f9; }}
    .subtitle {{ color: #64748b; font-size: .85rem; margin-bottom: 28px; }}
    h2 {{ color: #f1f5f9; font-size: 1.1rem; margin-bottom: 8px; }}
    .app-badge {{ background: rgba(99,102,241,.15); border: 1px solid rgba(99,102,241,.3); border-radius: 8px; padding: 10px 14px; margin: 16px 0 24px; display: flex; align-items: center; gap: 10px; }}
    .app-icon {{ width: 36px; height: 36px; background: #6366f1; border-radius: 8px; display: flex; align-items: center; justify-content: center; color: #fff; font-weight: 700; font-size: .9rem; flex-shrink: 0; }}
    .app-name {{ color: #a5b4fc; font-weight: 600; }}
    .app-desc {{ color: #64748b; font-size: .75rem; }}
    .perms {{ background: rgba(255,255,255,.03); border-radius: 10px; padding: 14px 16px; margin-bottom: 24px; }}
    .perm {{ display: flex; align-items: center; gap: 10px; color: #94a3b8; font-size: .82rem; padding: 5px 0; }}
    .perm::before {{ content: "✓"; color: #10b981; font-weight: 700; }}
    .btn-approve {{ width: 100%; background: linear-gradient(135deg, #f59e0b, #d97706); color: #1a1a1a; font-weight: 700; font-size: .95rem; border: none; border-radius: 10px; padding: 13px; cursor: pointer; margin-bottom: 10px; transition: opacity .2s; }}
    .btn-approve:hover {{ opacity: .9; }}
    .btn-deny {{ width: 100%; background: transparent; color: #64748b; font-size: .85rem; border: 1px solid rgba(255,255,255,.08); border-radius: 10px; padding: 11px; cursor: pointer; transition: border-color .2s; }}
    .btn-deny:hover {{ border-color: #f87171; color: #f87171; }}
    .footer {{ text-align: center; color: #475569; font-size: .72rem; margin-top: 20px; }}
  </style>
</head>
<body>
  <div class="card">
    <div class="logo">myG <span>Loyalty Portal</span></div>
    <div class="subtitle">MCP Data Connector</div>
    <h2>Authorize Access</h2>
    <div class="app-badge">
      <div class="app-icon">C</div>
      <div>
        <div class="app-name">Claude (Anthropic)</div>
        <div class="app-desc">{client_id}</div>
      </div>
    </div>
    <div class="perms">
      <div class="perm">Read sales & revenue data</div>
      <div class="perm">Query branch & store performance</div>
      <div class="perm">Access customer analytics</div>
      <div class="perm">Run read-only ClickHouse queries</div>
    </div>
    <form method="POST">
      <input type="hidden" name="client_id" value="{client_id}">
      <input type="hidden" name="redirect_uri" value="{redirect_uri}">
      <input type="hidden" name="state" value="{state}">
      <input type="hidden" name="code_challenge" value="{code_challenge}">
      <input type="hidden" name="code_challenge_method" value="{code_challenge_method}">
      <button type="submit" name="action" value="approve" class="btn-approve">✓ Allow Access</button>
      <button type="submit" name="action" value="deny" class="btn-deny">Deny</button>
    </form>
    <div class="footer">myG Loyalty Portal · Kerala, India · Read-only access</div>
  </div>
</body>
</html>"""
        return HTMLResponse(html)

    # POST: process approval
    form = await request.form()
    action         = form.get("action", "deny")
    client_id      = form.get("client_id", "")
    redirect_uri   = form.get("redirect_uri", "")
    state          = form.get("state", "")
    code_challenge = form.get("code_challenge", "")
    code_challenge_method = form.get("code_challenge_method", "S256")

    if action != "approve":
        qs = urllib.parse.urlencode({"error": "access_denied", "state": state})
        return RedirectResponse(f"{redirect_uri}?{qs}", status_code=302)

    # Generate auth code
    code = secrets.token_urlsafe(32)
    _auth_codes[code] = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "code_challenge": code_challenge,
        "code_challenge_method": code_challenge_method,
        "exp": int(time.time()) + 300,  # 5 min TTL
    }

    qs = urllib.parse.urlencode({"code": code, "state": state})
    return RedirectResponse(f"{redirect_uri}?{qs}", status_code=302)


# ── Endpoint: Token issuance (client_credentials + authorization_code) ────────
async def token_endpoint(request: Request):
    content_type = request.headers.get("content-type", "")
    if "application/x-www-form-urlencoded" in content_type or "multipart" in content_type:
        form = await request.form()
        grant_type     = form.get("grant_type", "")
        client_id      = form.get("client_id", "")
        client_secret  = form.get("client_secret", "")
        code           = form.get("code", "")
        redirect_uri   = form.get("redirect_uri", "")
        code_verifier  = form.get("code_verifier", "")
    else:
        try:
            body = await request.json()
            grant_type     = body.get("grant_type", "")
            client_id      = body.get("client_id", "")
            client_secret  = body.get("client_secret", "")
            code           = body.get("code", "")
            redirect_uri   = body.get("redirect_uri", "")
            code_verifier  = body.get("code_verifier", "")
        except Exception:
            return JSONResponse({"error": "invalid_request"}, status_code=400)

    # HTTP Basic auth fallback
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Basic "):
        try:
            decoded = base64.b64decode(auth_header[6:]).decode()
            client_id, client_secret = decoded.split(":", 1)
        except Exception:
            pass

    # ── Grant: authorization_code ──────────────────────────────────────────
    if grant_type == "authorization_code":
        entry = _auth_codes.pop(code, None)
        if not entry:
            return JSONResponse({"error": "invalid_grant", "error_description": "Code not found or expired"}, status_code=400)
        if entry["exp"] < int(time.time()):
            return JSONResponse({"error": "invalid_grant", "error_description": "Code expired"}, status_code=400)
        if entry["client_id"] and client_id and entry["client_id"] != client_id:
            return JSONResponse({"error": "invalid_client"}, status_code=401)
        if entry["redirect_uri"] and entry["redirect_uri"] != redirect_uri:
            return JSONResponse({"error": "invalid_grant", "error_description": "redirect_uri mismatch"}, status_code=400)

        # PKCE verification
        if entry.get("code_challenge"):
            if not code_verifier:
                return JSONResponse({"error": "invalid_grant", "error_description": "code_verifier required"}, status_code=400)
            method = entry.get("code_challenge_method", "S256")
            if method == "S256":
                digest = hashlib.sha256(code_verifier.encode()).digest()
                computed = base64.urlsafe_b64encode(digest).decode().rstrip("=")
            else:
                computed = code_verifier
            if not hmac.compare_digest(computed, entry["code_challenge"]):
                return JSONResponse({"error": "invalid_grant", "error_description": "PKCE verification failed"}, status_code=400)

        token = _make_token(client_id or entry["client_id"])
        return JSONResponse({
            "access_token": token,
            "token_type": "Bearer",
            "expires_in": TOKEN_TTL_SEC,
            "scope": "mcp:read",
        })

    # ── Grant: client_credentials ──────────────────────────────────────────
    if grant_type == "client_credentials":
        if client_id != CLIENT_ID or client_secret != CLIENT_SECRET:
            return JSONResponse({"error": "invalid_client", "error_description": "Invalid credentials"}, status_code=401)
        token = _make_token(client_id)
        return JSONResponse({
            "access_token": token,
            "token_type": "Bearer",
            "expires_in": TOKEN_TTL_SEC,
            "scope": "mcp:read",
        })

    return JSONResponse({"error": "unsupported_grant_type"}, status_code=400)


# ── Health check ──────────────────────────────────────────────────────────────
async def health_check(request: Request):
    return JSONResponse({"status": "ok", "mcp": "myg-portal", "auth": "oauth2"})


# Insert routes BEFORE the MCP routes
app.routes.insert(0, Route("/",                                      health_check,       methods=["GET"]))
app.routes.insert(1, Route("/health",                                health_check,       methods=["GET"]))
app.routes.insert(2, Route("/.well-known/oauth-authorization-server", oauth_metadata,   methods=["GET"]))
app.routes.insert(3, Route("/oauth/token",                           token_endpoint,     methods=["POST"]))
app.routes.insert(4, Route("/authorize",                             authorize_endpoint, methods=["GET", "POST"]))

# Bearer auth middleware
app.add_middleware(BearerAuthMiddleware)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=port)

