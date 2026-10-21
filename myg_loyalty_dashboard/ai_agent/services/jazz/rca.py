"""
Root-cause analysis engine for Jazz AI.

`explain_change()` investigates WHY a metric moved between two periods. It runs a handful of deterministic
ClickHouse queries (no LLM involved) and decomposes the change by branch, district, product, brand, category,
salesperson and time period. It returns:

  • main drivers per dimension with their contribution to the total change,
  • offsetting movers (things that moved the other way),
  • price-vs-volume decomposition (sales metric),
  • data-supported hypotheses ("possible explanations") with the evidence behind each,
  • a reconciliation check proving that the driver tables add up to the headline change,
  • data-completeness warnings (e.g. current period extends past the latest loaded date).

The LLM only has to narrate the result – the numbers come from here.
"""

import logging
from datetime import date, timedelta
from typing import Any, Dict, List, Optional

import pandas as pd

from . import common as C

logger = logging.getLogger(__name__)

METRICS = {
    "sales": {"label": "Sales value", "unit": "₹"},
    "units": {"label": "Units sold", "unit": "units"},
    "invoices": {"label": "Invoice count", "unit": "invoices"},
}
ALL_DIMENSIONS = ["branch", "district", "product", "brand", "category", "salesperson", "week"]
# invoices are only additive across dimensions that partition an invoice (branch / salesperson / week)
INVOICE_DIMENSIONS = ["branch", "district", "salesperson", "week"]
DIM_LABELS = {
    "branch": "Branch", "district": "District", "rbm": "RBM", "product": "Product", "brand": "Brand",
    "category": "Category", "salesperson": "Salesperson", "week": "Time period",
}
MIN_ABS = 1e-9


# ── period handling ───────────────────────────────────────────────────────────
def resolve_periods(
    current_start: Any, current_end: Any, base_start: Any, base_end: Any, compare_mode: str
) -> Dict[str, Any]:
    warnings: List[str] = []
    latest = C.latest_sales_date()
    ce = C.parse_date(current_end, latest)
    cs = C.parse_date(current_start, ce.replace(day=1))
    if cs > ce:
        raise ValueError("current_start is after current_end.")
    if ce > latest:
        warnings.append(
            f"Data is only available up to {latest:%d %b %Y}; the current period was cut from "
            f"{ce:%d %b %Y} to {latest:%d %b %Y} so that no incomplete days are compared."
        )
        ce = latest
        if cs > ce:
            raise ValueError(f"No data is loaded after {latest:%d %b %Y}.")
    n = (ce - cs).days + 1

    mode = (compare_mode or "previous_period").lower()
    bs_given, be_given = C.parse_date(base_start), C.parse_date(base_end)
    if bs_given and be_given:
        bs, be, mode = bs_given, be_given, "custom"
    elif mode == "same_period_last_month":
        bs, be = C.shift_months(cs, -1), C.shift_months(ce, -1)
    elif mode == "same_period_last_year":
        bs, be = C.shift_months(cs, -12), C.shift_months(ce, -12)
    else:
        mode = "previous_period"
        be = cs - timedelta(days=1)
        bs = be - timedelta(days=n - 1)
    if bs > be:
        raise ValueError("base_start is after base_end.")
    if be >= cs:
        raise ValueError("The comparison (base) period overlaps the current period.")
    nb = (be - bs).days + 1
    if nb != n:
        warnings.append(
            f"The periods have different lengths ({n} vs {nb} days) – compare the per-day averages as well as the totals."
        )
    return {"cs": cs, "ce": ce, "bs": bs, "be": be, "n": n, "nb": nb, "mode": mode, "warnings": warnings, "latest": latest}


# ── SQL builders ──────────────────────────────────────────────────────────────
def _base_sql(per: Dict[str, Any], branch_codes: Optional[List[str]], filters: Dict[str, Any]) -> str:
    """Sub-select of sales rows of both periods, tagged p=1 (current) / p=2 (base) and joined to item_master."""
    cs, ce, bs, be = per["cs"], per["ce"], per["bs"], per["be"]
    where_outer: List[str] = []
    prod = filters.get("product")
    if prod:
        parts = " OR ".join(f"positionCaseInsensitive(i.product, '{C.esc(p)}') > 0" for p in C._as_list(prod))
        where_outer.append(f"({parts})")
    brand = filters.get("brand")
    if brand:
        vals = ",".join(f"'{C.esc(b.upper())}'" for b in C._as_list(brand))
        where_outer.append(f"upper(i.brand) IN ({vals})")
    cat = filters.get("category")
    if cat:
        prods: List[str] = []
        for c in C._as_list(cat):
            prods.extend(C.products_in_category(c))
        if not prods:
            raise ValueError("Unknown category '%s'. Valid: %s" % (cat, ", ".join(C.valid_categories())))
        plist = ",".join(f"'{C.esc(p)}'" for p in prods)
        where_outer.append(f"i.product IN ({plist})")
    branch_clause = ""
    if branch_codes is not None:
        codes = ",".join(f"'{C.esc(c)}'" for c in branch_codes)
        branch_clause = f"AND branch IN ({codes})"
    outer = ("WHERE " + " AND ".join(where_outer)) if where_outer else ""
    return f"""(
  SELECT s.d AS d, s.p AS p, s.branch AS branch, s.invoice_no AS invoice_no, s.sold_price AS sold_price, s.qty AS qty,
         i.product AS product, i.brand AS brand
  FROM (
    SELECT toDate(date) AS d, branch, item_code, invoice_no, sold_price, qty,
           if(toDate(date) BETWEEN '{cs}' AND '{ce}', 1, 2) AS p
    FROM azure_sales_report
    WHERE (toDate(date) BETWEEN '{cs}' AND '{ce}' OR toDate(date) BETWEEN '{bs}' AND '{be}')
      AND {C.sales_exclusion()} {branch_clause}
  ) s
  LEFT JOIN item_master i ON s.item_code = i.item_code
  {outer}
)"""


def _fetch(per, branch_codes, filters, metric) -> Dict[str, Any]:
    """Pull the raw numbers for all dimensions. Returns DataFrames keyed by granularity."""
    base = _base_sql(per, branch_codes, filters)
    out: Dict[str, Any] = {}

    if metric in ("sales", "units"):
        out["bp"] = C.ch_df(f"""
            SELECT branch AS bcode, product, brand,
                   sumIf(sold_price, p = 1) AS vc, sumIf(sold_price, p = 2) AS vb,
                   sumIf(qty, p = 1) AS qc, sumIf(qty, p = 2) AS qb
            FROM {base} GROUP BY branch, product, brand""")
    else:
        out["bp"] = C.ch_df(f"""
            SELECT branch AS bcode, uniqExactIf(invoice_no, p = 1) AS ic, uniqExactIf(invoice_no, p = 2) AS ib
            FROM {base} GROUP BY branch""")

    out["daily"] = C.ch_df(f"""
        SELECT d, p, sum(sold_price) AS v, sum(qty) AS q, uniqExact(invoice_no) AS inv
        FROM {base} GROUP BY d, p ORDER BY p, d""")

    # salesperson = sales_staff_code of the invoice (azure_invoice_report)
    lo = min(per["cs"], per["bs"])
    hi = max(per["ce"], per["be"])
    val = {"sales": ("sumIf(sold_price, p = 1)", "sumIf(sold_price, p = 2)"),
           "units": ("sumIf(qty, p = 1)", "sumIf(qty, p = 2)"),
           "invoices": ("uniqExactIf(invoice_no, p = 1)", "uniqExactIf(invoice_no, p = 2)")}[metric]
    try:
        out["staff"] = C.ch_df(f"""
            SELECT if(inv.staff = '', '(unassigned)', inv.staff) AS who, {val[0]} AS cur, {val[1]} AS base
            FROM {base} AS b
            LEFT JOIN (
                SELECT invoice_no, any(sales_staff_code) AS staff FROM azure_invoice_report
                WHERE toDate(date) BETWEEN '{lo}' AND '{hi}' GROUP BY invoice_no
            ) inv ON b.invoice_no = inv.invoice_no
            GROUP BY who""")
    except Exception as e:  # noqa: BLE001
        logger.warning("RCA salesperson query failed: %s", e)
        out["staff"] = None
        out["staff_error"] = str(e)[:200]
    return out


# ── decomposition ─────────────────────────────────────────────────────────────
def _round(v: float, nd: int = 2) -> float:
    return round(float(v), nd)


def _drivers(g: pd.DataFrame, total_delta: float, top_n: int, label_col: Optional[str] = None) -> Dict[str, Any]:
    """g: index = member, columns cur/base. Returns drivers (same direction), offsets and an 'others' remainder."""
    g = g.copy()
    g["delta"] = g["cur"] - g["base"]
    direction = -1 if total_delta < 0 else 1
    same = g[(g["delta"] * direction) > MIN_ABS].sort_values("delta", ascending=(direction < 0))
    opp = g[(g["delta"] * direction) < -MIN_ABS].sort_values("delta", ascending=(direction > 0))

    def row(name, r):
        contrib = (r["delta"] / total_delta * 100) if abs(total_delta) > MIN_ABS else None
        change_pct = (r["delta"] / r["base"] * 100) if abs(r["base"]) > MIN_ABS else None
        item = {
            "name": str(name), "current": _round(r["cur"]), "base": _round(r["base"]), "delta": _round(r["delta"]),
            "contribution_pct": None if contrib is None else _round(contrib, 1),
            "change_pct": None if change_pct is None else _round(change_pct, 1),
        }
        if abs(r["base"]) <= MIN_ABS and r["cur"] > 0:
            item["note"] = "new (no activity in base period)"
        elif abs(r["cur"]) <= MIN_ABS and r["base"] > 0:
            item["note"] = "stopped (no activity in current period)"
        return item

    top = [row(n, r) for n, r in same.head(top_n).iterrows()]
    offs = [row(n, r) for n, r in opp.head(3).iterrows()]
    shown = same.head(top_n)["delta"].sum()
    others_delta = float(total_delta - shown)
    return {
        "drivers": top,
        "offsets": offs,
        "others_delta": _round(others_delta),
        "members": int(len(g)),
        "top_share_pct": None if abs(total_delta) <= MIN_ABS else _round(shown / total_delta * 100, 1),
        "sum_delta": _round(g["delta"].sum()),
    }


def _waterfall(base_total: float, cur_total: float, dr: Dict[str, Any]) -> List[Dict[str, Any]]:
    steps = [{"label": "Base period", "value": _round(base_total), "kind": "total"}]
    for d in dr["drivers"]:
        steps.append({"label": d["name"], "value": d["delta"], "kind": "delta"})
    if dr["offsets"] or abs(dr["others_delta"]) > MIN_ABS:
        steps.append({"label": "Others (net)", "value": dr["others_delta"], "kind": "delta"})
    steps.append({"label": "Current period", "value": _round(cur_total), "kind": "total"})
    return steps


def _price_volume(bp: pd.DataFrame) -> Optional[Dict[str, Any]]:
    """Per-product price-volume split of the sales-value change."""
    g = bp.groupby("product")[["vc", "vb", "qc", "qb"]].sum()
    if g.empty:
        return None
    pb = g["vb"] / g["qb"].where(g["qb"] != 0)
    vol = pd.Series(0.0, index=g.index)
    price = pd.Series(0.0, index=g.index)
    has_base = (g["qb"] > 0) & pb.notna()
    # products sold in both periods → volume at base price, price effect on current volume
    vol[has_base] = (g["qc"] - g["qb"])[has_base] * pb[has_base]
    price[has_base] = g["vc"][has_base] - g["qc"][has_base] * pb[has_base]
    # new products (no base units) → whole value is volume/mix; stopped products → lost value is volume
    new_mask = ~has_base
    vol[new_mask] = (g["vc"] - g["vb"])[new_mask]
    tot_delta = float((g["vc"] - g["vb"]).sum())
    asp_c = float(g["vc"].sum() / g["qc"].sum()) if g["qc"].sum() else None
    asp_b = float(g["vb"].sum() / g["qb"].sum()) if g["qb"].sum() else None
    worst_price = price.sort_values().head(3)
    return {
        "volume_effect": _round(vol.sum()),
        "price_effect": _round(price.sum()),
        "total_delta": _round(tot_delta),
        "volume_share_pct": None if abs(tot_delta) <= MIN_ABS else _round(vol.sum() / tot_delta * 100, 1),
        "price_share_pct": None if abs(tot_delta) <= MIN_ABS else _round(price.sum() / tot_delta * 100, 1),
        "avg_selling_price_current": None if asp_c is None else _round(asp_c),
        "avg_selling_price_base": None if asp_b is None else _round(asp_b),
        "biggest_price_drops": [{"product": str(p), "price_effect": _round(v)} for p, v in worst_price.items() if v < -MIN_ABS],
    }


def _weekly(daily: pd.DataFrame, per: Dict[str, Any], metric: str) -> pd.DataFrame:
    col = {"sales": "v", "units": "q", "invoices": "inv"}[metric]
    rows: Dict[str, Dict[str, float]] = {}
    for p, start, key in ((1, per["cs"], "cur"), (2, per["bs"], "base")):
        sub = daily[daily["p"] == p]
        for _, r in sub.iterrows():
            d = r["d"]
            idx = (d - start).days // 7 + 1
            rows.setdefault(idx, {"cur": 0.0, "base": 0.0})[key] += float(r[col])
    out = {}
    for idx, vals in sorted(rows.items()):
        c0 = per["cs"] + timedelta(days=(idx - 1) * 7)
        c1 = min(per["ce"], c0 + timedelta(days=6))
        b0 = per["bs"] + timedelta(days=(idx - 1) * 7)
        b1 = min(per["be"], b0 + timedelta(days=6))
        out[f"Week {idx}: {c0:%d %b}–{c1:%d %b} vs {b0:%d %b}–{b1:%d %b}"] = vals
    return pd.DataFrame.from_dict(out, orient="index") if out else pd.DataFrame(columns=["cur", "base"])


def _stock_cover(products: List[str], units_current: Dict[str, float], days: int) -> List[Dict[str, Any]]:
    """Latest stock snapshot vs current run-rate for the products that declined."""
    if not products:
        return []
    try:
        plist = ",".join(f"'{C.esc(p)}'" for p in products)
        df = C.ch_df(f"""
            SELECT product, sum(stock) AS stock FROM stock_ageing_report
            WHERE report_date = (SELECT max(report_date) FROM stock_ageing_report) AND product IN ({plist})
            GROUP BY product""")
    except Exception as e:  # noqa: BLE001
        logger.info("RCA stock cover unavailable: %s", e)
        return []
    out = []
    for _, r in df.iterrows():
        u = units_current.get(r["product"], 0.0)
        daily = u / days if days else 0
        cover = (r["stock"] / daily) if daily > 0 else None
        out.append({"product": r["product"], "stock_units": _round(r["stock"], 0),
                    "units_per_day_now": _round(daily, 1), "days_cover": None if cover is None else _round(cover, 1)})
    return out


# ── main entry point ──────────────────────────────────────────────────────────
def explain_change(
    metric: str = "sales",
    current_start: Any = None,
    current_end: Any = None,
    base_start: Any = None,
    base_end: Any = None,
    compare_mode: str = "previous_period",
    filters: Optional[Dict[str, Any]] = None,
    dimensions: Optional[List[str]] = None,
    top_n: int = 5,
) -> Dict[str, Any]:
    """Full root-cause analysis; returns a JSON-serialisable dict. Raises ValueError for bad input."""
    metric = (metric or "sales").lower()
    if metric not in METRICS:
        raise ValueError(f"metric must be one of {', '.join(METRICS)}")
    unit = METRICS[metric]["unit"]
    filters = {k: v for k, v in (filters or {}).items() if v not in (None, "", [])}
    top_n = max(3, min(10, int(top_n or 5)))

    per = resolve_periods(current_start, current_end, base_start, base_end, compare_mode)
    branch_codes = C.resolve_branch_codes(filters)
    allowed = INVOICE_DIMENSIONS if metric == "invoices" else ALL_DIMENSIONS
    dims = [d for d in (dimensions or ALL_DIMENSIONS) if d in ALL_DIMENSIONS + ["rbm", "bdm", "time"]]
    dims = ["week" if d == "time" else d for d in dims]
    skipped = [d for d in dims if d not in allowed and d != "rbm"] if dimensions else []
    dims = [d for d in dims if d in allowed or d == "rbm"] or allowed

    raw = _fetch(per, branch_codes, filters, metric)
    bp: pd.DataFrame = raw["bp"]
    daily: pd.DataFrame = raw["daily"]

    # headline totals
    if metric == "sales":
        cur_total, base_total = float(bp["vc"].sum()), float(bp["vb"].sum())
        bp["cur"], bp["base"] = bp["vc"], bp["vb"]
    elif metric == "units":
        cur_total, base_total = float(bp["qc"].sum()), float(bp["qb"].sum())
        bp["cur"], bp["base"] = bp["qc"], bp["qb"]
    else:
        cur_total, base_total = float(bp["ic"].sum()), float(bp["ib"].sum())
        bp["cur"], bp["base"] = bp["ic"], bp["ib"]
    delta = cur_total - base_total
    delta_pct = (delta / base_total * 100) if abs(base_total) > MIN_ABS else None

    warnings = list(per["warnings"])
    if skipped:
        warnings.append("Dimensions not additive for this metric were skipped: " + ", ".join(skipped))
    if abs(base_total) <= MIN_ABS and abs(cur_total) <= MIN_ABS:
        warnings.append("No data found for either period with these filters.")

    # enrich branch info
    bm = C.branch_master_df().set_index("code")
    bp["branch"] = bp["bcode"].map(bm["branch_name"]).fillna(bp["bcode"])
    bp["district"] = bp["bcode"].map(bm["district"]).replace("", pd.NA).fillna("(unknown)")
    bp["rbm"] = bp["bcode"].map(bm["rbm"]).replace("", pd.NA).fillna("(unknown)")
    if metric != "invoices":
        bp["product"] = bp["product"].replace("", "(unmatched item)")
        bp["brand"] = bp["brand"].replace("", "(unmatched item)")
        bp["category"] = bp["product"].map(C.category_of)

    dimensions_out: List[Dict[str, Any]] = []
    recon_ok = True
    recon_detail: List[str] = []
    for d in dims:
        if d == "week":
            g = _weekly(daily, per, metric)
        elif d == "salesperson":
            if raw.get("staff") is None:
                warnings.append("Salesperson breakdown unavailable (" + raw.get("staff_error", "query failed") + ").")
                continue
            g = raw["staff"].set_index("who")[["cur", "base"]]
        else:
            if d not in bp.columns:
                continue
            g = bp.groupby(d)[["cur", "base"]].sum()
        if g.empty:
            continue
        dr = _drivers(g, delta, top_n)
        diff = abs(dr["sum_delta"] - delta)
        # tolerance: 0.5% of the headline change or 1 unit (salesperson mapping can drop a few unmatched rows)
        tol = max(1.0, abs(delta) * 0.005)
        if diff > tol:
            recon_ok = False
            recon_detail.append(f"{d}: members sum to {dr['sum_delta']:,.2f} vs total {delta:,.2f}")
        dimensions_out.append({
            "key": d, "label": DIM_LABELS.get(d, d.title()), **dr,
            "waterfall": _waterfall(base_total, cur_total, dr),
        })

    # stable business order (category → product → brand → branch → district → salesperson → time)
    order = ["category", "product", "brand", "branch", "district", "rbm", "salesperson", "week"]
    dimensions_out.sort(key=lambda x: order.index(x["key"]) if x["key"] in order else 99)

    if metric != "invoices" and not bp.empty:
        um = bp[bp["product"] == "(unmatched item)"]
        um_cur = float(um["cur"].sum())
        if abs(cur_total) > MIN_ABS and abs(um_cur) / abs(cur_total) > 0.01:
            warnings.append(
                f"{um_cur / cur_total * 100:.1f}% of current-period {METRICS[metric]['label'].lower()} belongs to item codes "
                "that are missing from item_master (shown as '(unmatched item)'), so their product/brand/category is unknown."
            )

    price_volume = _price_volume(bp) if metric == "sales" and not bp.empty else None

    # daily comparison, aligned by day index
    cur_days = daily[daily["p"] == 1].set_index("d")
    base_days = daily[daily["p"] == 2].set_index("d")
    col = {"sales": "v", "units": "q", "invoices": "inv"}[metric]
    daily_out = []
    for i in range(max(per["n"], per["nb"])):
        cd, bd = per["cs"] + timedelta(days=i), per["bs"] + timedelta(days=i)
        daily_out.append({
            "day": i + 1,
            "current_date": f"{cd:%Y-%m-%d}" if cd <= per["ce"] else None,
            "current": _round(cur_days[col].get(cd, 0.0)) if cd <= per["ce"] else None,
            "base_date": f"{bd:%Y-%m-%d}" if bd <= per["be"] else None,
            "base": _round(base_days[col].get(bd, 0.0)) if bd <= per["be"] else None,
        })
    cur_active = int((cur_days[col] > 0).sum())
    base_active = int((base_days[col] > 0).sum())
    per_day_cur = cur_total / per["n"]
    per_day_base = base_total / per["nb"]

    hypotheses = _hypotheses(
        metric, unit, per, delta, delta_pct, dimensions_out, price_volume, cur_active, base_active,
        per_day_cur, per_day_base, bp, daily_out, branch_filtered=branch_codes is not None,
    )

    res = {
        "metric": metric,
        "metric_label": METRICS[metric]["label"],
        "unit": unit,
        "filters": filters,
        "matched_branches": None if branch_codes is None else len(branch_codes),
        "compare_mode": per["mode"],
        "current": {"start": f"{per['cs']}", "end": f"{per['ce']}", "days": per["n"], "value": _round(cur_total),
                    "per_day": _round(per_day_cur), "active_days": cur_active},
        "base": {"start": f"{per['bs']}", "end": f"{per['be']}", "days": per["nb"], "value": _round(base_total),
                 "per_day": _round(per_day_base), "active_days": base_active},
        "delta": _round(delta),
        "delta_pct": None if delta_pct is None else _round(delta_pct, 1),
        "per_day_delta_pct": None if abs(per_day_base) <= MIN_ABS else _round((per_day_cur - per_day_base) / per_day_base * 100, 1),
        "dimensions": dimensions_out,
        "price_volume": price_volume,
        "daily": daily_out,
        "hypotheses": hypotheses,
        "warnings": warnings,
        "reconciliation": {
            "ok": recon_ok,
            "note": "Every dimension's members add up to the headline change." if recon_ok else "; ".join(recon_detail),
        },
        "data_through": f"{per['latest']}",
        "definition": "Sales = sum(sold_price) from azure_sales_report excluding internal/transfer invoices "
                      "(SMC / EI invoices, HEAD OFFICE, UG SMART CHOICE, 3GH, CSB, CSBW)."
                      if metric == "sales" else "Same internal/transfer exclusions as the dashboards.",
    }
    return res


def _hypotheses(metric, unit, per, delta, delta_pct, dims, pv, cur_active, base_active, per_day_cur, per_day_base, bp, daily_out,
                branch_filtered=False):
    """Data-supported possible explanations. Each carries the evidence so the narration can cite it."""
    out: List[Dict[str, str]] = []
    direction = "decline" if delta < 0 else "increase"

    if abs(per["n"] - per["nb"]) == 0 and cur_active != base_active:
        out.append({"title": "Different number of trading days",
                    "evidence": f"{cur_active} active days in the current period vs {base_active} in the base period."})
    elif per["n"] != per["nb"]:
        pd_pct = (per_day_cur - per_day_base) / per_day_base * 100 if abs(per_day_base) > MIN_ABS else 0
        out.append({"title": "Unequal period lengths",
                    "evidence": f"Per-day run-rate changed {pd_pct:+.1f}% ({C.fmt_val(per_day_base, unit)} → {C.fmt_val(per_day_cur, unit)} per day) "
                                f"while the totals compare {per['n']} vs {per['nb']} days."})

    # weekday mix (e.g. 5 Sundays vs 4)
    def wd_count(s: date, e: date, wd: int) -> int:
        return sum(1 for d in C.day_range(s, e) if d.weekday() == wd)
    for wd, name in ((6, "Sundays"), (5, "Saturdays")):
        a, b = wd_count(per["cs"], per["ce"], wd), wd_count(per["bs"], per["be"], wd)
        if a != b:
            out.append({"title": f"Calendar mix: {name}",
                        "evidence": f"The current period has {a} {name} vs {b} in the base period, which shifts footfall patterns."})
            break

    if dims:
        best = max(dims, key=lambda x: abs(x["top_share_pct"] or 0) if x["key"] in ("branch", "salesperson", "product", "brand") else -1)
        if best["drivers"] and best["top_share_pct"] is not None and best["top_share_pct"] >= 40:
            d0 = best["drivers"][0]
            out.append({"title": f"Concentrated in one {best['label'].lower()}",
                        "evidence": f"{d0['name']} alone accounts for {d0['contribution_pct']}% of the {direction} "
                                    f"({C.fmt_val(d0['delta'], unit)})."})
        for dd in dims:
            if dd["key"] in ("branch", "salesperson", "product"):
                stopped = [x for x in dd["drivers"] if x.get("note", "").startswith("stopped")]
                if stopped:
                    out.append({"title": f"{dd['label']}s that stopped contributing",
                                "evidence": ", ".join(f"{x['name']} ({C.fmt_val(x['delta'], unit)})" for x in stopped[:3]) +
                                            " had activity in the base period but none in the current period."})
                    break

    if pv and abs(pv["total_delta"]) > MIN_ABS:
        vs, ps = pv.get("volume_share_pct"), pv.get("price_share_pct")
        if ps is not None and abs(ps) >= 30:
            out.append({"title": "Price / discount effect",
                        "evidence": f"{ps:+.0f}% of the change is price-driven (avg selling price "
                                    f"{C.fmt_inr(pv['avg_selling_price_base'] or 0)} → {C.fmt_inr(pv['avg_selling_price_current'] or 0)} per unit); "
                                    f"volume explains {vs:+.0f}%."})
        elif vs is not None:
            out.append({"title": "Volume-driven change",
                        "evidence": f"{vs:+.0f}% of the change comes from units sold; price effect is {ps:+.0f}%."})

    # stock availability for the products that declined (stock snapshot is network-wide → skip when branch-filtered)
    if metric in ("sales", "units") and delta < 0 and not branch_filtered:
        prod_dim = next((d for d in dims if d["key"] == "product"), None)
        if prod_dim and prod_dim["drivers"]:
            names = [x["name"] for x in prod_dim["drivers"][:5]]
            units_now = bp.groupby("product")["qc"].sum().to_dict() if "qc" in bp.columns else {}
            cover = _stock_cover(names, units_now, per["n"])
            low = [c for c in cover if c["days_cover"] is not None and c["days_cover"] < 10]
            if low:
                out.append({"title": "Possible stock shortage on declining products",
                            "evidence": "; ".join(f"{c['product']}: {c['stock_units']:,.0f} units in stock ≈ {c['days_cover']} days of cover"
                                                  for c in low[:3]) + " (latest stock snapshot)."})
            elif cover:
                out.append({"title": "Stock does not look like the constraint",
                            "evidence": "; ".join(f"{c['product']}: {c['days_cover']} days of cover"
                                                  for c in cover[:3] if c['days_cover'] is not None) + " (latest stock snapshot)."})

    # most unusual day
    vals = [(x["day"], x["current"], x["base"]) for x in daily_out if x["current"] is not None and x["base"] is not None]
    if vals:
        worst = min(vals, key=lambda t: t[1] - t[2]) if delta < 0 else max(vals, key=lambda t: t[1] - t[2])
        out.append({"title": "Largest single-day swing",
                    "evidence": f"Day {worst[0]} of the period moved {C.fmt_val(worst[1] - worst[2], unit)} "
                                f"({C.fmt_val(worst[2], unit)} → {C.fmt_val(worst[1], unit)})."})
    return out


# ── views ─────────────────────────────────────────────────────────────────────
def llm_view(res: Dict[str, Any]) -> Dict[str, Any]:
    """Compact version of the RCA result handed back to the LLM (keeps the prompt small)."""
    keep_dims = []
    for d in res["dimensions"][:6]:
        keep_dims.append({
            "dimension": d["label"], "members": d["members"], "top_drivers_share_pct": d["top_share_pct"],
            "drivers": d["drivers"][:5], "offsetting_movers": d["offsets"][:3],
        })
    return {
        "metric": res["metric_label"], "unit": res["unit"], "filters": res["filters"], "compare_mode": res["compare_mode"],
        "current": res["current"], "base": res["base"], "delta": res["delta"], "delta_pct": res["delta_pct"],
        "per_day_delta_pct": res["per_day_delta_pct"], "dimensions": keep_dims, "price_volume": res["price_volume"],
        "possible_explanations": res["hypotheses"], "warnings": res["warnings"], "reconciliation": res["reconciliation"],
        "data_through": res["data_through"],
        "note": "A chart with driver tables and a waterfall is shown to the user automatically; narrate the findings, "
                "cite contribution % and supporting numbers, and label hypotheses as possible explanations, not proven causes.",
    }
