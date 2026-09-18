"""Agent adapters for the shared route-preparation implementation.

The route computation and dependency bundle live under services.trips. This
module supplies agent-owned location resolution, progress timing, and the
existing provider and failure-adaptation seams used by route tools.
"""

from __future__ import annotations

import importlib

from app.services.agent.tools.base import ToolResult
from app.services.agent.tools.location_resolution import resolve_named_place
from app.services.agent.turn.finalization import record_phase_ms
from app.services.trips.location import ResolvedPlace
from app.services.trips.preparation import dependencies as _shared
from app.services.trips.preparation.context import (
    RoutePreparationContext,
    RoutePreparationFailure,
)
from app.services.trips.preparation.dependencies import (
    EVENT_EVIDENCE_TTL_S,
    LIVE_EVIDENCE_TTL_S,
    TRIP_CONTEXT_TIMEOUT_S,
    new_preparation_timings,
)
from app.services.trips.preparation.prepare import (
    PreparationDependencies,
    PreparedLeg,
)
from app.services.trips.preparation.prepare import (
    prepare_single_leg as _prepare_single_leg,
)
from app.services.trips.transfer_semantics import normalize_routes

directions_service = importlib.import_module("app.services.directions")
mta_realtime = importlib.import_module("app.services.mta.realtime")


def build_preparation_dependencies(
    *,
    context_timeout_seconds: float | None = None,
    live_evidence_ttl_seconds: int | None = None,
    event_evidence_ttl_seconds: int | None = None,
) -> PreparationDependencies:
    """Build the shared bundle with agent-owned endpoint composition."""

    return _shared.build_preparation_dependencies(
        context_timeout_seconds=context_timeout_seconds,
        live_evidence_ttl_seconds=live_evidence_ttl_seconds,
        event_evidence_ttl_seconds=event_evidence_ttl_seconds,
        resolve_named_place=resolve_named_place,
        record_phase_ms=record_phase_ms,
        normalize_routes=normalize_routes,
        directions_module=directions_service,
        mta_module=mta_realtime,
    )


async def prepare_single_leg(
    tool_input: dict,
    ctx: RoutePreparationContext,
    timings: dict[str, float],
    *,
    dependencies: PreparationDependencies,
    emit_comparing_progress: bool = True,
    resolved_origin: ResolvedPlace | None = None,
    resolved_destination: ResolvedPlace | None = None,
) -> PreparedLeg | ToolResult:
    """Run neutral preparation and adapt only its failure value."""

    result = await _prepare_single_leg(
        tool_input, ctx, timings,
        dependencies=dependencies,
        emit_comparing_progress=emit_comparing_progress,
        resolved_origin=resolved_origin,
        resolved_destination=resolved_destination,
    )
    if isinstance(result, RoutePreparationFailure):
        return ToolResult(ok=False, error=result.error)
    return result


__all__ = (
    "EVENT_EVIDENCE_TTL_S",
    "LIVE_EVIDENCE_TTL_S",
    "TRIP_CONTEXT_TIMEOUT_S",
    "PreparationDependencies",
    "PreparedLeg",
    "build_preparation_dependencies",
    "new_preparation_timings",
    "normalize_routes",
    "prepare_single_leg",
)
