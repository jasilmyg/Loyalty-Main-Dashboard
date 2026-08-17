import sys
path = 'myg_loyalty_dashboard/analytics/services.py'
with open(path, 'r', encoding='utf-8') as f:
    content = f.read()
content = content.replace("cache_key = '", "cache_key = 'v2_")
with open(path, 'w', encoding='utf-8') as f:
    f.write(content)
print('Updated cache keys in services.py')
