"""
Closed-Loop Campaign Learning, Leak-Free Predicted vs. Actual Backtest Scorecard,
Product / Item Category / Item Group ClickHouse Breakdown, and Colleague Excel/CSV Bulk Importer.
"""

from __future__ import annotations

import csv
import io
import re
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional, Sequence, Tuple

import openpyxl
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side

from .demand_promo_calendar import (
    CAMPAIGN_TYPE_META,
    CANONICAL_DISTRICTS,
    KNOWN_PRODUCT_LINES,
    STORE_FORMAT_LABELS,
    _attach_realized_lifts,
    _get_branch_catalog_cached,
    _get_latest_sales_date_cached,
    _normalize_csv_tokens,
    _parse_iso_date,
    _sql_esc,
    default_cooldown_for,
    estimate_store_planned_lift_from_ch,
    get_ch_client,
    get_item_categories_catalog,
    list_campaigns,
    normalize_districts,
    normalize_store_formats,
    resolve_effective_branches,
    save_campaigns_bulk,
    store_format_label,
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
# 3. COLLEAGUE EXCEL TEMPLATE + BULK EXCEL/CSV IMPORTER + BACKTEST EXPORT
# ═════════════════════════════════════════════════════════════════════════════

_IMPORT_COLUMNS: List[Tuple[str, str, int]] = [
    ("campaign_name*", "Campaign Title (Required)", 30),
    ("campaign_type", "mega_sale | weekend_dhamaka | exchange_cashback | brand_festival | flash_offer | product_launch", 20),
    ("start_date*", "YYYY-MM-DD or DD-MM-YYYY (Required)", 15),
    ("end_date*", "YYYY-MM-DD or DD-MM-YYYY (Required)", 15),
    ("product_lines", "Comma-separated or ALL (e.g. TV, REFRIGERATORS)", 28),
    ("item_categories", "Optional: exact item_category or ALL (e.g. WASHING MACHINE-FL, MIXER GRINDER)", 30),
    ("item_groups", "Optional: exact item_group or ALL (e.g. APPLE IPHONE 17 256GB, 1.5 TON 3 STAR)", 30),
    ("brands", "Comma-separated or ALL (e.g. APPLE, SAMSUNG, LG)", 24),
    ("store_formats", "ALL | MYG,MYG_FUTURE | MYG | MYG_FUTURE", 18),
    ("districts", "Comma-separated or ALL (e.g. KOZHIKODE, KANNUR)", 24),
    ("branches", "Specific store name(s) or ALL (leave ALL for full district)", 30),
    ("offer_strength", "low | medium | high | mega", 15),
    ("expected_lift_pct", "Leave BLANK for automatic ClickHouse Store Sale Value estimation!", 18),
    ("discount_summary", "1-line offer highlight (cashback / EMI / freebies / price drop)", 40),
]


def build_campaign_import_template_xlsx(ch=None) -> bytes:
    """Build a polished 4-sheet Excel template for colleagues to add previous & upcoming campaigns cleanly."""
    wb = openpyxl.Workbook()
    hdr_fill = PatternFill("solid", fgColor="0F766E")
    hdr_font = Font(bold=True, color="FFFFFF", size=10)
    sub_fill = PatternFill("solid", fgColor="F0FDF4")
    sub_font = Font(italic=True, color="065F46", size=9)
    ex_fill = PatternFill("solid", fgColor="FFFBEB")
    thin = Border(
        left=Side(style="thin", color="CBD5E1"),
        right=Side(style="thin", color="CBD5E1"),
        top=Side(style="thin", color="CBD5E1"),
        bottom=Side(style="thin", color="CBD5E1"),
    )

    # Sheet 1: Campaign Offers (Fill Here)
    ws = wb.active
    ws.title = "Campaign Offers"
    for idx, (col_key, hint, width) in enumerate(_IMPORT_COLUMNS, 1):
        c1 = ws.cell(row=1, column=idx, value=col_key)
        c1.fill = hdr_fill
        c1.font = hdr_font
        c1.alignment = Alignment(horizontal="center", vertical="center")
        c1.border = thin
        c2 = ws.cell(row=2, column=idx, value=hint)
        c2.fill = sub_fill
        c2.font = sub_font
        c2.alignment = Alignment(wrap_text=True, vertical="center")
        c2.border = thin
        ws.column_dimensions[openpyxl.utils.get_column_letter(idx)].width = width
    ws.row_dimensions[1].height = 24
    ws.row_dimensions[2].height = 34
    ws.freeze_panes = "A3"

    examples = [
        [
            "myG Onam Mega Sale 2026", "mega_sale", "2026-08-20", "2026-08-30",
            "TV, REFRIGERATORS, WASHING MACHINES, MOBILE", "ALL", "ALL", "ALL",
            "MYG,MYG_FUTURE", "ALL", "ALL", "mega", "",
            "Onam bumper discounts, bank cashback up to Rs.10,000 + assured gifts",
        ],
        [
            "Edappally Front Load & iPhone Flash", "flash_offer", "2026-10-12", "2026-10-13",
            "WASHING MACHINES, MOBILE", "WASHING MACHINE-FL, MOBILE SMART PHONE",
            "8KG, APPLE IPHONE 17 256GB", "LG, SAMSUNG, APPLE",
            "MYG_FUTURE", "ERNAKULAM", "EDAPPALLY FUTURE", "high", "",
            "48-Hr flash price drop on 8KG Front Load & iPhone 17 256GB at Edappally Future",
        ],
    ]
    for r_i, ex in enumerate(examples, 3):
        for c_i, val in enumerate(ex, 1):
            cell = ws.cell(row=r_i, column=c_i, value=val)
            cell.fill = ex_fill
            cell.border = thin

    # Sheet 2: Colleague Instructions
    ws_guide = wb.create_sheet("Instructions")
    ws_guide.column_dimensions["A"].width = 26
    ws_guide.column_dimensions["B"].width = 88
    guide_rows = [
        ("How to Use This Sheet", "Fill previous (historical) or upcoming myG campaigns in the 'Campaign Offers' tab starting from Row 3 (replace the yellow example rows)."),
        ("Previous Offers = Smarter AI", "Every past offer you upload is automatically matched to ClickHouse actual Sale Value (₹) and trains the self-learning lift model + Predicted vs. Actual scorecard!"),
        ("Leave expected_lift_pct Blank", "Leave column M (expected_lift_pct) empty so ClickHouse automatically calculates each store's realistic Sale Value Planned Lift % from complete history + learned past offers. Only fill a number if you want a manual override."),
        ("Product -> Category -> Group", "Use product_lines for broad categories (TV, MOBILE, WASHING MACHINES). Optionally narrow down using exact item_categories (e.g. WASHING MACHINE-FL, MIXER GRINDER) and item_groups (e.g. APPLE IPHONE 17 256GB, 1.5 TON 3 STAR, 8KG). Leave ALL if not restricted."),
        ("Specific Stores vs Districts", "If the offer ran only at specific store(s), list them in 'branches' (copy exact names from 'Valid Branches' tab). If district-wide, leave 'branches' as ALL and fill 'districts' + 'store_formats'."),
        ("Supported Date Formats", "YYYY-MM-DD (recommended), DD-MM-YYYY, DD/MM/YYYY, or native Excel dates."),
        ("Upload Back to Dashboard", "Go to Offer Calendar (/campaign-offers/) -> 'Bulk Import Past / Upcoming Offers (Excel / CSV)' -> Upload this file -> Preview -> Import & Learn!"),
    ]
    ws_guide.cell(row=1, column=1, value="Topic").font = hdr_font
    ws_guide.cell(row=1, column=1).fill = hdr_fill
    ws_guide.cell(row=1, column=2, value="Colleague SOP / Guideline").font = hdr_font
    ws_guide.cell(row=1, column=2).fill = hdr_fill
    for i, (k, v) in enumerate(guide_rows, 2):
        ws_guide.cell(row=i, column=1, value=k).font = Font(bold=True, size=10)
        ws_guide.cell(row=i, column=2, value=v).alignment = Alignment(wrap_text=True)

    # Sheet 3: Valid Branches & Districts (Live from ClickHouse)
    ws_br = wb.create_sheet("Valid Branches")
    for idx, (h, w) in enumerate([("Branch Code", 14), ("Exact Branch Name (use in branches)", 34), ("Canonical District", 22), ("Store Format", 16)], 1):
        c = ws_br.cell(row=1, column=idx, value=h)
        c.fill = hdr_fill
        c.font = hdr_font
        ws_br.column_dimensions[openpyxl.utils.get_column_letter(idx)].width = w
    for r_i, (code, bname, dist) in enumerate(_get_branch_catalog_cached(ch=ch), 2):
        ws_br.cell(row=r_i, column=1, value=code)
        ws_br.cell(row=r_i, column=2, value=bname)
        ws_br.cell(row=r_i, column=3, value=dist)
        ws_br.cell(row=r_i, column=4, value="MYG_FUTURE" if "FUTURE" in bname else "MYG")

    # Sheet 4: Valid Products & Item Categories (Live from ClickHouse)
    ws_pr = wb.create_sheet("Valid Products & Categories")
    for idx, (h, w) in enumerate([("Product Line (product_lines)", 26), ("Item Category (item_categories)", 34), ("SKU Count", 12)], 1):
        c = ws_pr.cell(row=1, column=idx, value=h)
        c.fill = hdr_fill
        c.font = hdr_font
        ws_pr.column_dimensions[openpyxl.utils.get_column_letter(idx)].width = w
    cats = get_item_categories_catalog(ch=ch, limit=500)
    if cats:
        for r_i, item in enumerate(cats, 2):
            ws_pr.cell(row=r_i, column=1, value=item["product"])
            ws_pr.cell(row=r_i, column=2, value=item["item_category"])
            ws_pr.cell(row=r_i, column=3, value=item["sku_count"])
    else:
        for r_i, prod in enumerate(KNOWN_PRODUCT_LINES, 2):
            ws_pr.cell(row=r_i, column=1, value=prod)
            ws_pr.cell(row=r_i, column=2, value="ALL")

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


_COL_ALIAS_MAP: Dict[str, str] = {
    "campaignname": "campaign_name",
    "offername": "campaign_name",
    "name": "campaign_name",
    "title": "campaign_name",
    "campaign": "campaign_name",
    "campaigntype": "campaign_type",
    "offertype": "campaign_type",
    "type": "campaign_type",
    "startdate": "start_date",
    "fromdate": "start_date",
    "start": "start_date",
    "from": "start_date",
    "enddate": "end_date",
    "todate": "end_date",
    "end": "end_date",
    "to": "end_date",
    "productlines": "product_lines",
    "productline": "product_lines",
    "products": "product_lines",
    "product": "product_lines",
    "itemcategories": "item_categories",
    "itemcategory": "item_categories",
    "categories": "item_categories",
    "category": "item_categories",
    "subcategory": "item_categories",
    "itemgroups": "item_groups",
    "itemgroup": "item_groups",
    "groups": "item_groups",
    "group": "item_groups",
    "models": "item_groups",
    "brands": "brands",
    "brand": "brands",
    "storeformats": "store_formats",
    "storeformat": "store_formats",
    "format": "store_formats",
    "districts": "districts",
    "district": "districts",
    "branches": "branches",
    "branch": "branches",
    "stores": "branches",
    "store": "branches",
    "targetstores": "branches",
    "offerstrength": "offer_strength",
    "strength": "offer_strength",
    "expectedliftpct": "expected_lift_pct",
    "expectedlift": "expected_lift_pct",
    "plannedlift": "expected_lift_pct",
    "plannedliftpct": "expected_lift_pct",
    "lift": "expected_lift_pct",
    "postcooldowndays": "post_cooldown_days",
    "cooldown": "post_cooldown_days",
    "discountsummary": "discount_summary",
    "discount": "discount_summary",
    "highlight": "discount_summary",
    "notes": "discount_summary",
    "details": "discount_summary",
}


def _norm_header_key(h: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(h or "").strip().lower())


def _parse_sheet_date(val: Any) -> Optional[date]:
    if isinstance(val, datetime):
        return val.date()
    if isinstance(val, date):
        return val
    s = str(val or "").strip()
    if not s:
        return None
    # Strip trailing time portion if present
    s_clean = s.split(" ")[0] if ("-" in s or "/" in s) else s
    d = _parse_iso_date(s_clean) or _parse_iso_date(s)
    if d:
        return d
    for fmt in ("%d-%b-%Y", "%d %b %Y", "%m/%d/%Y", "%Y.%m.%d", "%d.%m.%Y"):
        try:
            return datetime.strptime(s[:20], fmt).date()
        except Exception:
            pass
    return None


def parse_campaigns_from_spreadsheet(
    file_bytes: bytes,
    filename: str = "offers.xlsx",
    auto_estimate: bool = True,
    ch=None,
) -> Dict[str, Any]:
    """Parse an uploaded `.xlsx` or `.csv` campaign sheet into validated, ClickHouse-calibrated campaign payloads."""
    if not file_bytes:
        raise ValueError("Uploaded file is empty.")
    low_name = str(filename or "").strip().lower()
    raw_table: List[List[Any]] = []

    if low_name.endswith(".csv"):
        text = file_bytes.decode("utf-8-sig", errors="replace")
        reader = csv.reader(io.StringIO(text))
        raw_table = [list(r) for r in reader]
    elif low_name.endswith(".xlsx") or low_name.endswith(".xlsm"):
        wb = openpyxl.load_workbook(io.BytesIO(file_bytes), data_only=True, read_only=True)
        ws = wb.active
        for row in ws.iter_rows(values_only=True):
            raw_table.append(list(row))
        wb.close()
    else:
        raise ValueError("Please upload an Excel (.xlsx) or CSV (.csv) file.")

    if not raw_table:
        raise ValueError("No rows found in uploaded file.")

    # Locate header row (first row containing at least 2 recognized columns)
    header_idx = -1
    col_map: Dict[int, str] = {}
    for idx, row in enumerate(raw_table[:10]):
        mapped = {}
        for c_i, cell in enumerate(row):
            canon = _COL_ALIAS_MAP.get(_norm_header_key(cell))
            if canon:
                mapped[c_i] = canon
        if len(mapped) >= 2 and "campaign_name" in mapped.values():
            header_idx = idx
            col_map = mapped
            break
    if header_idx < 0:
        raise ValueError("Could not recognize required columns (expected at least 'campaign_name' and 'start_date'). Download the Excel template first.")

    client = ch or get_ch_client()
    latest_dt = _get_latest_sales_date_cached(ch=client) if auto_estimate else date.today()
    campaigns: List[Dict[str, Any]] = []
    warnings: List[str] = []

    for r_num, row in enumerate(raw_table[header_idx + 1 :], start=header_idx + 2):
        rec: Dict[str, Any] = {}
        for c_i, field_name in col_map.items():
            rec[field_name] = row[c_i] if c_i < len(row) else ""

        name = str(rec.get("campaign_name") or "").strip()
        # Skip empty rows or template hint row (Row 2 in template)
        if not name or name.lower().startswith("campaign title (required)"):
            continue

        sd = _parse_sheet_date(rec.get("start_date"))
        ed = _parse_sheet_date(rec.get("end_date")) or sd
        if not sd or not ed:
            warnings.append(f"Row {r_num} ('{name[:32]}'): skipped — invalid or missing start_date/end_date.")
            continue
        if ed < sd:
            sd, ed = ed, sd
        if (ed - sd).days > 60:
            warnings.append(f"Row {r_num} ('{name[:32]}'): skipped — duration exceeds 60 days.")
            continue

        ctype = str(rec.get("campaign_type") or "weekend_dhamaka").strip().lower().replace(" ", "_")
        if ctype not in CAMPAIGN_TYPE_META:
            ctype = "weekend_dhamaka"
        strength = str(rec.get("offer_strength") or "medium").strip().lower()
        if strength not in ("low", "medium", "high", "mega"):
            strength = "medium"

        prods_norm = _normalize_csv_tokens(rec.get("product_lines"), "ALL")
        cats_norm = _normalize_csv_tokens(rec.get("item_categories"), "ALL")
        grps_norm = _normalize_csv_tokens(rec.get("item_groups"), "ALL")
        brands_norm = _normalize_csv_tokens(rec.get("brands"), "ALL")
        dist_norm = normalize_districts(rec.get("districts"))
        sfmt_norm = normalize_store_formats(rec.get("store_formats"))
        br_norm, br_list, sel_mode = resolve_effective_branches(
            rec.get("branches"),
            districts_norm=dist_norm,
            store_formats=sfmt_norm,
            ch=client,
        )

        raw_lift_str = str(rec.get("expected_lift_pct") or "").strip().replace("%", "")
        manual_lift = 0.0
        if raw_lift_str:
            try:
                manual_lift = float(raw_lift_str)
            except Exception:
                manual_lift = 0.0

        basis_lbl = "Manual from sheet"
        if manual_lift > 0:
            final_lift = max(1.0, min(400.0, manual_lift))
        elif auto_estimate:
            est = estimate_store_planned_lift_from_ch(
                campaign_type=ctype,
                offer_strength=strength,
                campaign_name=name,
                start_date=sd,
                end_date=ed,
                product_lines=prods_norm,
                item_categories=cats_norm,
                item_groups=grps_norm,
                brands=brands_norm,
                districts=dist_norm,
                store_formats=sfmt_norm,
                branches=br_norm,
                ch=client,
            )
            final_lift = float(est["expected_lift_pct"])
            strength = est["offer_strength"]
            basis_lbl = est.get("basis_label") or "ClickHouse auto-estimate"
        else:
            final_lift = float(CAMPAIGN_TYPE_META[ctype]["lifts"].get(strength, 18.0))
            basis_lbl = "Campaign-type default"

        try:
            cd_val = int(float(str(rec.get("post_cooldown_days") or "").strip()))
            cd_val = max(0, min(14, cd_val))
        except Exception:
            cd_val = default_cooldown_for(ctype)

        campaigns.append({
            "campaign_name": name[:140],
            "campaign_type": ctype,
            "start_date": sd.strftime("%Y-%m-%d"),
            "end_date": ed.strftime("%Y-%m-%d"),
            "product_lines": ["ALL"] if prods_norm == "ALL" else prods_norm.split(","),
            "item_categories": ["ALL"] if cats_norm == "ALL" else cats_norm.split(","),
            "item_groups": ["ALL"] if grps_norm == "ALL" else grps_norm.split(","),
            "brands": ["ALL"] if brands_norm == "ALL" else brands_norm.split(","),
            "district": dist_norm,
            "districts": ["ALL"] if dist_norm == "ALL" else dist_norm.split(","),
            "store_formats": sfmt_norm,
            "store_formats_label": store_format_label(sfmt_norm),
            "branches": br_list,
            "branches_csv": br_norm,
            "store_selection_mode": sel_mode,
            "matched_store_count": 0 if br_norm == "ALL" else len(br_list),
            "offer_strength": strength,
            "expected_lift_pct": round(final_lift, 1),
            "planned_lift_basis": basis_lbl,
            "post_cooldown_days": cd_val,
            "discount_summary": str(rec.get("discount_summary") or "").strip()[:300],
            "source_channel": "excel_import",
            "is_historical": bool(ed < latest_dt),
        })

    if not campaigns:
        msg = "No valid campaign rows were found."
        if warnings:
            msg += " " + " ".join(warnings[:3])
        raise ValueError(msg)

    return {
        "campaigns": campaigns[:500],
        "count": len(campaigns[:500]),
        "historical_count": sum(1 for c in campaigns[:500] if c.get("is_historical")),
        "warnings": warnings[:25],
    }


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
