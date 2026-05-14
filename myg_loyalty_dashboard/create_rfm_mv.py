"""
Create mv_rfm_segments - pre-computed RFM segmentation from mv_customer_dates + mv_customer_summary.
This is computed once and updated when data changes, enabling instant dashboard loads.
"""
import os, django, time
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "myg_loyalty_dashboard.settings")
django.setup()

from django.db import connection

def run(label, sql):
    print(f"[{label}] Starting...")
    t0 = time.time()
    with connection.cursor() as cur:
        cur.execute(sql)
    connection.commit()
    print(f"[{label}] Done in {time.time()-t0:.1f}s")

run("mv_rfm_segments DROP", "DROP MATERIALIZED VIEW IF EXISTS mv_rfm_segments CASCADE")

run("mv_rfm_segments CREATE", """
    CREATE MATERIALIZED VIEW mv_rfm_segments AS
    WITH rfm_base AS (
        SELECT cd.mobile,
               (CURRENT_DATE - cd.lv_month)::INT AS recency,
               cs.visits AS frequency,
               cs.total_spend AS monetary
        FROM mv_customer_dates cd
        JOIN mv_customer_summary cs ON cs.mobile = cd.mobile
        WHERE cs.total_spend IS NOT NULL
          AND cd.lv_month >= cd.fv_month
    ),
    scored AS (
        SELECT mobile, recency, frequency, monetary,
            CASE WHEN recency<=90 THEN 5 WHEN recency<=180 THEN 4
                 WHEN recency<=365 THEN 3 WHEN recency<=730 THEN 2 ELSE 1 END AS r_score,
            CASE WHEN frequency>=5 THEN 5 WHEN frequency=4 THEN 4
                 WHEN frequency=3 THEN 3 WHEN frequency=2 THEN 2 ELSE 1 END AS f_score,
            NTILE(5) OVER (ORDER BY monetary ASC) AS m_score
        FROM rfm_base
    ),
    segmented AS (
        SELECT mobile, recency, frequency, monetary, r_score, f_score, m_score,
            r_score::TEXT||f_score::TEXT||m_score::TEXT AS rfm_code,
            CASE
                WHEN r_score>=4 AND f_score>=4 AND m_score>=4 THEN 'Champions'
                WHEN r_score>=3 AND f_score>=3 AND m_score>=3 THEN 'Loyal'
                WHEN r_score>=4 AND f_score<=2               THEN 'New'
                WHEN r_score=2 AND f_score>=3 AND m_score>=3 THEN 'At Risk'
                WHEN r_score=1                               THEN 'Lost'
                ELSE 'Others'
            END AS segment
        FROM scored
    )
    SELECT mobile, recency, frequency, monetary::FLOAT,
           r_score, f_score, m_score, rfm_code, segment
    FROM segmented
""")

run("mv_rfm_segments INDEX segment", "CREATE INDEX ON mv_rfm_segments (segment)")
run("mv_rfm_segments INDEX mobile", "CREATE UNIQUE INDEX ON mv_rfm_segments (mobile)")

with connection.cursor() as cur:
    cur.execute("""
        SELECT segment, COUNT(*) AS cnt, 
               SUM(monetary)::FLOAT, AVG(monetary)::FLOAT
        FROM mv_rfm_segments
        GROUP BY segment ORDER BY cnt DESC
    """)
    print("\nmv_rfm_segments summary:")
    for r in cur.fetchall():
        print(f"  {r[0]:15s}  count={r[1]:,}  total_rev={r[2]/10000000:.1f}Cr  avg={r[3]:.0f}")

print("\nDone! RFM MV ready.")
