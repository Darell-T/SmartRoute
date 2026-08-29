"""Direct REST trip planning for the Live Map surface.

Thin controller: validates the request, admits it, delegates the whole
model-free planning pipeline (routing, normalization, evidence, scoring,
constraints, selection, enrichment, itinerary, recommendation) to
``app.services.trips.direct_plan``, and maps controlled failures to the
established HTTP contract. No conversational SSE, no agent session state,
no advisor/shadow selection, no ``[ROUTE:N]`` control parsing.
"""

import math
import os
import time
import traceback

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict

from app.services import admission
from app.services.trips import direct_plan, enrichment

router = APIRouter()

TRIP_CONTEXT_TIMEOUT_S = float(os.getenv("TRIP_CONTEXT_TIMEOUT_S", "2.0"))


class TripRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    origin_lat: float
    origin_lng: float
    destination: str
    destination_lat: float | None = None
    destination_lng: float | None = None


class EnrichRouteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    steps: list[dict]


_STEP_KEYS = frozenset(
    {
        "type",
        "start_point",
        "end_point",
        "polyline",
        "train_line",
        "line_color",
        "direction",
        "departure_stop",
        "arrival_stop",
        "departure_coords",
        "arrival_coords",
        "minutes_until_train_arrives",
        "minutes_until_arrival",
        "route_total_minutes",
        "stop_count",
        "route_id",
        "intermediate_stops",
        "intermediate_stop_locations",
        "segment_index",
        "duration_minutes",
        "distance_meters",
        "route_total_seconds",
        "departure_time_iso",
        "arrival_time_iso",
    }
)
_STEP_TEXT_FIELDS = frozenset(
    {
        "route_id",
        "train_line",
        "line_color",
        "direction",
        "departure_stop",
        "arrival_stop",
    }
)
_STEP_ISO_FIELDS = frozenset({"departure_time_iso", "arrival_time_iso"})


_ALLOWED_STEP_TYPES = frozenset(
    {
        "WALK",
        "SUBWAY",
        "BUS",
        "RAIL",
        "TRAIN",
        "LIGHT_RAIL",
        "TRAM",
    }
)


def _bounded_text(value: object, limit: int) -> bool:
    return isinstance(value, str) and len(value) <= limit


def _bounded_number(value: object, lo: float, hi: float, *, integer: bool = False) -> bool:
    if integer:
        if not isinstance(value, int) or isinstance(value, bool):
            return False
    elif (
        not isinstance(value, (int, float))
        or isinstance(value, bool)
        or not math.isfinite(value)
    ):
        return False
    return lo <= value <= hi


_POINT_KEYS = {
    frozenset({"lat", "lng"}): ("lat", "lng"),
    frozenset({"latitude", "longitude"}): ("latitude", "longitude"),
}


def _bounded_point(value: object) -> bool:
    if not isinstance(value, dict):
        return False
    keys = _POINT_KEYS.get(frozenset(value))
    if keys is None:
        return False
    return _bounded_number(value[keys[0]], 40.2, 41.2) and _bounded_number(
        value[keys[1]], -74.6, -73.2
    )


def _bounded_stop_location(value: object) -> bool:
    return (
        isinstance(value, dict) and set(value) == {"name", "lat", "lng"}
        and isinstance(value["name"], str) and len(value["name"]) <= 300
        and _bounded_point({"lat": value["lat"], "lng": value["lng"]})
    )


_STEP_FIELD_OK = {
    **dict.fromkeys(_STEP_TEXT_FIELDS, lambda value: _bounded_text(value, 300)),
    **dict.fromkeys(_STEP_ISO_FIELDS, lambda value: _bounded_text(value, 64)),
    "minutes_until_train_arrives": lambda value: _bounded_number(value, -1440, 1440),
    "minutes_until_arrival": lambda value: _bounded_number(value, -1440, 1440),
    "route_total_minutes": lambda value: _bounded_number(value, 0, 1440),
    "duration_minutes": lambda value: _bounded_number(value, 0, 1440),
    "route_total_seconds": lambda value: _bounded_number(value, 0, 86_400),
    "distance_meters": lambda value: _bounded_number(value, 0, 1_000_000),
    "stop_count": lambda value: _bounded_number(value, 0, 256, integer=True),
    "segment_index": lambda value: _bounded_number(value, 0, 64, integer=True),
    "polyline": lambda value: (
        isinstance(value, dict)
        and set(value) == {"encodedPolyline"}
        and isinstance(value.get("encodedPolyline"), str)
        and len(value["encodedPolyline"]) <= 8192
    ),
    "start_point": _bounded_point,
    "end_point": _bounded_point,
    "departure_coords": _bounded_point,
    "arrival_coords": _bounded_point,
    "intermediate_stops": lambda value: (
        isinstance(value, list)
        and len(value) <= 64
        and all(isinstance(item, str) and len(item) <= 300 for item in value)
    ),
    "intermediate_stop_locations": lambda value: (
        isinstance(value, list)
        and len(value) <= 64
        and all(_bounded_stop_location(item) for item in value)
    ),
}


def _enrichment_steps_are_bounded(steps: object) -> bool:
    if not isinstance(steps, list) or len(steps) > 64:
        return False
    for step in steps:
        if (
            not isinstance(step, dict)
            or set(step) - _STEP_KEYS
            or not isinstance(step.get("type"), str)
            or step["type"] not in _ALLOWED_STEP_TYPES
        ):
            return False
        for key, value in step.items():
            checker = _STEP_FIELD_OK.get(key)
            if checker is not None and not checker(value):
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
    except Exception as exc:  # noqa: BLE001 enrichment faults return un-enriched steps
        print(f"[enrich-route] failed, returning un-enriched: {exc!r}")
        return {"steps": steps, "enriched": False}
    print(
        f"[enrich-route] subway_legs={metrics['subway_legs']} bus_legs={metrics['bus_legs']} "
        f"legs_with_stops={metrics['subway_with_stops'] + metrics['bus_with_stops']} "
        f"db_queries={(getattr(gtfs, '_query_count', 0) - query_count) if gtfs else 0} "
        f"total={time.monotonic() - started:.2f}s"
    )
    return {"steps": steps, "enriched": True}


def _finite_or_absent(values: tuple[float | None, ...]) -> bool:
    return not any(
        value is not None
        and (not isinstance(value, (int, float)) or not math.isfinite(value))
        for value in values
    )


def _in_service_area(lat: float, lng: float) -> bool:
    return 40.2 <= lat <= 41.2 and -74.6 <= lng <= -73.2


def _trip_payload_is_bounded(payload: TripRequest) -> bool:
    if not _finite_or_absent(
        (
            payload.origin_lat,
            payload.origin_lng,
            payload.destination_lat,
            payload.destination_lng,
        )
    ):
        return False
    if not _in_service_area(payload.origin_lat, payload.origin_lng):
        return False
    dest_lat, dest_lng = payload.destination_lat, payload.destination_lng
    if (dest_lat is None) != (dest_lng is None):
        return False
    if dest_lat is not None and not _in_service_area(dest_lat, dest_lng):
        return False
    destination = payload.destination
    return (
        isinstance(destination, str)
        and bool(destination.strip())
        and len(destination) <= 300
    )


@router.post("/api/trip")
async def plan_trip(request: Request, payload: TripRequest):
    if not _trip_payload_is_bounded(payload):
        raise HTTPException(status_code=400, detail="Invalid trip request")
    t0 = time.monotonic()
    lease = None
    timings: dict[str, float] = {}
    try:
        try:
            lease = await admission.acquire(
                admission.principal_from_request(
                    request.headers.get("X-SmartRoute-Principal")
                ),
                "trip",
            )
        except admission.AdmissionDenied as exc:
            raise HTTPException(
                status_code=exc.status_code,
                detail=(
                    "Request identity is invalid."
                    if exc.status_code == 403
                    else "Request admission is temporarily unavailable."
                    if exc.status_code == 503
                    else "Too many requests."
                ),
                headers={"Retry-After": str(exc.retry_after_s)},
            ) from None
        gtfs = getattr(request.app.state, "gtfs", None)
        result = await direct_plan.plan_direct_trip(
            gtfs=gtfs,
            origin_lat=payload.origin_lat,
            origin_lng=payload.origin_lng,
            destination=payload.destination,
            destination_lat=payload.destination_lat,
            destination_lng=payload.destination_lng,
            context_timeout_s=TRIP_CONTEXT_TIMEOUT_S,
            timings=timings,
        )
        elapsed = time.monotonic() - t0
        print(
            f"[trip] route={timings.get('route_provider_ms', 0.0) / 1000:.2f}s "
            f"mta={timings.get('mta_ms', 0.0) / 1000:.2f}s "
            f"incidents={timings.get('incident_ms', 0.0) / 1000:.2f}s "
            f"enrich={timings.get('enrichment_ms', 0.0) / 1000:.2f}s "
            f"total={elapsed:.2f}s"
        )
    except direct_plan.DirectTripError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from None
    except HTTPException:
        raise
    except Exception:  # noqa: BLE001 unhandled plan faults stay a generic 500
        # Full detail goes to the server log only; the public 500 stays generic
        # so internal exception text is never exposed to the browser.
        print(f"[trip] UNHANDLED ERROR:\n{traceback.format_exc()}")
        raise HTTPException(status_code=500, detail="Trip planning failed") from None
    else:
        return result
    finally:
        await admission.release(lease)


router.post("/api/trip/enrich-route")(enrich_route)
