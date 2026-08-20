import pandas as pd

path = r"C:\Users\jasil_myg\Desktop\myG Loyalty Main Dashboard\myg_loyalty_dashboard\DSR AUG 17-19 2026.xlsx"

# Check all sheets
xl = pd.ExcelFile(path)
print("Sheets:", xl.sheet_names)

# Read first sheet
df = pd.read_excel(path, sheet_name=0)
print(f"\nShape: {df.shape}")
print("\nColumns:")
for c in df.columns:
    print(f"  '{c}'")
print("\nFirst 3 rows:")
print(df.head(3).to_string())
print("\nDtypes:")
print(df.dtypes)
