"""Product Penetration V2 report.

Layout (one row per Month / Quarter / Year):

    DATABASE (period ending)   | PERIOD | PURCHASED COUNT | PURCHASED VALUE
    Mobile purchased, NOT      |        | customers who   | sales value of those
    purchased AC/TV/WM/REF     |        | bought AC/TV/   | AC/TV/WM/REF purchases
                               |        | WM/REF in the   |
                               |        | period          |

Definitions (same customer / invoice filters as the original Product Penetration report):
  * Database (period ending) = customers who bought a Mobile on/before the period end and have NOT bought a
    Washing Machine, Refrigerator, TV or Air Conditioner on/before that same date.
  * Opening database         = same, measured at the START of the period (before the period began).
  * Purchased Count          = customers from the opening database who bought AC / TV / WM / REF during the
                               period (a customer is counted once, in the period of their first such purchase).
  * Purchased Value          = sold-price total of those customers' AC / TV / WM / REF purchases in the period.
Periods are calendar months, calendar quarters (JFM, AMJ, JAS, OND) or calendar years.
"""
import calendar
import traceback
from datetime import date, datetime, timedelta

from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import JsonResponse
from django.views import View
from django.views.generic import TemplateView

MAX_PERIODS = {'month': 36, 'quarter': 20, 'year': 10}
QUARTER_NAMES = {1: 'JFM', 2: 'AMJ', 3: 'JAS', 4: 'OND'}
FAR_FUTURE = "toDate('2100-01-01')"

MOB_FILTER = """
    length(i.customer_mobile) = 10
    AND i.customer_mobile != ''
    AND i.customer_mobile NOT IN ('1313131313','0000000000','9999999999')
    AND toDate(i.date) != toDate('1970-01-01')
    AND i.invoice_total > 0
"""


def _period_start(d, unit):
    if unit == 'month':
        return date(d.year, d.month, 1)
    if unit == 'quarter':
        return date(d.year, 3 * ((d.month - 1) // 3) + 1, 1)
    return date(d.year, 1, 1)


def _period_end(start, unit):
    if unit == 'month':
        return date(start.year, start.month, calendar.monthrange(start.year, start.month)[1])
    if unit == 'quarter':
        m = start.month + 2
        return date(start.year, m, calendar.monthrange(start.year, m)[1])
    return date(start.year, 12, 31)


def _next_start(start, unit):
    return _period_end(start, unit) + timedelta(days=1)


def _label(start, unit):
    if unit == 'month':
        return start.strftime('%b %Y')
    if unit == 'quarter':
        return f"{QUARTER_NAMES[(start.month - 1) // 3 + 1]} {start.year}"
    return str(start.year)


def _periods(start, end, unit):
    out, cur = [], _period_start(start, unit)
    while cur <= end:
        out.append(cur)
        cur = _next_start(cur, unit)
    return out


def _sql_period_start(col, unit):
    return {'month': f"toStartOfMonth({col})",
            'quarter': f"toStartOfQuarter({col})",
            'year': f"toStartOfYear({col})"}[unit]


_BASE_CTES = """
    WITH rws AS (
        SELECT i.customer_mobile AS c,
               toDate(i.date) AS d,
               upper(extract(s.item_code, '^([A-Za-z]+)')) AS pf,
               toFloat64(s.sold_price) AS v
        FROM azure_sales_report s
        JOIN azure_invoice_report i ON s.invoice_no = i.invoice_no
        WHERE {mob_filter}
          AND s.sold_price > 0
          AND pf IN ('MOB', 'WSM', 'REF', 'TV', 'AC')
          AND toDate(i.date) <= toDate('{end}')
    ),
    cust AS (
        SELECT c,
               if(countIf(pf = 'MOB') = 0, {far}, minIf(d, pf = 'MOB'))  AS fm,
               if(countIf(pf != 'MOB') = 0, {far}, minIf(d, pf != 'MOB')) AS fc
        FROM rws
        GROUP BY c
        HAVING countIf(pf = 'MOB') > 0
    )
"""


def _ctes(end):
    return _BASE_CTES.format(mob_filter=MOB_FILTER, end=end.isoformat(), far=FAR_FUTURE)


def _sql_counts(periods, unit, end):
    """Database at period end / period start, and customers converting inside the period."""
    ps = ", ".join(f"toDate('{p.isoformat()}')" for p in periods)
    pe = ", ".join(f"toDate('{_period_end(p, unit).isoformat()}')" for p in periods)
    return f"""
    {_ctes(end)}
    SELECT ps,
           countIf(fm <= pe AND fc > pe)                AS db_end,
           countIf(fm < ps  AND fc >= ps)               AS db_open,
           countIf(fm < ps  AND fc >= ps AND fc <= pe)  AS purchased_count
    FROM cust
    ARRAY JOIN [{ps}] AS ps, [{pe}] AS pe
    GROUP BY ps
    ORDER BY ps
    """


def _sql_value(start, end, unit):
    """Value of AC/TV/WM/REF purchases made in a period by customers who were in that period's opening database."""
    pstart = _sql_period_start('r.d', unit)
    return f"""
    {_ctes(end)}
    SELECT {pstart} AS ps,
           uniqExact(r.c) AS n,
           sum(r.v)       AS val
    FROM rws r
    JOIN cust u ON r.c = u.c
    WHERE r.pf != 'MOB'
      AND r.d >= toDate('{start.isoformat()}')
      AND u.fm < {pstart}
      AND u.fc >= {pstart}
    GROUP BY ps
    ORDER BY ps
    """


class ProductPenetrationV2View(LoginRequiredMixin, TemplateView):
    template_name = 'dashboard/product_penetration_v2.html'


class ProductPenetrationV2APIView(LoginRequiredMixin, View):
    """GET ?unit=month|quarter|year&from=YYYY-MM-DD&to=YYYY-MM-DD"""

    def get(self, request):
        try:
            from analytics.clickhouse_service import get_ch_client

            unit = request.GET.get('unit', 'quarter')
            if unit not in MAX_PERIODS:
                return JsonResponse({'status': 'error', 'message': 'unit must be month, quarter or year'}, status=400)
            today = date.today()
            try:
                d_to = datetime.strptime(request.GET.get('to', ''), '%Y-%m-%d').date()
            except ValueError:
                d_to = today
            try:
                d_from = datetime.strptime(request.GET.get('from', ''), '%Y-%m-%d').date()
            except ValueError:
                d_from = date(d_to.year - 1, d_to.month, 1)
            if d_from > d_to:
                d_from, d_to = d_to, d_from

            # snap the range to whole periods
            start = _period_start(d_from, unit)
            end = _period_end(_period_start(d_to, unit), unit)
            periods = _periods(start, end, unit)
            if len(periods) > MAX_PERIODS[unit]:
                return JsonResponse({'status': 'error',
                                     'message': f'Please select at most {MAX_PERIODS[unit]} {unit}s.'}, status=400)

            ch = get_ch_client()
            counts = {r[0]: r[1:] for r in ch.query(_sql_counts(periods, unit, end)).result_rows}
            values = {r[0]: r[1:] for r in ch.query(_sql_value(start, end, unit)).result_rows}

            rows, tot_count, tot_value = [], 0, 0.0
            for p in periods:
                db_end, db_open, pcount = (int(x) for x in counts.get(p, (0, 0, 0)))
                pval = float(values.get(p, (0, 0.0))[1])
                tot_count += pcount
                tot_value += pval
                rows.append({
                    'label': _label(p, unit), 'start': p.isoformat(), 'end': _period_end(p, unit).isoformat(),
                    'partial': _period_end(p, unit) > today,
                    'db_end': db_end, 'db_open': db_open,
                    'purchased_count': pcount, 'purchased_value': round(pval, 2),
                })

            return JsonResponse({
                'status': 'success', 'unit': unit,
                'from': start.isoformat(), 'to': end.isoformat(),
                'rows': rows,
                'total': {'purchased_count': tot_count, 'purchased_value': round(tot_value, 2)},
            })
        except Exception as e:  # noqa: BLE001
            return JsonResponse({'status': 'error', 'message': str(e), 'trace': traceback.format_exc()}, status=500)
