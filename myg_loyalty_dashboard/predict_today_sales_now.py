import os, sys, django
import datetime
from decimal import Decimal

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'myg_loyalty_dashboard.settings')
django.setup()

from django.db import connection

today_sales_2pm = 146606096

def analyze():
    print("=" * 60)
    print("  myG Sales Predictor - Based on past 7 days average at 17:35  ")
    print("=" * 60)
    
    with connection.cursor() as cur:
        # Get past 7 days of sales data
        query = """
        SELECT parsed_date, "Time", "Total Value"
        FROM sales_data
        WHERE parsed_date >= (CURRENT_DATE - INTERVAL '7 days') 
          AND parsed_date < CURRENT_DATE
          AND "Total Value" IS NOT NULL
        """
        try:
            cur.execute(query)
            rows = cur.fetchall()
        except Exception as e:
            rows = []
            
        if not rows:
            # Let's try to get the most recent 7 days available in the database.
            query = """
            SELECT parsed_date, "Time", "Total Value"
            FROM sales_data
            WHERE parsed_date IN (
                SELECT parsed_date FROM (
                    SELECT DISTINCT parsed_date 
                    FROM sales_data 
                    WHERE parsed_date IS NOT NULL 
                    ORDER BY parsed_date DESC LIMIT 7
                ) AS subq
            )
            AND "Total Value" IS NOT NULL
            """
            cur.execute(query)
            rows = cur.fetchall()
            
    # Process the data
    daily_totals = {}
    daily_by_1735 = {}
    
    for row in rows:
        pdate = row[0]
        time_str = row[1]
        val = float(row[2] or 0)
        
        if pdate not in daily_totals:
            daily_totals[pdate] = 0.0
            daily_by_1735[pdate] = 0.0
            
        daily_totals[pdate] += val
        
        try:
            if time_str:
                time_str = str(time_str).strip().lower()
                # Parse format like '07:48:26 pm' or '11:46:56 am'
                parts = time_str.split(':')
                if len(parts) >= 2:
                    h = int(parts[0])
                    m = int(parts[1])
                    
                    is_pm = 'pm' in time_str
                    is_am = 'am' in time_str
                    
                    if is_pm and h < 12:
                        h += 12
                    elif is_am and h == 12:
                        h = 0
                        
                    # 17:35 threshold
                    if h < 17 or (h == 17 and m <= 35):
                        daily_by_1735[pdate] += val
        except:
            pass

    print(f"Data found for {len(daily_totals)} days in the recent history.")
    
    percentages = []
    print("\n--- Daily Breakdown ---")
    for d in sorted(daily_totals.keys()):
        total = daily_totals[d]
        by_1735 = daily_by_1735[d]
        
        if total > 0:
            pct = (by_1735 / total) * 100
            percentages.append(pct)
            print(f"{d}: Total = {total:,.0f} | By 17:35 = {by_1735:,.0f} | % = {pct:.1f}%")
            
    if percentages:
        avg_pct = sum(percentages) / len(percentages)
        print(f"\nAverage completion by 17:35 over these days: {avg_pct:.1f}%")
        
        predicted_total = today_sales_2pm / (avg_pct / 100)
        print("\n" + "=" * 60)
        print(f"  TODAY'S CURRENT SALE (17:35) : Rs. {today_sales_2pm:,.0f}")
        print(f"  PREDICTED FINAL SALE TODAY   : Rs. {predicted_total:,.0f}")
        print("=" * 60)
    else:
        print("Could not compute percentages based on the data format.")

if __name__ == "__main__":
    analyze()
