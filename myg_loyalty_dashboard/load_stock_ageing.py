"""
load_stock_ageing.py
====================
Loads a stock-ageing Excel snapshot (the "Detail" sheet of
stock_report_<DD>_<Month>_<YYYY>_all_products.xlsx) into the ClickHouse table
`stock_ageing_report`, which feeds the "Product Wise Stock Ageing Report".

Each Excel file is one dated snapshot, stored under `report_date`
(the table is partitioned by that date, so history is kept and the report always
reads the latest snapshot unless told otherwise).

Usage (run from the myg_loyalty_dashboard folder):
    python load_stock_ageing.py ..\\exports\\stock_report_18_October_2026_all_products.xlsx
    python load_stock_ageing.py <xlsx> --date 2026-10-18     # override the date parsed from the file name
    python load_stock_ageing.py <xlsx> --replace             # drop & reload that day's snapshot

Connection settings come from analytics.clickhouse_service (CH_* environment
variables), so no credentials live in this script.
"""
import argparse
import os
import re
import sys
import time
from datetime import date, datetime

import pandas as pd
import python_calamine

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from analytics.clickhouse_service import get_ch_client  # noqa: E402

TABLE = 'stock_ageing_report'

# (Excel header, ClickHouse column, kind)   kind: s = string, f32 / f64 = float
COLUMNS = [
    ('BRANCH', 'branch', 's'),
    ('Item code', 'item_code', 's'),
    ('SUPPLIER CODE', 'supplier_code', 's'),
    ('SUPPLIER NAME', 'supplier_name', 's'),
    ('PRODUCT', 'product', 's'),
    ('BRAND', 'brand', 's'),
    ('ITEM', 'item', 's'),
    ('ITEM CATEGORY', 'item_category', 's'),
    ('ITEM GROUP', 'item_group', 's'),
    ('DAMAGED', 'damaged', 'f32'),
    ('DISPLAYED', 'displayed', 'f32'),
    ('ADVANCE', 'advance', 'f32'),
    ('NON SALEABLE TOTAL', 'non_saleable_total', 'f32'),
    ('IMEI/BATCH', 'imei_batch', 's'),
    ('PURCHASE PRICE', 'purchase_price', 'f64'),
    ('TAX', 'tax', 'f32'),
    ('Dealer Price', 'dealer_price', 'f64'),
    ('COST PRICE', 'cost_price', 'f64'),
    ('MOP', 'mop', 'f64'),
    ('KA MOP', 'ka_mop', 'f64'),
    ('MRP', 'mrp', 'f64'),
    ('TOTAL AGE', 'total_age', 'f32'),
    ('BRANCH AGE', 'branch_age', 'f32'),
    ('STOCK', 'stock', 'f32'),
    ('BOOKED QTY', 'booked_qty', 'f32'),
    ('TRANSIT', 'transit', 'f32'),
    ('GIT(DAYS)', 'git_days', 'f32'),
    ('NON SALEABLE AGEING(DAYS)', 'non_saleable_ageing_days', 'f32'),
]

DDL = f"""
CREATE TABLE IF NOT EXISTS {TABLE}
(
    report_date                Date,
    branch                     LowCardinality(String),
    item_code                  String,
    supplier_code              String,
    supplier_name              LowCardinality(String),
    product                    LowCardinality(String),
    brand                      LowCardinality(String),
    item                       String,
    item_category              LowCardinality(String),
    item_group                 String,
    damaged                    Float32,
    displayed                  Float32,
    advance                    Float32,
    non_saleable_total         Float32,
    imei_batch                 String,
    purchase_price             Float64,
    tax                        Float32,
    dealer_price               Float64,
    cost_price                 Float64,
    mop                        Float64,
    ka_mop                     Float64,
    mrp                        Float64,
    total_age                  Float32,
    branch_age                 Float32,
    stock                      Float32,
    booked_qty                 Float32,
    transit                    Float32,
    git_days                   Float32,
    non_saleable_ageing_days   Float32,
    loaded_at                  DateTime DEFAULT now()
)
ENGINE = MergeTree
PARTITION BY report_date
ORDER BY (report_date, product, branch, item_code)
"""


def parse_date_from_name(path):
    m = re.search(r'stock_report_(\d{1,2})_([A-Za-z]+)_(\d{4})', os.path.basename(path))
    if not m:
        return None
    try:
        return datetime.strptime(f'{m.group(1)} {m.group(2)} {m.group(3)}', '%d %B %Y').date()
    except ValueError:
        return None


def connect(retries=5, wait=15):
    for attempt in range(1, retries + 1):
        client = get_ch_client()
        if client is not None:
            return client
        print(f'  ClickHouse not reachable (attempt {attempt}/{retries}); retrying in {wait}s...', flush=True)
        time.sleep(wait)
    sys.exit('ERROR: could not connect to ClickHouse.')


def read_detail_sheet(xlsx):
    print(f'Reading {xlsx} ...', flush=True)
    t0 = time.time()
    wb = python_calamine.CalamineWorkbook.from_path(xlsx)
    data = wb.get_sheet_by_name('Detail').to_python()
    df = pd.DataFrame(data[1:], columns=data[0])
    print(f'  {len(df):,} rows read in {time.time() - t0:.1f}s', flush=True)
    missing = [h for h, _, _ in COLUMNS if h not in df.columns]
    if missing:
        sys.exit(f'ERROR: Detail sheet is missing expected columns: {missing}')
    return df


def clean(df, report_date):
    out = pd.DataFrame({'report_date': [report_date] * len(df)})
    for header, col, kind in COLUMNS:
        s = df[header]
        if kind == 's':
            out[col] = s.fillna('').astype(str)
        else:
            out[col] = pd.to_numeric(s, errors='coerce').fillna(0.0).astype('float32' if kind == 'f32' else 'float64')
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('xlsx')
    ap.add_argument('--date', help='Snapshot date YYYY-MM-DD (default: parsed from the file name)')
    ap.add_argument('--replace', action='store_true', help='Drop and reload this date if it already exists')
    args = ap.parse_args()

    xlsx = os.path.abspath(args.xlsx)
    if not os.path.exists(xlsx):
        sys.exit(f'ERROR: file not found: {xlsx}')
    report_date = date.fromisoformat(args.date) if args.date else parse_date_from_name(xlsx)
    if report_date is None:
        sys.exit('ERROR: could not read the date from the file name; pass --date YYYY-MM-DD')
    print(f'Snapshot date: {report_date}')

    client = connect()
    client.command(DDL)
    print(f'Table `{TABLE}` is ready.')

    existing = client.query(f"SELECT count() FROM {TABLE} WHERE report_date = '{report_date}'").result_rows[0][0]
    if existing and not args.replace:
        sys.exit(f'SKIPPED: {TABLE} already has {existing:,} rows for {report_date}. Use --replace to reload.')

    df = clean(read_detail_sheet(xlsx), report_date)
    src_rows = len(df)
    src_stock = float(df['stock'].astype('float64').sum())
    src_mop_val = float((df['stock'].astype('float64') * df['mop'].where(df['mop'] != 0, df['cost_price'])).sum())

    if existing:
        print(f'Dropping existing snapshot ({existing:,} rows) for {report_date} ...')
        client.command(f"ALTER TABLE {TABLE} DROP PARTITION '{report_date}'")

    CHUNK = 100_000
    for i in range(0, src_rows, CHUNK):
        client.insert_df(TABLE, df.iloc[i:i + CHUNK])
        print(f'  inserted {min(i + CHUNK, src_rows):,} / {src_rows:,}', flush=True)

    # Verify against the source file
    n, stock, mop_val = client.query(
        f"SELECT count(), sum(stock), sum(stock * if(mop != 0, mop, cost_price)) "
        f"FROM {TABLE} WHERE report_date = '{report_date}'"
    ).result_rows[0]
    print('\n=== VERIFICATION ===')
    print(f'  rows       : source {src_rows:,}  | clickhouse {n:,}')
    print(f'  total stock: source {src_stock:,.0f}  | clickhouse {stock:,.0f}')
    print(f'  MOP value  : source Rs.{src_mop_val / 1e7:,.2f} Cr | clickhouse Rs.{mop_val / 1e7:,.2f} Cr')
    ok = n == src_rows and abs(stock - src_stock) < 1 and abs(mop_val - src_mop_val) / max(src_mop_val, 1) < 1e-6
    print('  RESULT     :', 'OK - matches source' if ok else 'MISMATCH - investigate')
    sys.exit(0 if ok else 1)


if __name__ == '__main__':
    main()
