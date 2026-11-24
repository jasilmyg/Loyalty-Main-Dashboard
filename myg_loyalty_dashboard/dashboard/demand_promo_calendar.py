"""
WhatsApp Campaign & Offer Calendar Engine (`demand_promo_calendar`)
===================================================================
Stores planned, live, and historical marketing offers (from myG's WhatsApp Channel,
offer posters, or marketing sheets) in ClickHouse table `demand_promo_calendar`
and feeds them directly into the Demand Intelligence forecasting engine:

  1. AI Offer Parser (`parse_whatsapp_offer`):
     Extracts structured campaign metadata (name, type, start/end dates, product lines,
     brands, offer strength, discount summary) from pasted WhatsApp text and/or
     uploaded offer banner/poster images via Gemini Vision (with deterministic
     regex fallback when offline or unconfigured).

  2. Forecast Integration (`build_promo_date_effects`):
     - Applies targeted daily lift multipliers on `[start_date .. end_date]` ONLY for
       matching product lines / brands / districts.
     - Applies empirical post-campaign pull-forward cooldown on big-ticket durables
       (TV, AC, Refrigerators, Washing Machines, Laptops, Home Theatre) for
       `post_cooldown_days` after the campaign ends.
     - Masks past campaign days from baseline calculation so short flash offers
       never contaminate steady-state velocity.
     - Emits chart pin markers so campaigns appear right alongside festivals.

  3. Realized Lift Measurement (`attach_realized_lifts`):
     Automatically measures actual incremental sales vs pre-campaign baseline in
     ClickHouse once a campaign's dates arrive.
"""

from __future__ import annotations

import base64
import difflib
import io
import json
import os
import re
import time
import uuid
import zipfile
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

import requests
from django.core.cache import cache

from analytics.clickhouse_service import get_active_engine, get_bypass_cache, get_ch_client
from analytics.portal_refresh import get_cache_epoch

PROMO_TABLE = "demand_promo_calendar"
_PROMO_TABLE_READY: Dict[str, bool] = {"clickhouse": False, "starrocks": False}

_PROMO_DDL_CH = f"""
    CREATE TABLE IF NOT EXISTS {PROMO_TABLE} (
        campaign_id        String,
        campaign_name      String,
        campaign_type      LowCardinality(String),
        start_date         Date,
        end_date           Date,
        product_lines      String,
        item_categories    String DEFAULT 'ALL',
        item_groups        String DEFAULT 'ALL',
        brands             String,
        district           String,
        store_formats      String DEFAULT 'ALL',
        branches           String DEFAULT 'ALL',
        offer_strength     LowCardinality(String),
        expected_lift_pct  Float32,
        post_cooldown_days UInt8,
        discount_summary   String,
        source_channel     LowCardinality(String),
        source_text        String,
        is_active          UInt8,
        created_by         String,
        updated_at         DateTime DEFAULT now()
    )
    ENGINE = MergeTree
    ORDER BY (campaign_id, updated_at)
"""

_PROMO_DDL_SR = f"""
    CREATE TABLE IF NOT EXISTS {PROMO_TABLE} (
        campaign_id        VARCHAR(64),
        campaign_name      STRING,
        campaign_type      VARCHAR(64),
        start_date         DATE,
        end_date           DATE,
        product_lines      STRING,
        item_categories    STRING,
        item_groups        STRING,
        brands             STRING,
        district           STRING,
        store_formats      STRING,
        branches           STRING,
        offer_strength     VARCHAR(32),
        expected_lift_pct  FLOAT,
        post_cooldown_days SMALLINT,
        discount_summary   STRING,
        source_channel     VARCHAR(32),
        source_text        STRING,
        is_active          TINYINT,
        created_by         VARCHAR(128),
        updated_at         DATETIME DEFAULT CURRENT_TIMESTAMP
    )
    ENGINE = OLAP
    DUPLICATE KEY(campaign_id, updated_at)
    DISTRIBUTED BY HASH(campaign_id) BUCKETS 2
    PROPERTIES ("replication_num" = "1")
"""

# Canonical `branch_master.district` values (exact spelling in ClickHouse)
CANONICAL_DISTRICTS: Tuple[str, ...] = (
    "KASARGOD", "KANNUR", "WAYANADU", "KOZHIKODE", "MALAPPURAM", "PALAKKAD",
    "THRISSUR", "ERNAKULAM", "IDUKKI", "KOTTAYAM", "ALAPPUZHA",
    "PATHANAMTHITTA", "KOLLAM", "THIRUVANANTHAPURAM",
    "DAKSHINA KANNADA", "MAHE", "CHAMRAJNAGAR",
)

# Common marketing poster spellings -> exact ClickHouse `branch_master.district`
DISTRICT_ALIAS_MAP: Dict[str, str] = {
    "CALICUT": "KOZHIKODE",
    "KOZHIKKODE": "KOZHIKODE",
    "CLT": "KOZHIKODE",
    "WAYANAD": "WAYANADU",
    "WYD": "WAYANADU",
    "KASARAGOD": "KASARGOD",
    "KASARGODE": "KASARGOD",
    "KASARAGODE": "KASARGOD",
    "KSD": "KASARGOD",
    "CANNANORE": "KANNUR",
    "KNR": "KANNUR",
    "MALAPURAM": "MALAPPURAM",
    "MLP": "MALAPPURAM",
    "PALGHAT": "PALAKKAD",
    "PKD": "PALAKKAD",
    "TRICHUR": "THRISSUR",
    "TCR": "THRISSUR",
    "COCHIN": "ERNAKULAM",
    "KOCHI": "ERNAKULAM",
    "EKM": "ERNAKULAM",
    "IDK": "IDUKKI",
    "KTM": "KOTTAYAM",
    "ALLEPPEY": "ALAPPUZHA",
    "ALPY": "ALAPPUZHA",
    "PTA": "PATHANAMTHITTA",
    "QUILON": "KOLLAM",
    "KLM": "KOLLAM",
    "TRIVANDRUM": "THIRUVANANTHAPURAM",
    "TVM": "THIRUVANANTHAPURAM",
    "MANGALORE": "DAKSHINA KANNADA",
    "MANGALURU": "DAKSHINA KANNADA",
}

STORE_FORMAT_LABELS: Dict[str, str] = {
    "ALL": "All Stores (myG + myG Future)",
    "MYG,MYG_FUTURE": "myG + myG Future",
    "MYG": "myG Stores Only",
    "MYG_FUTURE": "myG Future Stores Only",
}

# Durables exhibit pull-forward / post-promo hangover; consumables/accessories do not.
DURABLE_COOLDOWN_PRODUCTS: Set[str] = {
    "TV", "AIR CONDITIONER", "REFRIGERATORS", "WASHING MACHINES",
    "DISH WASHER", "FREEZER", "DRYER", "HOME THEATRE", "LAPTOP", "DESKTOP",
}

CAMPAIGN_TYPE_META: Dict[str, Dict[str, Any]] = {
    "mega_sale": {
        "label": "Mega Sale / Anniversary / Festive",
        "color": "#e11d48",
        "default_cooldown": 5,
        "lifts": {"low": 20.0, "medium": 40.0, "high": 70.0, "mega": 115.0},
        "ai_max_lift": 130.0,
    },
    "weekend_dhamaka": {
        "label": "Weekend / 2nd-Sat Dhamaka",
        "color": "#d97706",
        "default_cooldown": 3,
        "lifts": {"low": 10.0, "medium": 18.0, "high": 28.0, "mega": 40.0},
        "ai_max_lift": 45.0,
    },
    "exchange_cashback": {
        "label": "Exchange Mela / Bank Cashback / EMI",
        "color": "#0284c7",
        "default_cooldown": 3,
        "lifts": {"low": 8.0, "medium": 15.0, "high": 24.0, "mega": 35.0},
        "ai_max_lift": 40.0,
    },
    "brand_festival": {
        "label": "Brand Festival / Brand Days",
        "color": "#7c3aed",
        "default_cooldown": 3,
        "lifts": {"low": 10.0, "medium": 20.0, "high": 32.0, "mega": 45.0},
        "ai_max_lift": 50.0,
    },
    "flash_offer": {
        "label": "Flash Offer / Price Drop / Clearance",
        "color": "#ea580c",
        "default_cooldown": 2,
        "lifts": {"low": 10.0, "medium": 18.0, "high": 28.0, "mega": 40.0},
        "ai_max_lift": 45.0,
    },
    "product_launch": {
        "label": "Flagship / New Product Launch",
        "color": "#059669",
        "default_cooldown": 2,
        "lifts": {"low": 12.0, "medium": 22.0, "high": 35.0, "mega": 50.0},
        "ai_max_lift": 55.0,
    },
}

KNOWN_PRODUCT_LINES: Tuple[str, ...] = (
    "MOBILE", "TV", "AIR CONDITIONER", "REFRIGERATORS", "WASHING MACHINES",
    "LAPTOP", "TABLET", "SMALL APPLIANCES", "HOME APPLIANCES", "HOME THEATRE",
    "SMART WATCH", "EAR WEARABLES", "AUDIO", "ACCESSORIES", "CROCKERY",
    "MICROWAVE OVEN", "STABILIZER", "PRINTER", "DESKTOP", "GAMING", "CAMERA",
    "PERSONAL CARE", "STORAGE DEVICES", "IT ACCESSORIES", "FRAGRANCE", "GLAMSHIELD",
    "DISH WASHER", "FREEZER", "DRYER", "HOUSE HOLD", "RIG",
)

# Synonym mapping from WhatsApp marketing copy -> canonical myG `item_master.product`
_PRODUCT_SYNONYMS: List[Tuple[str, str]] = [
    (r"\b(smart\s*tv|led\s*tv|oled|qled|google\s*tv|4k\s*tv|tvs?|television)\b", "TV"),
    (r"\b(smart\s*phones?|mobiles?|phones?|iphones?|5g\s*phones?|handsets?)\b", "MOBILE"),
    (r"\b(air\s*conditioners?|split\s*ac|window\s*ac|inverter\s*ac|\bacs?\b)\b", "AIR CONDITIONER"),
    (r"\b(refrigerators?|fridges?|double\s*door|single\s*door|side\s*by\s*side)\b", "REFRIGERATORS"),
    (r"\b(washing\s*machines?|front\s*load|top\s*load|semi\s*auto)\b", "WASHING MACHINES"),
    (r"\b(laptops?|macbooks?|notebooks?|gaming\s*laptops?)\b", "LAPTOP"),
    (r"\b(tablets?|ipads?|galaxy\s*tab)\b", "TABLET"),
    (r"\b(home\s*theatres?|soundbars?|party\s*speakers?)\b", "HOME THEATRE"),
    (r"\b(smart\s*watch(?:es)?|apple\s*watch|galaxy\s*watch)\b", "SMART WATCH"),
    (r"\b(ear\s*buds?|airpods|tws|neckbands?|headphones?|earphones?|ear\s*wearables?)\b", "EAR WEARABLES"),
    (r"\b(microwaves?|ovens?|otg)\b", "MICROWAVE OVEN"),
    (r"\b(mixers?|grinders?|fans?|coolers?|irons?|kettles?|induction|small\s*appliances?)\b", "SMALL APPLIANCES"),
    (r"\b(inverters?|water\s*heaters?|geysers?|purifiers?|chimneys?|home\s*appliances?)\b", "HOME APPLIANCES"),
    (r"\b(crockery|cookware|cookers?|tawa|kadai)\b", "CROCKERY"),
    (r"\b(printers?)\b", "PRINTER"),
    (r"\b(speakers?|audio|bluetooth\s*speakers?)\b", "AUDIO"),
    (r"\b(accessories|chargers?|power\s*banks?|cables?)\b", "ACCESSORIES"),
    (r"\b(perfumes?|fragrances?|my\s*parf)\b", "FRAGRANCE"),
    (r"\b(dish\s*washers?)\b", "DISH WASHER"),
    (r"\b(cameras?|dslr)\b", "CAMERA"),
    (r"\b(playstation|ps5|xbox|gaming)\b", "GAMING"),
]

_KNOWN_BRANDS: Tuple[str, ...] = (
    "APPLE", "SAMSUNG", "LG", "SONY", "VIVO", "OPPO", "REALME", "REDMI", "XIAOMI",
    "ONEPLUS", "MOTOROLA", "NOTHING", "GOOGLE", "IQOO", "POCO", "HONOR", "NOKIA",
    "HP", "DELL", "LENOVO", "ASUS", "ACER", "MSI", "EPSON", "CANON",
    "WHIRLPOOL", "HAIER", "GODREJ", "BOSCH", "IFB", "VOLTAS", "LLOYD", "DAIKIN",
    "BLUESTAR", "PANASONIC", "CARRIER", "O GENERAL", "MITSUBISHI", "HITACHI", "ONIDA",
    "TCL", "HISENSE", "VU", "IMPEX", "BPL", "KELVINATOR", "VIDEOCON", "LIEBHERR",
    "PHILIPS", "PREETHI", "PRESTIGE", "SUJATA", "BAJAJ", "HAVELLS", "CROMPTON",
    "USHA", "ORIENT", "V-GUARD", "VGUARD", "BOAT", "JBL", "MARSHALL", "BOSE",
    "NOISE", "FIRE-BOLTT", "AMAZFIT", "MY PARF",
)


def _sql_esc(val: Any) -> str:
    return str(val or "").replace("\\", "\\\\").replace("'", "''")


def _get_rw_client(ch=None):
    eng = get_active_engine()
    if eng == "clickhouse":
        from analytics.starrocks_service import get_ch_backup_client
        return "clickhouse", get_ch_backup_client()
    return "starrocks", (ch or get_ch_client())


def _ensure_table(eng: str, client) -> None:
    if _PROMO_TABLE_READY.get(eng):
        return
    if eng == "clickhouse":
        exists_rows = client.query(f"EXISTS TABLE {PROMO_TABLE}").result_rows
        if not (exists_rows and int(exists_rows[0][0]) == 1):
            client.command(_PROMO_DDL_CH)
        else:
            # Safe idempotent schema evolution for existing tables
            for col_ddl in (
                "store_formats String DEFAULT 'ALL'",
                "branches String DEFAULT 'ALL'",
                "item_categories String DEFAULT 'ALL'",
                "item_groups String DEFAULT 'ALL'",
            ):
                try:
                    client.command(f"ALTER TABLE {PROMO_TABLE} ADD COLUMN IF NOT EXISTS {col_ddl}")
                except Exception as e:
                    print(f"[promo-calendar] column check skipped ({col_ddl}):", e)
    else:
        client.command(_PROMO_DDL_SR)
        for col_ddl in (
            "store_formats STRING DEFAULT 'ALL'",
            "branches STRING DEFAULT 'ALL'",
            "item_categories STRING DEFAULT 'ALL'",
            "item_groups STRING DEFAULT 'ALL'",
        ):
            try:
                client.command(f"ALTER TABLE {PROMO_TABLE} ADD COLUMN {col_ddl}")
            except Exception:
                pass
    _PROMO_TABLE_READY[eng] = True


_ITEM_CAT_CATALOG_CACHE: Tuple[float, List[Dict[str, str]]] = (0.0, [])


def get_item_categories_catalog(ch=None, limit: int = 600) -> List[Dict[str, str]]:
    """Return active canonical `item_master.item_category` values with parent `product` (60-min cached)."""
    global _ITEM_CAT_CATALOG_CACHE
    now_ts = time.time()
    c_ts, c_rows = _ITEM_CAT_CATALOG_CACHE
    if c_rows and (now_ts - c_ts) < 3600:
        return c_rows[:limit]
    try:
        client = ch or get_ch_client()
        if client is None:
            return []
        res = client.query(f"""
            SELECT
                upper(trim(item_category)) AS cat,
                any(upper(trim(product))) AS prod,
                count() AS sku_cnt
            FROM item_master
            WHERE trim(item_category) != ''
              AND upper(trim(item_category)) NOT IN ('NONE', 'NULL', 'N/A', 'DEFAULT')
            GROUP BY cat
            ORDER BY sku_cnt DESC, cat ASC
            LIMIT {max(50, int(limit))}
        """).result_rows
        rows = [
            {"item_category": str(r[0] or "").strip().upper(), "product": str(r[1] or "").strip().upper(), "sku_count": int(r[2] or 0)}
            for r in (res or ())
            if str(r[0] or "").strip()
        ]
        if rows:
            _ITEM_CAT_CATALOG_CACHE = (now_ts, rows)
        return rows
    except Exception as e:
        print("[promo-calendar] get_item_categories_catalog skipped:", e)
        return c_rows[:limit] if c_rows else []


def search_item_groups_catalog(
    query: str = "",
    product_lines: Any = None,
    item_categories: Any = None,
    limit: int = 60,
    ch=None,
) -> List[Dict[str, str]]:
    """Fast live ClickHouse typeahead over `item_master.item_group` filtered by query, product & item_category."""
    try:
        client = ch or get_ch_client()
        if client is None:
            return []
        clauses = [
            "trim(item_group) != ''",
            "upper(trim(item_group)) NOT IN ('NONE', 'NULL', 'N/A', 'DEFAULT', 'DEFAULT1', 'DEFAULT2')",
        ]
        q_clean = re.sub(r"\s+", " ", str(query or "").strip().upper())
        if q_clean:
            for tok in q_clean.split(" ")[:4]:
                if tok:
                    clauses.append(f"positionCaseInsensitive(item_group, '{_sql_esc(tok)}') > 0")
        prods_norm = _normalize_csv_tokens(product_lines, "ALL")
        if prods_norm != "ALL":
            ps = "', '".join(_sql_esc(x) for x in prods_norm.split(","))
            clauses.append(f"upper(trim(product)) IN ('{ps}')")
        cats_norm = _normalize_csv_tokens(item_categories, "ALL")
        if cats_norm != "ALL":
            cs = "', '".join(_sql_esc(x) for x in cats_norm.split(","))
            clauses.append(f"upper(trim(item_category)) IN ('{cs}')")
        lim = max(10, min(120, int(limit or 60)))
        res = client.query(f"""
            SELECT
                upper(trim(item_group)) AS grp,
                any(upper(trim(product))) AS prod,
                any(upper(trim(item_category))) AS cat,
                count() AS sku_cnt
            FROM item_master
            WHERE {' AND '.join(clauses)}
            GROUP BY grp
            ORDER BY sku_cnt DESC, grp ASC
            LIMIT {lim}
        """).result_rows
        return [
            {
                "item_group": str(r[0] or "").strip().upper(),
                "product": str(r[1] or "").strip().upper(),
                "item_category": str(r[2] or "").strip().upper(),
                "sku_count": int(r[3] or 0),
            }
            for r in (res or ())
            if str(r[0] or "").strip()
        ]
    except Exception as e:
        print("[promo-calendar] search_item_groups_catalog skipped:", e)
        return []


def normalize_districts(raw: Any) -> str:
    """Normalize single or comma-separated districts to canonical `branch_master.district` names or 'ALL'."""
    if raw is None:
        return "ALL"
    if isinstance(raw, (list, tuple, set)):
        tokens = [str(x or "").strip().upper() for x in raw]
    else:
        tokens = [x.strip().upper() for x in re.split(r"[,;|/]+", str(raw))]
    clean: List[str] = []
    seen: Set[str] = set()
    for t in tokens:
        if not t or t in ("NONE", "NULL"):
            continue
        if t in ("ALL", "ALL DISTRICTS", "ALL KERALA", "KERALA", "PAN KERALA", "*"):
            return "ALL"
        canon = DISTRICT_ALIAS_MAP.get(t, t)
        if canon not in seen:
            seen.add(canon)
            clean.append(canon)
    return ",".join(clean) if clean else "ALL"


def normalize_store_formats(raw: Any) -> str:
    """Normalize store format scope to 'ALL', 'MYG', 'MYG_FUTURE', or 'MYG,MYG_FUTURE'."""
    if raw is None:
        return "ALL"
    if isinstance(raw, (list, tuple, set)):
        tokens = [str(x or "").strip().upper() for x in raw]
    else:
        tokens = [x.strip().upper() for x in re.split(r"[,;|/+&]+", str(raw))]
    has_myg = False
    has_future = False
    for t in tokens:
        if not t or t in ("NONE", "NULL"):
            continue
        if t in ("ALL", "ALL STORES", "ALL BRANCHES", "BOTH", "*"):
            return "ALL"
        if "FUTURE" in t:
            has_future = True
        elif t in ("MYG", "MYG STORE", "MYG STORES", "MYG DIGITAL", "DIGITAL", "STANDARD"):
            has_myg = True
    if has_myg and has_future:
        return "MYG,MYG_FUTURE"
    if has_future:
        return "MYG_FUTURE"
    if has_myg:
        return "MYG"
    return "ALL"


def store_format_label(fmt: str) -> str:
    norm = normalize_store_formats(fmt)
    return STORE_FORMAT_LABELS.get(norm, norm)


def normalize_branches(raw: Any) -> str:
    """Normalize target specific store names/codes to uppercase CSV or 'ALL'."""
    if raw is None:
        return "ALL"
    if isinstance(raw, (list, tuple, set)):
        tokens = [str(x or "").strip().upper() for x in raw]
    else:
        tokens = [x.strip().upper() for x in re.split(r"[,;|/]+", str(raw))]
    clean: List[str] = []
    seen: Set[str] = set()
    for t in tokens:
        t = re.sub(r"\s+", " ", t).strip()
        if not t or t in ("NONE", "NULL", "-"):
            continue
        if t in ("ALL", "ALL STORES", "ALL BRANCHES", "ALL SHOWROOMS", "*"):
            return "ALL"
        if t not in seen:
            seen.add(t)
            clean.append(t)
    return ",".join(clean) if clean else "ALL"


def _get_branch_catalog_cached(ch=None) -> List[Tuple[str, str, str]]:
    """Return cached list of `(code_upper, branch_name_upper, district_upper)` for active retail branches."""
    bm_key = f"dii:promo_bm_cat:v2:{get_active_engine()}"
    cached = None if get_bypass_cache() else cache.get(bm_key)
    if cached is not None:
        return cached
    try:
        client = ch or get_ch_client()
        if client is None:
            return []
        raw = client.query("""
            SELECT upper(trim(code)), upper(trim(branch_name)), upper(trim(district))
            FROM branch_master
            WHERE upper(trim(store_type)) = 'BRANCH' AND trim(code) != ''
            ORDER BY upper(trim(branch_name)) ASC
        """).result_rows
        rows = [(str(r[0] or ""), str(r[1] or ""), str(r[2] or "")) for r in raw if r[0] or r[1]]
        if rows:
            cache.set(bm_key, rows, 1800)
        return rows
    except Exception as e:
        print("[promo-calendar] branch catalog lookup skipped:", e)
        return []


def expand_branches_for_scope(
    districts_norm: str = "ALL",
    store_formats: str = "ALL",
    ch=None,
) -> List[str]:
    """Return sorted canonical `branch_master.branch_name` list matching `(districts_norm, store_formats)`."""
    catalog = _get_branch_catalog_cached(ch=ch)
    if not catalog:
        return []
    d_norm = normalize_districts(districts_norm)
    d_set = set() if d_norm == "ALL" else {x for x in d_norm.split(",") if x}
    sfmt = normalize_store_formats(store_formats)
    matched: List[str] = []
    seen: Set[str] = set()
    for _code, bname, bdist in catalog:
        if not bname:
            continue
        if d_set and bdist not in d_set:
            continue
        is_fut = "FUTURE" in bname
        if sfmt == "MYG_FUTURE" and not is_fut:
            continue
        if sfmt == "MYG" and is_fut:
            continue
        if bname not in seen:
            seen.add(bname)
            matched.append(bname)
    return matched


def resolve_canonical_branches(
    raw: Any,
    store_formats: str = "ALL",
    districts_norm: str = "ALL",
    from_parser: bool = False,
    ch=None,
) -> str:
    """Snap store names (e.g. 'myG Future Edappally', 'Balussery', or branch codes) to canonical
    `branch_master.branch_name`. When `from_parser=True`, also guards against district-bar echoes
    and applies `myG` / `myG Future` format disambiguation for town names."""
    norm = normalize_branches(raw)
    if norm == "ALL":
        return "ALL"

    raw_tokens = [x.strip().upper() for x in norm.split(",") if x.strip()]
    sfmt = normalize_store_formats(store_formats)
    d_norm = normalize_districts(districts_norm)
    d_set = set() if d_norm == "ALL" else {x for x in d_norm.split(",") if x}

    # Parser-only guard: if Gemini echoed the district footer list (e.g. ['KANNUR','KASARGOD','CALICUT',...])
    # into `branches` without distinct showroom/mall/town names, treat as district-level so all
    # branches in those districts get expanded cleanly instead of picking only town-named branches.
    if from_parser and d_set and raw_tokens:
        all_are_districts = True
        norm_tok_dists: Set[str] = set()
        for t in raw_tokens:
            if "FUTURE" in t:
                all_are_districts = False
                break
            t_clean = re.sub(r"\b(MYG|STORES?|SHOWROOMS?|BRANCH(?:ES)?|DISTRICTS?)\b", "", t).strip()
            t_canon = DISTRICT_ALIAS_MAP.get(t_clean, t_clean)
            if t_canon not in d_set:
                all_are_districts = False
                break
            norm_tok_dists.add(t_canon)
        if all_are_districts and (len(raw_tokens) >= 2 or norm_tok_dists == d_set):
            return "ALL"

    catalog = _get_branch_catalog_cached(ch=ch)
    if not catalog:
        return norm
    by_name = {bn: bn for _c, bn, _d in catalog if bn}
    by_code = {c: bn for c, bn, _d in catalog if c and bn}
    resolved: List[str] = []
    seen: Set[str] = set()

    def _add(name_val: str) -> None:
        if name_val and name_val not in seen:
            seen.add(name_val)
            resolved.append(name_val)

    for cand in raw_tokens:
        if (not from_parser) and (cand in by_name or cand in by_code):
            _add(by_name.get(cand) or by_code[cand])
            continue

        cleaned = re.sub(r"\b(STORES?|SHOWROOMS?|BRANCH(?:ES)?|OUTLETS?)\b", "", cand).strip()
        cleaned = re.sub(r"\s+", " ", cleaned)
        tok_has_future = "FUTURE" in cleaned
        core = re.sub(r"\bMYG\b|\bFUTURE\b", "", cleaned).strip()
        core = re.sub(r"\s+", " ", core)

        # When parsing a poster that targets BOTH myG & myG Future for a named town
        # (e.g. "Visit myG, myG Future @ Balussery"), include both standard & Future branches if both exist.
        if from_parser and (not tok_has_future) and sfmt == "MYG,MYG_FUTURE" and core and (core in by_name) and (f"{core} FUTURE" in by_name):
            _add(by_name[core])
            _add(by_name[f"{core} FUTURE"])
            continue

        # When parsing a MYG_FUTURE-only poster where token omitted "FUTURE", prefer "<CORE> FUTURE"
        if from_parser and (not tok_has_future) and sfmt == "MYG_FUTURE" and core and (f"{core} FUTURE" in by_name):
            _add(by_name[f"{core} FUTURE"])
            continue

        hit = by_name.get(cand) or by_code.get(cand)
        if not hit:
            prefer_future = tok_has_future or (from_parser and sfmt == "MYG_FUTURE")
            trials = [cleaned]
            if core:
                if prefer_future:
                    trials.extend([f"{core} FUTURE", f"FUTURE {core}", core])
                else:
                    trials.extend([core, f"{core} FUTURE"])
            for tr in trials:
                if tr in by_name:
                    hit = by_name[tr]
                    break
            if not hit and core and len(core) >= 4:
                sub_hits = [
                    bn for _c, bn, _d in catalog
                    if core in bn and (("FUTURE" in bn) == prefer_future)
                ]
                if not sub_hits and not tok_has_future and sfmt in ("ALL", "MYG,MYG_FUTURE"):
                    sub_hits = [bn for _c, bn, _d in catalog if core in bn]
                if len(sub_hits) == 1:
                    hit = sub_hits[0]
                elif from_parser and len(sub_hits) > 1 and sfmt == "MYG,MYG_FUTURE" and len(sub_hits) <= 2:
                    for sh in sub_hits:
                        _add(sh)
                    continue
            # Fuzzy fallback for minor OCR/poster spelling variations (e.g. "EDAPALLY FUTURE" -> "EDAPPALLY FUTURE")
            if not hit and len(cleaned) >= 4:
                same_fmt_names = [bn for _c, bn, _d in catalog if ("FUTURE" in bn) == prefer_future]
                pool_names = same_fmt_names or [bn for _c, bn, _d in catalog]
                probe = f"{core} FUTURE" if (prefer_future and core and "FUTURE" not in core) else (core or cleaned)
                close = difflib.get_close_matches(probe, pool_names, n=1, cutoff=0.82)
                if not close and pool_names is not same_fmt_names:
                    close = difflib.get_close_matches(probe, [bn for _c, bn, _d in catalog], n=1, cutoff=0.84)
                if close:
                    hit = close[0]
        _add(hit or cand)
    return ",".join(resolved) if resolved else "ALL"


def resolve_effective_branches(
    raw_branches: Any,
    districts_norm: str = "ALL",
    store_formats: str = "ALL",
    ch=None,
) -> Tuple[str, List[str], str]:
    """
    Apply store-resolution priority for extracted offers:
      1. If specific store(s) are explicitly specified -> take ONLY those stores (checking myG / myG Future).
      2. Else if district(s) are given -> expand to ALL stores in those district(s) matching `store_formats`
         (`MYG` = myG only, `MYG_FUTURE` = myG Future only, `ALL`/`MYG,MYG_FUTURE` = both).
      3. Else -> `('ALL', ['ALL'], 'all_stores')`.
    """
    d_norm = normalize_districts(districts_norm)
    sfmt = normalize_store_formats(store_formats)
    explicit = resolve_canonical_branches(
        raw_branches,
        store_formats=sfmt,
        districts_norm=d_norm,
        from_parser=True,
        ch=ch,
    )
    if explicit != "ALL":
        br_list = [x for x in explicit.split(",") if x]
        return explicit, br_list, "explicit_stores"
    if d_norm != "ALL":
        expanded = expand_branches_for_scope(d_norm, store_formats=sfmt, ch=ch)
        if expanded:
            return ",".join(expanded), expanded, "district_expanded"
    return "ALL", ["ALL"], "all_stores"


def default_lift_for(campaign_type: str, offer_strength: str) -> float:
    ct = CAMPAIGN_TYPE_META.get((campaign_type or "").strip().lower(), CAMPAIGN_TYPE_META["weekend_dhamaka"])
    st = (offer_strength or "medium").strip().lower()
    return float(ct["lifts"].get(st, ct["lifts"]["medium"]))


def default_cooldown_for(campaign_type: str) -> int:
    ct = CAMPAIGN_TYPE_META.get((campaign_type or "").strip().lower(), CAMPAIGN_TYPE_META["weekend_dhamaka"])
    return int(ct["default_cooldown"])


_TRUE_MEGA_EVENT_RE = re.compile(
    r"\b(anniversary|onam|vishu|diwali|deepavali|christmas|xmas|new\s*year|"
    r"republic\s*day|independence\s*day|freedom\s*sale|eid|ramzan|ramadan|bakrid|"
    r"grand\s*opening|inauguration|relaunch|black\s*friday)\b",
    re.I,
)


def calibrate_ai_expected_lift(
    raw_lift: float,
    campaign_type: str,
    offer_strength: str,
    campaign_name: str = "",
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
) -> Tuple[str, float]:
    """
    Ground AI/rule-extracted Expected Daily Lift (%) in myG's empirical retail response curves.
    Returns `(calibrated_strength, calibrated_lift_pct)`:
      - Prevents poster marketing hyperbole ('killer pricing', 'Up to 75% off', 'Power 48') from
        inflating routine weekly/48-hr promos to +120%..+140%.
      - Applies a 0.80 weekday-only factor when a routine promo runs strictly Mon-Thu.
      - Preserves +60%..+130% lifts strictly for genuine annual storewide mega events
        (Onam, Vishu, Anniversary, Deepavali, Christmas, Grand Opening, etc.).
    Note: Manual planner edits in `save_campaign()` remain unbounded up to 400%.
    """
    ctype = (campaign_type or "weekend_dhamaka").strip().lower()
    meta = CAMPAIGN_TYPE_META.get(ctype, CAMPAIGN_TYPE_META["weekend_dhamaka"])
    strength = (offer_strength or "medium").strip().lower()
    if strength not in ("low", "medium", "high", "mega"):
        strength = "medium"

    is_true_mega = bool(_TRUE_MEGA_EVENT_RE.search(str(campaign_name or "")))
    eff_strength = strength
    if strength == "mega" and not is_true_mega:
        if ctype in ("flash_offer", "weekend_dhamaka", "exchange_cashback", "brand_festival", "product_launch"):
            eff_strength = "high"

    base_default = default_lift_for(ctype, eff_strength)
    type_cap = float(meta.get("ai_max_lift", 45.0))
    if ctype == "mega_sale" and not is_true_mega:
        dur_days = ((end_date - start_date).days + 1) if (start_date and end_date and end_date >= start_date) else 3
        type_cap = 55.0 if dur_days <= 3 else 65.0
        if strength == "mega":
            eff_strength = "high"
            base_default = min(base_default, 52.0)

    try:
        raw = float(raw_lift or 0.0)
    except Exception:
        raw = 0.0

    if raw <= 0:
        lift = base_default
    else:
        raw_clamped = max(5.0, min(raw, type_cap))
        lift = (0.55 * base_default) + (0.45 * raw_clamped)

    # Routine Mon-Thu weekday-only offers carry ~20% lower incremental surge than Fri-Sun promos
    if start_date and not is_true_mega:
        ed = end_date if (end_date and end_date >= start_date) else start_date
        span = min(7, (ed - start_date).days + 1)
        has_fri_to_sun = any((start_date + timedelta(days=i)).weekday() >= 4 for i in range(span))
        if not has_fri_to_sun:
            lift *= 0.80

    lift = max(5.0, min(type_cap, lift))
    return eff_strength, float(int(round(lift)))


_STORE_REV_PROFILE_CACHE: Dict[Tuple[Any, ...], Tuple[float, Dict[str, Any]]] = {}
_LATEST_SALES_DT_CACHE: Tuple[float, Optional[date]] = (0.0, None)


def _fmt_inr_short(val: float) -> str:
    a = abs(float(val or 0.0))
    if a >= 1e7:
        return f"₹{a / 1e7:.2f}Cr"
    if a >= 1e5:
        return f"₹{a / 1e5:.1f}L"
    if a >= 1e3:
        return f"₹{a / 1e3:.1f}k"
    return f"₹{a:,.0f}"


def _percentile_sorted(sorted_vals: Sequence[float], q: float) -> float:
    n = len(sorted_vals)
    if n == 0:
        return 0.0
    if n == 1:
        return float(sorted_vals[0])
    pos = max(0.0, min(1.0, float(q))) * (n - 1)
    lo = int(pos)
    hi = min(n - 1, lo + 1)
    frac = pos - lo
    return float(sorted_vals[lo]) * (1.0 - frac) + float(sorted_vals[hi]) * frac


def _get_latest_sales_date_cached(ch=None) -> date:
    global _LATEST_SALES_DT_CACHE
    now_ts = time.time()
    cached_ts, cached_dt = _LATEST_SALES_DT_CACHE
    if cached_dt is not None and (now_ts - cached_ts) < 900:
        return cached_dt
    try:
        client = ch or get_ch_client()
        if client is not None:
            res = client.query("SELECT max(toDate(date)) FROM azure_sales_report").result_rows
            if res and res[0] and res[0][0]:
                dt_val = _parse_iso_date(res[0][0])
                if dt_val:
                    _LATEST_SALES_DT_CACHE = (now_ts, dt_val)
                    return dt_val
    except Exception:
        pass
    return date.today()


def _fetch_store_sale_value_profile(
    ch,
    ref_anchor: date,
    camp_dows: Tuple[int, ...],
    branches_list: Sequence[str],
    districts_list: Sequence[str],
    store_formats: str,
    product_lines_list: Sequence[str],
    brands_list: Sequence[str],
    item_categories_list: Sequence[str] = (),
    item_groups_list: Sequence[str] = (),
) -> Optional[Dict[str, Any]]:
    """
    Query the store/category scope's COMPLETE ClickHouse Net Sale Value (₹) history up to
    `ref_anchor - 4d` (30-min cached) and compute quarter-detrended relative daily lifts (%)
    so multi-year store growth & seasonality never inflate promo elasticity.
    """
    cache_key = (
        "full_hist_v3",
        ref_anchor.strftime("%Y-%m-%d"),
        tuple(camp_dows),
        tuple(sorted(branches_list)),
        tuple(sorted(districts_list)),
        store_formats,
        tuple(sorted(product_lines_list)),
        tuple(sorted(brands_list)),
        tuple(sorted(item_categories_list)),
        tuple(sorted(item_groups_list)),
    )
    now_ts = time.time()
    mem = _STORE_REV_PROFILE_CACHE.get(cache_key)
    if mem and (now_ts - mem[0]) < 1800:
        return mem[1]

    if ch is None:
        return None
    try:
        from .demand_intelligence_service import _FESTIVAL_DATE_MAP
    except Exception:
        _FESTIVAL_DATE_MAP = {}

    win_end = ref_anchor - timedelta(days=4)
    recent_cutoff = win_end - timedelta(days=90)
    clauses = [
        "s.branch NOT IN ('3GH', 'CSB', 'CSBW')",
        "s.item_code != ''",
        "NOT (upper(s.invoice_no) LIKE '%SMC%' OR upper(s.invoice_no) LIKE '%EI%')",
    ]
    if product_lines_list:
        ps = "', '".join(_sql_esc(x) for x in product_lines_list)
        clauses.append(f"upper(trim(m.product)) IN ('{ps}')")
    if item_categories_list:
        cs = "', '".join(_sql_esc(x) for x in item_categories_list)
        clauses.append(f"upper(trim(m.item_category)) IN ('{cs}')")
    if item_groups_list:
        gs = "', '".join(_sql_esc(x) for x in item_groups_list)
        clauses.append(f"upper(trim(m.item_group)) IN ('{gs}')")
    if brands_list:
        bs = "', '".join(_sql_esc(x) for x in brands_list)
        clauses.append(f"upper(trim(m.brand)) IN ('{bs}')")

    need_bm = False
    if branches_list:
        need_bm = True
        br_sql = "', '".join(_sql_esc(x) for x in branches_list)
        clauses.append(f"(upper(trim(bm.branch_name)) IN ('{br_sql}') OR upper(trim(bm.code)) IN ('{br_sql}'))")
    else:
        if districts_list:
            need_bm = True
            ds_sql = "', '".join(_sql_esc(x) for x in districts_list)
            clauses.append(f"upper(trim(bm.district)) IN ('{ds_sql}')")
        if store_formats == "MYG_FUTURE":
            need_bm = True
            clauses.append("upper(bm.branch_name) LIKE '%FUTURE%'")
        elif store_formats == "MYG":
            need_bm = True
            clauses.append("upper(bm.branch_name) NOT LIKE '%FUTURE%'")

    bm_join = "INNER JOIN branch_master bm ON s.branch = bm.code" if need_bm else ""
    sql = f"""
        SELECT
            toDate(s.date) AS dt,
            sum(toFloat64(s.sold_price)) AS net_rev
        FROM azure_sales_report s
        INNER JOIN item_master m ON s.item_code = m.item_code
        {bm_join}
        WHERE s.date < toDateTime('{(win_end + timedelta(days=1))} 00:00:00')
          AND {' AND '.join(clauses)}
        GROUP BY dt
        ORDER BY dt
    """
    res = ch.query(sql).result_rows

    dow_set = set(camp_dows)
    qtr_same: Dict[Tuple[int, int], List[float]] = {}
    yr_same: Dict[int, List[float]] = {}
    qtr_all: Dict[Tuple[int, int], List[float]] = {}
    yr_all: Dict[int, List[float]] = {}
    fest_obs: List[Tuple[date, float]] = []
    recent_same: List[float] = []
    recent_all: List[float] = []
    first_dt: Optional[date] = None
    last_dt: Optional[date] = None

    for row in (res or ()):
        d_val = _parse_iso_date(row[0])
        rev = max(0.0, float(row[1] or 0.0))
        if not d_val or rev <= 0.0:
            continue
        if first_dt is None:
            first_dt = d_val
        last_dt = d_val
        qk = (d_val.year, (d_val.month - 1) // 3 + 1)
        is_fest = bool(_FESTIVAL_DATE_MAP.get(d_val, {}).get("is_core_surge", False))
        if is_fest:
            fest_obs.append((d_val, rev))
        else:
            qtr_all.setdefault(qk, []).append(rev)
            yr_all.setdefault(d_val.year, []).append(rev)
            if d_val >= recent_cutoff:
                recent_all.append(rev)
            if d_val.weekday() in dow_set:
                qtr_same.setdefault(qk, []).append(rev)
                yr_same.setdefault(d_val.year, []).append(rev)
                if d_val >= recent_cutoff:
                    recent_same.append(rev)

    def _detrended_lifts(q_map: Dict[Tuple[int, int], List[float]], y_map: Dict[int, List[float]]) -> List[float]:
        out_lifts: List[float] = []
        for qk, vals in q_map.items():
            if len(vals) >= 6:
                base_m = sum(vals) / len(vals)
            else:
                y_vals = y_map.get(qk[0], [])
                base_m = (sum(y_vals) / len(y_vals)) if len(y_vals) >= 6 else 0.0
            if base_m > 500.0:
                for v in vals:
                    out_lifts.append(((v - base_m) / base_m) * 100.0)
        out_lifts.sort()
        return out_lifts

    clean_same_lifts = _detrended_lifts(qtr_same, yr_same)
    clean_all_lifts = _detrended_lifts(qtr_all, yr_all)

    # Festival surges measured against each festival's own year/quarter clean baseline (across ALL years!)
    fest_lifts: List[float] = []
    for f_dt, f_rev in fest_obs:
        qk = (f_dt.year, (f_dt.month - 1) // 3 + 1)
        q_vals = qtr_all.get(qk, [])
        if len(q_vals) >= 14:
            base_m = sum(q_vals) / len(q_vals)
        else:
            y_vals = yr_all.get(f_dt.year, [])
            base_m = (sum(y_vals) / len(y_vals)) if len(y_vals) >= 14 else 0.0
        if base_m > 500.0:
            fest_lifts.append(((f_rev - base_m) / base_m) * 100.0)
    fest_lifts.sort()

    # Current run-rate daily baseline ₹ (recent 90d clean same-weekday, fallback latest available clean days)
    if len(recent_same) >= 5:
        current_base_rev = sum(recent_same) / len(recent_same)
    elif len(recent_all) >= 10:
        current_base_rev = sum(recent_all) / len(recent_all)
    else:
        latest_q = max(qtr_same.keys()) if qtr_same else (max(qtr_all.keys()) if qtr_all else None)
        fallback_vals = (qtr_same.get(latest_q) if latest_q in qtr_same else qtr_all.get(latest_q, [])) if latest_q else []
        current_base_rev = (sum(fallback_vals) / len(fallback_vals)) if fallback_vals else 0.0

    prof = {
        "clean_same_lifts": clean_same_lifts,
        "clean_all_lifts": clean_all_lifts,
        "fest_lifts": fest_lifts,
        "current_base_rev": current_base_rev,
        "first_year": first_dt.year if first_dt else None,
        "last_year": last_dt.year if last_dt else None,
    }
    if len(_STORE_REV_PROFILE_CACHE) > 256:
        _STORE_REV_PROFILE_CACHE.clear()
    _STORE_REV_PROFILE_CACHE[cache_key] = (now_ts, prof)
    return prof


_REALIZED_LIFT_CACHE: Dict[Tuple[str, str], Tuple[float, Dict[str, Any]]] = {}
_COMPLETED_LEARN_CACHE: Tuple[float, str, List[Dict[str, Any]]] = (0.0, "", [])


def _is_weekday_only_span(sd: Optional[date], ed: Optional[date]) -> bool:
    if not sd:
        return False
    end_d = ed if (ed and ed >= sd) else sd
    span = min(7, (end_d - sd).days + 1)
    return not any((sd + timedelta(days=i)).weekday() >= 4 for i in range(span))


def _token_overlap_score(a_set: Set[str], b_set: Set[str]) -> float:
    if not a_set and not b_set:
        return 1.0
    if not a_set or not b_set:
        return 0.45
    inter = len(a_set & b_set)
    if inter == 0:
        return 0.0
    return inter / max(1, len(a_set | b_set))


def _learn_lift_from_prior_campaigns(
    target_start: date,
    target_end: date,
    ctype: str,
    sfmt_norm: str,
    br_set: Set[str],
    prod_set: Set[str],
    cat_set: Set[str],
    grp_set: Set[str],
    type_cap: float,
    prior_completed: Sequence[Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    """
    Hierarchical Empirical Learner over strictly earlier completed campaigns (`prior.end_date < target_start`,
    zero leakage). Matches by Store x Item Group x Item Category x Product x Campaign Type x Day-type.
    """
    if not prior_completed:
        return None
    target_wd_only = _is_weekday_only_span(target_start, target_end)

    weighted_sum = 0.0
    total_w = 0.0
    matched_cnt = 0

    for pc in prior_completed:
        p_ed = _parse_iso_date(pc.get("end_date"))
        p_sd = _parse_iso_date(pc.get("start_date"))
        rl = pc.get("realized_lift_pct")
        if not p_ed or not p_sd or p_ed >= target_start or rl is None:
            continue
        try:
            rl_val = float(rl)
        except Exception:
            continue
        # Clamp extreme single-event glitches so one outage doesn't poison the learner
        rl_clamped = max(-35.0, min(type_cap, rl_val))

        # 1. Store similarity
        p_brs = {x.strip().upper() for x in (pc.get("branches_list") or []) if x and x != "ALL"}
        if br_set and p_brs:
            ov = _token_overlap_score(br_set, p_brs)
            store_w = (1.6 * ov) if ov > 0 else (0.55 if pc.get("store_formats") == sfmt_norm else 0.30)
        elif pc.get("store_formats") == sfmt_norm:
            store_w = 0.75
        else:
            store_w = 0.40

        # 2. 3-level Item Hierarchy similarity (item_group > item_category > product_line)
        p_grps = {x.strip().upper() for x in (pc.get("item_groups_list") or []) if x and x != "ALL"}
        p_cats = {x.strip().upper() for x in (pc.get("item_categories_list") or []) if x and x != "ALL"}
        p_prods = {x.strip().upper() for x in (pc.get("product_lines_list") or []) if x and x != "ALL"}

        item_w = 0.35
        if grp_set and p_grps:
            ov_g = _token_overlap_score(grp_set, p_grps)
            if ov_g > 0:
                item_w = 1.4 + 1.2 * ov_g
        elif cat_set and p_cats:
            ov_c = _token_overlap_score(cat_set, p_cats)
            if ov_c > 0:
                item_w = 1.1 + 0.9 * ov_c
        if item_w <= 0.40:
            ov_p = _token_overlap_score(prod_set, p_prods)
            item_w = max(item_w, (0.65 + 0.75 * ov_p) if ov_p > 0 else 0.25)

        # 3. Campaign type & weekday/weekend parity
        type_w = 1.5 if str(pc.get("campaign_type") or "").lower() == ctype else 0.45
        p_wd_only = _is_weekday_only_span(p_sd, p_ed)
        dow_w = 1.15 if (p_wd_only == target_wd_only) else 0.65

        # 4. Gentle recency decay across years
        age_days = max(1, (target_start - p_ed).days)
        rec_w = 0.90 ** (age_days / 365.0)

        w = store_w * item_w * type_w * dow_w * rec_w
        if w >= 0.18:
            weighted_sum += w * rl_clamped
            total_w += w
            matched_cnt += 1

    if matched_cnt == 0 or total_w < 0.55:
        return None

    learned_lift = weighted_sum / total_w
    # Credibility grows smoothly with matched prior campaigns (capped at 72% weight)
    w_learn = min(0.72, total_w / (total_w + 3.5))
    return {
        "learned_lift_pct": round(learned_lift, 1),
        "learned_weight": round(w_learn, 2),
        "matched_count": matched_cnt,
        "effective_weight_sum": round(total_w, 2),
    }


def _get_completed_campaigns_for_learning(ch, ref_dt: date) -> List[Dict[str, Any]]:
    """Return cached completed campaigns with ClickHouse realized Sale Value lifts for closed-loop learning."""
    global _COMPLETED_LEARN_CACHE
    now_ts = time.time()
    ref_iso = ref_dt.strftime("%Y-%m-%d")
    c_ts, c_ref, c_rows = _COMPLETED_LEARN_CACHE
    if c_ref == ref_iso and (now_ts - c_ts) < 900:
        return c_rows
    try:
        all_rows = list_campaigns(ch=ch, include_realized_lift=False, ref_dt=ref_dt)
        completed = [
            dict(r) for r in all_rows
            if _parse_iso_date(r.get("end_date")) and _parse_iso_date(r.get("end_date")) < ref_dt
        ]
        if completed:
            _attach_realized_lifts(ch, completed, ref_dt)
        _COMPLETED_LEARN_CACHE = (now_ts, ref_iso, completed)
        return completed
    except Exception:
        return []


def estimate_store_planned_lift_from_ch(
    campaign_type: str = "weekend_dhamaka",
    offer_strength: str = "medium",
    campaign_name: str = "",
    start_date: Any = None,
    end_date: Any = None,
    product_lines: Any = "ALL",
    brands: Any = "ALL",
    districts: Any = "ALL",
    store_formats: Any = "ALL",
    branches: Any = "ALL",
    item_categories: Any = "ALL",
    item_groups: Any = "ALL",
    raw_ai_lift: float = 0.0,
    ch=None,
    prior_completed: Optional[Sequence[Dict[str, Any]]] = None,
    use_learned_offers: bool = True,
) -> Dict[str, Any]:
    """
    Compute a store- & item-hierarchy-specific Sale Value (₹) Planned Lift % directly from:
      1. ClickHouse's COMPLETE multi-year store history (quarter-detrended across product / item_category / item_group)
      2. Closed-loop Empirical Learner over strictly earlier completed campaigns (`end_date < start_date`, zero leakage)
    """
    _DOW_SHORT = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
    ctype = (campaign_type or "weekend_dhamaka").strip().lower()
    if ctype not in CAMPAIGN_TYPE_META:
        ctype = "weekend_dhamaka"
    meta = CAMPAIGN_TYPE_META[ctype]

    sd = _parse_iso_date(start_date) or date.today()
    ed = _parse_iso_date(end_date) or sd
    if ed < sd:
        sd, ed = ed, sd

    eff_strength, clean_prior_lift = calibrate_ai_expected_lift(
        raw_lift=0.0,
        campaign_type=ctype,
        offer_strength=offer_strength,
        campaign_name=campaign_name,
        start_date=sd,
        end_date=ed,
    )
    _, fallback_lift = calibrate_ai_expected_lift(
        raw_lift=raw_ai_lift,
        campaign_type=ctype,
        offer_strength=offer_strength,
        campaign_name=campaign_name,
        start_date=sd,
        end_date=ed,
    )

    prods_norm = _normalize_csv_tokens(product_lines, "ALL")
    prods_list = [] if prods_norm == "ALL" else [x for x in prods_norm.split(",") if x]
    cats_norm = _normalize_csv_tokens(item_categories, "ALL")
    cats_list = [] if cats_norm == "ALL" else [x for x in cats_norm.split(",") if x]
    grps_norm = _normalize_csv_tokens(item_groups, "ALL")
    grps_list = [] if grps_norm == "ALL" else [x for x in grps_norm.split(",") if x]
    brands_norm = _normalize_csv_tokens(brands, "ALL")
    brands_list = [] if brands_norm == "ALL" else [x for x in brands_norm.split(",") if x]
    dist_norm = normalize_districts(districts)
    dist_list = [] if dist_norm == "ALL" else [x for x in dist_norm.split(",") if x]
    sfmt_norm = normalize_store_formats(store_formats)
    br_norm = normalize_branches(branches)
    br_list = [] if br_norm == "ALL" else [x for x in br_norm.split(",") if x]

    is_true_mega = bool(_TRUE_MEGA_EVENT_RE.search(str(campaign_name or "")))
    type_cap = float(meta.get("ai_max_lift", 45.0))
    if ctype == "mega_sale" and not is_true_mega:
        dur_days = (ed - sd).days + 1
        type_cap = 55.0 if dur_days <= 3 else 65.0

    camp_dows = tuple(sorted({(sd + timedelta(days=i)).weekday() for i in range(min(7, (ed - sd).days + 1))}))

    def _apply_closed_loop_learning(
        base_lift: float,
        base_label: str,
        client_obj,
        latest_dt_obj: date,
    ) -> Tuple[float, str, Optional[Dict[str, Any]]]:
        if not use_learned_offers:
            return base_lift, base_label, None
        pool = prior_completed if prior_completed is not None else _get_completed_campaigns_for_learning(client_obj, latest_dt_obj)
        learned = _learn_lift_from_prior_campaigns(
            target_start=sd,
            target_end=ed,
            ctype=ctype,
            sfmt_norm=sfmt_norm,
            br_set=set(br_list),
            prod_set=set(prods_list),
            cat_set=set(cats_list),
            grp_set=set(grps_list),
            type_cap=type_cap,
            prior_completed=pool,
        )
        if not learned:
            return base_lift, base_label, None
        wl = float(learned["learned_weight"])
        ll = float(learned["learned_lift_pct"])
        combined = (wl * ll) + ((1.0 - wl) * base_lift)
        # Keep planned lift realistic (allow down to 2% if past offers proved very soft, never negative for promo plan)
        combined_final = float(int(round(max(2.0, min(type_cap, combined)))))
        learn_tag = f" + Learned from {learned['matched_count']} prior offer{'s' if learned['matched_count'] != 1 else ''} (avg {ll:+.1f}%)"
        return combined_final, base_label + learn_tag, learned

    try:
        client = ch or get_ch_client()
        if client is None:
            raise RuntimeError("ClickHouse client unavailable")
        latest_dt = _get_latest_sales_date_cached(ch=client)
        ref_anchor = min(sd, latest_dt)
        prof = _fetch_store_sale_value_profile(
            ch=client,
            ref_anchor=ref_anchor,
            camp_dows=camp_dows,
            branches_list=br_list,
            districts_list=dist_list,
            store_formats=sfmt_norm,
            product_lines_list=prods_list,
            brands_list=brands_list,
            item_categories_list=cats_list,
            item_groups_list=grps_list,
        )
        if not prof:
            raise RuntimeError("Empty ClickHouse profile")

        clean_same_lifts = prof["clean_same_lifts"]
        clean_all_lifts = prof["clean_all_lifts"]
        fest_lifts = prof["fest_lifts"]
        cur_base_rev = float(prof.get("current_base_rev") or 0.0)
        fy = prof.get("first_year")
        ly = prof.get("last_year")
        span_lbl = f"{fy}–{ly}" if (fy and ly and fy != ly) else (str(ly or fy or "Full"))

        # Case 1: Genuine annual storewide festival / anniversary mega event across ALL historical years
        if ctype == "mega_sale" and is_true_mega and len(fest_lifts) >= 3 and cur_base_rev > 1000.0:
            fest_q = {"low": 0.35, "medium": 0.55, "high": 0.72, "mega": 0.85}.get(eff_strength, 0.72)
            emp_lift_raw = _percentile_sorted(fest_lifts, fest_q)
            emp_lift = max(15.0, min(type_cap, emp_lift_raw))
            w_n = len(fest_lifts) / (len(fest_lifts) + 6.0)
            w_rev = cur_base_rev / (cur_base_rev + 150000.0)
            w_ch = min(0.85, max(0.25, w_n * w_rev))
            blended = (w_ch * emp_lift) + ((1.0 - w_ch) * clean_prior_lift)
            hist_lift = float(int(round(max(15.0, min(type_cap, blended)))))
            base_lbl = f"ClickHouse Full History {span_lbl} ({len(fest_lifts)} fest days · base {_fmt_inr_short(cur_base_rev)}/d)"
            final_lift, final_lbl, learned = _apply_closed_loop_learning(hist_lift, base_lbl, client, latest_dt)
            return {
                "expected_lift_pct": final_lift,
                "offer_strength": eff_strength,
                "baseline_avg_sales": round(cur_base_rev, 0),
                "empirical_lift_pct": round(emp_lift_raw, 1),
                "sample_days": len(fest_lifts),
                "ch_weight": round(w_ch, 2),
                "history_span": span_lbl,
                "learned_from_count": learned["matched_count"] if learned else 0,
                "learned_lift_pct": learned["learned_lift_pct"] if learned else None,
                "learned_weight": learned["learned_weight"] if learned else 0.0,
                "source": "clickhouse_learned_festival" if learned else "clickhouse_store_festival",
                "basis_label": final_lbl,
            }

        # Case 2: Store + item hierarchy quarter-detrended same-weekday Sale Value (₹) surge
        use_same_dow = len(clean_same_lifts) >= 8
        sample_lifts = clean_same_lifts if use_same_dow else clean_all_lifts
        if len(sample_lifts) >= 8 and cur_base_rev > 1000.0:
            base_q = {"low": 0.68, "medium": 0.76, "high": 0.84, "mega": 0.89}.get(eff_strength, 0.76)
            ctype_adj = {
                "exchange_cashback": -0.02,
                "weekend_dhamaka": 0.00,
                "flash_offer": 0.00,
                "brand_festival": 0.01,
                "product_launch": 0.03,
                "mega_sale": 0.04,
            }.get(ctype, 0.0)
            pct_q = max(0.60, min(0.92, base_q + ctype_adj))
            emp_lift_raw = _percentile_sorted(sample_lifts, pct_q)
            emp_lift = max(5.0, min(type_cap, emp_lift_raw))

            w_n = len(sample_lifts) / (len(sample_lifts) + 12.0)
            w_rev = cur_base_rev / (cur_base_rev + 150000.0)
            w_ch = min(0.88, max(0.20, w_n * w_rev))
            blended = (w_ch * emp_lift) + ((1.0 - w_ch) * clean_prior_lift)
            hist_lift = float(int(round(max(5.0, min(type_cap, blended)))))
            dow_str = "/".join(_DOW_SHORT[d] for d in camp_dows) if use_same_dow else "all-days"
            base_lbl = f"ClickHouse Full History {span_lbl} ({len(sample_lifts)} clean {dow_str} · base {_fmt_inr_short(cur_base_rev)}/d)"
            final_lift, final_lbl, learned = _apply_closed_loop_learning(hist_lift, base_lbl, client, latest_dt)
            return {
                "expected_lift_pct": final_lift,
                "offer_strength": eff_strength,
                "baseline_avg_sales": round(cur_base_rev, 0),
                "empirical_lift_pct": round(emp_lift_raw, 1),
                "sample_days": len(sample_lifts),
                "ch_weight": round(w_ch, 2),
                "history_span": span_lbl,
                "learned_from_count": learned["matched_count"] if learned else 0,
                "learned_lift_pct": learned["learned_lift_pct"] if learned else None,
                "learned_weight": learned["learned_weight"] if learned else 0.0,
                "source": "clickhouse_learned_store" if learned else "clickhouse_store_value",
                "basis_label": final_lbl,
            }
    except Exception as e:
        print("[promo-calendar] store ClickHouse planned lift fallback:", e)

    return {
        "expected_lift_pct": float(fallback_lift),
        "offer_strength": eff_strength,
        "baseline_avg_sales": None,
        "empirical_lift_pct": None,
        "sample_days": 0,
        "ch_weight": 0.0,
        "history_span": "",
        "learned_from_count": 0,
        "learned_lift_pct": None,
        "learned_weight": 0.0,
        "source": "prior_fallback",
        "basis_label": "Calibrated campaign-type baseline (insufficient ClickHouse history)",
    }


def attach_clickhouse_planned_lifts(
    campaigns: List[Dict[str, Any]],
    ref_today: Optional[date] = None,
    ch=None,
) -> None:
    """Enrich extracted campaign offers with per-store ClickHouse Sale Value Planned Lift %."""
    if not campaigns:
        return
    try:
        client = ch or get_ch_client()
        if client is None:
            return
    except Exception:
        return
    for c in campaigns[:40]:
        try:
            est = estimate_store_planned_lift_from_ch(
                campaign_type=c.get("campaign_type") or "weekend_dhamaka",
                offer_strength=c.get("offer_strength") or "medium",
                campaign_name=c.get("campaign_name") or "",
                start_date=c.get("start_date") or ref_today,
                end_date=c.get("end_date") or c.get("start_date") or ref_today,
                product_lines=c.get("product_lines") or "ALL",
                item_categories=c.get("item_categories") or "ALL",
                item_groups=c.get("item_groups") or "ALL",
                brands=c.get("brands") or "ALL",
                districts=c.get("districts") or c.get("district") or "ALL",
                store_formats=c.get("store_formats") or "ALL",
                branches=c.get("branches") or c.get("branches_csv") or "ALL",
                raw_ai_lift=float(c.get("expected_lift_pct") or 0.0),
                ch=client,
            )
            if est.get("source") != "prior_fallback":
                c["expected_lift_pct"] = float(est["expected_lift_pct"])
                c["offer_strength"] = est["offer_strength"]
            c["planned_lift_basis"] = est.get("basis_label") or ""
            c["planned_baseline_sales"] = est.get("baseline_avg_sales")
            c["planned_lift_source"] = est.get("source") or "prior_fallback"
        except Exception as e:
            print("[promo-calendar] attach_clickhouse_planned_lifts skipped:", e)


def recalibrate_campaigns_from_clickhouse(
    campaign_ids: Optional[Sequence[str]] = None,
    user_label: str = "clickhouse_calibration",
    ch=None,
) -> List[Dict[str, Any]]:
    """Recalibrate saved active campaigns using each store's complete multi-year ClickHouse Sale Value history + learned completed offers."""
    client = ch or get_ch_client()
    latest_dt = _get_latest_sales_date_cached(ch=client)
    existing = list_campaigns(ch=client, include_realized_lift=False, ref_dt=latest_dt)
    id_filter = {str(x).strip() for x in (campaign_ids or ()) if str(x).strip()}

    # Pre-measure completed campaigns once for closed-loop learning (zero leakage per campaign start_date!)
    completed_pool = _get_completed_campaigns_for_learning(client, latest_dt)

    to_save: List[Dict[str, Any]] = []
    report: List[Dict[str, Any]] = []
    for c in existing:
        if id_filter and c["campaign_id"] not in id_filter:
            continue
        old_lift = float(c.get("expected_lift_pct") or 0.0)
        est = estimate_store_planned_lift_from_ch(
            campaign_type=c.get("campaign_type") or "weekend_dhamaka",
            offer_strength=c.get("offer_strength") or "medium",
            campaign_name=c.get("campaign_name") or "",
            start_date=c.get("start_date"),
            end_date=c.get("end_date"),
            product_lines=c.get("product_lines") or "ALL",
            item_categories=c.get("item_categories") or "ALL",
            item_groups=c.get("item_groups") or "ALL",
            brands=c.get("brands") or "ALL",
            districts=c.get("district") or "ALL",
            store_formats=c.get("store_formats") or "ALL",
            branches=c.get("branches") or "ALL",
            raw_ai_lift=old_lift,
            ch=client,
            prior_completed=completed_pool,
        )
        new_lift = float(est["expected_lift_pct"])
        new_strength = est["offer_strength"]
        to_save.append({
            "campaign_id": c["campaign_id"],
            "campaign_name": c["campaign_name"],
            "campaign_type": c["campaign_type"],
            "start_date": c["start_date"],
            "end_date": c["end_date"],
            "product_lines": c["product_lines"],
            "item_categories": c.get("item_categories") or "ALL",
            "item_groups": c.get("item_groups") or "ALL",
            "brands": c["brands"],
            "district": c["district"],
            "store_formats": c["store_formats"],
            "branches": c["branches"],
            "offer_strength": new_strength,
            "expected_lift_pct": new_lift,
            "post_cooldown_days": c["post_cooldown_days"],
            "discount_summary": c["discount_summary"],
            "source_channel": c["source_channel"],
            "source_text": c["source_text"],
        })
        report.append({
            "campaign_id": c["campaign_id"],
            "campaign_name": c["campaign_name"],
            "branches": c["branches"],
            "old_lift_pct": old_lift,
            "new_lift_pct": new_lift,
            "offer_strength": new_strength,
            "baseline_avg_sales": est.get("baseline_avg_sales"),
            "empirical_lift_pct": est.get("empirical_lift_pct"),
            "learned_from_count": est.get("learned_from_count", 0),
            "basis_label": est.get("basis_label"),
            "source": est.get("source"),
        })

    if to_save:
        save_campaigns_bulk(to_save, user_label=user_label)
    return report


def _normalize_csv_tokens(raw: Any, default_all: str = "ALL") -> str:
    if raw is None:
        return default_all
    if isinstance(raw, (list, tuple, set)):
        items = [str(x or "").strip().upper() for x in raw]
    else:
        items = [x.strip().upper() for x in str(raw).replace(";", ",").split(",")]
    clean = []
    seen = set()
    for it in items:
        if not it or it in ("NONE", "NULL"):
            continue
        if it in ("ALL", "ALL PRODUCTS", "ALL BRANDS", "ALL CATEGORIES", "*"):
            return default_all
        if it not in seen:
            seen.add(it)
            clean.append(it)
    return ",".join(clean) if clean else default_all


def _parse_iso_date(val: Any) -> Optional[date]:
    if isinstance(val, datetime):
        return val.date()
    if isinstance(val, date):
        return val
    s = str(val or "").strip()
    if not s:
        return None
    for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%Y/%m/%d", "%d %b %Y", "%d %B %Y"):
        try:
            return datetime.strptime(s[:20], fmt).date()
        except Exception:
            pass
    return None


def _invalidate_promo_caches() -> None:
    global _COMPLETED_LEARN_CACHE
    _REALIZED_LIFT_CACHE.clear()
    _COMPLETED_LEARN_CACHE = (0.0, "", [])
    try:
        cache.delete(f"dii:promo_list:v1:{get_active_engine()}:{get_cache_epoch()}")
    except Exception:
        pass
    try:
        from analytics.portal_refresh import bump_cache_epoch
        bump_cache_epoch()
    except Exception:
        pass


# ═════════════════════════════════════════════════════════════════════════════
# 1. CRUD — LIST / SAVE / DELETE CAMPAIGNS IN CLICKHOUSE
# ═════════════════════════════════════════════════════════════════════════════

def list_campaigns(
    ch=None,
    include_realized_lift: bool = False,
    ref_dt: Optional[date] = None,
) -> List[Dict[str, Any]]:
    """Return all active campaigns from `demand_promo_calendar`, newest start_date first."""
    cache_key = f"dii:promo_list:v1:{get_active_engine()}:{get_cache_epoch()}"
    cached = None if (get_bypass_cache() or include_realized_lift) else cache.get(cache_key)
    if cached is not None:
        return cached

    rows = []
    try:
        eng, client = _get_rw_client(ch)
        _ensure_table(eng, client)
        if eng == "clickhouse":
            res = client.query(f"""
                SELECT
                    campaign_id,
                    argMax(campaign_name, updated_at) AS c_name,
                    argMax(campaign_type, updated_at) AS c_type,
                    argMax(start_date, updated_at) AS c_start,
                    argMax(end_date, updated_at) AS c_end,
                    argMax(product_lines, updated_at) AS c_prods,
                    argMax(brands, updated_at) AS c_brands,
                    argMax(district, updated_at) AS c_dist,
                    argMax(offer_strength, updated_at) AS c_strength,
                    argMax(expected_lift_pct, updated_at) AS c_lift,
                    argMax(post_cooldown_days, updated_at) AS c_cooldown,
                    argMax(discount_summary, updated_at) AS c_disc,
                    argMax(source_channel, updated_at) AS c_src_ch,
                    argMax(source_text, updated_at) AS c_src_txt,
                    argMax(is_active, updated_at) AS c_active,
                    argMax(created_by, updated_at) AS c_created_by,
                    max(updated_at) AS last_updated_at,
                    argMax(store_formats, updated_at) AS c_store_fmts,
                    argMax(branches, updated_at) AS c_branches,
                    argMax(item_categories, updated_at) AS c_item_cats,
                    argMax(item_groups, updated_at) AS c_item_grps
                FROM {PROMO_TABLE}
                GROUP BY campaign_id
                HAVING c_active = 1
                ORDER BY c_start DESC, last_updated_at DESC
            """).result_rows
        else:
            res = client.query(f"""
                SELECT
                    campaign_id, campaign_name, campaign_type, start_date, end_date,
                    product_lines, brands, district, offer_strength, expected_lift_pct,
                    post_cooldown_days, discount_summary, source_channel, source_text,
                    is_active, created_by, updated_at, store_formats, branches,
                    item_categories, item_groups
                FROM (
                    SELECT *, ROW_NUMBER() OVER (PARTITION BY campaign_id ORDER BY updated_at DESC) AS rn
                    FROM {PROMO_TABLE}
                ) t
                WHERE rn = 1 AND is_active = 1
                ORDER BY start_date DESC, updated_at DESC
            """).result_rows

        today_dt = ref_dt or date.today()
        for r in res:
            sd = _parse_iso_date(r[3])
            ed = _parse_iso_date(r[4])
            if not sd or not ed:
                continue
            ctype = str(r[2] or "weekend_dhamaka").strip().lower()
            strength = str(r[8] or "medium").strip().lower()
            exp_lift = float(r[9] or 0.0)
            eff_lift = exp_lift if exp_lift > 0 else default_lift_for(ctype, strength)
            meta = CAMPAIGN_TYPE_META.get(ctype, CAMPAIGN_TYPE_META["weekend_dhamaka"])
            if ed < today_dt:
                status = "completed"
            elif sd <= today_dt <= ed:
                status = "live"
            else:
                status = "upcoming"
            prods_str = str(r[5] or "ALL").strip().upper() or "ALL"
            brands_str = str(r[6] or "ALL").strip().upper() or "ALL"
            dist_str = normalize_districts(r[7])
            dist_list = [] if dist_str == "ALL" else [x for x in dist_str.split(",") if x]
            sfmt_str = normalize_store_formats(r[17] if len(r) > 17 else "ALL")
            br_str = normalize_branches(r[18] if len(r) > 18 else "ALL")
            br_list = [] if br_str == "ALL" else [x for x in br_str.split(",") if x]
            cats_str = _normalize_csv_tokens(r[19] if len(r) > 19 else "ALL", "ALL")
            cats_list = [] if cats_str == "ALL" else [x for x in cats_str.split(",") if x]
            grps_str = _normalize_csv_tokens(r[20] if len(r) > 20 else "ALL", "ALL")
            grps_list = [] if grps_str == "ALL" else [x for x in grps_str.split(",") if x]
            rows.append({
                "campaign_id": str(r[0]),
                "campaign_name": str(r[1] or "Campaign Offer"),
                "campaign_type": ctype,
                "campaign_type_label": meta["label"],
                "color": meta["color"],
                "start_date": sd.strftime("%Y-%m-%d"),
                "end_date": ed.strftime("%Y-%m-%d"),
                "date_range_display": (
                    sd.strftime("%d %b %Y")
                    if sd == ed
                    else f"{sd.strftime('%d %b')} – {ed.strftime('%d %b %Y')}"
                ),
                "product_lines": prods_str,
                "product_lines_list": [] if prods_str == "ALL" else [x for x in prods_str.split(",") if x],
                "item_categories": cats_str,
                "item_categories_list": cats_list,
                "item_groups": grps_str,
                "item_groups_list": grps_list,
                "brands": brands_str,
                "brands_list": [] if brands_str == "ALL" else [x for x in brands_str.split(",") if x],
                "district": dist_str,
                "districts_list": dist_list,
                "store_formats": sfmt_str,
                "store_formats_label": store_format_label(sfmt_str),
                "branches": br_str,
                "branches_list": br_list,
                "offer_strength": strength,
                "expected_lift_pct": round(exp_lift, 1),
                "effective_lift_pct": round(eff_lift, 1),
                "post_cooldown_days": int(r[10] if r[10] is not None else default_cooldown_for(ctype)),
                "discount_summary": str(r[11] or ""),
                "source_channel": str(r[12] or "whatsapp"),
                "source_text": str(r[13] or ""),
                "created_by": str(r[15] or ""),
                "updated_at": str(r[16] or ""),
                "status": status,
                "realized_lift_pct": None,
                "realized_total_sales": None,
                "baseline_total_sales": None,
                "realized_avg_sales": None,
                "baseline_avg_sales": None,
                "incremental_sales": None,
                "actual_cooldown_dip_pct": None,
                "realized_qty_lift_pct": None,
                "realized_avg_units": None,
                "baseline_avg_units": None,
                "incremental_units": None,
                "matched_branches": None,
                "baseline_mode": None,
                "baseline_label": "",
                "realized_days": 0,
            })
    except Exception as e:
        print("[promo-calendar] list_campaigns skipped:", e)
        return []

    if not include_realized_lift:
        cache.set(cache_key, rows, 600)
        return rows

    if rows:
        _attach_target_branch_counts(ch or get_ch_client(), rows)
        if ref_dt is not None:
            _attach_realized_lifts(ch or get_ch_client(), rows, ref_dt)
    return rows


def _attach_target_branch_counts(ch, campaigns: List[Dict[str, Any]]) -> None:
    """Attach exact matching retail branch count from `branch_master` for each campaign's target stores / districts / formats."""
    if ch is None or not campaigns:
        return
    try:
        bm_rows = _get_branch_catalog_cached(ch=ch)
        for c in campaigns:
            br_set = set(c.get("branches_list") or [])
            if br_set:
                cnt = sum(1 for bcode, bname, _bdist in bm_rows if (bname in br_set or bcode in br_set))
                c["target_branches"] = cnt or len(br_set)
                continue
            d_set = set(c.get("districts_list") or [])
            sfmt = c.get("store_formats") or "ALL"
            cnt = 0
            for _bcode, bname, bdist in bm_rows:
                if d_set and bdist not in d_set:
                    continue
                is_fut = "FUTURE" in bname
                if sfmt == "MYG_FUTURE" and not is_fut:
                    continue
                if sfmt == "MYG" and is_fut:
                    continue
                cnt += 1
            c["target_branches"] = cnt
    except Exception as e:
        print("[promo-calendar] target branch count skipped:", e)


def _attach_realized_lifts(ch, campaigns: List[Dict[str, Any]], ref_dt: date) -> None:
    """Measure realized incremental lift in ClickHouse for campaigns that have already started,
    strictly filtered to the campaign's target stores / districts / myG format, and compared against
    a Same-Day-of-Week matched, festival-cleansed baseline (prior 7 weeks) so weekend peaks don't
    artificially drag weekday promo realized lifts negative."""
    if ch is None:
        return
    eligible = [c for c in campaigns if _parse_iso_date(c["start_date"]) and _parse_iso_date(c["start_date"]) <= ref_dt]
    if not eligible:
        return
    try:
        from .demand_intelligence_service import _FESTIVAL_DATE_MAP
    except Exception:
        _FESTIVAL_DATE_MAP = {}

    _DOW_SHORT = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
    ref_iso = ref_dt.strftime("%Y-%m-%d")
    now_ts = time.time()

    for c in eligible[:120]:
        try:
            cid_key = (str(c.get("campaign_id") or ""), ref_iso)
            cached_m = _REALIZED_LIFT_CACHE.get(cid_key)
            if cached_m and (now_ts - cached_m[0]) < 1200:
                c.update(cached_m[1])
                continue

            sd = _parse_iso_date(c["start_date"])
            ed = min(_parse_iso_date(c["end_date"]), ref_dt)
            if not sd or not ed or ed < sd:
                continue
            camp_dates = [sd + timedelta(days=i) for i in range((ed - sd).days + 1)]
            camp_dows = sorted({d.weekday() for d in camp_dates})

            # Include up to 4 post-campaign days when campaign has ended so we also measure actual cooldown hangover
            post_end = min(ed + timedelta(days=4), ref_dt) if ed < ref_dt else ed
            post_dates = [ed + timedelta(days=k) for k in range(1, (post_end - ed).days + 1)]

            # Look back 7 full weeks (52..4 days prior) so every weekday has up to 7 clean observations
            pre_start = sd - timedelta(days=52)
            pre_end = sd - timedelta(days=4)
            all_pre_dates = [pre_start + timedelta(days=i) for i in range((pre_end - pre_start).days + 1)]
            clean_all_dates = [
                d for d in all_pre_dates
                if not (_FESTIVAL_DATE_MAP.get(d, {}).get("is_core_surge", False))
            ]
            if len(clean_all_dates) < 14:
                clean_all_dates = all_pre_dates

            clean_by_dow: Dict[int, List[date]] = {
                dow: [d for d in clean_all_dates if d.weekday() == dow]
                for dow in range(7)
            }
            use_same_dow = all(len(clean_by_dow[dow]) >= 3 for dow in camp_dows)

            clauses = [
                "s.branch NOT IN ('3GH', 'CSB', 'CSBW')",
                "s.item_code != ''",
                "NOT (upper(s.invoice_no) LIKE '%SMC%' OR upper(s.invoice_no) LIKE '%EI%')",
            ]
            if c.get("product_lines") and c["product_lines"] != "ALL":
                ps = "', '".join(_sql_esc(x) for x in c["product_lines_list"])
                clauses.append(f"upper(trim(m.product)) IN ('{ps}')")
            if c.get("item_categories") and c["item_categories"] != "ALL":
                cs = "', '".join(_sql_esc(x) for x in c.get("item_categories_list") or [])
                if cs:
                    clauses.append(f"upper(trim(m.item_category)) IN ('{cs}')")
            if c.get("item_groups") and c["item_groups"] != "ALL":
                gs = "', '".join(_sql_esc(x) for x in c.get("item_groups_list") or [])
                if gs:
                    clauses.append(f"upper(trim(m.item_group)) IN ('{gs}')")
            if c.get("brands") and c["brands"] != "ALL":
                bs = "', '".join(_sql_esc(x) for x in c["brands_list"])
                clauses.append(f"upper(trim(m.brand)) IN ('{bs}')")

            # Specific target stores take precedence; otherwise apply district & myG / myG Future store-format filters
            need_bm_join = False
            br_list = c.get("branches_list") or []
            if br_list:
                need_bm_join = True
                br_sql = "', '".join(_sql_esc(x) for x in br_list)
                clauses.append(f"(upper(trim(bm.branch_name)) IN ('{br_sql}') OR upper(trim(bm.code)) IN ('{br_sql}'))")
            else:
                dist_list = c.get("districts_list") or []
                sfmt = c.get("store_formats") or "ALL"
                if dist_list:
                    need_bm_join = True
                    ds_sql = "', '".join(_sql_esc(x) for x in dist_list)
                    clauses.append(f"upper(trim(bm.district)) IN ('{ds_sql}')")
                if sfmt == "MYG_FUTURE":
                    need_bm_join = True
                    clauses.append("upper(bm.branch_name) LIKE '%FUTURE%'")
                elif sfmt == "MYG":
                    need_bm_join = True
                    clauses.append("upper(bm.branch_name) NOT LIKE '%FUTURE%'")

            bm_join = "INNER JOIN branch_master bm ON s.branch = bm.code" if need_bm_join else ""
            where_sql = " AND ".join(clauses)
            net_q = "if(s.sold_price < 0, -abs(toFloat64(s.qty)), abs(toFloat64(s.qty)))"
            sql = f"""
                SELECT
                    toDate(s.date) AS dt,
                    sum(toFloat64(s.sold_price)) AS net_rev,
                    sum({net_q}) AS net_u,
                    groupUniqArrayIf(s.branch, toDate(s.date) >= toDate('{sd}') AND toDate(s.date) <= toDate('{ed}')) AS camp_brs
                FROM azure_sales_report s
                INNER JOIN item_master m ON s.item_code = m.item_code
                {bm_join}
                WHERE s.date >= toDateTime('{pre_start} 00:00:00')
                  AND s.date < toDateTime('{(post_end + timedelta(days=1))} 00:00:00')
                  AND {where_sql}
                GROUP BY dt
            """
            res = ch.query(sql).result_rows
            if res is not None:
                day_rev: Dict[date, float] = {}
                day_units: Dict[date, float] = {}
                camp_br_set: Set[str] = set()
                for row in res:
                    d_val = _parse_iso_date(row[0])
                    if not d_val:
                        continue
                    day_rev[d_val] = max(0.0, float(row[1] or 0.0))
                    day_units[d_val] = max(0.0, float(row[2] or 0.0))
                    if len(row) > 3 and row[3]:
                        for bcode in row[3]:
                            if bcode:
                                camp_br_set.add(str(bcode).strip().upper())

                camp_days = max(1, len(camp_dates))
                camp_rev = sum(day_rev.get(d, 0.0) for d in camp_dates)
                camp_avg_rev = camp_rev / camp_days
                camp_u = sum(day_units.get(d, 0.0) for d in camp_dates)
                camp_avg_u = camp_u / camp_days

                metrics_out: Dict[str, Any] = {
                    "matched_branches": len(camp_br_set),
                    "realized_days": camp_days,
                }

                if use_same_dow:
                    dow_avg_rev = {
                        dow: (sum(day_rev.get(bd, 0.0) for bd in clean_by_dow[dow]) / len(clean_by_dow[dow]))
                        for dow in camp_dows
                    }
                    dow_avg_u = {
                        dow: (sum(day_units.get(bd, 0.0) for bd in clean_by_dow[dow]) / len(clean_by_dow[dow]))
                        for dow in camp_dows
                    }
                    expected_camp_rev = sum(dow_avg_rev[cd.weekday()] for cd in camp_dates)
                    expected_camp_u = sum(dow_avg_u[cd.weekday()] for cd in camp_dates)
                    base_avg_rev = expected_camp_rev / camp_days
                    base_avg_u = expected_camp_u / camp_days
                    base_days_used = sum(len(clean_by_dow[dow]) for dow in camp_dows)
                    dow_str = "/".join(_DOW_SHORT[dow] for dow in camp_dows)
                    metrics_out["baseline_mode"] = "same_dow"
                    metrics_out["baseline_label"] = f"Same-weekday ({base_days_used} clean {dow_str})"
                else:
                    base_days_used = max(1, len(clean_all_dates))
                    base_avg_rev = sum(day_rev.get(bd, 0.0) for bd in clean_all_dates) / base_days_used
                    base_avg_u = sum(day_units.get(bd, 0.0) for bd in clean_all_dates) / base_days_used
                    expected_camp_rev = base_avg_rev * camp_days
                    expected_camp_u = base_avg_u * camp_days
                    metrics_out["baseline_mode"] = "clean_all_days"
                    metrics_out["baseline_label"] = f"Clean prior {base_days_used}d avg"

                # Primary Realized Lift = Net Sale Value (₹) % vs same-weekday clean baseline
                if base_avg_rev > 100.0:
                    val_lift = ((camp_avg_rev - base_avg_rev) / base_avg_rev) * 100.0
                    metrics_out["realized_lift_pct"] = round(val_lift, 1)
                    metrics_out["realized_total_sales"] = round(camp_rev, 0)
                    metrics_out["baseline_total_sales"] = round(expected_camp_rev, 0)
                    metrics_out["realized_avg_sales"] = round(camp_avg_rev, 0)
                    metrics_out["baseline_avg_sales"] = round(base_avg_rev, 0)
                    metrics_out["incremental_sales"] = round(camp_rev - expected_camp_rev, 0)
                if base_avg_u > 0.2:
                    qty_lift = ((camp_avg_u - base_avg_u) / base_avg_u) * 100.0
                    metrics_out["realized_qty_lift_pct"] = round(qty_lift, 1)
                    metrics_out["realized_avg_units"] = round(camp_avg_u, 1)
                    metrics_out["baseline_avg_units"] = round(base_avg_u, 1)
                    metrics_out["incremental_units"] = int(round(camp_u - expected_camp_u))

                # Measure actual post-promo cooldown dip % when 2+ post-campaign days have completed
                if len(post_dates) >= 2:
                    post_rev = sum(day_rev.get(pd_d, 0.0) for pd_d in post_dates)
                    post_base = 0.0
                    for pd_d in post_dates:
                        same_list = clean_by_dow.get(pd_d.weekday(), [])
                        if len(same_list) >= 3:
                            post_base += sum(day_rev.get(bd, 0.0) for bd in same_list) / len(same_list)
                        else:
                            post_base += base_avg_rev
                    if post_base > 200.0:
                        metrics_out["actual_cooldown_dip_pct"] = round(((post_rev - post_base) / post_base) * 100.0, 1)

                c.update(metrics_out)
                if len(_REALIZED_LIFT_CACHE) > 512:
                    _REALIZED_LIFT_CACHE.clear()
                _REALIZED_LIFT_CACHE[cid_key] = (now_ts, metrics_out)
        except Exception as e:
            print(f"[promo-calendar] realized lift skipped for {c.get('campaign_id')}:", e)


def save_campaign(payload: Dict[str, Any], user_label: str = "") -> Dict[str, Any]:
    """Create or update a campaign in `demand_promo_calendar` and invalidate forecast caches."""
    name = str(payload.get("campaign_name") or "").strip()
    if not name:
        raise ValueError("Campaign name is required.")

    sd = _parse_iso_date(payload.get("start_date"))
    ed = _parse_iso_date(payload.get("end_date") or payload.get("start_date"))
    if not sd or not ed:
        raise ValueError("Valid start_date and end_date (YYYY-MM-DD) are required.")
    if ed < sd:
        sd, ed = ed, sd
    if (ed - sd).days > 60:
        raise ValueError("Campaign duration cannot exceed 60 days.")

    ctype = str(payload.get("campaign_type") or "weekend_dhamaka").strip().lower()
    if ctype not in CAMPAIGN_TYPE_META:
        ctype = "weekend_dhamaka"
    strength = str(payload.get("offer_strength") or "medium").strip().lower()
    if strength not in ("low", "medium", "high", "mega"):
        strength = "medium"

    try:
        exp_lift = float(payload.get("expected_lift_pct") or 0.0)
    except Exception:
        exp_lift = 0.0
    exp_lift = max(0.0, min(exp_lift, 400.0))

    cd_raw = payload.get("post_cooldown_days")
    if cd_raw is None or str(cd_raw).strip() == "":
        cooldown = default_cooldown_for(ctype)
    else:
        try:
            cooldown = max(0, min(14, int(cd_raw)))
        except Exception:
            cooldown = default_cooldown_for(ctype)

    cid = str(payload.get("campaign_id") or "").strip() or f"camp_{uuid.uuid4().hex[:12]}"
    prods = _normalize_csv_tokens(payload.get("product_lines"), "ALL")
    item_cats = _normalize_csv_tokens(payload.get("item_categories"), "ALL")
    item_grps = _normalize_csv_tokens(payload.get("item_groups"), "ALL")
    brands = _normalize_csv_tokens(payload.get("brands"), "ALL")
    dist = normalize_districts(payload.get("districts") or payload.get("district"))
    sfmt = normalize_store_formats(payload.get("store_formats") or payload.get("store_format"))
    brs = resolve_canonical_branches(
        payload.get("branches") or payload.get("target_stores") or payload.get("stores"),
        store_formats=sfmt,
        districts_norm=dist,
    )
    disc_summary = str(payload.get("discount_summary") or "").strip()[:300]
    src_chan = str(payload.get("source_channel") or "whatsapp").strip().lower()[:30]
    src_text = str(payload.get("source_text") or "").strip()[:3000]
    now_dt = datetime.utcnow()

    eng, client = _get_rw_client()
    _ensure_table(eng, client)
    client.insert(
        PROMO_TABLE,
        [[
            cid, name[:160], ctype, sd, ed, prods, item_cats, item_grps, brands, dist, sfmt, brs,
            strength, float(exp_lift), int(cooldown), disc_summary,
            src_chan, src_text, 1, (user_label or "dashboard")[:120], now_dt,
        ]],
        column_names=[
            "campaign_id", "campaign_name", "campaign_type", "start_date", "end_date",
            "product_lines", "item_categories", "item_groups", "brands", "district", "store_formats", "branches",
            "offer_strength", "expected_lift_pct",
            "post_cooldown_days", "discount_summary", "source_channel", "source_text",
            "is_active", "created_by", "updated_at",
        ],
    )
    _invalidate_promo_caches()
    return {
        "campaign_id": cid,
        "campaign_name": name[:160],
        "campaign_type": ctype,
        "start_date": sd.strftime("%Y-%m-%d"),
        "end_date": ed.strftime("%Y-%m-%d"),
        "product_lines": prods,
        "item_categories": item_cats,
        "item_groups": item_grps,
        "brands": brands,
        "district": dist,
        "store_formats": sfmt,
        "store_formats_label": store_format_label(sfmt),
        "branches": brs,
        "offer_strength": strength,
        "expected_lift_pct": round(exp_lift, 1),
        "effective_lift_pct": round(exp_lift if exp_lift > 0 else default_lift_for(ctype, strength), 1),
        "post_cooldown_days": cooldown,
        "discount_summary": disc_summary,
    }


def save_campaigns_bulk(payloads: Sequence[Dict[str, Any]], user_label: str = "") -> List[Dict[str, Any]]:
    """Create or update multiple campaigns in one ClickHouse batch insert + single cache invalidation."""
    rows_to_insert = []
    saved_list: List[Dict[str, Any]] = []
    now_dt = datetime.utcnow()
    for payload in payloads or ():
        name = str(payload.get("campaign_name") or "").strip()
        if not name:
            continue
        sd = _parse_iso_date(payload.get("start_date"))
        ed = _parse_iso_date(payload.get("end_date") or payload.get("start_date"))
        if not sd or not ed:
            raise ValueError(f"Valid start_date and end_date are required for '{name}'.")
        if ed < sd:
            sd, ed = ed, sd
        if (ed - sd).days > 60:
            raise ValueError(f"Campaign duration for '{name}' cannot exceed 60 days.")

        ctype = str(payload.get("campaign_type") or "weekend_dhamaka").strip().lower()
        if ctype not in CAMPAIGN_TYPE_META:
            ctype = "weekend_dhamaka"
        strength = str(payload.get("offer_strength") or "medium").strip().lower()
        if strength not in ("low", "medium", "high", "mega"):
            strength = "medium"

        try:
            exp_lift = float(payload.get("expected_lift_pct") or 0.0)
        except Exception:
            exp_lift = 0.0
        exp_lift = max(0.0, min(exp_lift, 400.0))

        cd_raw = payload.get("post_cooldown_days")
        if cd_raw is None or str(cd_raw).strip() == "":
            cooldown = default_cooldown_for(ctype)
        else:
            try:
                cooldown = max(0, min(14, int(cd_raw)))
            except Exception:
                cooldown = default_cooldown_for(ctype)

        cid = str(payload.get("campaign_id") or "").strip() or f"camp_{uuid.uuid4().hex[:12]}"
        prods = _normalize_csv_tokens(payload.get("product_lines"), "ALL")
        item_cats = _normalize_csv_tokens(payload.get("item_categories"), "ALL")
        item_grps = _normalize_csv_tokens(payload.get("item_groups"), "ALL")
        brands = _normalize_csv_tokens(payload.get("brands"), "ALL")
        dist = normalize_districts(payload.get("districts") or payload.get("district"))
        sfmt = normalize_store_formats(payload.get("store_formats") or payload.get("store_format"))
        brs = resolve_canonical_branches(
            payload.get("branches") or payload.get("target_stores") or payload.get("stores"),
            store_formats=sfmt,
            districts_norm=dist,
        )
        disc_summary = str(payload.get("discount_summary") or "").strip()[:300]
        src_chan = str(payload.get("source_channel") or "whatsapp_poster").strip().lower()[:30]
        src_text = str(payload.get("source_text") or "").strip()[:3000]

        rows_to_insert.append([
            cid, name[:160], ctype, sd, ed, prods, item_cats, item_grps, brands, dist, sfmt, brs,
            strength, float(exp_lift), int(cooldown), disc_summary,
            src_chan, src_text, 1, (user_label or "dashboard")[:120], now_dt,
        ])
        saved_list.append({
            "campaign_id": cid,
            "campaign_name": name[:160],
            "campaign_type": ctype,
            "start_date": sd.strftime("%Y-%m-%d"),
            "end_date": ed.strftime("%Y-%m-%d"),
            "product_lines": prods,
            "item_categories": item_cats,
            "item_groups": item_grps,
            "brands": brands,
            "district": dist,
            "store_formats": sfmt,
            "store_formats_label": store_format_label(sfmt),
            "branches": brs,
            "offer_strength": strength,
            "expected_lift_pct": round(exp_lift, 1),
            "effective_lift_pct": round(exp_lift if exp_lift > 0 else default_lift_for(ctype, strength), 1),
            "post_cooldown_days": cooldown,
            "discount_summary": disc_summary,
        })

    if not rows_to_insert:
        raise ValueError("No valid campaigns provided.")

    eng, client = _get_rw_client()
    _ensure_table(eng, client)
    client.insert(
        PROMO_TABLE,
        rows_to_insert,
        column_names=[
            "campaign_id", "campaign_name", "campaign_type", "start_date", "end_date",
            "product_lines", "item_categories", "item_groups", "brands", "district", "store_formats", "branches",
            "offer_strength", "expected_lift_pct",
            "post_cooldown_days", "discount_summary", "source_channel", "source_text",
            "is_active", "created_by", "updated_at",
        ],
    )
    _invalidate_promo_caches()
    return saved_list


def delete_campaign(campaign_id: str, user_label: str = "") -> bool:
    """Soft-delete a campaign by inserting a tombstone row (`is_active = 0`)."""
    cid = str(campaign_id or "").strip()
    if not cid:
        return False
    eng, client = _get_rw_client()
    _ensure_table(eng, client)
    client.insert(
        PROMO_TABLE,
        [[
            cid, "", "weekend_dhamaka", date(2026, 1, 1), date(2026, 1, 1),
            "ALL", "ALL", "ALL", "ALL", "ALL", "ALL", "ALL", "medium", 0.0, 0, "", "manual", "", 0,
            (user_label or "dashboard")[:120], datetime.utcnow(),
        ]],
        column_names=[
            "campaign_id", "campaign_name", "campaign_type", "start_date", "end_date",
            "product_lines", "item_categories", "item_groups", "brands", "district", "store_formats", "branches",
            "offer_strength", "expected_lift_pct",
            "post_cooldown_days", "discount_summary", "source_channel", "source_text",
            "is_active", "created_by", "updated_at",
        ],
    )
    _invalidate_promo_caches()
    return True


# ═════════════════════════════════════════════════════════════════════════════
# 2. FORECAST INTEGRATION — SCOPE MATCHING, LIFT & POST-PROMO COOLDOWN
# ═════════════════════════════════════════════════════════════════════════════

def campaign_scope_weight(
    campaign: Dict[str, Any],
    product_line: str = "",
    brand: str = "",
    district: str = "",
    branch_name: str = "",
    branch_code: str = "",
    item_category: str = "",
    item_group: str = "",
) -> float:
    """
    Return how strongly `campaign` applies to `(product_line, item_category, item_group, brand, district, branch)`:
      - 1.0  : exact match (e.g. specific-store promo viewed on that store, or Front Load 8KG promo on that group)
      - 0..1 : proportional dilution when viewing an unfiltered aggregate slice
      - 0.0  : non-matching store, district, store format, product, item_category, item_group, or brand
    """
    p_norm = (product_line or "").strip().upper()
    ic_norm = (item_category or "").strip().upper()
    ig_norm = (item_group or "").strip().upper()
    b_norm = (brand or "").strip().upper()
    d_norm = normalize_districts(district) if (district or "").strip() else ""
    if d_norm == "ALL":
        d_norm = ""
    bn_norm = (branch_name or "").strip().upper()
    bc_norm = (branch_code or "").strip().upper()

    # 1. Specific target stores (takes precedence over broad district/format filters when specified)
    c_branches = campaign.get("branches_list")
    if c_branches is None:
        raw_br = normalize_branches(campaign.get("branches") or campaign.get("target_stores"))
        c_branches = [] if raw_br == "ALL" else [x for x in raw_br.split(",") if x]

    c_dists = campaign.get("districts_list")
    if c_dists is None:
        raw_d = normalize_districts(campaign.get("districts") or campaign.get("district"))
        c_dists = [] if raw_d == "ALL" else [x for x in raw_d.split(",") if x]

    if c_branches:
        br_set = {x.strip().upper() for x in c_branches if x}
        if bn_norm or bc_norm:
            if (bn_norm and bn_norm in br_set) or (bc_norm and bc_norm in br_set):
                dist_scale = 1.0
                fmt_scale = 1.0
            else:
                return 0.0
        elif d_norm:
            # Exact district share using cached retail branch catalog when available
            catalog = _get_branch_catalog_cached()
            if catalog:
                dist_rows = [(c, bn) for c, bn, d in catalog if d == d_norm]
                if dist_rows:
                    in_dist_matched = sum(1 for c, bn in dist_rows if (bn in br_set or c in br_set))
                    if in_dist_matched == 0:
                        return 0.0
                    dist_scale = round(min(1.0, in_dist_matched / len(dist_rows)), 4)
                    fmt_scale = 1.0
                else:
                    if c_dists and d_norm not in c_dists:
                        return 0.0
                    dist_scale = min(0.70, max(0.10, len(br_set) / 10.0))
                    fmt_scale = 1.0
            else:
                if c_dists and d_norm not in c_dists:
                    return 0.0
                dist_scale = min(0.70, max(0.10, len(br_set) / 10.0))
                fmt_scale = 1.0
        else:
            # Unfiltered All-Kerala portfolio view: scale by exact retail branch share
            catalog = _get_branch_catalog_cached()
            total_cat = len(catalog) if catalog else 148
            matched_cat = sum(1 for c, bn, _d in catalog if (bn in br_set or c in br_set)) if catalog else len(br_set)
            dist_scale = round(min(1.0, max(0.01, (matched_cat or len(br_set)) / max(total_cat, 1))), 4)
            fmt_scale = 1.0
    else:
        # 1b. Multi-district scope matching
        if c_dists:
            if d_norm:
                if d_norm not in c_dists:
                    return 0.0
                dist_scale = 1.0
            else:
                # Unfiltered All-Locations view: scale proportionally to Kerala district coverage
                dist_scale = 1.0 if len(c_dists) >= 14 else min(0.92, max(0.16, len(c_dists) / 13.0))
        else:
            dist_scale = 1.0

        # 2. Store format scope matching (myG vs myG Future)
        sfmt = normalize_store_formats(campaign.get("store_formats") or campaign.get("store_format") or "ALL")
        if sfmt in ("ALL", "MYG,MYG_FUTURE"):
            fmt_scale = 1.0
        elif sfmt == "MYG_FUTURE":
            if bn_norm:
                if "FUTURE" not in bn_norm:
                    return 0.0
                fmt_scale = 1.0
            else:
                # ~73 of 148 retail branches are myG Future
                fmt_scale = 0.50
        elif sfmt == "MYG":
            if bn_norm:
                if "FUTURE" in bn_norm:
                    return 0.0
                fmt_scale = 1.0
            else:
                # ~75 of 148 retail branches are standard myG
                fmt_scale = 0.50
        else:
            fmt_scale = 1.0

    # 3. Product line scope matching
    c_prods = campaign.get("product_lines_list")
    if c_prods is None:
        raw_p = _normalize_csv_tokens(campaign.get("product_lines"), "ALL")
        c_prods = [] if raw_p == "ALL" else [x for x in raw_p.split(",") if x]
    if c_prods:
        if p_norm and p_norm not in ("ALL", "ALL PRODUCTS", "CORE", "CORE RETAIL CATALOG"):
            if p_norm not in c_prods:
                return 0.0
            prod_scale = 1.0
        else:
            # Portfolio aggregate view: dilute targeted single-category promo proportionally
            prod_scale = min(0.75, 0.22 * len(c_prods))
    else:
        prod_scale = 1.0

    # 4. Item Category scope matching (`item_master.item_category`)
    c_cats = campaign.get("item_categories_list")
    if c_cats is None:
        raw_c = _normalize_csv_tokens(campaign.get("item_categories"), "ALL")
        c_cats = [] if raw_c == "ALL" else [x for x in raw_c.split(",") if x]
    if c_cats:
        if ic_norm and ic_norm not in ("ALL", "ALL CATEGORIES", "NONE"):
            if ic_norm not in c_cats:
                return 0.0
            cat_scale = 1.0
        else:
            cat_scale = min(0.75, 0.30 * len(c_cats))
    else:
        cat_scale = 1.0

    # 5. Item Group scope matching (`item_master.item_group`)
    c_grps = campaign.get("item_groups_list")
    if c_grps is None:
        raw_g = _normalize_csv_tokens(campaign.get("item_groups"), "ALL")
        c_grps = [] if raw_g == "ALL" else [x for x in raw_g.split(",") if x]
    if c_grps:
        if ig_norm and ig_norm not in ("ALL", "ALL GROUPS", "NONE"):
            if ig_norm not in c_grps:
                return 0.0
            grp_scale = 1.0
        else:
            grp_scale = min(0.65, 0.22 * len(c_grps))
    else:
        grp_scale = 1.0

    # 6. Brand scope matching
    c_brands = campaign.get("brands_list")
    if c_brands is None:
        raw_b = _normalize_csv_tokens(campaign.get("brands"), "ALL")
        c_brands = [] if raw_b == "ALL" else [x for x in raw_b.split(",") if x]
    if c_brands:
        if b_norm and b_norm not in ("ALL", "ALL BRANDS", "GENERAL"):
            if b_norm not in c_brands:
                return 0.0
            brand_scale = 1.0
        else:
            brand_scale = min(0.65, 0.25 * len(c_brands))
    else:
        brand_scale = 1.0

    return round(dist_scale * fmt_scale * prod_scale * cat_scale * grp_scale * brand_scale, 4)


def build_promo_date_effects(
    campaigns: Sequence[Dict[str, Any]],
    product_line: str = "",
    brand: str = "",
    district: str = "",
    branch_name: str = "",
    branch_code: str = "",
    item_category: str = "",
    item_group: str = "",
) -> Tuple[Dict[date, float], Set[date], List[Dict[str, Any]]]:
    """
    Convert active campaigns matching `(product_line, item_category, item_group, brand, district, branch)` into:
      1. `date_mult`: {date -> multiplicative factor} (>1.0 during offer, <1.0 during durable cooldown)
      2. `mask_dates`: set of campaign active dates to exclude from clean baseline calculation
      3. `pins`: chart pin descriptors matching `_FESTIVAL_CORE_EVENTS` structure
    """
    date_mult: Dict[date, float] = {}
    mask_dates: Set[date] = set()
    pins: List[Dict[str, Any]] = []

    p_norm = (product_line or "").strip().upper()
    is_durable_scope = (not p_norm) or (p_norm in DURABLE_COOLDOWN_PRODUCTS)

    for c in campaigns or ():
        w = campaign_scope_weight(
            c,
            product_line=p_norm,
            brand=brand,
            district=district,
            branch_name=branch_name,
            branch_code=branch_code,
            item_category=item_category,
            item_group=item_group,
        )
        if w <= 0.0:
            continue
        sd = _parse_iso_date(c.get("start_date"))
        ed = _parse_iso_date(c.get("end_date"))
        if not sd or not ed or ed < sd:
            continue

        lift_pct = float(c.get("effective_lift_pct") or c.get("expected_lift_pct") or 0.0)
        if lift_pct <= 0:
            lift_pct = default_lift_for(c.get("campaign_type", ""), c.get("offer_strength", "medium"))
        eff_lift_frac = (lift_pct / 100.0) * w
        span_days = (ed - sd).days + 1

        for off in range(span_days):
            cur = sd + timedelta(days=off)
            # Inside multi-day offers (>3d), opening + closing/weekend days peak slightly higher
            if span_days <= 3:
                shape = 1.0
            elif off == 0 or off == span_days - 1 or cur.weekday() in (5, 6):
                shape = 1.08
            else:
                shape = 0.90
            m = min(3.20, 1.0 + eff_lift_frac * shape)
            prev_m = date_mult.get(cur)
            # Overlapping promos stack partially (never blindly multiply two 2x promos to 4x)
            if prev_m is None or prev_m <= 1.0:
                date_mult[cur] = round(m, 4)
            else:
                big = max(prev_m, m) - 1.0
                small = min(prev_m, m) - 1.0
                date_mult[cur] = round(min(3.40, 1.0 + big + 0.35 * small), 4)
            if m >= 1.15:
                mask_dates.add(cur)

        # Post-campaign pull-forward cooldown for durables (never overrides an active promo day)
        cd_days = int(c.get("post_cooldown_days") or 0)
        if is_durable_scope and cd_days > 0 and eff_lift_frac >= 0.15:
            # Cooldown dip scales with campaign magnitude, capped at -16% daily
            max_dip = min(0.16, 0.22 * eff_lift_frac)
            for k in range(1, cd_days + 1):
                cur = ed + timedelta(days=k)
                if cur in date_mult and date_mult[cur] > 1.0:
                    continue
                decay = 1.0 - (k - 1) / max(cd_days, 1)
                cool_m = round(max(0.82, 1.0 - max_dip * (0.45 + 0.55 * decay)), 4)
                prev_m = date_mult.get(cur)
                date_mult[cur] = min(prev_m, cool_m) if prev_m is not None else cool_m

        pins.append({
            "date": sd,
            "date_str": sd.strftime("%Y-%m-%d"),
            "end_date_str": ed.strftime("%Y-%m-%d"),
            "display_date": c.get("date_range_display") or sd.strftime("%b %d, %Y"),
            "key": f"promo_{c.get('campaign_id', '')}",
            "label": f"Offer: {c.get('campaign_name', 'Campaign')[:28]}",
            "color": c.get("color") or "#e11d48",
            "is_promo": True,
            "lift_pct": round(lift_pct * w, 1),
        })

    return date_mult, mask_dates, pins


# ═════════════════════════════════════════════════════════════════════════════
# 3. AI WHATSAPP OFFER PARSER (GEMINI VISION + DETERMINISTIC FALLBACK)
# ═════════════════════════════════════════════════════════════════════════════

def _get_gemini_api_key() -> str:
    key = os.environ.get("GEMINI_API_KEY", "").strip()
    if key:
        return key
    try:
        from analytics.portal_refresh import _read_dotenv_fallback
        return _read_dotenv_fallback("GEMINI_API_KEY")
    except Exception:
        return ""


def _parse_offer_deterministic(raw_text: str, today_dt: date) -> Dict[str, Any]:
    """Zero-dependency fallback parser for WhatsApp offer messages when Gemini is unavailable."""
    txt = (raw_text or "").strip()
    low = txt.lower()

    # 1. Detect product lines
    prods: List[str] = []
    for pat, canon in _PRODUCT_SYNONYMS:
        if re.search(pat, low, re.IGNORECASE) and canon not in prods:
            prods.append(canon)

    # 2. Detect brands
    brands: List[str] = []
    for br in _KNOWN_BRANDS:
        if re.search(rf"\b{re.escape(br.lower())}\b", low):
            brands.append(br)

    # 3. Detect campaign type & strength
    if any(k in low for k in ("anniversary", "onam", "vishu", "diwali", "deepavali", "mega sale", "christmas", "biggest sale", "freedom sale", "republic")):
        ctype, strength = "mega_sale", "high"
    elif any(k in low for k in ("launch", "pre-book", "prebook", "now available", "just launched", "new arrival")):
        ctype, strength = "product_launch", "high"
    elif any(k in low for k in ("exchange", "cashback", "emi", "hdfc", "sbi", "icici", "axis", "bajaj", "zero down")):
        ctype, strength = "exchange_cashback", "medium"
    elif any(k in low for k in ("flash", "clearance", "price drop", "flat off", "today only", "24 hours", "48 hours", "midnight")):
        ctype, strength = "flash_offer", "high"
    elif brands and len(brands) <= 2 and any(k in low for k in ("day", "days", "fest", "carnival", "week")):
        ctype, strength = "brand_festival", "medium"
    else:
        ctype, strength = "weekend_dhamaka", "medium"

    if any(k in low for k in ("upto 50%", "up to 50%", "upto 60%", "up to 60%", "upto 70%", "mega", "bumper", "grand")):
        strength = "mega" if ctype == "mega_sale" else "high"

    # 4. Detect dates (e.g., "Nov 27 to 30", "27-30 Nov", "27/11/2026 - 30/11/2026", "till 30 Nov")
    month_map = {
        "jan": 1, "january": 1, "feb": 2, "february": 2, "mar": 3, "march": 3,
        "apr": 4, "april": 4, "may": 5, "jun": 6, "june": 6, "jul": 7, "july": 7,
        "aug": 8, "august": 8, "sep": 9, "sept": 9, "september": 9,
        "oct": 10, "october": 10, "nov": 11, "november": 11, "dec": 12, "december": 12,
    }
    found_dates: List[date] = []

    # Pattern A: "27-30 Nov" or "27 to 30 Nov 2026"
    m_rng1 = re.search(
        r"\b(\d{1,2})\s*(?:st|nd|rd|th)?\s*(?:-|–|—|to)\s*(\d{1,2})\s*(?:st|nd|rd|th)?\s+"
        r"(jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\s*(\d{4})?\b",
        low,
    )
    if m_rng1:
        d1, d2 = int(m_rng1.group(1)), int(m_rng1.group(2))
        mo = month_map[m_rng1.group(3)[:3]]
        yr = int(m_rng1.group(4)) if m_rng1.group(4) else today_dt.year
        try:
            found_dates.extend([date(yr, mo, d1), date(yr, mo, d2)])
        except ValueError:
            pass

    # Pattern B: "Nov 27 - 30" or "Nov 27 to Nov 30"
    if not found_dates:
        m_rng2 = re.search(
            r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\s+(\d{1,2})\s*(?:st|nd|rd|th)?\s*"
            r"(?:-|–|—|to)\s*(?:(jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\s+)?(\d{1,2})\b",
            low,
        )
        if m_rng2:
            m1 = month_map[m_rng2.group(1)[:3]]
            d1 = int(m_rng2.group(2))
            m2 = month_map[m_rng2.group(3)[:3]] if m_rng2.group(3) else m1
            d2 = int(m_rng2.group(4))
            try:
                found_dates.extend([date(today_dt.year, m1, d1), date(today_dt.year, m2, d2)])
            except ValueError:
                pass

    # Pattern C: explicit DD/MM/YYYY or YYYY-MM-DD or "27 Nov"
    if not found_dates:
        for m in re.finditer(r"\b(\d{4})-(\d{1,2})-(\d{1,2})\b", txt):
            try:
                found_dates.append(date(int(m.group(1)), int(m.group(2)), int(m.group(3))))
            except ValueError:
                pass
        for m in re.finditer(r"\b(\d{1,2})[/-](\d{1,2})[/-](20\d{2})\b", txt):
            try:
                found_dates.append(date(int(m.group(3)), int(m.group(2)), int(m.group(1))))
            except ValueError:
                pass
        for m in re.finditer(
            r"\b(\d{1,2})\s*(?:st|nd|rd|th)?\s+(jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\b",
            low,
        ):
            try:
                found_dates.append(date(today_dt.year, month_map[m.group(2)[:3]], int(m.group(1))))
            except ValueError:
                pass
        for m in re.finditer(
            r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\s+(\d{1,2})\b",
            low,
        ):
            try:
                found_dates.append(date(today_dt.year, month_map[m.group(1)[:3]], int(m.group(2))))
            except ValueError:
                pass

    if found_dates:
        sd, ed = min(found_dates), max(found_dates)
        if ed == sd and ("till" in low or "valid upto" in low or "ends on" in low) and sd > today_dt:
            sd = today_dt
    elif "this weekend" in low:
        days_to_sat = (5 - today_dt.weekday()) % 7
        sd = today_dt + timedelta(days=days_to_sat)
        ed = sd + timedelta(days=1)
    else:
        sd = today_dt
        ed = today_dt + timedelta(days=2)

    # 5. Build clean campaign title from first meaningful line
    first_line = ""
    for ln in txt.splitlines():
        clean_ln = re.sub(r"[^\w\s₹%+\-&/()!.,]", "", ln).strip()
        if len(clean_ln) >= 4:
            first_line = clean_ln[:75]
            break
    if not first_line:
        first_line = f"myG {', '.join(prods[:2]) or 'Store'} Offer"

    # 6. Extract discount/offer highlight snippets
    disc_bits = re.findall(
        r"(?:up\s*to\s*\d+%[^,\n.]*|flat\s*₹?[\d,]+[^,\n.]*|cashback[^,\n.]*|exchange\s*bonus[^,\n.]*|0%\s*emi|no\s*cost\s*emi)",
        txt,
        re.IGNORECASE,
    )
    disc_summary = " · ".join(dict.fromkeys(b.strip()[:70] for b in disc_bits[:3]))

    # 7. Detect store format scope (myG / myG Future / both)
    if re.search(r"\bmyg\s*(?:,|&|and|\+|/)\s*myg\s*future\b", low) or re.search(r"\bmyg\s*future\s*(?:,|&|and|\+|/)\s*myg\b", low):
        detected_sfmt = "MYG,MYG_FUTURE"
    elif re.search(r"\bonly\s+(?:at|in)\s+myg\s*future\b|\bmyg\s*future\s*(?:only|exclusive)\b", low):
        detected_sfmt = "MYG_FUTURE"
    elif re.search(r"\bonly\s+(?:at|in)\s+myg\s+(?:store|stores|showroom|branch)\b", low) and "myg future" not in low:
        detected_sfmt = "MYG"
    elif re.search(r"\bmyg\s*future\b", low):
        detected_sfmt = "MYG,MYG_FUTURE" if re.search(r"\bmyg\s+(?:store|stores|showroom|digital)\b", low) else "MYG_FUTURE"
    else:
        detected_sfmt = "ALL"

    # 8. Detect target districts (canonical names & poster aliases like CALICUT, WAYANAD, KASARAGOD)
    found_dists: List[str] = []
    for d_name in CANONICAL_DISTRICTS:
        if re.search(rf"\b{re.escape(d_name.lower())}\b", low) and d_name not in found_dists:
            found_dists.append(d_name)
    for alias, canon in DISTRICT_ALIAS_MAP.items():
        if len(alias) >= 4:
            if re.search(rf"\b{re.escape(alias.lower())}\b", low) and canon not in found_dists:
                found_dists.append(canon)
        else:
            # 3-letter codes (TVM, CLT, EKM, etc.) must appear uppercase to avoid false matches
            if re.search(rf"\b{re.escape(alias)}\b", txt) and canon not in found_dists:
                found_dists.append(canon)
    dist_norm = normalize_districts(found_dists) if found_dists else "ALL"

    # 9. Detect explicit store names (if any non-district branches appear in text); otherwise
    # expand all matching myG / myG Future stores for the detected district(s).
    found_explicit_brs: List[str] = []
    dist_name_set = set(CANONICAL_DISTRICTS) | set(DISTRICT_ALIAS_MAP.keys())
    catalog = _get_branch_catalog_cached()
    if catalog:
        # Match longer branch names first (e.g. "BALUSSERY FUTURE" before "BALUSSERY")
        for _code, bname, _bdist in sorted(catalog, key=lambda r: -len(r[1])):
            if len(bname) < 4 or bname in dist_name_set:
                continue
            if re.search(rf"\b{re.escape(bname.lower())}\b", low):
                if not any(bname in existing for existing in found_explicit_brs):
                    found_explicit_brs.append(bname)
    br_csv, br_list, sel_mode = resolve_effective_branches(
        found_explicit_brs if found_explicit_brs else "ALL",
        districts_norm=dist_norm,
        store_formats=detected_sfmt,
    )

    return {
        "campaign_name": first_line,
        "campaign_type": ctype,
        "start_date": sd.strftime("%Y-%m-%d"),
        "end_date": ed.strftime("%Y-%m-%d"),
        "product_lines": prods if prods else ["ALL"],
        "item_categories": ["ALL"],
        "item_groups": ["ALL"],
        "brands": brands if brands else ["ALL"],
        "district": dist_norm,
        "districts": ["ALL"] if dist_norm == "ALL" else dist_norm.split(","),
        "store_formats": detected_sfmt,
        "store_formats_label": store_format_label(detected_sfmt),
        "branches": br_list,
        "branches_csv": br_csv,
        "store_selection_mode": sel_mode,
        "matched_store_count": 0 if br_csv == "ALL" else len(br_list),
        "offer_strength": strength,
        "expected_lift_pct": round(default_lift_for(ctype, strength), 1),
        "post_cooldown_days": default_cooldown_for(ctype),
        "discount_summary": disc_summary,
        "parser_mode": "rule_fallback",
    }


def _normalize_parsed_offer(parsed: Dict[str, Any], ref_today: date, parser_mode: str) -> Dict[str, Any]:
    ctype = str(parsed.get("campaign_type") or "weekend_dhamaka").strip().lower()
    if ctype not in CAMPAIGN_TYPE_META:
        ctype = "weekend_dhamaka"
    strength = str(parsed.get("offer_strength") or "medium").strip().lower()
    if strength not in ("low", "medium", "high", "mega"):
        strength = "medium"

    sd = _parse_iso_date(parsed.get("start_date")) or ref_today
    ed = _parse_iso_date(parsed.get("end_date")) or sd
    if ed < sd:
        sd, ed = ed, sd

    prods_norm = _normalize_csv_tokens(parsed.get("product_lines"), "ALL")
    prods_list = ["ALL"] if prods_norm == "ALL" else [p for p in prods_norm.split(",") if p in KNOWN_PRODUCT_LINES] or ["ALL"]
    cats_norm = _normalize_csv_tokens(parsed.get("item_categories"), "ALL")
    cats_list = ["ALL"] if cats_norm == "ALL" else cats_norm.split(",")
    grps_norm = _normalize_csv_tokens(parsed.get("item_groups"), "ALL")
    grps_list = ["ALL"] if grps_norm == "ALL" else grps_norm.split(",")
    brands_norm = _normalize_csv_tokens(parsed.get("brands"), "ALL")
    brands_list = ["ALL"] if brands_norm == "ALL" else brands_norm.split(",")
    dist_norm = normalize_districts(parsed.get("districts") or parsed.get("district"))
    dist_list = ["ALL"] if dist_norm == "ALL" else dist_norm.split(",")
    sfmt_norm = normalize_store_formats(parsed.get("store_formats") or parsed.get("store_format"))
    br_norm, br_list, sel_mode = resolve_effective_branches(
        parsed.get("branches") or parsed.get("target_stores") or parsed.get("stores"),
        districts_norm=dist_norm,
        store_formats=sfmt_norm,
    )

    camp_name = str(parsed.get("campaign_name") or "myG Campaign Offer").strip()[:120]
    try:
        raw_lift = float(parsed.get("expected_lift_pct") or 0.0)
    except Exception:
        raw_lift = 0.0
    strength, lift = calibrate_ai_expected_lift(
        raw_lift=raw_lift,
        campaign_type=ctype,
        offer_strength=strength,
        campaign_name=camp_name,
        start_date=sd,
        end_date=ed,
    )

    try:
        cd = int(parsed.get("post_cooldown_days"))
    except Exception:
        cd = default_cooldown_for(ctype)
    cd = max(0, min(14, cd))

    return {
        "campaign_name": camp_name,
        "campaign_type": ctype,
        "start_date": sd.strftime("%Y-%m-%d"),
        "end_date": ed.strftime("%Y-%m-%d"),
        "product_lines": prods_list,
        "item_categories": cats_list,
        "item_groups": grps_list,
        "brands": brands_list,
        "district": dist_norm,
        "districts": dist_list,
        "store_formats": sfmt_norm,
        "store_formats_label": store_format_label(sfmt_norm),
        "branches": br_list,
        "branches_csv": br_norm,
        "store_selection_mode": sel_mode,
        "matched_store_count": 0 if br_norm == "ALL" else len(br_list),
        "offer_strength": strength,
        "expected_lift_pct": round(lift, 1),
        "post_cooldown_days": cd,
        "discount_summary": str(parsed.get("discount_summary") or "").strip()[:300],
        "parser_mode": parser_mode,
    }


_ZIP_IMG_EXT_MIME: Dict[str, str] = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
    ".gif": "image/gif",
    ".bmp": "image/bmp",
}


def _natural_sort_key(path_str: str) -> List[Any]:
    return [int(tok) if tok.isdigit() else tok.lower() for tok in re.split(r"(\d+)", str(path_str or ""))]


def _compress_poster_image(img_bytes: bytes, mime: str = "image/jpeg", max_dim: int = 1280) -> Tuple[bytes, str]:
    """Resize oversized poster images server-side with Pillow so large ZIP batches upload fast to Gemini Vision."""
    if not img_bytes:
        return img_bytes, mime
    norm_mime = (mime or "image/jpeg").split(";")[0].strip().lower()
    needs_convert = norm_mime not in ("image/jpeg", "image/png", "image/webp", "image/gif")
    try:
        from PIL import Image, ImageOps

        with Image.open(io.BytesIO(img_bytes)) as im:
            im = ImageOps.exif_transpose(im)
            w, h = im.size
            if (not needs_convert) and max(w, h) <= max_dim and len(img_bytes) <= 750 * 1024:
                return img_bytes, norm_mime
            if max(w, h) > max_dim:
                im.thumbnail((max_dim, max_dim), Image.Resampling.LANCZOS)
            if im.mode in ("RGBA", "LA") or (im.mode == "P" and "transparency" in im.info):
                bg = Image.new("RGB", im.size, (255, 255, 255))
                im_rgba = im.convert("RGBA")
                bg.paste(im_rgba, mask=im_rgba.split()[-1])
                im = bg
            elif im.mode != "RGB":
                im = im.convert("RGB")
            out = io.BytesIO()
            im.save(out, format="JPEG", quality=82, optimize=True)
            return out.getvalue(), "image/jpeg"
    except Exception:
        return img_bytes, ("image/jpeg" if needs_convert else norm_mime)


def extract_images_from_zip(
    zip_source: Any,
    max_images: int = 500,
    max_entry_bytes: int = 50 * 1024 * 1024,
    max_total_uncompressed: int = 4 * 1024 * 1024 * 1024,
) -> Tuple[List[Tuple[bytes, str, str]], Dict[str, Any]]:
    """
    Safely unpack offer poster images from a `.zip` archive (accepts `bytes` or a seekable file-like stream
    such as Django's `TemporaryUploadedFile` so up to 2 GB archives unpack without loading the full ZIP in RAM).
    - Ignores directories, `__MACOSX/` metadata, hidden dotfiles, and non-image files.
    - Preserves original entry filename for store-wise poster hints.
    - Compresses large print-res posters via Pillow so Gemini Vision handles up to 500 images reliably.
    """
    if zip_source is None:
        raise ValueError("Uploaded ZIP file is empty.")
    if isinstance(zip_source, (bytes, bytearray, memoryview)):
        if not zip_source:
            raise ValueError("Uploaded ZIP file is empty.")
        buf: Any = io.BytesIO(zip_source)
    else:
        buf = zip_source
        if hasattr(buf, "seek"):
            buf.seek(0)
    if not zipfile.is_zipfile(buf):
        raise ValueError("Uploaded file is not a valid ZIP archive.")
    if hasattr(buf, "seek"):
        buf.seek(0)

    extracted: List[Tuple[bytes, str, str]] = []
    meta: Dict[str, Any] = {
        "extracted": 0,
        "total_images_in_zip": 0,
        "truncated": 0,
        "skipped_non_images": 0,
        "skipped_oversized": 0,
    }
    cum_uncompressed = 0

    try:
        with zipfile.ZipFile(buf, "r") as zf:
            infos = sorted(zf.infolist(), key=lambda info: _natural_sort_key(info.filename))
            for info in infos:
                if info.is_dir():
                    continue
                norm_name = (info.filename or "").replace("\\", "/")
                parts = [p for p in norm_name.split("/") if p]
                if not parts:
                    continue
                if any(p == "__MACOSX" or p.startswith(".") for p in parts):
                    continue
                fname = parts[-1]
                ext = os.path.splitext(fname)[1].lower()
                mime = _ZIP_IMG_EXT_MIME.get(ext)
                if not mime:
                    meta["skipped_non_images"] += 1
                    continue
                meta["total_images_in_zip"] += 1
                if info.file_size > max_entry_bytes:
                    meta["skipped_oversized"] += 1
                    continue
                if len(extracted) >= max_images:
                    meta["truncated"] += 1
                    continue
                cum_uncompressed += max(0, int(info.file_size or 0))
                if cum_uncompressed > max_total_uncompressed:
                    meta["truncated"] += 1
                    continue
                try:
                    raw = zf.read(info)
                except Exception:
                    meta["skipped_oversized"] += 1
                    continue
                if not raw or len(raw) > max_entry_bytes:
                    meta["skipped_oversized"] += 1
                    continue
                comp_b, comp_mime = _compress_poster_image(raw, mime)
                label_name = "/".join(parts[-2:]) if len(parts) >= 2 else fname
                extracted.append((comp_b, comp_mime, label_name[:120]))
    except zipfile.BadZipFile:
        raise ValueError("Uploaded file is corrupted or not a valid ZIP archive.")
    except RuntimeError as exc:
        if "encrypted" in str(exc).lower() or "password" in str(exc).lower():
            raise ValueError("Password-protected ZIP files are not supported. Please upload an unencrypted ZIP.")
        raise ValueError(f"Could not read ZIP archive: {exc}")

    meta["extracted"] = len(extracted)
    return extracted, meta


def _union_scope_tokens(a_list: Sequence[str], b_list: Sequence[str]) -> List[str]:
    a = [x for x in (a_list or []) if x and x != "ALL"]
    b = [x for x in (b_list or []) if x and x != "ALL"]
    if a and b:
        return list(dict.fromkeys(a + b))
    return a or b or ["ALL"]


def _merge_store_formats(f1: str, f2: str) -> str:
    s1 = normalize_store_formats(f1)
    s2 = normalize_store_formats(f2)
    if s1 == s2:
        return s1
    if "ALL" in (s1, s2):
        return "ALL"
    return "MYG,MYG_FUTURE"


def _merge_dedup_campaigns(campaigns: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Merge duplicate campaign entries across multi-poster / ZIP batches:
      Pass 1 (Same event + dates + location/stores): merges `product_lines` & `brands` across slides.
      Pass 2 (Same event + dates + products + brands across store-wise posters): merges `branches`,
      `districts`, and `store_formats` so e.g. 80 store-wise posters of the same 48-Hour Sale combine
      into 1 clean multi-store campaign instead of 80 duplicate rows.
    """
    def _clean_name_key(name: Any) -> str:
        k = re.sub(r"[^a-z0-9]+", "", str(name or "").lower())
        return re.sub(r"^myg(?=[a-z0-9]{4,})", "", k)

    def _sync_derived_fields(cur: Dict[str, Any]) -> None:
        if cur.get("branches") == ["ALL"] or not cur.get("branches"):
            cur["branches"] = ["ALL"]
            cur["branches_csv"] = "ALL"
            cur["matched_store_count"] = 0
        else:
            cur["branches_csv"] = ",".join(cur["branches"])
            cur["matched_store_count"] = len(cur["branches"])
        if cur.get("districts") == ["ALL"] or not cur.get("districts"):
            cur["districts"] = ["ALL"]
            cur["district"] = "ALL"
        else:
            cur["district"] = ",".join(cur["districts"])
        cur["store_formats_label"] = store_format_label(cur.get("store_formats") or "ALL")

    # Pass 1: same location/stores, different product/brand slides
    p1_map: Dict[Tuple[str, str, str, str, str, str], Dict[str, Any]] = {}
    p1_order: List[Tuple[str, str, str, str, str, str]] = []
    for c in campaigns:
        br_sig = ",".join(sorted(x for x in (c.get("branches") or ["ALL"]) if x))
        key1 = (
            _clean_name_key(c.get("campaign_name")),
            str(c.get("start_date") or ""),
            str(c.get("end_date") or ""),
            str(c.get("district") or "ALL"),
            str(c.get("store_formats") or "ALL"),
            br_sig,
        )
        if key1 not in p1_map:
            p1_map[key1] = dict(c)
            p1_order.append(key1)
            continue
        cur = p1_map[key1]
        cur["product_lines"] = _union_scope_tokens(cur.get("product_lines") or [], c.get("product_lines") or [])
        cur["item_categories"] = _union_scope_tokens(cur.get("item_categories") or [], c.get("item_categories") or [])
        cur["item_groups"] = _union_scope_tokens(cur.get("item_groups") or [], c.get("item_groups") or [])
        cur["brands"] = _union_scope_tokens(cur.get("brands") or [], c.get("brands") or [])
        cur["expected_lift_pct"] = float(int(round((float(cur.get("expected_lift_pct") or 0) + float(c.get("expected_lift_pct") or 0)) / 2.0)))
        if len(str(c.get("discount_summary") or "")) > len(str(cur.get("discount_summary") or "")):
            cur["discount_summary"] = c.get("discount_summary") or ""
        _sync_derived_fields(cur)

    # Pass 2: same event + dates + products + brands across different store-wise posters
    p2_map: Dict[Tuple[str, str, str, str, str], Dict[str, Any]] = {}
    p2_order: List[Tuple[str, str, str, str, str]] = []
    for k1 in p1_order:
        c = p1_map[k1]
        prods_sig = ",".join(sorted(x for x in (c.get("product_lines") or ["ALL"]) if x))
        brands_sig = ",".join(sorted(x for x in (c.get("brands") or ["ALL"]) if x))
        key2 = (
            _clean_name_key(c.get("campaign_name")),
            str(c.get("start_date") or ""),
            str(c.get("end_date") or ""),
            prods_sig,
            brands_sig,
        )
        if key2 not in p2_map:
            p2_map[key2] = dict(c)
            p2_order.append(key2)
            continue
        cur = p2_map[key2]
        cur["branches"] = _union_scope_tokens(cur.get("branches") or [], c.get("branches") or [])
        cur["districts"] = _union_scope_tokens(cur.get("districts") or [], c.get("districts") or [])
        cur["store_formats"] = _merge_store_formats(cur.get("store_formats") or "ALL", c.get("store_formats") or "ALL")
        if cur.get("branches") != ["ALL"]:
            cur["store_selection_mode"] = "explicit_stores"
        cur["expected_lift_pct"] = float(int(round((float(cur.get("expected_lift_pct") or 0) + float(c.get("expected_lift_pct") or 0)) / 2.0)))
        if len(str(c.get("discount_summary") or "")) > len(str(cur.get("discount_summary") or "")):
            cur["discount_summary"] = c.get("discount_summary") or ""
        _sync_derived_fields(cur)

    return [p2_map[k] for k in p2_order]


class VisionUnavailableError(RuntimeError):
    """Raised when Gemini Vision models are temporarily rate-limited / overloaded (429/503) on image-only uploads."""


_MODEL_COOLDOWN_UNTIL: Dict[str, float] = {}
_LAST_GOOD_MODEL: Tuple[str, float] = ("", 0.0)


def _detect_branch_from_filename(filename: str, catalog: Sequence[Tuple[str, str, str]]) -> str:
    """Detect canonical `branch_master.branch_name` from a ZIP folder/file path (e.g. 'Alappuzha Future/slide1.jpg')."""
    if not filename or not catalog:
        return ""
    norm = re.sub(r"[^A-Z0-9]+", " ", str(filename).upper()).strip()
    if not norm:
        return ""
    # Strip trailing extension tokens & generic words
    norm = re.sub(r"\b(JPG|JPEG|PNG|WEBP|GIF|BMP|STORE|STORES|WISE|OFFER|OFFERS|HOURS|POWER|SALE|PAGE|SLIDE|POSTER)\b", " ", norm)
    norm = re.sub(r"\s+", " ", norm).strip()
    if not norm:
        return ""
    has_fut = "FUTURE" in norm
    # Match longest branch names first so 'BALUSSERY FUTURE' beats 'BALUSSERY'
    sorted_cat = sorted(catalog, key=lambda r: -len(r[1]))
    for _code, bname, _dist in sorted_cat:
        if len(bname) < 3:
            continue
        if re.search(rf"\b{re.escape(bname)}\b", norm):
            return bname
    # Try matching town name + 'FUTURE' flag (e.g. '48_HRS_FUTURE_EDAPPALLY_1.jpg' -> 'EDAPPALLY FUTURE')
    if has_fut:
        for _code, bname, _dist in sorted_cat:
            if "FUTURE" not in bname:
                continue
            core = bname.replace(" FUTURE", "").strip()
            if len(core) >= 4 and re.search(rf"\b{re.escape(core)}\b", norm):
                return bname
    return ""


def _enrich_offer_location_from_branches(offer: Dict[str, Any], catalog: Sequence[Tuple[str, str, str]]) -> None:
    """Backfill exact district & myG/myG Future store_formats when explicit branches are known."""
    brs = [x for x in (offer.get("branches") or []) if x and x != "ALL"]
    if not brs or not catalog:
        return
    dist_map = {bn: d for _c, bn, d in catalog if bn and d}
    if offer.get("district") in ("", "ALL") or offer.get("districts") == ["ALL"]:
        found_d = list(dict.fromkeys(dist_map[b] for b in brs if b in dist_map))
        if found_d:
            offer["districts"] = found_d
            offer["district"] = ",".join(found_d)
    has_fut = any("FUTURE" in b for b in brs)
    has_std = any("FUTURE" not in b for b in brs)
    if has_fut and has_std:
        offer["store_formats"] = "MYG,MYG_FUTURE"
    elif has_fut:
        offer["store_formats"] = "MYG_FUTURE"
    elif has_std:
        offer["store_formats"] = "MYG"
    offer["store_formats_label"] = store_format_label(offer["store_formats"])


def _ordered_vision_models(base_models: Sequence[str]) -> List[str]:
    """Order Vision models so the last known healthy model runs first and recently 429/503'd models move to the back."""
    now = time.time()
    ordered = list(base_models)
    last_m, last_ts = _LAST_GOOD_MODEL
    if last_m and (now - last_ts) < 300 and last_m in ordered:
        ordered.remove(last_m)
        ordered.insert(0, last_m)
    ready = [m for m in ordered if _MODEL_COOLDOWN_UNTIL.get(m, 0.0) <= now]
    cooling = [m for m in ordered if _MODEL_COOLDOWN_UNTIL.get(m, 0.0) > now]
    # Sort cooling models by earliest expiry so if all are cooling we still try the freshest one
    cooling.sort(key=lambda m: _MODEL_COOLDOWN_UNTIL.get(m, 0.0))
    return ready + cooling


def _call_gemini_vision_chunk(
    txt: str,
    chunk_imgs: Sequence[Tuple[Any, ...]],
    chunk_offset: int,
    system_prompt: str,
    api_key: str,
    models: Sequence[str],
    ref_today: date,
    store_hints: Optional[Sequence[str]] = None,
) -> Tuple[Optional[List[Dict[str, Any]]], str, str]:
    """Run one Gemini Vision request on up to 8 poster images (or text-only). Returns `(campaigns, mode, err)`."""
    global _LAST_GOOD_MODEL
    n_imgs = len(chunk_imgs)
    catalog = _get_branch_catalog_cached()
    batch_detected_stores: List[str] = []

    parts: List[Dict[str, Any]] = []
    if txt:
        parts.append({"text": f"WhatsApp Channel message text (accompanying {n_imgs} poster image(s)):\n{txt}"})
    else:
        parts.append({
            "text": (
                f"Extract all retail campaign offer details from these {n_imgs} myG WhatsApp offer poster image(s)."
                if n_imgs > 1
                else "Extract the retail campaign offer details from this myG WhatsApp offer poster image."
            )
        })

    hint_list = [str(h or "").strip().upper() for h in (store_hints or [])]
    for idx_local, img_entry in enumerate(chunk_imgs, 1):
        img_bytes = img_entry[0]
        img_mime = img_entry[1] if len(img_entry) > 1 else "image/jpeg"
        img_name = str(img_entry[2] or "").strip() if len(img_entry) > 2 else ""
        comp_b, comp_mime = _compress_poster_image(img_bytes, img_mime)
        mime = (comp_mime or "image/jpeg").split(";")[0].strip().lower()
        if mime not in ("image/jpeg", "image/png", "image/webp", "image/gif"):
            mime = "image/jpeg"

        # Resolve store hint from explicit hint list or ZIP folder/filename
        hint_raw = hint_list[idx_local - 1] if (idx_local - 1) < len(hint_list) else ""
        det_store = resolve_canonical_branches(hint_raw, from_parser=True) if hint_raw and hint_raw != "ALL" else ""
        if (not det_store or det_store == "ALL") and img_name:
            det_store = _detect_branch_from_filename(img_name, catalog)
        if det_store and det_store != "ALL" and det_store not in batch_detected_stores:
            batch_detected_stores.append(det_store)

        is_generic_name = (not img_name) or bool(re.match(r"^(poster|image|img|photo|wa|whatsapp|pasted)[_\-\s]*\d*\.[a-z]+$", img_name.lower()))
        if n_imgs > 1 or (not is_generic_name) or det_store:
            hint_bits = []
            if det_store and det_store != "ALL":
                hint_bits.append(f"STORE: {det_store}")
            if not is_generic_name:
                hint_bits.append(f"file: {img_name}")
            hint_str = f" ({' | '.join(hint_bits)})" if hint_bits else ""
            parts.append({"text": f"--- Poster Image #{chunk_offset + idx_local}{hint_str} ---"})
        parts.append({
            "inline_data": {
                "mime_type": mime,
                "data": base64.b64encode(comp_b).decode("ascii"),
            }
        })

    body = {
        "systemInstruction": {"parts": [{"text": system_prompt}]},
        "contents": [{"role": "user", "parts": parts}],
        "generationConfig": {
            "temperature": 0.1,
            "maxOutputTokens": 3072 if n_imgs > 1 else 1024,
            "responseMimeType": "application/json",
        },
    }

    gemini_url = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
    last_err = ""
    timeout_sec = min(26, 10 + 3 * n_imgs)
    ordered_models = _ordered_vision_models(models)

    for attempt_round in range(2):
        for model in ordered_models:
            try:
                r = requests.post(
                    gemini_url.format(model=model),
                    headers={"x-goog-api-key": api_key, "Content-Type": "application/json"},
                    json=body,
                    timeout=timeout_sec,
                )
                if r.status_code == 429:
                    _MODEL_COOLDOWN_UNTIL[model] = time.time() + 60.0
                    last_err = f"{model}: HTTP 429 (quota/rate limit)"
                    continue
                if r.status_code == 503:
                    _MODEL_COOLDOWN_UNTIL[model] = time.time() + 15.0
                    last_err = f"{model}: HTTP 503 (busy)"
                    continue
                if r.status_code == 404:
                    _MODEL_COOLDOWN_UNTIL[model] = time.time() + 3600.0
                    last_err = f"{model}: HTTP 404"
                    continue
                if r.status_code != 200:
                    last_err = f"{model}: HTTP {r.status_code}"
                    continue
                data = r.json()
                cands = data.get("candidates") or []
                if not cands:
                    last_err = f"{model}: empty candidates"
                    continue
                resp_parts = (cands[0].get("content") or {}).get("parts") or []
                raw_json = "".join(str(p.get("text") or "") for p in resp_parts).strip()
                raw_json = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw_json, flags=re.DOTALL).strip()
                parsed_top = json.loads(raw_json)
                if isinstance(parsed_top, dict) and isinstance(parsed_top.get("campaigns"), list):
                    raw_list = [x for x in parsed_top["campaigns"] if isinstance(x, dict)]
                elif isinstance(parsed_top, list):
                    raw_list = [x for x in parsed_top if isinstance(x, dict)]
                elif isinstance(parsed_top, dict):
                    raw_list = [parsed_top]
                else:
                    continue
                if not raw_list:
                    continue

                _LAST_GOOD_MODEL = (model, time.time())
                mode_str = f"gemini:{model}"
                norm_list = [_normalize_parsed_offer(item, ref_today, mode_str) for item in raw_list[:20]]
                # Enforce detected store folder/filename when Gemini missed the store on a single-store batch
                for item in norm_list:
                    if item.get("branches") == ["ALL"] and len(batch_detected_stores) == 1:
                        st = batch_detected_stores[0]
                        item["branches"] = [st]
                        item["branches_csv"] = st
                        item["store_selection_mode"] = "explicit_stores"
                        item["matched_store_count"] = 1
                    _enrich_offer_location_from_branches(item, catalog)
                return norm_list, mode_str, ""
            except Exception as exc:
                last_err = f"{model}: {exc}"
                continue
        if attempt_round == 0:
            time.sleep(1.5)
            ordered_models = _ordered_vision_models(models)

    return None, "", last_err


def parse_whatsapp_offers(
    raw_text: str = "",
    images: Optional[Sequence[Tuple[Any, ...]]] = None,
    today_dt: Optional[date] = None,
    store_hints: Optional[Sequence[str]] = None,
) -> Dict[str, Any]:
    """
    Parse a WhatsApp Channel campaign message and/or 1..250 offer poster images (direct upload or unpacked
    from a `.zip` archive) into structured campaign object(s) for `demand_promo_calendar`.
    - For >8 images, automatically splits into parallel 8-poster Gemini Vision batches and merges duplicates.
    """
    ref_today = today_dt or date.today()
    txt = (raw_text or "").strip()
    img_list = [entry for entry in (images or []) if entry and entry[0]]
    if not txt and not img_list:
        raise ValueError("Please paste a WhatsApp offer message or upload poster image(s) / a .ZIP file.")

    api_key = _get_gemini_api_key()
    if not api_key:
        one = _parse_offer_deterministic(txt, ref_today)
        return {
            "campaigns": [one],
            "parser_mode": "rule_fallback",
            "image_count": len(img_list),
            "warning": "GEMINI_API_KEY not configured — extracted using built-in text rules.",
        }

    n_imgs = len(img_list)
    system_prompt = (
        "You are a retail demand-planning assistant for myG (a leading Kerala consumer electronics, "
        "smartphones, laptops & home appliances retail chain with two store formats: `myG` standard stores "
        "and `myG Future` large-format stores). Extract structured promotional campaign details from a "
        "WhatsApp Channel offer post (English, Malayalam, or Manglish) and/or 1 or more offer poster images.\n\n"
        f"Today's date is {ref_today.strftime('%Y-%m-%d')} ({ref_today.strftime('%A')}).\n"
        f"Valid myG product_lines: {', '.join(KNOWN_PRODUCT_LINES)}, or ALL.\n"
        f"Valid canonical districts: {', '.join(CANONICAL_DISTRICTS)}, or ALL.\n"
        "Valid store_formats values: \"ALL\", \"MYG,MYG_FUTURE\", \"MYG\", \"MYG_FUTURE\".\n"
        "Valid campaign_type values: mega_sale, weekend_dhamaka, exchange_cashback, brand_festival, flash_offer, product_launch.\n"
        "Valid offer_strength values: low, medium, high, mega.\n\n"
        "Return ONLY valid JSON with this exact top-level schema:\n"
        "{\n"
        '  "campaigns": [\n'
        "    {\n"
        '      "campaign_name": "Short clean title (<= 70 chars)",\n'
        '      "campaign_type": "one of the 6 valid types",\n'
        '      "start_date": "YYYY-MM-DD",\n'
        '      "end_date": "YYYY-MM-DD",\n'
        '      "product_lines": ["TV", "REFRIGERATORS"] or ["ALL"],\n'
        '      "item_categories": ["WASHING MACHINE-FL", "MIXER GRINDER"] or ["ALL"] (optional specific myG sub-categories only when poster explicitly narrows to a sub-category like Front Load vs Top Load, Mixer Grinder, Air Fryer, Split AC; otherwise ["ALL"]),\n'
        '      "item_groups": ["APPLE IPHONE 17 256GB", "1.5 TON 3 STAR", "8KG", "55"] or ["ALL"] (optional specific models / capacities / screen sizes only when poster explicitly restricts to exact models/capacities; otherwise ["ALL"]),\n'
        '      "brands": ["LG", "SAMSUNG"] or ["ALL"],\n'
        '      "districts": ["KANNUR", "KASARGOD", "KOZHIKODE"] or ["ALL"],\n'
        '      "store_formats": "ALL | MYG,MYG_FUTURE | MYG | MYG_FUTURE",\n'
        '      "branches": ["EDAPPALLY FUTURE", "KOTTAKKAL FUTURE"] or ["ALL"],\n'
        '      "offer_strength": "low|medium|high|mega",\n'
        '      "expected_lift_pct": number (realistic incremental daily unit lift % over normal baseline — typically 10-25 for weekday promos, 15-35 for weekend/brand/flash promos, and ONLY 60-120 for true storewide Onam/Vishu/Anniversary/Deepavali/Christmas mega events),\n'
        '      "post_cooldown_days": integer (0..10; typically 2-5 for durables mega/weekend sales, 0-2 for accessories),\n'
        '      "discount_summary": "Concise 1-line summary of discount/cashback/EMI/exchange deal"\n'
        "    }\n"
        "  ]\n"
        "}\n"
        "Extraction rules (read poster headers, product tiles, AND bottom location/store footer bars carefully!):\n"
        "- Realistic Lift Calibration (`offer_strength` & `expected_lift_pct`): Do NOT confuse poster discount headlines ('Up to 75% off', 'killer prices', 'massive discounts') with unit sales lift! Because myG runs weekly promos, routine 48-hour flash sales, weekend dhamakas, exchange melas, and brand days realistically add 10%-35% daily unit volume (12%-25% on Mon-Thu weekdays; 18%-35% on Fri-Sun weekends). Reserve `offer_strength: \"mega\"` and lifts of 60%-120% STRICTLY for major annual storewide events (Onam, Vishu, myG Anniversary, Deepavali, Christmas, New Year, Grand Opening).\n"
        "- Store Format (`store_formats`): Read the bottom bar / fine print! If it says 'Visit nearest myG, myG Future Store' or mentions both myG & myG Future -> \"MYG,MYG_FUTURE\". If it explicitly restricts to 'myG Future' stores only -> \"MYG_FUTURE\". If it restricts to 'myG' stores only (excluding myG Future) -> \"MYG\". If no store format restriction is mentioned -> \"ALL\".\n"
        "- Target Districts (`districts`): ALWAYS check the bottom footer strip of posters (e.g., '@ KANNUR | KASARGOD | CALICUT | WAYANAD | PALAKKAD | MALAPPURAM'). Extract EVERY district listed there and map poster spellings to exact canonical names: CALICUT -> KOZHIKODE, WAYANAD -> WAYANADU, KASARAGOD -> KASARGOD, TRICHUR -> THRISSUR, COCHIN/KOCHI -> ERNAKULAM, ALLEPPEY -> ALAPPUZHA, TRIVANDRUM -> THIRUVANANTHAPURAM, MANGALORE -> DAKSHINA KANNADA. Use [\"ALL\"] only if no specific districts are listed.\n"
        "- Specific Target Stores (`branches`): ONLY list specific named branch(es)/showroom(s)/town(s)/mall(s) when the poster (or its filename) explicitly restricts the offer to those individual stores (e.g., 'Grand Anniversary at myG Future Edappally', 'Only at Kottakkal Future & Perinthalmanna', 'Balussery Future Exclusive', or store-wise file 'Edappally Future.jpg') -> e.g. [\"EDAPPALLY FUTURE\"]. Do NOT copy a district footer strip (e.g., 'Visit nearest myG, myG Future Store @ KANNUR | KASARGOD | CALICUT | WAYANAD | PALAKKAD | MALAPPURAM') into `branches` — put those in `districts` and set `branches` to [\"ALL\"] (our system will automatically expand all matching myG / myG Future stores in those districts).\n"
        "- Store-wise pack rule: When multiple posters are store-wise variants of the SAME sale event (e.g. '48 Hours Sale' with different store names on each poster), keep `campaign_name` as the clean common event title (e.g. '48 Hours Sale', WITHOUT appending the store name to `campaign_name`) and list all participating store names in `branches` so they combine cleanly!\n"
        "- Multi-image rules: If multiple posters are slides of the SAME sale event with the same dates and overall store/district scope, merge them into 1 combined campaign entry. If posters represent DISTINCT offers (different dates, districts, or unrelated promos), return ONE entry per distinct offer in `campaigns`.\n"
        "- If only a day/month is mentioned without year, infer the nearest upcoming/current year relative to today.\n"
        "- If 'valid till X' is mentioned without a start date, set start_date to today.\n"
        "- Map synonyms accurately (Fridge -> REFRIGERATORS, AC -> AIR CONDITIONER, LED/Smart TV -> TV, Smartphone/iPhone -> MOBILE, Washing Machine -> WASHING MACHINES, TWS/Earbuds -> EAR WEARABLES, Power Bank -> ACCESSORIES, Speaker -> AUDIO)."
    )

    env_models = [m.strip() for m in os.environ.get("GEMINI_MODELS", "").split(",") if m.strip()]
    models = []
    for m in env_models + [
        "gemini-3.5-flash-lite",
        "gemini-flash-lite-latest",
        "gemini-3.5-flash",
        "gemini-flash-latest",
        "gemini-3-flash-preview",
        "gemini-3.1-flash-lite-preview",
    ]:
        if m not in models:
            models.append(m)

    # Pre-warm branch catalog cache before parallel chunk threads run
    if n_imgs > 0:
        _get_branch_catalog_cached()

    chunk_size = 8
    hints_seq = list(store_hints or [])
    if n_imgs <= chunk_size:
        norm_list, mode_str, last_err = _call_gemini_vision_chunk(
            txt, img_list, 0, system_prompt, api_key, models, ref_today, store_hints=hints_seq
        )
        if norm_list:
            merged_small = _merge_dedup_campaigns(norm_list)[:500]
            attach_clickhouse_planned_lifts(merged_small, ref_today=ref_today)
            return {
                "campaigns": merged_small,
                "parser_mode": mode_str,
                "image_count": n_imgs,
            }
    else:
        chunks = [
            (offset, img_list[offset : offset + chunk_size], hints_seq[offset : offset + chunk_size])
            for offset in range(0, n_imgs, chunk_size)
        ]
        chunk_results: List[Tuple[Optional[List[Dict[str, Any]]], str, str]] = [
            (None, "", "") for _ in chunks
        ]
        with ThreadPoolExecutor(max_workers=min(2, len(chunks))) as pool:
            futs = [
                (
                    i,
                    pool.submit(
                        _call_gemini_vision_chunk,
                        txt,
                        ch_imgs,
                        offset,
                        system_prompt,
                        api_key,
                        models,
                        ref_today,
                        ch_hints,
                    ),
                )
                for i, (offset, ch_imgs, ch_hints) in enumerate(chunks)
            ]
            for i, fut in futs:
                chunk_results[i] = fut.result()

        combined: List[Dict[str, Any]] = []
        used_modes: List[str] = []
        failed_chunks = 0
        last_err = ""
        for norm_list, mode_str, err_str in chunk_results:
            if norm_list:
                combined.extend(norm_list)
                if mode_str and mode_str not in used_modes:
                    used_modes.append(mode_str)
            else:
                failed_chunks += 1
                if err_str:
                    last_err = err_str

        if combined:
            merged = _merge_dedup_campaigns(combined)[:500]
            attach_clickhouse_planned_lifts(merged, ref_today=ref_today)
            out: Dict[str, Any] = {
                "campaigns": merged,
                "parser_mode": used_modes[0] if used_modes else "gemini",
                "image_count": n_imgs,
                "batch_count": len(chunks),
            }
            if failed_chunks:
                out["warning"] = (
                    f"{len(chunks) - failed_chunks}/{len(chunks)} poster batches extracted; "
                    f"{failed_chunks} batch(es) skipped ({last_err[:80]})."
                )
            return out

    # Never fabricate a fake empty "myG Store Offer" on image-only uploads when Gemini Vision is temporarily busy (429/503)
    if n_imgs > 0 and not txt:
        raise VisionUnavailableError(f"Gemini Vision temporarily busy/rate-limited ({last_err[:120]}). Please retry this batch.")

    fallback = _parse_offer_deterministic(txt, ref_today)
    attach_clickhouse_planned_lifts([fallback], ref_today=ref_today)
    return {
        "campaigns": [fallback],
        "parser_mode": "rule_fallback",
        "image_count": n_imgs,
        "warning": f"AI parser fell back to text rules ({last_err[:120]}). Please verify dates & products.",
    }


def parse_whatsapp_offer(
    raw_text: str = "",
    image_bytes: Optional[bytes] = None,
    image_mime: str = "image/jpeg",
    today_dt: Optional[date] = None,
) -> Dict[str, Any]:
    """Single-offer convenience wrapper around `parse_whatsapp_offers`."""
    imgs = [(image_bytes, image_mime)] if image_bytes else []
    res = parse_whatsapp_offers(raw_text=raw_text, images=imgs, today_dt=today_dt)
    first = dict(res["campaigns"][0])
    if res.get("warning"):
        first["warning"] = res["warning"]
    return first
