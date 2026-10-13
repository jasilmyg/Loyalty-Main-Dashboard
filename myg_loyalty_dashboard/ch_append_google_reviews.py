"""
ch_append_google_reviews.py
===========================
Append ONLY the new rows of the Google Sheet to the ClickHouse `google_reviews` table.

Why not ch_load_google_reviews.py?  That script DROPs and recreates the table, which would wipe the
columns added later (hf_category, hf_confidence, dim_*).  This one never deletes or rewrites anything.

How "new" is decided (the sheet has no review id): a row is identified by
    (store_name, updated_at, reviewer_name, rating, comment)
and compared as a multiset, so genuinely repeated reviews are handled correctly: if the sheet holds 6
copies of a row and ClickHouse holds 5, exactly 1 is added.

Usage (run from this folder):
    python ch_append_google_reviews.py            # dry run: shows what would be added, changes nothing
    python ch_append_google_reviews.py --apply    # inserts the new rows

New rows get the same derived columns the original loader produced (sentiment_score,
sentiment_category, rca_category, rca_subcategory).  The AI columns (hf_*, dim_*) are left empty for
non-empty comments; rows with no text get the same "Empty/No Comment" values as existing rows.
"""
import io
import os
import sys
from collections import Counter

HERE = os.path.dirname(os.path.abspath(__file__))
os.chdir(HERE)
sys.path.insert(0, HERE)
if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

import pandas as pd

# importing the original loader gives us the exact same sheet reader and classifiers (main is guarded)
from ch_load_google_reviews import get_google_sheet_data, analyze_sentiment_and_rca
from analytics.clickhouse_service import get_ch_client

RENAME = {
    'Dots \u2192 Connectedto': 'store_name',
    'Updatedat': 'updated_at',
    'Rating': 'rating',
    'Reviewer: Displayname': 'reviewer_name',
    'Comment': 'comment',
}
BASE_COLS = ['store_name', 'updated_at', 'rating', 'reviewer_name', 'comment',
             'sentiment_score', 'sentiment_category', 'rca_category', 'rca_subcategory']
KEY_COLS = ['store_name', 'updated_at', 'reviewer_name', 'rating', 'comment']
CHUNK = 5000


def normalise(df):
    """Same cleaning the original loader applies, so keys match what is already in ClickHouse."""
    missing = [c for c in RENAME if c not in df.columns]
    if missing:
        raise SystemExit(f"Sheet is missing expected columns {missing}. Sheet columns: {df.columns.tolist()}")
    df = df.rename(columns=RENAME)
    df['store_name'] = df['store_name'].fillna('').astype(str)
    df['updated_at'] = df['updated_at'].fillna('').astype(str)
    df['rating'] = pd.to_numeric(df['rating'], errors='coerce').fillna(0).astype(int)
    df['reviewer_name'] = df['reviewer_name'].fillna('').astype(str)
    df['comment'] = df['comment'].fillna('').astype(str)
    return df


def main(apply):
    ch = get_ch_client()
    if not ch:
        raise SystemExit("Cannot connect to ClickHouse")

    sheet = get_google_sheet_data()
    print(f"Sheet rows: {len(sheet):,}   columns: {sheet.columns.tolist()}")
    sheet = normalise(sheet)

    before = ch.query("SELECT count() FROM google_reviews").result_rows[0][0]
    print(f"ClickHouse rows: {before:,}")
    existing = Counter(map(tuple, ch.query(f"SELECT {', '.join(KEY_COLS)} FROM google_reviews").result_rows))

    new_idx = []
    for i, key in enumerate(sheet[KEY_COLS].itertuples(index=False, name=None)):
        if existing[key] > 0:
            existing[key] -= 1
        else:
            new_idx.append(i)
    only_in_ch = sum(existing.values())
    new = sheet.iloc[new_idx].copy()
    print(f"New rows to add: {len(new):,}   |   rows in ClickHouse but not in sheet: {only_in_ch:,} (left untouched)")

    if len(new):
        print("\nBy store (top 10):\n" + new['store_name'].value_counts().head(10).to_string())
        print("\nBy rating:\n" + new['rating'].value_counts().sort_index().to_string())
        sample = new.head(5)[['store_name', 'updated_at', 'rating', 'comment']]
        sample['comment'] = sample['comment'].str.slice(0, 80)
        print("\nSample:\n" + sample.to_string(index=False))

    if not len(new):
        print("\nNothing to add - ClickHouse is already up to date.")
        return
    if not apply:
        print("\nDRY RUN - nothing was written. Re-run with --apply to insert these rows.")
        return

    # derive sentiment + RCA columns exactly like the original loader (it expects a 'Comment' column)
    tmp = new.rename(columns={'comment': 'Comment'})
    tmp = analyze_sentiment_and_rca(tmp).rename(columns={'Comment': 'comment'})
    new['sentiment_score'] = pd.to_numeric(tmp['sentiment_score'], errors='coerce').fillna(0.0).astype(float).values
    new['sentiment_category'] = tmp['sentiment_category'].astype(str).values
    new['rca_category'] = tmp['rca_category'].astype(str).values
    new['rca_subcategory'] = tmp['rca_subcategory'].astype(str).values

    cols = list(BASE_COLS)
    extra = {}
    empty = new['comment'].str.strip().str.len() < 2        # same rule st_multidim_classify used
    if empty.any():
        extra = {'hf_category': 'Empty/No Comment', 'dim_product_sentiment': 'N/A', 'dim_service_sentiment': 'N/A',
                 'dim_primary_root_cause': 'Empty/No Comment', 'dim_secondary_issue': 'N/A',
                 'dim_emotion': 'Neutral', 'dim_suggested_action': 'No action needed'}
        have = {r[0] for r in ch.query("SELECT name FROM system.columns WHERE table='google_reviews' AND database=currentDatabase()").result_rows}
        extra = {k: v for k, v in extra.items() if k in have}
        for k, v in extra.items():
            new[k] = [v if e else '' for e in empty]
        cols += list(extra)

    rows = [tuple(r) for r in new[cols].itertuples(index=False, name=None)]
    print(f"\nInserting {len(rows):,} rows ...")
    for s in range(0, len(rows), CHUNK):
        ch.insert('google_reviews', rows[s:s + CHUNK], column_names=cols)
    after = ch.query("SELECT count() FROM google_reviews").result_rows[0][0]
    print(f"Done. google_reviews: {before:,} -> {after:,} (+{after - before:,}); expected +{len(rows):,}")
    if after - before != len(rows):
        print("WARNING: row count change does not match the number inserted - please check.")


if __name__ == '__main__':
    main(apply='--apply' in sys.argv)
