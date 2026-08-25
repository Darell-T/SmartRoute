"""Bind prepared route rows to one immutable canonical evidence snapshot."""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from app.services.trips.preparation.prepare import AggregatePreparation
from app.services.trips.preparation.constraints import route_constraints
from app.services.trips import scoring
from app.services.trips.itinerary import (
    build_canonical_itinerary,
    build_chained_itinerary,
)


def finalize_aggregate(
    aggregate: AggregatePreparation,
    tool_input: dict[str, Any],
    *,
    snapshot_id: str,
    snapshot_observed_at: str,
) -> AggregatePreparation:
    planning_mode = (
        "arrive_by"
        if tool_input.get("arrival_by")
        else "depart_at"
        if tool_input.get("departure_time")
        else "leave_now"
    )
    origin = aggregate.origin_place.to_event_point()
    itineraries: list[dict[str, Any]] = []
    constraints: list[dict[str, Any]] = []
    finalized_scores: list[dict[str, Any]] = []
    for index, route in enumerate(aggregate.parsed_routes):
        candidate_destination = (
            aggregate.candidate_destinations[index]
            if index < len(aggregate.candidate_destinations)
            else aggregate.destination_place
        )
        destination = candidate_destination.to_event_point()
        segments = (
            aggregate.aggregate_segments[index]
            if index < len(aggregate.aggregate_segments)
            else []
        )
        if segments:
            itinerary = build_chained_itinerary(
                segments,
                origin=origin,
                final_destination=destination,
                planning_mode=planning_mode,
                requested_departure=tool_input.get("departure_time"),
                requested_arrival=tool_input.get("arrival_by"),
                generated_at=snapshot_observed_at,
                snapshot_id=snapshot_id,
                snapshot_observed_at=snapshot_observed_at,
            )
        else:
            itinerary = build_canonical_itinerary(
                route,
                origin=origin,
                destination=destination,
                planning_mode=planning_mode,
                requested_departure=tool_input.get("departure_time"),
                requested_arrival=tool_input.get("arrival_by"),
                generated_at=snapshot_observed_at,
                snapshot_id=snapshot_id,
                snapshot_observed_at=snapshot_observed_at,
            )
        hard = route_constraints(route, tool_input, itinerary=itinerary)
        evidence = (
            aggregate.candidate_evidence[index]
            if index < len(aggregate.candidate_evidence)
            else {}
        )
        candidate_alerts = evidence.get("alerts")
        if not isinstance(candidate_alerts, list):
            candidate_alerts = (
                [] if "alerts" in evidence else list(aggregate.relevant_alerts or [])
            )
        row = scoring.finalized_route_score(
            route=route,
            itinerary=itinerary,
            alerts=candidate_alerts,
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


__all__ = ("finalize_aggregate",)
