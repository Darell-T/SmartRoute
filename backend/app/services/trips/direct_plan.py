"""Direct Live Map trip planning: shared preparation, deterministic selection.

The non-conversational ``POST /api/trip`` endpoint consumes the same
model-free canonical pipeline as the agent path (``prepare_single_leg``):
routing, semantic transfer normalization, MTA context, incident-index
evidence, crowd evidence, and scoring. This module owns orchestration, the
deterministic hard-valid selection, chosen-route enrichment, canonical
candidate/itinerary/recommendation projection, and the top-level response
contract. No model, advisor, shadow, or ``[ROUTE:N]`` control parsing is
involved.
"""

from __future__ import annotations

import asyncio
import os
import time
from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Any

from app.services.trips import candidates, enrichment, scoring, text
from app.services.trips.itinerary import build_canonical_itinerary
from app.services.trips.location import ResolvedPlace
from app.services.trips.preparation.constraints import route_constraints
from app.services.trips.preparation.context import (
    RoutePreparationContext,
    is_route_preparation_failure,
)
from app.services.trips.preparation.dependencies import (
    build_preparation_dependencies,
)
from app.services.trips.preparation.prepare import prepare_single_leg
from app.services.trips.route_incidents.scan import (
    INCOMPLETE_INCIDENT_DISCLOSURE,
    incident_scan_is_complete,
)
from app.services.trips.selection_record import build_route_selection_decision


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
        return DirectTripError(503, "Destination lookup is temporarily unavailable.")
    if "routing failed (" in message:
        code = message.split("routing failed (", 1)[1].rstrip(")").strip()
        if code == "timeout":
            return DirectTripError(503, "Google Routes API timed out")
        if code == "not_configured":
            return DirectTripError(500, "Routing provider is not configured")
        if code.startswith("http_"):
            return DirectTripError(502, f"Upstream routing provider error ({code})")
        if code == "request_failed":
            return DirectTripError(502, "Upstream routing provider network error")
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


NEUTRAL_RECOMMENDATION_FALLBACK = (
    "Recommended as the best valid route for this trip."
)


def build_recommendation_reasons(
    selected_score: dict[str, Any],
    alternative_scores: Iterable[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Return supported, deterministic facts about the selected candidate."""
    alternatives = [score for score in alternative_scores if isinstance(score, dict)]
    if not alternatives:
        return []

    reasons: list[dict[str, Any]] = []
    selected_minutes = _nonnegative_int(selected_score.get("total_minutes"))
    alternative_minutes = [
        _nonnegative_int(score.get("total_minutes")) for score in alternatives
    ]
    next_best_minutes = min(alternative_minutes, default=selected_minutes)
    if selected_minutes <= next_best_minutes:
        reasons.append(
            {
                "code": "fastest",
                "duration_minutes": selected_minutes,
                "difference_seconds": max(0, next_best_minutes - selected_minutes) * 60,
            }
        )

    selected_walk = _walking_minutes(selected_score)
    alternative_walks = [_walking_minutes(score) for score in alternatives]
    best_alternative_walk = min(alternative_walks, default=selected_walk)
    if (
        _nonnegative_number(selected_score.get("walking_penalty")) > 0
        and selected_walk < best_alternative_walk
    ):
        reasons.append(
            {
                "code": "less_walking",
                "walking_minutes": selected_walk,
                "walking_difference": best_alternative_walk - selected_walk,
            }
        )

    selected_transfers = _nonnegative_int(selected_score.get("transfers"))
    best_alternative_transfers = min(
        (_nonnegative_int(score.get("transfers")) for score in alternatives),
        default=selected_transfers,
    )
    if selected_transfers < best_alternative_transfers:
        reasons.append(
            {
                "code": "fewer_transfers",
                "transfer_difference": best_alternative_transfers - selected_transfers,
            }
        )

    selected_alerts = scoring.alert_penalty_from_score(selected_score)
    if any(
        scoring.alert_penalty_from_score(score) > selected_alerts
        for score in alternatives
    ):
        reasons.append({"code": "avoids_active_disruption"})

    selected_event_penalty = _nonnegative_number(
        selected_score.get("event_crowd_penalty")
    )
    best_alternative_event_penalty = min(
        _nonnegative_number(score.get("event_crowd_penalty"))
        for score in alternatives
    )
    if selected_event_penalty < best_alternative_event_penalty:
        reasons.append(
            {
                "code": "lower_event_crowd_exposure",
                "event_penalty_difference": (
                    best_alternative_event_penalty - selected_event_penalty
                ),
            }
        )

    # An explicit optimization preference is more useful than a raw time
    # comparison when explaining why a slightly slower route won.
    reasons.sort(
        key=lambda reason: 0 if reason.get("code") == "less_walking" else 1
    )
    return reasons


def format_recommendation_reason(reason: object) -> str | None:
    """Format one supported fact for legacy string consumers only."""
    if not isinstance(reason, dict):
        return None
    code = reason.get("code")
    if code == "fastest":
        seconds = _nonnegative_int(reason.get("difference_seconds"))
        duration = _nonnegative_int(reason.get("duration_minutes"))
        if seconds >= 60:
            suffix = f" ({duration} min total)" if duration else ""
            return f"About {round(seconds / 60)} min faster than the next option{suffix}."
        return f"Fastest available route at {duration} min." if duration else "Fastest available route."
    if code == "less_walking":
        difference = _nonnegative_int(reason.get("walking_difference"))
        minutes = _nonnegative_int(reason.get("walking_minutes"))
        if difference:
            unit = "minute" if difference == 1 else "minutes"
            return f"Uses {difference} fewer {unit} of walking ({minutes} min on foot)."
        return f"Prioritizes less walking ({minutes} min on foot)."
    if code == "fewer_transfers":
        difference = _nonnegative_int(reason.get("transfer_difference"))
        if difference:
            unit = "transfer" if difference == 1 else "transfers"
            return f"Uses {difference} fewer {unit}."
    if code == "avoids_active_disruption":
        return "Avoids active service alerts on another option."
    if code == "lower_event_crowd_exposure":
        return "Avoids heavier event crowd exposure on another option."
    return None


def _nonnegative_int(value: object) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


def _nonnegative_number(value: object) -> float:
    try:
        return max(0.0, float(value))
    except (TypeError, ValueError):
        return 0.0


def _walking_minutes(score: dict[str, Any]) -> int:
    raw = score.get("walk_minutes")
    if isinstance(raw, (int, float)) and not isinstance(raw, bool):
        return max(0, round(raw))
    seconds = score.get("street_walking_seconds")
    if isinstance(seconds, (int, float)) and not isinstance(seconds, bool):
        return max(0, round(seconds / 60))
    return 0


def project_route_candidates(
    *,
    parsed_routes: list[list[dict]],
    chosen_index: int,
    scored: list[dict],
    origin_place: ResolvedPlace,
    destination_place: ResolvedPlace,
    incident_scan_metadata: dict,
    selection_reason: str,
    event_evidence_status: str,
    event_impacts: list[dict],
) -> tuple[list[dict], str, dict]:
    """Build REST candidates, canonical itineraries, and selection facts."""
    route_candidates = candidates._build_route_candidates(
        parsed_routes,
        chosen_index,
        {},
        scored,
    )
    score_by_index = scoring._score_by_index(scored)
    chosen_score = score_by_index.get(chosen_index, {})
    origin_point = {
        "label": origin_place.name,
        "lat": origin_place.latitude,
        "lng": origin_place.longitude,
    }
    destination_point = {
        "label": destination_place.name,
        "lat": destination_place.latitude,
        "lng": destination_place.longitude,
    }
    structured_reasons = build_recommendation_reasons(
        chosen_score,
        [
            score
            for index, score in score_by_index.items()
            if index != chosen_index
        ],
    )
    rendered_reasons = [
        rendered
        for rendered in (
            format_recommendation_reason(reason) for reason in structured_reasons
        )
        if rendered
    ]
    recommendation = text._sanitize_recommendation(
        rendered_reasons[0] if rendered_reasons else NEUTRAL_RECOMMENDATION_FALLBACK
    )
    if not incident_scan_is_complete(incident_scan_metadata):
        recommendation = f"{recommendation} {INCOMPLETE_INCIDENT_DISCLOSURE}"
    selection_decision = build_route_selection_decision(
        selected_index=chosen_index,
        selected_candidate_id=f"candidate-{chosen_index}",
        selected_score=chosen_score,
        selection_reason=selection_reason,
        excluded_modes=set(),
        arrival_by=False,
        avoid_crowds=False,
        event_evidence_status=event_evidence_status,
        event_impacts=event_impacts,
    )

    for index, candidate in enumerate(route_candidates):
        route = candidate.get("steps") or []
        if index == chosen_index and recommendation:
            candidate["recommendation_reason"] = recommendation
        itinerary = build_canonical_itinerary(
            route,
            origin=origin_point,
            destination=destination_point,
            reasons=structured_reasons if index == chosen_index else [],
            itinerary_id=str(candidate.get("id") or "") or None,
        )
        if index == chosen_index:
            itinerary["selection_decision"] = selection_decision
        candidate["itinerary"] = itinerary
        candidate["structured_recommendation_reasons"] = (
            structured_reasons if index == chosen_index else []
        )
        candidate["total_minutes"] = max(
            0, round(int(itinerary["total_duration_seconds"]) / 60)
        )
        candidate.setdefault("score_breakdown", {})["transfers"] = int(
            itinerary["transfer_count"]
        )
        if itinerary.get("arrival_at"):
            candidate["arrival_at"] = itinerary["arrival_at"]
    return route_candidates, recommendation, selection_decision


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
    ctx = RoutePreparationContext(
        gtfs=gtfs,
        session={},
        session_id="",
        turn_id="",
        now_et=datetime.now(UTC).isoformat(),
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
    if is_route_preparation_failure(prepared):
        raise _translate_prepare_error(getattr(prepared, "error", None) or "No route found")

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
    except TimeoutError as exc:
        raise DirectTripError(503, "Trip planning is temporarily unavailable.") from exc


__all__ = (
    "INCOMPLETE_INCIDENT_DISCLOSURE",
    "NEUTRAL_RECOMMENDATION_FALLBACK",
    "DirectTripError",
    "build_recommendation_reasons",
    "format_recommendation_reason",
    "plan_direct_trip",
    "project_route_candidates",
)
