"""Bind prepared route rows to one immutable canonical evidence snapshot."""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from app.services.trips import scoring
from app.services.trips.itinerary import (
    build_canonical_itinerary,
    build_chained_itinerary,
)
from app.services.trips.preparation.constraints import route_constraints
from app.services.trips.preparation.prepare import AggregatePreparation


def finalize_aggregate(
    aggregate: AggregatePreparation,
    tool_input: dict[str, Any],
    *,
    snapshot_id: str,
    snapshot_observed_at: str,
) -> AggregatePreparation:
    planning_mode = _planning_mode(tool_input)
    origin = aggregate.origin_place.to_event_point()
    itineraries: list[dict[str, Any]] = []
    constraints: list[dict[str, Any]] = []
    finalized_scores: list[dict[str, Any]] = []
    for index, route in enumerate(aggregate.parsed_routes):
        itinerary, hard, row = _finalize_candidate(
            aggregate,
            tool_input,
            index,
            route,
            planning_mode=planning_mode,
            origin=origin,
            snapshot_id=snapshot_id,
            snapshot_observed_at=snapshot_observed_at,
        )
        itineraries.append(itinerary)
        constraints.append(hard)
        finalized_scores.append(row)
    return replace(
        aggregate,
        scored=finalized_scores,
        candidate_itineraries=itineraries,
        candidate_constraints=constraints,
        snapshot_id=snapshot_id,
        snapshot_observed_at=snapshot_observed_at,
        finalized=True,
    )


def _planning_mode(tool_input: dict[str, Any]) -> str:
    if tool_input.get("arrival_by"):
        return "arrive_by"
    if tool_input.get("departure_time"):
        return "depart_at"
    return "leave_now"


def _finalize_candidate(
    aggregate: AggregatePreparation,
    tool_input: dict[str, Any],
    index: int,
    route: list[dict],
    *,
    planning_mode: str,
    origin: dict[str, Any],
    snapshot_id: str,
    snapshot_observed_at: str,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    destination_place = (
        aggregate.candidate_destinations[index]
        if index < len(aggregate.candidate_destinations)
        else aggregate.destination_place
    )
    destination = destination_place.to_event_point()
    itinerary = _itinerary_for_candidate(
        aggregate,
        index,
        route,
        origin=origin,
        destination=destination,
        planning_mode=planning_mode,
        tool_input=tool_input,
        snapshot_id=snapshot_id,
        snapshot_observed_at=snapshot_observed_at,
    )
    hard = route_constraints(route, tool_input, itinerary=itinerary)
    evidence = (
        aggregate.candidate_evidence[index]
        if index < len(aggregate.candidate_evidence)
        else {}
    )
    row = scoring.finalized_route_score(
        route=route,
        itinerary=itinerary,
        alerts=_alerts_for_candidate(evidence, aggregate.relevant_alerts),
        incidents=list(evidence.get("incidents") or []),
        vehicle_claims=list(evidence.get("unconfirmed_material_claims") or []),
        event_impacts=list(evidence.get("event_impacts") or []),
        route_index=index,
        routing_preference=str(
            tool_input.get("routing_preference") or "FEWER_TRANSFERS"
        ),
        preferred_modes=list(tool_input.get("preferred_modes") or []),
        hard_constraints=hard,
        evidence_coverage=dict(evidence.get("evidence_coverage") or {}),
    )
    row["index"] = index
    row["evidence_snapshot"] = {
        "id": snapshot_id,
        "observed_at": snapshot_observed_at,
    }
    return itinerary, hard, row


def _itinerary_for_candidate(
    aggregate: AggregatePreparation,
    index: int,
    route: list[dict],
    *,
    origin: dict[str, Any],
    destination: dict[str, Any],
    planning_mode: str,
    tool_input: dict[str, Any],
    snapshot_id: str,
    snapshot_observed_at: str,
) -> dict[str, Any]:
    segments = (
        aggregate.aggregate_segments[index]
        if index < len(aggregate.aggregate_segments)
        else []
    )
    shared = {
        "origin": origin,
        "planning_mode": planning_mode,
        "requested_departure": tool_input.get("departure_time"),
        "requested_arrival": tool_input.get("arrival_by"),
        "generated_at": snapshot_observed_at,
        "snapshot_id": snapshot_id,
        "snapshot_observed_at": snapshot_observed_at,
    }
    if segments:
        return build_chained_itinerary(
            segments,
            final_destination=destination,
            **shared,
        )
    return build_canonical_itinerary(route, destination=destination, **shared)


def _alerts_for_candidate(evidence: dict[str, Any], fallback_alerts: list) -> list:
    candidate_alerts = evidence.get("alerts")
    if isinstance(candidate_alerts, list):
        return candidate_alerts
    if "alerts" in evidence:
        return []
    return list(fallback_alerts or [])


__all__ = ("finalize_aggregate",)
