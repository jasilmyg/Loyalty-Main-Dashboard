"""
ClickHouse Assistant — engine
------------------------------
A chatbot brain that can answer ANY question about the ClickHouse database:

  • Data questions   → Gemini writes SQL, we run it READ-ONLY, Gemini explains the result.
  • Schema questions → the live schema (tables / columns / sizes) is injected into the prompt
                       and can be re-inspected on demand via the `inspect_table` tool.
  • General ClickHouse knowledge (syntax, functions, engines, tuning) → answered directly by
                       the model, and verified against `system.*` tables when useful.

Safety (defence in depth)
  1. Static SQL validation (single statement, read-only verbs only, no table functions).
  2. Every query is sent with the ClickHouse setting `readonly=1` – the SERVER itself rejects writes.
  3. Execution-time / result-row caps on every query.

LLM: Google Gemini REST API (free tier) – no extra SDK needed, only `requests`.
"""

import json
import logging
import os
import re
import threading
import time
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Dict, List, Optional, Tuple

import requests

logger = logging.getLogger(__name__)

# ── Configuration ─────────────────────────────────────────────────────────────
# Ordered fallback list – first model that answers wins (free-tier models can be busy / rate limited).
GEMINI_MODELS = [
    m.strip() for m in os.environ.get(
        "GEMINI_MODELS",
        "gemini-3.6-flash,gemini-3-flash-preview,gemini-3.7-flash,gemini-3.1-flash-lite,gemini-3.5-flash",
    ).split(",") if m.strip()
]
GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

MAX_TOOL_STEPS = 6            # max SQL/inspect calls per question
OVERALL_DEADLINE_SEC = 100    # hard stop for one question (gunicorn timeout is 120s)
LLM_ROWS_LIMIT = 40           # rows of each result shown to the LLM
UI_ROWS_LIMIT = 500           # rows of each result returned to the browser
LLM_RESULT_CHAR_LIMIT = 7000  # char cap of a tool result passed back to the LLM
MAX_HISTORY_TURNS = 10        # previous chat messages sent for context

CH_QUERY_SETTINGS = {
    "readonly": 1,                      # server-side guarantee: no writes, no DDL
    "max_execution_time": 30,           # seconds
    "max_result_rows": 10000,
    "result_overflow_mode": "break",
}

SYSTEM_DBS = ("system", "INFORMATION_SCHEMA", "information_schema")

# ── SQL safety ────────────────────────────────────────────────────────────────
_ALLOWED_START = ("SELECT", "WITH", "DESCRIBE", "DESC", "SHOW", "EXPLAIN", "EXISTS")
_FORBIDDEN = re.compile(
    r"\b(INSERT|UPDATE|DELETE|DROP|ALTER|TRUNCATE|RENAME|GRANT|REVOKE|ATTACH|DETACH|"
    r"OPTIMIZE|KILL|SET|USE|EXCHANGE|UNDROP|MOVE|BACKUP|RESTORE)\b",
    re.IGNORECASE,
)
_TABLE_FUNCS = re.compile(
    r"\b(url|urlCluster|file|s3|s3Cluster|gcs|hdfs|remote|remoteSecure|mysql|postgresql|mongodb|redis|"
    r"jdbc|odbc|sqlite|azureBlobStorage|deltaLake|iceberg|hudi|input|executable|cluster|clusterAllReplicas|"
    r"dictionary)\s*\(",
    re.IGNORECASE,
)
_STRING_LITERAL = re.compile(r"'(?:[^'\\]|\\.|'')*'")
_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)?$")


def _strip_literals_and_comments(sql: str) -> str:
    s = _STRING_LITERAL.sub("''", sql)
    s = re.sub(r"/\*.*?\*/", " ", s, flags=re.DOTALL)
    s = re.sub(r"--[^\n]*", " ", s)
    return s


def validate_sql(sql: str) -> Tuple[bool, str, str]:
    """Returns (ok, cleaned_sql_or_empty, error_message)."""
    if not sql or not sql.strip():
        return False, "", "Empty SQL."
    cleaned = sql.strip().rstrip(";").strip()
    probe = _strip_literals_and_comments(cleaned)
    if ";" in probe:
        return False, "", "Only a single SQL statement is allowed."
    first = probe.strip().split(None, 1)[0].upper() if probe.strip() else ""
    if first not in _ALLOWED_START:
        return False, "", f"Only read-only queries are allowed ({', '.join(_ALLOWED_START)}). Got: {first or 'nothing'}."
    m = _FORBIDDEN.search(probe)
    if m:
        return False, "", f"Forbidden keyword '{m.group(1).upper()}' – this assistant is read-only."
    m = _TABLE_FUNCS.search(probe)
    if m:
        return False, "", f"Table function '{m.group(1)}()' is not allowed."
    if re.search(r"\bINTO\s+OUTFILE\b", probe, re.IGNORECASE):
        return False, "", "INTO OUTFILE is not allowed."
    return True, cleaned, ""


# ── ClickHouse execution ──────────────────────────────────────────────────────
def _json_safe(v: Any) -> Any:
    if v is None or isinstance(v, (bool, int, str)):
        return v
    if isinstance(v, float):
        return None if v != v else v  # NaN → null
    if isinstance(v, Decimal):
        return float(v)
    if isinstance(v, (datetime, date)):
        return v.isoformat(sep=" ") if isinstance(v, datetime) else v.isoformat()
    if isinstance(v, bytes):
        return v.decode("utf-8", errors="replace")
    if isinstance(v, (list, tuple, set)):
        return [_json_safe(x) for x in v]
    if isinstance(v, dict):
        return {str(k): _json_safe(x) for k, x in v.items()}
    return str(v)


def run_readonly_query(sql: str) -> Dict[str, Any]:
    """Validate + execute a query read-only. Never raises; returns {'error': ...} on failure."""
    ok, cleaned, err = validate_sql(sql)
    if not ok:
        return {"error": err, "sql": sql}

    from analytics.clickhouse_service import get_ch_client, reset_client

    client = get_ch_client()
    if client is None:
        return {"error": "Could not connect to ClickHouse.", "sql": cleaned}
    t0 = time.time()
    try:
        res = client.query(cleaned, settings=CH_QUERY_SETTINGS)
    except Exception as e:  # noqa: BLE001
        msg = str(e)
        if "Connection" in msg or "timed out" in msg.lower():
            reset_client()
        # ClickHouse errors are verbose – keep the first meaningful part so the LLM can self-correct.
        return {"error": msg[:900], "sql": cleaned}
    elapsed = int((time.time() - t0) * 1000)
    cols = list(res.column_names)
    rows_all = res.result_rows
    total = len(rows_all)
    rows = [[_json_safe(c) for c in r] for r in rows_all[:UI_ROWS_LIMIT]]
    return {
        "sql": cleaned,
        "columns": cols,
        "rows": rows,
        "row_count": total,
        "truncated": total > UI_ROWS_LIMIT,
        "elapsed_ms": elapsed,
    }


# ── Schema catalog (cached) ───────────────────────────────────────────────────
_schema_lock = threading.Lock()
_schema_cache: Dict[str, Any] = {"ts": 0.0, "data": None}
SCHEMA_TTL_SEC = 600


def _human(n: Optional[float]) -> str:
    if n is None:
        return "?"
    n = float(n)
    for unit in ("", "K", "M", "B"):
        if abs(n) < 1000:
            return f"{n:.0f}{unit}" if unit == "" else f"{n:.1f}{unit}"
        n /= 1000
    return f"{n:.1f}T"


def get_schema(force: bool = False) -> Dict[str, Any]:
    """Returns {'tables': [{database,name,engine,rows,bytes,sorting_key,partition_key,comment,columns:[{name,type,comment}]}]}"""
    with _schema_lock:
        if not force and _schema_cache["data"] and time.time() - _schema_cache["ts"] < SCHEMA_TTL_SEC:
            return _schema_cache["data"]

    skip = ",".join(f"'{d}'" for d in SYSTEM_DBS)
    t = run_readonly_query(
        "SELECT database, name, engine, total_rows, total_bytes, sorting_key, partition_key, comment "
        f"FROM system.tables WHERE database NOT IN ({skip}) AND NOT startsWith(name, '.inner') ORDER BY database, name"
    )
    c = run_readonly_query(
        "SELECT database, `table`, name, type, comment "
        f"FROM system.columns WHERE database NOT IN ({skip}) ORDER BY database, `table`, position"
    )
    if "error" in t or "error" in c:
        raise RuntimeError((t.get("error") or c.get("error")))

    cols_by_table: Dict[Tuple[str, str], List[Dict[str, str]]] = {}
    for db, tbl, name, typ, comment in c["rows"]:
        cols_by_table.setdefault((db, tbl), []).append({"name": name, "type": typ, "comment": comment or ""})
    tables = []
    for db, name, engine, rows, nbytes, skey, pkey, comment in t["rows"]:
        tables.append({
            "database": db, "name": name, "engine": engine, "rows": rows, "bytes": nbytes,
            "sorting_key": skey or "", "partition_key": pkey or "", "comment": comment or "",
            "columns": cols_by_table.get((db, name), []),
        })
    data = {"tables": tables, "loaded_at": datetime.utcnow().isoformat() + "Z"}
    with _schema_lock:
        _schema_cache.update(ts=time.time(), data=data)
    return data


def schema_as_prompt_text() -> str:
    lines = []
    for t in get_schema()["tables"]:
        fq = f"{t['database']}.{t['name']}"
        meta = [t["engine"], f"{_human(t['rows'])} rows"]
        if t["sorting_key"]:
            meta.append(f"ORDER BY {t['sorting_key']}")
        if t["partition_key"]:
            meta.append(f"PARTITION BY {t['partition_key']}")
        head = f"- {fq} [{'; '.join(meta)}]"
        if t["comment"]:
            head += f" -- {t['comment']}"
        lines.append(head)
        lines.append("    cols: " + ", ".join(
            f"{c['name']} {c['type']}" + (f" /*{c['comment']}*/" if c["comment"] else "") for c in t["columns"]
        ))
    return "\n".join(lines)


# ── Prompt ────────────────────────────────────────────────────────────────────
def build_system_prompt() -> str:
    try:
        schema_txt = schema_as_prompt_text()
    except Exception as e:  # noqa: BLE001
        schema_txt = f"(schema could not be loaded right now: {e}. Use inspect_table / run_sql on system.tables.)"
    today = datetime.now().strftime("%Y-%m-%d")
    return f"""You are the **ClickHouse Assistant** for the myG Loyalty portal – an expert in ClickHouse AND in this
specific ClickHouse Cloud database (myG retail stores in Kerala, India; amounts are in Indian Rupees ₹). Today is {today}.

You can answer ANY question related to this ClickHouse database:
1. DATA questions (sales, customers, branches, stock, loyalty, pine-lab, OSG, market-basket …) → use the `run_sql` tool.
2. SCHEMA / STRUCTURE questions (what tables exist, columns, sizes, engines, keys, relationships) → use the schema below,
   and `inspect_table` for samples / exact DDL.
3. GENERAL ClickHouse questions (SQL syntax, functions, engines, MergeTree, performance tuning, best practices,
   errors) → answer from your expertise. If you are not 100% sure a function exists or how it behaves in this
   server version, verify with `run_sql` (e.g. SELECT name FROM system.functions WHERE name ILIKE '%x%').
   Server-side questions (table sizes on disk, parts, running queries, settings) can be answered from `system.*` tables.

# Tool rules
- NEVER invent numbers, table names or columns. For anything data-related you MUST query first.
- Only read-only SQL is possible (SELECT / WITH / DESCRIBE / SHOW / EXPLAIN). If the user asks to modify data or
  schema, politely refuse and explain this assistant is read-only; you may show the SQL they could run themselves.
- Use ClickHouse SQL dialect only. Always qualify with correct column names from the schema; check column TYPES:
  numeric values stored as String need toFloat64OrZero()/toInt64OrZero(); dates stored as String need toDate().
- Prefer aggregations, add ORDER BY and LIMIT (default LIMIT 50 unless the user wants everything). Never SELECT * on the
  big tables without LIMIT. Avoid expensive cross joins on tables with tens of millions of rows.
- If a query fails, read the error, fix the SQL and retry (you have at most {MAX_TOOL_STEPS} tool calls per question).
- If the question is ambiguous (unknown period, metric or table) pick the most sensible interpretation, state the
  assumption in one line, and answer – only ask a clarifying question when truly impossible to proceed.

# Business conventions of this database
- Placeholder rows with date = '1970-01-01' are invalid – exclude them from time-based analysis.
- The main dashboards EXCLUDE internal/transfer data: invoices whose invoice_no contains 'SMC' or 'EI' and branches
  'HEAD OFFICE', 'UG SMART CHOICE', '3GH'. For sales/revenue questions on azure_invoice_report / azure_sales_report apply:
    NOT (upper(invoice_no) LIKE '%SMC%' OR upper(invoice_no) LIKE '%EI%' OR upper(branch) IN ('HEAD OFFICE','UG SMART CHOICE','3GH'))
  and mention that this standard filter was applied (skip it only if the user asks for raw/all data).
- azure_invoice_report = one row per invoice; azure_sales_report = one row per item sold (join on invoice_no);
  item_master (item_code → product/brand/category); branch_master (branch code → name, RBM, BDM, district).

# Answer style
- Be concise and business-friendly. Lead with the direct answer, then key insights. Use Markdown (short tables ok).
- Format money as ₹ with Indian grouping when natural (₹12,34,567), large totals may use Lakh/Crore in brackets.
- The raw result table is shown to the user automatically below your answer – do NOT repeat all rows; summarise
  or show only the top few. Mention the date range / filters you used.
- Customer mobile numbers are personal data: show them only if explicitly asked, otherwise aggregate.

# Live database schema (ClickHouse server, database `default`)
{schema_txt}
"""


# ── Tools exposed to Gemini ───────────────────────────────────────────────────
TOOL_DECLARATIONS = [{
    "functionDeclarations": [
        {
            "name": "run_sql",
            "description": (
                "Run ONE read-only ClickHouse SQL query (SELECT/WITH/DESCRIBE/SHOW/EXPLAIN) and get columns + rows. "
                "Server enforces readonly mode; results are capped. Use for all data/system-table questions."
            ),
            "parameters": {
                "type": "OBJECT",
                "properties": {"sql": {"type": "STRING", "description": "A single ClickHouse SQL statement."}},
                "required": ["sql"],
            },
        },
        {
            "name": "inspect_table",
            "description": "Get the exact CREATE TABLE DDL, columns and a few sample rows of one table (e.g. 'azure_sales_report').",
            "parameters": {
                "type": "OBJECT",
                "properties": {
                    "table": {"type": "STRING", "description": "Table name, optionally db-qualified."},
                    "sample_rows": {"type": "INTEGER", "description": "How many sample rows (0-5). Default 3."},
                },
                "required": ["table"],
            },
        },
    ]
}]


def _result_for_llm(res: Dict[str, Any]) -> Dict[str, Any]:
    if "error" in res:
        return {"error": res["error"]}
    out = {
        "columns": res["columns"],
        "rows": res["rows"][:LLM_ROWS_LIMIT],
        "total_rows_returned": res["row_count"],
        "shown_rows": min(res["row_count"], LLM_ROWS_LIMIT),
        "elapsed_ms": res["elapsed_ms"],
    }
    txt = json.dumps(out, default=str)
    while len(txt) > LLM_RESULT_CHAR_LIMIT and out["rows"]:
        out["rows"] = out["rows"][: max(1, len(out["rows"]) // 2)]
        out["shown_rows"] = len(out["rows"])
        txt = json.dumps(out, default=str)
        if len(out["rows"]) == 1:
            break
    return out


def _tool_run_sql(args: Dict[str, Any], ui_results: List[Dict[str, Any]]) -> Dict[str, Any]:
    res = run_readonly_query(str(args.get("sql", "")))
    ui_results.append(res)
    return _result_for_llm(res)


def _tool_inspect_table(args: Dict[str, Any], ui_results: List[Dict[str, Any]]) -> Dict[str, Any]:
    table = str(args.get("table", "")).strip().strip("`")
    if not _IDENT.match(table):
        return {"error": "Invalid table name."}
    try:
        n = max(0, min(5, int(args.get("sample_rows", 3))))
    except (TypeError, ValueError):
        n = 3
    ddl = run_readonly_query(f"SHOW CREATE TABLE {table}")
    out: Dict[str, Any] = {}
    if "error" in ddl:
        return {"error": ddl["error"]}
    out["create_table"] = str(ddl["rows"][0][0])[:3500] if ddl["rows"] else ""
    if n:
        sample = run_readonly_query(f"SELECT * FROM {table} LIMIT {n}")
        if "error" not in sample:
            out["sample_columns"] = sample["columns"]
            out["sample_rows"] = sample["rows"]
            ui_results.append(sample)
    return out


_TOOLS = {"run_sql": _tool_run_sql, "inspect_table": _tool_inspect_table}


# ── Gemini REST client ────────────────────────────────────────────────────────
class LLMError(Exception):
    pass


def _gemini_generate(contents: List[Dict], system_prompt: str, allow_tools: bool, deadline: float) -> Tuple[Dict, str]:
    api_key = os.environ.get("GEMINI_API_KEY", "")
    if not api_key:
        raise LLMError("GEMINI_API_KEY is not configured. Add it to the .env file (free key: https://aistudio.google.com/apikey).")
    body: Dict[str, Any] = {
        "systemInstruction": {"parts": [{"text": system_prompt}]},
        "contents": contents,
        "generationConfig": {"temperature": 0.1, "maxOutputTokens": 8192, "thinkingConfig": {"thinkingLevel": "low"}},
    }
    if allow_tools:
        body["tools"] = TOOL_DECLARATIONS
    else:
        body["tools"] = TOOL_DECLARATIONS
        body["toolConfig"] = {"functionCallingConfig": {"mode": "NONE"}}

    last_err = "unknown error"
    for model in GEMINI_MODELS:
        for attempt in range(2):
            remaining = deadline - time.time()
            if remaining < 5:
                raise LLMError("The request took too long. Please try a simpler question.")
            try:
                r = requests.post(
                    GEMINI_URL.format(model=model),
                    headers={"x-goog-api-key": api_key, "Content-Type": "application/json"},
                    json=body,
                    timeout=min(70, remaining),
                )
            except requests.RequestException as e:
                last_err = f"{model}: {e}"
                break  # try next model
            if r.status_code == 200:
                data = r.json()
                if data.get("candidates") and data["candidates"][0].get("content"):
                    return data, model
                last_err = f"{model}: empty response ({data.get('promptFeedback') or data['candidates'][0].get('finishReason', '')})"
                break
            last_err = f"{model}: HTTP {r.status_code} {r.text[:200]}"
            if r.status_code in (429, 500, 502, 503, 504) and attempt == 0:
                time.sleep(1.5)  # brief retry once, then fall through to next model
                continue
            break  # 400/403/404 etc. → next model
    logger.warning("Gemini failed on all models: %s", last_err)
    if "429" in last_err:
        raise LLMError("The free Gemini quota is exhausted for the moment. Please wait a minute and try again.")
    raise LLMError(f"The AI model is unavailable right now ({last_err[:160]}).")


# ── Public entry point ────────────────────────────────────────────────────────
def _history_to_contents(history: List[Dict[str, str]]) -> List[Dict]:
    contents: List[Dict] = []
    for h in (history or [])[-MAX_HISTORY_TURNS:]:
        role = "model" if h.get("role") in ("assistant", "model") else "user"
        text = str(h.get("content", "")).strip()[:6000]
        if not text:
            continue
        if contents and contents[-1]["role"] == role:  # Gemini requires alternating roles
            contents[-1]["parts"][0]["text"] += "\n" + text
        else:
            contents.append({"role": role, "parts": [{"text": text}]})
    while contents and contents[0]["role"] != "user":
        contents.pop(0)
    return contents


def _text_of(content: Dict) -> str:
    return "".join(p.get("text", "") for p in content.get("parts", []) if p.get("text") and not p.get("thought")).strip()


def ask(question: str, history: Optional[List[Dict[str, str]]] = None) -> Dict[str, Any]:
    """Answer one question. Returns {answer, results[], steps[], model, elapsed_ms}."""
    started = time.time()
    deadline = started + OVERALL_DEADLINE_SEC
    question = (question or "").strip()
    if not question:
        return {"answer": "Please type a question.", "results": [], "steps": [], "model": None, "elapsed_ms": 0}

    system_prompt = build_system_prompt()
    contents = _history_to_contents(history or [])
    if contents and contents[-1]["role"] == "user":
        contents.pop()  # the new question replaces a dangling user turn
    contents.append({"role": "user", "parts": [{"text": question[:4000]}]})

    ui_results: List[Dict[str, Any]] = []
    steps: List[Dict[str, Any]] = []
    used_model = None

    for _ in range(MAX_TOOL_STEPS + 1):
        force_answer = len(steps) >= MAX_TOOL_STEPS
        data, used_model = _gemini_generate(contents, system_prompt, allow_tools=not force_answer, deadline=deadline)
        content = data["candidates"][0]["content"]
        calls = [p["functionCall"] for p in content.get("parts", []) if "functionCall" in p]

        if not calls or force_answer:
            answer = _text_of(content) or "I could not produce an answer. Please rephrase your question."
            break

        contents.append(content)  # keep verbatim (includes thought signatures needed by Gemini 3.x)
        responses = []
        for call in calls:
            name, args = call.get("name"), call.get("args") or {}
            fn = _TOOLS.get(name)
            result = fn(args, ui_results) if fn else {"error": f"Unknown tool {name}"}
            step = {"tool": name, "sql": args.get("sql") or f"inspect {args.get('table', '')}"}
            if "error" in result:
                step["error"] = result["error"]
            steps.append(step)
            responses.append({"functionResponse": {"name": name, "response": {"result": result}}})
        contents.append({"role": "user", "parts": responses})
    else:  # pragma: no cover
        answer = "I ran out of steps before finishing. Please narrow the question."

    # Only surface successful results to the UI (errors are shown in the 'steps' trail).
    shown = [r for r in ui_results if "error" not in r and r.get("columns")]
    return {
        "answer": answer,
        "results": shown[-1:],
        "steps": steps,
        "model": used_model,
        "elapsed_ms": int((time.time() - started) * 1000),
    }
