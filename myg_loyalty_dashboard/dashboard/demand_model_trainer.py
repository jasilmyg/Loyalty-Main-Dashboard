"""
Offline (backend) training of the demand-forecast model on the WHOLE sales history.

Until now the LightGBM model was re-fitted inside every dashboard request on only the last 135 days of
whatever slice the user had filtered (a few hundred rows for e.g. MOBILE/VIVO). This module instead:

  1. walks backwards over the full history in 15-day steps (one training "origin" every 15 days),
  2. at each origin rebuilds EXACTLY the features the dashboard uses at inference time
     (same bucket SQL, same de-seasonalising multipliers, same `_build_fold_features`) for EVERY SKU,
     with the next-15-day units as the target,
  3. fits LightGBM (Tweedie, log-link anchored on the statistical forecast) on all origins together,
     chooses the configuration on an out-of-time holdout (the most recent origins, never used for fitting),
     learns the LightGBM-vs-statistical blend weight and conformal interval scales on that holdout,
  4. refits on everything and stores the model in ClickHouse (`demand_model_store`).

Run:  python manage.py train_demand_model
"""
from __future__ import annotations

import os
import pickle
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta
from typing import Any, Callable, Dict, List, Optional

import numpy as np
import pandas as pd

from . import demand_intelligence_service as dis
from .demand_daily_features import fetch_daily_rows, window_features
from .demand_ml_engine import (
    PretrainedDemandModel, _build_fold_features, _classify_model_tiers, _fit_tweedie_lgbm,
    _predict_tweedie_lgbm, _route_tier_predictions, blend_and_calibrate, moderate_bucket_mults,
    FEATURE_METADATA, HAS_LIGHTGBM, LGBM_VERSION, USE_DAILY_FEATURES,
)

STEP_DAYS = 15

# Candidate LightGBM configurations; the best one on the out-of-time holdout is kept.
CONFIGS: Dict[str, Optional[Dict[str, Any]]] = {
    "small_75": None,   # the (small) settings the per-request model used
    "mid_250": {"n_rounds": 250, "params": {"learning_rate": 0.05, "num_leaves": 31, "min_data_in_leaf": 150,
                                           "feature_fraction": 0.85, "lambda_l2": 5.0}},
    "deep_400": {"n_rounds": 400, "params": {"learning_rate": 0.04, "num_leaves": 63, "min_data_in_leaf": 250,
                                            "feature_fraction": 0.8, "lambda_l2": 8.0}},
}


# ─────────────────────────────────────────────────────────────────────────────
# 1. Per-origin data (identical SQL / multipliers to the live dashboard)
# ─────────────────────────────────────────────────────────────────────────────
def _bucket_sql(ref_dt: date, where_sql: str) -> str:
    d_ref = ref_dt.strftime("%Y-%m-%d")
    start_135 = (ref_dt - timedelta(days=134)).strftime("%Y-%m-%d 00:00:00")
    end_dt = (ref_dt + timedelta(days=1)).strftime("%Y-%m-%d 00:00:00")
    net_q = "if(s.sold_price < 0, -abs(toFloat64(s.qty)), abs(toFloat64(s.qty)))"
    age = f"dateDiff('day', toDate(s.date), toDate('{d_ref}'))"
    exprs = []
    for k in range(9):
        lo, hi = 15 * k, 15 * k + 14
        exprs.append(f"greatest(0.0, sumIf({net_q}, {age} >= {lo} AND {age} <= {hi})) AS u_b{k}")
        exprs.append(f"greatest(0.0, sumIf(toFloat64(s.sold_price), {age} >= {lo} AND {age} <= {hi})) AS r_b{k}")
        exprs.append(f"countDistinctIf(toDate(s.date), {age} >= {lo} AND {age} <= {hi} AND s.sold_price > 0) AS a_b{k}")
    return f"""
        SELECT
            s.item_code AS id,
            upper(any(m.product)) AS product_line,
            upper(any(if(m.brand != '', m.brand, 'GENERAL'))) AS brand,
            any(toFloat64(m.mop)) AS unit_mop,
            any(toFloat64(m.mrp)) AS unit_mrp,
            greatest(0.0, sumIf({net_q}, {age} >= 0 AND {age} <= 6)) AS d7,
            {", ".join(exprs)},
            countDistinctIf(s.branch, {age} >= 0 AND {age} <= 89 AND s.sold_price > 0) AS branch_breadth_90,
            countDistinctIf(s.branch, {age} >= 30 AND {age} <= 119 AND s.sold_price > 0) AS branch_breadth_prior
        FROM azure_sales_report s
        LEFT JOIN item_master m ON s.item_code = m.item_code
        WHERE s.date >= toDateTime('{start_135}') AND s.date < toDateTime('{end_dt}') AND {where_sql}
        GROUP BY s.item_code
    """


def _fetch_origin(ch, ref_dt: date, sales_where: List[str], cache_dir: Optional[str]) -> Dict[str, Any]:
    cp = os.path.join(cache_dir, f"origin_v2_{ref_dt}.pkl") if cache_dir else None
    if cp and os.path.exists(cp):
        with open(cp, "rb") as f:
            return pickle.load(f)
    prof = dis._compute_seasonal_and_festival_profile(ch, ref_dt, sales_where)
    bms = [float(x) for x in (prof.get("bucket_mult_sums") or [15.0] * 9)]
    rows = ch.query(_bucket_sql(ref_dt, " AND ".join(sales_where))).result_rows
    cols = ["id", "product_line", "brand", "unit_mop", "unit_mrp", "d7"]
    for k in range(9):
        cols += [f"u_b{k}", f"r_b{k}", f"a_b{k}"]
    cols += ["branch_breadth_90", "branch_breadth_prior"]
    df = pd.DataFrame(rows, columns=cols)
    for c in cols[3:]:
        df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0.0)
    df["product_line"] = df["product_line"].fillna("OTHER").astype(str)
    df["brand"] = df["brand"].fillna("GENERAL").astype(str)
    daily = fetch_daily_rows(ch, ref_dt, sales_where) if USE_DAILY_FEATURES else None
    out = {"ref": ref_dt, "bms": bms, "df": df, "daily": daily}
    if cp:
        with open(cp, "wb") as f:
            pickle.dump(out, f, protocol=pickle.HIGHEST_PROTOCOL)
    return out


def _samples(fr: Dict[str, Any], prod_cats: List[str], brand_cats: List[str]) -> Dict[str, Any]:
    """Supervised rows of one origin: features from buckets b1..b6, target = units of the newest bucket b0."""
    df = fr["df"]
    u_b = [df[f"u_b{k}"].values.astype(float) for k in range(9)]
    r_b = [df[f"r_b{k}"].values.astype(float) for k in range(9)]
    a_b = [df[f"a_b{k}"].values.astype(float) for k in range(9)]
    c_b = moderate_bucket_mults(fr["bms"], 9)
    br_live = df["branch_breadth_90"].values.astype(float)
    br_prior = df["branch_breadth_prior"].values.astype(float)
    cat_prod = pd.Categorical(df["product_line"].astype(str), categories=prod_cats)
    cat_brand = pd.Categorical(df["brand"].astype(str), categories=brand_cats)
    # fold origin = ref - 15d  ->  its "last 14 days" are ages 15..28 of the fetched daily window (k=1)
    daily = window_features(fr["daily"], df["id"].values, k=1)
    X, stat, tsb, aux = _build_fold_features(
        u_b[1:7], r_b[1:7], a_b[1:7], c_b[1:7], c_b[0],
        df["unit_mop"].values.astype(float), df["unit_mrp"].values.astype(float),
        np.maximum(br_prior, br_live * 0.85), cat_prod, cat_brand, daily=daily,
    )
    y = u_b[0]
    m = aux["d90"] > 0
    return {
        "X": X, "y": y, "stat": stat, "tsb": tsb, "mask": m, "eval_mask": m | (y > 0),
        "aux": {k: aux[k] for k in ("d90", "d30", "older_history", "active_60")},
        "ref": fr["ref"], "ids": df["id"].values,
    }


# ─────────────────────────────────────────────────────────────────────────────
# 2. Training
# ─────────────────────────────────────────────────────────────────────────────
def train_demand_model(
    ch,
    last_ref: date,
    n_origins: int = 36,
    holdout_origins: int = 2,
    cache_dir: Optional[str] = None,
    workers: int = 4,
    log: Callable[[str], None] = print,
    configs: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Train on `n_origins` fifteen-day origins ending at `last_ref` (= last day with sales data).
    Returns {'model': PretrainedDemandModel, 'report': {...}} (nothing is persisted here)."""
    if not HAS_LIGHTGBM:
        raise RuntimeError("lightgbm is not installed")
    t0 = time.time()
    sales_where = dis._build_sales_where_clauses(macro_category="", product_filter="", brand_filter="", sales_branch_codes=None)
    if cache_dir:
        os.makedirs(cache_dir, exist_ok=True)

    refs = sorted([last_ref - timedelta(days=STEP_DAYS * i) for i in range(n_origins)])
    log(f"Training origins: {len(refs)} x {STEP_DAYS}d, {refs[0]} .. {refs[-1]}  (holdout = last {holdout_origins})")

    frames: Dict[date, Dict[str, Any]] = {}
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futs = {pool.submit(_fetch_origin, ch, r, sales_where, cache_dir): r for r in refs}
        for i, f in enumerate(as_completed(futs), 1):
            r = futs[f]
            frames[r] = f.result()
            log(f"  [{i:2d}/{len(refs)}] origin {r}: {len(frames[r]['df']):,} SKUs  ({time.time() - t0:.0f}s)")

    prod_cats = sorted({p for fr in frames.values() for p in fr["df"]["product_line"].unique()})
    brand_cats = sorted({b for fr in frames.values() for b in fr["df"]["brand"].unique()})
    log(f"Categories: {len(prod_cats)} product lines, {len(brand_cats)} brands")

    S = [_samples(frames[r], prod_cats, brand_cats) for r in refs]
    train_S, hold_S = S[:-holdout_origins], S[-holdout_origins:]

    def stack(parts, key, mask_key="mask"):
        return np.concatenate([p[key][p[mask_key]] for p in parts])

    X_tr = pd.concat([p["X"].loc[p["mask"]] for p in train_S], ignore_index=True)
    y_tr, prior_tr = stack(train_S, "y"), stack(train_S, "stat")
    log(f"Fit set: {len(X_tr):,} SKU-origin rows from {len(train_S)} origins; holdout origins: {[str(p['ref']) for p in hold_S]}")

    X_h = pd.concat([p["X"].loc[p["eval_mask"]] for p in hold_S], ignore_index=True)
    y_h = stack(hold_S, "y", "eval_mask")
    stat_h = stack(hold_S, "stat", "eval_mask")
    tsb_h = stack(hold_S, "tsb", "eval_mask")
    aux_h = {k: np.concatenate([p["aux"][k][p["eval_mask"]] for p in hold_S]) for k in ("d90", "d30", "older_history", "active_60")}
    tiers_h = _classify_model_tiers(aux_h, "sku")

    results: Dict[str, Dict[str, Any]] = {}
    for name in (configs or list(CONFIGS)):
        t1 = time.time()
        bst, cal = _fit_tweedie_lgbm(X_tr, y_tr, prior_tr, config=CONFIGS[name])
        lg = _predict_tweedie_lgbm(bst, X_h, stat_h, cal)
        ml = _route_tier_predictions(tiers_h, lg, tsb_h, stat_h)
        bc = blend_and_calibrate(y_h, stat_h, ml)
        results[name] = {"bst": bst, "cal": cal, "bc": bc}
        log(f"  config {name:9s}: holdout WAPE stat {bc['stat_wape']:.2f} | ML {bc['ml_wape']:.2f} | hybrid {bc['hyb_wape']:.2f} "
            f"(w_ml={bc['best_w']:.2f}) [{time.time() - t1:.0f}s]")
    best_name = min(results, key=lambda k: results[k]["bc"]["hyb_wape"])
    bc = results[best_name]["bc"]
    log(f"Chosen config: {best_name}  -> holdout hybrid WAPE {bc['hyb_wape']:.2f}% (accuracy {100 - bc['hyb_wape']:.1f}%)")

    # Final refit on ALL origins (incl. the holdout ones) with the chosen configuration
    X_all = pd.concat([X_tr, pd.concat([p["X"].loc[p["mask"]] for p in hold_S], ignore_index=True)], ignore_index=True)
    y_all = np.concatenate([y_tr, stack(hold_S, "y")])
    prior_all = np.concatenate([prior_tr, stack(hold_S, "stat")])
    bst_full, cal_full = _fit_tweedie_lgbm(X_all, y_all, prior_all, config=CONFIGS[best_name])

    metrics = {
        "stat_wape": bc["stat_wape"], "stat_mape": bc["stat_mape"], "ml_wape": bc["ml_wape"], "ml_mape": bc["ml_mape"],
        "hyb_wape": bc["hyb_wape"], "hyb_mape": bc["hyb_mape"],
        "eval_entities": int(len(y_h)),
        "holdout_origins": [str(p["ref"]) for p in hold_S],
        "config_scores": {k: {"hybrid_wape": v["bc"]["hyb_wape"], "ml_wape": v["bc"]["ml_wape"]} for k, v in results.items()},
        "lgbm_version": LGBM_VERSION,
        "features": [f[0] for f in FEATURE_METADATA],
    }
    model = PretrainedDemandModel(
        booster_str=bst_full.model_to_string(), cal_factor=float(cal_full), best_w=float(bc["best_w"]),
        q80_scale=float(bc["q80_scale"]), q95_scale=float(bc["q95_scale"]),
        prod_cats=prod_cats, brand_cats=brand_cats, metrics=metrics,
        trained_at=datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"), ref_date=str(last_ref),
        n_samples=int(len(X_all)), n_origins=len(refs), gran="sku", config_name=best_name,
    )
    report = {
        "origins": len(refs), "first_origin": str(refs[0]), "last_origin": str(refs[-1]),
        "rows": int(len(X_all)), "chosen_config": best_name, "metrics": metrics,
        "seconds": round(time.time() - t0, 1), "num_trees": int(bst_full.num_trees()),
    }
    log(f"Done in {report['seconds']}s: {report['rows']:,} rows, {report['num_trees']} trees, "
        f"blend w_ml={model.best_w:.2f}, cal={model.cal_factor:.3f}")
    return {"model": model, "report": report}
