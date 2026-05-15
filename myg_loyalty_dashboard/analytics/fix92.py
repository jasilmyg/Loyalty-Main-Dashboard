"""Fix the corrupted line 92 in services.py"""
path = r'c:\Users\jasil_myg\Desktop\myG Loyalty Main Dashboard\myg_loyalty_dashboard\analytics\services.py'
with open(path, 'r', encoding='utf-8') as f:
    lines = f.readlines()

print(f"Line 92: {repr(lines[91])}")

# Fix line 92 (index 91)
lines[91] = '        date_expr = f\'CAST({prefix}\\"Date\\" AS DATE)\'\r\n'

with open(path, 'w', encoding='utf-8') as f:
    f.writelines(lines)

print("Fixed! New line 92:", repr(lines[91]))

# Verify syntax
import py_compile
try:
    py_compile.compile(path, doraise=True)
    print("Syntax OK!")
except py_compile.PyCompileError as e:
    print(f"Syntax error: {e}")
