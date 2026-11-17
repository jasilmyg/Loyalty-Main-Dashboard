"""
Daily-resolution recency features for the demand model.

The 15-day buckets used by the model cannot see *when* inside the window the sales happened. These features
add that: last-3/last-7 day sales, the 14-day median, single-day spike share and number of selling days.

One query per origin returns net daily units for the 29 days before the origin; the two 14-day windows
(k=0: the 14 days before the origin, k=1: the 14 days before origin-15d) are derived from it in pandas, so the
same function builds the training rows (fold origin = R-15d -> k=1) and the live row (origin = R -> k=0).
"""
from __future__ import annotations

from datetime import date, timedelta
from typing import Dict, List

import numpy as np
import pandas as pd

WINDOW = 29            # days fetched (ages 0..28)


def daily_sql(ref_dt: date, where_sql: str) -> str:
    d_ref = ref_dt.strftime("%Y-%m-%d")
    start = (ref_dt - timedelta(days=WINDOW - 1)).strftime("%Y-%m-%d 00:00:00")
    end = (ref_dt + timedelta(days=1)).strftime("%Y-%m-%d 00:00:00")
    return f"""
        SELECT s.item_code AS id,
               dateDiff('day', toDate(s.date), toDate('{d_ref}')) AS age,
               greatest(0.0, sum(if(s.sold_price < 0, -abs(toFloat64(s.qty)), abs(toFloat64(s.qty))))) AS u
        FROM azure_sales_report s
        LEFT JOIN item_master m ON s.item_code = m.item_code
        WHERE s.date >= toDateTime('{start}') AND s.date < toDateTime('{end}') AND {where_sql}
        GROUP BY s.item_code, age
    """


def fetch_daily_rows(ch, ref_dt: date, sales_where: List[str]) -> pd.DataFrame:
    rows = ch.query(daily_sql(ref_dt, " AND ".join(sales_where))).result_rows
    df = pd.DataFrame(rows, columns=["id", "age", "u"])
    if not df.empty:
        df["age"] = pd.to_numeric(df["age"], errors="coerce").fillna(-1).astype(int)
        df["u"] = pd.to_numeric(df["u"], errors="coerce").fillna(0.0)
        df = df[(df["age"] >= 0) & (df["age"] < WINDOW)]
    return df


def window_features(daily_rows: pd.DataFrame, ids, k: int) -> Dict[str, np.ndarray]:
    """Features of the 14-day window that ends 15*k days before the origin, aligned to `ids` (zeros if absent)."""
    ids = pd.Index(ids)
    mat = np.zeros((len(ids), WINDOW), dtype=float)
    if daily_rows is not None and len(daily_rows):
        pos = ids.get_indexer(daily_rows["id"].values)
        ok = pos >= 0
        np.add.at(mat, (pos[ok], daily_rows["age"].values[ok]), daily_rows["u"].values[ok])
    lo = 15 * k
    win = mat[:, lo:lo + 14]
    tot = win.sum(axis=1)
    mx = win.max(axis=1)
    win7 = mat[:, lo:lo + 7]
    sum7 = win7.sum(axis=1)
    peak_in_7 = np.argmax(win, axis=1) < 7
    # mean daily units of the latest 7 days, with the 14-day peak day removed when it falls inside them
    r7ex = np.where(peak_in_7, (sum7 - mx) / 6.0, sum7 / 7.0)
    return {
        "r7ex": np.maximum(r7ex, 0.0),
        "w3": mat[:, lo:lo + 3].sum(axis=1),
        "w7": mat[:, lo:lo + 7].sum(axis=1),
        "med14": np.median(win, axis=1),
        "maxsh14": np.where(tot > 0, mx / np.maximum(tot, 1e-9), 0.0),
        "nz14": (win > 0).sum(axis=1).astype(float),
        "max14": mx,
        "sum14": tot,
    }
