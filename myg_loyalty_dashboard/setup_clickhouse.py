"""
Setup ClickHouse Cloud with FULL sales_data schema (all 42 columns)
"""
import os, django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'myg_loyalty_dashboard.settings')
django.setup()

from analytics.clickhouse_service import get_ch_client

client = get_ch_client()
if not client:
    print("ERROR: Cannot connect to ClickHouse!")
    exit(1)

print(f"Connected to ClickHouse {client.server_version}")
print("\nDropping and recreating sales_data with all 42 columns...")

client.command("DROP TABLE IF EXISTS sales_data")

client.command("""
CREATE TABLE sales_data (
    slno                         String,
    sale_date_text               String,
    sale_time                    String,
    invoice_number               String,
    enq_job_no                   String,
    rbm                          LowCardinality(String),
    bdm                          LowCardinality(String),
    branch                       LowCardinality(String),
    staff_code                   String,
    staff                        String,
    customer_name                String,
    customer_mobile              String,
    financier                    String,
    finance                      String,
    delivery_order_no            String,
    cash                         String,
    debit_card                   String,
    credit_card                  String,
    benow                        String,
    advance_receipt              String,
    bharath_qr                   String,
    paytm_qr                     String,
    pine_labs_qr                 String,
    upi_cashback                 String,
    card_reward                  String,
    card_cashback                String,
    gift_voucher                 String,
    approved_credit              String,
    emi                          String,
    customer_type                LowCardinality(String),
    total_value                  Float64,
    exchange                     String,
    discount                     String,
    indirect_discount            String,
    buyback                      String,
    addition                     String,
    deduction                    String,
    point_redemption             String,
    myg_online_coupon            String,
    source_file                  String,
    parsed_date                  Date,
    uid                          Int64
) ENGINE = MergeTree()
ORDER BY (branch, parsed_date, customer_mobile)
SETTINGS index_granularity = 8192
""")

print("  Table created with all 42 columns!")

count = client.query("SELECT count() FROM sales_data").result_rows[0][0]
print(f"\nRows: {count:,}")
print("\nSetup complete! Run sync_to_clickhouse.py to load data.")
