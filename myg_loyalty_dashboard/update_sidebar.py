import re

with open('templates/base.html', 'r', encoding='utf-8') as f:
    content = f.read()

new_sections = """
        <!-- === NEW ENTERPRISE AI PORTAL 2.0 === -->
        <div class="mb-2 mt-4" style="padding-left: 2.5rem;">
            <small class="text-muted fw-bold text-uppercase" style="font-size: 0.65rem; letter-spacing: 1px; color: #f97316 !important;">AI Intelligence Portal</small>
        </div>
        <ul class="nav flex-column mb-3" style="border-left: 2px solid #f97316; margin-left: 1.5rem; padding-left: 0.5rem; background: rgba(249, 115, 22, 0.03); border-radius: 0 12px 12px 0;">
            <li class="nav-item">
                <a class="nav-link {% if request.resolver_match.url_name == 'executive_dashboard' %}active{% endif %}" href="{% url 'executive_dashboard' %}"><i class="bi bi-speedometer2 text-danger"></i> Executive Dashboard</a>
            </li>
            <li class="nav-item">
                <a class="nav-link {% if request.resolver_match.url_name == 'customer_intelligence' %}active{% endif %}" href="{% url 'customer_intelligence' %}"><i class="bi bi-people-fill text-primary"></i> Customer Intelligence</a>
            </li>
            <li class="nav-item">
                <a class="nav-link {% if request.resolver_match.url_name == 'customer_segmentation' %}active{% endif %}" href="{% url 'customer_segmentation' %}"><i class="bi bi-pie-chart-fill text-info"></i> Customer Segmentation</a>
            </li>
            <li class="nav-item">
                <a class="nav-link {% if request.resolver_match.url_name == 'sales_intelligence' %}active{% endif %}" href="{% url 'sales_intelligence' %}"><i class="bi bi-graph-up-arrow text-success"></i> Sales Intelligence</a>
            </li>
            <li class="nav-item">
                <a class="nav-link {% if request.resolver_match.url_name == 'product_intelligence' %}active{% endif %}" href="{% url 'product_intelligence' %}"><i class="bi bi-box-seam-fill text-warning"></i> Product Intelligence</a>
            </li>
            <li class="nav-item">
                <a class="nav-link {% if request.resolver_match.url_name == 'recommendation_engine' %}active{% endif %}" href="{% url 'recommendation_engine' %}"><i class="bi bi-star-fill text-warning"></i> Recommendation Engine</a>
            </li>
            <li class="nav-item">
                <a class="nav-link {% if request.resolver_match.url_name == 'inventory_intelligence' %}active{% endif %}" href="{% url 'inventory_intelligence' %}"><i class="bi bi-boxes text-secondary"></i> Inventory Intelligence</a>
            </li>
            <li class="nav-item">
                <a class="nav-link {% if request.resolver_match.url_name == 'promotion_intelligence' %}active{% endif %}" href="{% url 'promotion_intelligence' %}"><i class="bi bi-tags-fill text-danger"></i> Promotion Intelligence</a>
            </li>
            <li class="nav-item">
                <a class="nav-link {% if request.resolver_match.url_name == 'branch_intelligence' %}active{% endif %}" href="{% url 'branch_intelligence' %}"><i class="bi bi-shop text-primary"></i> Branch Intelligence</a>
            </li>
            <li class="nav-item">
                <a class="nav-link {% if request.resolver_match.url_name == 'ai_insights_center' %}active{% endif %}" href="{% url 'ai_insights_center' %}"><i class="bi bi-lightbulb-fill text-warning"></i> AI Insights Center</a>
            </li>
        </ul>

        <div class="mb-2 mt-3" style="padding-left: 2.5rem;">
            <small class="text-muted fw-bold text-uppercase" style="font-size: 0.65rem; letter-spacing: 1px; color: #64748b !important;">Enterprise Admin</small>
        </div>
        <ul class="nav flex-column mb-4" style="border-left: 2px solid #64748b; margin-left: 1.5rem; padding-left: 0.5rem; background: rgba(100, 116, 139, 0.03); border-radius: 0 12px 12px 0;">
            <li class="nav-item">
                <a class="nav-link {% if request.resolver_match.url_name == 'reports_exports' %}active{% endif %}" href="{% url 'reports_exports' %}"><i class="bi bi-file-earmark-bar-graph-fill text-muted"></i> Reports & Exports</a>
            </li>
            <li class="nav-item">
                <a class="nav-link {% if request.resolver_match.url_name == 'data_management' %}active{% endif %}" href="{% url 'data_management' %}"><i class="bi bi-database-fill text-muted"></i> Data Management</a>
            </li>
            <li class="nav-item">
                <a class="nav-link {% if request.resolver_match.url_name == 'model_management' %}active{% endif %}" href="{% url 'model_management' %}"><i class="bi bi-cpu-fill text-muted"></i> Model Management</a>
            </li>
            <li class="nav-item">
                <a class="nav-link {% if request.resolver_match.url_name == 'settings_portal' %}active{% endif %}" href="{% url 'settings_portal' %}"><i class="bi bi-gear-fill text-muted"></i> Settings</a>
            </li>
        </ul>

        <!-- === LEGACY MODULES === -->
        <div class="mb-2 mt-4" style="padding-left: 2.5rem;">
            <small class="text-muted fw-bold text-uppercase" style="font-size: 0.65rem; letter-spacing: 1px; color: #94a3b8 !important;">Legacy Operations</small>
        </div>
"""

# Find the end of the Logo Area and insert the new sections
pattern = r'(<div class="fw-bold text-uppercase mt-2"[^>]*>.*?</div>\s*</div>)'
new_content = re.sub(pattern, r'\1\n' + new_sections, content)

with open('templates/base.html', 'w', encoding='utf-8') as f:
    f.write(new_content)

print('Injected new AI portal sections successfully.')
