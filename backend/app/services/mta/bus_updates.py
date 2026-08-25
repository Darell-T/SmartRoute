"""Bounded BusTime arrival updates for live-feed consumers."""

from __future__ import annotations

import asyncio
import time
from datetime import datetime
from typing import Any, Literal, TypedDict

from app.services.mta import bus_runtime
from app.services.mta.bus import (
    _location_cache_key,
    fetch_bus_stop_monitoring,
    fetch_nearby_bus_stops,
    parse_bus_stop_monitoring,
)
from app.services.mta.config import NYC_TZ


NEARBY_ARRIVALS_CACHE_TTL_S = 15
BUS_UPDATE_MAX_STALE_S = 45
BUS_TOTAL_TIMEOUT_S = 2.5

BusUpdateStatus = Literal["ready", "cached", "unavailable"]


class BusUpdate(TypedDict):
    arrivals: list[dict[str, Any]]
    fetched_at: int
    status: BusUpdateStatus
    debug: dict[str, Any]


class BusUpdateData(TypedDict):
    generation: int
    arrivals: list[dict[str, Any]]
    fetched_at: int
    status: BusUpdateStatus


class BusUpdateEvent(TypedDict):
    type: Literal["bus_update"]
    data: BusUpdateData


def _arrival_cache_key(
    lat: float,
    lng: float,
    radius_m: float,
    stop_limit: int,
    visits_per_stop: int,
) -> str:
    return (
        f"{_location_cache_key(lat, lng, radius_m)}:"
        f"{max(1, int(stop_limit))}:{max(1, int(visits_per_stop))}"
    )


async def _fetch_nearby_bus_arrivals(
    lat: float,
    lng: float,
    radius_m: float,
    stop_limit: int,
    visits_per_stop: int,
) -> tuple[list[dict], dict]:
    stops, debug = await fetch_nearby_bus_stops(lat, lng, radius_m, stop_limit)
    if not stops:
        return [], {**debug, "bus_arrival_count": 0}

    tasks = [fetch_bus_stop_monitoring(stop["stop_id"], visits_per_stop) for stop in stops]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    arrivals = []
    failures = 0
    for stop, result in zip(stops, results):
        if isinstance(result, Exception):
            failures += 1
            continue
        arrivals.extend(parse_bus_stop_monitoring(result, stop))

    now = datetime.now(tz=NYC_TZ).timestamp()
    arrivals = [
        arrival for arrival in arrivals
        if arrival.get("arrival_time") and arrival["arrival_time"] >= now - 60
    ]
    arrivals.sort(key=lambda arrival: arrival.get("arrival_time") or 0)
    return arrivals, {
        **debug,
        "nearby_bus_stop_count": len(stops),
        "bus_arrival_count": len(arrivals),
        "bus_stop_monitoring_failures": failures,
    }


def _bus_update(
    arrivals: list[dict],
    debug: dict,
    *,
    status: BusUpdateStatus,
    fetched_at: int | None = None,
) -> BusUpdate:
    return {
        "arrivals": [dict(arrival) for arrival in arrivals],
        "fetched_at": fetched_at or int(time.time()),
        "status": status,
        "debug": {
            **debug,
            "bus_arrival_count": int(debug.get("bus_arrival_count") or len(arrivals)),
            "bus_arrivals_supported": bool(debug.get("bus_arrivals_supported")),
        },
    }


async def fetch_nearby_bus_update(
    lat: float,
    lng: float,
    radius_m: float = 804.672,
    stop_limit: int = 10,
    visits_per_stop: int = 4,
) -> BusUpdate:
    """Return one bounded BusTime update, sharing in-flight location work."""
    cache_key = _arrival_cache_key(lat, lng, radius_m, stop_limit, visits_per_stop)
    cached = bus_runtime.get_cached(
        bus_runtime.nearby_arrivals_cache,
        cache_key,
        retain_expired=True,
    )
    if isinstance(cached, dict):
        return {
            **cached,
            "arrivals": [dict(arrival) for arrival in cached.get("arrivals", [])],
            "status": "cached",
        }

    async def refresh() -> BusUpdate:
        try:
            arrivals, debug = await asyncio.wait_for(
                _fetch_nearby_bus_arrivals(
                    lat,
                    lng,
                    radius_m,
                    stop_limit,
                    visits_per_stop,
                ),
                timeout=BUS_TOTAL_TIMEOUT_S,
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            stale = bus_runtime.get_last_cached(
                bus_runtime.nearby_arrivals_cache,
                cache_key,
            )
            if isinstance(stale, dict):
                return {
                    **stale,
                    "arrivals": [dict(arrival) for arrival in stale.get("arrivals", [])],
                    "status": "cached",
                }
            return _bus_update(
                [],
                {"bus_arrivals_supported": False, "reason": type(exc).__name__},
                status="unavailable",
            )
        status = "ready" if debug.get("bus_arrivals_supported") else "unavailable"
        update = _bus_update(arrivals, debug, status=status)
        if status != "ready":
            return update
        bus_runtime.set_cached(
            bus_runtime.nearby_arrivals_cache,
            cache_key,
            update,
            NEARBY_ARRIVALS_CACHE_TTL_S,
            stale_ttl_s=BUS_UPDATE_MAX_STALE_S,
        )
        return update

    return await bus_runtime.share_inflight(f"nearby-arrivals:{cache_key}", refresh)


def cached_nearby_bus_update(
    lat: float,
    lng: float,
    radius_m: float = 804.672,
) -> BusUpdate | None:
    """REST uses only a fresh cached BusTime result; it never blocks on one."""
    cached = bus_runtime.get_cached(
        bus_runtime.nearby_arrivals_cache,
        _arrival_cache_key(lat, lng, radius_m, 10, 4),
    )
    if not isinstance(cached, dict):
        return None
    return {
        **cached,
        "arrivals": [dict(arrival) for arrival in cached.get("arrivals", [])],
        "status": "cached",
    }
