"""
RFM Feature Engine
==================
Extracts 10 RFM features per customer directly from ClickHouse.
Pushes all computation to the database — no Python-side sampling.
Returns a Pandas DataFrame ready for model training/scoring.
"""
import os
import logging
import pandas as pd
import numpy as np

logger = logging.getLogger(__name__)


def _get_ch_client():
    import clickhouse_connect
    return clickhouse_connect.get_client(
        host=os.environ.get("CH_HOST", "ytoyqewr56.ap-south-1.aws.clickhouse.cloud"),
        port=int(os.environ.get("CH_PORT", "8443")),
        username=os.environ.get("CH_USER", "default"),
        password=os.environ.get("CH_PASSWORD", "QyB2XKWS44Qt~"),
        database=os.environ.get("CH_DATABASE", "default"),
        secure=True, connect_timeout=30, send_receive_timeout=180,
    )


# ── Full RFM query (all customers, computed in ClickHouse) ────────────────────
RFM_QUERY = """
WITH ordered AS (
    SELECT
        customer_mobile,
        parsed_date,
        total_value,
        lagInFrame(parsed_date) OVER (
            PARTITION BY customer_mobile ORDER BY parsed_date
        ) AS prev_date
    FROM sales_data
    WHERE customer_mobile != ''
      AND length(customer_mobile) = 10
      AND total_value > 0
      AND parsed_date >= toDate('2020-01-01')
),
gaps AS (
    SELECT
        customer_mobile,
        parsed_date,
        total_value,
        if(prev_date IS NULL OR prev_date = toDate('1970-01-01'),
           NULL,
           dateDiff('day', prev_date, parsed_date)) AS gap_days
    FROM ordered
)
SELECT
    customer_mobile,
    dateDiff('day', max(parsed_date), today())              AS recency_days,
    count()                                                  AS frequency,
    round(avg(total_value), 2)                               AS avg_monetary,
    round(sum(total_value), 2)                               AS total_spend,
    dateDiff('day', min(parsed_date), max(parsed_date))      AS customer_tenure,
    dateDiff('day', min(parsed_date), today())               AS age_days,
    round(avgIf(gap_days, gap_days IS NOT NULL AND gap_days > 0), 1) AS avg_interpurchase_gap,
    round(max(total_value), 2)                               AS max_order_value,
    toYear(min(parsed_date))                                 AS cohort_year,
    toMonth(min(parsed_date))                                AS first_month
FROM gaps
GROUP BY customer_mobile
HAVING frequency >= 1
"""


def extract_rfm(client=None, min_frequency: int = 1) -> pd.DataFrame:
    """
    Extract full RFM feature table from ClickHouse.
    Returns DataFrame with columns:
        customer_mobile, recency_days, frequency, avg_monetary, total_spend,
        customer_tenure, age_days, avg_interpurchase_gap, max_order_value,
        cohort_year, first_month, dormancy_ratio
    """
    if client is None:
        client = _get_ch_client()

    logger.info("[RFM] Running feature extraction query on 1.3 Cr rows...")
    result = client.query(RFM_QUERY)
    cols = [
        'customer_mobile', 'recency_days', 'frequency', 'avg_monetary',
        'total_spend', 'customer_tenure', 'age_days', 'avg_interpurchase_gap',
        'max_order_value', 'cohort_year', 'first_month'
    ]
    df = pd.DataFrame(result.result_rows, columns=cols)
    logger.info(f"[RFM] Extracted {len(df):,} customers.")

    # Derived feature: dormancy ratio (recency / avg gap)
    df['avg_interpurchase_gap'] = df['avg_interpurchase_gap'].fillna(
        df['recency_days']
    ).clip(lower=1)
    df['dormancy_ratio'] = (df['recency_days'] / df['avg_interpurchase_gap']).clip(upper=50)

    # For BG/NBD: use MONTHS (not weeks) — smaller numbers converge better
    df['bgf_frequency'] = (df['frequency'] - 1).clip(lower=0)          # repeat purchases
    df['bgf_recency']   = ((df['age_days'] - df['recency_days']) / 30).clip(lower=0)
    df['bgf_T']         = (df['age_days'] / 30).clip(lower=0.1)        # customer age in months
    # Ensure bgf_recency <= bgf_T
    df['bgf_recency'] = df[['bgf_recency', 'bgf_T']].min(axis=1)

    # Filter: need at least min_frequency purchases
    df = df[df['frequency'] >= min_frequency].copy()

    logger.info(f"[RFM] Final feature table: {len(df):,} rows, {len(df.columns)} features.")
    return df


def extract_dormant_rfm(client=None, min_days: int = 180, max_days: int = 730) -> pd.DataFrame:
    """Returns RFM only for currently dormant at-risk customers (180-730 days)."""
    df = extract_rfm(client=client)
    mask = (df['recency_days'] >= min_days) & (df['recency_days'] <= max_days)
    return df[mask].copy()


def get_monthly_reactivations(client=None) -> pd.DataFrame:
    """
    Monthly count of reactivations (2020-2026).
    Definition: a purchase where the customer's PREVIOUS purchase was 180+ days ago.
    This works with data entirely within 2020-2026 — no pre-2020 baseline needed.
    """
    if client is None:
        client = _get_ch_client()

    logger.info("[RFM] Querying monthly reactivation counts (rolling 180-day gap)...")
    query = """
    WITH ordered AS (
        SELECT
            customer_mobile,
            parsed_date,
            total_value,
            lagInFrame(parsed_date) OVER (
                PARTITION BY customer_mobile ORDER BY parsed_date
            ) AS prev_date
        FROM sales_data
        WHERE customer_mobile != ''
          AND length(customer_mobile) = 10
          AND total_value > 0
          AND parsed_date >= toDate('2020-01-01')
    )
    SELECT
        toStartOfMonth(parsed_date) AS month,
        countIf(
            prev_date IS NOT NULL
            AND prev_date != toDate('1970-01-01')
            AND dateDiff('day', prev_date, parsed_date) >= 180
        ) AS reactivations
    FROM ordered
    GROUP BY month
    ORDER BY month
    """
    result = client.query(query)
    df = pd.DataFrame(result.result_rows, columns=['ds', 'y'])
    df['ds'] = pd.to_datetime(df['ds'])
    df['y'] = df['y'].astype(float)
    # Remove current (partial) month
    df = df[df['ds'] < pd.Timestamp.now().replace(day=1)].copy()
    logger.info(f"[RFM] Monthly reactivation data: {len(df)} months, total={int(df['y'].sum()):,}")
    return df


def get_historical_labels(client=None) -> pd.DataFrame:
    """
    Build binary labels for LightGBM training:
    - Customers dormant (180+ days gap) before July 2024
    - Label: did they return between Aug 2024 – Jan 2025? (1 = yes, 0 = no)
    """
    if client is None:
        client = _get_ch_client()

    logger.info("[RFM] Building historical labels for LightGBM...")
    query = """
    WITH last_before_cutoff AS (
        SELECT customer_mobile, max(parsed_date) as last_date
        FROM sales_data
        WHERE customer_mobile != ''
          AND length(customer_mobile) = 10
          AND total_value > 0
          AND parsed_date < toDate('2024-07-01')
        GROUP BY customer_mobile
        HAVING last_date >= toDate('2021-01-01')
    ),
    returned AS (
        SELECT DISTINCT customer_mobile
        FROM sales_data
        WHERE customer_mobile != ''
          AND parsed_date BETWEEN toDate('2024-07-01') AND toDate('2025-01-31')
          AND total_value > 0
    )
    SELECT
        lb.customer_mobile,
        lb.last_date,
        dateDiff('day', lb.last_date, toDate('2024-07-01')) as dormancy_days_at_cutoff,
        if(r.customer_mobile != '', 1, 0) as returned_label
    FROM last_before_cutoff lb
    LEFT JOIN returned r ON lb.customer_mobile = r.customer_mobile
    WHERE dateDiff('day', lb.last_date, toDate('2024-07-01')) >= 180
    """
    result = client.query(query)
    df = pd.DataFrame(result.result_rows, columns=[
        'customer_mobile', 'last_date', 'dormancy_days_at_cutoff', 'returned_label'
    ])
    logger.info(
        f"[RFM] Labels: {len(df):,} dormant customers, "
        f"{df['returned_label'].sum():,} returned ({df['returned_label'].mean()*100:.1f}%)"
    )
    return df
