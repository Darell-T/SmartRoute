"""Load and validate the immutable candidate facts owned by present_route."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

from app.services.agent import candidate_store, public_surface
from app.services.agent import trip_state as trip_state_module
from app.services.agent.tools._types import ToolContext, ToolResult
from app.services.agent.tools.location_resolution import ResolvedPlace
from app.services.agent.turn.contract import GoalKind, GoalState
from app.services.trips.preparation.constraints import route_constraints


@dataclass(frozen=True)
class ValidatedRoutePresentation:
    session_id: str
    session: dict
    candidate_set_id: str
    candidate_id: str
    record: dict[str, Any]
    entry: dict[str, Any]
    chosen_index: int
    parsed_routes: list[list[dict]]
    canonical_itinerary: dict
    first_route: list[dict]
    origin_place: ResolvedPlace
    destination_place: ResolvedPlace
    candidate_evidence: dict[str, Any]
    evidence_envelopes: dict[str, Any]
    scenario_mode: str
    commit_scenario: bool
    reuses_temporary_preview: bool
    status: str
    tool_input_body: dict[str, Any]
    lead_in: str
    follow_up: str
    reason_code: str | None
    structured_reason: dict[str, Any] | None
    selection_source: str
    selection_reason: str
    goal_key: str | None
    scored: list[dict[str, Any]]
    first_leg_context: dict | None
    timings: dict[str, float]
    plan_origin: float


def owned_candidate(tool_input: dict, ctx: ToolContext) -> dict[str, Any] | ToolResult:
    binding = _candidate_binding(tool_input, ctx)
    if isinstance(binding, ToolResult):
        return binding
    return _load_candidate_entry(binding)


def canonical_facts(owned: dict[str, Any]) -> dict[str, Any] | ToolResult:
    plan_origin = time.monotonic()
    record = owned["record"]
    entry = owned["entry"]
    chosen_index = owned["chosen_index"]
    parsed_routes = owned["parsed_routes"]
    canonical = _load_canonical_candidate(
        record, chosen_index, parsed_routes[chosen_index], entry
    )
    if isinstance(canonical, ToolResult):
        return canonical
    chosen_route, canonical_itinerary, first_route = canonical
    parsed_routes[chosen_index] = chosen_route
    constraints = route_constraints(
        chosen_route,
        dict(record.get("tool_input") or {}),
        itinerary=canonical_itinerary,
    )
    if constraints.get("satisfied") is not True:
        return ToolResult(
            ok=False,
            error="selected candidate does not satisfy the server-owned hard constraints",
        )
    origin_place = _place_from_dict(record.get("origin_place"))
    digest = entry.get("digest") if isinstance(entry.get("digest"), dict) else {}
    destination_place = _place_from_dict(
        entry.get("destination_place")
        or digest.get("_destination_place")
        or record.get("destination_place")
    )
    if origin_place is None or destination_place is None:
        return ToolResult(ok=False, error="stored place identity is invalid")
    scenario_mode = str(record.get("scenario_mode") or "active")
    commit_scenario = scenario_mode == "what_if" and bool(
        owned["tool_input"].get("commit_scenario")
    )
    scored = [row for row in (record.get("scored") or []) if isinstance(row, dict)]
    try:
        has_selected_score = any(int(row.get("index", -1)) == chosen_index for row in scored)
    except (TypeError, ValueError):
        has_selected_score = False
    if not has_selected_score:
        return ToolResult(
            ok=False,
            error="prepared candidate is missing finalized comparison factors",
            internal_diagnostic=True,
        )
    candidate_evidence = _candidate_evidence(record, chosen_index)
    stored_first_leg = record.get("first_leg_arrival_context")
    first_leg_context = dict(stored_first_leg) if isinstance(stored_first_leg, dict) else None
    evidence_envelopes = {
        name: _EnvelopeShim(payload)
        for name, payload in (candidate_evidence.get("evidence_envelopes") or {}).items()
        if isinstance(payload, dict)
    }
    owned.update(
        {
            "parsed_routes": parsed_routes,
            "canonical_itinerary": canonical_itinerary,
            "first_route": first_route,
            "origin_place": origin_place,
            "destination_place": destination_place,
            "scenario_mode": scenario_mode,
            "commit_scenario": commit_scenario,
            "scored": scored,
            "candidate_evidence": candidate_evidence,
            "evidence_envelopes": evidence_envelopes,
            "first_leg_context": first_leg_context,
            "timings": dict(record.get("timings") or {}),
            "plan_origin": plan_origin,
            "tool_input_body": dict(record.get("tool_input") or {}),
        }
    )
    return owned


def rebind_to_entry(facts: dict[str, Any], entry: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(entry, dict):
        return None
    candidate_id = str(entry.get("candidate_id") or "").strip()
    if not candidate_id:
        return None
    try:
        chosen_index = int(entry.get("index"))
    except (TypeError, ValueError):
        return None
    parsed_routes = [
        list(route)
        for route in (facts["record"].get("parsed_routes") or [])
        if isinstance(route, list)
    ]
    return {
        **facts,
        "candidate_id": candidate_id,
        "entry": entry,
        "chosen_index": chosen_index,
        "parsed_routes": parsed_routes,
    }


def destination_selection_mode(record: object) -> str | None:
    """Classify a candidate set as single-destination or comparison.

    The persisted mode is authoritative because preparation can prune or fail
    branches before presentation. Structural evidence exists only to support
    legacy records and to reject a record that claims to be single-destination
    while still owning multiple destination identities.
    """

    if not isinstance(record, dict):
        return None
    explicit_mode = str(record.get("destination_selection_mode") or "").strip()
    if explicit_mode not in {"", "single", "comparison"}:
        return None
    has_multiple_destinations = any(
        len(destination_ids) > 1
        for destination_ids in _destination_identity_groups(record)
    )
    if explicit_mode == "single" and has_multiple_destinations:
        return None
    if explicit_mode:
        return explicit_mode
    return "comparison" if has_multiple_destinations else "single"


def is_destination_comparison(record: object) -> bool:
    """Return the validated destination-comparison classification."""

    return destination_selection_mode(record) == "comparison"


def _destination_identity_groups(record: dict[str, Any]) -> tuple[set[str], ...]:
    tool_input = record.get("tool_input")
    input_ids = (
        tool_input.get("destination_place_ids")
        if isinstance(tool_input, dict)
        else None
    )
    record_ids = record.get("destination_place_ids")
    candidate_ids = [
        candidate["digest"].get("destination_place_id")
        for candidate in record.get("candidates") or []
        if isinstance(candidate, dict) and isinstance(candidate.get("digest"), dict)
    ]
    branch_ids = [
        branch.get("place_id")
        for branch in record.get("branch_coverage") or []
        if isinstance(branch, dict)
    ]
    return tuple(
        {
            str(value).strip()
            for value in values or []
            if str(value or "").strip()
        }
        for values in (input_ids, record_ids, candidate_ids, branch_ids)
        if isinstance(values, list)
    )


def _candidate_binding(tool_input: dict, ctx: ToolContext) -> dict[str, Any] | ToolResult:
    session_id = str(getattr(ctx, "session_id", None) or "").strip()
    if not session_id:
        return ToolResult(ok=False, error="session is required for route presentation")
    candidate_id = str(tool_input.get("candidate_id") or "").strip()
    if not candidate_id:
        return ToolResult(ok=False, error="candidate_id is required")
    session = ctx.session if isinstance(ctx.session, dict) else {}
    state = trip_state_module.get_trip_state(session)
    candidate_set_id = str(
        state.get("temporary_candidate_set_id") or state.get("active_candidate_set_id") or ""
    ).strip()
    if not candidate_set_id:
        return ToolResult(
            ok=False,
            error="no active candidate set; call prepare_route_options first",
        )
    evidence = getattr(ctx, "turn_evidence", None)
    goal_key = _route_goal_key(tool_input, ctx)
    preview = _temporary_preview(
        state, candidate_set_id, candidate_id, goal_key, evidence, session, session_id
    )
    if isinstance(preview, ToolResult):
        return preview
    reuses_temporary_preview, _error = preview
    if (
        evidence is not None
        and goal_key is not None
        and evidence.handle_for(goal_key) != candidate_set_id
        and not reuses_temporary_preview
    ):
        return ToolResult(
            ok=False,
            error="candidate set does not belong to this route goal",
            internal_diagnostic=True,
        )
    if candidate_set_id not in {
        state.get("active_candidate_set_id"),
        state.get("temporary_candidate_set_id"),
    }:
        return ToolResult(ok=False, error="candidate set is not active for this trip")
    return {
        "session_id": session_id,
        "session": session,
        "candidate_set_id": candidate_set_id,
        "candidate_id": candidate_id,
        "goal_key": goal_key,
        "evidence": evidence,
        "reuses_temporary_preview": reuses_temporary_preview,
        "tool_input": tool_input,
    }


def _load_candidate_entry(binding: dict[str, Any]) -> dict[str, Any] | ToolResult:
    record, entry, store_error = candidate_store.get_candidate(
        binding["candidate_set_id"], binding["candidate_id"], session_id=binding["session_id"]
    )
    if record is None:
        return ToolResult(ok=False, error=store_error or "candidate not found")
    if entry is None:
        # Candidate membership is an authorization boundary.  A model- or
        # rider-supplied id that is not in this server-owned set must never be
        # reinterpreted as a request for the deterministic fallback candidate.
        return ToolResult(
            ok=False,
            error=store_error or "candidate id is unknown for this set",
        )
    selection_mode = destination_selection_mode(record)
    if selection_mode is None:
        return ToolResult(
            ok=False,
            error="candidate set has an invalid destination selection shape",
            internal_diagnostic=True,
        )
    status = str(record.get("route_status") or "good")
    if binding["reuses_temporary_preview"] and record.get("presented"):
        return ToolResult(
            ok=False,
            error="temporary route preview has already been presented",
            internal_diagnostic=True,
        )
    try:
        chosen_index = int(entry.get("index") or 0)
    except (TypeError, ValueError):
        chosen_index = -1
    parsed_routes = [
        route
        for route in (record.get("parsed_routes") or [])
        if isinstance(route, list)
    ]
    if chosen_index < 0 or chosen_index >= len(parsed_routes):
        return ToolResult(ok=False, error="candidate index is out of range")
    return {
        **binding,
        "record": record,
        "entry": entry,
        "chosen_index": chosen_index,
        "parsed_routes": parsed_routes,
        "status": status,
        "destination_selection_mode": selection_mode,
    }


def _temporary_preview(
    state: dict,
    candidate_set_id: str,
    candidate_id: str,
    goal_key: str | None,
    evidence: Any,
    session: dict,
    session_id: str,
) -> tuple[bool, ToolResult | None] | ToolResult:
    temporary_set_id = str(state.get("temporary_candidate_set_id") or "").strip()
    temporary_selected_id = str(state.get("temporary_selected_candidate_id") or "").strip()
    if (
        temporary_set_id == candidate_set_id
        and temporary_selected_id
        and candidate_id != temporary_selected_id
    ):
        return ToolResult(
            ok=False,
            error="temporary route preview requires its selected candidate",
            internal_diagnostic=True,
        )
    return (
        temporary_set_id == candidate_set_id
        and goal_key is not None
        and evidence is not None
        and evidence.state_for(goal_key) == GoalState.PENDING
        and public_surface.active_temporary_route_preview(
            session, session_id=session_id
        )
        == (candidate_set_id, candidate_id),
        None,
    )


def _route_goal_key(tool_input: dict, ctx: ToolContext) -> str | None:
    evidence = getattr(ctx, "turn_evidence", None)
    contract = getattr(evidence, "turn_contract", None)
    if contract is None:
        return None
    raw = tool_input.get("goal_key")
    if not isinstance(raw, str) or not raw.strip():
        return None
    goal = contract.get_goal(raw.strip())
    return raw.strip() if goal is not None and goal.kind == GoalKind.ROUTE else None


def _load_canonical_candidate(
    record: dict[str, Any],
    chosen_index: int,
    stored_route: list[dict],
    entry: dict[str, Any],
) -> tuple[list[dict], dict, list[dict]] | ToolResult:
    stored_digest = entry.get("digest")
    stored_itinerary = entry.get("canonical_itinerary")
    if not isinstance(stored_itinerary, dict) and isinstance(stored_digest, dict):
        stored_itinerary = stored_digest.get("_canonical_itinerary")
    if not isinstance(stored_itinerary, dict):
        return ToolResult(
            ok=False,
            error="prepared candidate is missing its canonical itinerary snapshot",
            internal_diagnostic=True,
        )
    if str(record.get("candidate_kind") or "single_leg") != "multi_stop":
        return stored_route, dict(stored_itinerary), stored_route
    all_segments = record.get("aggregate_segments") or []
    segments = all_segments[chosen_index] if chosen_index < len(all_segments) else []
    if not isinstance(segments, list) or not segments:
        return ToolResult(
            ok=False,
            error="prepared multi-stop candidate is missing its segment snapshot",
            internal_diagnostic=True,
        )
    first_route = segments[0].get("steps") if isinstance(segments[0], dict) else []
    return (
        stored_route,
        dict(stored_itinerary),
        first_route if isinstance(first_route, list) else stored_route,
    )


def _place_from_dict(raw: object) -> ResolvedPlace | None:
    if not isinstance(raw, dict):
        return None
    try:
        latitude = raw.get("latitude") if raw.get("latitude") is not None else raw["lat"]
        longitude = raw.get("longitude") if raw.get("longitude") is not None else raw["lng"]
        return ResolvedPlace(
            name=str(raw.get("name") or raw.get("label") or "Place"),
            latitude=float(latitude), longitude=float(longitude),
            source=str(raw.get("source") or "geocode"),
            address=raw.get("address"), place_id=raw.get("place_id"),
        )
    except (KeyError, TypeError, ValueError):
        return None


def _candidate_evidence(record: dict[str, Any], chosen_index: int) -> dict[str, Any]:
    values = record.get("candidate_evidence")
    if (
        isinstance(values, list)
        and 0 <= chosen_index < len(values)
        and isinstance(values[chosen_index], dict)
    ):
        return values[chosen_index]
    return {
        "alerts": list(record.get("relevant_alerts") or []),
        "incidents": list(record.get("incidents") or []),
        "unconfirmed_material_claims": list(record.get("unconfirmed_material_claims") or []),
        "evidence_coverage": dict(record.get("evidence_coverage") or {}),
        "event_impacts": [
            impact
            for impact in record.get("event_impacts") or []
            if isinstance(impact, dict)
            and impact.get("route_index") == chosen_index
        ],
        "event_evidence_status": record.get("event_evidence_status") or "unscanned",
        "event_failures": list(record.get("event_failures") or []),
        "crowd_search_metadata": dict(record.get("crowd_search_metadata") or {}),
        "incident_scan_metadata": dict(record.get("incident_scan_metadata") or {}),
        "evidence_envelopes": dict(record.get("evidence_envelopes") or {}),
    }


class _EnvelopeShim:
    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = payload

    def to_model_dict(self, *, empty: Any, now: Any = None) -> dict[str, Any]:
        result = dict(self._payload)
        if result.get("status") != "current":
            result["payload"] = empty
        return result

    def current_payload(self, now: Any = None) -> Any:
        return self._payload.get("payload") if self._payload.get("status") == "current" else None


__all__ = (
    "ValidatedRoutePresentation", "canonical_facts", "destination_selection_mode",
    "is_destination_comparison", "owned_candidate", "rebind_to_entry",
)
