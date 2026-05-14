"""
Create mv_gap_analysis - pre-computed gap segmentation from mv_customer_dates.
Uses fv_month and lv_month to estimate avg gap = (lv_month - fv_month) / max(visits-1, 1)
This is an approximation but avoids the heavy window function scan on 12M rows.
Run: python create_gap_mv.py
"""
import os
import django
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "myg_loyalty_dashboard.settings")
django.setup()

from django.db import connection
import time

def run(label, sql):
    print(f"[{label}] Starting...")
    t0 = time.time()
    with connection.cursor() as cur:
        cur.execute(sql)
    connection.commit()
    print(f"[{label}] Done in {time.time()-t0:.1f}s")

run("mv_gap_analysis DROP", "DROP MATERIALIZED VIEW IF EXISTS mv_gap_analysis CASCADE")

run("mv_gap_analysis CREATE", """
    CREATE MATERIALIZED VIEW mv_gap_analysis AS
    WITH customer_gaps AS (
        SELECT
            cd.mobile,
            cs.visits,
            -- Estimated avg gap: total span / number of intervals
            CASE 
                WHEN cs.visits > 1 
                THEN ((cd.lv_month - cd.fv_month)::FLOAT / (cs.visits - 1))
                ELSE NULL
            END AS avg_gap_days
        FROM mv_customer_dates cd
        JOIN mv_customer_summary cs ON cs.mobile = cd.mobile
        WHERE cs.visits >= 2
    ),
    bucketed AS (
        SELECT mobile, avg_gap_days,
            CASE
                WHEN avg_gap_days<=7    THEN '1-7 Days'
                WHEN avg_gap_days<=30   THEN '8-30 Days'
                WHEN avg_gap_days<=60   THEN '31-60 Days'
                WHEN avg_gap_days<=90   THEN '61-90 Days'
                WHEN avg_gap_days<=180  THEN '91-180 Days'
                WHEN avg_gap_days<=365  THEN '180-365 Days'
                WHEN avg_gap_days<=730  THEN '1-2 Years'
                WHEN avg_gap_days<=1095 THEN '2-3 Years'
                WHEN avg_gap_days<=1460 THEN '3-4 Years'
                ELSE '4+ Years'
            END AS gap_range,
            CASE
                WHEN avg_gap_days<=7    THEN 1 WHEN avg_gap_days<=30   THEN 2
                WHEN avg_gap_days<=60   THEN 3 WHEN avg_gap_days<=90   THEN 4
                WHEN avg_gap_days<=180  THEN 5 WHEN avg_gap_days<=365  THEN 6
                WHEN avg_gap_days<=730  THEN 7 WHEN avg_gap_days<=1095 THEN 8
                WHEN avg_gap_days<=1460 THEN 9 ELSE 10
            END AS sort_order
        FROM customer_gaps
        WHERE avg_gap_days IS NOT NULL
    )
    SELECT
        gap_range,
        sort_order,
        COUNT(DISTINCT mobile)::bigint AS customers,
        AVG(avg_gap_days)::FLOAT AS avg_gap
    FROM bucketed
    GROUP BY gap_range, sort_order
    ORDER BY sort_order ASC
""")

run("mv_gap_analysis INDEX", "CREATE UNIQUE INDEX ON mv_gap_analysis (sort_order)")

with connection.cursor() as cur:
    cur.execute("SELECT gap_range, customers, avg_gap FROM mv_gap_analysis ORDER BY sort_order")
    print("\nmv_gap_analysis sample:")
    for r in cur.fetchall():
        print(f"  {r[0]:20s}  customers={r[1]:,}  avg_gap={r[2]:.1f} days")

print("\nDone! Gap analysis MV ready.")
