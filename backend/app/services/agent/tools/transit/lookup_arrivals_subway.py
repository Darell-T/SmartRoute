"""GTFS-realtime subway arrival lookup."""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime

from google.protobuf.message import DecodeError

from app.services.agent.tools._types import ToolContext, ToolOutcome, ToolResult
from app.services.agent.tools.location_resolution import parse_coordinates
from app.services.agent.tools.transit.lookup_arrivals_common import (
    FEED_STALE_AFTER_S,
    _active_boarding,
    _arrival_payload,
    _dedupe_predictions,
    _direction_from_boarding,
    _empty_payload,
    _location,
    _normalize_direction,
    _normalized_name,
    canonical_station_query,
)
from app.services.geography import distance_meters
from app.services.mta.feeds import parse_feed_message

_LOGGER = logging.getLogger(__name__)


def _served_stops(gtfs: object, route_id: str) -> list[dict]:
    try:
        stops = gtfs.get_subway_stops_with_routes({route_id})
    except Exception:  # noqa: BLE001 subway-stop index faults stay empty
        return []
    return [
        stop
        for stop in stops
        if route_id in {str(item).upper() for item in stop.get("route_ids") or []}
    ]


def _resolve_stop(
    gtfs: object,
    route_id: str,
    stop_query: str | None,
    location: tuple[float, float] | None,
    boarding: dict | None,
) -> tuple[dict | None, list[dict]]:
    query = canonical_station_query(stop_query) if stop_query else ""
    if query:
        stops = _served_stops(gtfs, route_id)
        if not stops:
            return None, []
        normalized_query = _normalized_name(query)
        exact = [
            stop
            for stop in stops
            if _normalized_name(stop.get("stop_name")) == normalized_query
        ]
        matches = exact or [
            stop
            for stop in stops
            if normalized_query in _normalized_name(stop.get("stop_name"))
            or _normalized_name(stop.get("stop_name")) in normalized_query
        ]
        if len(matches) == 1:
            return matches[0], []
        return (None, matches[:4]) if matches else (None, [])

    if boarding and boarding.get("stop_id"):
        coords = boarding.get("coordinates") or {}
        return {
            "stop_id": str(boarding["stop_id"]),
            "stop_name": str(boarding.get("stop_name") or "Transit stop"),
            "stop_lat": coords.get("latitude", coords.get("lat")),
            "stop_lon": coords.get("longitude", coords.get("lng")),
        }, []

    stops = _served_stops(gtfs, route_id)
    if not stops:
        return None, []
    if boarding and boarding.get("stop_name"):
        target = _normalized_name(canonical_station_query(boarding["stop_name"]))
        match = next(
            (stop for stop in stops if _normalized_name(stop.get("stop_name")) == target),
            None,
        )
        if match:
            return match, []
    if location is None:
        return None, []
    latitude, longitude = location
    return min(
        stops,
        key=lambda stop: distance_meters(
            latitude,
            longitude,
            float(stop["stop_lat"]),
            float(stop["stop_lon"]),
        ),
    ), []


async def _scheduled_fallback(
    *,
    ctx: ToolContext,
    route_id: str,
    stop: dict,
    child_ids: set[str],
    requested_direction: str | None,
    limit: int,
    walking_minutes: int | None,
    clock: object,
) -> dict | None:
    lookup = getattr(ctx.gtfs, "get_scheduled_arrivals", None)
    if not callable(lookup):
        return None
    now = int(clock.time())
    try:
        result = await asyncio.to_thread(
            lookup,
            route_id=route_id,
            stop_ids=sorted(child_ids),
            direction=requested_direction,
            now=datetime.fromtimestamp(now, UTC),
            limit=limit,
        )
    except Exception:  # noqa: BLE001 schedule lookup faults stay unavailable
        return None
    if not isinstance(result, dict) or result.get("status") != "scheduled":
        return None
    grouped: dict[str, list[dict]] = {}
    for value in result.get("predictions") or []:
        direction = _normalize_direction(value.get("direction")) or "unknown"
        grouped.setdefault(direction, []).append(value)
    grouped = {
        direction: _dedupe_predictions(values, limit=limit, now=now)
        for direction, values in grouped.items()
    }
    return _arrival_payload(
        route_id=route_id,
        stop=stop,
        grouped=grouped,
        updated_at=now,
        now=now,
        status="scheduled",
        walking_minutes=walking_minutes,
        valid_until=result.get("valid_until"),
    )


def _resolved_arrival_stop(
    tool_input: dict,
    ctx: ToolContext,
    route_id: str,
    clock: object,
    timings: dict[str, float],
) -> ToolResult | tuple[dict, dict | None, tuple[float, float] | None]:
    stop_started = clock.monotonic()
    boarding = _active_boarding(ctx, route_id)
    location = _location(tool_input, ctx, boarding)
    stop_query, boarding, location, invalid = _arrival_source_bindings(
        tool_input, boarding, location
    )
    if invalid is not None:
        timings["stop_resolution_ms"] = (clock.monotonic() - stop_started) * 1000
        return invalid
    stop, ambiguity = _resolve_stop(ctx.gtfs, route_id, stop_query, location, boarding)
    timings["stop_resolution_ms"] = (clock.monotonic() - stop_started) * 1000
    if stop is None:
        data = _empty_payload(
            route_id,
            "stop_not_resolved",
            now=int(clock.time()),
            stop_name=str(stop_query or "Transit stop"),
            ambiguity=[
                {"stop_id": item.get("stop_id"), "stop_name": item.get("stop_name")}
                for item in ambiguity
            ],
        )
        return ToolResult(
            ok=True,
            data=data,
            summary=f"could not resolve a {route_id} station",
            outcome=ToolOutcome.NEEDS_CLARIFICATION,
        )
    return stop, boarding, location


def _arrival_source_bindings(
    tool_input: dict,
    boarding: dict | None,
    location: tuple[float, float] | None,
) -> tuple[str | None, dict | None, tuple[float, float] | None, ToolResult | None]:
    stop_source = str(tool_input.get("stop_source") or "auto").strip().casefold()
    stop_query = str(tool_input.get("stop_query") or "").strip() or None
    coordinate_query = parse_coordinates(stop_query)
    if coordinate_query is not None:
        return None, None, coordinate_query, None
    if stop_source == "current_location":
        return None, None, location, None
    if stop_source == "accepted_trip":
        return None, boarding, None, None
    if stop_source == "named_station":
        return stop_query, None, None, None
    if stop_source != "auto":
        return None, boarding, location, ToolResult(
            ok=False, error="stop_source is invalid", internal_diagnostic=True
        )
    return stop_query, boarding, location, None


def _child_stop_ids(ctx: ToolContext, stop: dict) -> set[str]:
    try:
        child_ids = {str(item) for item in ctx.gtfs.get_child_stop_ids(stop["stop_id"])}
    except Exception:  # noqa: BLE001 child-stop index faults to N/S suffix
        child_ids = {f"{stop['stop_id']}N", f"{stop['stop_id']}S"}
    child_ids.add(str(stop["stop_id"]))
    return child_ids


async def _collect_feed_predictions(
    feed_api: object,
    metadata: list[dict],
    route_id: str,
    child_ids: set[str],
) -> tuple[list[dict], list[int]]:
    parsed = await asyncio.gather(
        *(asyncio.to_thread(feed_api.parse_bytes, item["content"]) for item in metadata),
        return_exceptions=True,
    )
    predictions: list[dict] = []
    feed_timestamps: list[int] = []
    for item, rows in zip(metadata, parsed, strict=False):
        stamp = _readable_feed_timestamp(item)
        if stamp is not None:
            feed_timestamps.append(stamp)
        if isinstance(rows, BaseException):
            continue
        predictions.extend(_matching_feed_rows(rows, route_id, child_ids))
    return predictions, feed_timestamps


def _readable_feed_timestamp(item: dict) -> int | None:
    try:
        feed = parse_feed_message(item["content"])
    except (DecodeError, TypeError, ValueError) as exc:
        _LOGGER.debug("subway feed timestamp unreadable: %s", type(exc).__name__)
        return None
    if not feed.header.timestamp:
        return None
    return int(feed.header.timestamp)


def _matching_feed_rows(
    rows: list[dict], route_id: str, child_ids: set[str]
) -> list[dict]:
    matched: list[dict] = []
    for row in rows:
        stop_id = str(row.get("stop_id") or "")
        if str(row.get("route_id") or "").upper() != route_id:
            continue
        if stop_id not in child_ids and stop_id.rstrip("NS") not in child_ids:
            continue
        matched.append(row)
    return matched


async def _scheduled_or_unavailable(
    *,
    ctx: ToolContext,
    route_id: str,
    stop: dict,
    child_ids: set[str],
    requested_direction: str | None,
    limit: int,
    walking_minutes: int | None,
    clock: object,
    timings: dict[str, float],
) -> ToolResult:
    parse_started = clock.monotonic()
    scheduled = await _scheduled_fallback(
        ctx=ctx,
        route_id=route_id,
        stop=stop,
        child_ids=child_ids,
        requested_direction=requested_direction,
        limit=limit,
        walking_minutes=walking_minutes,
        clock=clock,
    )
    timings["feed_parse_ms"] = (clock.monotonic() - parse_started) * 1000
    if scheduled is not None:
        return ToolResult(
            ok=True,
            data=scheduled,
            summary=f"scheduled arrivals for {route_id} at {stop['stop_name']}",
            events=[],
        )
    payload = _arrival_payload(
        route_id=route_id,
        stop=stop,
        grouped={},
        updated_at=int(clock.time()),
        now=int(clock.time()),
        status="provider_unavailable",
    )
    return ToolResult(
        ok=True,
        data=payload,
        summary=f"arrival provider unavailable for {route_id} at {stop['stop_name']}",
        events=[],
    )


def _grouped_future_predictions(
    predictions: list[dict],
    feed_timestamps: list[int],
    requested_direction: str | None,
    limit: int,
    now: int,
) -> tuple[dict[str, list[dict]], str, int]:
    future = [
        value for value in predictions if int(value.get("arrival_time") or 0) >= now - 30
    ]
    if requested_direction in {"uptown", "downtown"}:
        future = [
            value
            for value in future
            if _normalize_direction(value.get("direction")) == requested_direction
        ]
    grouped: dict[str, list[dict]] = {}
    for value in future:
        direction = _normalize_direction(value.get("direction")) or "unknown"
        grouped.setdefault(direction, []).append(value)
    grouped = {
        direction: _dedupe_predictions(values, limit=limit, now=now)
        for direction, values in grouped.items()
    }
    latest_feed = max(feed_timestamps, default=now)
    status = _feed_prediction_status(feed_timestamps, now, latest_feed, grouped)
    return grouped, status, latest_feed


def _feed_prediction_status(
    feed_timestamps: list[int],
    now: int,
    latest_feed: int,
    grouped: dict[str, list[dict]],
) -> str:
    if feed_timestamps and now - latest_feed > FEED_STALE_AFTER_S:
        return "stale"
    if grouped:
        return "live"
    return "no_predictions"


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

    if ctx.gtfs is None:
        return completed(ToolResult(ok=False, error="transit stop data is not ready"))
    resolved = _resolved_arrival_stop(tool_input, ctx, route_id, clock, timings)
    if isinstance(resolved, ToolResult):
        return completed(resolved)
    stop, boarding, location = resolved
    child_ids = _child_stop_ids(ctx, stop)
    explicit_direction = _normalize_direction(tool_input.get("direction"))
    boarding_direction = _direction_from_boarding(boarding)
    requested_direction = (
        explicit_direction
        if explicit_direction in {"uptown", "downtown"}
        else boarding_direction or explicit_direction
    )
    walking_minutes = tool_input.get("walking_minutes")
    if walking_minutes is None:
        walking_minutes = (boarding or {}).get("walking_minutes")
    normalized_walking = int(walking_minutes) if walking_minutes is not None else None
    catchability_walking = (
        normalized_walking if requested_direction in {"uptown", "downtown"} else None
    )
    if location:
        stop["distance_m"] = round(
            distance_meters(
                location[0], location[1], float(stop["stop_lat"]), float(stop["stop_lon"])
            ),
            1,
        )

    feed_started = clock.monotonic()
    metadata = await feed_api.fetch_feeds_with_metadata([route_id], "agent_arrivals")
    timings["feed_fetch_ms"] = (clock.monotonic() - feed_started) * 1000
    if not metadata:
        return completed(
            await _scheduled_or_unavailable(
                ctx=ctx,
                route_id=route_id,
                stop=stop,
                child_ids=child_ids,
                requested_direction=requested_direction,
                limit=limit,
                walking_minutes=catchability_walking,
                clock=clock,
                timings=timings,
            )
        )

    parse_started = clock.monotonic()
    predictions, feed_timestamps = await _collect_feed_predictions(
        feed_api, metadata, route_id, child_ids
    )
    now = int(clock.time())
    grouped, status, latest_feed = _grouped_future_predictions(
        predictions, feed_timestamps, requested_direction, limit, now
    )
    if status == "stale" or (not feed_timestamps and not grouped):
        scheduled = await _scheduled_fallback(
            ctx=ctx,
            route_id=route_id,
            stop=stop,
            child_ids=child_ids,
            requested_direction=requested_direction,
            limit=limit,
            walking_minutes=catchability_walking,
            clock=clock,
        )
        if scheduled is not None:
            timings["feed_parse_ms"] = (clock.monotonic() - parse_started) * 1000
            return completed(
                ToolResult(
                    ok=True,
                    data=scheduled,
                    summary=f"scheduled arrivals for {route_id} at {stop['stop_name']}",
                    events=[],
                )
            )
    if status == "stale":
        grouped = {}
    payload = _arrival_payload(
        route_id=route_id,
        stop=stop,
        grouped=grouped,
        updated_at=latest_feed,
        now=now,
        status=status,
        walking_minutes=catchability_walking,
    )
    timings["feed_parse_ms"] = (clock.monotonic() - parse_started) * 1000
    return completed(
        ToolResult(
            ok=True,
            data=payload,
            summary=f"{status} arrivals for {route_id} at {stop['stop_name']}",
            events=[],
        )
    )
