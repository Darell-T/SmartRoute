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

from app.routers.trip_enrichment import (
    EnrichRouteRequest,
    _enrichment_steps_are_bounded,
    enrich_route,
)
from app.services import admission
from app.services.trips import direct_plan

router = APIRouter()

TRIP_CONTEXT_TIMEOUT_S = float(os.getenv("TRIP_CONTEXT_TIMEOUT_S", "2.0"))


class TripRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    origin_lat: float
    origin_lng: float
    destination: str
    destination_lat: float | None = None
    destination_lng: float | None = None


def _trip_payload_is_bounded(payload: TripRequest) -> bool:
    coordinates = (
        payload.origin_lat,
        payload.origin_lng,
        payload.destination_lat,
        payload.destination_lng,
    )
    if any(
        value is not None
        and (not isinstance(value, (int, float)) or not math.isfinite(value))
        for value in coordinates
    ):
        return False
    if not (40.2 <= payload.origin_lat <= 41.2 and -74.6 <= payload.origin_lng <= -73.2):
        return False
    if (payload.destination_lat is None) != (payload.destination_lng is None):
        return False
    if payload.destination_lat is not None and not (
        40.2 <= payload.destination_lat <= 41.2
        and -74.6 <= payload.destination_lng <= -73.2
    ):
        return False
    return (
        isinstance(payload.destination, str)
        and bool(payload.destination.strip())
        and len(payload.destination) <= 300
    )


@router.post("/api/trip")
async def plan_trip(request: Request, payload: TripRequest):
    t0 = time.monotonic()
    lease = None
    timings: dict[str, float] = {}
    try:
        if not _trip_payload_is_bounded(payload):
            raise HTTPException(status_code=400, detail="Invalid trip request")
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
        # Single per-trip log line: stage durations + total.
        print(
            f"[trip] route={timings.get('route_provider_ms', 0.0) / 1000:.2f}s "
            f"mta={timings.get('mta_ms', 0.0) / 1000:.2f}s "
            f"incidents={timings.get('incident_ms', 0.0) / 1000:.2f}s "
            f"enrich={timings.get('enrichment_ms', 0.0) / 1000:.2f}s "
            f"total={elapsed:.2f}s"
        )
        return result
    except direct_plan.DirectTripError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from None
    except HTTPException:
        raise
    except Exception:
        # Full detail goes to the server log only; the public 500 stays generic
        # so internal exception text is never exposed to the browser.
        print(f"[trip] UNHANDLED ERROR:\n{traceback.format_exc()}")
        raise HTTPException(status_code=500, detail="Trip planning failed")
    finally:
        await admission.release(lease)


router.post("/api/trip/enrich-route")(enrich_route)
