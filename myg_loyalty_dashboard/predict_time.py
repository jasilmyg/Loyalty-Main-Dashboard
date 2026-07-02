import os, sys, django
import pandas as pd
import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'myg_loyalty_dashboard.settings')
django.setup()

from django.db import connection

def predict_time():
    target_sales = 245900000
    current_sales = 146606096  # at 17:35
    
    with connection.cursor() as cur:
        # Get the last 7 days (June 20 to June 26)
        cur.execute("""
            WITH daily_totals AS (
                SELECT parsed_date, SUM("Total Value") as day_total
                FROM sales_data
                WHERE parsed_date >= '2026-06-20' AND parsed_date <= '2026-06-26'
                GROUP BY parsed_date
            ),
            sales_at_1735 AS (
                SELECT parsed_date, SUM("Total Value") as sales_so_far
                FROM sales_data
                WHERE parsed_date >= '2026-06-20' AND parsed_date <= '2026-06-26'
                  AND TO_TIMESTAMP("Time", 'HH:MI:SS AM')::time <= '17:35:00'::time
                GROUP BY parsed_date
            )
            SELECT d.parsed_date, d.day_total, s.sales_so_far, (s.sales_so_far / d.day_total) as pct_at_1735
            FROM daily_totals d
            JOIN sales_at_1735 s ON d.parsed_date = s.parsed_date
        """)
        
        results = cur.fetchall()
        avg_pct_at_1735 = sum(r[3] for r in results) / len(results)
        
        predicted_today_total = current_sales / avg_pct_at_1735
        
        target_pct = target_sales / predicted_today_total
        print(f"Predicted Today Total: {predicted_today_total}")
        print(f"Target Pct needed: {target_pct * 100:.2f}%")
        
        # Now find at what time historically we hit `target_pct`
        cur.execute("""
            WITH daily_totals AS (
                SELECT parsed_date, SUM("Total Value") as day_total
                FROM sales_data
                WHERE parsed_date >= '2026-06-20' AND parsed_date <= '2026-06-26'
                GROUP BY parsed_date
            ),
            cumulative_sales AS (
                SELECT 
                    parsed_date, 
                    TO_TIMESTAMP("Time", 'HH:MI:SS AM')::time as sale_time,
                    SUM("Total Value") OVER (PARTITION BY parsed_date ORDER BY TO_TIMESTAMP("Time", 'HH:MI:SS AM')::time) as running_total
                FROM sales_data
                WHERE parsed_date >= '2026-06-20' AND parsed_date <= '2026-06-26'
            )
            SELECT 
                c.parsed_date,
                MIN(c.sale_time) as time_hit_target
            FROM cumulative_sales c
            JOIN daily_totals d ON c.parsed_date = d.parsed_date
            WHERE c.running_total >= d.day_total * %s
            GROUP BY c.parsed_date
        """, [float(target_pct)])
        
        times_hit = cur.fetchall()
        print("Historical times hitting this percentage:")
        total_seconds = 0
        for row in times_hit:
            print(f"{row[0]}: {row[1]}")
            # convert time to seconds since midnight
            t = row[1]
            total_seconds += t.hour * 3600 + t.minute * 60 + t.second
            
        avg_seconds = total_seconds / len(times_hit)
        avg_time = datetime.timedelta(seconds=int(avg_seconds))
        print(f"Average time to hit target: {avg_time}")

if __name__ == "__main__":
    predict_time()
