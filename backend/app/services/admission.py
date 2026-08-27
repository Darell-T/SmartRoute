"""Shared Redis admission with orphan-safe, expiring lease membership."""
from __future__ import annotations

import asyncio
import os
import re
import secrets
import time
from dataclasses import dataclass

import redis

from app import runtime

WINDOW_S, LEASE_TTL_S = 60, 120
# WebSockets release their leases when they close, so expiry is only the
# orphan-recovery boundary. A longer expiry avoids refreshing two healthy,
# long-lived rider streams every 40 seconds while still reclaiming abandoned
# connections within a bounded period.
WEBSOCKET_LEASE_TTL_S = 600
REQUESTS_PER_PRINCIPAL, REQUESTS_GLOBAL = 20, 240
WEBSOCKET_CONNECTIONS_PER_PRINCIPAL, WEBSOCKET_CONNECTIONS_GLOBAL = 30, 600
REDIS_CONNECT_TIMEOUT_S = float(os.getenv("REDIS_CONNECT_TIMEOUT_S", "0.5"))
REDIS_READ_TIMEOUT_S = float(os.getenv("REDIS_READ_TIMEOUT_S", "1.0"))
CONCURRENT_PER_PRINCIPAL, CONCURRENT_GLOBAL = 4, 48
WEBSOCKET_CONCURRENT_PER_PRINCIPAL, WEBSOCKET_CONCURRENT_GLOBAL = 6, 200
_PRINCIPAL_PATTERN = re.compile(r"^v1\.[A-Za-z0-9_-]{16,64}$")


@dataclass(frozen=True)
class AdmissionDenied(Exception):
    status_code: int
    code: str
    retry_after_s: int


@dataclass(frozen=True)
class AdmissionLease:
    principal: str
    kind: str
    token: str
    ttl_s: int = LEASE_TTL_S


_redis_client: redis.Redis | None = None
_memory_requests: dict[str, tuple[int, float]] = {}
_memory_leases: dict[str, tuple[str, str, float]] = {}
_memory_nonces: dict[str, float] = {}


def principal_from_request(raw: str | None) -> str:
    if not raw or not _PRINCIPAL_PATTERN.fullmatch(raw):
        raise AdmissionDenied(403, "invalid_principal", 1)
    return raw


def _client() -> redis.Redis | None:
    global _redis_client
    if _redis_client is None and os.getenv("REDIS_URL"):
        _redis_client = redis.from_url(
            os.environ["REDIS_URL"],
            decode_responses=True,
            socket_connect_timeout=REDIS_CONNECT_TIMEOUT_S,
            socket_timeout=REDIS_READ_TIMEOUT_S,
        )
    return _redis_client


def _key(scope: str, principal: str | None = None) -> str:
    return f"smartroute:admission:{scope}:{principal}" if principal else f"smartroute:admission:{scope}:global"


def _prune_memory(now: float) -> None:
    for key, (_count, expires) in list(_memory_requests.items()):
        if now >= expires:
            _memory_requests.pop(key, None)
    for token, (_principal, _pool, expires) in list(_memory_leases.items()):
        if now >= expires:
            _memory_leases.pop(token, None)
    for nonce, expires in list(_memory_nonces.items()):
        if now >= expires:
            _memory_nonces.pop(nonce, None)


def _lease_ttl_s(kind: str) -> int:
    return WEBSOCKET_LEASE_TTL_S if kind == "ws" else LEASE_TTL_S


def _admission_pool(kind: str) -> str:
    return "websocket" if kind == "ws" else "request"


def _limits(kind: str) -> tuple[int, int, int, int]:
    if kind == "ws":
        return (
            WEBSOCKET_CONNECTIONS_PER_PRINCIPAL,
            WEBSOCKET_CONNECTIONS_GLOBAL,
            WEBSOCKET_CONCURRENT_PER_PRINCIPAL,
            WEBSOCKET_CONCURRENT_GLOBAL,
        )
    return (
        REQUESTS_PER_PRINCIPAL,
        REQUESTS_GLOBAL,
        CONCURRENT_PER_PRINCIPAL,
        CONCURRENT_GLOBAL,
    )


def _memory_acquire(principal: str, kind: str, token: str) -> AdmissionLease:
    now = time.monotonic()
    _prune_memory(now)
    pool = _admission_pool(kind)
    principal_rate, global_rate, principal_concurrency, global_concurrency = _limits(
        kind
    )
    request_keys = (_key(f"rate:{pool}", principal), _key(f"rate:{pool}"))
    for key, limit in zip(request_keys, (principal_rate, global_rate), strict=True):
        count, expires = _memory_requests.get(key, (0, now + WINDOW_S))
        if count >= limit:
            raise AdmissionDenied(429, "rate_limited", max(1, int(expires - now)))
    principal_active = 0
    global_active = 0
    for owner, lease_pool, _expires in _memory_leases.values():
        if lease_pool != pool:
            continue
        global_active += 1
        if owner == principal:
            principal_active += 1
    if principal_active >= principal_concurrency or global_active >= global_concurrency:
        raise AdmissionDenied(503, "busy", 1)
    for key in request_keys:
        count, expires = _memory_requests.get(key, (0, now + WINDOW_S))
        _memory_requests[key] = (count + 1, expires)
    ttl_s = _lease_ttl_s(kind)
    _memory_leases[token] = (principal, pool, now + ttl_s)
    return AdmissionLease(principal, kind, token, ttl_s)


_ACQUIRE = """
local rp, rg, principal_leases, global_leases = KEYS[1], KEYS[2], KEYS[3], KEYS[4]
local principal_limit, global_limit, principal_concurrency, global_concurrency, window, now, expiry, token = unpack(ARGV)
redis.call('ZREMRANGEBYSCORE', principal_leases, '-inf', now)
redis.call('ZREMRANGEBYSCORE', global_leases, '-inf', now)
if tonumber(redis.call('GET', rp) or '0') >= tonumber(principal_limit) then return {1, redis.call('TTL', rp)} end
if tonumber(redis.call('GET', rg) or '0') >= tonumber(global_limit) then return {2, redis.call('TTL', rg)} end
if redis.call('ZCARD', principal_leases) >= tonumber(principal_concurrency) then return {3, 1} end
if redis.call('ZCARD', global_leases) >= tonumber(global_concurrency) then return {4, 1} end
for _, key in ipairs({rp, rg}) do local n=redis.call('INCR',key); if n==1 then redis.call('EXPIRE',key,window) end end
redis.call('ZADD', principal_leases, expiry, token); redis.call('ZADD', global_leases, expiry, token)
return {0, 0}
"""
_RELEASE = """redis.call('ZREM', KEYS[1], ARGV[1]); redis.call('ZREM', KEYS[2], ARGV[1]); return 1"""
_REFRESH = """
local principal_leases, global_leases = KEYS[1], KEYS[2]
local now, expiry, token = unpack(ARGV)
redis.call('ZREMRANGEBYSCORE', principal_leases, '-inf', now); redis.call('ZREMRANGEBYSCORE', global_leases, '-inf', now)
if redis.call('ZSCORE', principal_leases, token) == false or redis.call('ZSCORE', global_leases, token) == false then return 0 end
redis.call('ZADD', principal_leases, expiry, token); redis.call('ZADD', global_leases, expiry, token); return 1
"""


async def acquire(principal: str, kind: str) -> AdmissionLease:
    token = secrets.token_urlsafe(24)
    client = _client()
    if client is None:
        if runtime.allows_mock_modes():
            return _memory_acquire(principal, kind, token)
        raise AdmissionDenied(503, "admission_unavailable", 1)
    now = int(time.time())
    ttl_s = _lease_ttl_s(kind)
    pool = _admission_pool(kind)
    principal_rate, global_rate, principal_concurrency, global_concurrency = _limits(
        kind
    )
    try:
        result = await asyncio.to_thread(
            client.eval,
            _ACQUIRE,
            4,
            _key(f"rate:{pool}", principal),
            _key(f"rate:{pool}"),
            _key(f"leases:{pool}", principal),
            _key(f"leases:{pool}"),
            principal_rate,
            global_rate,
            principal_concurrency,
            global_concurrency,
            WINDOW_S,
            now,
            now + ttl_s,
            token,
        )
    except Exception:
        raise AdmissionDenied(503, "admission_unavailable", 1) from None
    code, retry = int(result[0]), int(result[1])
    if code:
        raise AdmissionDenied(429 if code < 3 else 503, "rate_limited" if code < 3 else "busy", max(1, retry))
    return AdmissionLease(principal, kind, token, ttl_s)


async def release(lease: AdmissionLease | None) -> None:
    if lease is None:
        return
    client = _client()
    if client is None:
        _memory_leases.pop(lease.token, None)
        return
    pool = _admission_pool(lease.kind)
    try:
        await asyncio.to_thread(
            client.eval,
            _RELEASE,
            2,
            _key(f"leases:{pool}", lease.principal),
            _key(f"leases:{pool}"),
            lease.token,
        )
    except Exception:
        return


async def refresh(lease: AdmissionLease) -> bool:
    client = _client()
    if client is None:
        record = _memory_leases.get(lease.token)
        if (
            record is None
            or record[0] != lease.principal
            or record[1] != _admission_pool(lease.kind)
            or record[2] <= time.monotonic()
        ):
            return False
        _memory_leases[lease.token] = (
            lease.principal,
            record[1],
            time.monotonic() + lease.ttl_s,
        )
        return True
    now = int(time.time())
    pool = _admission_pool(lease.kind)
    try:
        return bool(
            await asyncio.to_thread(
                client.eval,
                _REFRESH,
                2,
                _key(f"leases:{pool}", lease.principal),
                _key(f"leases:{pool}"),
                now,
                now + lease.ttl_s,
                lease.token,
            )
        )
    except Exception:
        return False


async def consume_nonce(nonce: str, ttl_s: int) -> str:
    key = f"smartroute:ticket-nonce:{nonce}"
    client = _client()
    if client is None:
        if not runtime.allows_mock_modes():
            return "unavailable"
        now = time.monotonic()
        _prune_memory(now)
        if key in _memory_nonces:
            return "replay"
        _memory_nonces[key] = now + max(1, ttl_s)
        return "consumed"
    try:
        return "consumed" if await asyncio.to_thread(client.set, key, "1", nx=True, ex=max(1, ttl_s)) else "replay"
    except Exception:
        return "unavailable"
