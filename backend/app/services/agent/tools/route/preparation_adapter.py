"""Agent adapters for the shared route-preparation implementation.

The route computation and dependency bundle live under services.trips. This
module supplies agent-owned location resolution, progress timing, and the
existing provider and failure-adaptation seams used by route tools.
"""

from __future__ import annotations

import dataclasses
import importlib
from typing import Any

from app.services.agent.tools.location_resolution import resolve_named_place
from app.services.agent.tools._types import ToolResult
from app.services.agent.turn.finalization import record_phase_ms
from app.services.trips.preparation import dependencies as _shared
from app.services.trips.preparation.context import RoutePreparationFailure
from app.services.trips.preparation.dependencies import (
    EVENT_EVIDENCE_TTL_S,
    LIVE_EVIDENCE_TTL_S,
    TRIP_CONTEXT_TIMEOUT_S,
    new_preparation_timings,
)
from app.services.trips.preparation.input import (
    derive_arrive_by_departure,
    route_with_recovery,
)
from app.services.trips.preparation.prepare import (
    PreparedLeg,
    PreparationDependencies,
    prepare_single_leg as _prepare_single_leg,
)
from app.services.trips.transfer_semantics import normalize_routes


directions_service = importlib.import_module("app.services.directions")
mta_realtime = importlib.import_module("app.services.mta.realtime")


async def _route_with_recovery(**kwargs: Any) -> list:
    return await route_with_recovery(
        directions_service=directions_service,
        **kwargs,
    )


async def _derive_arrive_by_departure(**kwargs: Any) -> str:
    return await derive_arrive_by_departure(
        directions_service=directions_service,
        **kwargs,
    )


def build_preparation_dependencies(
    *,
    context_timeout_seconds: float | None = None,
    live_evidence_ttl_seconds: int | None = None,
    event_evidence_ttl_seconds: int | None = None,
):
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
        route_with_recovery_fn=_route_with_recovery,
        derive_arrive_by_departure_fn=_derive_arrive_by_departure,
    )


def _as_tool_result(value: object) -> object:
    if isinstance(value, RoutePreparationFailure):
        return ToolResult(ok=False, error=value.error)
    return value


async def prepare_single_leg(*args: Any, **kwargs: Any) -> PreparedLeg | ToolResult:
    """Run neutral preparation and adapt only its failure value."""

    dependencies = kwargs.get("dependencies")
    if dependencies is not None:
        # Bind the agent-level route normalizer without making the neutral
        # preparation service import agent modules. Dataclass dependencies are
        # frozen, while lightweight injected fixtures may be mutable.
        if dataclasses.is_dataclass(dependencies):
            kwargs["dependencies"] = dataclasses.replace(
                dependencies,
                normalize_routes=normalize_routes,
            )
        else:
            try:
                dependencies.normalize_routes = normalize_routes
            except (AttributeError, TypeError):
                pass
    result = await _prepare_single_leg(*args, **kwargs)
    return _as_tool_result(result)


__all__ = (
    "EVENT_EVIDENCE_TTL_S",
    "LIVE_EVIDENCE_TTL_S",
    "TRIP_CONTEXT_TIMEOUT_S",
    "PreparedLeg",
    "PreparationDependencies",
    "build_preparation_dependencies",
    "new_preparation_timings",
    "normalize_routes",
    "prepare_single_leg",
)
