# SHE START – Startup Applicant Evaluation Dashboard

The goal is to implement a modern, premium executive-level dashboard for the "She Start" program. This dashboard will parse the applicant responses and evaluation scores to provide powerful analytics, visualizations, and an AI-powered selection intelligence matrix.

## User Review Required

> [!IMPORTANT]
> **Data Parsing Strategy:** The plan proposes reading the two provided Excel files (`myG “She Start” Detailed Application Form  (Responses).xlsx` and `She Start Evaluation Sheet .xlsx`) directly using pandas, merging them based on the applicant's name/details, and calculating the metrics dynamically. Is this acceptable, or would you prefer a mechanism to upload these files through the UI into a database table?

> [!IMPORTANT]
> **UI Framework:** We will use vanilla CSS, Bootstrap 5, and Plotly/Chart.js (which are already included in your `base.html`) to achieve the premium, modern aesthetic without introducing new heavy dependencies.

## Proposed Changes

### 1. Template & UI Adjustments

#### [MODIFY] [base.html](file:///c:/Users/jasil_myg/Desktop/myG%20Loyalty%20Main%20Dashboard/myg_loyalty_dashboard/templates/base.html)
- Add a new dedicated section in the left sidebar under "myG Loyalty Dashboard" (or as a separate major category) for "SHE START".
- Use a distinct icon (e.g., `bi-rocket` or `bi-stars`) to make it stand out.

#### [NEW] [she_start.html](file:///c:/Users/jasil_myg/Desktop/myG%20Loyalty%20Main%20Dashboard/myg_loyalty_dashboard/templates/dashboard/she_start.html)
- Create the core dashboard view implementing the premium light mode aesthetic (background `#F8FAFC`, white cards with soft shadows, 16px border radius).
- Include the following sections:
    - **KPI Cards**: Top metrics (Total Applicants, Average Score, Top Score, etc.) with gradient accents.
    - **Demographics & Profile**: District map/charts, age/employment distributions.
    - **Applicant Intelligence**: Top applicants leaderboard and the "Startup Potential Matrix" (Bubble Chart: X=Innovation, Y=Growth, Size=Income/Score).
    - **Panelist Analytics**: Heatmaps and strict/generous panelist variance.
    - **AI Text Analytics**: Word clouds/frequency charts for Strengths and Risks.
    - **Deep Dive Modal/Section**: A detailed 360-degree view when clicking on a specific applicant.

---

### 2. Backend Routing & Views

#### [MODIFY] [urls.py](file:///c:/Users/jasil_myg/Desktop/myG%20Loyalty%20Main%20Dashboard/myg_loyalty_dashboard/dashboard/urls.py)
- Add `path('she-start/', views.SheStartView.as_view(), name='she_start')`
- Add `path('api/v1/she-start/data/', views.SheStartDataAPIView.as_view(), name='she_start_data_api')`

#### [MODIFY] [views.py](file:///c:/Users/jasil_myg/Desktop/myG%20Loyalty%20Main%20Dashboard/myg_loyalty_dashboard/dashboard/views.py)
- **`SheStartView`**: Renders the `she_start.html` template.
- **`SheStartDataAPIView`**: 
    - Loads the raw data from `myG “She Start” Detailed Application Form  (Responses).xlsx` and `She Start Evaluation Sheet .xlsx`.
    - Merges the form data with panelist evaluations.
    - Computes the derived metrics (e.g., Business Readiness Score, Selection Index).
    - Extracts strengths and risks.
    - Returns a comprehensive JSON payload for the frontend to render the charts and tables.

---

### 3. Data Processing Script

#### [NEW] [she_start_engine.py](file:///c:/Users/jasil_myg/Desktop/myG%20Loyalty%20Main%20Dashboard/myg_loyalty_dashboard/analytics/she_start_engine.py)
- A helper module containing functions to read and clean the Excel files.
- Includes logic to calculate:
    - **Selection Index**: Weighted score (30% Growth, 20% Innovation, etc.).
    - **Business Readiness Score**.
    - **Text Analytics**: Simple frequency counting for Strengths (e.g., 'Leadership', 'Confidence') and Risks (e.g., 'Lack of Experience').

## Verification Plan

### Automated/Code Verification
- Verify the API endpoint correctly loads and merges the two Excel files without throwing exceptions.
- Verify the JSON payload structure matches what the frontend Javascript expects.

### Manual Verification
- Open the dashboard in the browser and navigate to the new "She Start" section.
- Validate that all KPI cards load, charts render properly (Radar, Bubble, Bar charts), and the leaderboard is functional.
- Ensure the UI looks premium and matches the specified design requirements.
