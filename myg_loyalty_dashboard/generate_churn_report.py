import sys
sys.path.append(r'c:\Users\jasil_myg\Desktop\myG Loyalty Main Dashboard\myg_loyalty_dashboard')
import os
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'myg_loyalty_dashboard.settings')
import django
django.setup()
import clickhouse_connect
import pandas as pd

ch = clickhouse_connect.get_client(host='pdhsuv47ec.ap-south-1.aws.clickhouse.cloud', port=8443, username='default', password='ZFlujj9SA_Iei', secure=True)

sql = '''
WITH customer_last_visit AS (
    SELECT 
        customer_mobile,
        MAX(date) as last_visit_date,
        argMax(branch, date) as last_branch
    FROM azure_invoice_report
    WHERE customer_mobile != '' AND date != '1970-01-01'
    GROUP BY customer_mobile
)
SELECT 
    if(isNull(b.branch_name) OR b.branch_name='', c.last_branch, b.branch_name) as branch_name,
    COUNT(*) as churned_customers
FROM customer_last_visit c
LEFT JOIN branch_master b ON c.last_branch = b.code
WHERE c.last_visit_date < today() - INTERVAL 3 YEAR
GROUP BY c.last_branch, branch_name
ORDER BY churned_customers DESC
'''

result = ch.query(sql).result_rows
df = pd.DataFrame(result, columns=['Branch Name', 'Customers Not Returned (3+ Years)'])

md_lines = []
md_lines.append('# Customers Not Returned in Last 3 Years (Branch-wise)')
md_lines.append('\nThis table shows the count of unique customers whose **last purchase** across the entire brand was over 3 years ago, grouped by the branch where they made that final purchase.\n')
md_lines.append('| Branch Name | Customers Not Returned (3+ Years) |')
md_lines.append('|---|---|')
for idx, row in df.iterrows():
    md_lines.append(f'| {row["Branch Name"]} | {row["Customers Not Returned (3+ Years)"]:,} |')

with open(r'C:\Users\jasil_myg\.gemini\antigravity-ide\brain\ba75da7e-fd68-43fd-a065-5b15bcd6d456\churned_customers_3_years.md', 'w') as f:
    f.write('\n'.join(md_lines))
    
df.to_csv(r'C:\Users\jasil_myg\.gemini\antigravity-ide\brain\ba75da7e-fd68-43fd-a065-5b15bcd6d456\churned_customers_3_years.csv', index=False)
