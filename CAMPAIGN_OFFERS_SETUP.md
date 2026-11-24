# myG Campaign & WhatsApp Offer Calendar — Colleague Setup & Handoff Guide

This module lives at **`/campaign-offers/`** (Sidebar: **Campaigns & Offers → Offer Calendar**) and is wired live to ClickHouse (`demand_promo_calendar` + `azure_sales_report` + `item_master` + `branch_master`) and **Demand Intelligence & AI Forecast** (`/demand-intelligence/`).

---

## 1. Live Deployment Setup (Zero Manual SQL Needed)

1. **ClickHouse Table Auto-Migration**:
   - The `demand_promo_calendar` table and all columns (`store_formats`, `branches`, `item_categories`, `item_groups`) are created and evolved **automatically** on first request via `_ensure_table()` in `myg_loyalty_dashboard/dashboard/demand_promo_calendar.py`.
2. **Required Environment Variables** (already configured in `.env` / Render):
   - `GEMINI_API_KEY` — required for AI WhatsApp poster / `.ZIP` extraction (optional for Excel/CSV & manual entry).
   - ClickHouse connection variables (existing portal ClickHouse Cloud credentials).
3. **Restart Django after pulling from Git**:
   - Local: `python myg_loyalty_dashboard/manage.py runserver`
   - Production (Render/Gunicorn): automatic on git push to `main`.

---

## 2. How to Add Previous & Upcoming Offers (3 Ways)

### Option A — Bulk Excel / CSV Import (Recommended for Past Offers)
1. Open **`/campaign-offers/`** and click **Download Excel Template** (downloads `myG_Campaign_Offers_Import_Template.xlsx`).
2. The template has **4 sheets**:
   - **`Campaign Offers`** — fill one row per campaign starting from Row 3 (replace the yellow examples).
   - **`Instructions`** — quick SOP rules.
   - **`Valid Branches`** — live ClickHouse list of all 148 myG / myG Future branch names & districts.
   - **`Valid Products & Categories`** — live ClickHouse list of `product` and `item_category` values.
3. **Key tips**:
   - **Leave `expected_lift_pct` blank** so ClickHouse automatically calculates each store's realistic **Sale Value (₹) Planned Lift %** from complete multi-year store history + previously learned completed offers.
   - Use **`product_lines`** for broad lines (`TV`, `MOBILE`, `WASHING MACHINES`), and optionally narrow with **`item_categories`** (`WASHING MACHINE-FL`, `MIXER GRINDER`, `AC INDOOR`) and **`item_groups`** (`APPLE IPHONE 17 256GB`, `1.5 TON 3 STAR`, `8KG`, `55`).
   - Put exact store names in **`branches`** for store-specific promos; leave `branches = ALL` and fill **`districts`** + **`store_formats`** (`MYG`, `MYG_FUTURE`, `MYG,MYG_FUTURE`) for district-wide promos.
4. Click **Upload Excel / CSV** → check the Preview table → click **Import & Learn in ClickHouse**.

### Option B — AI Extraction from Posters, WhatsApp Text, or `.ZIP` Packs
1. Paste a WhatsApp Channel message, drop/paste poster images, or upload a store-wise `.ZIP` pack (up to **2 GB / 500 posters**, streamed directly from disk and grouped by store).
2. Click **Extract Offer(s) with AI**.
3. Review the extracted offers (each automatically receives its store-specific ClickHouse Sale Value Planned Lift %) and click **Save Selected Offers & Update Forecast**.

### Option C — Manual Entry / Editing (Step 2)
1. Fill Campaign Name, Type, Dates, Product Lines, optional searchable **Item Categories** & **Item Groups**, Store Format / Districts / Specific Stores.
2. Click **Auto (ClickHouse)** next to *Expected Daily Lift (%)* anytime to compute that store & item scope's empirical Sale Value Planned Lift % (or type a manual override).
3. Click **Save & Update Forecast**.

---

## 3. Closed-Loop Learning & `Predicted vs. Actual` Backtest (Step 4)

- **How Learning Works**:
  - Every completed past offer (`end_date < latest ClickHouse sales date`) is automatically measured against its **festival-cleansed Same-Day-of-Week ClickHouse Sale Value (₹) baseline**.
  - Future and newly uploaded offers blend **each store's complete multi-year quarter-detrended Sale Value history** with **realized lifts from strictly earlier completed offers** matched hierarchically (`Store × Item Group × Item Category × Product × Campaign Type × Weekday/Weekend`).
- **Predicted vs. Actual Scorecard**:
  - Scroll to **Step 4** to inspect leak-free **Revenue Accuracy (100 − WAPE)**, **Mean Lift Bias**, **±15% Hit Rate**, and row-by-row **Predicted ₹ vs. Actual ₹ vs. Variance**.
  - Click **Items** (or the hierarchy icon on any row) to open the live ClickHouse **Product → Item Category → Item Group** Predicted vs. Actual breakdown.
  - Click **Export Report (.xlsx)** to download the management Excel report.
