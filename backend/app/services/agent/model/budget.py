"""Cost + concurrency guardrails for the conversational agent.

These are the AUTHORITATIVE limits -- the frontend/proxy rate limit is
advisory only (see the plan doc's Security section). Checked inside
loop.run_agent_turn() before any model call is made, so a turn that trips a
limit never reaches the Anthropic API.
"""

from __future__ import annotations

import asyncio
import os
import time
from datetime import UTC, datetime

from redis.exceptions import RedisError

from app.services import cache

AGENT_MAX_CONCURRENT_STREAMS = int(os.getenv("AGENT_MAX_CONCURRENT_STREAMS", "4"))
AGENT_TURNS_PER_SESSION_PER_MIN = int(os.getenv("AGENT_TURNS_PER_SESSION_PER_MIN", "6"))
AGENT_DAILY_SPEND_LIMIT_USD = float(os.getenv("AGENT_DAILY_SPEND_LIMIT_USD", "5"))

# Rough $/token guardrail constants (Sonnet-tier list pricing at time of
# writing) -- a budget circuit breaker, not a billing reconciliation.
# Override via env if pricing changes.
AGENT_INPUT_COST_PER_TOKEN_USD = float(os.getenv("AGENT_INPUT_COST_PER_TOKEN_USD", "0.000003"))
AGENT_OUTPUT_COST_PER_TOKEN_USD = float(os.getenv("AGENT_OUTPUT_COST_PER_TOKEN_USD", "0.000015"))

_semaphore: asyncio.Semaphore | None = None
_semaphore_limit: int | None = None


def agent_enabled() -> bool:
    return os.getenv("AGENT_ENABLED", "1").strip() != "0"


def concurrency_semaphore() -> asyncio.Semaphore:
    """Process-global stream slot semaphore (Render runs a single instance,
    per the plan doc, so a process-local semaphore is the whole guard).

    Lazily created against the current AGENT_MAX_CONCURRENT_STREAMS so tests
    that reload this module with a patched env get a semaphore sized to
    match, instead of one frozen at first import.
    """
    global _semaphore, _semaphore_limit
    limit = AGENT_MAX_CONCURRENT_STREAMS
    if _semaphore is None or _semaphore_limit != limit:
        _semaphore = asyncio.Semaphore(limit)
        _semaphore_limit = limit
    return _semaphore


def _current_minute_bucket() -> str:
    return str(int(time.time()) // 60)


def _incr_counter(key: str, ttl_seconds: int) -> int:
    """Atomic increment when Redis is available; best-effort read-modify-
    write against the in-memory fallback otherwise (single-process dev/test
    only -- see utils/cache.py)."""
    if cache.redis_client is not None:
        pipe = cache.redis_client.pipeline()
        pipe.incr(key)
        pipe.expire(key, ttl_seconds)
        result = pipe.execute()
        return int(result[0])
    current = cache.cache_get(key)
    value = int(current) + 1 if current else 1
    cache.cache_set(key, str(value), ttl_seconds)
    return value


def check_session_rate_limit(session_id: str) -> bool:
    """True if this turn is allowed under the per-session per-minute cap."""
    key = f"agent:rl:{session_id}:{_current_minute_bucket()}"
    count = _incr_counter(key, ttl_seconds=90)
    return count <= AGENT_TURNS_PER_SESSION_PER_MIN


def _today_key() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%d")


def _spend_cache_key() -> str:
    return f"agent:spend:{_today_key()}"


def daily_spend_usd() -> float:
    raw = cache.cache_get(_spend_cache_key())
    if raw is None:
        return 0.0
    try:
        value = raw.decode("utf-8") if isinstance(raw, (bytes, bytearray)) else raw
        return float(value)
    except (ValueError, TypeError):
        return 0.0


def daily_spend_exceeded() -> bool:
    return daily_spend_usd() >= AGENT_DAILY_SPEND_LIMIT_USD


def record_usage_cost(input_tokens: int, output_tokens: int) -> float:
    """Add this turn's estimated cost to today's spend counter. Returns the
    cost added (not the running total). Fail-open: never raises."""
    cost = (
        max(0, int(input_tokens)) * AGENT_INPUT_COST_PER_TOKEN_USD
        + max(0, int(output_tokens)) * AGENT_OUTPUT_COST_PER_TOKEN_USD
    )
    if cost <= 0:
        return 0.0
    key = _spend_cache_key()
    now = datetime.now(UTC)
    seconds_left_today = 86400 - (now.hour * 3600 + now.minute * 60 + now.second)
    ttl = max(60, seconds_left_today)
    try:
        if cache.redis_client is not None:
            pipe = cache.redis_client.pipeline()
            pipe.incrbyfloat(key, cost)
            pipe.expire(key, ttl)
            pipe.execute()
        else:
            cache.cache_set(key, str(daily_spend_usd() + cost), ttl)
    except (RedisError, OSError, TypeError, ValueError) as exc:
        print(f"[agent-budget] spend counter update failed (continuing): {exc!r}")
    return cost
