from pydantic_settings import BaseSettings
import os

class Settings(BaseSettings):
    PGDATABASE: str = os.environ.get('PGDATABASE', 'defaultdb')
    PGUSER: str = os.environ.get('PGUSER', 'doadmin')
    PGPASSWORD: str = os.environ.get('PGPASSWORD', '')
    PGHOST: str = os.environ.get('PGHOST', 'localhost')
    PGPORT: str = os.environ.get('PGPORT', '25060')
    
    REDIS_URL: str = os.environ.get('REDIS_URL', 'redis://localhost:6379/0')
    
    # Materialized View Names
    TABLE: str = 'v_sales_data'
    MV_MONTHLY: str = 'mv_monthly_summary'
    MV_CUSTOMER: str = 'mv_customer_summary'

settings = Settings()
