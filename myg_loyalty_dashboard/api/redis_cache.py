import redis.asyncio as redis
from .config import settings
import json
import logging

logger = logging.getLogger(__name__)

class RedisCache:
    def __init__(self):
        self.client = None

    async def connect(self):
        if not self.client:
            try:
                self.client = redis.from_url(settings.REDIS_URL, decode_responses=True)
                await self.client.ping()
                logger.info("Connected to Redis successfully")
            except Exception as e:
                logger.error(f"Error connecting to Redis: {e}")
                # We can fallback to no-op if redis is down, or raise
                self.client = None

    async def get(self, key):
        if not self.client: return None
        try:
            data = await self.client.get(key)
            return json.loads(data) if data else None
        except Exception:
            return None

    async def set(self, key, value, expire=3600):
        if not self.client: return
        try:
            await self.client.set(key, json.dumps(value), ex=expire)
        except Exception:
            pass

cache = RedisCache()

from functools import wraps
from fastapi import Request
import hashlib

def cached(expire: int = 300):
    def decorator(func):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            # Extract request if present to build query string cache key
            request = kwargs.get('request')
            if request and isinstance(request, Request):
                # Base key is the path
                key_base = request.url.path
                # Add query params
                if request.url.query:
                    key_base += f"?{request.url.query}"
                # Add path parameters if any
                key = f"api_cache:{hashlib.md5(key_base.encode()).hexdigest()}"
            else:
                # Fallback to function name and args if no request object
                str_args = str(args) + str(kwargs)
                key = f"api_cache:{func.__name__}:{hashlib.md5(str_args.encode()).hexdigest()}"

            # Try to get from cache
            cached_data = await cache.get(key)
            if cached_data is not None:
                return cached_data

            # Execute function
            result = await func(*args, **kwargs)

            # Store in cache
            if result is not None:
                await cache.set(key, result, expire)

            return result
        return wrapper
    return decorator
