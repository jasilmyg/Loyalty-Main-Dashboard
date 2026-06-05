# Database Knowledge Base for the Enterprise AI Agent

DATABASE_DICTIONARY = {
    "tables": {
        "sales": "Contains all transactional data including invoices, revenue, and customer IDs.",
        "customers": "Contains customer profiles, loyalty points, and segments."
    },
    "kpis": {
        "ATV": "Average Transaction Value. Calculated as Total Revenue / Total Invoice Count.",
        "Retention Rate": "Calculated as Repeat Customers / Previous Period Total Customers.",
        "Dormancy Rate": "Percentage of customers who haven't purchased in 180-365 days."
    },
    "business_rules": [
        "Financial year starts in April and ends in March.",
        "Customers are considered 'At Risk' if they haven't purchased in 90 days.",
        "Revenue should always exclude tax for KPI calculations unless stated otherwise."
    ]
}
