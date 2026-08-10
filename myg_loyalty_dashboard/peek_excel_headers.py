"""
peek_excel_headers.py
Reads the header row of all large Excel files to identify
which ones contain item-wise and invoice-wise data.
"""
import os
import pandas as pd

FILES = [
    r"C:\Users\jasil_myg\Desktop\myG Loyalty Main Dashboard\myg_loyalty_dashboard\APR 2026.xlsx",
    r"C:\Users\jasil_myg\Desktop\myG Loyalty Main Dashboard\myg_loyalty_dashboard\MAY 2026 PRODUCT.xlsx",
]

# Also check project_folder
import glob
extra = glob.glob(r"C:\Users\jasil_myg\Desktop\myG Loyalty Main Dashboard\**\*.xlsx", recursive=True)
FILES += extra

seen = set()
for path in FILES:
    if not os.path.exists(path):
        continue
    if path in seen:
        continue
    seen.add(path)
    try:
        df = pd.read_excel(path, nrows=2, engine='calamine')
        cols = df.columns.tolist()
        name = os.path.basename(path)
        size_mb = os.path.getsize(path) / 1024 / 1024
        print(f"\nFile: {name}  ({size_mb:.1f} MB)")
        print(f"Cols: {cols}")
    except Exception as e:
        print(f"  ERROR reading {path}: {e}")
