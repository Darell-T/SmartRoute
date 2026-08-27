"""Associate neutral event-provider facts with concrete route candidates.

Raw Ticketmaster payloads never reach scoring or the conversational model.
"""

from __future__ import annotations

import asyncio
import dataclasses
from collections.abc import Awaitable, Callable, Iterable
from datetime import UTC, datetime, timedelta
from typing import Any, Literal
from zoneinfo import ZoneInfo

from app.services.geography import distance_meters
from app.services.trips.crowds import event_provider

EventEvidenceStatus = Literal[
    "available",
    "no_relevant_events",
    "partial",
    "provider_unavailable",
    "not_required",
]

_BASE_SEARCH_HUBS = 4
_MAX_SEARCH_HUBS = 8
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
    route_id: str = ""


def _parse_time(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(UTC)


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
                        route_id=str(
                            step.get("route_id") or step.get("train_line") or ""
                        )
                        .strip()
                        .upper(),
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
    additional_route_points: Iterable[RoutePoint] = (),
) -> list[dict]:
    """Associate only events close in both space and the candidate timeline."""

    points = [*route_points(routes), *additional_route_points]
    closest: dict[tuple[int, str], dict] = {}
    for event in events:
        event_id = str(event.get("event_id") or "").strip()
        coordinates = _event_coordinates(event)
        if not event_id or coordinates is None:
            continue
        for point in points:
            row = _event_point_row(event, event_id, point, fallback_time, coordinates)
            if row is None:
                continue
            key = (point.route_index, event_id)
            previous = closest.get(key)
            if previous is None or row["distance_meters"] < previous["distance_meters"]:
                closest[key] = row
    return sorted(
        closest.values(),
        key=lambda item: (
            int(item["route_index"]),
            -float(item["risk_score"]),
            str(item["event_id"]),
        ),
    )


def search_hubs(routes: list[list[dict]]) -> list[RoutePoint]:
    """Select a bounded hub set that represents each candidate route first."""

    points_by_route: dict[int, list[RoutePoint]] = {}
    seen_by_route: dict[int, set[tuple[int, int]]] = {}
    for point in route_points(routes):
        key = (round(point.latitude * 1000), round(point.longitude * 1000))
        seen = seen_by_route.setdefault(point.route_index, set())
        if key in seen:
            continue
        seen.add(key)
        points_by_route.setdefault(point.route_index, []).append(point)

    route_indexes = sorted(points_by_route)
    if not route_indexes:
        return []
    budget = min(
        _MAX_SEARCH_HUBS,
        max(_BASE_SEARCH_HUBS, len(route_indexes)),
    )

    # Start at each candidate's destination-side transit point. Common
    # origins otherwise consume the whole global budget while leaving later
    # route candidates completely unobserved.
    selected = [points_by_route[index][-1] for index in route_indexes[:budget]]
    if len(selected) == budget:
        return selected

    # Spend the remaining bounded budget round-robin on transfer/origin-side
    # points so one route cannot monopolize secondary coverage.
    remaining = {
        index: list(reversed(points_by_route[index][:-1]))
        for index in route_indexes[:budget]
    }
    while len(selected) < budget:
        added = False
        for index in route_indexes[:budget]:
            candidates = remaining[index]
            if not candidates:
                continue
            selected.append(candidates.pop(0))
            added = True
            if len(selected) == budget:
                break
        if not added:
            break
    return selected


def _event_coordinates(event: dict) -> tuple[float, float] | None:
    try:
        latitude = float(event.get("venue_latitude"))
        longitude = float(event.get("venue_longitude"))
    except (TypeError, ValueError):
        return None
    return (latitude, longitude)


def _event_point_row(
    event: dict,
    event_id: str,
    point: RoutePoint,
    fallback_time: datetime,
    coordinates: tuple[float, float],
) -> dict | None:
    event_lat, event_lng = coordinates
    distance = distance_meters(
        point.latitude,
        point.longitude,
        event_lat,
        event_lng,
    )
    if distance > _ASSOCIATION_RADIUS_METERS:
        return None
    exposure = _exposure_window(point.expected_at or fallback_time, event)
    if exposure is None:
        return None
    window, base_risk = exposure
    risk = _event_risk_fields(event, base_risk, distance)
    return {
        "event_id": event_id,
        "source_ref": str(
            event.get("source_reference")
            or f"{event.get('source_class') or 'structured'}:{event_id}"
        )[:160],
        "title": str(event.get("name") or "Nearby event")[:140],
        "category": str(event.get("category") or "other")[:24],
        "venue": str(event.get("venue_name") or "venue")[:100],
        "latitude": event_lat,
        "longitude": event_lng,
        "route_index": point.route_index,
        "distance_meters": round(distance, 1),
        "affected_stop_ids": [point.name],
        "stations": [point.name],
        "lines": [point.route_id] if point.route_id else [],
        "impact_scope": "station_crowding",
        "exposure_window": window,
        "window_start_iso": event.get("start_iso"),
        "window_end_iso": event.get("estimated_end_iso"),
        **risk,
        "observed_at": event.get("observed_at"),
        "freshness_status": "current",
    }


def _event_risk_fields(
    event: dict,
    base_risk: float,
    distance: float,
) -> dict[str, Any]:
    """Normalize authorization and risk without implying observed occupancy."""

    scoring_authorized = bool(event.get("scoring_authorized", True))
    source_class = str(event.get("source_class") or "structured")
    source_multiplier = 0.65 if source_class == "official_x" else 1.0
    risk_score = 0.0
    if scoring_authorized:
        risk_score = min(
            5.0 if source_class == "official_x" else 18.0,
            round(
                base_risk
                * max(0.35, 1.0 - distance / _ASSOCIATION_RADIUS_METERS)
                * source_multiplier,
                2,
            ),
        )
    return {
        "source_class": source_class,
        "risk_score": risk_score,
        "confidence": float(
            event.get("confidence")
            or (0.85 if event.get("start_time_status") == "confirmed" else 0.6)
        ),
        "crowd_level": ("high" if risk_score >= 6 else "moderate")
        if scoring_authorized
        else None,
        "verification_tier": str(event.get("verification_tier") or "structured"),
        "scoring_authorized": scoring_authorized,
    }


async def collect_route_event_evidence(
    routes: list[list[dict]],
    ctx: Any,
    *,
    lookup: Callable[[dict, Any], Awaitable[Any]] | None = None,
    search_points: Iterable[RoutePoint] | None = None,
) -> tuple[EventEvidenceStatus, list[dict], list[str]]:
    """Search route hubs concurrently and return status, impacts, failures."""

    if lookup is None:
        lookup = event_provider.lookup_events

    supplied_points = list(search_points or [])
    hubs = supplied_points or search_hubs(routes)
    if not hubs:
        if routes:
            return "partial", [], ["no usable route hub for event coverage"]
        return "no_relevant_events", [], []
    travel_time = next((point.expected_at for point in hubs if point.expected_at), None)
    if travel_time is None:
        travel_time = _parse_time(ctx.now_et) or datetime.now(UTC)
    date = travel_time.astimezone(_NYC_TZ).date().isoformat()
    (
        events,
        failures,
        completed_route_indexes,
        completed_lookups,
    ) = await _lookup_event_hubs(hubs, lookup, ctx, date)
    impacts = associate_events(
        routes,
        events,
        fallback_time=travel_time,
        additional_route_points=supplied_points,
    )
    status = _event_evidence_status(
        routes,
        impacts,
        failures,
        completed_lookups,
        completed_route_indexes,
    )
    return status, impacts, failures


async def _lookup_event_hubs(
    hubs: list[RoutePoint],
    lookup: Callable[[dict, Any], Awaitable[Any]],
    ctx: Any,
    date: str,
) -> tuple[list[dict], list[str], set[int], int]:
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
    completed_route_indexes: set[int] = set()
    completed_lookups = 0
    seen: set[str] = set()
    for hub, result in zip(hubs, results, strict=False):
        if isinstance(result, BaseException):
            failures.append(type(result).__name__)
            continue
        if not result.ok:
            failures.append(str(result.error or "event lookup failed"))
            continue
        completed_lookups += 1
        completed_route_indexes.add(hub.route_index)
        for event in (result.data or {}).get("events") or []:
            event_id = str(event.get("event_id") or "")
            if not event_id or event_id in seen:
                continue
            seen.add(event_id)
            events.append(event)
    return events, failures, completed_route_indexes, completed_lookups


def _event_evidence_status(
    routes: list[list[dict]],
    impacts: list[dict],
    failures: list[str],
    completed_lookups: int,
    completed_route_indexes: set[int],
) -> EventEvidenceStatus:
    expected_route_indexes = set(range(len(routes)))
    missing_route_indexes = expected_route_indexes - completed_route_indexes
    incomplete_candidate_coverage = bool(missing_route_indexes)
    if impacts:
        return "partial" if failures or incomplete_candidate_coverage else "available"
    if completed_lookups == 0:
        return "provider_unavailable"
    if failures or incomplete_candidate_coverage:
        return "partial"
    return "no_relevant_events"


def route_event_penalty(route_index: int, impacts: Iterable[dict]) -> float:
    """Bound multiple-event exposure so one dense venue area cannot dominate."""

    total = sum(
        max(0.0, float(impact.get("risk_score") or 0.0))
        for impact in impacts
        if int(impact.get("route_index", -1)) == route_index
    )
    return min(18.0, round(total, 2))
