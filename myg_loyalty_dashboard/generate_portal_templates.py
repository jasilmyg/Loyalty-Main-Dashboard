import os

modules = [
    ("customer_intelligence", "Customer Intelligence", "bi-people-fill", "#1e40af", "Deep-dive analytics on customer behavior, purchase patterns, and lifetime value.", [
        ("Total Customers", "bi-people", "#1e40af"), ("Active Customers", "bi-person-check", "#15803d"),
        ("New Customers", "bi-person-plus", "#c2410c"), ("Dormant Customers", "bi-person-dash", "#b45309"),
        ("Avg Customer Value", "bi-currency-rupee", "#7c3aed"), ("Purchase Frequency", "bi-arrow-repeat", "#0e7490"),
    ]),
    ("customer_segmentation", "Customer Segmentation", "bi-pie-chart-fill", "#7c3aed", "AI-powered RFM segmentation — Champions, At-Risk, Dormant, and more.", [
        ("Champions", "bi-trophy-fill", "#f59e0b"), ("Loyal Customers", "bi-heart-fill", "#ef4444"),
        ("At Risk", "bi-exclamation-triangle-fill", "#f97316"), ("Dormant", "bi-moon-fill", "#6366f1"),
        ("New Customers", "bi-star-fill", "#10b981"), ("Lost Customers", "bi-x-circle-fill", "#64748b"),
    ]),
    ("sales_intelligence", "Sales Intelligence", "bi-graph-up", "#15803d", "Analyze sales by day, branch, category, brand, and staff with full drill-down.", [
        ("Total Revenue", "bi-currency-rupee", "#15803d"), ("Total Invoices", "bi-receipt", "#1e40af"),
        ("Avg Order Value", "bi-cart3", "#c2410c"), ("Units Sold", "bi-box-seam", "#7c3aed"),
        ("Growth Rate", "bi-graph-up-arrow", "#0e7490"), ("Top Branch", "bi-shop", "#b45309"),
    ]),
    ("product_intelligence", "Product Intelligence", "bi-box-seam-fill", "#b45309", "Product performance, lifecycle analysis, and AI-based product classification.", [
        ("Star Products", "bi-star-fill", "#f59e0b"), ("High Margin", "bi-graph-up", "#15803d"),
        ("Fast Moving", "bi-lightning-fill", "#1e40af"), ("Slow Moving", "bi-hourglass", "#f97316"),
        ("Dead Stock", "bi-x-octagon-fill", "#ef4444"), ("Growth Products", "bi-arrow-up-circle-fill", "#10b981"),
    ]),
    ("recommendation_engine", "Recommendation Engine", "bi-star-fill", "#f59e0b", "Hybrid AI recommendations — Association Rules, Collaborative Filtering, and Content-Based.", [
        ("Cross-Sell Rules", "bi-diagram-3-fill", "#1e40af"), ("Upsell Opportunities", "bi-arrow-up-right", "#15803d"),
        ("Basket Analysis", "bi-basket-fill", "#7c3aed"), ("Customer Affinities", "bi-heart-fill", "#ef4444"),
        ("Trending Products", "bi-fire", "#f97316"), ("Top Associations", "bi-link-45deg", "#0e7490"),
    ]),
    ("inventory_intelligence", "Inventory Intelligence", "bi-boxes", "#0e7490", "AI stock recommendations, reorder points, stockout risk, and dead stock alerts.", [
        ("Stockout Risk", "bi-exclamation-triangle-fill", "#ef4444"), ("Low Stock", "bi-battery-half", "#f97316"),
        ("Dead Stock", "bi-archive-fill", "#64748b"), ("Overstock", "bi-stack-overflow", "#b45309"),
        ("Stock Value", "bi-currency-rupee", "#1e40af"), ("Turnover Rate", "bi-arrow-clockwise", "#15803d"),
    ]),
    ("promotion_intelligence", "Promotion Intelligence", "bi-tags-fill", "#ef4444", "Analyze promotion ROI, discount abuse, and AI-recommended campaign strategies.", [
        ("Promo Revenue", "bi-megaphone-fill", "#ef4444"), ("Discount %", "bi-percent", "#f97316"),
        ("Promo ROI", "bi-graph-up-arrow", "#15803d"), ("Failed Promos", "bi-x-circle-fill", "#64748b"),
        ("Discount Abuse", "bi-shield-exclamation", "#b91c1c"), ("Best Campaigns", "bi-trophy-fill", "#f59e0b"),
    ]),
    ("branch_intelligence", "Branch Intelligence", "bi-shop", "#6366f1", "Branch ranking, forecast, inventory risk, and AI-driven branch-level insights.", [
        ("Top Branch", "bi-trophy-fill", "#f59e0b"), ("Branch Growth", "bi-graph-up", "#15803d"),
        ("Avg Order Value", "bi-cart3", "#1e40af"), ("Customer Growth", "bi-people-fill", "#7c3aed"),
        ("Inventory Risk", "bi-exclamation-diamond-fill", "#ef4444"), ("Forecast", "bi-calendar3-range", "#0e7490"),
    ]),
    ("ai_insights_center", "AI Insights Center", "bi-lightbulb-fill", "#f59e0b", "Automated AI scanning for opportunities, risks, anomalies, and business recommendations.", [
        ("Opportunities", "bi-arrow-up-circle-fill", "#15803d"), ("Risk Alerts", "bi-exclamation-triangle-fill", "#ef4444"),
        ("Anomalies", "bi-activity", "#f97316"), ("Churn Risk", "bi-person-dash-fill", "#b45309"),
        ("Stock Alerts", "bi-box-seam", "#0e7490"), ("Margin Alerts", "bi-graph-down-arrow", "#7c3aed"),
    ]),
    ("reports_exports", "Reports & Exports", "bi-file-earmark-bar-graph-fill", "#475569", "Schedule and export Excel, CSV, and PDF reports for all intelligence modules.", [
        ("Executive Report", "bi-file-earmark-text-fill", "#1e40af"), ("Customer Report", "bi-people-fill", "#15803d"),
        ("Sales Forecast", "bi-graph-up-arrow", "#f97316"), ("Product Report", "bi-box-seam-fill", "#b45309"),
        ("Branch Report", "bi-shop", "#6366f1"), ("Inventory Report", "bi-boxes", "#0e7490"),
    ]),
    ("data_management", "Data Management", "bi-database-fill", "#334155", "Upload, validate, and manage your retail data with AI-powered quality scoring.", [
        ("Data Quality Score", "bi-patch-check-fill", "#15803d"), ("Missing Values", "bi-exclamation-circle-fill", "#ef4444"),
        ("Duplicates", "bi-files-alt", "#f97316"), ("Invalid Records", "bi-x-circle-fill", "#b45309"),
        ("Last Upload", "bi-upload", "#1e40af"), ("Records Loaded", "bi-table", "#7c3aed"),
    ]),
    ("model_management", "Model Management", "bi-cpu-fill", "#1e40af", "Manage, train, compare, and activate AI models across all forecasting and segmentation tasks.", [
        ("Prophet", "bi-activity", "#15803d"), ("XGBoost", "bi-cpu", "#1e40af"),
        ("LSTM", "bi-diagram-3", "#7c3aed"), ("K-Means", "bi-pie-chart", "#f59e0b"),
        ("Apriori", "bi-link-45deg", "#0e7490"), ("Collaborative Filter", "bi-people", "#ef4444"),
    ]),
    ("settings_portal", "Settings", "bi-gear-fill", "#64748b", "Configure portal preferences, roles, users, API keys, and system integrations.", [
        ("User Roles", "bi-person-badge-fill", "#1e40af"), ("Security", "bi-shield-lock-fill", "#15803d"),
        ("Integrations", "bi-plug-fill", "#7c3aed"), ("Notifications", "bi-bell-fill", "#f97316"),
        ("Audit Logs", "bi-journal-text", "#64748b"), ("System Info", "bi-info-circle-fill", "#0e7490"),
    ]),
]

TEMPLATE_DIR = r"templates\dashboard\portal"

for slug, title, icon, color, desc, cards in modules:
    cards_html = ""
    for c_title, c_icon, c_color in cards:
        cards_html += f"""
            <div class="col-6 col-md-4 col-xl-2">
                <div class="cs-card">
                    <div class="cs-card-icon" style="background: {c_color}18; color: {c_color};">
                        <i class="bi {c_icon}"></i>
                    </div>
                    <div class="cs-card-label">{c_title}</div>
                    <div class="cs-card-value coming-soon-val" style="color:{c_color};">—</div>
                    <div class="cs-card-sub">Populating from ClickHouse...</div>
                </div>
            </div>"""

    html = f"""
{{% extends 'base.html' %}}
{{% load static %}}

{{% block title %}}{title} | AI Retail Engine{{% endblock %}}

{{% block content %}}
<style>
.portal-header {{
    background: linear-gradient(135deg, {color}ee 0%, {color}99 100%);
    color: white; padding: 2rem 2.5rem;
    margin: -2.5rem -2.5rem 2rem -2.5rem;
    position: relative; overflow: hidden;
}}
.portal-header::before {{
    content: ''; position: absolute; top: -60px; right: -60px;
    width: 200px; height: 200px; border-radius: 50%;
    background: rgba(255,255,255,0.06);
}}
.portal-header h1 {{ font-size: 1.6rem; font-weight: 800; }}
.portal-header p {{ color: rgba(255,255,255,0.82); font-size: 0.88rem; }}

.cs-card {{
    background: #fff; border: 1px solid #e2e8f0; border-radius: 14px;
    padding: 1.25rem; transition: all 0.2s;
}}
.cs-card:hover {{ transform: translateY(-2px); box-shadow: 0 6px 24px rgba(0,0,0,0.07); }}
.cs-card-icon {{
    width: 42px; height: 42px; border-radius: 10px;
    display: flex; align-items: center; justify-content: center;
    font-size: 1.15rem; margin-bottom: 0.75rem;
}}
.cs-card-label {{ font-size: 0.68rem; font-weight: 700; text-transform: uppercase; letter-spacing: 1px; color: #64748b; }}
.cs-card-value {{ font-size: 1.6rem; font-weight: 800; margin: 0.2rem 0; color: #0f172a; }}
.cs-card-sub {{ font-size: 0.72rem; color: #94a3b8; }}
.module-info-card {{
    background: linear-gradient(135deg, #0f172a, {color}cc);
    border-radius: 16px; padding: 2rem; color: white;
}}
.roadmap-item {{
    background: #fff; border: 1px solid #e2e8f0; border-radius: 12px;
    padding: 1rem 1.25rem; display: flex; align-items: center; gap: 1rem;
    margin-bottom: 0.75rem;
}}
.roadmap-icon {{
    width: 36px; height: 36px; border-radius: 8px; flex-shrink: 0;
    display: flex; align-items: center; justify-content: center; font-size: 1rem;
    background: {color}18; color: {color};
}}
.coming-badge {{
    background: {color}18; color: {color}; border-radius: 20px;
    padding: 0.2rem 0.7rem; font-size: 0.7rem; font-weight: 700; display: inline-block;
}}
</style>

<div class="portal-header">
    <div class="d-flex align-items-start gap-3">
        <div style="background: rgba(255,255,255,0.18); border-radius: 14px; padding: 0.75rem 1rem; font-size: 1.75rem;">
            <i class="bi {icon}"></i>
        </div>
        <div>
            <span class="coming-badge mb-2">AI RETAIL ENGINE</span>
            <h1 class="mb-1">{title}</h1>
            <p class="mb-0">{desc}</p>
        </div>
    </div>
</div>

<div class="container-fluid px-0">
    <!-- Metric Cards Row -->
    <div class="row g-3 mb-4">
        {cards_html}
    </div>

    <!-- Module Info -->
    <div class="row g-3 mb-4">
        <div class="col-12 col-md-7">
            <div class="module-info-card">
                <div class="mb-1" style="font-size:0.7rem; font-weight:700; color:{color}aa; text-transform:uppercase; letter-spacing:1px;">MODULE STATUS</div>
                <h4 class="fw-bold mb-2">{title} is being wired up</h4>
                <p style="color: rgba(255,255,255,0.75); font-size:0.9rem; line-height:1.7;">
                    This module will connect directly to your ClickHouse + Azure databases to provide
                    <strong style="color:white;">{desc.lower()}</strong>
                    All KPI cards above will display live data once the backend queries are complete.
                </p>
                <div class="mt-3 d-flex gap-2 flex-wrap">
                    <span style="background: rgba(255,255,255,0.1); border-radius: 20px; padding: 0.3rem 0.9rem; font-size: 0.78rem; color: rgba(255,255,255,0.85);">
                        <i class="bi bi-database me-1"></i> ClickHouse Ready
                    </span>
                    <span style="background: rgba(255,255,255,0.1); border-radius: 20px; padding: 0.3rem 0.9rem; font-size: 0.78rem; color: rgba(255,255,255,0.85);">
                        <i class="bi bi-cloud me-1"></i> Azure Connected
                    </span>
                    <span style="background: rgba(255,255,255,0.1); border-radius: 20px; padding: 0.3rem 0.9rem; font-size: 0.78rem; color: rgba(255,255,255,0.85);">
                        <i class="bi bi-cpu me-1"></i> AI Engine Active
                    </span>
                </div>
            </div>
        </div>
        <div class="col-12 col-md-5">
            <div style="background:#fff; border:1px solid #e2e8f0; border-radius:16px; padding:1.5rem; height:100%;">
                <div class="fw-bold mb-3" style="color:#0f172a;">What this module will deliver:</div>
                <div class="roadmap-item">
                    <div class="roadmap-icon"><i class="bi bi-graph-up-arrow"></i></div>
                    <div>
                        <div style="font-weight:600; font-size:0.85rem;">Live KPI Metrics</div>
                        <div style="font-size:0.75rem; color:#64748b;">Real-time data from ClickHouse</div>
                    </div>
                </div>
                <div class="roadmap-item">
                    <div class="roadmap-icon"><i class="bi bi-bar-chart-fill"></i></div>
                    <div>
                        <div style="font-weight:600; font-size:0.85rem;">Interactive Visualizations</div>
                        <div style="font-size:0.75rem; color:#64748b;">Charts, tables, and drill-downs</div>
                    </div>
                </div>
                <div class="roadmap-item">
                    <div class="roadmap-icon"><i class="bi bi-lightbulb-fill"></i></div>
                    <div>
                        <div style="font-weight:600; font-size:0.85rem;">AI-Generated Insights</div>
                        <div style="font-size:0.75rem; color:#64748b;">Automated recommendations and alerts</div>
                    </div>
                </div>
                <div class="roadmap-item">
                    <div class="roadmap-icon"><i class="bi bi-file-earmark-arrow-down-fill"></i></div>
                    <div>
                        <div style="font-weight:600; font-size:0.85rem;">Export Reports</div>
                        <div style="font-size:0.75rem; color:#64748b;">Excel, CSV, and PDF exports</div>
                    </div>
                </div>
            </div>
        </div>
    </div>
</div>
{{% endblock %}}
"""
    filepath = os.path.join(TEMPLATE_DIR, f"{slug}.html")
    with open(filepath, "w", encoding="utf-8") as f:
        f.write(html.strip())
    print(f"OK: {slug}.html")

print("All shell templates populated!")
