import clickhouse_connect
import csv
import os

print("Connecting to ClickHouse...")
client = clickhouse_connect.get_client(
    host="ytoyqewr56.ap-south-1.aws.clickhouse.cloud",
    port=8443,
    username="default",
    password="QyB2XKWS44Qt~",
    database="default",
    secure=True
)

query = """
SELECT * 
FROM sales_data 
WHERE parsed_date >= '2026-07-01' AND parsed_date <= '2026-07-31'
"""

print("Executing query... (this might take a minute depending on the size)")
result = client.query(query)

columns = result.column_names
rows = result.result_rows

desktop_path = os.path.join(os.path.expanduser("~"), "Desktop")
output_file = os.path.join(desktop_path, "July_2026_Sales_Data.csv")

print(f"Writing {len(rows)} rows to {output_file}...")

with open(output_file, 'w', newline='', encoding='utf-8') as f:
    writer = csv.writer(f)
    writer.writerow(columns)
    for row in rows:
        writer.writerow(row)

print("Download complete!")
