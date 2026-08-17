import sys
sys.stdout.reconfigure(encoding='utf-8')

with open('templates/dashboard/target_command_center.html', encoding='utf-8') as f:
    content = f.read()

# Find the exact problematic region - the JS sections array
# The issue is emoji characters in JS template literals breaking the syntax
# We need to find the line number boundaries

lines = content.split('\n')
print(f'Total template lines: {len(lines)}')

# Find problematic emoji in JS context (inside <script> tags)
in_script = False
for i, line in enumerate(lines, 1):
    if '<script>' in line:
        in_script = True
    if '</script>' in line:
        in_script = False
    if in_script:
        for ch in line:
            if ord(ch) > 127:
                print(f'Line {i}: non-ASCII char U+{ord(ch):04X} ({ch!r}) - {repr(line[:80])}')
                break
