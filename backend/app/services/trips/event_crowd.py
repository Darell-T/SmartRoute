"""Ticketmaster event evidence associated with concrete route candidates.

The provider lookup stays in the agent tool module; this module converts its
normalized events into deterministic, route-indexed crowd evidence. Raw
Ticketmaster payloads never reach scoring or the conversational model.
"""

from __future__ import annotations

import asyncio
import dataclasses
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable, Iterable, Literal
from zoneinfo import ZoneInfo

from app.utils.geo import distance_meters

EventEvidenceStatus = Literal[
    "available",
    "no_relevant_events",
    "provider_unavailable",
    "not_required",
]

_MAX_SEARCH_HUBS = 4
_SEARCH_RADIUS_MILES = 1.25
_ASSOCIATION_RADIUS_METERS = 900.0
_NYC_TZ = ZoneInfo("America/New_York")


@dataclasses.dataclass(frozen=True)
class RoutePoint:
    route_index: int
    name: str
    latitude: float
    longitude: float
    expected_at: datetime | None


def _parse_time(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc)


def _coordinate(value: object) -> tuple[float, float] | None:
    if not isinstance(value, dict):
        return None
    try:
        latitude = float(value.get("latitude", value.get("lat")))
        longitude = float(value.get("longitude", value.get("lng")))
    except (TypeError, ValueError):
        return None
    if not (-90 <= latitude <= 90 and -180 <= longitude <= 180):
        return None
    return latitude, longitude


def route_points(routes: list[list[dict]]) -> list[RoutePoint]:
    points: list[RoutePoint] = []
    for route_index, route in enumerate(routes):
        for step in route:
            if step.get("type") not in {"SUBWAY", "BUS"}:
                continue
            for name_key, coord_key, time_key in (
                ("departure_stop", "departure_coords", "departure_time_iso"),
                ("arrival_stop", "arrival_coords", "arrival_time_iso"),
            ):
                coords = _coordinate(step.get(coord_key))
                if coords is None:
                    continue
                points.append(
                    RoutePoint(
                        route_index=route_index,
                        name=str(step.get(name_key) or "route stop")[:100],
                        latitude=coords[0],
                        longitude=coords[1],
                        expected_at=_parse_time(step.get(time_key)),
                    )
                )
    return points


def _exposure_window(point_time: datetime, event: dict) -> tuple[str, float] | None:
    start = _parse_time(event.get("start_iso"))
    end = _parse_time(event.get("estimated_end_iso"))
    if start is None:
        return None
    if start - timedelta(minutes=90) <= point_time <= start + timedelta(minutes=30):
        return "ingress", 8.0
    if end and end - timedelta(minutes=30) <= point_time <= end + timedelta(minutes=75):
        return "egress", 9.0
    if end and start + timedelta(minutes=30) < point_time < end - timedelta(minutes=30):
        return "during", 4.0
    return None


def associate_events(
    routes: list[list[dict]],
    events: Iterable[dict],
    *,
    fallback_time: datetime,
) -> list[dict]:
    """Associate only events close in both space and the candidate timeline."""

    points = route_points(routes)
    closest: dict[tuple[int, str], dict] = {}
    for event in events:
        try:
            event_lat = float(event.get("venue_latitude"))
            event_lng = float(event.get("venue_longitude"))
        except (TypeError, ValueError):
            continue
        event_id = str(event.get("event_id") or "").strip()
        if not event_id:
            continue
        for point in points:
            distance = distance_meters(
                point.latitude,
                point.longitude,
                event_lat,
                event_lng,
            )
            if distance > _ASSOCIATION_RADIUS_METERS:
                continue
            exposure = _exposure_window(point.expected_at or fallback_time, event)
            if exposure is None:
                continue
            window, base_risk = exposure
            distance_factor = max(0.35, 1.0 - distance / _ASSOCIATION_RADIUS_METERS)
            risk_score = round(base_risk * distance_factor, 2)
            row = {
                "event_id": event_id,
                "title": str(event.get("name") or "Nearby event")[:140],
                "venue": str(event.get("venue_name") or "venue")[:100],
                "latitude": event_lat,
                "longitude": event_lng,
                "route_index": point.route_index,
                "distance_meters": round(distance, 1),
                "affected_stop_ids": [point.name],
                "stations": [point.name],
                "lines": [],
                "impact_scope": "station_crowding",
                "exposure_window": window,
                "window_start_iso": event.get("start_iso"),
                "window_end_iso": event.get("estimated_end_iso"),
                "risk_score": risk_score,
                "confidence": 0.85 if event.get("start_time_status") == "confirmed" else 0.6,
                "crowd_level": "high" if risk_score >= 6 else "moderate",
            }
            key = (point.route_index, event_id)
            previous = closest.get(key)
            if previous is None or distance < previous["distance_meters"]:
                closest[key] = row
    return sorted(
        closest.values(),
        key=lambda item: (int(item["route_index"]), -float(item["risk_score"]), str(item["event_id"])),
    )


def _search_hubs(routes: list[list[dict]]) -> list[RoutePoint]:
    unique: list[RoutePoint] = []
    seen: set[tuple[int, int]] = set()
    for point in route_points(routes):
        key = (round(point.latitude * 1000), round(point.longitude * 1000))
        if key in seen:
            continue
        seen.add(key)
        unique.append(point)
    # The last stop and transfers generally carry more event exposure than
    # repeated adjacent stops. Preserve a deterministic bounded sample.
    if len(unique) <= _MAX_SEARCH_HUBS:
        return unique
    indices = {
        round(position * (len(unique) - 1) / (_MAX_SEARCH_HUBS - 1))
        for position in range(_MAX_SEARCH_HUBS)
    }
    return [unique[index] for index in sorted(indices)]


async def collect_route_event_evidence(
    routes: list[list[dict]],
    ctx: Any,
    *,
    lookup: Callable[[dict, Any], Awaitable[Any]] | None = None,
) -> tuple[EventEvidenceStatus, list[dict], list[str]]:
    """Search route hubs concurrently and return status, impacts, failures."""

    if lookup is None:
        # Lazy import avoids making the pure trip-scoring module depend on the
        # agent tool registry during package initialization.
        from app.services.agent.tools.event_lookup import execute as lookup

    hubs = _search_hubs(routes)
    if not hubs:
        return "no_relevant_events", [], []
    travel_time = next((point.expected_at for point in hubs if point.expected_at), None)
    if travel_time is None:
        travel_time = _parse_time(ctx.now_et) or datetime.now(timezone.utc)
    date = travel_time.astimezone(_NYC_TZ).date().isoformat()
    calls = [
        lookup(
            {
                "query": "",
                "date": date,
                "latitude": hub.latitude,
                "longitude": hub.longitude,
                "radius_miles": _SEARCH_RADIUS_MILES,
            },
            ctx,
        )
        for hub in hubs
    ]
    results = await asyncio.gather(*calls, return_exceptions=True)
    events: list[dict] = []
    failures: list[str] = []
    seen: set[str] = set()
    for result in results:
        if isinstance(result, BaseException):
            failures.append(type(result).__name__)
            continue
        if not result.ok:
            failures.append(str(result.error or "event lookup failed"))
            continue
        for event in (result.data or {}).get("events") or []:
            event_id = str(event.get("event_id") or "")
            if not event_id or event_id in seen:
                continue
            seen.add(event_id)
            events.append(event)

    impacts = associate_events(routes, events, fallback_time=travel_time)
    if impacts:
        return "available", impacts, failures
    if failures and not events:
        return "provider_unavailable", [], failures
    return "no_relevant_events", [], failures


def route_event_penalty(route_index: int, impacts: Iterable[dict]) -> float:
    """Bound multiple-event exposure so one dense venue area cannot dominate."""

    total = sum(
        max(0.0, float(impact.get("risk_score") or 0.0))
        for impact in impacts
        if int(impact.get("route_index", -1)) == route_index
    )
    return min(18.0, round(total, 2))
