"""Process-owned normalized MTA realtime state.

The refresh loop is the only routine that parses network-wide GTFS-RT data.
Sockets and HTTP callers read the latest completed generation and perform only
location-specific filtering and enrichment.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from types import MappingProxyType
from typing import Awaitable, Callable, Mapping

from app.services.mta.alerts import (
    fetch_service_alerts,
    parse_service_alerts,
    parse_service_alerts_for_service_board,
)
from app.services.mta.config import ALL_SUBWAY_ROUTES, route_to_feed
from app.services.mta.feeds import fetch_feeds_with_metadata, parse_bytes
from app.services.mta.subway import _build_subway_vehicle_positions


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


def _normalize_network_data(
    feed_rows: list[dict],
    raw_alerts: bytes,
    generation: int,
) -> NetworkSnapshot:
    """Parse all feeds sequentially in one worker instead of per-client pools."""

    trip_updates: list[dict] = []
    for feed in feed_rows:
        trip_updates.extend(parse_bytes(feed["content"]))
    arrival_lookup = {
        (str(update["trip_id"]), str(update["stop_id"])): int(update["arrival_time"])
        for update in trip_updates
        if update.get("trip_id") and update.get("stop_id") and update.get("arrival_time")
    }

    requested_routes = set(ALL_SUBWAY_ROUTES)
    vehicles, vehicle_debug = _build_subway_vehicle_positions(
        feed_rows,
        {route for route in requested_routes if route in route_to_feed},
        requested_routes,
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
        arrival_lookup=MappingProxyType(arrival_lookup),
        vehicles=tuple(MappingProxyType(dict(vehicle)) for vehicle in vehicles),
        vehicle_debug=MappingProxyType(dict(vehicle_debug)),
        alerts=tuple(
            MappingProxyType({
                **alert,
                "route_ids": tuple(alert.get("route_ids") or ()),
                "stop_ids": tuple(alert.get("stop_ids") or ()),
            })
            for alert in alerts
        ),
        service_alerts=tuple(
            MappingProxyType({
                **alert,
                "route_ids": tuple(alert.get("route_ids") or ()),
                "stop_ids": tuple(alert.get("stop_ids") or ()),
            })
            for alert in service_alerts
        ),
        feed_count=len(feed_rows),
    )


async def build_network_snapshot(generation: int) -> NetworkSnapshot:
    feed_result, alert_result = await asyncio.gather(
        fetch_feeds_with_metadata(
            ALL_SUBWAY_ROUTES,
            "network_snapshot",
            force_refresh=True,
        ),
        fetch_service_alerts(force_refresh=True),
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
    """Own exactly one current generation and one optional in-flight build."""

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

    @property
    def current(self) -> NetworkSnapshot | None:
        return self._current

    def refresh_event(self) -> asyncio.Event:
        return self._refresh_event

    async def refresh(self) -> NetworkSnapshot:
        async with self._lock:
            task = self._inflight
            if task is None:
                generation = self._next_generation
                self._next_generation += 1
                task = asyncio.create_task(self._build_and_publish(generation))
                self._inflight = task
        return await asyncio.shield(task)

    async def get_or_refresh(self) -> NetworkSnapshot:
        return self._current or await self.refresh()

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
        self._refresh_event.set()


network_snapshot_store = NetworkSnapshotStore()
