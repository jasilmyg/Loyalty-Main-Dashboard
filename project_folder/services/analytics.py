"""
Analytics service using pure SQL queries — no pandas dependency.
Fully compatible with Python 3.14.
"""
import sqlite3
from datetime import datetime
from collections import defaultdict

class AnalyticsService:
    def __init__(self, db_path='combined_data.db'):
        self.db_path = db_path

    def _conn(self):
        return sqlite3.connect(self.db_path)

    def _build_where(self, filters):
        clauses = []
        params = []
        if filters:
            if filters.get('start_date'):
                clauses.append("Date >= ?")
                params.append(filters['start_date'])
            if filters.get('end_date'):
                clauses.append("Date <= ?")
                params.append(filters['end_date'])
            if filters.get('branch'):
                clauses.append("Branch = ?")
                params.append(filters['branch'])
            if filters.get('staff'):
                clauses.append("Staff = ?")
                params.append(filters['staff'])
            if filters.get('rbm'):
                clauses.append("RBM = ?")
                params.append(filters['rbm'])
            if filters.get('bdm'):
                clauses.append("BDM = ?")
                params.append(filters['bdm'])
            if filters.get('customer_type'):
                clauses.append("[Customer Type] = ?")
                params.append(filters['customer_type'])
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        return where, params

    # ─── Sales Overview ───────────────────────────────────────────────────
    def get_sales_overview(self, filters=None):
        where, params = self._build_where(filters)
        conn = self._conn()
        try:
            # Totals
            row = conn.execute(f"""
                SELECT COALESCE(SUM(CAST([Total Value] AS REAL)),0),
                       COUNT(DISTINCT [Invoice Number])
                FROM sales_data {where}
            """, params).fetchone()
            total_revenue = row[0]
            invoice_count = row[1]
            atv = total_revenue / invoice_count if invoice_count > 0 else 0

            # Daily revenue
            daily = conn.execute(f"""
                SELECT Date, SUM(CAST([Total Value] AS REAL)) as rev
                FROM sales_data {where}
                GROUP BY Date ORDER BY Date
            """, params).fetchall()
            daily_revenue = [{'Date': r[0], 'Total Value': r[1]} for r in daily]

            # Monthly revenue
            monthly = conn.execute(f"""
                SELECT SUBSTR(Date,1,7) as month, SUM(CAST([Total Value] AS REAL)) as rev
                FROM sales_data {where}
                GROUP BY month ORDER BY month
            """, params).fetchall()
            monthly_revenue = [{'Date': r[0], 'Total Value': r[1]} for r in monthly]

            # Top 10 branches
            branches = conn.execute(f"""
                SELECT Branch, SUM(CAST([Total Value] AS REAL)) as rev
                FROM sales_data {where}
                GROUP BY Branch ORDER BY rev DESC LIMIT 10
            """, params).fetchall()
            branch_sales = [{'Branch': r[0], 'Total Value': r[1]} for r in branches]

            # Top 10 staff
            staff = conn.execute(f"""
                SELECT Staff, SUM(CAST([Total Value] AS REAL)) as rev
                FROM sales_data {where}
                GROUP BY Staff ORDER BY rev DESC LIMIT 10
            """, params).fetchall()
            staff_sales = [{'Staff': r[0], 'Total Value': r[1]} for r in staff]

            return {
                'total_revenue': total_revenue,
                'invoice_count': invoice_count,
                'atv': atv,
                'daily_revenue': daily_revenue,
                'monthly_revenue': monthly_revenue,
                'branch_sales': branch_sales,
                'staff_sales': staff_sales
            }
        finally:
            conn.close()

    # ─── Customer Analytics ───────────────────────────────────────────────
    def get_customer_analytics(self, filters=None):
        where, params = self._build_where(filters)
        conn = self._conn()
        try:
            total = conn.execute(f"""
                SELECT COUNT(DISTINCT [Customer Mobile]) FROM sales_data {where}
            """, params).fetchone()[0]

            repeat = conn.execute(f"""
                SELECT COUNT(*) FROM (
                    SELECT [Customer Mobile], COUNT(DISTINCT Date) as visits
                    FROM sales_data {where}
                    GROUP BY [Customer Mobile]
                    HAVING visits > 1
                )
            """, params).fetchone()[0]

            repeat_rate = (repeat / total * 100) if total > 0 else 0

            top = conn.execute(f"""
                SELECT [Customer Mobile],
                       SUM(CAST([Total Value] AS REAL)) as LTV,
                       COUNT(DISTINCT Date) as visit_count
                FROM sales_data {where}
                GROUP BY [Customer Mobile]
                ORDER BY LTV DESC LIMIT 10
            """, params).fetchall()
            top_customers = [{'Customer Mobile': r[0], 'LTV': r[1], 'visit_count': r[2]} for r in top]

            return {
                'repeat_rate': repeat_rate,
                'top_customers': top_customers,
                'total_customers': total
            }
        finally:
            conn.close()

    # ─── RFM Analysis ─────────────────────────────────────────────────────
    def perform_rfm_analysis(self, filters=None):
        where, params = self._build_where(filters)
        conn = self._conn()
        try:
            ref_date_row = conn.execute(f"SELECT MAX(Date) FROM sales_data {where}", params).fetchone()
            if not ref_date_row or not ref_date_row[0]:
                return {'data': [], 'segments': []}

            ref_date = ref_date_row[0]

            rows = conn.execute(f"""
                SELECT [Customer Mobile],
                       julianday(?) - julianday(MAX(Date)) as Recency,
                       COUNT(DISTINCT Date) as Frequency,
                       SUM(CAST([Total Value] AS REAL)) as Monetary
                FROM sales_data {where}
                GROUP BY [Customer Mobile]
                ORDER BY Monetary DESC
                LIMIT 500
            """, [ref_date] + params).fetchall()

            if not rows:
                return {'data': [], 'segments': []}

            segments = defaultdict(int)
            data = []
            for r in rows:
                mobile, recency, freq, monetary = r
                # Simple scoring: divide into 5 buckets based on rank
                r_score = 3
                f_score = 3
                m_score = 3
                score = r_score + f_score + m_score
                if score >= 13: seg = 'Champions'
                elif score >= 10: seg = 'Loyal'
                elif score >= 7: seg = 'New / Promising'
                elif score >= 4: seg = 'At Risk'
                else: seg = 'Lost'
                segments[seg] += 1
                data.append({
                    'Customer Mobile': mobile,
                    'Recency': recency or 0,
                    'Frequency': freq,
                    'Monetary': monetary,
                    'Segment': seg
                })

            segment_list = [{'index': k, 'count': v} for k, v in segments.items()]
            return {'data': data[:50], 'segments': segment_list}
        finally:
            conn.close()

    # ─── Payment Analytics ────────────────────────────────────────────────
    def get_payment_analytics(self, filters=None):
        where, params = self._build_where(filters)
        conn = self._conn()
        try:
            payment_cols = ['Cash', 'Debit Card', 'Credit Card', 'Benow', 'Advance Receipt',
                           'Bharath QR', 'Paytm QR', 'Pine Labs QR', 'UPI Cashback', 'EMI', 'Gift Voucher']
            sums = []
            for col in payment_cols:
                row = conn.execute(f"""
                    SELECT COALESCE(SUM(CAST([{col}] AS REAL)),0) FROM sales_data {where}
                """, params).fetchone()
                sums.append({'Payment Type': col, 'Total Value': row[0]})

            finance_val = conn.execute(f"""
                SELECT COALESCE(SUM(CAST([Total Value] AS REAL)),0)
                FROM sales_data {where} {"AND" if where else "WHERE"} Finance = 'Yes'
            """, params).fetchone()[0]

            cash_val = conn.execute(f"""
                SELECT COALESCE(SUM(CAST(Cash AS REAL)),0) FROM sales_data {where}
            """, params).fetchone()[0]

            return {
                'distribution': sums,
                'finance_vs_cash': {'Finance': float(finance_val), 'Cash': float(cash_val)}
            }
        finally:
            conn.close()

    # ─── Discount Analysis ────────────────────────────────────────────────
    def get_discount_analysis(self, filters=None):
        where, params = self._build_where(filters)
        conn = self._conn()
        try:
            disc_cols = ['Discount', 'Indirect Discount', 'Exchange', 'Buyback',
                        'Addition', 'Deduction', 'POINT REDUMPTION (DEDUCTION)']
            impact = []
            for col in disc_cols:
                row = conn.execute(f"""
                    SELECT COALESCE(SUM(CAST([{col}] AS REAL)),0) FROM sales_data {where}
                """, params).fetchone()
                impact.append({'Category': col, 'Value': row[0]})

            branch_disc = conn.execute(f"""
                SELECT Branch, SUM(CAST(Discount AS REAL)) as d
                FROM sales_data {where}
                GROUP BY Branch ORDER BY d DESC LIMIT 10
            """, params).fetchall()

            staff_disc = conn.execute(f"""
                SELECT Staff, SUM(CAST(Discount AS REAL)) as d
                FROM sales_data {where}
                GROUP BY Staff ORDER BY d DESC LIMIT 10
            """, params).fetchall()

            return {
                'impact': impact,
                'branch_discount': [{'Branch': r[0], 'Discount': r[1]} for r in branch_disc],
                'staff_discount': [{'Staff': r[0], 'Discount': r[1]} for r in staff_disc]
            }
        finally:
            conn.close()

    # ─── Staff Performance ────────────────────────────────────────────────
    def get_staff_performance(self, filters=None):
        where, params = self._build_where(filters)
        conn = self._conn()
        try:
            rows = conn.execute(f"""
                SELECT Staff,
                       SUM(CAST([Total Value] AS REAL)) as total,
                       COUNT(DISTINCT [Invoice Number]) as invoices
                FROM sales_data {where}
                GROUP BY Staff ORDER BY total DESC LIMIT 50
            """, params).fetchall()
            return [{'Staff': r[0], 'Total Value': r[1], 'Invoice Number': r[2],
                     'ATV': r[1]/r[2] if r[2] > 0 else 0} for r in rows]
        finally:
            conn.close()

    # ─── Branch Performance ───────────────────────────────────────────────
    def get_branch_performance(self, filters=None):
        where, params = self._build_where(filters)
        conn = self._conn()
        try:
            rows = conn.execute(f"""
                SELECT Branch,
                       SUM(CAST([Total Value] AS REAL)) as total,
                       COUNT(DISTINCT [Invoice Number]) as invoices,
                       COUNT(DISTINCT [Customer Mobile]) as customers
                FROM sales_data {where}
                GROUP BY Branch ORDER BY total DESC
            """, params).fetchall()
            return [{'Branch': r[0], 'Total Value': r[1], 'Invoice Number': r[2],
                     'Customer Count': r[3],
                     'ATV': r[1]/r[2] if r[2] > 0 else 0} for r in rows]
        finally:
            conn.close()

    # ─── Backward-compatible wrapper ──────────────────────────────────────
    def get_data(self, filters=None):
        """Kept for backward compatibility — returns None.
        Routes should call specific analytics methods with filters directly."""
        return filters
