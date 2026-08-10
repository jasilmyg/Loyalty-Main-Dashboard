import os
import django
import time

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'myg_loyalty_dashboard.settings')
django.setup()
from django.db import connection
from analytics.services import TABLE

def create_mvs():
    print("Creating mv_yearly_matrix...")
    start = time.time()
    with connection.cursor() as cursor:
        cursor.execute("DROP MATERIALIZED VIEW IF EXISTS mv_yearly_matrix CASCADE;")
        cursor.execute(f"""
            CREATE MATERIALIZED VIEW mv_yearly_matrix AS
            WITH base AS (
                SELECT s."Customer Mobile" AS mob, s."Invoice Number" AS inv, s."Date" AS sale_d
                FROM {TABLE} s
                WHERE s."Customer Mobile" IS NOT NULL
                  AND s."Customer Mobile" ~ '^[0-9]{{10}}$'
                  AND s."Customer Mobile" NOT IN ('1313131313','0000000000','9999999999')
                  AND s."Date" IS NOT NULL
            ),
            cust_first AS (SELECT b.mob, DATE_TRUNC('year',  MIN(b.sale_d))::date AS first_bucket FROM base b GROUP BY b.mob),
            agg AS (
                SELECT DATE_TRUNC('year',  b.sale_d)::date AS period_start, TO_CHAR(DATE_TRUNC('year', b.sale_d), 'YYYY') AS period_id,
                       COUNT(DISTINCT b.mob)::bigint AS total_members,
                       COUNT(DISTINCT b.mob) FILTER (WHERE cf.first_bucket = DATE_TRUNC('year',  b.sale_d)::date)::bigint AS new_members,
                       COUNT(DISTINCT b.inv)::bigint AS total_visits
                FROM base b JOIN cust_first cf ON cf.mob = b.mob GROUP BY 1, 2
            )
            SELECT a.period_id, a.period_start, a.total_members, a.new_members, a.total_visits
            FROM agg a ORDER BY a.period_start ASC
        """)
    print(f"Created mv_yearly_matrix in {time.time() - start:.2f}s")
    
    print("Creating mv_yearly_matrix_branch...")
    start = time.time()
    with connection.cursor() as cursor:
        cursor.execute("DROP MATERIALIZED VIEW IF EXISTS mv_yearly_matrix_branch CASCADE;")
        cursor.execute(f"""
            CREATE MATERIALIZED VIEW mv_yearly_matrix_branch AS
            WITH base AS (
                SELECT s."Customer Mobile" AS mob, s."Invoice Number" AS inv, s."Date" AS sale_d, UPPER(s."Branch") as branch
                FROM {TABLE} s
                WHERE s."Customer Mobile" IS NOT NULL
                  AND s."Customer Mobile" ~ '^[0-9]{{10}}$'
                  AND s."Customer Mobile" NOT IN ('1313131313','0000000000','9999999999')
                  AND s."Date" IS NOT NULL
                  AND s."Branch" IS NOT NULL
            ),
            cust_first AS (SELECT b.mob, DATE_TRUNC('year',  MIN(b.sale_d))::date AS first_bucket FROM base b GROUP BY b.mob),
            agg AS (
                SELECT DATE_TRUNC('year',  b.sale_d)::date AS period_start, TO_CHAR(DATE_TRUNC('year', b.sale_d), 'YYYY') AS period_id, b.branch,
                       COUNT(DISTINCT b.mob)::bigint AS total_members,
                       COUNT(DISTINCT b.mob) FILTER (WHERE cf.first_bucket = DATE_TRUNC('year',  b.sale_d)::date)::bigint AS new_members,
                       COUNT(DISTINCT b.inv)::bigint AS total_visits
                FROM base b JOIN cust_first cf ON cf.mob = b.mob GROUP BY 1, 2, 3
            )
            SELECT a.period_id, a.period_start, a.branch, a.total_members, a.new_members, a.total_visits
            FROM agg a ORDER BY a.period_start ASC
        """)
    print(f"Created mv_yearly_matrix_branch in {time.time() - start:.2f}s")

if __name__ == '__main__':
    create_mvs()
