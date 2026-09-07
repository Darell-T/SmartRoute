"""Neutral preparation-only bindings for the shared ``prepare_single_leg``.

The direct Live Map endpoint (``app.routers.trips`` ->
``app.services.trips.direct_plan``) must never load the nested-advisor stack.
This module builds the narrow ``PreparationDependencies`` consumed by
``prepare_single_leg`` (routing, MTA context, incident index, crowd evidence,
scoring) and nothing else: no model selection, projection, or enrichment
bindings.

Provider modules are bound at import time so the router test harness that
re-imports this module under swapped ``app.services.directions`` /
``app.services.mta.realtime`` modules keeps working.
"""

from __future__ import annotations

import importlib
import os
from collections.abc import Awaitable, Callable
from typing import Any

from app.services import evidence
from app.services.trips import candidates, scoring
from app.services.trips.crowds import evidence as crowd_evidence
from app.services.trips.crowds import hotspots as crowd_hotspots
from app.services.trips.location import (
    resolve_named_place as resolve_neutral_named_place,
)
from app.services.trips.preparation.input import (
    derive_arrive_by_departure,
    route_with_recovery,
)
from app.services.trips.preparation.prepare import PreparationDependencies
from app.services.trips.route_incidents import scan as trip_incidents

TRIP_CONTEXT_TIMEOUT_S = float(os.getenv("TRIP_CONTEXT_TIMEOUT_S", "2.0"))
LIVE_EVIDENCE_TTL_S = 120
EVENT_EVIDENCE_TTL_S = 300

directions_service = importlib.import_module("app.services.directions")
mta_realtime = importlib.import_module("app.services.mta.realtime")


def _route_service_ids(route: list[dict]) -> set[str]:
    return {
        scoring._step_route_id(step).strip().upper()
        for step in route or []
        if step.get("type") in {"SUBWAY", "BUS"}
        and scoring._step_route_id(step).strip()
    }


def build_preparation_dependencies(
    *,
    context_timeout_seconds: float | None = None,
    live_evidence_ttl_seconds: int | None = None,
    event_evidence_ttl_seconds: int | None = None,
    resolve_named_place: Callable[..., Awaitable[tuple[Any, str | None]]] | None = None,
    record_phase_ms: Callable[[dict[str, Any], str, float], None] | None = None,
    normalize_routes: Callable[..., Any] | None = None,
    directions_module: Any | None = None,
    mta_module: Any | None = None,
    route_with_recovery_fn: Callable[..., Awaitable[list]] | None = None,
    derive_arrive_by_departure_fn: Callable[..., Awaitable[str]] | None = None,
) -> PreparationDependencies:
    """Return provider bindings for one model-free route-preparation request."""
    bound_directions = directions_module or directions_service
    bound_mta = mta_module or mta_realtime

    async def route_with_bound_provider(**kwargs: Any) -> list:
        return await route_with_recovery(
            directions_service=bound_directions,
            **kwargs,
        )

    async def derive_with_bound_provider(**kwargs: Any) -> str:
        return await derive_arrive_by_departure(
            directions_service=bound_directions,
            **kwargs,
        )

    return PreparationDependencies(
        directions_service=bound_directions,
        route_with_recovery=route_with_recovery_fn or route_with_bound_provider,
        derive_arrive_by_departure=(
            derive_arrive_by_departure_fn or derive_with_bound_provider
        ),
        resolve_named_place=resolve_named_place or resolve_neutral_named_place,
        collect_alerts=bound_mta.fetch_service_alerts,
        collect_stalled_trains=bound_mta.get_stalled_trains,
        collect_stalled_buses=bound_mta.get_stalled_buses,
        parse_service_alerts=bound_mta.parse_service_alerts,
        filter_alerts_for_routes=bound_mta.filter_alerts_for_routes,
        candidates=candidates,
        crowd_evidence=crowd_evidence,
        crowd_hotspots=crowd_hotspots,
        scoring=scoring,
        trip_incidents=trip_incidents,
        current_payload=evidence.current_payload,
        evidence_envelope=evidence.evidence_envelope,
        route_service_ids=_route_service_ids,
        context_timeout_seconds=(
            context_timeout_seconds
            if context_timeout_seconds is not None
            else TRIP_CONTEXT_TIMEOUT_S
        ),
        live_evidence_ttl_seconds=(
            live_evidence_ttl_seconds
            if live_evidence_ttl_seconds is not None
            else LIVE_EVIDENCE_TTL_S
        ),
        event_evidence_ttl_seconds=(
            event_evidence_ttl_seconds
            if event_evidence_ttl_seconds is not None
            else EVENT_EVIDENCE_TTL_S
        ),
        normalize_routes=normalize_routes,
        record_phase_ms=record_phase_ms,
    )


def new_preparation_timings() -> dict[str, float]:
    """Create the timing accumulator shared by route preparation paths."""

    return dict.fromkeys(("place_resolution_ms", "route_provider_ms", "mta_ms", "ticketmaster_ms", "incident_ms", "advisor_ms", "scoring_ms", "enrichment_ms", "plan_trip_ms"), 0.0)


__all__ = (
    "EVENT_EVIDENCE_TTL_S",
    "LIVE_EVIDENCE_TTL_S",
    "TRIP_CONTEXT_TIMEOUT_S",
    "build_preparation_dependencies",
    "new_preparation_timings",
)
