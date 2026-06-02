import psycopg2
import time

conn = psycopg2.connect(
    host='db-postgresql-blr1-90397-do-user-3146770-0.e.db.ondigitalocean.com',
    port=25060, dbname='defaultdb', user='doadmin',
    password='YOUR_DB_PASSWORD', sslmode='require'
)
cur = conn.cursor()

start = time.time()
print("Creating materialized view mv_customer_propensity...")

create_mv_query = """
DROP MATERIALIZED VIEW IF EXISTS mv_customer_propensity CASCADE;

CREATE MATERIALIZED VIEW mv_customer_propensity AS
WITH customer_features AS (
    SELECT 
        mobile,
        visits AS frequency,
        total_spend AS monetary,
        (CURRENT_DATE - (CASE
            WHEN SUBSTRING(last_visit::text, 5, 1) = '-' THEN TO_DATE(SUBSTRING(last_visit::text, 1, 10), 'YYYY-MM-DD')
            WHEN SUBSTRING(last_visit::text, 3, 1) = '-' THEN TO_DATE(last_visit::text, 'DD-MM-YYYY')
            ELSE NULL
        END)) AS recency,
        (CURRENT_DATE - (CASE
            WHEN SUBSTRING(first_visit::text, 5, 1) = '-' THEN TO_DATE(SUBSTRING(first_visit::text, 1, 10), 'YYYY-MM-DD')
            WHEN SUBSTRING(first_visit::text, 3, 1) = '-' THEN TO_DATE(first_visit::text, 'DD-MM-YYYY')
            ELSE NULL
        END)) AS age
    FROM mv_customer_summary
    WHERE last_visit IS NOT NULL AND first_visit IS NOT NULL
      AND mobile ~ '^[0-9]{10}$'
),
normalized AS (
    SELECT 
        mobile,
        frequency,
        monetary,
        recency,
        age,
        GREATEST(0.0, LEAST(3.0, (COALESCE(recency::float, 365.0) / 365.0))) AS recency_norm,
        GREATEST(0.0, LEAST(3.0, (COALESCE(frequency::float, 1.0) / 5.0))) AS freq_norm,
        GREATEST(0.0, LEAST(3.0, (COALESCE(monetary::float, 5000.0) / 25000.0))) AS monetary_norm,
        GREATEST(0.0, LEAST(3.0, (COALESCE(age::float, 365.0) / 730.0))) AS age_norm
    FROM customer_features
),
logits AS (
    SELECT 
        mobile,
        frequency,
        monetary,
        recency,
        age,
        (
            (recency_norm * -3.5) + 
            (freq_norm * 4.5) + 
            (monetary_norm * 2.5) + 
            (age_norm * -0.5) + 
            -4.2 -- bias
        ) AS logit
    FROM normalized
),
probabilities AS (
    SELECT 
        mobile,
        frequency,
        monetary,
        recency,
        age,
        1.0 / (1.0 + EXP(-logit)) AS probability
    FROM logits
)
SELECT 
    mobile,
    frequency,
    monetary,
    recency,
    age,
    ROUND((probability * 100)::numeric, 2) AS probability
FROM probabilities;
"""

cur.execute(create_mv_query)
conn.commit()
print(f"Materialized view created in {time.time() - start:.3f} seconds.")

# Now create indexes
print("Creating indexes on mv_customer_propensity...")
idx_start = time.time()
cur.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_mv_cust_prop_mobile ON mv_customer_propensity(mobile);")
cur.execute("CREATE INDEX IF NOT EXISTS idx_mv_cust_prop_prob ON mv_customer_propensity(probability DESC);")
conn.commit()
print(f"Indexes created in {time.time() - idx_start:.3f} seconds.")

print(f"Total time elapsed: {time.time() - start:.3f} seconds.")
cur.close()
conn.close()
