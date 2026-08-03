"""Lazy alternate-itinerary enrichment endpoint and input boundary."""

from __future__ import annotations

import math
import time

from fastapi import HTTPException, Request
from pydantic import BaseModel, ConfigDict

from app.services.trips import enrichment


class EnrichRouteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    steps: list[dict]


_STEP_KEYS = frozenset({
    "type", "start_point", "end_point", "polyline", "train_line", "line_color", "direction",
    "departure_stop", "arrival_stop", "departure_coords", "arrival_coords", "minutes_until_train_arrives",
    "minutes_until_arrival", "route_total_minutes", "stop_count", "route_id", "intermediate_stops",
    "intermediate_stop_locations", "segment_index", "duration_minutes", "distance_meters",
    "route_total_seconds", "departure_time_iso", "arrival_time_iso",
})
_STEP_TEXT_FIELDS = frozenset({
    "route_id", "train_line", "line_color", "direction", "departure_stop", "arrival_stop",
})
_STEP_ISO_FIELDS = frozenset({"departure_time_iso", "arrival_time_iso"})


def _bounded_point(value: object) -> bool:
    if not isinstance(value, dict):
        return False
    if set(value) == {"lat", "lng"}:
        lat, lng = value["lat"], value["lng"]
    elif set(value) == {"latitude", "longitude"}:
        lat, lng = value["latitude"], value["longitude"]
    else:
        return False
    return (
        isinstance(lat, (int, float))
        and not isinstance(lat, bool)
        and isinstance(lng, (int, float))
        and not isinstance(lng, bool)
        and math.isfinite(lat)
        and math.isfinite(lng)
        and 40.2 <= lat <= 41.2
        and -74.6 <= lng <= -73.2
    )


def _bounded_stop_location(value: object) -> bool:
    return (
        isinstance(value, dict) and set(value) == {"name", "lat", "lng"}
        and isinstance(value["name"], str) and len(value["name"]) <= 300
        and _bounded_point({"lat": value["lat"], "lng": value["lng"]})
    )


def _enrichment_steps_are_bounded(steps: object) -> bool:
    if not isinstance(steps, list) or len(steps) > 64:
        return False
    for step in steps:
        if (
            not isinstance(step, dict)
            or set(step) - _STEP_KEYS
            or not isinstance(step.get("type"), str)
            or step["type"] not in {"WALK", "SUBWAY", "BUS"}
        ):
            return False
        for key, value in step.items():
            if key in _STEP_TEXT_FIELDS and (
                not isinstance(value, str) or len(value) > 300
            ):
                return False
            if key in _STEP_ISO_FIELDS and (
                not isinstance(value, str) or len(value) > 64
            ):
                return False
            if key in {"minutes_until_train_arrives", "minutes_until_arrival"} and (
                not isinstance(value, (int, float))
                or isinstance(value, bool)
                or not math.isfinite(value)
                or not -1440 <= value <= 1440
            ):
                return False
            if key in {"route_total_minutes", "duration_minutes"} and (
                not isinstance(value, (int, float))
                or isinstance(value, bool)
                or not math.isfinite(value)
                or not 0 <= value <= 1440
            ):
                return False
            if key == "route_total_seconds" and (
                not isinstance(value, (int, float))
                or isinstance(value, bool)
                or not math.isfinite(value)
                or not 0 <= value <= 86_400
            ):
                return False
            if key == "distance_meters" and (
                not isinstance(value, (int, float))
                or isinstance(value, bool)
                or not 0 <= value <= 1_000_000
            ):
                return False
            if key == "stop_count" and (
                not isinstance(value, int)
                or isinstance(value, bool)
                or not 0 <= value <= 256
            ):
                return False
            if key == "segment_index" and (
                not isinstance(value, int)
                or isinstance(value, bool)
                or not 0 <= value <= 64
            ):
                return False
            if key == "polyline" and (
                not isinstance(value, dict)
                or set(value) != {"encodedPolyline"}
                or not isinstance(value.get("encodedPolyline"), str)
                or len(value["encodedPolyline"]) > 8192
            ):
                return False
            if key in {"start_point", "end_point", "departure_coords", "arrival_coords"} and not _bounded_point(value):
                return False
            if key == "intermediate_stops" and (
                not isinstance(value, list)
                or len(value) > 64
                or any(not isinstance(item, str) or len(item) > 300 for item in value)
            ):
                return False
            if key == "intermediate_stop_locations" and (
                not isinstance(value, list)
                or len(value) > 64
                or any(not _bounded_stop_location(item) for item in value)
            ):
                return False
    return True


async def enrich_route(request: Request, payload: EnrichRouteRequest):
    """Enrich an alternate on demand without making the initial trip wait."""
    started = time.monotonic()
    gtfs = getattr(request.app.state, "gtfs", None)
    steps = payload.steps or []
    if not _enrichment_steps_are_bounded(steps):
        raise HTTPException(status_code=400, detail="Invalid route enrichment request")
    query_count = getattr(gtfs, "_query_count", 0) if gtfs else 0
    try:
        metrics = await enrichment._enrich_route(gtfs, steps)
    except Exception as exc:
        print(f"[enrich-route] failed, returning un-enriched: {exc!r}")
        return {"steps": steps, "enriched": False}
    print(
        f"[enrich-route] subway_legs={metrics['subway_legs']} bus_legs={metrics['bus_legs']} "
        f"legs_with_stops={metrics['subway_with_stops'] + metrics['bus_with_stops']} "
        f"db_queries={(getattr(gtfs, '_query_count', 0) - query_count) if gtfs else 0} "
        f"total={time.monotonic() - started:.2f}s"
    )
    return {"steps": steps, "enriched": True}
