"""BusTime-backed bus arrival lookup."""

from __future__ import annotations

from app.services.agent.tools._types import ToolContext, ToolResult
from app.services.agent.tools.location_resolution import parse_coordinates
from app.services.agent.tools.transit.lookup_arrivals_common import (
    _active_boarding,
    _arrival_payload,
    _dedupe_predictions,
    _direction_from_boarding,
    _direction_value_matches,
    _empty_payload,
    _location,
    _normalize_direction,
    _normalized_name,
    canonical_station_query,
)


async def execute(
    tool_input: dict,
    ctx: ToolContext,
    route_id: str,
    limit: int,
    *,
    feed_api: object,
    clock: object,
) -> ToolResult:
    timings = {
        "stop_resolution_ms": 0.0,
        "feed_fetch_ms": 0.0,
        "feed_parse_ms": 0.0,
    }

    def completed(result: ToolResult) -> ToolResult:
        result.timings.update(timings)
        return result

    stop_started = clock.monotonic()
    boarding = _active_boarding(ctx, route_id)
    location = _location(tool_input, ctx, boarding)
    stop_source = str(tool_input.get("stop_source") or "auto").strip().casefold()
    coordinate_query = parse_coordinates(tool_input.get("stop_query"))
    if coordinate_query is not None:
        location, boarding = coordinate_query, None
    elif stop_source == "current_location":
        boarding = None
        location = parse_coordinates(tool_input.get("user_location")) or parse_coordinates(
            ctx.origin or {}
        )
    elif stop_source == "named_station":
        location, boarding = None, None
    elif stop_source != "accepted_trip" and stop_source != "auto":
        return completed(
            ToolResult(ok=False, error="stop_source is invalid", internal_diagnostic=True)
        )
    timings["stop_resolution_ms"] = (clock.monotonic() - stop_started) * 1000
    if location is None:
        data = _empty_payload(
            route_id,
            "stop_not_resolved",
            now=int(clock.time()),
            stop_name=str(tool_input.get("stop_query") or "Transit stop"),
        )
        return completed(
            ToolResult(
                ok=True,
                data=data,
                summary=f"a location or stop is needed for {route_id} arrivals",
                events=[],
            )
        )

    feed_started = clock.monotonic()
    update = await feed_api.fetch_nearby_bus_update(
        location[0],
        location[1],
        stop_limit=12,
        visits_per_stop=max(limit, 3),
    )
    arrivals = update["arrivals"]
    debug = update["debug"]
    timings["feed_fetch_ms"] = (clock.monotonic() - feed_started) * 1000
    if not debug.get("bus_arrivals_supported"):
        data = _empty_payload(
            route_id,
            "provider_unavailable",
            now=int(clock.time()),
            stop_name=str(tool_input.get("stop_query") or "Transit stop"),
        )
        return completed(
            ToolResult(
                ok=True,
                data=data,
                summary=f"arrival provider unavailable for {route_id}",
                events=[],
            )
        )

    parse_started = clock.monotonic()
    stop_query = "" if coordinate_query is not None else _normalized_name(
        canonical_station_query(
            tool_input.get("stop_query") or (boarding or {}).get("stop_name") or ""
        )
    )
    requested_direction = _normalize_direction(tool_input.get("direction"))
    requested_direction = requested_direction or _direction_from_boarding(boarding)
    matches = [
        row
        for row in arrivals
        if str(row.get("route_id") or "").upper() == route_id
        and (not stop_query or stop_query in _normalized_name(row.get("station_name")))
        and (
            requested_direction is None
            or _direction_value_matches(requested_direction, row.get("direction"))
            or _direction_value_matches(
                requested_direction, row.get("terminal_stop_name")
            )
        )
    ]
    if not matches:
        data = _empty_payload(
            route_id,
            "no_predictions",
            now=int(clock.time()),
            stop_name=str(tool_input.get("stop_query") or "Transit stop"),
        )
        timings["feed_parse_ms"] = (clock.monotonic() - parse_started) * 1000
        return completed(
            ToolResult(
                ok=True,
                data=data,
                summary=f"no predictions returned for {route_id}",
                events=[],
            )
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
        key = _normalize_direction(
            row.get("terminal_stop_name") or row.get("direction")
        ) or "route"
        copied = dict(row)
        copied["direction_label"] = row.get("terminal_stop_name") or row.get("direction")
        grouped.setdefault(key, []).append(copied)
    now = int(clock.time())
    grouped = {
        direction: _dedupe_predictions(values, limit=limit, now=now)
        for direction, values in grouped.items()
    }
    payload = _arrival_payload(
        route_id=route_id,
        stop=stop,
        grouped=grouped,
        updated_at=now,
        now=now,
        status="live",
        walking_minutes=(boarding or {}).get("walking_minutes"),
    )
    timings["feed_parse_ms"] = (clock.monotonic() - parse_started) * 1000
    return completed(
        ToolResult(
            ok=True,
            data=payload,
            summary=f"live arrivals for {route_id} at {stop['stop_name']}",
            events=[],
        )
    )
