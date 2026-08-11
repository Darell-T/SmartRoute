"""Single-leg planning execution for the ``plan_trip`` compatibility facade."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Callable

from app.services.agent.tools._types import ToolContext, ToolResult
from app.services.agent.tools.plan_trip_advisor import select_agent_route
from app.services.agent.tools.plan_trip_first_leg import first_leg_arrival_context
from app.services.agent.tools.plan_trip_prepare import (
    PreparationDependencies,
    prepare_single_leg,
)
from app.services.agent.turn_telemetry import record_phase_ms


@dataclass(frozen=True)
class PlanTripDependencies(PreparationDependencies):
    """Legacy full dependency contract for the nested-advisor ``plan_trip``.

    Extends the narrow preparation type with exactly the extra bindings the
    legacy executor/advisor stack reads: enrichment, geo, the advisor timeout,
    and the optional advisor/projection bindings. The direct Live Map path
    never constructs this type, so its import graph stays advisor-free.
    """

    enrichment: Any
    geo: Any
    advisor_timeout_seconds: float
    ai_advisor: Any | None = None
    advisor_context: Any | None = None
    project: Callable[..., ToolResult] | None = None


async def execute_single_leg(
    tool_input: dict,
    ctx: ToolContext,
    timings: dict[str, float],
    *,
    dependencies: PlanTripDependencies,
) -> ToolResult:
    """Resolve, route, enrich, score, nested-select, then project the card."""
    prepared = await prepare_single_leg(
        tool_input,
        ctx,
        timings,
        dependencies=dependencies,
        emit_comparing_progress=True,
    )
    if isinstance(prepared, ToolResult):
        return prepared

    judge_payload = dependencies.advisor_context.build_advisor_payload(
        routes=prepared.parsed_routes,
        service_alerts=prepared.relevant_alerts,
        incidents=prepared.incidents,
        stalled_trains=prepared.stalled,
        stalled_buses=prepared.stalled_buses,
        ticketmaster_event_impacts=prepared.event_impacts,
        evidence=prepared.evidence_envelopes,
        mode=dependencies.advisor_context.PlanningMode.INTELLIGENCE,
        scored_candidates=prepared.scored,
    )
    selection = await select_agent_route(
        payload=judge_payload,
        candidate_count=len(prepared.parsed_routes),
        scored=prepared.scored,
        ctx=ctx,
        dependencies=dependencies,
        timings=timings,
        leg_telemetry=prepared.leg_telemetry,
    )
    chosen_index = selection.chosen_index
    candidate_analysis = selection.candidate_analysis
    decision_reason = "advisor_tiebreak" if not selection.fallback else "lowest_final_score"
    selection_log_reason = (
        "advisor_selection" if not selection.fallback else "advisor_fallback_score"
    )
    scoring_event_impacts = [
        impact
        for impact in prepared.event_impacts
        if float(impact.get("risk_score") or 0) > 0
    ]
    chosen_route = prepared.parsed_routes[chosen_index]
    enrichment_started = time.monotonic()
    await dependencies.enrichment._enrich_route(ctx.gtfs, chosen_route)
    first_leg_context = await first_leg_arrival_context(
        tool_input,
        ctx,
        prepared.origin_place,
        chosen_route,
        dependencies,
    )
    timings["enrichment_ms"] = (time.monotonic() - enrichment_started) * 1000
    elapsed = (time.monotonic() - prepared.plan_origin) * 1000
    timings["enrichment_complete_ms"] = elapsed
    record_phase_ms(ctx.telemetry, "enrichment_complete_ms", elapsed)
    projected = dependencies.project(
        tool_input=tool_input,
        ctx=ctx,
        timings=timings,
        parsed_routes=prepared.parsed_routes,
        origin_raw=prepared.origin_raw,
        destination_raw=prepared.destination_raw,
        origin_place=prepared.origin_place,
        destination_place=prepared.destination_place,
        departure_time=prepared.departure_time,
        arrival_by=prepared.arrival_by,
        excluded=prepared.excluded,
        relevant_alerts=prepared.relevant_alerts,
        event_evidence_status=prepared.event_evidence_status,
        event_impacts=prepared.event_impacts,
        event_failures=prepared.event_failures,
        crowd_search_metadata=prepared.crowd_search_metadata,
        incident_scan_metadata=prepared.incident_scan_metadata,
        evidence_envelopes=prepared.evidence_envelopes,
        collect_crowd_evidence=prepared.collect_crowd_evidence,
        chosen_index=chosen_index,
        candidate_analysis=candidate_analysis,
        scored=prepared.scored,
        decision_reason=decision_reason,
        selection_log_reason=selection_log_reason,
        scoring_event_impacts=scoring_event_impacts,
        first_leg_arrival_context=first_leg_context,
        advisor_recommendation=selection.recommendation,
    )
    await ctx.emit_progress("comparing_options", "complete")
    return projected


__all__ = ("PlanTripDependencies", "execute_single_leg")
