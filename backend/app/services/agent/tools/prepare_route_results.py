"""Candidate result construction for the prepared-route tool surface.

Converts a prepared leg into an aggregate candidate set, looks up
per-candidate evidence, and builds non-fatal empty candidate results when
coverage is insufficient, so prepare_route_options stays focused on
orchestration.
"""

from __future__ import annotations

import time
from typing import Any

from app.services.agent import candidate_store
from app.services.agent import trip_state as trip_state_module
from app.services.agent.tools._types import ToolContext, ToolResult
from app.services.agent.tools.plan_trip_prepare import PreparedLeg
from app.services.agent.tools.route_option_assembly import AggregatePreparation
from app.services.agent.tools.route_option_evidence import (
    candidate_evidence_for_route,
    coverage_for_prepared,
)


def as_aggregate(
    prepared: PreparedLeg | AggregatePreparation,
) -> AggregatePreparation:
    if isinstance(prepared, AggregatePreparation):
        return prepared
    coverage = coverage_for_prepared(prepared)
    return AggregatePreparation(
        parsed_routes=prepared.parsed_routes,
        scored=prepared.scored,
        aggregate_segments=[],
        origin_place=prepared.origin_place,
        destination_place=prepared.destination_place,
        relevant_alerts=prepared.relevant_alerts,
        event_impacts=prepared.event_impacts,
        event_failures=prepared.event_failures,
        event_evidence_status=prepared.event_evidence_status,
        incident_scan_metadata=prepared.incident_scan_metadata,
        evidence_envelopes=prepared.evidence_envelopes,
        crowd_search_metadata=prepared.crowd_search_metadata,
        collect_crowd_evidence=prepared.collect_crowd_evidence,
        incidents=prepared.incidents,
        coverage=coverage,
        timings=prepared.timings,
        candidate_evidence=[
            candidate_evidence_for_route(
                prepared,
                route_index=index,
                aggregate_index=index,
            )
            for index in range(len(prepared.parsed_routes))
        ],
    )


def candidate_evidence(
    aggregate: AggregatePreparation,
    index: int,
) -> dict[str, Any]:
    if index < len(aggregate.candidate_evidence):
        return aggregate.candidate_evidence[index]
    return {
        "alerts": aggregate.relevant_alerts,
        "incidents": aggregate.incidents,
        "event_impacts": [
            impact
            for impact in aggregate.event_impacts
            if impact.get("route_index") == index
        ],
    }


def nonfatal_prepare_result(
    result: ToolResult,
    tool_input: dict,
    ctx: ToolContext,
    started: float,
) -> ToolResult:
    message = str(result.error or "route coverage is insufficient")
    message_lower = message.casefold()
    if not any(token in message_lower for token in ("no transit route", "no route", "no transit modes", "coverage")):
        return result
    route_status = (
        "no_hard_constraint_match"
        if "no transit modes" in message_lower
        else "insufficient_coverage"
    )
    session_id = str(getattr(ctx, "session_id", None) or "").strip()
    set_id = candidate_store.store_candidate_set(
        session_id=session_id,
        payload={
            "tool_input": tool_input,
            "candidates": [],
            "route_status": route_status,
            "scenario_mode": tool_input.get("scenario", "active"),
            "evidence_coverage": {"routes": "unavailable"},
        },
    )
    if isinstance(ctx.session, dict):
        if tool_input.get("scenario") == "what_if":
            trip_state_module.bind_temporary_candidate_set(ctx.session, set_id)
        else:
            # A non-presentable active preparation must not move the accepted
            # canonical selection (same invariant as the aggregate no-good
            # path in prepare_route_options.execute): keep the accepted route
            # facts, active candidate set, and selected candidate bound, and
            # store the new set only as a separate audit record. Only an
            # obsolete what-if scenario is discarded for the new active
            # request; the audit set is never bound as active.
            trip_state_module.discard_scenario(ctx.session)
    return ToolResult(
        ok=True,
        data={
            "candidate_set_id": set_id,
            "route_status": route_status,
            "presentation_allowed": False,
            "candidates": [],
            "evidence_coverage": {"routes": "unavailable"},
        },
        summary=message,
        timings={"plan_trip_ms": (time.monotonic() - started) * 1000},
    )


__all__ = ("as_aggregate", "candidate_evidence", "nonfatal_prepare_result")
