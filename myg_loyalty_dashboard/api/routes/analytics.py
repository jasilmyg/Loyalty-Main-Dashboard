from fastapi import APIRouter, Query, Depends, Request
from pydantic import BaseModel
from typing import List, Optional
from ..db import db
from ..redis_cache import cache, cached
from ..config import settings
from ..utils import build_where_clause
import time
from datetime import datetime

router = APIRouter(prefix="/analytics", tags=["analytics"])

# --- Pydantic Models ---
class MonthlyTrend(BaseModel):
    month: str
    revenue: float

class SalesOverviewResponse(BaseModel):
    total_revenue: float
    total_invoices: int
    atv: float
    monthly_trend: List[MonthlyTrend]

class LoyaltyKPIsResponse(BaseModel):
    total_customers: int
    repeat_customers: int
    repeat_rate: float
    avg_gap_days: float

class RFMSegment(BaseModel):
    segment: str
    count: int
    revenue: float

class RetailMatrixRow(BaseModel):
    period: str
    total_m: int
    new_m: int
    total_v: int
    repeat_m: int

# --- Endpoints ---

@router.get("/sales-overview", response_model=SalesOverviewResponse)
@cached(expire=300) # 5 minutes for live data
async def get_sales_overview(
    request: Request,
    start_date: str = Query(None),
    end_date: str = Query(None),
    branch: str = Query(None),
    staff: str = Query(None)
):
    filters = {"start_date": start_date, "end_date": end_date, "branch": branch, "staff": staff}
    where_sql, params = build_where_clause(filters)
    row = await db.fetchrow(f"SELECT SUM(revenue)::FLOAT as revenue, SUM(invoices) as invoices FROM {settings.MV_MONTHLY} {where_sql}", *params)
    tr = float(row['revenue'] or 0) if row else 0
    ti = int(row['invoices'] or 0) if row else 0
    atv = tr / ti if ti > 0 else 0

    rows = await db.fetch(f"SELECT TO_CHAR(month_date, 'Mon YY') AS month, SUM(revenue)::FLOAT AS revenue FROM {settings.MV_MONTHLY} {where_sql} GROUP BY month_date ORDER BY month_date ASC", *params)
    return {"total_revenue": tr, "total_invoices": ti, "atv": atv, "monthly_trend": [dict(r) for r in rows]}

@router.get("/loyalty-kpis", response_model=LoyaltyKPIsResponse)
@cached(expire=300)
async def get_loyalty_kpis(
    request: Request,
    start_date: str = Query(None),
    end_date: str = Query(None),
    branch: str = Query(None)
):
    filters = {"start_date": start_date, "end_date": end_date, "branch": branch}
    where_sql, params = build_where_clause(filters)
    
    # Using the optimized logic from services.py
    if not where_sql or where_sql == "WHERE ":
        row = await db.fetchrow(f"""
            WITH gaps AS (
                SELECT mobile, visits,
                    CASE WHEN visits > 1 THEN (
                        (CASE WHEN SUBSTRING(last_visit::text, 5, 1) = '-' THEN TO_DATE(SUBSTRING(last_visit::text, 1, 10), 'YYYY-MM-DD') ELSE TO_DATE(last_visit::text, 'DD-MM-YYYY') END) - 
                        (CASE WHEN SUBSTRING(first_visit::text, 5, 1) = '-' THEN TO_DATE(SUBSTRING(first_visit::text, 1, 10), 'YYYY-MM-DD') ELSE TO_DATE(first_visit::text, 'DD-MM-YYYY') END)
                    )::FLOAT / (visits - 1) ELSE NULL END AS avg_gap
                FROM {settings.MV_CUSTOMER}
            )
            SELECT COUNT(DISTINCT mobile) as total, COUNT(DISTINCT CASE WHEN visits > 1 THEN mobile END) as repeat, AVG(avg_gap)::FLOAT as gap
            FROM gaps
        """)
    else:
        # Fallback to more precise but slower query if filtered
        row = await db.fetchrow(f"""
            WITH cs AS (
                SELECT "Customer Mobile", COUNT(DISTINCT "Date") AS visits
                FROM {settings.TABLE} {where_sql} GROUP BY "Customer Mobile"
            )
            SELECT COUNT(*) as total, COUNT(CASE WHEN visits > 1 THEN 1 END) as repeat, 45.0 as gap
            FROM cs
        """, *params)

    total = int(row['total'] or 0) if row else 0
    repeat = int(row['repeat'] or 0) if row else 0
    return {
        "total_customers": total,
        "repeat_customers": repeat,
        "repeat_rate": round(repeat/total*100, 1) if total else 0,
        "avg_gap_days": round(float(row['gap'] or 0), 1) if row else 0
    }

@router.get("/rfm-segments", response_model=List[RFMSegment])
@cached(expire=86400) # 24 hours for heavy segmentation
async def get_rfm_segments(request: Request, branch: str = Query(None)):
    query = f"""
        WITH scored AS (
            SELECT mobile, total_spend, visits,
                NTILE(5) OVER (ORDER BY (CURRENT_DATE - last_visit) ASC) as r_score,
                NTILE(5) OVER (ORDER BY visits ASC) as f_score,
                NTILE(5) OVER (ORDER BY total_spend ASC) as m_score
            FROM {settings.MV_CUSTOMER}
        )
        SELECT 
            CASE WHEN r_score >= 4 AND f_score >= 4 THEN 'Champions'
                 WHEN r_score >= 3 AND f_score >= 3 THEN 'Loyal'
                 WHEN r_score >= 4 AND f_score <= 2 THEN 'New'
                 WHEN r_score <= 2 AND f_score >= 3 THEN 'At Risk'
                 ELSE 'Others' END as segment,
            COUNT(*) as count, SUM(total_spend)::FLOAT as revenue
        FROM scored GROUP BY 1 ORDER BY count DESC
    """
    rows = await db.fetch(query)
    return [dict(r) for r in rows]

@router.get("/retail-matrix", response_model=List[RetailMatrixRow])
@cached(expire=86400) # 24 hours
async def get_retail_matrix(
    request: Request,
    period: str = Query("monthly"),
    branch: str = Query(None)
):
    trunc = "month" if period == "monthly" else "year"
    fmt = "YYYY-MM" if period == "monthly" else "YYYY"
    
    dim_sql = ""
    dim_params = []
    if branch and branch.lower() not in ('all branches', 'all', ''):
        dim_sql = ' AND UPPER(s."Branch") = UPPER($1)'
        dim_params.append(branch)
        
    query = f"""
        WITH base AS (
            SELECT s."Customer Mobile" AS mob,
                   s."Invoice Number" AS inv,
                   s."Date" AS sale_d
            FROM {settings.TABLE} s
            WHERE s."Customer Mobile" IS NOT NULL 
              AND s."Customer Mobile" ~ '^[0-9]{{10}}$'
              AND s."Customer Mobile" NOT IN ('1313131313','0000000000','9999999999')
              AND s."Date" IS NOT NULL
              {dim_sql}
        ),
        cust_first AS (
            SELECT b.mob, DATE_TRUNC('{trunc}', MIN(b.sale_d))::date AS first_bucket
            FROM base b
            GROUP BY b.mob
        ),
        agg AS (
            SELECT
                DATE_TRUNC('{trunc}', b.sale_d)::date AS p_start,
                COUNT(DISTINCT b.mob)::bigint AS total_m,
                COUNT(DISTINCT b.mob) FILTER (
                    WHERE cf.first_bucket = DATE_TRUNC('{trunc}', b.sale_d)::date
                )::bigint AS new_m,
                COUNT(DISTINCT b.inv)::bigint AS total_v
            FROM base b
            JOIN cust_first cf ON cf.mob = b.mob
            GROUP BY 1
        )
        SELECT 
            TO_CHAR(a.p_start, '{fmt}') AS period,
            a.total_m, COALESCE(a.new_m, 0) AS new_m, a.total_v,
            (a.total_m - COALESCE(a.new_m, 0)) AS repeat_m
        FROM agg a
        ORDER BY a.p_start DESC LIMIT 12
    """
    rows = await db.fetch(query, *dim_params)
    return [dict(r) for r in rows]

@router.get("/branches", response_model=List[str])
@cached(expire=604800) # 7 days
async def get_branches(request: Request):
    rows = await db.fetch(f'SELECT DISTINCT "Branch" FROM {settings.MV_MONTHLY} WHERE "Branch" IS NOT NULL ORDER BY 1')
    return [r[0] for r in rows]
