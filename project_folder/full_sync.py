import sys, os, subprocess

# 1. Run the heavy SQLite import
print("==========================================")
print("1. Starting main SQLite sync (89 files)...")
print("==========================================")
try:
    subprocess.run([sys.executable, "sync_data.py"], check=True)
except Exception as e:
    print("Error in sync_data.py:", e)
    sys.exit(1)

# 2. Rebuild DuckDB cache
print("\n==========================================")
print("2. Rebuilding DuckDB cache...")
print("==========================================")
try:
    duck_script = os.path.join("..", "myg_loyalty_dashboard", "rebuild_duck.py")
    subprocess.run([sys.executable, duck_script], check=True)
except Exception as e:
    print("Error in rebuild_duck.py:", e)
    sys.exit(1)

print("\n✅ FULL SYNC COMPLETE!")
