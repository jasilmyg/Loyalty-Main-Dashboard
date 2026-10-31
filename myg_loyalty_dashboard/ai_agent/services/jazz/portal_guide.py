"""
Jazz AI – portal knowledge
==========================
Lets Jazz understand every page in the portal's left sidebar:

* `index_text()`     – compact page → purpose → tables map that goes into the system prompt.
* `page_info(page)`  – on-demand detail for ONE page (the tool `portal_page_info`): the headings, filters and column
                       headers visible on the page, plus the SQL that the page's own API runs, so Jazz can reproduce or
                       explain the exact numbers the user sees on that page.

`portal_knowledge.json` (page list, URLs, API endpoints, tables) is generated from the code by
scratch/gen_portal_knowledge.py – re-run it when sidebar pages are added.
"""

import inspect
import json
import logging
import os
import re
import sys
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_HERE = os.path.dirname(os.path.abspath(__file__))
_JSON = os.path.join(os.path.dirname(_HERE), "portal_knowledge.json")
_TEMPLATES = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(_HERE))), "templates")
_MODULES = ("dashboard.views", "analytics.views", "market_basket.views", "ai_agent.views")
_cache: Dict[str, Any] = {}

# What each page is for (from its sidebar name) – a starting point; `portal_page_info` gives the real logic/SQL.
PURPOSE = {
    "dashboard": "Sales Overview – headline sales, bills, customers and trends across branches and time.",
    "customers": "Customer Analytics – customer base, new vs repeat, frequency and value.",
    "retail_analytics": "Retail Loyalty Report – loyalty-programme performance of the retail branches.",
    "loyalty_gap": "Loyalty & Gap Analysis – gap between loyalty members and total billing customers.",
    "rfm": "RFM Segmentation – customers segmented by recency, frequency, monetary value.",
    "cohorts": "Cohort Retention – retention of customers grouped by first-purchase period.",
    "campaign_analysis": "Campaign Analysis – results of marketing / loyalty campaigns.",
    "daily_new_repeat": "Daily New vs Repeat – day-wise new versus repeat customers.",
    "redemption_analysis": "Redemption Analysis – loyalty-point redemption behaviour.",
    "osg_sale_report": "OSG Warranty Report – OSG extended-warranty sales and attach rate.",
    "target_executive": "Target Analysis Dashboard – targets versus achievement.",
    "customer_propensity": "Customer Intelligence AI – propensity / likelihood scoring of customers.",
    "smart_next_visit": "Next-Visit Predictor – when customers are likely to visit again.",
    "ai_intelligence": "AI Intelligence Engine – AI-generated business intelligence.",
    "enterprise_dashboard": "Executive Dashboard / Retail Performance Analytics – category, brand, product and branch comparison between two periods (e.g. JAS 2025 vs JAS 2026) with growth % and share.",
    "special_event_reports": "Special Event Reports – sales during special events / festivals / campaigns.",
    "azure_analytics": "Ultimate Azure Analytics – detailed analytics on the Azure sales and invoice reports.",
    "stock_ageing_report": "Stock Ageing Report – product-wise stock value and ageing buckets from the latest stock snapshot.",
    "stock_ageing_test": "Stock Ageing Test – test version of the stock ageing report.",
    "clickhouse_assistant": "Jazz AI – this chat assistant.",
    "future_sale_analysis": "Future Sale Analysis – forecast of future sales.",
    "rca_dashboard": "Customer Review RCA – root-cause analysis of Google customer reviews.",
    "store_analysis_upload": "Store Excel Analysis – analysis of an uploaded store Excel file.",
    "category_analysis": "Category Analysis – sales by product category.",
    "product_penetration": "Product Penetration – how widely each product is bought across customers / bills.",
    "mobile_ce_crosssell": "Mobile CE Reports – cross-sell of consumer-electronics with mobile purchases.",
    "market_basket_dashboard": "Market Basket Analysis – items bought together (association rules, basket KPIs).",
    "she_start": "SHE START Dashboard – SHE START programme performance.",
    "she_start_detailed": "SHE START Detailed Report – detailed SHE START programme report.",
    "db_manager": "DB Manager – database administration page (not an analytics page).",
}


def _load() -> List[Dict[str, Any]]:
    if "pages" not in _cache:
        try:
            with open(_JSON, encoding="utf-8") as f:
                _cache["pages"] = json.load(f).get("pages", [])
        except Exception as e:  # noqa: BLE001
            logger.warning("portal_knowledge.json not loaded: %s", e)
            _cache["pages"] = []
    return _cache["pages"]


def index_text() -> str:
    pages = _load()
    if not pages:
        return ""
    lines = []
    for p in pages:
        tbl = ", ".join(p.get("tables") or []) or "-"
        lines.append(f"  - {p['label']} (/{p['path'].strip('/')}) – {PURPOSE.get(p['url_name'], p['label'])} Tables: {tbl}")
    return (
        "\n# Portal pages (left sidebar) – the user's questions often refer to these pages\n"
        "When the user mentions a page / report / dashboard by name, or asks to match what a page shows, call `portal_page_info` for that page FIRST: "
        "it returns the page's filters, column headers and the exact SQL its API runs, so your numbers match the page. "
        "The table lists below are auto-detected hints - confirm with the schema.\n" + "\n".join(lines) + "\n"
    )


def _norm(s: str) -> str:
    toks = [t[:-1] if len(t) > 3 and t.endswith("s") else t for t in re.sub(r"[^a-z0-9]+", " ", str(s).lower()).split()]
    return " ".join(toks)


def _find(page: str) -> Optional[Dict[str, Any]]:
    q = _norm(page)
    if not q:
        return None
    best, best_score = None, 0
    for p in _load():
        hay = [_norm(x) for x in (p["label"], p["url_name"], p["path"])]
        score = 0
        for h in hay:
            if q == h:
                score = max(score, 100)
            elif h and (q in h or h in q):
                score = max(score, 60 + min(len(q), len(h)))
            else:
                common = (set(q.split()) & set(h.split())) - {"report", "analysi", "dashboard"}
                score = max(score, 20 * len(common))
        if score > best_score:
            best, best_score = p, score
    return best if best_score >= 20 else None


def _template_facts(tpl: str) -> Dict[str, List[str]]:
    path = os.path.join(_TEMPLATES, tpl)
    if not tpl or not os.path.exists(path):
        return {}
    html = open(path, encoding="utf-8", errors="ignore").read()
    html = re.sub(r"<script.*?</script>|<style.*?</style>", " ", html, flags=re.S | re.I)

    def grab(pattern: str, limit: int) -> List[str]:
        out: List[str] = []
        for m in re.finditer(pattern, html, re.S | re.I):
            t = re.sub(r"\s+", " ", re.sub(r"<[^>]+>|\{[%{].*?[%}]\}", " ", m.group(1))).strip()
            if 1 < len(t) < 70 and t not in out:
                out.append(t)
            if len(out) >= limit:
                break
        return out

    return {
        "headings": grab(r"<h[1-4][^>]*>(.*?)</h[1-4]>", 25),
        "filters": grab(r"<label[^>]*>(.*?)</label>", 25),
        "columns": grab(r"<th[^>]*>(.*?)</th>", 40),
        "tabs_buttons": grab(r"<button[^>]*>(.*?)</button>", 20),
    }


def _resolve(name: str):
    for mod in _MODULES:
        m = sys.modules.get(mod)
        if m is None:
            try:
                __import__(mod)
                m = sys.modules.get(mod)
            except Exception:  # noqa: BLE001
                continue
        obj = getattr(m, name, None)
        if obj is not None:
            return obj, m
    return None, None


def _sql_snippets(class_names: List[str], budget: int) -> str:
    seen, chunks, used = set(), [], 0
    for cn in class_names:
        obj, mod = _resolve(cn)
        if obj is None:
            continue
        try:
            src = inspect.getsource(obj)
        except Exception:  # noqa: BLE001
            continue
        texts = [(cn, src)]
        queue = [(src, 0)]
        names_done = set()
        while queue and len(texts) < 14:
            text, depth = queue.pop(0)
            cands = []
            for callee in set(re.findall(r"(?<![\w.])([A-Za-z_]\w+)\s*\(", text)):
                fn = getattr(mod, callee, None)
                if inspect.isfunction(fn) or (inspect.isclass(fn) and callee.endswith(("Service", "Logic", "Engine"))):
                    cands.append((callee, fn))
            for owner, callee in set(re.findall(r"\b([A-Za-z_]\w*)\.([A-Za-z_]\w+)\s*\(", text)):
                ow = getattr(mod, owner, None)
                if inspect.ismodule(ow) or inspect.isclass(ow):
                    cands.append((f"{owner}.{callee}", getattr(ow, callee, None)))
            for label, fn in cands:
                if label in names_done or not callable(fn) or (getattr(fn, "__module__", None) or "").split(".")[0] not in ("dashboard", "analytics", "market_basket"):
                    continue
                names_done.add(label)
                try:
                    fsrc = inspect.getsource(fn)
                    if len(fsrc) > 40000:
                        continue
                except Exception:  # noqa: BLE001
                    continue
                texts.append((label, fsrc))
                if depth < 1:
                    queue.append((fsrc, depth + 1))
        for label, text in texts:
            if label in seen:
                continue
            seen.add(label)
            for m in re.finditer(r"(?:f?\"\"\"|f?''')(.*?)(?:\"\"\"|''')", text, re.S):
                s = m.group(1)
                if re.search(r"\bSELECT\b", s, re.I) and re.search(r"\bFROM\b", s, re.I):
                    s = re.sub(r"\s+\n", "\n", re.sub(r"[ \t]+", " ", s)).strip()
                    if used + len(s) > budget:
                        return "\n\n".join(chunks)
                    chunks.append(f"-- in {label}\n{s}")
                    used += len(s)
    return "\n\n".join(chunks)


def page_info(page: str, include_sql: bool = True) -> Dict[str, Any]:
    p = _find(page)
    if not p:
        return {"error": f"No sidebar page matches '{page}'. Pages: " + ", ".join(x["label"] for x in _load())}
    out: Dict[str, Any] = {
        "page": p["label"],
        "url": p["path"],
        "purpose": PURPOSE.get(p["url_name"], p["label"]),
        "tables_detected": p.get("tables"),
        "api_endpoints": p.get("apis"),
        "on_page": _template_facts(p.get("template", "")),
    }
    if include_sql:
        try:
            sql = _sql_snippets([c for c in [p.get("view")] + list(p.get("api_views") or []) if c], budget=6500)
        except Exception as e:  # noqa: BLE001
            logger.warning("page sql extraction failed for %s: %s", p.get("label"), e)
            sql = ""
        if sql:
            out["page_sql"] = sql
        else:
            code = ""
            for cn in [c for c in list(p.get("api_views") or []) + [p.get("view")] if c]:
                obj, _m = _resolve(cn)
                try:
                    code += f"\n# {cn}\n" + inspect.getsource(obj) if obj is not None else ""
                except Exception:  # noqa: BLE001
                    pass
                if len(code) > 5500:
                    break
            out["page_logic_python"] = code[:5500] or "(logic not found; inspect the tables above)"
    out["note"] = ("Use this to reproduce the page's numbers with run_sql (same filters, same exclusions). "
                   "If the page SQL differs from your first idea, follow the page.")
    return out
