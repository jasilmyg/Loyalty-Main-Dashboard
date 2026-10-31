"""
StarRocks Analytics Service & ClickHouse-to-StarRocks SQL Dialect Translator
----------------------------------------------------------------------------
Connects the StarRocks Clone Portal to StarRocks 4.1+ (`Loyalty_Analytics`, configured via `.env`)
using a thread-local persistent PyMySQL connection and a balanced-parenthesis
ClickHouse -> StarRocks SQL translator + Client Adapter matching `clickhouse_connect`.
"""

import os
import re
import time
import decimal
import datetime
import threading
import logging
from pathlib import Path
from typing import Optional, List, Any, Dict, Tuple

import pymysql
import pandas as pd
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

logger = logging.getLogger(__name__)

# ─── StarRocks Credentials (loaded strictly from .env / environment) ─────────
SR_HOST     = os.environ.get("STARROCKS_HOST",     "")
SR_PORT     = int(os.environ.get("STARROCKS_PORT", "9030"))
SR_USER     = os.environ.get("STARROCKS_USER",     "")
SR_PASSWORD = os.environ.get("STARROCKS_PASSWORD", "")
SR_DATABASE = os.environ.get("STARROCKS_DATABASE", "Loyalty_Analytics")

_sr_local = threading.local()


def get_raw_sr_connection() -> pymysql.connections.Connection:
    """Return a per-thread persistent PyMySQL connection to StarRocks Loyalty_Analytics."""
    conn = getattr(_sr_local, "conn", None)
    if conn is not None:
        try:
            conn.ping(reconnect=True)
            return conn
        except Exception:
            try:
                conn.close()
            except Exception:
                pass
            _sr_local.conn = None

    conn = pymysql.connect(
        host=SR_HOST,
        port=SR_PORT,
        user=SR_USER,
        password=SR_PASSWORD,
        database=SR_DATABASE,
        charset="utf8mb4",
        connect_timeout=10,
        read_timeout=180,
        write_timeout=180,
        autocommit=True,
    )
    _sr_local.conn = conn
    return conn


def reset_sr_connection():
    conn = getattr(_sr_local, "conn", None)
    if conn is not None:
        try:
            conn.close()
        except Exception:
            pass
    _sr_local.conn = None


# ═════════════════════════════════════════════════════════════════════════════
# ClickHouse -> StarRocks 4.1 SQL Translator
# ═════════════════════════════════════════════════════════════════════════════

_SQL_CLAUSE_STOP_WORDS = {
    "WHERE", "GROUP", "ORDER", "HAVING", "LIMIT", "OFFSET",
    "UNION", "EXCEPT", "INTERSECT", "INNER", "LEFT", "RIGHT",
    "FULL", "CROSS", "JOIN", "ON", "USING", "WINDOW", "SETTINGS",
}

_AGG_MARKERS = (
    "SUM(", "COUNT(", "AVG(", "MIN(", "MAX(", "MAX_BY(", "MIN_BY(",
    "ANY_VALUE(", "ARRAY_AGG(", "STDDEV_POP(", "NTILE(", "ROW_NUMBER(", " OVER"
)


def _escape_sr_literal(val: Any) -> str:
    """Safely format a Python parameter value as a StarRocks SQL literal."""
    if val is None:
        return "NULL"
    if isinstance(val, bool):
        return "1" if val else "0"
    if isinstance(val, (int, float, decimal.Decimal)):
        return str(val)
    if isinstance(val, datetime.datetime):
        return f"'{val.strftime('%Y-%m-%d %H:%M:%S')}'"
    if isinstance(val, datetime.date):
        return f"'{val.strftime('%Y-%m-%d')}'"
    s = str(val).replace("\\", "\\\\").replace("'", "\\'")
    return f"'{s}'"


def _bind_ch_parameters(sql: str, params: Optional[Dict[str, Any]]) -> str:
    """Replace ClickHouse `{name:Type}` and `has({arr:Array(...)}, expr)` placeholders."""
    if not params:
        params = {}

    # 1. Handle has({param:Array(...)}, expr)
    has_pat = re.compile(
        r"\bhas\s*\(\s*\{\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*:\s*Array\([^)]+\)\s*\}\s*,\s*",
        re.IGNORECASE,
    )
    pos = 0
    while True:
        m = has_pat.search(sql, pos)
        if not m:
            break
        pname = m.group(1)
        expr_start = m.end()
        # Walk balanced parens to find closing ')' of has(...)
        depth = 1
        i = expr_start
        in_str = False
        while i < len(sql) and depth > 0:
            ch = sql[i]
            if ch == "'" and (i == 0 or sql[i - 1] != "\\"):
                in_str = not in_str
            elif not in_str:
                if ch == "(":
                    depth += 1
                elif ch == ")":
                    depth -= 1
            i += 1
        expr_sql = sql[expr_start : i - 1].strip()
        arr_vals = params.get(pname) or []
        if not arr_vals:
            replacement = "(1 = 0)"
        else:
            joined = ", ".join(_escape_sr_literal(v) for v in arr_vals)
            replacement = f"({expr_sql} IN ({joined}))"
        sql = sql[: m.start()] + replacement + sql[i:]
        pos = m.start() + len(replacement)

    # 2. Replace scalar {name:Type} placeholders
    def _replace_param(match: re.Match) -> str:
        pname = match.group(1)
        ptype = match.group(2).strip().lower()
        if pname not in params:
            return match.group(0)
        val = params[pname]
        lit = _escape_sr_literal(val)
        if ptype.startswith("date") and not ptype.startswith("datetime"):
            return f"CAST({lit} AS DATE)"
        if ptype.startswith("datetime"):
            return f"CAST({lit} AS DATETIME)"
        if "int" in ptype or "float" in ptype or "decimal" in ptype:
            return str(val)
        return lit

    sql = re.sub(
        r"\{\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*:\s*([a-zA-Z0-9_()]+)\s*\}",
        _replace_param,
        sql,
    )
    return sql


def _strip_comments_and_protect_strings(sql: str) -> Tuple[str, List[str]]:
    """
    Strip `-- ...` and `/* ... */` SQL comments while preserving `'...'` string literals,
    replacing each string literal with `__STRLIT_i__` so subsequent syntax transforms
    never touch text inside quotes.
    """
    literals: List[str] = []
    out: List[str] = []
    i = 0
    n = len(sql)
    while i < n:
        ch = sql[i]
        # Single-line comment
        if ch == "-" and i + 1 < n and sql[i + 1] == "-":
            while i < n and sql[i] != "\n":
                i += 1
            continue
        # Block comment
        if ch == "/" and i + 1 < n and sql[i + 1] == "*":
            i += 2
            while i + 1 < n and not (sql[i] == "*" and sql[i + 1] == "/"):
                i += 1
            i += 2
            out.append(" ")
            continue
        # Single-quoted string literal
        if ch == "'":
            start = i
            i += 1
            while i < n:
                if sql[i] == "\\":
                    i += 2
                    continue
                if sql[i] == "'":
                    if i + 1 < n and sql[i + 1] == "'":
                        i += 2
                        continue
                    i += 1
                    break
                i += 1
            lit = sql[start:i]
            idx = len(literals)
            literals.append(lit)
            out.append(f"__STRLIT_{idx}__")
            continue
        out.append(ch)
        i += 1
    return "".join(out), literals


def _unquote_lit(token: str, literals: List[str]) -> Optional[str]:
    """If token is `__STRLIT_k__`, return its unquoted inner string content."""
    m = re.fullmatch(r"__STRLIT_(\d+)__", token.strip())
    if not m:
        return None
    raw = literals[int(m.group(1))]
    if len(raw) >= 2 and raw[0] == "'" and raw[-1] == "'":
        return raw[1:-1]
    return raw


def _split_top_level_args(arg_str: str) -> List[str]:
    """Split comma-separated arguments respecting nested `(...)` and `[...]`."""
    s = arg_str.strip()
    if not s:
        return []
    args: List[str] = []
    depth_p = 0
    depth_b = 0
    start = 0
    for idx, ch in enumerate(s):
        if ch == "(":
            depth_p += 1
        elif ch == ")":
            depth_p -= 1
        elif ch == "[":
            depth_b += 1
        elif ch == "]":
            depth_b -= 1
        elif ch == "," and depth_p == 0 and depth_b == 0:
            args.append(s[start:idx].strip())
            start = idx + 1
    args.append(s[start:].strip())
    return args


def _looks_like_date_expr(expr: str) -> bool:
    e = expr.strip()
    el = e.lower()
    if "as date" in el or "date_trunc" in el or "str_to_date" in el or "curdate()" in el:
        return True
    if el in ("date", "d", "parsed_date", "sale_d", "purchase_date", "activity_date", "last_date", "first_date"):
        return True
    if el.endswith(".date") or el.endswith(".parsed_date") or el.endswith(".d"):
        return True
    return False


def _rewrite_ch_function(fname: str, prev_non_ws_char: str, args: List[str], raw_inner: str, literals: List[str]) -> Optional[str]:
    """
    Translate a single ClickHouse function call `fname(args...)` (whose inner arguments
    have ALREADY been recursively translated) into StarRocks 4.1 SQL.
    Returns None if no rewrite is needed (keeps `fname(translated_inner)`).
    """
    fn = fname.lower()

    # ── Aggregations & Conditional Aggregations ──────────────────────────────
    if fn == "count":
        if len(args) == 0:
            return "COUNT(*)"
        return None

    if fn in ("countdistinct", "uniq", "uniqexact"):
        if len(args) == 1:
            return f"COUNT(DISTINCT {args[0]})"

    if fn in ("countdistinctif", "uniqif", "uniqexactif"):
        if len(args) == 2:
            return f"COUNT(DISTINCT IF({args[1]}, {args[0]}, NULL))"

    if fn == "countif":
        if len(args) == 1:
            return f"SUM(IF({args[0]}, 1, 0))"
        if len(args) == 2:
            a0 = args[0].strip()
            m_dist = re.match(r"^DISTINCT\s+(.+)$", a0, re.IGNORECASE | re.DOTALL)
            if m_dist:
                return f"COUNT(DISTINCT IF({args[1]}, {m_dist.group(1)}, NULL))"
            return f"SUM(IF({args[1]} AND ({a0}) IS NOT NULL, 1, 0))"

    if fn == "sumif" and len(args) == 2:
        return f"SUM(IF({args[1]}, {args[0]}, 0))"

    if fn == "avgif" and len(args) == 2:
        return f"AVG(IF({args[1]}, {args[0]}, NULL))"

    if fn == "minif" and len(args) == 2:
        zero_default = "CAST('1970-01-01' AS DATE)" if _looks_like_date_expr(args[0]) else "0"
        return f"COALESCE(MIN(IF({args[1]}, {args[0]}, NULL)), {zero_default})"

    if fn == "maxif" and len(args) == 2:
        zero_default = "CAST('1970-01-01' AS DATE)" if _looks_like_date_expr(args[0]) else "0"
        return f"COALESCE(MAX(IF({args[1]}, {args[0]}, NULL)), {zero_default})"

    if fn == "argmax" and len(args) == 2:
        return f"MAX_BY({args[0]}, {args[1]})"

    if fn == "argmin" and len(args) == 2:
        return f"MIN_BY({args[0]}, {args[1]})"

    if fn == "argmaxif" and len(args) == 3:
        return f"MAX_BY(IF({args[2]}, {args[0]}, NULL), IF({args[2]}, {args[1]}, NULL))"

    if fn == "any" and len(args) == 1:
        # Avoid rewriting SQL `= ANY (...)` comparison operator
        if prev_non_ws_char not in ("=", "<", ">", "!"):
            return f"ANY_VALUE({args[0]})"

    if fn == "grouparray" and len(args) == 1:
        return f"ARRAY_AGG({args[0]})"

    if fn == "groupuniqarray" and len(args) == 1:
        return f"ARRAY_DISTINCT(ARRAY_AGG({args[0]}))"

    if fn == "stddevpop" and len(args) == 1:
        return f"STDDEV_POP({args[0]})"

    # ── Control Flow / Nulls ─────────────────────────────────────────────────
    if fn == "multiif" and len(args) >= 3:
        parts = ["CASE"]
        i = 0
        while i + 1 < len(args) - 1:
            parts.append(f"WHEN {args[i]} THEN {args[i + 1]}")
            i += 2
        if i == len(args) - 2:
            # Even number of args? Shouldn't happen in valid multiIf, fallback
            parts.append(f"WHEN {args[i]} THEN {args[i + 1]}")
        else:
            parts.append(f"ELSE {args[-1]}")
        parts.append("END")
        return " ".join(parts)

    if fn == "isnull" and len(args) == 1:
        return f"({args[0]} IS NULL)"

    if fn == "isnotnull" and len(args) == 1:
        return f"({args[0]} IS NOT NULL)"

    # ── Window Functions ─────────────────────────────────────────────────────
    if fn == "laginframe" and len(args) >= 1:
        return f"LAG({', '.join(args)})"

    if fn == "leadinframe" and len(args) >= 1:
        return f"LEAD({', '.join(args)})"

    # ── Date & Time Functions ────────────────────────────────────────────────
    if fn == "todate" and len(args) == 1:
        return f"CAST({args[0]} AS DATE)"

    if fn == "todatetime" and len(args) >= 1:
        return f"CAST({args[0]} AS DATETIME)"

    if fn == "toyear" and len(args) == 1:
        return f"YEAR({args[0]})"

    if fn == "tomonth" and len(args) == 1:
        return f"MONTH({args[0]})"

    if fn == "toquarter" and len(args) == 1:
        return f"QUARTER({args[0]})"

    if fn == "todayofmonth" and len(args) == 1:
        return f"DAY({args[0]})"

    if fn == "todayofweek" and len(args) >= 1:
        return f"DAYOFWEEK_ISO({args[0]})"

    if fn == "toyyyymm" and len(args) == 1:
        return f"CAST(DATE_FORMAT({args[0]}, '%Y%m') AS INT)"

    if fn == "tostartofmonth" and len(args) == 1:
        return f"CAST(DATE_TRUNC('month', {args[0]}) AS DATE)"

    if fn == "tostartofquarter" and len(args) == 1:
        return f"CAST(DATE_TRUNC('quarter', {args[0]}) AS DATE)"

    if fn == "tostartofyear" and len(args) == 1:
        return f"CAST(DATE_TRUNC('year', {args[0]}) AS DATE)"

    if fn in ("tostartofweek", "tomonday") and len(args) >= 1:
        return f"CAST(DATE_TRUNC('week', {args[0]}) AS DATE)"

    if fn == "formatdatetime" and len(args) >= 2:
        fmt_tok = args[1].strip()
        raw_fmt = _unquote_lit(fmt_tok, literals)
        if raw_fmt is not None and "%H:%M" in raw_fmt:
            fixed_fmt = raw_fmt.replace("%H:%M", "%H:%i")
            idx = len(literals)
            literals.append(f"'{fixed_fmt}'")
            fmt_tok = f"__STRLIT_{idx}__"
        return f"DATE_FORMAT({args[0]}, {fmt_tok})"

    if fn == "datediff" and len(args) == 3:
        unit = (_unquote_lit(args[0], literals) or "day").strip().lower()
        start_d, end_d = args[1], args[2]
        if unit in ("day", "days", "d"):
            return f"DATEDIFF(CAST({end_d} AS DATE), CAST({start_d} AS DATE))"
        if unit in ("month", "months", "m"):
            return f"((YEAR({end_d}) - YEAR({start_d})) * 12 + (MONTH({end_d}) - MONTH({start_d})))"
        if unit in ("year", "years", "y"):
            return f"(YEAR({end_d}) - YEAR({start_d}))"
        if unit in ("hour", "hours"):
            return f"TIMESTAMPDIFF(HOUR, {start_d}, {end_d})"
        if unit in ("minute", "minutes"):
            return f"TIMESTAMPDIFF(MINUTE, {start_d}, {end_d})"
        if unit in ("second", "seconds"):
            return f"TIMESTAMPDIFF(SECOND, {start_d}, {end_d})"
        return f"DATEDIFF(CAST({end_d} AS DATE), CAST({start_d} AS DATE))"

    if fn == "subtractdays" and len(args) == 2:
        return f"DATE_SUB({args[0]}, INTERVAL {args[1]} DAY)"

    if fn == "adddays" and len(args) == 2:
        return f"DATE_ADD({args[0]}, INTERVAL {args[1]} DAY)"

    if fn == "subtractmonths" and len(args) == 2:
        return f"DATE_SUB({args[0]}, INTERVAL {args[1]} MONTH)"

    if fn == "addmonths" and len(args) == 2:
        return f"DATE_ADD({args[0]}, INTERVAL {args[1]} MONTH)"

    if fn == "today" and len(args) == 0:
        return "CURDATE()"

    if fn in ("parsedatetimebesteffort", "parsedatetimebesteffortornull") and len(args) >= 1:
        x = args[0].strip()
        idx_f1 = len(literals); literals.append("'%b %d %Y %H:%i'")
        if "(" in x:
            # Avoid repeating complex inner expression (e.g. REGEXP_REPLACE) inside aggregate CSE in StarRocks
            return f"STR_TO_DATE(TRIM({x}), __STRLIT_{idx_f1}__)"
        idx_f2 = len(literals); literals.append("'%d-%m-%Y'")
        idx_f3 = len(literals); literals.append("'%Y-%m-%d'")
        return (
            f"COALESCE("
            f"CAST({x} AS DATETIME), "
            f"STR_TO_DATE(TRIM({x}), __STRLIT_{idx_f1}__), "
            f"STR_TO_DATE(TRIM({x}), __STRLIT_{idx_f2}__), "
            f"STR_TO_DATE(TRIM({x}), __STRLIT_{idx_f3}__)"
            f")"
        )

    # ── Type Casts & String / Regex Functions ────────────────────────────────
    if fn in ("tofloat64", "tofloat32") and len(args) == 1:
        return f"CAST({args[0]} AS DOUBLE)"

    if fn in ("tofloat64orzero", "tofloat32orzero") and len(args) == 1:
        idx_c = len(literals); literals.append("','")
        idx_e = len(literals); literals.append("''")
        return (
            f"COALESCE(CAST(NULLIF(TRIM(REPLACE(CAST({args[0]} AS STRING), "
            f"__STRLIT_{idx_c}__, __STRLIT_{idx_e}__)), __STRLIT_{idx_e}__) AS DOUBLE), 0.0)"
        )

    if fn in ("toint64", "toint32", "toint16", "toint8", "touint64", "touint32", "touint16", "touint8") and len(args) == 1:
        return f"CAST({args[0]} AS BIGINT)"

    if fn in ("toint64orzero", "toint32orzero") and len(args) == 1:
        idx_e = len(literals); literals.append("''")
        return f"COALESCE(CAST(NULLIF(TRIM(CAST({args[0]} AS STRING)), __STRLIT_{idx_e}__) AS BIGINT), 0)"

    if fn == "tostring" and len(args) == 1:
        return f"CAST({args[0]} AS STRING)"

    if fn == "extract" and len(args) == 2:
        pat_raw = _unquote_lit(args[1], literals) or ""
        grp = 1 if "(" in pat_raw else 0
        return f"REGEXP_EXTRACT({args[0]}, {args[1]}, {grp})"

    if fn == "replaceregexpall" and len(args) == 3:
        return f"REGEXP_REPLACE({args[0]}, {args[1]}, {args[2]})"

    if fn == "startswith" and len(args) == 2:
        return f"STARTS_WITH({args[0]}, {args[1]})"

    if fn == "endswith" and len(args) == 2:
        return f"ENDS_WITH({args[0]}, {args[1]})"

    if fn == "positioncaseinsensitive" and len(args) == 2:
        return f"LOCATE(LOWER({args[1]}), LOWER({args[0]}))"

    if fn == "currentdatabase" and len(args) == 0:
        return "DATABASE()"

    return None


def _translate_functions_recursive(sql: str, literals: List[str]) -> str:
    """
    Walk `sql`, find every `identifier(...)` function call, recursively translate
    its inner text first, and then apply `_rewrite_ch_function`.
    """
    out: List[str] = []
    i = 0
    n = len(sql)
    ident_re = re.compile(r"[a-zA-Z_][a-zA-Z0-9_]*")

    while i < n:
        m = ident_re.match(sql, i)
        if not m:
            out.append(sql[i])
            i += 1
            continue

        word = m.group(0)
        after_word = m.end()
        # Check if followed by optional whitespace + '('
        j = after_word
        while j < n and sql[j] in (" ", "\t"):
            j += 1

        if j < n and sql[j] == "(":
            # Find matching ')'
            depth = 1
            k = j + 1
            while k < n and depth > 0:
                if sql[k] == "(":
                    depth += 1
                elif sql[k] == ")":
                    depth -= 1
                k += 1
            if depth == 0:
                inner_raw = sql[j + 1 : k - 1]
                # Prev non-whitespace char before `word`
                p = i - 1
                while p >= 0 and sql[p].isspace():
                    p -= 1
                prev_ch = sql[p] if p >= 0 else ""

                # Handle ClickHouse curried topK(N)(col)[1] -> ANY_VALUE(col)
                if word.lower() == "topk":
                    rest = sql[k:]
                    m_curried = re.match(r"^\s*\(([^()]+)\)\s*(?:\[\s*\d+\s*\])?", rest)
                    if m_curried:
                        col_translated = _translate_functions_recursive(m_curried.group(1), literals)
                        out.append(f"ANY_VALUE({col_translated.strip()})")
                        i = k + m_curried.end()
                        continue

                # Handle splitByChar(sep, str)[idx] -> SPLIT_PART(str, sep, idx)
                if word.lower() == "splitbychar":
                    rest = sql[k:]
                    m_idx = re.match(r"^\s*\[\s*(\d+)\s*\]", rest)
                    inner_tr = _translate_functions_recursive(inner_raw, literals)
                    s_args = _split_top_level_args(inner_tr)
                    if m_idx and len(s_args) == 2:
                        out.append(f"SPLIT_PART({s_args[1]}, {s_args[0]}, {m_idx.group(1)})")
                        i = k + m_idx.end()
                        continue

                inner_translated = _translate_functions_recursive(inner_raw, literals)
                # Don't treat SQL keywords like IN (...), OVER (...), VALUES (...) as CH functions
                args = _split_top_level_args(inner_translated)
                rewritten = _rewrite_ch_function(word, prev_ch, args, inner_translated, literals)
                if rewritten is not None:
                    out.append(rewritten)
                else:
                    spacing = sql[after_word:j]
                    out.append(f"{word}{spacing}({inner_translated})")
                i = k
                continue

        out.append(word)
        i = after_word

    return "".join(out)


def _rewrite_array_join(sql: str) -> str:
    """
    Convert ClickHouse `ARRAY JOIN [a1, a2, ...] AS col1, [b1, b2, ...] AS col2`
    into StarRocks `CROSS JOIN (SELECT a1 AS col1, b1 AS col2 UNION ALL ...) AS _sr_aj`.
    """
    aj_pat = re.compile(
        r"\bARRAY\s+JOIN\s+\[(.*?)\]\s+AS\s+([a-zA-Z_][a-zA-Z0-9_]*)"
        r"(?:\s*,\s*\[(.*?)\]\s+AS\s+([a-zA-Z_][a-zA-Z0-9_]*))?",
        re.IGNORECASE | re.DOTALL,
    )

    def _repl(m: re.Match) -> str:
        arr1 = _split_top_level_args(m.group(1))
        col1 = m.group(2)
        if m.group(3) is not None:
            arr2 = _split_top_level_args(m.group(3))
            col2 = m.group(4)
            selects = [
                f"SELECT {v1} AS {col1}, {v2} AS {col2}"
                for v1, v2 in zip(arr1, arr2)
            ]
        else:
            selects = [f"SELECT {v1} AS {col1}" for v1 in arr1]
        union_sql = " UNION ALL ".join(selects)
        return f"CROSS JOIN ({union_sql}) AS _sr_aj"

    return aj_pat.sub(_repl, sql)


def _add_missing_derived_table_aliases(sql: str) -> str:
    """
    StarRocks requires every derived subquery in `FROM (SELECT ...)` or `JOIN (SELECT ...)`
    to have an alias (Error 1248). ClickHouse allows unaliased derived subqueries.
    """
    pat = re.compile(r"\b(?:FROM|JOIN)\s*\(", re.IGNORECASE)
    alias_counter = 0
    pos = 0
    while True:
        m = pat.search(sql, pos)
        if not m:
            break
        open_paren = m.end() - 1
        # Check if inside starts with SELECT or WITH (skipping any amount of leading whitespace/newlines)
        inner_probe = sql[open_paren + 1 :].lstrip()[:12].upper()
        if not (inner_probe.startswith("SELECT") or inner_probe.startswith("WITH")):
            pos = m.end()
            continue

        # Walk balanced parens from open_paren
        depth = 1
        k = open_paren + 1
        n = len(sql)
        while k < n and depth > 0:
            if sql[k] == "(":
                depth += 1
            elif sql[k] == ")":
                depth -= 1
            k += 1

        if depth != 0:
            break

        # Inspect token right after closing ')' at index k
        rest = sql[k:].lstrip()
        needs_alias = False
        if not rest or rest[0] in (")", ";", ","):
            needs_alias = True
        else:
            mw = re.match(r"^([a-zA-Z_][a-zA-Z0-9_]*)", rest)
            if mw and mw.group(1).upper() in _SQL_CLAUSE_STOP_WORDS:
                needs_alias = True

        if needs_alias:
            alias = f" AS _sr_dt{alias_counter}"
            alias_counter += 1
            sql = sql[:k] + alias + sql[k:]

        # Advance pos just past `open_paren + 1` so nested `FROM (SELECT ...)` inside are also checked!
        pos = open_paren + 1

    return sql


def _expand_computed_select_aliases_in_where(sql: str) -> str:
    """
    1. ClickHouse permits referencing a computed `SELECT expr AS alias` inside the same
       block's `WHERE` clause (e.g. `UPPER(REGEXP_EXTRACT(...)) AS pf ... WHERE pf IN (...)`).
    2. ClickHouse prefers `SELECT expr AS alias` in `GROUP BY alias` even when joined tables
       both contain a column named `alias` (e.g. `s.branch` and `i.branch` -> `GROUP BY branch`),
       whereas StarRocks raises `Column 'branch' is ambiguous`.
    Expands non-aggregate SELECT aliases in same-level `WHERE` and `GROUP BY` clauses.
    """
    def _process_block(block: str) -> str:
        # Recursively process parenthesized subblocks first
        out: List[str] = []
        i = 0
        n = len(block)
        while i < n:
            if block[i] == "(":
                depth = 1
                k = i + 1
                while k < n and depth > 0:
                    if block[k] == "(":
                        depth += 1
                    elif block[k] == ")":
                        depth -= 1
                    k += 1
                inner = _process_block(block[i + 1 : k - 1])
                out.append(f"({inner})")
                i = k
            else:
                out.append(block[i])
                i += 1
        b = "".join(out)

        # Find top-level keyword positions
        kw_positions = []
        depth = 0
        for m in re.finditer(r"[()]|\b(SELECT|FROM|WHERE|GROUP|ORDER|HAVING|LIMIT|UNION)\b", b, re.IGNORECASE):
            tok = m.group(0)
            if tok == "(":
                depth += 1
            elif tok == ")":
                depth -= 1
            elif depth == 0:
                kw_positions.append((tok.upper(), m.start(), m.end()))

        idx = 0
        edits = []
        while idx < len(kw_positions):
            if kw_positions[idx][0] == "SELECT":
                sel_end = kw_positions[idx][2]
                from_kw = None
                where_kw = None
                where_stop = None
                group_kw = None
                group_stop = None

                for j in range(idx + 1, len(kw_positions)):
                    kname, ks, ke = kw_positions[j]
                    if kname in ("UNION", "SELECT"):
                        if where_kw and where_stop is None:
                            where_stop = ks
                        if group_kw and group_stop is None:
                            group_stop = ks
                        break
                    if kname == "FROM" and from_kw is None:
                        from_kw = (ks, ke)
                    elif kname == "WHERE" and from_kw is not None and where_kw is None:
                        where_kw = (ks, ke)
                    elif kname == "GROUP" and from_kw is not None and group_kw is None:
                        if where_kw and where_stop is None:
                            where_stop = ks
                        # Advance past `BY` right after `GROUP`
                        m_by = re.match(r"^\s+BY\b", b[ke:], re.IGNORECASE)
                        if m_by:
                            group_kw = (ks, ke + m_by.end())
                    elif kname in ("ORDER", "HAVING", "LIMIT"):
                        if where_kw and where_stop is None:
                            where_stop = ks
                        if group_kw and group_stop is None:
                            group_stop = ks

                if where_kw and where_stop is None:
                    where_stop = len(b)
                if group_kw and group_stop is None:
                    group_stop = len(b)

                if from_kw and (where_kw or group_kw):
                    sel_str = b[sel_end : from_kw[0]]
                    where_aliases = {}
                    group_aliases = {}
                    for item in _split_top_level_args(sel_str):
                        ma = re.match(r"^(.*?)\s+AS\s+([a-zA-Z_][a-zA-Z0-9_]*)\s*$", item, re.IGNORECASE | re.DOTALL)
                        if ma:
                            expr_part = ma.group(1).strip()
                            al_name = ma.group(2).strip()
                            expr_up = expr_part.upper()
                            if not any(ag in expr_up for ag in _AGG_MARKERS):
                                if "(" in expr_part:
                                    where_aliases[al_name] = expr_part
                                if "(" in expr_part or "." in expr_part:
                                    group_aliases[al_name] = expr_part

                    if where_kw and where_aliases:
                        where_str = b[where_kw[1] : where_stop]
                        new_where = where_str
                        for al_name, expr_part in where_aliases.items():
                            new_where = re.sub(
                                rf"(?<![.\w])\b{re.escape(al_name)}\b",
                                f"({expr_part})",
                                new_where,
                            )
                        if new_where != where_str:
                            edits.append((where_kw[1], where_stop, new_where))

                    if group_kw and group_aliases:
                        grp_str = b[group_kw[1] : group_stop]
                        grp_items = _split_top_level_args(grp_str)
                        changed_grp = False
                        new_grp_items = []
                        for g_it in grp_items:
                            g_clean = g_it.strip()
                            if g_clean in group_aliases:
                                new_grp_items.append(group_aliases[g_clean])
                                changed_grp = True
                            else:
                                new_grp_items.append(g_it)
                        if changed_grp:
                            edits.append((group_kw[1], group_stop, " " + ", ".join(new_grp_items) + " "))
            idx += 1

        # Apply edits from right to left so offsets stay valid
        for ws, we, nw in sorted(edits, key=lambda x: x[0], reverse=True):
            b = b[:ws] + nw + b[we:]
        return b

    return _process_block(sql)


def _rewrite_ilike_and_date_sub(sql: str) -> str:
    """
    1. Convert `<ident_or_expr> [NOT] ILIKE <pat>` -> `LOWER(...) [NOT] LIKE LOWER(<pat>)`.
    2. Convert `CURDATE() - CAST(<expr> AS DATE)` -> `DATEDIFF(CURDATE(), CAST(<expr> AS DATE))`.
    3. Strip ClickHouse trailing `SETTINGS ...` and table `FINAL` modifier.
    """
    # Strip trailing SETTINGS ...
    sql = re.sub(r"\bSETTINGS\s+[a-zA-Z_][a-zA-Z0-9_]*\s*=.*$", "", sql, flags=re.IGNORECASE | re.DOTALL)

    # ILIKE / NOT ILIKE on column or parenthesized expr
    sql = re.sub(
        r"([a-zA-Z_][a-zA-Z0-9_.]*)\s+NOT\s+ILIKE\s+(__STRLIT_\d+__|[a-zA-Z_][a-zA-Z0-9_.]*)",
        r"LOWER(\1) NOT LIKE LOWER(\2)",
        sql,
        flags=re.IGNORECASE,
    )
    sql = re.sub(
        r"([a-zA-Z_][a-zA-Z0-9_.]*)\s+ILIKE\s+(__STRLIT_\d+__|[a-zA-Z_][a-zA-Z0-9_.]*)",
        r"LOWER(\1) LIKE LOWER(\2)",
        sql,
        flags=re.IGNORECASE,
    )

    # ClickHouse `today() - toDate(max(date))` -> already translated functions to `CURDATE() - CAST(max(date) AS DATE)`
    sql = re.sub(
        r"\bCURDATE\(\)\s*-\s*(CAST\([^()]+(?:\([^()]*\)[^()]*)*\s+AS\s+DATE\))",
        r"DATEDIFF(CURDATE(), \1)",
        sql,
        flags=re.IGNORECASE,
    )

    # ClickHouse `LIMIT N BY col1, col2` -> ROW_NUMBER() window wrapper
    m_limby = re.search(
        r"\bORDER\s+BY\s+(.+?)\s+LIMIT\s+(\d+)\s+BY\s+([a-zA-Z0-9_.,\s]+)\s*$",
        sql,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if m_limby:
        ord_by = m_limby.group(1).strip()
        lim_n = m_limby.group(2).strip()
        part_by = m_limby.group(3).strip()
        base_q = sql[: m_limby.start()].strip()
        sql = (
            f"SELECT * FROM ("
            f"SELECT _sr_lb.*, ROW_NUMBER() OVER (PARTITION BY {part_by} ORDER BY {ord_by}) AS _sr_rn "
            f"FROM ({base_q}) AS _sr_lb"
            f") AS _sr_lb_out WHERE _sr_rn <= {lim_n}"
        )

    return sql


def translate_ch_to_sr_sql(sql: str, params: Optional[Dict[str, Any]] = None) -> str:
    """Translate a ClickHouse SQL query + parameters dict into valid StarRocks 4.1 SQL."""
    # Step 1: Bind ClickHouse `{param:Type}` & `has({arr:Array(...)}, expr)`
    bound_sql = _bind_ch_parameters(sql, params)

    # Step 2: Strip comments & protect string literals
    clean_sql, literals = _strip_comments_and_protect_strings(bound_sql)

    # Step 3: Rewrite ARRAY JOIN before function translation
    clean_sql = _rewrite_array_join(clean_sql)

    # Step 4: Recursively translate all ClickHouse functions to StarRocks functions
    translated = _translate_functions_recursive(clean_sql, literals)

    # Step 5: Rewrite ILIKE, date subtraction, SETTINGS, LIMIT N BY
    translated = _rewrite_ilike_and_date_sub(translated)

    # Step 6: Expand computed SELECT aliases referenced in same-level WHERE
    translated = _expand_computed_select_aliases_in_where(translated)

    # Step 7: Add aliases to unaliased derived subqueries (`FROM (SELECT ...)`)
    translated = _add_missing_derived_table_aliases(translated)

    # Step 8: Restore protected string literals
    def _restore_lit(m: re.Match) -> str:
        return literals[int(m.group(1))]

    final_sql = re.sub(r"__STRLIT_(\d+)__", _restore_lit, translated)
    return final_sql.strip()


# ═════════════════════════════════════════════════════════════════════════════
# Client Adapter Matching `clickhouse_connect` Interface
# ═════════════════════════════════════════════════════════════════════════════

def _normalize_cell(v: Any) -> Any:
    """Convert Decimal to float so downstream arithmetic with floats matches ClickHouse."""
    if isinstance(v, decimal.Decimal):
        return float(v)
    return v


class StarRocksQueryResult:
    """Drop-in replacement for `clickhouse_connect.driver.query.QueryResult`."""

    def __init__(self, result_rows: List[Tuple[Any, ...]], column_names: List[str]):
        self.result_rows = result_rows
        self.column_names = column_names

    @property
    def first_row(self) -> Tuple[Any, ...]:
        return self.result_rows[0] if self.result_rows else ()

    @property
    def row_count(self) -> int:
        return len(self.result_rows)


class StarRocksClientAdapter:
    """
    Mimics `clickhouse_connect.driver.Client` (`.query()`, `.query_df()`, `.command()`)
    executing translated queries on StarRocks `Loyalty_Analytics` and recording
    high-resolution SQL execution timings.
    """

    def query(self, query: str, parameters: Optional[Dict[str, Any]] = None, **kwargs) -> StarRocksQueryResult:
        from analytics.clickhouse_service import record_query_metric

        sr_sql = translate_ch_to_sr_sql(query, parameters)
        t0 = time.perf_counter()
        try:
            conn = get_raw_sr_connection()
            with conn.cursor() as cur:
                cur.execute(sr_sql)
                raw_rows = cur.fetchall()
                col_names = [d[0] for d in (cur.description or [])]
        except pymysql.err.OperationalError:
            # Reconnect once if connection dropped
            reset_sr_connection()
            conn = get_raw_sr_connection()
            with conn.cursor() as cur:
                cur.execute(sr_sql)
                raw_rows = cur.fetchall()
                col_names = [d[0] for d in (cur.description or [])]
        except Exception as e:
            elapsed_ms = (time.perf_counter() - t0) * 1000.0
            record_query_metric("starrocks", sr_sql, elapsed_ms, 0, error=str(e))
            logger.error(f"[StarRocks] Query failed ({elapsed_ms:.1f} ms): {e}\nTranslated SQL:\n{sr_sql}")
            raise

        elapsed_ms = (time.perf_counter() - t0) * 1000.0

        # Strip synthetic `_sr_rn` column if LIMIT N BY rewrite was used
        if col_names and col_names[-1] == "_sr_rn":
            col_names = col_names[:-1]
            rows = [tuple(_normalize_cell(c) for c in r[:-1]) for r in raw_rows]
        else:
            rows = [tuple(_normalize_cell(c) for c in r) for r in raw_rows]

        record_query_metric("starrocks", sr_sql, elapsed_ms, len(rows))
        return StarRocksQueryResult(rows, col_names)

    def query_df(self, query: str, parameters: Optional[Dict[str, Any]] = None, **kwargs) -> pd.DataFrame:
        res = self.query(query, parameters=parameters, **kwargs)
        if not res.result_rows:
            return pd.DataFrame(columns=res.column_names)
        return pd.DataFrame(res.result_rows, columns=res.column_names)

    def command(self, cmd: str, parameters: Optional[Dict[str, Any]] = None, **kwargs) -> Any:
        from analytics.clickhouse_service import record_query_metric

        sr_sql = translate_ch_to_sr_sql(cmd, parameters)
        t0 = time.perf_counter()
        conn = get_raw_sr_connection()
        with conn.cursor() as cur:
            cur.execute(sr_sql)
            res = cur.fetchone()
        elapsed_ms = (time.perf_counter() - t0) * 1000.0
        record_query_metric("starrocks", sr_sql, elapsed_ms, 1 if res else 0)
        return res[0] if res else None


_sr_adapter_singleton = StarRocksClientAdapter()


def get_sr_client() -> StarRocksClientAdapter:
    """Return the thread-safe StarRocksClientAdapter instance."""
    return _sr_adapter_singleton

