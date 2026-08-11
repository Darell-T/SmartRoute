"""Advisor-free presentation bindings for the ``present_route`` tool.

Canonical route presentation legitimately reads enrichment, geo distance,
scoring helpers, and the neutral ``plan_trip_projection.project_single_leg``
callbacks.  It never needs the legacy nested-advisor stack, so this module
binds only those pieces.  ``plan_trip_dependencies`` stays preparation-only
for the direct Live Map graph, and the rollback-only ``plan_trip`` facade
delegates its non-advisor projection/first-boarding helpers here so the
shared logic exists once.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from app.services.agent import events as agent_events
from app.services.agent.tools._types import ToolResult
from app.services.agent.tools.plan_trip_input import point_label, summary_eta_minutes
from app.services.agent.tools.plan_trip_projection import project_single_leg
from app.services.trips import candidates, enrichment, scoring, text
from app.utils import geo


def first_boarding_context(gtfs, step: dict, walking_minutes: int) -> dict:
    """Resolve canonical stop/direction ids for one transit boarding.

    Shared by the conversational presentation path and the rollback-only
    ``plan_trip`` facade projection.
    """
    route_id = scoring._step_route_id(step).strip().upper()
    context = {
        "route_id": route_id,
        "mode": str(step.get("type") or "").lower(),
        "stop_name": step.get("departure_stop"),
        "coordinates": step.get("departure_coords"),
        "direction_label": step.get("direction"),
        "walking_minutes": walking_minutes,
    }
    pattern_index = getattr(gtfs, "_pattern_index", None) if gtfs else None
    resolve = getattr(pattern_index, "resolve_route_segment", None)
    if not callable(resolve):
        return context
    resolved = resolve(
        route_id, step.get("departure_stop"), step.get("arrival_stop"),
        step.get("departure_coords"), step.get("arrival_coords"),
    )
    if resolved:
        context.update({
            "stop_id": resolved.get("origin_stop_id"),
            "direction_id": resolved.get("direction_id"),
            "destination_stop_id": resolved.get("destination_stop_id"),
        })
    return context


def _project_single_leg(**kwargs: Any) -> ToolResult:
    return project_single_leg(
        **kwargs,
        point_label=point_label,
        summary_eta_minutes=summary_eta_minutes,
        first_boarding_context=first_boarding_context,
        candidates_module=candidates,
        scoring_module=scoring,
        text_module=text,
        route_card_event=agent_events.RouteCardEvent,
    )


@dataclass(frozen=True)
class PresentationDependencies:
    """Narrow immutable bindings one route presentation actually reads.

    Kept injectable so focused tests can substitute a failing projection or
    enrichment without touching provider modules. No advisor, advisor
    timeout, or preparation binding lives here.
    """

    enrichment: Any
    geo: Any
    scoring: Any
    project: Callable[..., ToolResult]


def build_presentation_dependencies() -> PresentationDependencies:
    """Return the neutral advisor-free presentation bindings for one execution."""
    return PresentationDependencies(
        enrichment=enrichment,
        geo=geo,
        scoring=scoring,
        project=_project_single_leg,
    )


__all__ = (
    "PresentationDependencies",
    "build_presentation_dependencies",
    "first_boarding_context",
)
