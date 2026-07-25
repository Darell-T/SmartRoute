"""Deterministic, GTFS-backed subway and bus arrival lookup for chat."""

from __future__ import annotations

import asyncio
import math
import re
import time
from datetime import datetime, timezone
from typing import Iterable

from app.services import mta_feed
from app.services.agent import events as agent_events
from app.services.agent.tools._types import ToolContext, ToolResult
from app.services.mta.feeds import parse_feed_message
from app.utils.geo import distance_meters

ARRIVAL_LIMIT_DEFAULT = 3
ARRIVAL_LIMIT_MAX = 5
FEED_STALE_AFTER_S = 120
BOARDING_BUFFER_MINUTES = 2

_STATION_ALIASES = {
    "newkirk avenue": "Newkirk Plaza",
    "newkirk av": "Newkirk Plaza",
    "atlantic avenue": "Atlantic Av-Barclays Ctr",
    "atlantic terminal": "Atlantic Av-Barclays Ctr",
    "barclays center": "Atlantic Av-Barclays Ctr",
    "penn station": "34 St-Penn Station",
    "grand central": "Grand Central-42 St",
}

LOOKUP_ARRIVALS_SCHEMA = {
    "name": "lookup_arrivals",
    "description": (
        "Look up grounded MTA subway or bus arrivals for a route and stop. "
        "Use this for 'next train/bus', 'how long until my train', and "
        "'will I make it' questions. Never invent arrival times."
    ),
    "strict": True,
    "input_schema": {
        "type": "object",
        "properties": {
            "mode": {"type": "string", "enum": ["subway", "bus"]},
            "route_id": {"type": "string", "description": "MTA route, e.g. B, Q, F, M15, B35."},
            "stop_query": {"type": "string", "description": "Explicit station or bus-stop name."},
            "direction": {
                "type": "string",
                "description": "Explicit rider direction or destination. Omit to show both directions.",
            },
            "user_location": {
                "type": "object",
                "properties": {
                    "latitude": {"type": "number"},
                    "longitude": {"type": "number"},
                },
                "required": ["latitude", "longitude"],
                "additionalProperties": False,
            },
            "active_trip_id": {"type": "string"},
            "limit": {"type": "integer"},
            "walking_minutes": {
                "type": "integer",
                "description": "Known walk time to the boarding stop for catchability assessment.",
            },
        },
        "required": ["route_id"],
        "additionalProperties": False,
    },
}


def _normalized_name(value: object) -> str:
    text = " ".join(
        str(value or "").casefold().replace("–", "-").replace("—", "-").split()
    )
    text = re.sub(r"\b(?:station|stop)\b", "", text)
    return " ".join(text.split())


def canonical_station_query(value: object) -> str:
    normalized = _normalized_name(value)
    return _STATION_ALIASES.get(normalized, str(value or "").strip())


def _active_boarding(ctx: ToolContext, route_id: str) -> dict | None:
    active = (ctx.session or {}).get("active_trip") or {}
    boarding = active.get("first_boarding") if isinstance(active, dict) else None
    if not isinstance(boarding, dict):
        return None
    active_route = str(boarding.get("route_id") or "").strip().upper()
    if route_id and active_route and active_route != route_id:
        return None
    return boarding


def _location(tool_input: dict, ctx: ToolContext, boarding: dict | None) -> tuple[float, float] | None:
    explicit = tool_input.get("user_location")
    if isinstance(explicit, dict):
        try:
            return float(explicit["latitude"]), float(explicit["longitude"])
        except (KeyError, TypeError, ValueError):
            return None
    if boarding:
        coords = boarding.get("coordinates")
        if isinstance(coords, dict):
            try:
                return float(coords.get("latitude", coords.get("lat"))), float(
                    coords.get("longitude", coords.get("lng"))
                )
            except (TypeError, ValueError):
                pass
    origin = ctx.origin or {}
    try:
        return float(origin["lat"]), float(origin["lng"])
    except (KeyError, TypeError, ValueError):
        return None


def _served_subway_stops(gtfs, route_id: str) -> list[dict]:
    try:
        stops = gtfs.get_subway_stops_with_routes({route_id})
    except Exception:
        return []
    return [stop for stop in stops if route_id in {str(item).upper() for item in stop.get("route_ids") or []}]


def _resolve_subway_stop(
    gtfs,
    route_id: str,
    stop_query: str | None,
    location: tuple[float, float] | None,
    boarding: dict | None,
) -> tuple[dict | None, list[dict]]:
    stops = _served_subway_stops(gtfs, route_id)
    if not stops:
        return None, []

    query = canonical_station_query(stop_query) if stop_query else ""
    if query:
        normalized_query = _normalized_name(query)
        exact = [stop for stop in stops if _normalized_name(stop.get("stop_name")) == normalized_query]
        matches = exact or [
            stop
            for stop in stops
            if normalized_query in _normalized_name(stop.get("stop_name"))
            or _normalized_name(stop.get("stop_name")) in normalized_query
        ]
        if len(matches) == 1:
            return matches[0], []
        if matches:
            return None, matches[:4]
        return None, []

    if boarding and boarding.get("stop_name"):
        target = _normalized_name(canonical_station_query(boarding["stop_name"]))
        match = next((stop for stop in stops if _normalized_name(stop.get("stop_name")) == target), None)
        if match:
            return match, []

    if location is None:
        return None, []
    latitude, longitude = location
    ranked = sorted(
        stops,
        key=lambda stop: distance_meters(
            latitude,
            longitude,
            float(stop["stop_lat"]),
            float(stop["stop_lon"]),
        ),
    )
    return ranked[0], []


def _normalize_direction(value: object) -> str | None:
    normalized = _normalized_name(value)
    if not normalized:
        return None
    if any(token in normalized for token in ("uptown", "northbound", "manhattan bound", "bronx bound")):
        return "uptown"
    if any(token in normalized for token in ("downtown", "southbound", "brooklyn bound", "queens bound")):
        return "downtown"
    return normalized


def _dedupe_predictions(values: Iterable[dict], *, limit: int) -> list[dict]:
    seen: set[tuple[str, int]] = set()
    result: list[dict] = []
    for value in sorted(values, key=lambda row: int(row.get("arrival_time") or 0)):
        arrival_time = int(value.get("arrival_time") or 0)
        direction = str(value.get("direction") or "unknown")
        key = (direction, arrival_time)
        if arrival_time <= 0 or key in seen:
            continue
        seen.add(key)
        result.append(value)
        if len(result) >= limit:
            break
    return result


def assess_catchability(
    arrival_minutes: Iterable[int],
    *,
    walking_minutes: int,
    boarding_buffer_minutes: int = BOARDING_BUFFER_MINUTES,
) -> dict:
    values = sorted({max(0, int(value)) for value in arrival_minutes})
    threshold = max(0, int(walking_minutes)) + max(0, int(boarding_buffer_minutes))
    catchable = next((value for value in values if value >= threshold), None)
    return {
        "walking_minutes": max(0, int(walking_minutes)),
        "boarding_buffer_minutes": max(0, int(boarding_buffer_minutes)),
        "arrival_minutes": values,
        "catchable_arrival_minutes": catchable,
        "confidence": 0.9 if values else 0.0,
    }


def _empty_payload(
    route_id: str,
    status: str,
    *,
    stop_name: str = "Transit stop",
    ambiguity: list[dict] | None = None,
) -> dict:
    return {
        "route_id": route_id,
        "stop": {"id": "", "name": stop_name},
        "directions": [],
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "source_status": status,
        **({"ambiguity": ambiguity} if ambiguity is not None else {}),
    }


def _arrival_payload(
    *,
    route_id: str,
    stop: dict,
    grouped: dict[str, list[dict]],
    updated_at: int,
    status: str,
    walking_minutes: int | None = None,
) -> dict:
    directions = []
    now = int(time.time())
    for direction, values in sorted(grouped.items()):
        arrivals = []
        for value in values:
            timestamp = int(value["arrival_time"])
            arrivals.append(
                {
                    "expected_at": datetime.fromtimestamp(timestamp, timezone.utc).isoformat(),
                    "minutes": max(0, math.ceil((timestamp - now) / 60)),
                    "realtime": status in {"live", "stale"},
                    "trip_id": value.get("trip_id"),
                    "vehicle_id": value.get("vehicle_id"),
                }
            )
        directions.append(
            {
                "id": direction,
                "label": (
                    "Uptown / Manhattan-bound"
                    if direction == "uptown"
                    else "Downtown / Brooklyn-bound"
                    if direction == "downtown"
                    else str(values[0].get("direction_label") or direction).title()
                ),
                "arrivals": arrivals,
            }
        )
    all_minutes = [arrival["minutes"] for group in directions for arrival in group["arrivals"]]
    payload = {
        "route_id": route_id,
        "stop": {
            "id": str(stop.get("stop_id") or ""),
            "name": str(stop.get("stop_name") or "Transit stop"),
            "distance_meters": stop.get("distance_m"),
            "latitude": stop.get("stop_lat"),
            "longitude": stop.get("stop_lon"),
        },
        "directions": directions,
        "updated_at": datetime.fromtimestamp(updated_at, timezone.utc).isoformat(),
        "source_status": status,
    }
    if walking_minutes is not None:
        payload["catchability"] = assess_catchability(
            all_minutes,
            walking_minutes=walking_minutes,
        )
    return payload


async def _lookup_subway(tool_input: dict, ctx: ToolContext, route_id: str, limit: int) -> ToolResult:
    if ctx.gtfs is None:
        return ToolResult(ok=False, error="transit stop data is not ready")
    boarding = _active_boarding(ctx, route_id)
    location = _location(tool_input, ctx, boarding)
    stop, ambiguity = _resolve_subway_stop(
        ctx.gtfs,
        route_id,
        str(tool_input.get("stop_query") or "").strip() or None,
        location,
        boarding,
    )
    if stop is None:
        data = _empty_payload(
            route_id,
            "stop_not_resolved",
            stop_name=str(tool_input.get("stop_query") or "Transit stop"),
            ambiguity=[
                {"stop_id": item.get("stop_id"), "stop_name": item.get("stop_name")}
                for item in ambiguity
            ],
        )
        return ToolResult(
            ok=True,
            data=data,
            summary=f"could not resolve a {route_id} station",
            events=[agent_events.ArrivalCardEvent.from_lookup(ctx.turn_id, data)],
        )

    child_ids = {str(item) for item in ctx.gtfs.get_child_stop_ids(stop["stop_id"])}
    child_ids.add(str(stop["stop_id"]))
    metadata = await mta_feed.fetch_feeds_with_metadata([route_id], "agent_arrivals")
    if not metadata:
        payload = _arrival_payload(
            route_id=route_id,
            stop=stop,
            grouped={},
            updated_at=int(time.time()),
            status="provider_unavailable",
        )
        return ToolResult(
            ok=True,
            data=payload,
            summary=f"arrival provider unavailable for {route_id} at {stop['stop_name']}",
            events=[agent_events.ArrivalCardEvent.from_lookup(ctx.turn_id, payload)],
        )

    parsed = await asyncio.gather(
        *(asyncio.to_thread(mta_feed.parse_bytes, item["content"]) for item in metadata),
        return_exceptions=True,
    )
    predictions: list[dict] = []
    feed_timestamps: list[int] = []
    for item, rows in zip(metadata, parsed):
        try:
            feed = parse_feed_message(item["content"])
            if feed.header.timestamp:
                feed_timestamps.append(int(feed.header.timestamp))
        except Exception:
            pass
        if isinstance(rows, BaseException):
            continue
        for row in rows:
            stop_id = str(row.get("stop_id") or "")
            if str(row.get("route_id") or "").upper() != route_id:
                continue
            if stop_id not in child_ids and stop_id.rstrip("NS") not in child_ids:
                continue
            predictions.append(row)

    requested_direction = _normalize_direction(
        tool_input.get("direction") or (boarding or {}).get("direction")
    )
    now = int(time.time())
    future = [
        value
        for value in predictions
        if int(value.get("arrival_time") or 0) >= now - 30
    ]
    if requested_direction in {"uptown", "downtown"}:
        future = [
            value for value in future if _normalize_direction(value.get("direction")) == requested_direction
        ]

    grouped: dict[str, list[dict]] = {}
    for value in future:
        direction = _normalize_direction(value.get("direction")) or "unknown"
        grouped.setdefault(direction, []).append(value)
    grouped = {
        direction: _dedupe_predictions(values, limit=limit)
        for direction, values in grouped.items()
    }
    latest_feed = max(feed_timestamps, default=now)
    status = (
        "stale"
        if feed_timestamps and now - latest_feed > FEED_STALE_AFTER_S
        else "live"
        if grouped
        else "no_predictions"
    )
    if location:
        stop["distance_m"] = round(
            distance_meters(
                location[0],
                location[1],
                float(stop["stop_lat"]),
                float(stop["stop_lon"]),
            ),
            1,
        )
    walking_minutes = tool_input.get("walking_minutes")
    if walking_minutes is None:
        walking_minutes = (boarding or {}).get("walking_minutes")
    payload = _arrival_payload(
        route_id=route_id,
        stop=stop,
        grouped=grouped,
        updated_at=latest_feed,
        status=status,
        walking_minutes=int(walking_minutes) if walking_minutes is not None else None,
    )
    return ToolResult(
        ok=True,
        data=payload,
        summary=f"{status} arrivals for {route_id} at {stop['stop_name']}",
        events=[agent_events.ArrivalCardEvent.from_lookup(ctx.turn_id, payload)],
    )


async def _lookup_bus(tool_input: dict, ctx: ToolContext, route_id: str, limit: int) -> ToolResult:
    boarding = _active_boarding(ctx, route_id)
    location = _location(tool_input, ctx, boarding)
    if location is None:
        data = _empty_payload(
            route_id,
            "stop_not_resolved",
            stop_name=str(tool_input.get("stop_query") or "Transit stop"),
        )
        return ToolResult(
            ok=True,
            data=data,
            summary=f"a location or stop is needed for {route_id} arrivals",
            events=[agent_events.ArrivalCardEvent.from_lookup(ctx.turn_id, data)],
        )
    arrivals, debug = await mta_feed.fetch_nearby_bus_arrivals(
        location[0],
        location[1],
        stop_limit=12,
        visits_per_stop=max(limit, 3),
    )
    if not debug.get("bus_arrivals_supported"):
        data = _empty_payload(
            route_id,
            "provider_unavailable",
            stop_name=str(tool_input.get("stop_query") or "Transit stop"),
        )
        return ToolResult(
            ok=True,
            data=data,
            summary=f"arrival provider unavailable for {route_id}",
            events=[agent_events.ArrivalCardEvent.from_lookup(ctx.turn_id, data)],
        )
    stop_query = _normalized_name(
        canonical_station_query(
            tool_input.get("stop_query") or (boarding or {}).get("stop_name") or ""
        )
    )
    requested_direction = _normalize_direction(
        tool_input.get("direction") or (boarding or {}).get("direction")
    )
    matches = [
        row
        for row in arrivals
        if str(row.get("route_id") or "").upper() == route_id
        and (not stop_query or stop_query in _normalized_name(row.get("station_name")))
        and (
            requested_direction is None
            or requested_direction in _normalized_name(row.get("direction"))
            or requested_direction in _normalized_name(row.get("terminal_stop_name"))
        )
    ]
    if not matches:
        data = _empty_payload(
            route_id,
            "no_predictions",
            stop_name=str(tool_input.get("stop_query") or "Transit stop"),
        )
        return ToolResult(
            ok=True,
            data=data,
            summary=f"no predictions returned for {route_id}",
            events=[agent_events.ArrivalCardEvent.from_lookup(ctx.turn_id, data)],
        )
    nearest = min(matches, key=lambda row: float(row.get("distance_m") or 0))
    stop = {
        "stop_id": nearest.get("parent_stop_id") or nearest.get("stop_id"),
        "stop_name": nearest.get("station_name") or nearest.get("parent_stop_name"),
        "stop_lat": nearest.get("stop_lat"),
        "stop_lon": nearest.get("stop_lon"),
        "distance_m": nearest.get("distance_m"),
    }
    same_stop = [
        row
        for row in matches
        if (row.get("parent_stop_id") or row.get("stop_id")) == stop["stop_id"]
    ]
    grouped: dict[str, list[dict]] = {}
    for row in same_stop:
        key = _normalize_direction(row.get("terminal_stop_name") or row.get("direction")) or "route"
        copied = dict(row)
        copied["direction_label"] = row.get("terminal_stop_name") or row.get("direction")
        grouped.setdefault(key, []).append(copied)
    grouped = {
        direction: _dedupe_predictions(values, limit=limit)
        for direction, values in grouped.items()
    }
    payload = _arrival_payload(
        route_id=route_id,
        stop=stop,
        grouped=grouped,
        updated_at=int(time.time()),
        status="live",
        walking_minutes=(boarding or {}).get("walking_minutes"),
    )
    return ToolResult(
        ok=True,
        data=payload,
        summary=f"live arrivals for {route_id} at {stop['stop_name']}",
        events=[agent_events.ArrivalCardEvent.from_lookup(ctx.turn_id, payload)],
    )


async def execute(tool_input: dict, ctx: ToolContext) -> ToolResult:
    route_id = str(tool_input.get("route_id") or "").strip().upper()
    if not route_id:
        active = _active_boarding(ctx, "")
        route_id = str((active or {}).get("route_id") or "").strip().upper()
    if not route_id:
        return ToolResult(ok=False, error="route_id is required")
    try:
        limit = min(ARRIVAL_LIMIT_MAX, max(1, int(tool_input.get("limit") or ARRIVAL_LIMIT_DEFAULT)))
    except (TypeError, ValueError):
        limit = ARRIVAL_LIMIT_DEFAULT
    requested_mode = str(tool_input.get("mode") or "").strip().lower()
    mode = requested_mode or ("bus" if re.match(r"^[A-Z]+\d+", route_id) else "subway")
    if mode == "bus":
        return await _lookup_bus(tool_input, ctx, route_id, limit)
    return await _lookup_subway(tool_input, ctx, route_id, limit)
