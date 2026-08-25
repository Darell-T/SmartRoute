"""Reserve, commit, and record a validated route presentation."""

from __future__ import annotations

import time
from typing import Any

from app.services.agent import candidate_store
from app.services.agent import trip_state as trip_state_module
from app.services.agent.tools._types import ToolContext, ToolResult
from app.services.agent.tools.route.present_route_state import (
    ValidatedRoutePresentation,
    destination_selection_mode,
    is_destination_comparison,
)
from app.services.agent.turn.contract import GoalState
from app.services.agent.turn.finalization import record_phase_ms


def reserve_and_commit(
    presentation: ValidatedRoutePresentation,
    projected: ToolResult,
    ctx: ToolContext,
) -> ToolResult | None:
    selected_place_id: str | None = None
    if presentation.scenario_mode != "what_if" or presentation.commit_scenario:
        selected_place_id = _candidate_discovery_place_id(
            presentation.record,
            presentation.entry,
            session_id=presentation.session_id,
        )
        if (
            _requires_discovery_destination_binding(presentation.record)
            and selected_place_id is None
        ):
            return ToolResult(
                ok=False,
                error="selected candidate is not bound to the session discovery place",
                internal_diagnostic=True,
            )
        reservation_error = candidate_store.mark_presented(
            presentation.candidate_set_id,
            presentation.candidate_id,
            session_id=presentation.session_id,
        )
        if reservation_error:
            return ToolResult(ok=False, error=reservation_error)
    if presentation.scenario_mode == "what_if":
        if presentation.commit_scenario:
            trip_state_module.commit_scenario(
                presentation.session,
                candidate_set_id=presentation.candidate_set_id,
                candidate_id=presentation.candidate_id,
                tool_input=presentation.tool_input_body,
            )
            activate_stored_discovery_context(
                presentation.record,
                presentation.session,
                presentation.session_id,
                selected_place_id=selected_place_id,
            )
            trip_state_module.update_trip_state(
                presentation.session,
                destination=_accepted_destination_label(presentation),
            )
        else:
            trip_state_module.bind_temporary_selected_candidate(
                presentation.session, presentation.candidate_id
            )
            projected.session_route_cards = []
    else:
        trip_state_module.bind_selected_candidate(
            presentation.session, presentation.candidate_id
        )
        trip_state_module.update_trip_state(
            presentation.session,
            destination=_accepted_destination_label(presentation),
        )
        activate_stored_discovery_context(
            presentation.record,
            presentation.session,
            presentation.session_id,
            selected_place_id=selected_place_id,
        )
    presentation.timings["enrichment_ms"] = (
        time.monotonic() - presentation.plan_origin
    ) * 1000
    record_phase_ms(ctx.telemetry, "enrichment_complete_ms", presentation.timings["enrichment_ms"])
    projected.timings = presentation.timings
    projected.terminal = False
    projected.terminal_path = None
    return None


def record_presentation(presentation: ValidatedRoutePresentation, ctx: ToolContext) -> None:
    evidence = getattr(ctx, "turn_evidence", None)
    goal_key = presentation.goal_key
    if evidence is None or goal_key is None:
        return
    if presentation.reuses_temporary_preview:
        record_goal_handle = getattr(evidence, "record_goal_handle", None)
        if callable(record_goal_handle):
            record_goal_handle(goal_key, presentation.candidate_set_id)
    record_goal = getattr(evidence, "record_goal", None)
    if callable(record_goal):
        record_goal(goal_key, GoalState.SATISFIED, attempted=True, presented=True)


def _accepted_destination_label(presentation: ValidatedRoutePresentation) -> str:
    """Keep the rider's accepted label separate from the canonical endpoint."""

    if is_destination_comparison(presentation.record):
        digest = presentation.entry.get("digest")
        if isinstance(digest, dict):
            selected_name = str(digest.get("destination_name") or "").strip()
            if selected_name:
                return selected_name
        return presentation.destination_place.name
    record_label = str(presentation.record.get("destination_raw") or "").strip()
    if record_label:
        return record_label
    tool_label = str(presentation.tool_input_body.get("destination") or "").strip()
    return tool_label or presentation.destination_place.name


def activate_stored_discovery_context(
    record: dict[str, Any],
    session: dict,
    session_id: str,
    *,
    selected_place_id: str | None = None,
) -> None:
    from app.services.agent import discovery_store

    discovery_set_id = str(
        record.get("destination_discovery_set_id") or ""
    ).strip()
    if not discovery_set_id:
        return
    discovery_record = discovery_store.load_discovery_set(
        discovery_set_id,
        session_id=session_id,
    )
    if discovery_record is None:
        return
    valid_place_ids = {
        str(place.get("place_id") or "").strip()
        for place in discovery_record.get("places") or []
        if isinstance(place, dict)
        and discovery_store.is_opaque_place_id(place.get("place_id"))
    }
    destination_place_id = str(record.get("destination_place_id") or "").strip()
    if destination_place_id not in valid_place_ids:
        destination_place_id = ""
    selected_place_id = str(selected_place_id or "").strip()
    if selected_place_id not in valid_place_ids:
        selected_place_id = ""
    trip_state_module.bind_discovery_context(
        session,
        discovery_set_id=discovery_set_id,
        selected_place_id=selected_place_id or destination_place_id or None,
    )


def _candidate_discovery_place_id(
    record: dict[str, Any],
    entry: dict[str, Any],
    *,
    session_id: str,
) -> str | None:
    """Return the selected candidate's verified opaque discovery identity."""

    from app.services.agent import discovery_store

    discovery_set_id = str(
        record.get("destination_discovery_set_id") or ""
    ).strip()
    if not discovery_set_id:
        return None
    discovery_record = discovery_store.load_discovery_set(
        discovery_set_id,
        session_id=session_id,
    )
    if discovery_record is None:
        return None
    valid_place_ids = {
        str(place.get("place_id") or "").strip()
        for place in discovery_record.get("places") or []
        if isinstance(place, dict)
        and discovery_store.is_opaque_place_id(place.get("place_id"))
    }
    digest = entry.get("digest") if isinstance(entry, dict) else None
    candidate_place_id = (
        str(digest.get("destination_place_id") or "").strip()
        if isinstance(digest, dict)
        else ""
    )
    if candidate_place_id in valid_place_ids:
        return candidate_place_id
    if destination_selection_mode(record) == "single":
        fallback_place_id = str(record.get("destination_place_id") or "").strip()
        return fallback_place_id if fallback_place_id in valid_place_ids else None
    return None


def _requires_discovery_destination_binding(record: dict[str, Any]) -> bool:
    return bool(str(record.get("destination_discovery_set_id") or "").strip())


__all__ = ("activate_stored_discovery_context", "record_presentation", "reserve_and_commit")
