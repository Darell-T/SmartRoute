# cache.py - Redis Caching Wrapper with in-memory fallback
import redis
import os
import time

_redis_url = os.getenv("REDIS_URL")
redis_client = redis.from_url(_redis_url) if _redis_url else None

# In-memory cache used when Redis is not configured: {key: (value, expires_at)}
_mem: dict = {}

if redis_client is None:
    print("[cache] REDIS_URL not set — using in-memory cache")


def cache_get(key):
    if redis_client is not None:
        return redis_client.get(key)
    entry = _mem.get(key)
    if entry and time.monotonic() < entry[1]:
        return entry[0]
    return None


def cache_set(key, value, ttl_seconds):
    if redis_client is not None:
        redis_client.setex(key, ttl_seconds, value)
    else:
        _mem[key] = (value, time.monotonic() + ttl_seconds)
