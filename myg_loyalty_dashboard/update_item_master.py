"""
update_item_master.py
======================
Replaces ClickHouse item_master table with the new
'Item Master as on 25-09-2026.xlsx' file.

Steps:
  1. Read Excel → map to CH columns
  2. TRUNCATE item_master
  3. INSERT all new rows in batches
"""
import sys, os, pandas as pd
sys.path.insert(0, r'C:\Users\jasil_myg\Desktop\myG Loyalty Main Dashboard\myg_loyalty_dashboard')
import django; os.environ.setdefault('DJANGO_SETTINGS_MODULE','myg_loyalty_dashboard.settings'); django.setup()
from analytics.clickhouse_service import get_ch_client
ch = get_ch_client()

EXCEL_FILE = r'C:\Users\jasil_myg\Desktop\myG Loyalty Main Dashboard\Item Master as on 25-09-2026.xlsx'

print("="*60)
print("  Item Master Update — ClickHouse")
print("="*60)
print()

# ── 1. Read Excel ─────────────────────────────────────────────
print("[1] Reading Excel file...")
df = pd.read_excel(EXCEL_FILE)
print(f"    Rows loaded: {len(df):,}")
print(f"    Columns: {list(df.columns)}")

# ── 2. Map columns → ClickHouse schema ────────────────────────
COL_MAP = {
    'Item Code'     : 'item_code',
    'Product'       : 'product',
    'Brand'         : 'brand',
    'Category'      : 'category',
    'Item'          : 'item_name',
    'Item Group'    : 'item_group',
    'Item Category' : 'item_category',
    'HSN'           : 'hsn',
    'Tax %'         : 'tax_percent',
    'Mop'           : 'mop',
    'Mrp'           : 'mrp',
}
df = df.rename(columns=COL_MAP)

# Keep only CH columns
CH_COLS = ['item_code','product','brand','category','item_name','item_group','item_category','hsn','tax_percent','mop','mrp']
df = df[CH_COLS].copy()

# Clean / type fix
df['item_code']    = df['item_code'].astype(str).str.strip()
df['product']      = df['product'].astype(str).str.strip().fillna('')
df['brand']        = df['brand'].astype(str).str.strip().fillna('')
df['category']     = df['category'].astype(str).str.strip().fillna('')
df['item_name']    = df['item_name'].astype(str).str.strip().fillna('')
df['item_group']   = df['item_group'].astype(str).str.strip().fillna('')
df['item_category']= df['item_category'].astype(str).str.strip().fillna('')
df['hsn']          = df['hsn'].astype(str).str.strip().fillna('')
df['tax_percent']  = pd.to_numeric(df['tax_percent'], errors='coerce').fillna(0.0).astype(float)
df['mop']          = pd.to_numeric(df['mop'], errors='coerce').fillna(0.0).astype(float)
df['mrp']          = pd.to_numeric(df['mrp'], errors='coerce').fillna(0.0).astype(float)

# Drop rows with no item_code
df = df[df['item_code'].notna() & (df['item_code'] != '') & (df['item_code'] != 'nan')]
print(f"    Clean rows: {len(df):,}")

# ── 3. TRUNCATE old data ───────────────────────────────────────
print()
print("[2] Truncating existing item_master table...")
old_cnt = ch.query('SELECT count() FROM item_master').result_rows[0][0]
print(f"    Old row count: {old_cnt:,}")
ch.command('TRUNCATE TABLE item_master')
print("    ✅ Truncated.")

# ── 4. INSERT new data in batches ─────────────────────────────
print()
print("[3] Inserting new data...")
BATCH_SIZE = 10000
total_inserted = 0
for i in range(0, len(df), BATCH_SIZE):
    batch = df.iloc[i:i+BATCH_SIZE]
    ch.insert(
        'item_master',
        batch.values.tolist(),
        column_names=CH_COLS
    )
    total_inserted += len(batch)
    print(f"    Inserted {total_inserted:,}/{len(df):,} rows...", end='\r')

print()

# ── 5. Verify ─────────────────────────────────────────────────
print()
print("[4] Verifying...")
new_cnt = ch.query('SELECT count() FROM item_master').result_rows[0][0]
sample  = ch.query("SELECT item_code, item_name, brand, item_category, tax_percent, mop, mrp FROM item_master LIMIT 3").result_rows
cats    = ch.query("SELECT item_category, count() FROM item_master GROUP BY item_category ORDER BY count() DESC LIMIT 10").result_rows

print(f"    New row count : {new_cnt:,}")
print()
print("    Sample rows:")
for r in sample:
    print(f"      {r[0]:15s} | {r[1][:30]:30s} | {r[2]:15s} | {r[3]:20s} | Tax:{r[4]}%")
print()
print("    Top categories:")
for r in cats:
    print(f"      {r[0]:30s} : {r[1]:,}")

print()
print("="*60)
print(f"  ✅ item_master updated: {old_cnt:,} → {new_cnt:,} rows")
print("="*60)
