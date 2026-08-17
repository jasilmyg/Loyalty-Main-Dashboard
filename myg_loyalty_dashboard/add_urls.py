import sys
sys.stdout.reconfigure(encoding='utf-8')
with open('dashboard/urls.py', encoding='utf-8') as f:
    content = f.read()
# Add AI targeting URL before the closing ]
addition = """
    # AI Customer Targeting Engine
    path('ai-targeting/', views.AITargetingView.as_view(), name='ai_targeting'),
    path('api/v1/ai-targeting/', views.AITargetingAPIView.as_view(), name='ai_targeting_api'),
"""
# Insert before last ]
content = content.rstrip()
if content.endswith(']'):
    content = content[:-1] + addition + ']\n'
else:
    content += addition + ']\n'

with open('dashboard/urls.py', 'w', encoding='utf-8') as f:
    f.write(content)

print('Added AI targeting URL routes')
with open('dashboard/urls.py', encoding='utf-8') as f:
    lines = f.readlines()
print('Last 10 lines:')
for l in lines[-10:]:
    print(repr(l))
