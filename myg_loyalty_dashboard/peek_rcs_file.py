import pandas as pd

rcs_path = r"C:\Users\jasil_myg\Desktop\myG Loyalty Main Dashboard\myg_loyalty_dashboard\350 RCS LOYALTY POINTS DATA.xlsx"

df = pd.read_excel(rcs_path, engine='calamine', nrows=5)
print("Shape (first 5 rows):", df.shape)
print("Columns:", list(df.columns))
print(df.head())

# Full count
df_full = pd.read_excel(rcs_path, engine='calamine')
print(f"\nTotal rows in RCS file: {len(df_full):,}")
print("Column dtypes:\n", df_full.dtypes)
