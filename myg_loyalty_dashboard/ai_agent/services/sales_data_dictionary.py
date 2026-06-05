SALES_DATA_DICTIONARY = {
  "Slno": {
    "description": "Auto-increment primary key for each transaction row",
    "type": "INTEGER",
    "category": "System",
    "example": "1, 2, 3..."
  },
  "Date": {
    "description": "Transaction date — used for time-series, cohort, and trend analysis",
    "type": "DATE (YYYY-MM-DD)",
    "category": "Time",
    "example": "2026-05-15",
    "used_in": ["Daily Revenue", "MoM Growth", "Dormant Customer", "Cohort Analysis"]
  },
  "Time": {
    "description": "Transaction time — used for peak hour and shift-wise performance",
    "type": "TIME (HH:MM:SS)",
    "category": "Time",
    "example": "14:32:00",
    "used_in": ["Peak Hour Analysis", "Shift Reports"]
  },
  "Invoice Number": {
    "description": "Unique sales invoice identifier — primary key for a sale event",
    "type": "VARCHAR",
    "category": "Transaction",
    "example": "INV-2026-00145",
    "used_in": ["Bill Count", "Deduplication", "Revenue", "Conversion Rate"]
  },
  "Enq/Job No.": {
    "description": "Enquiry or job card number raised before invoice — used to compute conversion rate",
    "type": "VARCHAR",
    "category": "Pre-Sales",
    "example": "ENQ-2026-00890",
    "used_in": ["Conversion Rate = Invoices / Enquiries"]
  },
  "RBM": {
    "description": "Regional Business Manager — top-level regional hierarchy for performance reporting",
    "type": "VARCHAR",
    "category": "Hierarchy",
    "example": "Rajan K",
    "used_in": ["Regional Revenue", "RBM Leaderboard"]
  },
  "BDM": {
    "description": "Business Development Manager — mid-level area manager under RBM",
    "type": "VARCHAR",
    "category": "Hierarchy",
    "example": "Anoop M",
    "used_in": ["Area Performance", "BDM Dashboard"]
  },
  "Branch": {
    "description": "Store / outlet identifier — used for branch-level P&L and rankings. CRITICAL: If a user asks for 'Future Stores', you MUST filter using `\"Branch\" ILIKE '%FUTURE%'`. If a user asks for 'Normal Stores', you MUST filter using `\"Branch\" NOT ILIKE '%FUTURE%'`.",
    "type": "VARCHAR",
    "category": "Operations",
    "example": "Kozhikode Main, AAKKULAM FUTURE",
    "used_in": ["Branch Revenue Rank", "Branch Target vs Actual", "Footfall Analysis"]
  },
  "Staff Code": {
    "description": "Unique numeric/alphanumeric code for each sales staff member",
    "type": "VARCHAR",
    "category": "Staff",
    "example": "MYG-045",
    "used_in": ["Staff Productivity", "Individual KPIs"]
  },
  "Staff": {
    "description": "Name of the sales executive who raised the invoice",
    "type": "VARCHAR",
    "category": "Staff",
    "example": "Muhammed Jasil",
    "used_in": ["Salesperson Leaderboard", "Staff Performance Report"]
  },
  "Customer Name": {
    "description": "Full name of the customer — used for CRM display and communication",
    "type": "VARCHAR",
    "category": "Customer",
    "example": "Arun Krishnan",
    "used_in": ["CRM", "Customer Profile"]
  },
  "Customer Mobile": {
    "description": "10-digit mobile number — PRIMARY unique customer identifier across all analytics",
    "type": "VARCHAR (10 digits)",
    "category": "Customer",
    "example": "9876543210",
    "primary_key_for_crm": True,
    "used_in": [
      "Unique Customer Count",
      "New vs Repeat",
      "Dormant Customers",
      "Retained Customers",
      "Churn Rate",
      "CLV",
      "RFM Segmentation"
    ]
  },
  "Financier": {
    "description": "Name of the bank or NBFC providing finance for the purchase",
    "type": "VARCHAR",
    "category": "Finance",
    "example": "HDFC Bank, Bajaj Finance",
    "used_in": ["Financier-wise Penetration", "Finance Mix Report"]
  },
  "Finance": {
    "description": "Total finance amount sanctioned by the financier for this transaction",
    "type": "DECIMAL",
    "category": "Finance",
    "example": "25000.00",
    "used_in": ["Finance Penetration Rate", "Financed Revenue"]
  },
  "Delivery Order No.": {
    "description": "Delivery reference number — used for order fulfilment and logistics tracking",
    "type": "VARCHAR",
    "category": "Operations",
    "example": "DO-2026-00321",
    "used_in": ["Fulfilment Rate", "Pending Delivery Reports"]
  },
  "Cash": {
    "description": "Amount paid in cash for this transaction",
    "type": "DECIMAL",
    "category": "Payment",
    "example": "5000.00",
    "used_in": ["Payment Mode Mix", "Cash vs Digital Split"]
  },
  "Debit Card": {
    "description": "Amount paid via debit card swipe on EDC terminal",
    "type": "DECIMAL",
    "category": "Payment",
    "example": "12000.00",
    "used_in": ["Payment Mode Mix", "Digital Payment %"]
  },
  "Credit Card": {
    "description": "Amount paid via credit card — also influences Card Reward and Cashback fields",
    "type": "DECIMAL",
    "category": "Payment",
    "example": "18000.00",
    "used_in": ["Payment Mode Mix", "Credit Card Penetration", "Reward Cost"]
  },
  "Benow": {
    "description": "Payment made through Benow POS digital platform",
    "type": "DECIMAL",
    "category": "Payment",
    "example": "3000.00",
    "used_in": ["Digital Payment %", "UPI Ecosystem"]
  },
  "Advance Receipt": {
    "description": "Advance or booking amount collected before full payment — tracks pre-sales cash flow",
    "type": "DECIMAL",
    "category": "Payment",
    "example": "2000.00",
    "used_in": ["Pre-booking Revenue", "Cash Flow Forecast"]
  },
  "Bharath QR": {
    "description": "Payment collected via Bharat QR code scan",
    "type": "DECIMAL",
    "category": "Payment",
    "example": "1500.00",
    "used_in": ["Digital Payment %", "QR Payment Share"]
  },
  "Paytm QR": {
    "description": "Payment collected via Paytm QR code scan",
    "type": "DECIMAL",
    "category": "Payment",
    "example": "800.00",
    "used_in": ["Digital Payment %", "QR Payment Share"]
  },
  "Pine Labs QR": {
    "description": "Payment collected via Pine Labs QR / EDC terminal",
    "type": "DECIMAL",
    "category": "Payment",
    "example": "4500.00",
    "used_in": ["Digital Payment %", "Pine Labs Terminal Utilisation"]
  },
  "UPI Cashback": {
    "description": "Cashback amount given to the customer for paying via UPI — a cost to the business",
    "type": "DECIMAL",
    "category": "Deduction / Promo Cost",
    "example": "100.00",
    "used_in": ["Discount Leakage", "Net Revenue", "Promo Cost Report"]
  },
  "Card Reward": {
    "description": "Reward points value applied on credit/debit card usage — reduces effective revenue",
    "type": "DECIMAL",
    "category": "Deduction / Promo Cost",
    "example": "250.00",
    "used_in": ["Discount Leakage", "Bank Offer Cost", "Net Revenue"]
  },
  "Card Cashback": {
    "description": "Bank-offered cashback on card payments — a deduction from net revenue",
    "type": "DECIMAL",
    "category": "Deduction / Promo Cost",
    "example": "500.00",
    "used_in": ["Discount Leakage", "Bank Offer Cost", "Net Revenue"]
  },
  "Gift Voucher": {
    "description": "Value of gift voucher redeemed by the customer at point of sale",
    "type": "DECIMAL",
    "category": "Deduction / Promo",
    "example": "1000.00",
    "used_in": ["Voucher Redemption Rate", "Promo ROI", "Net Revenue"]
  },
  "Approved Credit": {
    "description": "Credit line approved for B2B or corporate customers — deferred payment",
    "type": "DECIMAL",
    "category": "Payment / B2B",
    "example": "50000.00",
    "used_in": ["B2B Revenue", "Credit Exposure Report", "Receivables"]
  },
  "EMI": {
    "description": "Amount transacted under EMI scheme — key finance product metric",
    "type": "DECIMAL",
    "category": "Finance",
    "example": "24000.00",
    "used_in": ["EMI Penetration Rate", "Finance Mix", "Digital Payment %"]
  },
  "Customer Type": {
    "description": "Segment label for the customer — New, Repeat, Loyalty, Corporate, etc.",
    "type": "VARCHAR",
    "category": "Customer",
    "example": "New | Repeat | Loyalty",
    "used_in": ["New vs Repeat Analysis", "Segmentation", "Retention Rate"]
  },
  "Total Value": {
    "description": "Final invoiced value — the top-line GMV figure before deductions are netted out",
    "type": "DECIMAL",
    "category": "Revenue",
    "example": "35000.00",
    "used_in": [
      "GMV",
      "Net Revenue",
      "AOV / ATV",
      "Discount Rate",
      "Branch Ranking",
      "Staff Productivity",
      "CLV",
      "MoM Growth"
    ]
  },
  "Exchange": {
    "description": "Trade-in or exchange value offered to the customer for their old product",
    "type": "DECIMAL",
    "category": "Programme",
    "example": "5000.00",
    "used_in": ["Exchange Uptake Rate", "Exchange Value Report"]
  },
  "Discount": {
    "description": "Direct / negotiated discount given on the bill at point of sale",
    "type": "DECIMAL",
    "category": "Deduction",
    "example": "1500.00",
    "used_in": ["Discount Rate %", "Discount Leakage", "Net Revenue", "Margin Analysis"]
  },
  "Indirect Discount": {
    "description": "Backend or scheme-based discount not shown on invoice — true margin impact",
    "type": "DECIMAL",
    "category": "Deduction",
    "example": "800.00",
    "used_in": ["True Margin Analysis", "Net Revenue", "Total Leakage %"]
  },
  "Buyback": {
    "description": "Buyback commitment value given to customer — a future liability for the business",
    "type": "DECIMAL",
    "category": "Liability / Programme",
    "example": "10000.00",
    "used_in": ["Buyback Liability Report", "Future Cash Outflow Forecast"]
  },
  "Addition": {
    "description": "Add-on product or accessory value added to the main sale — tracks cross-sell success",
    "type": "DECIMAL",
    "category": "Revenue",
    "example": "2500.00",
    "used_in": ["Add-on Attach Rate", "Basket Size", "Cross-sell Revenue"]
  },
  "Deduction": {
    "description": "Post-sale deduction applied to the invoice for any adjustment reason",
    "type": "DECIMAL",
    "category": "Deduction",
    "example": "200.00",
    "used_in": ["Net Revenue", "Invoice Adjustment Report"]
  },
  "POINT REDUMPTION (DEDUCTION)": {
    "description": "Value of loyalty points redeemed by the customer — deducted from revenue",
    "type": "DECIMAL",
    "category": "Loyalty / Deduction",
    "example": "350.00",
    "used_in": ["Loyalty Programme ROI", "Points Burn Rate", "Net Revenue", "Discount Leakage"]
  },
  "MYG ONLINE COUPON (DEDUCTION)": {
    "description": "Discount from myG online coupon redeemed at store — measures digital marketing ROI",
    "type": "DECIMAL",
    "category": "Marketing / Deduction",
    "example": "500.00",
    "used_in": ["Coupon Redemption Rate", "Digital Marketing ROI", "Net Revenue", "Discount Leakage"]
  },
  "_meta": {
    "table_name": "v_sales_data",
    "primary_key": "Slno",
    "crm_key": "Customer Mobile",
    "invoice_key": "Invoice Number",
    "total_columns": 38,
    "revenue_columns": ["Total Value", "Addition"],
    "deduction_columns": [
      "Discount",
      "Indirect Discount",
      "Deduction",
      "UPI Cashback",
      "Card Reward",
      "Card Cashback",
      "Gift Voucher",
      "POINT REDUMPTION (DEDUCTION)",
      "MYG ONLINE COUPON (DEDUCTION)"
    ],
    "payment_columns": [
      "Cash", "Debit Card", "Credit Card", "Benow",
      "Advance Receipt", "Bharath QR", "Paytm QR",
      "Pine Labs QR", "EMI", "Finance", "Approved Credit"
    ],
    "net_revenue_formula": "Total Value - Discount - Indirect Discount - Deduction - UPI Cashback - Card Reward - Card Cashback - POINT REDUMPTION (DEDUCTION) - MYG ONLINE COUPON (DEDUCTION)"
  }
}
