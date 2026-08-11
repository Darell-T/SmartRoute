"""Neutral preparation-only bindings for the shared ``prepare_single_leg``.

The direct Live Map endpoint (``app.routers.trips`` ->
``app.services.trips.direct_plan``) must never load the nested-advisor stack.
This module builds the narrow ``PreparationDependencies`` consumed by
``prepare_single_leg`` (routing, MTA context, incident index, crowd evidence,
scoring) and nothing else: no advisor, advisor timeout, projection, or
enrichment bindings. The legacy nested-advisor facade keeps its own full
binding construction in ``plan_trip_executor.PlanTripDependencies``.

Provider modules are bound at import time, matching the legacy ``plan_trip``
facade, so the router test harness that re-imports this module under swapped
``app.services.directions`` / ``app.services.mta_feed`` modules keeps working.
"""

from __future__ import annotations

import importlib
import os
from typing import Any

from app.services.agent.tools._location import resolve_named_place
from app.services.agent.tools.plan_trip_input import (
    derive_arrive_by_departure,
    route_with_recovery,
)
from app.services.agent.tools.plan_trip_prepare import PreparationDependencies
from app.services import evidence
from app.services.trips import (
    candidates,
    crowd_evidence,
    crowd_hotspots,
    scoring,
)
from app.services.trips import incidents as trip_incidents

TRIP_CONTEXT_TIMEOUT_S = float(os.getenv("TRIP_CONTEXT_TIMEOUT_S", "2.0"))
LIVE_EVIDENCE_TTL_S = 120
EVENT_EVIDENCE_TTL_S = 300

directions_service = importlib.import_module("app.services.directions")
mta_feed = importlib.import_module("app.services.mta_feed")


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
) -> PreparationDependencies:
    """Return the neutral preparation bindings for one direct trip."""
    return PreparationDependencies(
        directions_service=directions_service,
        route_with_recovery=_route_with_recovery,
        derive_arrive_by_departure=_derive_arrive_by_departure,
        resolve_named_place=resolve_named_place,
        collect_alerts=mta_feed.fetch_service_alerts,
        collect_stalled_trains=mta_feed.get_stalled_trains,
        collect_stalled_buses=mta_feed.get_stalled_buses,
        parse_service_alerts=mta_feed.parse_service_alerts,
        filter_alerts_for_routes=mta_feed.filter_alerts_for_routes,
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
    )


__all__ = (
    "EVENT_EVIDENCE_TTL_S",
    "LIVE_EVIDENCE_TTL_S",
    "TRIP_CONTEXT_TIMEOUT_S",
    "build_preparation_dependencies",
)
