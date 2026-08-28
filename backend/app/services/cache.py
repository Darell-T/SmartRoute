import asyncio
import os
import threading
import time

import redis

REDIS_CONNECT_TIMEOUT_S = float(os.getenv("REDIS_CONNECT_TIMEOUT_S", "0.5"))
REDIS_READ_TIMEOUT_S = float(os.getenv("REDIS_READ_TIMEOUT_S", "1.0"))

_redis_url = os.getenv("REDIS_URL")
redis_client = (
    redis.from_url(
        _redis_url,
        socket_connect_timeout=REDIS_CONNECT_TIMEOUT_S,
        socket_timeout=REDIS_READ_TIMEOUT_S,
    )
    if _redis_url
    else None
)

_mem: dict = {}
_mem_lock = threading.Lock()
_FAIL_OPEN_LOG_COOLDOWN_SECONDS = 60
_last_fail_open_log = 0.0

if redis_client is None:
    print("[cache] REDIS_URL not set — using in-memory cache")


_DELETE_IF_VALUE_SCRIPT = """
if redis.call('get', KEYS[1]) == ARGV[1] then
    return redis.call('del', KEYS[1])
end
return 0
"""

_SET_PRESERVE_TTL_SCRIPT = """
local ttl = redis.call('pttl', KEYS[1])
if ttl == -2 then
    return 0
end
if ttl == -1 then
    redis.call('set', KEYS[1], ARGV[1])
else
    redis.call('set', KEYS[1], ARGV[1], 'px', ttl)
end
return 1
"""


def _as_bytes(value: object) -> bytes:
    if isinstance(value, bytes):
        return value
    if isinstance(value, (bytearray, memoryview)):
        return bytes(value)
    return str(value).encode("utf-8")


def _same_value(left: object, right: object) -> bool:
    if isinstance(left, (bytes, bytearray, memoryview)) or isinstance(
        right, (bytes, bytearray, memoryview)
    ):
        return _as_bytes(left) == _as_bytes(right)
    return left == right


def _memory_get(key):
    with _mem_lock:
        entry = _mem.get(key)
        if entry and time.monotonic() < entry[1]:
            return entry[0]
        if entry:
            _mem.pop(key, None)
    return None


def _memory_set(key, value, ttl_seconds):
    with _mem_lock:
        _mem[key] = (value, time.monotonic() + ttl_seconds)


def _memory_set_preserve_ttl(key, value) -> bool:
    with _mem_lock:
        entry = _mem.get(key)
        if entry is None:
            return False
        if time.monotonic() >= entry[1]:
            _mem.pop(key, None)
            return False
        _mem[key] = (value, entry[1])
        return True


def _log_fail_open(operation: str, exc: Exception) -> None:
    global _last_fail_open_log
    now = time.monotonic()
    if now - _last_fail_open_log < _FAIL_OPEN_LOG_COOLDOWN_SECONDS:
        return
    _last_fail_open_log = now
    print(
        f"[cache] Redis {operation} failed; optional provider cache is using "
        f"process memory ({type(exc).__name__})"
    )


def cache_get(key, *, fail_open: bool = False):
    if redis_client is not None:
        try:
            value = redis_client.get(key)
            if value is not None or not fail_open:
                return value
            return _memory_get(key)
        except redis.exceptions.RedisError as exc:
            if not fail_open:
                raise
            _log_fail_open("read", exc)
    return _memory_get(key)


def cache_get_many(keys, *, fail_open: bool = False) -> dict:
    """Read many keys in one round trip; missing keys map to None."""
    unique_keys = list(dict.fromkeys(keys))
    if not unique_keys:
        return {}
    if redis_client is not None:
        try:
            values = redis_client.mget(unique_keys)
            result = dict(zip(unique_keys, values, strict=True))
            if fail_open:
                result = {
                    key: value if value is not None else _memory_get(key)
                    for key, value in result.items()
                }
        except redis.exceptions.RedisError as exc:
            if not fail_open:
                raise
            _log_fail_open("read", exc)
        else:
            return result
    return {key: _memory_get(key) for key in unique_keys}


async def cache_get_many_async(keys, *, fail_open: bool = False, timeout_s: float | None = None):
    """Request-path batch read: bounded and off the async event loop."""
    timeout = timeout_s if timeout_s is not None else REDIS_READ_TIMEOUT_S
    return await asyncio.wait_for(
        asyncio.to_thread(cache_get_many, keys, fail_open=fail_open),
        timeout=timeout,
    )


def cache_set(key, value, ttl_seconds, *, fail_open: bool = False):
    if redis_client is not None:
        try:
            redis_client.setex(key, ttl_seconds, value)
        except redis.exceptions.RedisError as exc:
            if not fail_open:
                raise
            _log_fail_open("write", exc)
        else:
            if not fail_open:
                return
    _memory_set(key, value, ttl_seconds)


def cache_set_preserve_ttl(key, value) -> bool:
    """Replace an existing value without extending its current expiry.

    Session cleanup uses this for failed or disconnected turns: diagnostic
    state may be retained, but a failed response cannot keep an idle session
    alive. Missing or already-expired keys are left absent.
    """
    if redis_client is not None:
        return bool(
            redis_client.eval(
                _SET_PRESERVE_TTL_SCRIPT,
                1,
                key,
                value,
            )
        )
    return _memory_set_preserve_ttl(key, value)


def cache_delete(key, *, fail_open: bool = False) -> bool:
    """Delete one cache key and report whether it existed."""
    deleted = False
    if redis_client is not None:
        try:
            deleted = bool(redis_client.delete(key))
        except redis.exceptions.RedisError as exc:
            if not fail_open:
                raise
            _log_fail_open("delete", exc)
        else:
            if not fail_open:
                return deleted
    with _mem_lock:
        deleted = _mem.pop(key, None) is not None or deleted
    return deleted


def cache_add(key, value, ttl_seconds) -> bool:
    """Atomically store value only when key is absent; expired counts as absent."""
    if redis_client is not None:
        return bool(redis_client.set(key, value, nx=True, ex=int(ttl_seconds)))
    with _mem_lock:
        now = time.monotonic()
        entry = _mem.get(key)
        if entry is not None and now < entry[1]:
            return False
        _mem[key] = (value, now + ttl_seconds)
        return True


def cache_delete_if_value(key, expected_value) -> bool:
    """Atomically delete key only when its current value matches expected value."""
    if redis_client is not None:
        return bool(
            redis_client.eval(
                _DELETE_IF_VALUE_SCRIPT,
                1,
                key,
                _as_bytes(expected_value),
            )
        )
    with _mem_lock:
        entry = _mem.get(key)
        if entry is None or time.monotonic() >= entry[1]:
            return False
        if not _same_value(entry[0], expected_value):
            return False
        del _mem[key]
        return True
