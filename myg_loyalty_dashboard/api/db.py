import asyncpg
from .config import settings
import logging

logger = logging.getLogger(__name__)

class Database:
    def __init__(self):
        self.pool = None

    async def connect(self):
        if not self.pool:
            try:
                self.pool = await asyncpg.create_pool(
                    user=settings.PGUSER,
                    password=settings.PGPASSWORD,
                    database=settings.PGDATABASE,
                    host=settings.PGHOST,
                    port=settings.PGPORT,
                    ssl='require',
                    min_size=5,
                    max_size=20,
                    statement_cache_size=0, # Required for PgBouncer transaction mode
                )
                logger.info("PostgreSQL pool created successfully")
            except Exception as e:
                logger.error(f"Error creating PostgreSQL pool: {e}")
                raise

    async def disconnect(self):
        if self.pool:
            await self.pool.close()
            logger.info("PostgreSQL pool closed")

    async def fetch(self, query, *args):
        async with self.pool.acquire() as conn:
            return await conn.fetch(query, *args)

    async def fetchrow(self, query, *args):
        async with self.pool.acquire() as conn:
            return await conn.fetchrow(query, *args)

db = Database()
