"""
Archetype 6 — Catalog Hygiene, Virtual VAS Attach-Rate & Paired Hardware Engine
================================================================================
Covers the 60+ non-retail, virtual VAS, promo-bundle, and 1:1 paired hardware lines
identified in the full 93-product ClickHouse catalog audit:

  1. Core Retail Catalog Hygiene Exclusions:
     - Moves `GDOT CARE` (₹40.18 Cr / 180d, 136K units, 0 physical stock) into
       `VIRTUAL_VAS_PRODUCTS` so it is excluded from Core Physical Retail stock
       KPIs and cleanly viewable under `Warranty & Protection (VAS)`.
     - Moves `OFFER KIT` (88K units @ ₹5 ASP), `LAPTOP BAG` (31K bundled units @
       ₹51 ASP), and `SMART CHOICE` (−₹41.05 Cr trade-in accounting rows) into
       `NON_RETAIL_PRODUCTS` so Core Retail WAPE, unit totals, and stockout alerts
       reflect true physical merchandise.
  2. Virtual VAS Attach-Rate & Zero-Stock Guard (when user selects VAS or `ALL_RAW`):
     - Computes live 90-day Hardware Attach Rates against `MOBILE`, `LAPTOP`, and
       `CE` appliances (e.g. `GDOT CARE` ~23.8% of Mobile, `OSG WARRANTY`,
       `LG AMC`, `CARE PLUS`, `PROTECT MAX`).
     - Suppresses false physical-stockout alerts & PO replenishment quantities on
       zero-stock intangible warranties (`projected_stockout_15d = False`,
       `replenish_qty_30d = 0`, `is_virtual_vas = True`).
  3. 1:1 Paired Hardware & Bundle Attach Metadata (`AC OUTDOOR`, `LAPTOP BAG`, `OFFER KIT`):
     - Tracks live `AC OUTDOOR / AIR CONDITIONER` pairing ratio (`~99.9%`) and
       `LAPTOP BAG / LAPTOP` attach ratio (`~55.8%`) when explicitly inspected.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any, Dict, Optional, Tuple

import numpy as np
import pandas as pd
from django.core.cache import cache

from analytics.clickhouse_service import get_active_engine, get_bypass_cache

# ── Archetype 6 Catalog Hygiene Additions ────────────────────────────────────
ARCHETYPE_6_EXTRA_NON_RETAIL: Tuple[str, ...] = (
    'OFFER KIT',
    'LAPTOP BAG',
    'SMART CHOICE',
)
ARCHETYPE_6_EXTRA_VIRTUAL_VAS: Tuple[str, ...] = (
    'GDOT CARE',
)

# Zero-warehouse-stock digital/service VAS products (never trigger physical stockouts/POs!)
ARCHETYPE_6_INTANGIBLE_VAS_SET = frozenset({
    'GDOT CARE', 'CARE PLUS', 'OSG WARRANTY', 'LG AMC', 'PROTECT MAX',
    'PROTECTION', 'GDOT', 'GDEW', 'EXTENDED WARRANTY', 'STELLR VOUCHERS',
    'MYG VERSE',
})

# All Virtual VAS + Derived/Paired lines eligible for Archetype 6 attach-rate metadata
ARCHETYPE_6_DERIVED_PRODUCTS: Tuple[str, ...] = (
    'GDOT CARE', 'OSG WARRANTY', 'LG AMC', 'CARE PLUS', 'PROTECT MAX',
    'PROTECTION', 'GDOT', 'GDEW', 'EXTENDED WARRANTY',
    'MOBILE ANTIVIRUS', 'TOTAL SECURITY', 'KASPERSKY', 'QUICKHEAL',
    'AC OUTDOOR', 'LAPTOP BAG', 'OFFER KIT',
)
ARCHETYPE_6_DERIVED_SET = frozenset(ARCHETYPE_6_DERIVED_PRODUCTS)

# Anchor hardware product(s) for each derived VAS / paired line
_ATTACH_DRIVER_MAP: Dict[str, Tuple[str, Tuple[str, ...]]] = {
    'GDOT CARE': ('Mobile', ('MOBILE',)),
    'CARE PLUS': ('Mobile', ('MOBILE',)),
    'MOBILE ANTIVIRUS': ('Mobile', ('MOBILE',)),
    'TOTAL SECURITY': ('Mobile+Laptop', ('MOBILE', 'LAPTOP')),
    'KASPERSKY': ('Laptop', ('LAPTOP',)),
    'QUICKHEAL': ('Laptop', ('LAPTOP',)),
    'OSG WARRANTY': ('Mobile+CE+Laptop', ('MOBILE', 'AIR CONDITIONER', 'TV', 'REFRIGERATORS', 'WASHING MACHINES', 'LAPTOP')),
    'PROTECT MAX': ('Mobile+CE', ('MOBILE', 'AIR CONDITIONER', 'TV', 'REFRIGERATORS', 'WASHING MACHINES')),
    'LG AMC': ('CE Appliances', ('AIR CONDITIONER', 'TV', 'REFRIGERATORS', 'WASHING MACHINES')),
    'AC OUTDOOR': ('AC Indoor', ('AIR CONDITIONER',)),
    'LAPTOP BAG': ('Laptop', ('LAPTOP',)),
    'OFFER KIT': ('Hardware Bundles', ('MOBILE', 'LAPTOP', 'AIR CONDITIONER', 'TV', 'REFRIGERATORS', 'WASHING MACHINES')),
}

_EXCLUDED_BRANCH_CODES = ('3GH', 'CSB', 'CSBW', 'HEAD OFFICE', 'UG SMART CHOICE')


def get_archetype6_attach_context(ch, ref_dt: date) -> Dict[str, Any]:
    """
    Fetch trailing 90-day unit totals for hardware drivers & VAS/paired products
    in a single fast ClickHouse scan (~150ms, cached 30m).
    Only invoked when the active DataFrame actually contains Archetype 6 derived rows,
    so Core Physical Retail mode incurs zero extra query overhead.
    """
    ref_str = ref_dt.strftime('%Y-%m-%d')
    cache_key = f"dii:arch6_attach_v1:{get_active_engine()}:{ref_str}"
    if not get_bypass_cache():
        cached = cache.get(cache_key)
        if cached is not None:
            return cached

    empty = {'u90_by_prod': {}, 'attach_pct_by_prod': {}, 'driver_label_by_prod': {}, 'ref_date': ref_str}
    if ch is None:
        return empty

    needed_prods = set(ARCHETYPE_6_DERIVED_SET)
    for _lbl, drivers in _ATTACH_DRIVER_MAP.values():
        needed_prods.update(drivers)

    prods_sql = "', '".join(sorted(needed_prods))
    excl_br_sql = "', '".join(_EXCLUDED_BRANCH_CODES)
    start_90 = (ref_dt - timedelta(days=89)).strftime('%Y-%m-%d 00:00:00')
    end_dt = (ref_dt + timedelta(days=1)).strftime('%Y-%m-%d 00:00:00')
    net_q = "if(s.sold_price < 0, -abs(toFloat64(s.qty)), abs(toFloat64(s.qty)))"

    sql = f"""
        SELECT
            upper(trim(m.product)) AS prod,
            greatest(0.0, sum({net_q})) AS u90
        FROM azure_sales_report s
        INNER JOIN item_master m ON s.item_code = m.item_code
        WHERE s.date >= toDateTime('{start_90}')
          AND s.date < toDateTime('{end_dt}')
          AND upper(trim(m.product)) IN ('{prods_sql}')
          AND s.item_code != ''
          AND upper(trim(s.branch)) NOT IN ('{excl_br_sql}')
          AND NOT (upper(s.invoice_no) LIKE '%SMC%' OR upper(s.invoice_no) LIKE '%EI%')
        GROUP BY prod
    """
    try:
        rows = ch.query(sql).result_rows
    except Exception as e:
        print('[archetype6] attach context query failed:', e)
        return empty

    u90_map = {str(r[0] or '').strip().upper(): float(r[1] or 0.0) for r in rows}
    attach_pct: Dict[str, float] = {}
    driver_lbl: Dict[str, str] = {}
    for prod, (lbl, drivers) in _ATTACH_DRIVER_MAP.items():
        drv_sum = sum(u90_map.get(d, 0.0) for d in drivers)
        p_u = u90_map.get(prod, 0.0)
        if drv_sum > 0.0 and p_u > 0.0:
            attach_pct[prod] = round((p_u / drv_sum) * 100.0, 1)
            driver_lbl[prod] = lbl

    ctx = {
        'u90_by_prod': u90_map,
        'attach_pct_by_prod': attach_pct,
        'driver_label_by_prod': driver_lbl,
        'ref_date': ref_str,
    }
    cache.set(cache_key, ctx, 1800)
    return ctx


def apply_archetype6_derived_intelligence(
    df: pd.DataFrame,
    attach_ctx: Optional[Dict[str, Any]],
) -> Tuple[pd.DataFrame, Dict[str, Any]]:
    """
    Annotate Virtual VAS, 1:1 Paired Hardware (`AC OUTDOOR`), and Promo Bundle lines
    with live hardware attach-rate context, and protect zero-stock intangible digital VAS
    plans from generating false physical-stockout or PO replenishment alerts.
    """
    n = len(df)
    arch6_active = np.zeros(n, dtype=bool)
    is_virtual_vas = np.zeros(n, dtype=bool)
    arch6_tags = [''] * n

    prods = df['product_line'].astype(str).tolist()
    stk_arr = df['current_stock'].astype(float).to_numpy(dtype=float, copy=False) if 'current_stock' in df.columns else np.zeros(n, dtype=float)
    attach_map = (attach_ctx or {}).get('attach_pct_by_prod') or {}
    drv_map = (attach_ctx or {}).get('driver_label_by_prod') or {}

    vas_guarded = 0
    paired_cnt = 0

    for i in range(n):
        p = prods[i].strip().upper()
        if p not in ARCHETYPE_6_DERIVED_SET:
            continue
        arch6_active[i] = True
        pct = attach_map.get(p)
        dlbl = drv_map.get(p, '')

        if p == 'AC OUTDOOR':
            paired_cnt += 1
            arch6_tags[i] = f"1:1 Paired w/ AC Indoor ({pct:.1f}%)" if pct is not None else "1:1 Paired w/ AC Indoor"
        elif p in ('LAPTOP BAG', 'OFFER KIT'):
            paired_cnt += 1
            arch6_tags[i] = f"Promo Bundle ({pct:.1f}% of {dlbl})" if (pct is not None and dlbl) else "Promo Bundle Attach"
        else:
            if p in ARCHETYPE_6_INTANGIBLE_VAS_SET and stk_arr[i] <= 0.0:
                is_virtual_vas[i] = True
                vas_guarded += 1
            arch6_tags[i] = f"VAS Attach ({pct:.1f}% of {dlbl})" if (pct is not None and dlbl) else "Virtual VAS Plan"

    df['archetype6_active'] = arch6_active
    df['archetype6_tag'] = arch6_tags
    df['is_virtual_vas'] = is_virtual_vas

    # Suppress false physical stockout & PO replenishment alerts on intangible zero-stock VAS
    if vas_guarded > 0:
        if 'projected_stockout_15d' in df.columns:
            df['projected_stockout_15d'] = np.where(is_virtual_vas, False, df['projected_stockout_15d'])
        if 'replenish_qty_30d' in df.columns:
            df['replenish_qty_30d'] = np.where(is_virtual_vas, 0.0, df['replenish_qty_30d'])

    diagnostics = {
        'enabled': True,
        'archetype': 'Archetype 6 · Catalog Hygiene, Virtual VAS Attach & 1:1 Paired Hardware',
        'hygiene_exclusions_added': list(ARCHETYPE_6_EXTRA_NON_RETAIL) + list(ARCHETYPE_6_EXTRA_VIRTUAL_VAS),
        'derived_entities_in_view': int(arch6_active.sum()),
        'virtual_vas_stockout_guarded': vas_guarded,
        'paired_or_bundle_entities': paired_cnt,
        'attach_rates_90d': attach_map,
    }
    return df, diagnostics
