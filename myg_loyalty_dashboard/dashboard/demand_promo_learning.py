"""
Closed-Loop Campaign Learning, Leak-Free Predicted vs. Actual Backtest Scorecard,
Product / Item Category / Item Group ClickHouse Breakdown, and Backtest Excel Report Export.
"""

from __future__ import annotations

import io
from datetime import date, timedelta
from typing import Any, Dict, List, Optional, Tuple

import openpyxl
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side

from .demand_promo_calendar import (
    _attach_realized_lifts,
    _get_latest_sales_date_cached,
    _parse_iso_date,
    _sql_esc,
    estimate_store_planned_lift_from_ch,
    get_ch_client,
    list_campaigns,
)


def _campaign_scope_sql_clauses(c: Dict[str, Any]) -> Tuple[str, str]:
    """Build ClickHouse `(bm_join, where_sql)` matching a campaign's full store & 3-level item hierarchy scope."""
    clauses = [
        "s.branch NOT IN ('3GH', 'CSB', 'CSBW')",
        "s.item_code != ''",
        "NOT (upper(s.invoice_no) LIKE '%SMC%' OR upper(s.invoice_no) LIKE '%EI%')",
    ]
    if c.get("product_lines") and c["product_lines"] != "ALL":
        plist = c.get("product_lines_list") or [x for x in str(c["product_lines"]).split(",") if x]
        if plist:
            ps = "', '".join(_sql_esc(x) for x in plist)
            clauses.append(f"upper(trim(m.product)) IN ('{ps}')")
    if c.get("item_categories") and c["item_categories"] != "ALL":
        clist = c.get("item_categories_list") or [x for x in str(c["item_categories"]).split(",") if x]
        if clist:
            cs = "', '".join(_sql_esc(x) for x in clist)
            clauses.append(f"upper(trim(m.item_category)) IN ('{cs}')")
    if c.get("item_groups") and c["item_groups"] != "ALL":
        glist = c.get("item_groups_list") or [x for x in str(c["item_groups"]).split(",") if x]
        if glist:
            gs = "', '".join(_sql_esc(x) for x in glist)
            clauses.append(f"upper(trim(m.item_group)) IN ('{gs}')")
    if c.get("brands") and c["brands"] != "ALL":
        blist = c.get("brands_list") or [x for x in str(c["brands"]).split(",") if x]
        if blist:
            bs = "', '".join(_sql_esc(x) for x in blist)
            clauses.append(f"upper(trim(m.brand)) IN ('{bs}')")

    need_bm = False
    br_list = c.get("branches_list") or []
    if br_list:
        need_bm = True
        br_sql = "', '".join(_sql_esc(x) for x in br_list)
        clauses.append(f"(upper(trim(bm.branch_name)) IN ('{br_sql}') OR upper(trim(bm.code)) IN ('{br_sql}'))")
    else:
        dist_list = c.get("districts_list") or []
        sfmt = c.get("store_formats") or "ALL"
        if dist_list:
            need_bm = True
            ds_sql = "', '".join(_sql_esc(x) for x in dist_list)
            clauses.append(f"upper(trim(bm.district)) IN ('{ds_sql}')")
        if sfmt == "MYG_FUTURE":
            need_bm = True
            clauses.append("upper(bm.branch_name) LIKE '%FUTURE%'")
        elif sfmt == "MYG":
            need_bm = True
            clauses.append("upper(bm.branch_name) NOT LIKE '%FUTURE%'")

    bm_join = "INNER JOIN branch_master bm ON s.branch = bm.code" if need_bm else ""
    return bm_join, " AND ".join(clauses)


# ═════════════════════════════════════════════════════════════════════════════
# 1. LEAK-FREE PREDICTED VS. ACTUAL BACKTEST SCORECARD
# ═════════════════════════════════════════════════════════════════════════════

def run_campaign_backtest_scorecard(ch=None, ref_dt: Optional[date] = None) -> Dict[str, Any]:
    """
    Strict time-ordered leak-free backtest across all started/completed campaigns:
      - For every campaign `c` (chronologically sorted), predicts Sale Value Lift % using ONLY:
          (a) ClickHouse store sales strictly before `c.start_date - 3d`, AND
          (b) Earlier completed campaigns whose `end_date < c.start_date` (zero leakage).
      - Compares Predicted Sale Value (₹) & Lift % against Actual ClickHouse Sale Value (₹) & Realized Lift %.
    """
    client = ch or get_ch_client()
    latest_dt = ref_dt or _get_latest_sales_date_cached(ch=client)
    all_rows = list_campaigns(ch=client, include_realized_lift=False, ref_dt=latest_dt)

    started = [
        dict(r) for r in all_rows
        if _parse_iso_date(r.get("start_date")) and _parse_iso_date(r.get("start_date")) <= latest_dt
    ]
    if started:
        _attach_realized_lifts(client, started, latest_dt)

    # Sort chronologically so walk-forward learning pool strictly contains only earlier completed offers
    started.sort(key=lambda r: (str(r.get("start_date") or ""), str(r.get("end_date") or ""), str(r.get("campaign_id") or "")))

    completed_pool: List[Dict[str, Any]] = [
        r for r in started
        if _parse_iso_date(r.get("end_date")) and _parse_iso_date(r.get("end_date")) < latest_dt and r.get("realized_lift_pct") is not None
    ]

    evaluated_rows: List[Dict[str, Any]] = []
    tot_base = 0.0
    tot_pred = 0.0
    tot_actual = 0.0
    tot_abs_err = 0.0
    lift_biases: List[float] = []
    within_15_hits = 0

    for c in started:
        sd = _parse_iso_date(c.get("start_date"))
        ed = _parse_iso_date(c.get("end_date"))
        base_tot = c.get("baseline_total_sales")
        act_tot = c.get("realized_total_sales")
        act_lift = c.get("realized_lift_pct")
        if not sd or not ed or base_tot is None or act_tot is None or act_lift is None or float(base_tot) <= 100.0:
            continue

        # Strictly earlier completed campaigns only (end_date < sd) -> 100% leak-free!
        strictly_prior = [
            p for p in completed_pool
            if _parse_iso_date(p.get("end_date")) and _parse_iso_date(p.get("end_date")) < sd and p["campaign_id"] != c["campaign_id"]
        ]
        est = estimate_store_planned_lift_from_ch(
            campaign_type=c.get("campaign_type") or "weekend_dhamaka",
            offer_strength=c.get("offer_strength") or "medium",
            campaign_name=c.get("campaign_name") or "",
            start_date=sd,
            end_date=ed,
            product_lines=c.get("product_lines") or "ALL",
            item_categories=c.get("item_categories") or "ALL",
            item_groups=c.get("item_groups") or "ALL",
            brands=c.get("brands") or "ALL",
            districts=c.get("district") or "ALL",
            store_formats=c.get("store_formats") or "ALL",
            branches=c.get("branches") or "ALL",
            ch=client,
            prior_completed=strictly_prior,
            use_learned_offers=True,
        )
        pred_lift = float(est.get("expected_lift_pct") or 0.0)
        saved_lift = float(c.get("effective_lift_pct") or 0.0)
        base_f = float(base_tot)
        act_f = float(act_tot)
        pred_f = base_f * (1.0 + pred_lift / 100.0)
        var_sales = act_f - pred_f
        var_lift = float(act_lift) - pred_lift
        abs_err = abs(act_f - pred_f)
        abs_err_pct = (abs_err / act_f) * 100.0 if act_f > 0 else 0.0
        rev_err_pct = (abs_err / max(pred_f, act_f, 1.0)) * 100.0

        tot_base += base_f
        tot_pred += pred_f
        tot_actual += act_f
        tot_abs_err += abs_err
        lift_biases.append(pred_lift - float(act_lift))
        if rev_err_pct <= 15.0:
            within_15_hits += 1

        evaluated_rows.append({
            "campaign_id": c["campaign_id"],
            "campaign_name": c["campaign_name"],
            "campaign_type": c["campaign_type"],
            "campaign_type_label": c.get("campaign_type_label") or c["campaign_type"],
            "status": c.get("status") or "completed",
            "start_date": c["start_date"],
            "end_date": c["end_date"],
            "date_range_display": c.get("date_range_display") or c["start_date"],
            "branches": c.get("branches") or "ALL",
            "district": c.get("district") or "ALL",
            "store_formats": c.get("store_formats") or "ALL",
            "product_lines": c.get("product_lines") or "ALL",
            "item_categories": c.get("item_categories") or "ALL",
            "item_groups": c.get("item_groups") or "ALL",
            "brands": c.get("brands") or "ALL",
            "realized_days": int(c.get("realized_days") or 1),
            "baseline_label": c.get("baseline_label") or "",
            "baseline_total_sales": round(base_f, 0),
            "saved_planned_lift_pct": round(saved_lift, 1),
            "predicted_lift_pct": round(pred_lift, 1),
            "predicted_total_sales": round(pred_f, 0),
            "actual_total_sales": round(act_f, 0),
            "actual_lift_pct": round(float(act_lift), 1),
            "incremental_sales": round(act_f - base_f, 0),
            "variance_sales": round(var_sales, 0),
            "variance_lift_pct": round(var_lift, 1),
            "abs_error_pct": round(abs_err_pct, 1),
            "actual_cooldown_dip_pct": c.get("actual_cooldown_dip_pct"),
            "learned_from_count": int(est.get("learned_from_count") or 0),
            "prediction_basis": est.get("basis_label") or "",
            "prediction_source": est.get("source") or "prior_fallback",
        })

    # Show newest campaigns first in the scorecard table
    evaluated_rows.reverse()

    n_eval = len(evaluated_rows)
    wape = (tot_abs_err / tot_actual) * 100.0 if tot_actual > 0 else None
    accuracy = max(0.0, 100.0 - wape) if wape is not None else None
    mean_bias = (sum(lift_biases) / len(lift_biases)) if lift_biases else None
    hit_rate = (within_15_hits / n_eval) * 100.0 if n_eval > 0 else None

    return {
        "ref_date": latest_dt.strftime("%Y-%m-%d"),
        "summary": {
            "evaluated_campaigns": n_eval,
            "learned_completed_campaigns": len(completed_pool),
            "total_saved_campaigns": len(all_rows),
            "revenue_wape_pct": round(wape, 1) if wape is not None else None,
            "revenue_accuracy_pct": round(accuracy, 1) if accuracy is not None else None,
            "mean_lift_bias_pct": round(mean_bias, 1) if mean_bias is not None else None,
            "within_15pct_hit_rate": round(hit_rate, 1) if hit_rate is not None else None,
            "total_baseline_sales": round(tot_base, 0),
            "total_predicted_sales": round(tot_pred, 0),
            "total_actual_sales": round(tot_actual, 0),
            "total_variance_sales": round(tot_actual - tot_pred, 0),
        },
        "rows": evaluated_rows,
    }


# ═════════════════════════════════════════════════════════════════════════════
# 2. PER-CAMPAIGN PRODUCT / ITEM CATEGORY / ITEM GROUP CLICKHOUSE BREAKDOWN
# ═════════════════════════════════════════════════════════════════════════════

def get_campaign_hierarchy_breakdown(
    campaign_id: str,
    ch=None,
    ref_dt: Optional[date] = None,
) -> Dict[str, Any]:
    """
    Break down a single campaign's Predicted vs. Actual Sale Value (₹) across all 3 myG item hierarchy levels:
      1. Product (`item_master.product`)
      2. Item Category (`item_master.item_category`)
      3. Item Group (`item_master.item_group`)
    """
    client = ch or get_ch_client()
    latest_dt = ref_dt or _get_latest_sales_date_cached(ch=client)
    rows = list_campaigns(ch=client, include_realized_lift=False, ref_dt=latest_dt)
    target = next((r for r in rows if r["campaign_id"] == str(campaign_id or "").strip()), None)
    if not target:
        raise ValueError("Campaign not found.")

    sd = _parse_iso_date(target["start_date"])
    ed = min(_parse_iso_date(target["end_date"]), latest_dt)
    if not sd or not ed or ed < sd:
        return {
            "campaign": target,
            "has_actuals": False,
            "message": "This campaign has not started yet (no ClickHouse sales actuals available yet).",
            "by_product": [],
            "by_item_category": [],
            "by_item_group": [],
        }

    try:
        from .demand_intelligence_service import _FESTIVAL_DATE_MAP
    except Exception:
        _FESTIVAL_DATE_MAP = {}

    _DOW_SHORT = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
    camp_dates = [sd + timedelta(days=i) for i in range((ed - sd).days + 1)]
    camp_dows = sorted({d.weekday() for d in camp_dates})
    camp_days = max(1, len(camp_dates))

    pre_start = sd - timedelta(days=52)
    pre_end = sd - timedelta(days=4)
    all_pre_dates = [pre_start + timedelta(days=i) for i in range((pre_end - pre_start).days + 1)]
    clean_all_dates = [
        d for d in all_pre_dates
        if not (_FESTIVAL_DATE_MAP.get(d, {}).get("is_core_surge", False))
    ]
    if len(clean_all_dates) < 14:
        clean_all_dates = all_pre_dates
    clean_by_dow = {dow: [d for d in clean_all_dates if d.weekday() == dow] for dow in camp_dows}
    use_same_dow = all(len(clean_by_dow[dow]) >= 3 for dow in camp_dows)

    if use_same_dow:
        base_dates = [d for dow in camp_dows for d in clean_by_dow[dow]]
        base_days_cnt = len(base_dates)
        dow_str = "/".join(_DOW_SHORT[d] for d in camp_dows)
        base_lbl = f"Same-weekday ({base_days_cnt} clean {dow_str})"
    else:
        base_dates = clean_all_dates
        base_days_cnt = max(1, len(base_dates))
        base_lbl = f"Clean prior {base_days_cnt}d avg"

    base_sql_in = ", ".join(f"toDate('{d.strftime('%Y-%m-%d')}')" for d in base_dates)
    bm_join, where_sql = _campaign_scope_sql_clauses(target)
    planned_lift = float(target.get("effective_lift_pct") or 0.0)
    net_q = "if(s.sold_price < 0, -abs(toFloat64(s.qty)), abs(toFloat64(s.qty)))"

    sql = f"""
        SELECT
            upper(trim(m.product)) AS prod,
            upper(trim(m.item_category)) AS icat,
            upper(trim(m.item_group)) AS igrp,
            sumIf(toFloat64(s.sold_price), toDate(s.date) >= toDate('{sd}') AND toDate(s.date) <= toDate('{ed}')) AS camp_rev,
            sumIf(toFloat64(s.sold_price), toDate(s.date) IN ({base_sql_in})) AS base_rev_sum,
            sumIf({net_q}, toDate(s.date) >= toDate('{sd}') AND toDate(s.date) <= toDate('{ed}')) AS camp_qty,
            sumIf({net_q}, toDate(s.date) IN ({base_sql_in})) AS base_qty_sum
        FROM azure_sales_report s
        INNER JOIN item_master m ON s.item_code = m.item_code
        {bm_join}
        WHERE s.date >= toDateTime('{pre_start} 00:00:00')
          AND s.date < toDateTime('{(ed + timedelta(days=1))} 00:00:00')
          AND {where_sql}
        GROUP BY prod, icat, igrp
    """
    raw_rows = client.query(sql).result_rows or []

    # Aggregate cleanly at all 3 levels in Python from the single ClickHouse scan
    prod_agg: Dict[str, List[float]] = {}
    cat_agg: Dict[Tuple[str, str], List[float]] = {}
    grp_agg: Dict[Tuple[str, str, str], List[float]] = {}
    grand_actual = 0.0

    scale_base = camp_days / max(1, base_days_cnt)
    for r in raw_rows:
        p = str(r[0] or "UNKNOWN").strip().upper()
        ic = str(r[1] or "OTHER").strip().upper()
        ig = str(r[2] or "OTHER").strip().upper()
        c_rev = max(0.0, float(r[3] or 0.0))
        b_rev = max(0.0, float(r[4] or 0.0)) * scale_base
        c_qty = max(0.0, float(r[5] or 0.0))
        b_qty = max(0.0, float(r[6] or 0.0)) * scale_base
        if c_rev <= 0 and b_rev <= 0:
            continue
        grand_actual += c_rev
        for d_map, k in ((prod_agg, p), (cat_agg, (p, ic)), (grp_agg, (p, ic, ig))):
            acc = d_map.setdefault(k, [0.0, 0.0, 0.0, 0.0])
            acc[0] += c_rev
            acc[1] += b_rev
            acc[2] += c_qty
            acc[3] += b_qty

    def _format_level(agg_map: Dict[Any, List[float]], mode: str, top_n: int) -> List[Dict[str, Any]]:
        out = []
        for k, (c_rev, b_rev, c_qty, b_qty) in agg_map.items():
            pred_rev = b_rev * (1.0 + planned_lift / 100.0)
            lift_pct = ((c_rev - b_rev) / b_rev) * 100.0 if b_rev > 100.0 else None
            row_d: Dict[str, Any] = {
                "actual_sales": round(c_rev, 0),
                "baseline_sales": round(b_rev, 0),
                "predicted_sales": round(pred_rev, 0),
                "incremental_sales": round(c_rev - b_rev, 0),
                "variance_sales": round(c_rev - pred_rev, 0),
                "actual_lift_pct": round(lift_pct, 1) if lift_pct is not None else None,
                "planned_lift_pct": round(planned_lift, 1),
                "share_pct": round((c_rev / grand_actual) * 100.0, 1) if grand_actual > 0 else 0.0,
                "actual_qty": int(round(c_qty)),
                "baseline_qty": round(b_qty, 1),
            }
            if mode == "product":
                row_d["product"] = k
            elif mode == "category":
                row_d["product"] = k[0]
                row_d["item_category"] = k[1]
            else:
                row_d["product"] = k[0]
                row_d["item_category"] = k[1]
                row_d["item_group"] = k[2]
            out.append(row_d)
        out.sort(key=lambda x: (-x["actual_sales"], -x["baseline_sales"]))
        return out[:top_n]

    return {
        "campaign": target,
        "has_actuals": True,
        "measured_days": camp_days,
        "baseline_label": base_lbl,
        "total_actual_sales": round(grand_actual, 0),
        "by_product": _format_level(prod_agg, "product", 30),
        "by_item_category": _format_level(cat_agg, "category", 40),
        "by_item_group": _format_level(grp_agg, "group", 50),
    }


# ═════════════════════════════════════════════════════════════════════════════
# 3. PREDICTED VS. ACTUAL BACKTEST EXCEL EXPORT
# ═════════════════════════════════════════════════════════════════════════════

def build_backtest_report_xlsx(scorecard: Dict[str, Any]) -> bytes:
    """Export the Leak-Free Predicted vs. Actual Backtest Scorecard as a formatted Excel workbook."""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Predicted vs Actual"
    hdr_fill = PatternFill("solid", fgColor="1E3A8A")
    hdr_font = Font(bold=True, color="FFFFFF", size=10)
    thin = Border(
        left=Side(style="thin", color="CBD5E1"),
        right=Side(style="thin", color="CBD5E1"),
        top=Side(style="thin", color="CBD5E1"),
        bottom=Side(style="thin", color="CBD5E1"),
    )

    sm = scorecard.get("summary") or {}
    ws.cell(row=1, column=1, value="myG Campaign Offers — Leak-Free Predicted vs. Actual Sale Value Backtest").font = Font(bold=True, size=13, color="0F172A")
    ws.cell(
        row=2,
        column=1,
        value=(
            f"Sales Anchor: {scorecard.get('ref_date', '')} | Evaluated Offers: {sm.get('evaluated_campaigns', 0)} | "
            f"Learned Completed Offers: {sm.get('learned_completed_campaigns', 0)} | "
            f"Revenue Accuracy (100-WAPE): {sm.get('revenue_accuracy_pct', 'N/A')}% | "
            f"Mean Lift Bias: {sm.get('mean_lift_bias_pct', 'N/A')}% | ±15% Hit Rate: {sm.get('within_15pct_hit_rate', 'N/A')}%"
        ),
    ).font = Font(italic=True, size=10, color="334155")

    cols = [
        ("Campaign", "campaign_name", 28),
        ("Type", "campaign_type_label", 22),
        ("Dates", "date_range_display", 20),
        ("Status", "status", 12),
        ("Stores / Branches", "branches", 28),
        ("Products", "product_lines", 26),
        ("Item Categories", "item_categories", 24),
        ("Item Groups", "item_groups", 24),
        ("Baseline Sale Value (₹)", "baseline_total_sales", 20),
        ("Predicted Lift (%)", "predicted_lift_pct", 16),
        ("Predicted Sale Value (₹)", "predicted_total_sales", 20),
        ("Actual Lift (%)", "actual_lift_pct", 16),
        ("Actual Sale Value (₹)", "actual_total_sales", 20),
        ("Variance (Actual-Pred ₹)", "variance_sales", 20),
        ("Lift Variance (pts)", "variance_lift_pct", 16),
        ("Abs Error (%)", "abs_error_pct", 14),
        ("Post Cooldown Dip (%)", "actual_cooldown_dip_pct", 18),
        ("Prior Offers Learned", "learned_from_count", 16),
        ("Prediction Basis", "prediction_basis", 42),
    ]
    for c_i, (h, _k, w) in enumerate(cols, 1):
        cell = ws.cell(row=4, column=c_i, value=h)
        cell.fill = hdr_fill
        cell.font = hdr_font
        cell.alignment = Alignment(horizontal="center", vertical="center")
        cell.border = thin
        ws.column_dimensions[openpyxl.utils.get_column_letter(c_i)].width = w
    ws.freeze_panes = "A5"

    for r_off, row in enumerate(scorecard.get("rows") or [], 5):
        for c_i, (_h, k, _w) in enumerate(cols, 1):
            v = row.get(k, "")
            c = ws.cell(row=r_off, column=c_i, value=v if v is not None else "")
            c.border = thin
            if isinstance(v, (int, float)):
                c.alignment = Alignment(horizontal="right", vertical="center")

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
