import sys
sys.stdout.reconfigure(encoding='utf-8')

sidebar_addition = '''
        <!-- MY PARF Data Download -->
        <div class="mb-2 mt-1" style="padding: 0 0.75rem;">
            <a class="nav-link {% if request.resolver_match.url_name == 'my_parf_download' %}active{% endif %}"
               href="{% url 'my_parf_download' %}"
               style="border-left: 4px solid #e11d48; color: #e11d48; background: rgba(225, 29, 72, 0.07); font-weight: 700; border-radius: 8px 12px 12px 8px; margin-left: 0.5rem; transition: all 0.3s ease;">
                <i class="bi bi-cloud-download" style="color: #e11d48; font-size: 1.1rem; margin-right: 10px !important;"></i>
                MY PARF Data
                <span class="badge ms-1" style="background:linear-gradient(135deg,#e11d48,#f43f5e);font-size:8px;font-weight:800;padding:0.3em 0.6em;border-radius:6px;">CSV</span>
            </a>
        </div>

'''

with open('templates/base.html', encoding='utf-8') as f:
    content = f.read()

ANCHOR = '        <!-- AI Customer Targeting Engine -->'
if '<!-- MY PARF Data Download -->' not in content:
    content = content.replace(ANCHOR, sidebar_addition + ANCHOR)
    print('Added MY PARF sidebar entry')
else:
    print('Already exists')

with open('templates/base.html', 'w', encoding='utf-8') as f:
    f.write(content)
