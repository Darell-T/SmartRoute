"""Candidate, itinerary, and recommendation projection for the direct path.

``app.services.trips.direct_plan`` owns orchestration, error translation,
place resolution, and deterministic selection; this module owns the cohesive
projection responsibility: REST candidates, canonical itineraries, the rider
recommendation, and the single canonical ``selection_decision`` for the
selected candidate. Pure projection with no provider calls or orchestration.
"""

from __future__ import annotations

from app.services.agent.tools._location import ResolvedPlace
from app.services.trips import candidates, scoring, text
from app.services.trips.incidents import (
    INCOMPLETE_INCIDENT_DISCLOSURE,
    incident_scan_is_complete,
)
from app.services.trips.itinerary import build_canonical_itinerary
from app.services.trips.recommendation_reasons import (
    build_recommendation_reasons,
    format_recommendation_reason,
)
from app.services.trips.selection_decision import build_route_selection_decision

NEUTRAL_RECOMMENDATION_FALLBACK = (
    "Recommended as the best valid route for this trip."
)


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
    """Build REST candidates + canonical itineraries + decision + recommendation.

    Exactly one canonical ``selection_decision`` is built for the selected
    candidate, returned for the top-level contract, and attached to that
    candidate's itinerary only. Alternates never claim they were selected.
    """
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


__all__ = ("NEUTRAL_RECOMMENDATION_FALLBACK", "project_route_candidates")
