path = r'c:\Users\jasil_myg\Desktop\myG Loyalty Main Dashboard\myg_loyalty_dashboard\analytics\services.py'
with open(path, 'r', encoding='utf-8') as f:
    lines = f.readlines()

# Print lines around 92 to see exact content
for i in range(89, 96):
    print(f"{i+1}: {repr(lines[i])}")
