"""BusTime-backed bus arrival lookup."""

from __future__ import annotations

from dataclasses import dataclass

from app.services.agent.tools.base import ToolContext, ToolResult
from app.services.agent.tools.location_resolution import (
    _origin_latlng,
    parse_coordinates,
)
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


@dataclass(frozen=True)
class _BusStopAdmission:
    location: tuple[float, float]
    boarding: dict | None


def _with_timings(result: ToolResult, timings: dict[str, float]) -> ToolResult:
    result.timings.update(timings)
    return result


def _empty_bus_result(
    route_id: str,
    status: str,
    tool_input: dict,
    clock: object,
    summary: str,
) -> ToolResult:
    data = _empty_payload(
        route_id,
        status,
        now=int(clock.time()),
        stop_name=str(tool_input.get("stop_query") or "Transit stop"),
    )
    return ToolResult(ok=True, data=data, summary=summary, events=[])


def _admit_bus_stop(
    tool_input: dict, ctx: ToolContext, route_id: str, clock: object
) -> _BusStopAdmission | ToolResult:
    boarding = _active_boarding(ctx, route_id)
    location = _location(tool_input, ctx, boarding)
    stop_source = str(tool_input.get("stop_source") or "auto").strip().casefold()
    coordinate_query = parse_coordinates(tool_input.get("stop_query"))
    if coordinate_query is not None:
        location, boarding = coordinate_query, None
    elif stop_source == "current_location":
        boarding = None
        location = parse_coordinates(tool_input.get("user_location")) or _origin_latlng(
            ctx
        )
    elif stop_source == "named_station":
        location, boarding = None, None
    elif stop_source != "accepted_trip" and stop_source != "auto":
        return ToolResult(
            ok=False, error="stop_source is invalid", internal_diagnostic=True
        )
    if location is None:
        return _empty_bus_result(
            route_id,
            "stop_not_resolved",
            tool_input,
            clock,
            f"a location or stop is needed for {route_id} arrivals",
        )
    return _BusStopAdmission(location=location, boarding=boarding)


def _bus_row_matches(
    row: dict,
    route_id: str,
    stop_query: str,
    requested_direction: str | None,
) -> bool:
    if str(row.get("route_id") or "").upper() != route_id:
        return False
    station = _normalized_name(row.get("station_name"))
    if stop_query and stop_query not in station:
        return False
    if requested_direction is None:
        return True
    return _direction_value_matches(
        requested_direction, row.get("direction")
    ) or _direction_value_matches(requested_direction, row.get("terminal_stop_name"))


def _grouped_bus_predictions(
    matches: list[dict], *, limit: int, now: int
) -> tuple[dict, dict[str, list[dict]]]:
    nearest = min(matches, key=lambda row: float(row.get("distance_m") or 0))
    stop = _bus_stop_record(nearest)
    same_stop = [
        row for row in matches if _bus_row_stop_id(row) == stop["stop_id"]
    ]
    grouped = {
        direction: _dedupe_predictions(values, limit=limit, now=now)
        for direction, values in _bus_predictions_by_direction(same_stop).items()
    }
    return stop, grouped


def _bus_row_stop_id(row: dict) -> object:
    return row.get("parent_stop_id") or row.get("stop_id")


def _bus_stop_record(nearest: dict) -> dict:
    return {
        "stop_id": _bus_row_stop_id(nearest),
        "stop_name": nearest.get("station_name") or nearest.get("parent_stop_name"),
        "stop_lat": nearest.get("stop_lat"),
        "stop_lon": nearest.get("stop_lon"),
        "distance_m": nearest.get("distance_m"),
    }


def _bus_predictions_by_direction(rows: list[dict]) -> dict[str, list[dict]]:
    grouped: dict[str, list[dict]] = {}
    for row in rows:
        label = row.get("terminal_stop_name") or row.get("direction")
        key = _normalize_direction(label) or "route"
        copied = dict(row)
        copied["direction_label"] = label
        grouped.setdefault(key, []).append(copied)
    return grouped


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
    stop_started = clock.monotonic()
    admitted = _admit_bus_stop(tool_input, ctx, route_id, clock)
    timings["stop_resolution_ms"] = (clock.monotonic() - stop_started) * 1000
    if isinstance(admitted, ToolResult):
        return _with_timings(admitted, timings)

    feed_started = clock.monotonic()
    update = await feed_api.fetch_nearby_bus_update(
        admitted.location[0],
        admitted.location[1],
        stop_limit=12,
        visits_per_stop=max(limit, 3),
    )
    timings["feed_fetch_ms"] = (clock.monotonic() - feed_started) * 1000
    if not update["debug"].get("bus_arrivals_supported"):
        return _with_timings(
            _empty_bus_result(
                route_id,
                "provider_unavailable",
                tool_input,
                clock,
                f"arrival provider unavailable for {route_id}",
            ),
            timings,
        )

    parse_started = clock.monotonic()
    coordinate_query = parse_coordinates(tool_input.get("stop_query"))
    stop_query = "" if coordinate_query is not None else _normalized_name(
        canonical_station_query(
            tool_input.get("stop_query")
            or (admitted.boarding or {}).get("stop_name")
            or ""
        )
    )
    requested_direction = _normalize_direction(
        tool_input.get("direction")
    ) or _direction_from_boarding(admitted.boarding)
    matches = [
        row
        for row in update["arrivals"]
        if _bus_row_matches(row, route_id, stop_query, requested_direction)
    ]
    if not matches:
        timings["feed_parse_ms"] = (clock.monotonic() - parse_started) * 1000
        return _with_timings(
            _empty_bus_result(
                route_id,
                "no_predictions",
                tool_input,
                clock,
                f"no predictions returned for {route_id}",
            ),
            timings,
        )

    now = int(clock.time())
    stop, grouped = _grouped_bus_predictions(matches, limit=limit, now=now)
    payload = _arrival_payload(
        route_id=route_id,
        stop=stop,
        grouped=grouped,
        updated_at=now,
        now=now,
        status="live",
        walking_minutes=(admitted.boarding or {}).get("walking_minutes"),
    )
    timings["feed_parse_ms"] = (clock.monotonic() - parse_started) * 1000
    return _with_timings(
        ToolResult(
            ok=True,
            data=payload,
            summary=f"live arrivals for {route_id} at {stop['stop_name']}",
            events=[],
        ),
        timings,
    )
