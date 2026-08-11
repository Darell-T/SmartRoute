"""Direct Live Map trip planning: shared preparation, deterministic selection.

The non-conversational ``POST /api/trip`` endpoint consumes the same
model-free canonical pipeline as the agent path (``prepare_single_leg``):
routing, semantic transfer normalization, MTA context, incident-index
evidence, crowd evidence, and scoring. This module owns orchestration: the
deterministic hard-valid selection, chosen-route enrichment, and the top-level
response contract. Candidate/itinerary/recommendation projection lives in
``direct_plan_projection``. No model, advisor, shadow, or ``[ROUTE:N]``
control parsing is involved.
"""

from __future__ import annotations

import asyncio
import os
import time
from datetime import datetime, timezone
from typing import Any

from app.services.agent.tools._location import ResolvedPlace
from app.services.agent.tools._types import ToolContext, ToolResult
from app.services.agent.tools.plan_trip_dependencies import (
    build_preparation_dependencies,
)
from app.services.agent.tools.plan_trip_prepare import prepare_single_leg
from app.services.agent.tools.route_option_assembly import route_constraints
from app.services.trips import direct_plan_projection, enrichment
from app.services.trips.direct_plan_projection import (
    NEUTRAL_RECOMMENDATION_FALLBACK,
    project_route_candidates,
)
from app.services.trips.incidents import INCOMPLETE_INCIDENT_DISCLOSURE


class DirectTripError(Exception):
    """Controlled, rider-safe direct trip failure with an HTTP mapping.

    The router translates this into the public HTTP response. Details are
    rider-safe and never expose provider payloads or internal reasoning.
    """

    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


DIRECT_TRIP_DEADLINE_S = float(os.getenv("DIRECT_TRIP_DEADLINE_S", "15.0"))


def _translate_prepare_error(error: str) -> DirectTripError:
    """Map shared-preparation failures to the established REST status codes."""
    message = str(error or "").strip()
    lowered = message.casefold()
    if (
        not message
        or "no transit route found" in lowered
        or "could not find that destination" in lowered
        or "address not found" in lowered
    ):
        return DirectTripError(404, "No route found")
    if "temporarily unavailable" in lowered:
        return DirectTripError(
            503, "Destination lookup is temporarily unavailable."
        )
    if "routing failed (" in message:
        code = message.split("routing failed (", 1)[1].rstrip(")").strip()
        if code == "timeout":
            return DirectTripError(503, "Google Routes API timed out")
        if code == "not_configured":
            return DirectTripError(500, "Routing provider is not configured")
        if code.startswith("http_"):
            return DirectTripError(
                502, f"Upstream routing provider error ({code})"
            )
        if code == "request_failed":
            return DirectTripError(
                502, "Upstream routing provider network error"
            )
        if code == "invalid_json":
            return DirectTripError(
                502, "Upstream routing provider returned invalid data"
            )
        return DirectTripError(502, f"Upstream routing provider error ({code})")
    return DirectTripError(404, "No route found")


def _resolved_places(
    origin_lat: float,
    origin_lng: float,
    destination: str,
    destination_lat: float | None,
    destination_lng: float | None,
) -> tuple[ResolvedPlace, ResolvedPlace | None]:
    """Exact server-owned places from REST coordinates when supplied.

    When destination coordinates are absent the destination is left for the
    shared named-destination resolution inside ``prepare_single_leg``.
    """
    origin = ResolvedPlace(
        name="Your location",
        latitude=origin_lat,
        longitude=origin_lng,
        source="user",
    )
    destination_place = None
    if destination_lat is not None and destination_lng is not None:
        destination_place = ResolvedPlace(
            name=str(destination or "").strip() or "Selected destination",
            latitude=destination_lat,
            longitude=destination_lng,
            source="gps",
        )
    return origin, destination_place


def _tool_input(destination: str) -> dict[str, Any]:
    return {
        "origin": "user",
        "destination": str(destination or "").strip(),
        "routing_preference": "FEWER_TRANSFERS",
        # The direct endpoint opts out of live web/X crowd research; the
        # shared path still applies cached event evidence and the bounded
        # Ticketmaster lookup when a route touches a curated venue hotspot.
        "crowd_search_mode": "off",
    }


def _select_first_valid(
    parsed_routes: list[list[dict]],
    scored: list[dict],
    tool_input: dict[str, Any],
) -> tuple[int | None, str | None]:
    """First hard-valid candidate in the deterministic scored ordering.

    ``scored`` is already ordered by ``(score, total_minutes, transfers,
    index)``. The top-scored route gets ``lowest_final_score``; a lower-ranked
    candidate chosen because better-scored routes violate hard constraints
    gets ``hard_constraint``. Never fabricates a winner.
    """
    for rank, row in enumerate(scored):
        index = row.get("index")
        if not isinstance(index, int) or isinstance(index, bool):
            continue
        if not 0 <= index < len(parsed_routes):
            continue
        constraints = route_constraints(parsed_routes[index], tool_input)
        if constraints.get("satisfied"):
            reason = "lowest_final_score" if rank == 0 else "hard_constraint"
            return index, reason
    return None, None


async def _plan_direct_trip_once(
    *,
    gtfs: Any,
    origin_lat: float,
    origin_lng: float,
    destination: str,
    destination_lat: float | None,
    destination_lng: float | None,
    context_timeout_s: float | None = None,
    timings: dict[str, float],
) -> dict:
    """Plan one direct Live Map trip through the shared model-free pipeline."""
    started = time.monotonic()
    origin_place, destination_place = _resolved_places(
        origin_lat,
        origin_lng,
        destination,
        destination_lat,
        destination_lng,
    )
    tool_input = _tool_input(destination)
    ctx = ToolContext(
        gtfs=gtfs,
        session={},
        session_id="",
        turn_id="",
        now_et=datetime.now(timezone.utc).isoformat(),
        origin={"lat": origin_lat, "lng": origin_lng},
        telemetry={},
    )
    dependencies = build_preparation_dependencies(
        context_timeout_seconds=context_timeout_s,
    )
    prepare_timings: dict[str, float] = {}
    prepared = await prepare_single_leg(
        tool_input,
        ctx,
        prepare_timings,
        dependencies=dependencies,
        emit_comparing_progress=False,
        resolved_origin=origin_place,
        resolved_destination=destination_place,
    )
    if isinstance(prepared, ToolResult):
        raise _translate_prepare_error(prepared.error or "No route found")

    chosen_index, selection_reason = _select_first_valid(
        prepared.parsed_routes,
        prepared.scored,
        tool_input,
    )
    if chosen_index is None:
        raise DirectTripError(404, "No route found")

    enrichment_started = time.monotonic()
    chosen_route = prepared.parsed_routes[chosen_index]
    await enrichment._enrich_route(gtfs, chosen_route)
    timings["enrichment_ms"] = (time.monotonic() - enrichment_started) * 1000

    route_candidates, recommendation, selection_decision = project_route_candidates(
        parsed_routes=prepared.parsed_routes,
        chosen_index=chosen_index,
        scored=prepared.scored,
        origin_place=prepared.origin_place,
        destination_place=prepared.destination_place,
        incident_scan_metadata=prepared.incident_scan_metadata,
        selection_reason=selection_reason,
        event_evidence_status=prepared.event_evidence_status,
        event_impacts=prepared.event_impacts,
    )
    for key in (
        "place_resolution_ms",
        "route_provider_ms",
        "mta_ms",
        "incident_ms",
        "scoring_ms",
        "ticketmaster_ms",
    ):
        timings[key] = prepare_timings.get(key, 0.0)
    timings["total_ms"] = (time.monotonic() - started) * 1000
    return {
        "recommendation": recommendation,
        "route": chosen_route,
        "selected_route_index": chosen_index,
        "route_candidates": route_candidates,
        "alerts": prepared.relevant_alerts,
        "selection_decision": selection_decision,
    }


async def plan_direct_trip(
    *,
    gtfs: Any,
    origin_lat: float,
    origin_lng: float,
    destination: str,
    destination_lat: float | None,
    destination_lng: float | None,
    context_timeout_s: float | None = None,
    timings: dict[str, float] | None = None,
) -> dict:
    """Bound the whole direct trip so multiplied provider retries cannot stall a request."""
    resolved_timings = timings if timings is not None else {}
    try:
        return await asyncio.wait_for(
            _plan_direct_trip_once(
                gtfs=gtfs,
                origin_lat=origin_lat,
                origin_lng=origin_lng,
                destination=destination,
                destination_lat=destination_lat,
                destination_lng=destination_lng,
                context_timeout_s=context_timeout_s,
                timings=resolved_timings,
            ),
            timeout=DIRECT_TRIP_DEADLINE_S,
        )
    except asyncio.TimeoutError as exc:
        raise DirectTripError(503, "Trip planning is temporarily unavailable.") from exc


__all__ = (
    "DirectTripError",
    "INCOMPLETE_INCIDENT_DISCLOSURE",
    "NEUTRAL_RECOMMENDATION_FALLBACK",
    "plan_direct_trip",
)
