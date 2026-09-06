import os
import sys
import pandas as pd
import numpy as np
import time

output_dir = 'Final_Global_Customers'
os.makedirs(output_dir, exist_ok=True)

print("1. Loading Set A: Global Filtered Customers (4,081,546 expected)...")
set_a = set()
global_dir = 'Exported_Global_Customers_Filtered'
for f in os.listdir(global_dir):
    if f.endswith('.xlsx') and not f.startswith('~$'):
        df = pd.read_excel(os.path.join(global_dir, f))
        for val in df['Mobile Number'].dropna():
            set_a.add(str(val).split('.')[0].strip())
print(f" -> Loaded {len(set_a)} numbers into Set A.")

print("\\n2. Loading Set B: Extracted Branch Customers (1,924,193 expected)...")
set_b = set()
branches_dir = 'Exported_Branches'
for f in os.listdir(branches_dir):
    if f.endswith('.xlsx') and not f.startswith('~$'):
        df = pd.read_excel(os.path.join(branches_dir, f))
        for val in df['Mobile Number'].dropna():
            set_b.add(str(val).split('.')[0].strip())
print(f" -> Loaded {len(set_b)} numbers into Set B.")

print("\\n3. Loading Set C: Failed Data...")
set_c = set()
df_failed = pd.read_excel('Failed Data 3.xlsx', header=None)
for val in df_failed[0].dropna():
    set_c.add(str(val).split('.')[0].strip())
print(f" -> Loaded {len(set_c)} numbers into Set C.")

print("\\n4. Calculating Sets...")
# The numbers to definitively remove are those in the 35 branches, UNLESS they failed.
set_to_remove = set_b - set_c
print(f" -> Numbers in branches but NOT failed (will be removed): {len(set_to_remove)}")

# The final set is the global set minus the set to remove.
final_set = set_a - set_to_remove
print(f" -> Final set size to export: {len(final_set)}")

print("\\n5. Splitting and saving...")
final_list = list(final_set)
chunks = np.array_split(final_list, 5)

for i, chunk in enumerate(chunks, 1):
    print(f"  -> Writing Part {i} with {len(chunk)} numbers...")
    df = pd.DataFrame(chunk, columns=['Mobile Number'])
    file_path = os.path.join(output_dir, f'Final_Global_Customers_Part_{i}.xlsx')
    df.to_excel(file_path, index=False)
    print(f"     [OK] Saved {file_path}")

print("\\nSUCCESS: Operation completed!")
