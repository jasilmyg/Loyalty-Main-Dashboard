# COMPLETE DETAILED WORK REPORT — Q2 2026 (APRIL, MAY & JUNE)

> **Prepared by:** Jasil  
> **Period:** April 1, 2026 – June 30, 2026  
> **Projects Covered:** myG Loyalty Main Dashboard, OSG-myG-PORTAL, Bigg Boss / FoneFlix Portal, HR e-Sign Portal, FIFA World Cup Contest, She Start Dashboard  
> **Date Generated:** July 17, 2026

---

## TABLE OF CONTENTS

1. [Project 1: myG Loyalty Analytics Dashboard (Django + PostgreSQL)](#1-myg-loyalty-analytics-dashboard)
2. [Project 2: OSG-myG-PORTAL (Claims Management + WhatsApp)](#2-osg-myg-portal)
3. [Project 3: Bigg Boss & FoneFlix Registration Portal (Flask)](#3-bigg-boss--foneflix-registration-portal)
4. [Project 4: SHE START – Startup Applicant Evaluation Dashboard](#4-she-start--startup-applicant-evaluation-dashboard)
5. [Project 5: Enterprise AI Agent (LLM-Powered BI)](#5-enterprise-ai-agent)
6. [Project 6: Machine Learning & Predictive Forecasting](#6-machine-learning--predictive-forecasting)
7. [Project 7: Database Administration & Optimization](#7-database-administration--optimization)
8. [Project 8: HR e-Sign Portal](#8-hr-e-sign-portal)
9. [Project 9: FIFA World Cup Contest Platform](#9-fifa-world-cup-contest-platform)
10. [Project 10: Enterprise Retail Analytics Dashboard](#10-enterprise-retail-analytics-dashboard)
11. [Month-by-Month Chronological Timeline](#month-by-month-chronological-timeline)
12. [Technical Statistics Summary](#technical-statistics-summary)

---

## 1. myG LOYALTY ANALYTICS DASHBOARD

**Tech Stack:** Django 5.0, Django REST Framework, PostgreSQL (DigitalOcean Managed), DuckDB, Redis, Chart.js, Plotly, Pandas, Scikit-Learn, Gunicorn, WhiteNoise, Render (Deployment)

### 1.1 Architecture & Infrastructure

- Built a **full-stack Business Intelligence & Loyalty Analytics Dashboard** for myG retail operations
- The system ingests raw sales data from Google Drive Excel files, processes it into a high-performance PostgreSQL analytics database, and exposes a rich web portal
- **Database:** 12.6M+ transaction rows across DigitalOcean managed PostgreSQL
- **Materialized Views:** 26 concurrent materialized views for sub-second query performance
- **Caching:** Redis + LocMemCache integration for millisecond API responses
- **Deployment:** Render.com with Gunicorn WSGI, WhiteNoise for static files

### 1.2 Dashboard Modules Built / Enhanced (29 Templates)

| # | Module | Template File | Description |
|---|--------|--------------|-------------|
| 1 | Main Dashboard | `index.html` | Revenue KPIs, Monthly Trends, Total Invoices, ATV |
| 2 | Customer Analytics | `customers.html` | Unique customers, LTV, Repeat Rate, Visit Frequency |
| 3 | RFM Segmentation | `rfm.html` | 3D scoring (Recency/Frequency/Monetary), 6 segments with Excel export |
| 4 | Cohort Analysis | `cohorts.html` | Monthly + Yearly cohort retention, LTV per cohort |
| 5 | Payments Analysis | `payments.html` | Revenue breakdown by payment mode (Cash, Card, EMI, UPI, etc.) |
| 6 | Discounts Analysis | `discounts.html` | Discount types: Exchange, Buyback, Point Redemption, Risk Pool |
| 7 | Staff Performance | `staff.html` | Revenue per staff, Invoice count, ATV, Top 50 leaderboard |
| 8 | Branch Performance | `branches.html` | Revenue, transactions, customer count per branch |
| 9 | Loyalty Gap Analysis | `loyalty_gap.html` | Inter-purchase gap segmentation (10 segments) with campaign strategies |
| 10 | Retail Analytics | `retail_analytics.html` | Longitudinal Monthly/Quarterly/Yearly loyalty reporting |
| 11 | Monthly Retention | `monthly_retention.html` | Customer retention tracking from Dec 2025 baseline |
| 12 | Campaign Analysis | `campaign_analysis.html` | Dormant customer resurrection, reactivation tracking |
| 13 | Target Executive | `target_executive.html` | Burn-up chart with linear target line, AI insights |
| 14 | LSTM Forecast | `lstm_forecast.html` | Deep learning prediction engine for sales forecasting |
| 15 | Enterprise AI Agent | `enterprise_ai_agent.html` | Conversational AI for business intelligence queries |
| 16 | She Start Dashboard | `she_start.html` | Startup applicant evaluation (Final Selection Matrix) |
| 17 | She Start Detailed | `she_start_detailed.html` | Individual panelist scores with drop-highest/lowest logic |
| 18 | Redemption Analysis | `redemption_analysis.html` | Loyalty point redemption metrics and customer tracking |
| 19 | Customer Propensity | `customer_propensity.html` | Dormant customer probability gauges and risk meters |
| 20 | Store Analysis | `store_analysis.html` | Future store vs normal store comparison analytics |
| 21 | Enterprise Dashboard | `enterprise_dashboard.html` | Executive-level retail KPI overview |
| 22 | DB Manager | `db_manager.html` | Data upload interface with success messages and validation |
| 23 | Report Generator | `report_generator.html` | Monthly category & brand performance Excel reports |
| 24 | Security | `security.html` | Password reset and global session revocation |
| 25 | Profile | `profile.html` | User profile management |
| 26 | Store Upload | `store_upload.html` | Store data upload interface |
| 27 | React Dashboard | `react_dashboard.html` | React-based interactive dashboard component |
| 28 | Invalid Mobiles | `invalid_mobiles.html` | Data quality: invalid mobile number detection |
| 29 | AI Performance | `ai_performance.html` | AI model performance metrics display |

### 1.3 REST API Endpoints (20+)

| Endpoint | Description |
|----------|-------------|
| `/api/sales-overview/` | Revenue KPIs + monthly trend |
| `/api/customer-analytics/` | Customer count, LTV, repeat rate |
| `/api/customer-frequency/` | Visit frequency distribution |
| `/api/rfm-segments/` | RFM segment counts + revenue |
| `/api/monetary-quintiles/` | Customer spend quintile breakdown |
| `/api/cohorts/` | Monthly cohort retention matrix |
| `/api/yearly-cohorts/` | Yearly cohort with LTV + RFM health |
| `/api/payment-analytics/` | Payment mode breakdown |
| `/api/discount-analysis/` | Discount type breakdown |
| `/api/staff-performance/` | Staff leaderboard |
| `/api/branch-performance/` | Branch comparison |
| `/api/loyalty-overview/` | Loyalty KPIs (repeat rate, avg gap) |
| `/api/gap-segments/` | Inter-purchase gap segments |
| `/api/loyalty-segmentation/` | Recency × Frequency matrix |
| `/api/action-engine/` | Actionable customer segments |
| `/api/business-insights/` | Auto-generated insights |
| `/api/retail-loyalty-report/` | Retail loyalty trend report |
| `/api/retail-loyalty-advanced/` | Advanced analytics with insights |
| `/api/branches-list/` | Unique branch list for filters |
| `/api/download/<module>/` | Excel export for any module |

### 1.4 Key Git Commits (April–June 2026)

| Commit | Description |
|--------|-------------|
| `3b5c429` | Materialized views + fast-path architecture for sub-second dashboard |
| `a432626` | Correct cohort retention calculations |
| `9fc38c9` | Cohort retention fast path — cold 453ms, warm 0.2ms |
| `1ede1c2` | Script to rebuild cohort MV with exact yearly revenue |
| `21b0b0b` | myG FUTURE logo + WhiteNoise for production static files |
| `372a680` | Fixed Quarterly/Yearly Retail Matrix repetition counting |
| `495c78d` | Hide download/export buttons from non-admin users |
| `b171f0c` | Password reset and global session revocation |
| `03e7ba6` | Passive 3D liveness detection FaceLock for myguser |
| `d141536` | Strict facial recognition threshold to prevent false positives |
| `f5d0a8a` | Blink Detection to prevent photo spoofing |
| `1eda216` | Native Target Executive Dashboard, DB file upload with hygiene filters |
| `0651862` | Fix Render deploy: scikit-learn, statsmodels, SQLAlchemy |
| `4ce5716` | Premium Plotly aesthetics, pacing delta, smooth gradients |
| `bc9dbcc` | Massive Premium Executive Dashboard Redesign — Glassmorphism |
| `49f4940` | Download buttons for mygadmin — fix conn reference, JS permission check |
| `742ccca` | Fix cohort retention duplicates, accurate LTV, remove Kerala branding |
| `d12a40e` | LSTM forecast cache in PostgreSQL instead of local file |
| `89515ba` | Bake LSTM forecast data into data migration 0002 |
| `3c001e3` | Linear target line to burn-up forecast chart |
| `52f8225` | Missing customer_propensity template |
| `d25b1bb` | Campaign Analysis with advanced AI console |
| `cba526b` | Automate Materialized Views refresh + cache clearing on upload |
| `f42743b` | Dynamic Advanced AI Insights Engine + Random Forest Score Models |
| `30b2495` | Fix UI infinite spinners on API failure |
| `4c923d0` | Train ML model on historical 2020-2025 for Kerala festival patterns |
| `cb582c0` | Tune MLP hyperparameters to stabilize seasonal prediction |
| `b4049ef` | Add missing malayalam_calendar.py |
| `1963244` | She Start dashboard with permanent local DB saving, inline editing |
| `36a23ca` | Add gspread to Render requirements |
| `d538ba2` | Render Secret Files + Env Var fallback for service_account.json |
| `8366e06` | Hardcode Excel mapping for Place/Business Name on Render |
| `e0c6b7f` | Live syncing interval from 30s to 5s |
| `f342629` | Restrict shestart user to only She Start dashboard |
| `30563a6` | Allow mygadmin user to access She Start section |
| `d0de911` | Live syncing interval 5s to 25s |
| `e5dc438` | She Start Detailed Report — panelist scores, drop highest/lowest |
| `17801db` | She Start engine: drop highest/lowest for all 6 metrics |
| `86f9ae3` | Fix scoring logic: 10 Google Sheet questions → Interview score |
| `77a121e` | Replace DOM-traversal Tab with array-indexing |
| `4a998da` | Comprehensive professional README.md |
| `504d46c` | Fix UI: LSTM actuals date truncation, Target Executive hardcoded dates |
| `6128d94` | Redemption metrics + Redemption Analysis view |
| `94be797` | Split Loyalty Point Matrix + Redeemed Customers count |
| `275cf53` | Campaign analysis + Celery setup |
| `fca7036` | Secure cohort customer download options |
| `7d84c43` | Update dashboard with June 2026 data — fix campaign/LSTM/charts |

---

## 2. OSG-myG-PORTAL

**Tech Stack:** Flask, PostgreSQL, Google Apps Script, Telinfy/GreenAds WhatsApp API, Pandas, openpyxl

### 2.1 Claims Management System

- **Full claims lifecycle:** Submit → Registered → Repair Completed → Replacement Approved → Settled
- **Claim investigation scripts:** Built targeted investigative tools to trace and resolve specific claim anomalies:
  - `trace_9895.py` — Trace specific claim by mobile number
  - `investigate_claim.py` — Deep dive into claim data
  - `investigate_data.py` — Bulk data investigation
  - `investigate_followup_bug.py` — Follow-up system bug analysis
- **Claim repair utilities:**
  - `fix_claims_workflow.py` — Fix corrupted claim workflow states
  - `restore_workflow.py` — Restore broken workflow sequences
  - `fix_status_case.py` — Standardize status case formatting
  - `fix_followup_reverts.py` — Fix follow-up revert issues
  - `fix_mobin_claim.py` — Fix specific customer claim
  - `fix_9895.py` — Repair specific claim record
- **Cancelled status feature:** Added Cancelled status dropdown in Edit modal, KPI card in Analytics, filter support
- **Dashboard aging buckets:** Fixed aging bucket display and merge cells to match original design

### 2.2 WhatsApp API Integration (Telinfy/GreenAds Global)

- Engineered automated messaging pipelines linking the portal's events to WhatsApp API endpoints
- **Templates created and integrated:**
  - `myg_onsitego_registered_main` — Automatic message when claim is registered
  - `myg_onsitego_replacement_main` — Message when replacement is approved
  - `myg_onsitego_repair_completed_main` — Message when repair is completed
  - `myg_onsitego_part_order_main` — Spare parts pending notification with dedicated trigger button
- Debugged payload routing and delivery issues (`whatsapp_debug_log.txt`)
- Rebuilt data fetching logic utilizing Postman API networks (`scrape_postman.py`)
- **Cut-off date management:** Configured WhatsApp auto-message cut-off dates (June 13 → June 15 → July 1, 2026)
- Built test scripts: `test_whatsapp.py`, `test_whatsapp_debug.py`, `test_telfiny.py`, `test_telfiny2.py`, `test_telfiny3.py`

### 2.3 Large-Scale Data Ingestion (OSID)

- Engineered massive data parsers for legacy Excel imports:
  - `import_osid_feb2026.py` — Initial OSID Feb 2026 import
  - `import_osid_feb2026_full.py` — Full OSID Feb 2026 import with all columns
- Automated data sanitization pipelines:
  - `fix_date_formats.py` — Standardize European/American date collisions
  - `check_date_formats.py` — Date format validation
  - `check_all_dates.py` — Comprehensive date audit
- Updated Onsitego OSID file to Feb 2026 reference
- Updated Excel data files: Future Store List, RBM/BDM/Branch, myG All Store

### 2.4 Google Apps Script Bridge

- Developed `google_apps_script.js` (v6 — Robust row matching + SR No fix, 361 lines, 13.6 KB)
- Features:
  - Seamless sync between operational Google Sheets and Flask backend
  - Bidirectional data flow — portal updates push to Google Sheets
  - Column blacklist system to prevent auto-addition of internal columns
  - SR Number generation and assignment when status changes from Submit → Registered
  - Follow-up Notes sync: REMARKS and ONSITEGO STATUS columns auto-append to Follow Up Notes
  - New claim registration auto-appends to Google Sheet
- Multiple Google Apps Script web app deployments and debugging sessions

### 2.5 SR Number System

- Implemented automatic SR Number generation (format: `260627-000833`)
- SR Number assigned when claim status changes from Submit to Registered
- SR Number sync between portal, PostgreSQL database, and Google Sheet
- Multiple debugging sessions to ensure correct SR Number propagation

---

## 3. BIGG BOSS & FONEFLIX REGISTRATION PORTAL

**Tech Stack:** Flask, Google Drive API (OAuth 2.0), Google Sheets API, gspread, HTML5/CSS3/JS, Render (Deployment)

### 3.1 Bigg Boss Season 8 – Agnipareeksha (June 2026)

- Built a highly customized Flask-based web application for Bigg Boss auditions
- **Hero Section Design:**
  - Two-column layout: Left 40% (branding), Right 60% (registration form)
  - Mohanlal host image with precise positioning and overlap with form section
  - Agnipareeksha logo with fire/energy effects
  - myG logo integration with suitable background colors
  - Neon-glow typography, dark glassmorphism form backgrounds
  - Floating 3D particle animations for brand elements
- **Video Upload Architecture (Google Drive OAuth):**
  - Custom OAuth 2.0 Client ID integration for user-authenticated video uploads
  - Videos uploaded directly to designated Google Drive folders
  - Bypassed Render server's ephemeral storage limits
  - Upload progress bar with "Uploading short film" status message
  - File size handling for large video files (90MB+)
  - Multiple fallback methods for video storage
  - Google Drive folder: dedicated folder per contest
- **Data Storage:**
  - Registration data saved to Google Sheets via gspread library
  - Video links saved in Google Sheet alongside registration data
  - JSON backup file system for data redundancy
- **Mobile Responsiveness:**
  - Fully responsive layout for mobile and desktop
  - Mohanlal image positioning optimized for mobile view
  - Social icons (Instagram, Facebook) added for mobile
  - Orange separator line between sections
- **Security & Validation:**
  - Server-side validation: Mobile (10 digits), Email (valid format), Age (18+)
  - One mobile = one entry enforcement
  - Hidden Google Apps Script URL protection
- **Popup Banner System:**
  - Dynamic popup banner on website load with close button
  - Multiple banner image iterations
- **Audition Closed Page:**
  - Created "Auditions Closed" landing page when registration period ended

### 3.2 FoneFlix Mobile Phone Short Film Contest 2026 (June 2026)

- Successfully **pivoted the entire codebase** from Bigg Boss to FoneFlix
- **Rebranding:**
  - Changed from Navy Blue & Neon Pink (Bigg Boss) to Dark Browns & Orange (FoneFlix)
  - New hero images for desktop (`film_contest.png`) and mobile versions
  - Removed Mohanlal images, Asianet logo, Hotstar logo
  - New contest-specific form fields and consent clauses
- **New Google Drive folder** for FoneFlix video uploads
- **New Google Sheet** for FoneFlix registration data
- Updated Terms & Conditions and Privacy Notice
- Removed old branding elements (Asianet, Hotstar, Banijay)
- Max video size: 500 MB
- Video upload timeout: 10 minutes with user-friendly error messages
- GitHub: `github.com/jasilmyg/bigboss.git`

---

## 4. SHE START – STARTUP APPLICANT EVALUATION DASHBOARD

**Tech Stack:** Django, gspread (Google Sheets API), PostgreSQL, HTML5/CSS3/JS, SweetAlert2

### 4.1 Live Google Sheets Synchronization

- Utilized `gspread` library to create a live-syncing engine
- Google Sheet: `1qVW_WZx3yu5l-iRd6h0pe5BxMBDk3kDC4yQ3sFj4Q-o`
- **Sync interval:** 25 seconds (evolved from 30s → 5s → 25s)
- Authentication: Service account JSON with multiple fallback methods:
  - Environment variable JSON
  - Local service_account.json
  - Render Secret Files path
- Data maintained in sync between Google Sheet → Django Portal → PostgreSQL

### 4.2 Advanced Scoring Algorithm (6-Panelist System)

- **10 Google Sheet evaluation questions** mapped to Interview score:
  1. Passion & Commitment
  2. Clarity of Business Idea
  3. Communication & Presentation
  4. Growth Potential
  5. Need for Support
  6. Social/Family Impact
  7. Inspirational Value
  8. Innovation/Uniqueness
  9. Financial Responsibility
  10. Utilization Plan
- **Scoring logic:** 6 panelists evaluate each candidate → system drops the absolute highest (#1) and lowest (#6) scores → averages the remaining 4 panelist scores
- **Interview Score (40% weight):** Average of middle 4 panelists
- **5 Manual evaluation columns** (editable in portal):
  - Business Growth Potential (15%)
  - Genuine Need for Support (15%)
  - Emotional/Inspirational Impact (10%)
  - Sustainability (10%)
  - Utilization (10%)
- **Final Weighted Score** = (Interview × 40%) + (Growth × 15%) + (Support × 15%) + (Emotional × 10%) + (Sustainability × 10%) + (Utilization × 10%)
- **Automated Decision Badging:**
  - ≥85 → *Strong Final Selection*
  - 70–84 → *Waitlist Consideration*
  - <70 → Lower priority
  - Decision only calculated when all 5 manual criteria are filled

### 4.3 Interactive Dashboard UI

- **Inline cell editing** for Growth, Support Need, Emotional, Sustainability, Utilization columns
- **SweetAlert2 integration:** Subtle toast notifications for "Saved" and "Deleted" confirmations
- **Tab key navigation:** Robust array-indexing to guarantee sequential left-to-right flow with row wrapping
- **Interview column:** Read-only (data strictly from Google Sheets)
- **Permanent data persistence:** Scores saved to local PostgreSQL database, survive page refreshes and browser restarts
- **Sorting:** Data ordered by descending Total Weighted Score
- **Excel Export:** Professional layout with clean text extraction (DOM parser replacing regex)

### 4.4 She Start Detailed Report

- Individual panelist scores view
- Visual marking: top and bottom scores highlighted in red, middle 4 in green
- Per-panelist breakdown with average calculation
- Excel download with formatted report

### 4.5 Access Control

- Dedicated `shestart` user (username: `shestart`, password: `shestart123`)
- `shestart` user can ONLY see the She Start section (other dashboard sections hidden)
- `mygadmin` user also has access to She Start section
- Data migration to auto-create shestart user during deployment

---

## 5. ENTERPRISE AI AGENT

**Tech Stack:** Django, PostgreSQL, NVIDIA Nemotron Ultra API, OpenRouter API, n8n (Chat Workflow), Celery (Background Tasks)

### 5.1 Multi-Layered Agent Architecture

- **SQL Agent:** Translates natural language business questions into complex PostgreSQL queries
- **AI Analyst:** Interprets raw database output and generates readable business insights
- **FastPath Engine:** Pre-computed answers for common queries (response time: 0.18 seconds)
- **Forecasting Engine:** Background prediction generation for complex forecast questions

### 5.2 LLM Integration & Optimization

- **NVIDIA API:** Integrated `integrate.api.nvidia.com/v1/chat/completions` endpoints
  - Models used: Nemotron Ultra for SQL generation
  - Overcame severe API latency (5–10 minute → seconds) through:
    - Aggressive prompt engineering
    - Query scope reduction
    - FastPath pre-computation for common patterns
- **OpenRouter API:** Alternative LLM endpoint for flexibility
- **Schema Hardening:** Explicitly feeding database schema context to prevent hallucinations (e.g., preventing queries against non-existent `future_stores` tables)

### 5.3 n8n Chat Integration

- Connected n8n workflow chat agent to the Django portal
- Chat Trigger node with streaming response mode
- Database AI Agent node connected to PostgreSQL
- Webhook-based integration: `jasil.app.n8n.cloud/webhook/...`
- SQL query validation and error handling
- Training data: provided detailed SQL rules, column meanings, and business context

### 5.4 Enterprise AI Agent UI (`enterprise_ai_agent.html` — 47 KB)

- Conversational chat interface
- Real-time streaming responses
- Export options: PDF and CSV
- Error handling with user-friendly messages
- Deep analysis retry mechanism when Nemotron is slow/busy

---

## 6. MACHINE LEARNING & PREDICTIVE FORECASTING

**Tech Stack:** Scikit-Learn (Random Forest, MLPRegressor, GradientBoosting), Pandas, NumPy, Plotly

### 6.1 Model Deployment

- Configured and deployed multiple ML models directly into the `CampaignAnalysisAPIView`:
  - **Random Forest** — Ensemble tree model
  - **MLPRegressor** — Neural Network proxy
  - **GradientBoostingRegressor** — Sequential boosting model
- **Training data:** Historical years 2020–2025 to learn Kerala festival patterns
- **MLP Hyperparameter tuning** to stabilize seasonal prediction curves

### 6.2 MalayalamCalendarFeaturizer (`malayalam_calendar.py` — 908 lines, 38.6 KB)

- **Custom library** for ML/DL projects covering 2020 onwards
- Features:
  - Convert Gregorian ↔ Malayalam date (Kollavarsham system)
  - Get Malayalam month, year (Kollavarsham era)
  - 27 Nakshatras (birth stars) for any date
  - Kerala public holidays & major festival flags
  - **ML-ready feature vector generation** — multi-dimensional arrays for AI
  - Pandas DataFrame export
  - Seasonal/agricultural cycle features
  - Proximity to Onam and other Kerala festivals factored into predictive scoring
- 12 Malayalam months with Malayalam script names
- Season detection for monsoon, harvest, festival periods

### 6.3 LSTM Forecast System

- Built LSTM (Long Short-Term Memory) deep learning forecast engine
- **Advanced LSTM Forecaster** (`advanced_lstm_forecaster.py`)
- Forecast cache stored in PostgreSQL via `ForecastCache` model (instead of gitignored JSON files)
- Data migration `0002_seed_forecast_cache` with full cache payload embedded
- Actuals vs Forecast comparison charts
- Burn-up chart with linear target line
- Moving averages and smooth gradient visualizations

### 6.4 Dormant Customer Reactivation System

- Built SQL pre-aggregation engines (`mv_dormant_reactivation`) to bucket customers who purchased in 2024 but went silent in 2025/2026
- **UI Visualizers:**
  - Plotly **Probability Gauges** — Resurrection probability scores
  - **Semi-Donut** charts — Repeat purchase probability
  - **Dormancy Risk Meters** — Visual risk assessment
- Cohort-based dormant customer analysis per year
- Resurrection Rate % per month tracking
- Hidden patterns, seasonal comeback trends, festival-based reactivation spikes

### 6.5 Sales Prediction Reports

- **June 13, 2026:** Predicted daily final sale using deep learning + weather factors in Kerala
  - Analyzed current sale (₹6,11,61,903 at 2 PM) to predict end-of-day total
  - Factored in "Day & Night" store offers (Pattambi, Kottayam, Calicut, Kottakal, Perinthalmanna)
- **June 27, 2026:** Analyzed weekly sale pattern to predict end-of-day total
  - Input: ₹14,66,06,096 at 5:35 PM
  - Calculated time to reach ₹24.59 Cr target
- **Future Store Analysis:** New vs Repeat customer reports for specific stores (Balussery Future, Mavelikara Future, Falnir Future, Thamarassery Future)

---

## 7. DATABASE ADMINISTRATION & OPTIMIZATION

**Tech Stack:** PostgreSQL (DigitalOcean Managed), Materialized Views, Redis, LocMemCache

### 7.1 Materialized View Overhaul

- **Critical Bug Fix:** Diagnosed cross-join bug in `mv_yearly_cohort` that was duplicating rows (4 rows per cohort/year combo) and inflating LTV by thousands of percent
  - Root cause: `mv_customer_dates` had duplicate mobile rows causing `cohort_membership` to fan out
  - Fix: Rebuilt with `MIN(active_year)` per mobile to avoid cross-joins
  - Rebuild time: 146 seconds for full re-scan of 12M+ rows
- **26 Materialized Views** managed concurrently:
  - `mv_yearly_cohort`, `mv_cohort_customer_years`, `mv_action_engine`
  - `mv_rfm_summary`, `mv_rfm_segments`, `mv_monthly_summary`
  - `mv_dormant_reactivation`, `mv_branch_resurrection_2024_2026`
  - `mv_customer_active_years`, `mv_customer_summary`, `mv_customer_dates`
  - And 15+ more
- Built robust refresh system (`fast_refresh_all.py`, `fast_refresh.py`) that regenerates all MVs without locking the database
- **Automated MV refresh** upon new data upload through DB Manager

### 7.2 Data Uploads & Cleaning (April–June 2026)

| Data File | Date Range | Action |
|-----------|-----------|--------|
| DSR APR 2026 | April 2026 | Uploaded + cleaned |
| DSR MAY 2026 (Part 1) | May 1–17, 2026 | Uploaded to PostgreSQL |
| DSR MAY 2026 (Part 2) | May 18–26, 2026 | Uploaded, SMC/EI cleaned |
| DSR MAY 2026 (Part 3) | May 27–31, 2026 | Uploaded via custom Python script (bypassed web-server timeout) |
| DSR JUN 2026 (Part 1) | June 1–9, 2026 | Uploaded via DB Manager |
| DSR JUN 2026 (Part 2) | June 10–19, 2026 | Uploaded + cleaned |
| DSR JUN 2026 (Part X) | June 20–26, 2026 | Uploaded + deduplication check |
| DSR JUN 2026 (Part Y) | June 27–28, 2026 | Uploaded + SMC/EI removal |
| APR 2026 Product File | April products | Category & brand mapping |
| MAY 2026 Product File | May products | Category & brand mapping |

### 7.3 Data Hygiene Operations

- **SMC/EI Invoice Removal:** Filtered out all invoices with SMC/EI in Invoice Number from entire database
- **HEAD OFFICE + UG SMART CHOICE Removal:** Removed non-eligible branch records
- **Deduplication:** Ensured unique customer mobile numbers (handled `9745640360` vs `9745640360.0` format differences)
- **Row count verification:** Validated counts across SQLite ↔ DuckDB ↔ PostgreSQL after each operation
- Scripts: `delete_bad_data.py`, `fix_dates.py`, `remove_smc_ei_branches.py`, `remove_jan_2025.py`

### 7.4 Caching Infrastructure

- **Redis** for production caching on Render
- **LocMemCache** for local development
- Fixed stale memory cache issues where local environments held old values after database updates
- Cache clearing automated on data upload
- API-level caching: Cohorts (24h), Branches (24h), Customers/Payments (15 min)

### 7.5 Performance Achievements

| Metric | Before | After |
|--------|--------|-------|
| Cohort Retention (cold) | 10+ seconds | 453 ms |
| Cohort Retention (warm) | 10+ seconds | 0.2 ms |
| Page load (12.6M rows) | Minutes | Milliseconds |
| MV refresh (all 26) | Manual/sequential | Concurrent/automated |

---

## 8. HR E-SIGN PORTAL

**Tech Stack:** Flask/Django, Google Sheets API, Google Drive API, gspread, Render (Deployment)

- Built a demo e-signature portal for HR processes (June 11, 2026)
- **Features:**
  - Digital signature capture section
  - Signature saved as image to Google Drive folder
  - Registration data saved to Google Sheet
  - Google Sheet: `1M-7VE8_YvWOoCOvWM5e9z_db5UNz2ohpO8XfFRQewu8`
  - Google Drive folder: `1MAq3reveGo5mkypa1pfKX_E3MNaijQ1v`
- **Deployment challenges resolved:**
  - Environment variable configuration for service account JSON on Render
  - Base64 encoding of service account credentials
  - Multiple debugging sessions for Google Sheets/Drive API authentication
- GitHub: `github.com/jasilmyg/hr.git`
- Live URL: `hr-hq19.onrender.com`

---

## 9. FIFA WORLD CUP CONTEST PLATFORM

**Tech Stack:** Flask/Next.js, Google Sheets API, gspread, Vercel (Deployment)

- Built a visually stunning FIFA World Cup prediction contest platform (June 29, 2026)
- **Features:**
  - User registration with unique 5-digit player IDs
  - Match prediction system with real World Cup fixtures from June 29, 2026
  - Leaderboard with rank badges (1st, 2nd, 3rd)
  - Points system: Exact Score Bonus, Correct Result, Correct Goal Difference
  - Once predicted, match hidden from user
  - Mobile-responsive design
  - Data storage in Google Sheet: `13fTBNtuSCDvcofAjFyKysAuMTJ-7ZToAC0MJXo0FymY`
  - Mobile number tracking in Google Sheet
- **Deployment:** Vercel
- GitHub: `github.com/jasilmyg/fifaworldcup.git`

---

## 10. ENTERPRISE RETAIL ANALYTICS DASHBOARD

**Tech Stack:** Django, PostgreSQL, Pandas, openpyxl → calamine (Rust-based)

### 10.1 High-Speed Reporting

- Swapped out standard `openpyxl` exporters for the Rust-based **`calamine`** engine
- Excel parsing and generation speed improvement: **5–10x faster**

### 10.2 DataTables Integration

- Replaced static HTML tables with dynamic, searchable, and sortable DataTables grids
- Interactive filtering and pagination

### 10.3 Monthly Category & Brand Performance Analysis

- **APR vs MAY 2026 comparison report**
- Format: AMT displayed in Crores (not Lakhs)
- Average Selling Price (ASP) calculation per category
- Top 15 brands per category popup reports
- Category breakdown: Mobile, Laptop, Tablet, AC, Washing Machine, Refrigerator, etc.
- Removed non-essential categories: Spare, Others, Demo
- Multi-sheet Excel report with all category reports in one file

### 10.4 Advanced Metrics Integration

- Dynamic Average Selling Price (ASP) mapping
- Product-to-category cross-referencing
- Branch filter with multiple branch selection
- Custom date range filtering
- Values displayed in Crores for large amounts, Lakhs for AVG bill value

### 10.5 Branch Download System

- New sidebar section for downloading unique customer data per branch
- Custom date selection
- Multiple branch selection option
- Excel download with customer details
- Available only for `mygadmin` role

---

## 11. SECURITY FEATURES IMPLEMENTED

### 11.1 FaceLock System (Loyalty Dashboard)

- **Passive 3D Liveness Detection** to prevent photo spoofing
- **Blink Detection** to verify live user presence
- Facial recognition threshold tuning: 0.57 (balanced strictness/usability)
- Multiple batches of face photos added to FaceLock database
- Strict threshold enforcement to prevent false positives

### 11.2 Authentication & Access Control

- **Role-Based Access Control (RBAC):**
  - `mygadmin` — Full admin access to all sections
  - `shestart` — Restricted to She Start section only
  - Staff role — Filtered to own branch only
- **Password reset** and **global session revocation** features
- **Download button visibility** restricted to admin users only

---

## MONTH-BY-MONTH CHRONOLOGICAL TIMELINE

### APRIL 2026

| Week | Work Done |
|------|-----------|
| Week 1 (Apr 1–7) | Initial Loyalty Dashboard setup, materialized views architecture, PostgreSQL connection from DigitalOcean |
| Week 2 (Apr 8–14) | Fast-path architecture for sub-second dashboard, cohort retention calculations |
| Week 3 (Apr 15–21) | FaceLock implementation (3D liveness, blink detection), password reset, session revocation |
| Week 4 (Apr 22–30) | Target Executive Dashboard, DB file upload with hygiene filters, premium Plotly aesthetics, glassmorphism redesign, LSTM forecast cache in PostgreSQL |

### MAY 2026

| Week | Work Done |
|------|-----------|
| Week 1 (May 1–7) | Campaign Analysis with advanced AI console, Random Forest Score Models, MLP hyperparameter tuning |
| Week 2 (May 8–14) | Malayalam Calendar library development (908 lines), ML model training on 2020–2025 data |
| Week 3 (May 18–24) | Cohort retention fix (cross-join bug), LTV recalculation, Kerala text removal, Target linear line |
| Week 4 (May 25–31) | Enterprise Retail Analytics Dashboard (APR vs MAY reports), DataTables integration, DSR MAY data uploads (Parts 1–3), SMC/EI data cleaning |
| Week 4+ (May 29–31) | Monthly Retention section, Campaign Analysis (Dormant Resurrection), LSTM prediction engine, dormancy risk meters |
| May 31 | **SHE START Dashboard** — Full build: Google Sheets sync, 6-panelist scoring, inline editing, SweetAlert2, Tab navigation, access control |

### JUNE 2026

| Week | Work Done |
|------|-----------|
| Week 1 (Jun 1–7) | DSR MAY Part 3 upload, MV refresh automation, DB Manager success messages, Enterprise AI Agent (full 16-phase build), NVIDIA API integration, n8n chat integration, Redemption Analysis, Loyalty Point Matrix |
| Week 2 (Jun 8–14) | AI Agent optimization (5–10 min → seconds), OpenRouter API integration, sales prediction (Jun 13 — Day & Night offers analysis), OSG portal claims processing, WhatsApp cut-off date management, DB Manager password recovery |
| Jun 11 | **HR e-Sign Portal** — Built demo e-signature portal with Google Sheets/Drive integration |
| Jun 13 | **Real-time sales prediction** — Predicted end-of-day sale from 2 PM data point using deep learning |
| Week 3 (Jun 16–22) | **Bigg Boss Registration Portal** — Full build: hero section, Mohanlal image positioning, form section, video upload (Google Drive OAuth), mobile responsive, social icons |
| Jun 19–22 | Bigg Boss refinements: popup banner system, audition closed page, mobile layout fixes, server-side validation, video upload debugging |
| Jun 20 | DSR JUN Part 2 data upload + MV refresh |
| Jun 22 | OSG Portal: Claims investigation, WhatsApp message template integration (registered, replacement, repair completed, spare parts) |
| Jun 22 | n8n Chat Agent: Connected to portal, PostgreSQL integration, streaming responses |
| Week 4 (Jun 23–30) | n8n chat refinement, OSG Google Apps Script v6 (SR Number system), DSR JUN Parts X & Y upload |
| Jun 25 | **Audition Closed page** for Bigg Boss |
| Jun 27 | **FoneFlix Pivot** — Complete rebranding from Bigg Boss to FoneFlix contest, new Google Drive/Sheets, new hero images |
| Jun 27 | **Sales prediction report** — Predicted daily final sale from 5:35 PM data point |
| Jun 27–28 | Future Store analysis: Balussery Future, Mavelikara Future, Falnir Future — New vs Repeat customer reports |
| Jun 29 | **FIFA World Cup Contest** — Built prediction platform, Google Sheets integration, Vercel deployment |
| Jun 29 | OSG Portal: WhatsApp templates (repair completed, spare parts), Google Apps Script SR Number sync, Follow-up Notes auto-sync |
| Jun 30 | Branch-wise customer download with multiple selection, RFM segmentation data download, n8n AI Agent portal connection debugging |

---

## TECHNICAL STATISTICS SUMMARY

| Metric | Count |
|--------|-------|
| **Projects Worked On** | 9+ |
| **Dashboard Pages/Templates** | 29 |
| **REST API Endpoints** | 20+ |
| **Git Commits (Apr–Jun)** | 80+ (Loyalty Dashboard) + 7+ (OSG Portal) |
| **Materialized Views** | 26 |
| **Database Size** | 12.6M+ rows |
| **ML Models Deployed** | 4 (Random Forest, MLP, GradientBoosting, LSTM) |
| **Custom Libraries Built** | 1 (MalayalamCalendarFeaturizer — 908 lines) |
| **Google Sheets Integrations** | 5+ |
| **WhatsApp Templates** | 4 |
| **LLM APIs Integrated** | 3 (NVIDIA Nemotron, OpenRouter, OpenAI via n8n) |
| **Data Files Uploaded** | 10+ DSR Excel files |
| **Conversations Logged** | 50+ development sessions |
| **Deployment Platforms** | 3 (Render, Vercel, GitHub Pages) |
| **Languages Used** | Python, JavaScript, HTML, CSS, SQL, Google Apps Script |
| **Key Frameworks** | Django, Flask, DRF, Scikit-Learn, Plotly, Chart.js, Pandas |

---

## GITHUB REPOSITORIES

| Repository | Project |
|------------|---------|
| `github.com/jasilmyg/Loyalty-Main-Dashboard` | myG Loyalty Analytics Dashboard |
| `github.com/jasilnbalussery/OSG-myG-PORTAL` | OSG Claims Management Portal |
| `github.com/jasilmyg/bigboss` | Bigg Boss / FoneFlix Registration Portal |
| `github.com/jasilmyg/hr` | HR e-Sign Portal |
| `github.com/jasilmyg/fifaworldcup` | FIFA World Cup Contest Platform |

---

*This report was compiled from git history, conversation logs, source code analysis, and work history records spanning April 1 – June 30, 2026.*
