"""Process-owned normalized MTA realtime state.

The refresh loop is the only routine that parses network-wide GTFS-RT data.
Sockets and HTTP callers read the latest completed generation and perform only
location-specific filtering and enrichment.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from types import MappingProxyType

from app.services.mta.alerts import (
    fetch_service_alerts,
    parse_service_alerts,
    parse_service_alerts_for_service_board,
)
from app.services.mta.config import ALL_SUBWAY_ROUTES, route_to_feed
from app.services.mta.feeds import fetch_feeds_with_metadata, parse_bytes
from app.services.mta.subway import build_subway_vehicle_positions


@dataclass(frozen=True)
class NetworkSnapshot:
    generation: int
    updated_at: int
    trip_updates: tuple[Mapping[str, object], ...]
    arrival_lookup: Mapping[tuple[str, str], int]
    vehicles: tuple[Mapping[str, object], ...]
    vehicle_debug: Mapping[str, object]
    alerts: tuple[Mapping[str, object], ...]
    service_alerts: tuple[Mapping[str, object], ...]
    feed_count: int


def _arrival_lookup(
    trip_updates: list[dict],
) -> dict[tuple[str, str], int]:
    return {
        (str(update["trip_id"]), str(update["stop_id"])): int(update["arrival_time"])
        for update in trip_updates
        if update.get("trip_id") and update.get("stop_id") and update.get("arrival_time")
    }


def _freeze_alert_records(
    alerts: list,
) -> tuple[Mapping[str, object], ...]:
    return tuple(
        MappingProxyType({
            **alert,
            "route_ids": tuple(alert.get("route_ids") or ()),
            "stop_ids": tuple(alert.get("stop_ids") or ()),
        })
        for alert in alerts
    )


def _normalize_network_data(
    feed_rows: list[dict],
    raw_alerts: bytes,
    generation: int,
) -> NetworkSnapshot:
    trip_updates: list[dict] = []
    for feed in feed_rows:
        trip_updates.extend(parse_bytes(feed["content"]))
    vehicles, vehicle_debug = build_subway_vehicle_positions(
        feed_rows,
        {route for route in ALL_SUBWAY_ROUTES if route in route_to_feed},
        ALL_SUBWAY_ROUTES,
        True,
        True,
    )
    alerts = parse_service_alerts(raw_alerts) if raw_alerts else []
    service_alerts = (
        parse_service_alerts_for_service_board(raw_alerts) if raw_alerts else []
    )
    return NetworkSnapshot(
        generation=generation,
        updated_at=int(time.time()),
        trip_updates=tuple(MappingProxyType(dict(update)) for update in trip_updates),
        arrival_lookup=MappingProxyType(_arrival_lookup(trip_updates)),
        vehicles=tuple(MappingProxyType(dict(vehicle)) for vehicle in vehicles),
        vehicle_debug=MappingProxyType(dict(vehicle_debug)),
        alerts=_freeze_alert_records(alerts),
        service_alerts=_freeze_alert_records(service_alerts),
        feed_count=len(feed_rows),
    )


async def build_network_snapshot(generation: int) -> NetworkSnapshot:
    feed_result, alert_result = await asyncio.gather(
        fetch_feeds_with_metadata(
            ALL_SUBWAY_ROUTES,
            "network_snapshot",
            force_refresh=True,
            cache_result=False,
        ),
        fetch_service_alerts(force_refresh=True, cache_result=False),
        return_exceptions=True,
    )
    if isinstance(feed_result, Exception) or not feed_result:
        raise RuntimeError("subway realtime feeds unavailable")
    raw_alerts = b"" if isinstance(alert_result, Exception) else alert_result
    return await asyncio.to_thread(
        _normalize_network_data,
        feed_result,
        raw_alerts,
        generation,
    )


class NetworkSnapshotStore:
    def __init__(
        self,
        builder: Callable[[int], Awaitable[NetworkSnapshot]] = build_network_snapshot,
    ) -> None:
        self._builder = builder
        self._current: NetworkSnapshot | None = None
        self._inflight: asyncio.Task[NetworkSnapshot] | None = None
        self._lock = asyncio.Lock()
        self._refresh_event = asyncio.Event()
        self._next_generation = 1
        self._last_demand_at: float | None = None

    @property
    def current(self) -> NetworkSnapshot | None:
        return self._current

    def refresh_event(self) -> asyncio.Event:
        return self._refresh_event

    def note_demand(self) -> None:
        self._last_demand_at = time.monotonic()

    def has_recent_demand(self, max_idle_seconds: float) -> bool:
        last_demand = self._last_demand_at
        return (
            last_demand is not None
            and time.monotonic() - last_demand <= max_idle_seconds
        )

    async def refresh(self) -> NetworkSnapshot:
        async with self._lock:
            task = self._inflight
            if task is None:
                generation = self._next_generation
                self._next_generation += 1
                task = asyncio.create_task(self._build_and_publish(generation))
                self._inflight = task
        return await asyncio.shield(task)

    async def get_or_refresh(self, max_age_seconds: float = 30) -> NetworkSnapshot:
        self.note_demand()
        current = self._current
        if current is not None and time.time() - current.updated_at <= max_age_seconds:
            return current
        return await self.refresh()

    async def _build_and_publish(self, generation: int) -> NetworkSnapshot:
        task = asyncio.current_task()
        try:
            snapshot = await self._builder(generation)
            previous_event = self._refresh_event
            self._current = snapshot
            self._refresh_event = asyncio.Event()
            previous_event.set()
            return snapshot
        finally:
            async with self._lock:
                if self._inflight is task:
                    self._inflight = None

    async def close(self) -> None:
        async with self._lock:
            task = self._inflight
            self._inflight = None
        if task is not None and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        self._current = None
        self._last_demand_at = None
        self._refresh_event.set()


network_snapshot_store = NetworkSnapshotStore()
