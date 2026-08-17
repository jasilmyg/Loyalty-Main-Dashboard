import sys; sys.stdout.reconfigure(encoding='utf-8')
with open('templates/dashboard/ai_targeting.html', encoding='utf-8') as f:
    content = f.read()

# Replace em-dash in JS strings with safe ASCII equivalent
content = content.replace(
    'What Drives Repeat Purchase\u2014',
    'What Drives Repeat Purchase - '
)

# There are 4 em-dashes to fix - replace all in JS-string context
# Use ASCII replacement to avoid any encoding issues in the browser JS engine
replacements = [
    # Line 272 context
    ('class="at-sec-title">Target Gap Analysis</div><div class="at-sec-sub">JAS 2025 buyers not yet in JAS 2026 \u2014 your highest probability targets</div>',
     'class="at-sec-title">Target Gap Analysis</div><div class="at-sec-sub">JAS 2025 buyers not yet in JAS 2026 - your highest probability targets</div>'),
    # Line 319 context  
    ('Feature Importance \u2014 What Drives Repeat Purchase',
     'Feature Importance - What Drives Repeat Purchase'),
    # Line 375 context
    ('id="tbl-sub">JAS 2025 buyers NOT yet in JAS 2026 \u2014 ranked by AI probability score',
     'id="tbl-sub">JAS 2025 buyers NOT yet in JAS 2026 - ranked by AI probability score'),
    # Line 407 context (in applyFilters function)
    ('\u2014 ranked by AI probability score',
     '- ranked by AI probability score'),
]

count = 0
for old, new in replacements:
    if old in content:
        content = content.replace(old, new)
        count += 1
    else:
        print(f'NOT FOUND: {repr(old[:60])}')

# Fallback: replace ALL remaining em-dashes in the script block
# Find script block and replace remaining
import re
def fix_script(m):
    return m.group(0).replace('\u2014', '-')

# Replace any remaining em-dashes everywhere in the template JS
content = content.replace('\u2014', ' - ')

with open('templates/dashboard/ai_targeting.html', 'w', encoding='utf-8') as f:
    f.write(content)

print(f'Applied {count} targeted replacements + global em-dash cleanup')

# Verify
with open('templates/dashboard/ai_targeting.html', encoding='utf-8') as f:
    lines = f.readlines()
in_script = False
remaining = []
for i, line in enumerate(lines, 1):
    if '<script>' in line: in_script = True
    if '</script>' in line: in_script = False
    if in_script:
        for ch in line:
            if ord(ch) > 127:
                remaining.append(i)
                break
print(f'Remaining non-ASCII in script: {remaining}')
