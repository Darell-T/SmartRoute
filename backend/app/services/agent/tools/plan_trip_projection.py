"""Route-card, digest, and session projections for ``plan_trip``."""

from __future__ import annotations

import secrets
from typing import Any, Callable

from app.services.agent.quick_escalation import effectively_tied_scores
from app.services.agent.tools._types import ToolContext, ToolResult
from app.services.trips.itinerary import build_canonical_itinerary
from app.services.trips.recommendation_reasons import (
    build_recommendation_reasons,
    format_recommendation_reason,
)
from app.services.trips.selection_decision import build_route_selection_decision


def project_single_leg(
    *,
    tool_input: dict,
    ctx: ToolContext,
    timings: dict[str, float],
    parsed_routes: list[list[dict]],
    origin_raw: str,
    destination_raw: str,
    origin_place: Any,
    destination_place: Any,
    departure_time: str | None,
    arrival_by: str | None,
    excluded: set[str],
    relevant_alerts: list[dict],
    event_evidence_status: str,
    event_impacts: list[dict],
    event_failures: list[str],
    crowd_search_metadata: dict,
    incident_scan_metadata: dict,
    evidence_envelopes: dict[str, Any],
    collect_crowd_evidence: bool,
    chosen_index: int,
    candidate_analysis: list[dict],
    scored: list[dict],
    decision_reason: str,
    selection_log_reason: str,
    scoring_event_impacts: list[dict],
    first_leg_arrival_context: dict | None,
    point_label: Callable[[str], str],
    summary_eta_minutes: Callable[[list[dict], int], int],
    first_boarding_context: Callable[[Any, dict, int], dict],
    candidates_module: Any,
    scoring_module: Any,
    text_module: Any,
    route_card_event: Callable[..., Any],
) -> ToolResult:
    """Build the externally visible representations of one canonical trip."""
    display_candidates = candidates_module._build_route_candidates(
        parsed_routes,
        chosen_index,
        candidate_analysis,
        scored,
    )
    score_by_index = scoring_module._score_by_index(scored)
    selected_score = score_by_index[chosen_index]
    card_ids = [f"rc_{secrets.token_hex(4)}" for _route in parsed_routes]
    selection_decision = build_route_selection_decision(
        selected_index=chosen_index,
        selected_candidate_id=card_ids[chosen_index],
        selected_score=selected_score,
        selection_reason=decision_reason,
        excluded_modes=excluded,
        arrival_by=bool(arrival_by),
        avoid_crowds=collect_crowd_evidence,
        event_evidence_status=event_evidence_status,
        event_impacts=event_impacts,
    )
    print(
        f"[agent-plan_trip] candidates={len(parsed_routes)} selected={chosen_index} "
        f"selected_id={selection_decision['selected_candidate_id']} "
        f"reason={selection_log_reason} event_status={event_evidence_status} "
        f"event_impacts={len(event_impacts)}"
    )
    structured_reasons = build_recommendation_reasons(
        selected_score,
        [
            score
            for index, score in score_by_index.items()
            if index != chosen_index
        ],
    )
    selected_event_impacts = [
        impact for impact in event_impacts if impact.get("route_index") == chosen_index
    ]
    if scoring_event_impacts:
        structured_reasons.append(
            {
                "code": "lower_event_crowd_exposure",
                "event_count": len(selected_event_impacts),
                "provider_status": event_evidence_status,
            }
        )
    canonical_reason_copy = [
        rendered
        for rendered in (
            format_recommendation_reason(reason) for reason in structured_reasons
        )
        if rendered
    ]

    destination_label = point_label(destination_raw)
    origin_point = origin_place.to_event_point()
    destination_point = destination_place.to_event_point()
    origin_point["label"] = (
        point_label(origin_raw) if origin_place.source != "user" else "Your location"
    )
    destination_point["label"] = destination_label
    planning_mode = (
        "arrive_by" if arrival_by else ("depart_at" if departure_time else "leave_now")
    )

    digest: list[dict] = []
    events: list[Any] = []
    session_cards: list[dict] = []
    for index, route in enumerate(parsed_routes):
        card_id = card_ids[index]
        is_recommended = index == chosen_index
        candidate = display_candidates[index]
        lines = candidate["score_breakdown"]["transit_lines"]
        reason = (
            candidate["recommendation_reason"]
            if is_recommended
            else candidate["rejection_reason"]
        )
        if is_recommended and canonical_reason_copy:
            reason = canonical_reason_copy[0]
        alert_headlines = [
            text_module._safe_text(alert.get("header") or "", 80)
            for alert in (relevant_alerts or [])
        ][:3]
        first_step = route[0] if route else {}
        last_step = route[-1] if route else {}
        itinerary = build_canonical_itinerary(
            route,
            origin=origin_point,
            destination=destination_point,
            planning_mode=planning_mode,
            requested_departure=departure_time,
            requested_arrival=str(arrival_by) if arrival_by else None,
            reasons=structured_reasons if is_recommended else [],
            itinerary_id=card_id,
        )
        if is_recommended:
            itinerary["selection_decision"] = selection_decision
        eta_minutes = summary_eta_minutes(route, itinerary["total_duration_seconds"])
        transfers = int(itinerary["transfer_count"])
        walk_minutes = round(int(itinerary["total_walk_seconds"]) / 60)
        route_event_impacts = [
            {
                "event_name": impact.get("title"),
                "venue_name": impact.get("venue"),
                "exposure_window": impact.get("exposure_window"),
                "distance_meters": impact.get("distance_meters"),
                "risk_score": impact.get("risk_score"),
                "confidence": impact.get("confidence"),
                "source_class": impact.get("source_class"),
                "verification_tier": impact.get("verification_tier"),
                "scoring_authorized": impact.get("scoring_authorized"),
            }
            for impact in event_impacts
            if impact.get("route_index") == index
        ][:3]
        digest.append(
            {
                "card_id": card_id,
                "lines": lines,
                "eta_minutes": eta_minutes,
                "transfers": transfers,
                "departs_iso": first_step.get("departure_time_iso"),
                "arrives_iso": last_step.get("arrival_time_iso"),
                "walk_minutes": walk_minutes,
                "alert_headlines": alert_headlines,
                "reason": reason,
                "structured_recommendation_reasons": (
                    structured_reasons if is_recommended else []
                ),
                "event_evidence_status": event_evidence_status,
                "event_crowd_penalty": score_by_index[index].get(
                    "event_crowd_penalty",
                    0,
                ),
                "event_impacts": route_event_impacts,
                "first_leg_arrival": (
                    first_leg_arrival_context if is_recommended else None
                ),
            }
        )
        summary = {
            "eta_minutes": eta_minutes,
            "transfers": transfers,
            "lines": lines,
            "reason": reason
            or (canonical_reason_copy[0] if canonical_reason_copy else None),
            "event_evidence_status": event_evidence_status,
            "first_leg_arrival": (
                first_leg_arrival_context if is_recommended else None
            ),
        }
        events.append(
            route_card_event(
                card_id=card_id,
                turn_id=ctx.turn_id,
                role="recommended" if is_recommended else "alternative",
                origin=origin_point,
                destination=destination_point,
                depart_iso=departure_time,
                summary=summary,
                route=route,
                alerts=relevant_alerts,
                itinerary=itinerary,
                selection_decision=selection_decision,
            )
        )
        first_transit = next(
            (step for step in route if step.get("type") in {"SUBWAY", "BUS"}),
            None,
        )
        initial_walk_seconds = 0
        for leg in itinerary.get("legs") or []:
            if str(leg.get("mode") or "").upper() != "WALK":
                break
            initial_walk_seconds += int(leg.get("walk_seconds") or 0)
        session_cards.append(
            {
                "card_id": card_id,
                "role": "recommended" if is_recommended else "alternative",
                "lines": lines,
                "eta_minutes": eta_minutes,
                "destination": destination_point,
                "first_boarding": (
                    first_boarding_context(
                        ctx.gtfs,
                        first_transit,
                        round(initial_walk_seconds / 60),
                    )
                    if first_transit
                    else None
                ),
                "selection_decision": selection_decision,
            }
        )

    recommended_lines = digest[chosen_index]["lines"]
    return ToolResult(
        ok=True,
        data={
            "candidates": digest,
            "event_evidence": {
                "status": event_evidence_status,
                "impact_count": len(event_impacts),
                "provider_failure_count": len(event_failures),
                "search": crowd_search_metadata,
            },
            "incident_evidence": {
                key: incident_scan_metadata[key]
                for key in ("status", "scanned_at", "cache_hit", "sources")
                if key in incident_scan_metadata
            },
            "evidence": {
                name: {
                    **envelope.to_model_dict(empty=[]),
                    "payload": {"count": len(envelope.current_payload() or [])},
                }
                for name, envelope in sorted(evidence_envelopes.items())
            },
            "selected_route_index": chosen_index,
            "selection_decision": selection_decision,
            **(
                {"quick_escalation_reason": "effectively_tied_final_scores"}
                if effectively_tied_scores(scored)
                else {}
            ),
        },
        summary=(
            f"found {len(parsed_routes)} route(s) to {destination_label}; "
            f"recommended {'/'.join(recommended_lines) or 'a walking route'}"
        ),
        events=events,
        session_route_cards=session_cards,
        timings=timings,
    )
