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


def cache_delete(key):
    if redis_client is not None:
        redis_client.delete(key)
    else:
        _mem.pop(key, None)


def cache_trip_updates(key, feed_data, ttl=30):
    cache_set(key, feed_data, ttl)


def cache_incidents(key, incident_data, ttl=3600):
    cache_set(key, incident_data, ttl)


def cache_service_alerts(feed_data, ttl=60):
    cache_set(feed_data, ttl)
