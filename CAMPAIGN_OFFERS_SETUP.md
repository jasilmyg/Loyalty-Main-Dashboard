# myG Campaign & WhatsApp Offer Calendar — Colleague Setup & Handoff Guide

This module lives at **`/campaign-offers/`** (Sidebar: **Campaigns & Offers → Offer Calendar**) and is wired live to ClickHouse (`demand_promo_calendar` + `azure_sales_report` + `item_master` + `branch_master`) and **Demand Intelligence & AI Forecast** (`/demand-intelligence/`).

---

## 1. Live Deployment Setup (Zero Manual SQL Needed)

1. **ClickHouse Table Auto-Migration**:
   - The `demand_promo_calendar` table and all columns (`store_formats`, `branches`, `item_categories`, `item_groups`) are created and evolved **automatically** on first request via `_ensure_table()` in `myg_loyalty_dashboard/dashboard/demand_promo_calendar.py`.
2. **Required Environment Variables** (configured in `.env` / Render):
   - `GEMINI_API_KEY` — powers AI extraction from WhatsApp text, poster images, and store-wise `.ZIP` packs.
   - ClickHouse Cloud credentials (existing portal settings).
3. **Live URL**: `https://loyalty-main-dashboard.onrender.com/campaign-offers/` (auto-deploys from `main`).

---

## 2. Zero-Manual Workflow: Adding Past & Upcoming Offers

### Step 1 — Drop Posters, `.ZIP` Packs, or Paste WhatsApp Text
1. Open **`/campaign-offers/`**.
2. Paste any WhatsApp Channel offer message, drop/Ctrl+V poster image(s), or upload a store-wise `.ZIP` archive (up to **2 GB / 500 posters**, streamed directly from disk and grouped automatically by store).
3. Click **Extract Offer(s) with AI**.
   - AI automatically reads campaign title, dates, store format (`myG` / `myG Future`), districts, exact stores, `product_lines`, `item_categories`, and `item_groups`.
   - ClickHouse automatically calculates each store's realistic **Sale Value (₹) Planned Lift %** from that store's **complete multi-year sales history** + **previously learned completed offers**.
4. Click **Save Selected Offers & Update Forecast**.

*(Works identically for **previous / past offers** and **upcoming offers** — no manual spreadsheet entry required!)*

### Step 2 — Optional Fine-Tuning
- Narrow any offer to exact ClickHouse **`item_categories`** (e.g., `WASHING MACHINE-FL`, `REFRIGERATOR-SBS`, `MIXER GRINDER`) or **`item_groups`** (e.g., `APPLE IPHONE 17 256GB`, `1.5 TON 3 STAR`, `8KG`, `55`).
- Click **Auto (ClickHouse)** next to *Expected Daily Lift (%)* anytime to recalculate that store & item scope's empirical Sale Value Planned Lift % (or type a manual override).

---

## 3. Closed-Loop Learning & `Predicted vs. Actual` Backtest (Step 4)

- **Automatic Self-Learning**: Every past completed offer saved in `/campaign-offers/` is measured against its festival-cleansed **Same-Day-of-Week ClickHouse Sale Value (₹)** baseline and automatically trains future lift predictions hierarchically (`Store × Item Group × Item Category × Product × Campaign Type × Weekday/Weekend`).
- **Predicted vs. Actual Scorecard**: Inspect leak-free **Revenue Accuracy (100 − WAPE)**, **Mean Lift Bias**, **±15% Hit Rate**, and row-by-row **Predicted ₹ vs. Actual ₹ vs. Variance**, or export the report with **Export Report (.xlsx)**.
- **Hierarchy Breakdown**: Click the **Items** / hierarchy button on any offer to view live ClickHouse Predicted vs. Actual Sale Value broken down by **Product**, **Item Category**, and **Item Group**.
