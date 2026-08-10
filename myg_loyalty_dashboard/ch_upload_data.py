"""
ch_upload_data.py
==================
Standard script to append new Excel DSR data into the two canonical ClickHouse tables:
  - item_wise_sales_data   (item-level rows)
  - invoice_wise_sales_data (invoice-level rows)

Usage:
    python ch_upload_data.py --item  "path/to/item_wise.xlsx"
    python ch_upload_data.py --inv   "path/to/invoice_wise.xlsx"
    python ch_upload_data.py --item  "path/to/item.xlsx" --inv "path/to/inv.xlsx"

Rules:
  - Deduplicates by invoice_no + date + branch (won't insert same row twice)
  - Never creates new tables — always appends to the 2 canonical tables
  - Prints row counts before and after
"""

import os, sys, django, argparse
import pandas as pd

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'myg_loyalty_dashboard.settings')
django.setup()

from analytics.clickhouse_service import get_ch_client

# ── Column maps: Excel column name -> ClickHouse column name ─────────────────
ITEM_COL_MAP = {
    "Date":        "date",
    "Invoice No":  "invoice_no",
    "Branch":      "branch",
    "Item Code":   "item_code",
    "IMEI/Batch":  "imei_batch",
    "QTY":         "qty",
    "MOP":         "mop",
    "Discount":    "discount",
    "Buyback":     "buyback",
    "Sold Price":  "sold_price",
    "Taxable":     "taxable",
}

INV_COL_MAP = {
    "Date":                   "date",
    "Time":                   "time",
    "Invoice No":             "invoice_no",
    "Branch":                 "branch",
    "RBM":                    "rbm",
    "BDM":                    "bdm",
    "Customer Bill To No":    "customer_bill_to_no",
    "Customer Bill To Pincode": "customer_bill_to_pincode",
    "Customer Bill To GSTIN": "customer_bill_to_gstin",
    "Customer Type":          "customer_type",
    "Sales Staff Code":       "sales_staff_code",
    "Billing Staff Code":     "billing_staff_code",
    "Invoice Total":          "invoice_total",
    "Discount":               "discount",
    "Buyback":                "buyback",
}

ITEM_TABLE = "item_wise_sales_data"
INV_TABLE  = "invoice_wise_sales_data"


def get_existing_keys(client, table, key_col="invoice_no"):
    """Returns a set of existing key values to avoid duplicates."""
    print(f"   Fetching existing {key_col}s from {table}...")
    rows = client.query(f"SELECT DISTINCT {key_col} FROM {table}").result_rows
    return set(r[0] for r in rows)


def upload_item_wise(client, excel_path):
    print(f"\n{'=' * 60}")
    print(f"  Uploading ITEM WISE data")
    print(f"  File: {excel_path}")
    print(f"{'=' * 60}")

    df = pd.read_excel(excel_path, engine="calamine")
    print(f"  Read {len(df):,} rows from Excel")
    print(f"  Columns: {df.columns.tolist()}")

    # Rename columns to match ClickHouse
    df = df.rename(columns=ITEM_COL_MAP)

    # Keep only columns that exist in ClickHouse table
    ch_cols = list(ITEM_COL_MAP.values())
    available = [c for c in ch_cols if c in df.columns]
    df = df[available].copy()
    print(f"  Matched {len(available)} columns: {available}")

    # Type coercion
    for c in ["qty"]:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0).astype(int)
    for c in ["mop", "discount", "buyback", "sold_price", "taxable"]:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0.0)
    for c in ["date", "invoice_no", "branch", "item_code", "imei_batch"]:
        if c in df.columns:
            df[c] = df[c].astype(str).str.strip()

    # Deduplicate against existing data
    existing = get_existing_keys(client, ITEM_TABLE, "invoice_no")
    before = len(df)
    if "invoice_no" in df.columns:
        df = df[~df["invoice_no"].isin(existing)]
    after = len(df)
    print(f"  Dedup: {before:,} → {after:,} rows ({before - after:,} already in DB)")

    if after == 0:
        print("  ✓ No new rows to insert.")
        return

    # Count before
    count_before = client.query(f"SELECT count() FROM {ITEM_TABLE}").result_rows[0][0]
    print(f"  Rows in {ITEM_TABLE} BEFORE: {count_before:,}")

    # Insert
    rows_list = [tuple(row) for row in df.itertuples(index=False, name=None)]
    client.insert(ITEM_TABLE, rows_list, column_names=available)
    print(f"  ✓ Inserted {after:,} rows into {ITEM_TABLE}")

    # Count after
    count_after = client.query(f"SELECT count() FROM {ITEM_TABLE}").result_rows[0][0]
    print(f"  Rows in {ITEM_TABLE} AFTER : {count_after:,}  (+{count_after - count_before:,})")


def upload_invoice_wise(client, excel_path):
    print(f"\n{'=' * 60}")
    print(f"  Uploading INVOICE WISE data")
    print(f"  File: {excel_path}")
    print(f"{'=' * 60}")

    df = pd.read_excel(excel_path, engine="calamine")
    print(f"  Read {len(df):,} rows from Excel")
    print(f"  Columns: {df.columns.tolist()}")

    # Rename columns to match ClickHouse
    df = df.rename(columns=INV_COL_MAP)

    # Keep only columns that exist in ClickHouse table
    ch_cols = list(INV_COL_MAP.values())
    available = [c for c in ch_cols if c in df.columns]
    df = df[available].copy()
    print(f"  Matched {len(available)} columns: {available}")

    # Type coercion
    for c in ["customer_bill_to_no", "customer_bill_to_pincode"]:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0).astype(int)
    for c in ["invoice_total", "discount", "buyback"]:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0.0)
    for c in ["date", "time", "invoice_no", "branch", "rbm", "bdm",
              "customer_bill_to_gstin", "customer_type",
              "sales_staff_code", "billing_staff_code"]:
        if c in df.columns:
            df[c] = df[c].astype(str).str.strip()

    # Deduplicate against existing data
    existing = get_existing_keys(client, INV_TABLE, "invoice_no")
    before = len(df)
    if "invoice_no" in df.columns:
        df = df[~df["invoice_no"].isin(existing)]
    after = len(df)
    print(f"  Dedup: {before:,} → {after:,} rows ({before - after:,} already in DB)")

    if after == 0:
        print("  ✓ No new rows to insert.")
        return

    # Count before
    count_before = client.query(f"SELECT count() FROM {INV_TABLE}").result_rows[0][0]
    print(f"  Rows in {INV_TABLE} BEFORE: {count_before:,}")

    # Insert
    rows_list = [tuple(row) for row in df.itertuples(index=False, name=None)]
    client.insert(INV_TABLE, rows_list, column_names=available)
    print(f"  ✓ Inserted {after:,} rows into {INV_TABLE}")

    # Count after
    count_after = client.query(f"SELECT count() FROM {INV_TABLE}").result_rows[0][0]
    print(f"  Rows in {INV_TABLE} AFTER : {count_after:,}  (+{count_after - count_before:,})")


# ── Main ─────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Upload data to ClickHouse canonical tables")
    parser.add_argument("--item", help="Path to item-wise Excel file")
    parser.add_argument("--inv",  help="Path to invoice-wise Excel file")
    args = parser.parse_args()

    if not args.item and not args.inv:
        print("ERROR: Provide at least one of --item or --inv")
        print("Usage:")
        print('  python ch_upload_data.py --item "path/to/item_wise.xlsx"')
        print('  python ch_upload_data.py --inv  "path/to/invoice_wise.xlsx"')
        exit(1)

    client = get_ch_client()
    if not client:
        print("ERROR: Cannot connect to ClickHouse")
        exit(1)

    if args.item:
        upload_item_wise(client, args.item)

    if args.inv:
        upload_invoice_wise(client, args.inv)

    print("\n✓ All uploads complete.")
