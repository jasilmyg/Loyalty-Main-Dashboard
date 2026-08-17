import sys
sys.stdout.reconfigure(encoding='utf-8')
with open('templates/base.html', encoding='utf-8') as f:
    content = f.read()

TCC_BLOCK = '''        <!-- Target Achievement Command Center -->
        <div class="mb-2 mt-2" style="padding: 0 0.75rem;">
            <a class="nav-link {% if request.resolver_match.url_name == 'target_command_center' %}active{% endif %}"
               href="{% url 'target_command_center' %}"
               style="border-left: 4px solid #6366f1; color: #6366f1; background: rgba(99, 102, 241, 0.08); font-weight: 700; border-radius: 8px 12px 12px 8px; margin-left: 0.5rem; transition: all 0.3s ease;">
                <i class="bi bi-bullseye" style="color: #6366f1; font-size: 1.25rem; margin-right: 10px !important;"></i>
                \U0001f3af Target Command Center
                <span class="badge ms-1" style="background:linear-gradient(135deg,#6366f1,#8b5cf6);font-size:8px;font-weight:800;padding:0.3em 0.6em;border-radius:6px;">AI</span>
            </a>
        </div>'''

AI_BLOCK = '''        <!-- AI Customer Targeting Engine -->
        <div class="mb-2 mt-1" style="padding: 0 0.75rem;">
            <a class="nav-link {% if request.resolver_match.url_name == 'ai_targeting' %}active{% endif %}"
               href="{% url 'ai_targeting' %}"
               style="border-left: 4px solid #dc2626; color: #dc2626; background: rgba(220, 38, 38, 0.07); font-weight: 700; border-radius: 8px 12px 12px 8px; margin-left: 0.5rem; transition: all 0.3s ease;">
                <i class="bi bi-robot" style="color: #dc2626; font-size: 1.25rem; margin-right: 10px !important;"></i>
                \U0001f916 AI Targeting Engine
                <span class="badge ms-1" style="background:linear-gradient(135deg,#dc2626,#ef4444);font-size:8px;font-weight:800;padding:0.3em 0.6em;border-radius:6px;">ML</span>
            </a>
        </div>

'''

if '<!-- AI Customer Targeting Engine -->' not in content:
    content = content.replace(TCC_BLOCK, TCC_BLOCK + '\n' + AI_BLOCK)
    print('Added AI Targeting sidebar entry')
else:
    print('Already exists — skipped')

with open('templates/base.html', 'w', encoding='utf-8') as f:
    f.write(content)
