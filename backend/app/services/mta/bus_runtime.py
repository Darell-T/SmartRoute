"""Lifecycle-owned transport and bounded sharing for BusTime requests."""

from __future__ import annotations

import asyncio
import os
import time
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from typing import Any

import httpx


def _bounded_timeout(name: str, default: float, maximum: float) -> float:
    try:
        return min(maximum, max(0.1, float(os.getenv(name, str(default)))))
    except ValueError:
        return default


BUS_REQUEST_TIMEOUT_S = _bounded_timeout("MTA_BUS_REQUEST_TIMEOUT_S", 3.0, 8.0)
NEARBY_STOPS_CACHE_MAX_ENTRIES = 256
STOP_MONITORING_CACHE_MAX_ENTRIES = 512
NEARBY_ARRIVALS_CACHE_MAX_ENTRIES = 128
_client = None
_client_lock = asyncio.Lock()
_inflight_lock = asyncio.Lock()
_inflight: dict[str, asyncio.Task[Any]] = {}


class BoundedCache(OrderedDict[str, tuple[float, float, Any]]):
    """Small process-local LRU cache with an explicit stale-fallback window."""

    def __init__(self, max_entries: int) -> None:
        super().__init__()
        self.max_entries = max_entries


nearby_stops_cache = BoundedCache(NEARBY_STOPS_CACHE_MAX_ENTRIES)
stop_monitoring_cache = BoundedCache(STOP_MONITORING_CACHE_MAX_ENTRIES)
nearby_arrivals_cache = BoundedCache(NEARBY_ARRIVALS_CACHE_MAX_ENTRIES)


async def start_bus_client() -> None:
    """Create the one process-owned HTTP client before live feed traffic."""
    global _client
    async with _client_lock:
        if _client is None or _client.is_closed:
            _client = httpx.AsyncClient(timeout=BUS_REQUEST_TIMEOUT_S)  # noqa: TID251


async def bus_client():
    await start_bus_client()
    assert _client is not None
    return _client


async def close_bus_client() -> None:
    """Stop shared BusTime work before closing the lifecycle-owned client."""
    global _client
    async with _inflight_lock:
        tasks = list(_inflight.values())
        _inflight.clear()
    for task in tasks:
        task.cancel()
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)
    async with _client_lock:
        active, _client = _client, None
    nearby_stops_cache.clear()
    stop_monitoring_cache.clear()
    nearby_arrivals_cache.clear()
    if active is not None:
        await active.aclose()


def _remove_expired(store: BoundedCache, now: float) -> None:
    for cache_key, (_expires_at, stale_until, _value) in list(store.items()):
        if stale_until <= now:
            store.pop(cache_key, None)


def get_cached(
    store: BoundedCache,
    key: str,
    *,
    retain_expired: bool = False,
) -> Any | None:
    now = time.monotonic()
    _remove_expired(store, now)
    entry = store.get(key)
    if entry is None:
        return None
    expires_at, _stale_until, value = entry
    store.move_to_end(key)
    if expires_at <= now:
        if not retain_expired:
            store.pop(key, None)
        return None
    return value


def get_last_cached(store: BoundedCache, key: str) -> Any | None:
    """Return a value only during its explicit, finite stale-fallback window."""
    _remove_expired(store, time.monotonic())
    entry = store.get(key)
    if entry is None:
        return None
    store.move_to_end(key)
    return entry[2]


def set_cached(
    store: BoundedCache,
    key: str,
    value: Any,
    ttl_s: float,
    *,
    stale_ttl_s: float = 0,
) -> None:
    now = time.monotonic()
    _remove_expired(store, now)
    expires_at = now + ttl_s
    store[key] = (expires_at, expires_at + max(0.0, stale_ttl_s), value)
    store.move_to_end(key)
    while len(store) > store.max_entries:
        store.popitem(last=False)


async def share_inflight(key: str, factory: Callable[[], Awaitable[Any]]) -> Any:
    """Join duplicate requests without letting one cancelled caller cancel all."""
    async with _inflight_lock:
        task = _inflight.get(key)
        if task is None:
            task = asyncio.create_task(factory(), name="mta-bus-request")
            _inflight[key] = task
            task.add_done_callback(
                lambda completed: _inflight.pop(key, None)
                if _inflight.get(key) is completed
                else None
            )
    return await asyncio.shield(task)
