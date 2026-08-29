"""Stable vocabulary with a small, state-valid tool surface per round."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from app.services.agent import candidate_store, discovery_store, transcript_store
from app.services.agent.turn import completion as turn_completion
from app.services.agent.turn.contract import GoalKind, GoalState

PUBLIC_TOOL_NAMES: tuple[str, ...] = (
    "declare_goals",
    "discover_places",
    "check_transit",
    "prepare_route_options",
    "present_places",
    "present_transit",
    "present_route",
    "complete_turn",
)

INITIAL_TOOL_NAMES: frozenset[str] = frozenset(
    {
        "declare_goals",
        "discover_places",
        "check_transit",
        "prepare_route_options",
        "complete_turn",
    }
)

_CAPABILITY_BY_GOAL = {
    GoalKind.PLACE_RECOMMENDATION: "discover_places",
    GoalKind.DESTINATION_SELECTION: "discover_places",
    GoalKind.ROUTE: "prepare_route_options",
    GoalKind.SERVICE_STATUS: "check_transit",
    GoalKind.ARRIVALS: "check_transit",
    GoalKind.ACCESSIBILITY: "check_transit",
    GoalKind.TRANSIT_FACT: "check_transit",
    GoalKind.AREA_CONDITIONS: "check_transit",
    GoalKind.EVENT_OR_CROWD: "check_transit",
}

_PRESENTER_BY_GOAL = {
    GoalKind.PLACE_RECOMMENDATION: "present_places",
    GoalKind.DESTINATION_SELECTION: "present_places",
    GoalKind.ROUTE: "present_route",
    GoalKind.SERVICE_STATUS: "present_transit",
    GoalKind.ARRIVALS: "present_transit",
    GoalKind.ACCESSIBILITY: "present_transit",
    GoalKind.TRANSIT_FACT: "present_transit",
    GoalKind.AREA_CONDITIONS: "present_transit",
    GoalKind.EVENT_OR_CROWD: "present_transit",
}


def _feeds_route_selection(contract: object, goal_key: str) -> bool:
    """Whether a place goal is an internal dependency of a route goal.

    A delegated request such as "find a ramen spot and route me there" needs
    a verified place choice, not a rider-facing shortlist.  The route
    capability consumes that choice and the canonical route presenter is the
    visible result.  Standalone place recommendations and place-detail
    follow-ups still use ``present_places``.
    """

    goals = getattr(contract, "goals", ())
    return any(
        getattr(goal, "kind", None) == GoalKind.ROUTE
        and goal_key in tuple(getattr(goal, "depends_on", ()) or ())
        for goal in goals
    )


def _dependent_route_failed(contract: object, evidence: object, goal_key: str) -> bool:
    """Whether a delegated place choice must surface as partial success."""

    state_for = getattr(evidence, "state_for", None)
    if not callable(state_for):
        return False
    return any(
        getattr(goal, "kind", None) == GoalKind.ROUTE
        and goal_key in tuple(getattr(goal, "depends_on", ()) or ())
        and state_for(getattr(goal, "goal_key", ""))
        in {
            GoalState.ATTEMPTED_BUT_UNAVAILABLE,
            GoalState.UNSUPPORTED,
        }
        for goal in getattr(contract, "goals", ())
    )

INTERNAL_LEAF_TOOL_NAMES: frozenset[str] = frozenset(
    {
        "transit_snapshot",
        "check_area_conditions",
        "event_lookup",
        "lookup_arrivals",
        "venue_crowd_window",
        "accessibility_status",
        "lookup_facts",
        "get_place_details",
    }
)

STRICT_TOOL_BUDGET = 0
STRICT_OPTIONAL_FIELD_BUDGET = 0
OPTIONAL_FIELD_HARD_LIMIT = 24
STRICT_UNION_FIELD_BUDGET = 0
UNION_FIELD_HARD_LIMIT = 16
CAPABILITY_SURFACE_VERSION = "model_led_goals_v2"


def schema_optional_parameter_count(schema: object) -> int:
    if not isinstance(schema, dict):
        return 0
    properties = schema.get("properties") or {}
    required = set(schema.get("required") or [])
    return (
        sum(name not in required for name in properties)
        + sum(schema_optional_parameter_count(value) for value in properties.values())
        + schema_optional_parameter_count(schema.get("items"))
    )


def optional_parameter_count(tools: Iterable[Mapping[str, Any]]) -> int:
    return sum(
        schema_optional_parameter_count(tool.get("input_schema"))
        for tool in tools
        if tool.get("type") != "web_search_20250305"
    )


def schema_union_parameter_count(schema: object) -> int:
    """Count union-typed parameters that expand Anthropic's strict grammar."""

    if isinstance(schema, list):
        return sum(schema_union_parameter_count(value) for value in schema)
    if not isinstance(schema, dict):
        return 0
    current = int(isinstance(schema.get("type"), list) or "anyOf" in schema)
    return current + sum(schema_union_parameter_count(value) for value in schema.values())


def union_parameter_count(tools: Iterable[Mapping[str, Any]]) -> int:
    return sum(
        schema_union_parameter_count(tool.get("input_schema"))
        for tool in tools
        if tool.get("type") != "web_search_20250305"
    )


def offered_custom_tools(registry_schemas: Iterable[Mapping[str, Any]]) -> list[dict]:
    """Return every public schema in stable vocabulary order."""

    wanted = frozenset(PUBLIC_TOOL_NAMES)
    offered = []
    for schema in registry_schemas:
        if schema.get("name") not in wanted:
            continue
        provider_schema = dict(schema)
        # Anthropic rejects the combined public request during grammar
        # compilation even when only a subset is strict. The backend already
        # validates every capability at its executor/evidence boundary, so do
        # not opt the public request into provider-side grammar compilation.
        provider_schema.pop("strict", None)
        offered.append(provider_schema)
    names = [schema.get("name") for schema in offered]
    if names != list(PUBLIC_TOOL_NAMES):
        by_name = {schema.get("name"): schema for schema in offered}
        missing = [name for name in PUBLIC_TOOL_NAMES if name not in by_name]
        if missing:
            raise AssertionError(
                "public tool surface is missing required tools: "
                + ", ".join(missing)
            )
        offered = [by_name[name] for name in PUBLIC_TOOL_NAMES]
    strict_tools = [tool for tool in offered if tool.get("strict")]
    if len(strict_tools) != STRICT_TOOL_BUDGET:
        raise AssertionError(
            "public custom tool surface strict-tool count must be "
            f"{STRICT_TOOL_BUDGET}, got {len(strict_tools)}"
        )
    optional_count = optional_parameter_count(strict_tools)
    if optional_count != STRICT_OPTIONAL_FIELD_BUDGET:
        raise AssertionError(
            "public strict-tool optional-field count must be "
            f"{STRICT_OPTIONAL_FIELD_BUDGET}, got {optional_count}"
        )
    union_count = union_parameter_count(strict_tools)
    if union_count != STRICT_UNION_FIELD_BUDGET:
        raise AssertionError(
            "public strict-tool union-field count must be "
            f"{STRICT_UNION_FIELD_BUDGET}, got {union_count}"
        )
    return offered


def active_discovery_set_id(
    session: object | None,
    *,
    session_id: str | None = None,
) -> str | None:
    """Return the current session's live discovery handle, if it is valid.

    The handle in trip state is only a pointer. Loading the record through the
    session-scoped store is what proves that it is still live and belongs to
    this request; a stale or cross-session pointer must not make a presenter
    available.
    """

    if not isinstance(session, dict):
        return None
    owner = str(session_id or "").strip()
    if not owner:
        return None
    trip_state = session.get("trip_state")
    if not isinstance(trip_state, dict):
        return None
    set_id = str(trip_state.get("active_discovery_set_id") or "").strip()
    if not set_id:
        return None
    record = discovery_store.load_discovery_set(set_id, session_id=owner)
    places = record.get("places") if isinstance(record, dict) else None
    return set_id if isinstance(places, list) and places else None


def active_temporary_route_preview(
    session: object | None,
    *,
    session_id: str | None = None,
) -> tuple[str, str] | None:
    """Return a validated, still-presentable temporary route selection.

    Trip state contains only pointers. The candidate store is the authority
    for ownership, expiry, route status, and candidate identity; this helper
    keeps an inherited what-if preview out of the model-visible surface when
    any of those checks fail.
    """

    if not isinstance(session, dict):
        return None
    owner = str(session_id or "").strip()
    if not owner:
        return None
    trip_state = session.get("trip_state")
    if not isinstance(trip_state, dict):
        return None
    set_id = str(trip_state.get("temporary_candidate_set_id") or "").strip()
    candidate_id = str(
        trip_state.get("temporary_selected_candidate_id") or ""
    ).strip()
    if not set_id or not candidate_id:
        return None
    record, entry, error = candidate_store.get_candidate(
        set_id,
        candidate_id,
        session_id=owner,
    )
    if error or not isinstance(record, dict) or not isinstance(entry, dict):
        return None
    if str(record.get("scenario_mode") or "") != "what_if":
        return None
    if record.get("presented"):
        return None
    digest = entry.get("digest")
    if (
        not isinstance(digest, dict)
        or digest.get("hard_constraints_satisfied") is not True
    ):
        return None
    return set_id, candidate_id


def active_route_replay(session: object | None) -> dict[str, str] | None:
    """Return opaque replay handles for the still-valid accepted route card."""

    if not isinstance(session, dict):
        return None
    state = session.get("trip_state")
    candidate_id = (
        str(state.get("selected_candidate_id") or "").strip()
        if isinstance(state, dict)
        else ""
    )
    card = transcript_store.active_accepted_route_card(session)
    card_id = str(card.get("card_id") or "").strip() if card else ""
    if not candidate_id or not card_id:
        return None
    return {"candidate_id": candidate_id, "card_id": card_id}


def state_valid_tool_names(
    evidence: object | None,
    *,
    session: object | None = None,
    session_id: str | None = None,
) -> frozenset[str]:
    """Return tools valid for the backend state, never for rider phrasing."""

    contract = getattr(evidence, "turn_contract", None)
    if contract is None:
        return INITIAL_TOOL_NAMES

    names = {"complete_turn"}
    active_discovery = active_discovery_set_id(session, session_id=session_id)
    active_temporary_route = active_temporary_route_preview(
        session,
        session_id=session_id,
    )
    active_route = active_route_replay(session)
    for goal in contract.goals:
        state = evidence.state_for(goal.goal_key)
        if state == GoalState.EVIDENCE_READY:
            skip_presenter = (
                goal.kind == GoalKind.DESTINATION_SELECTION
                and _feeds_route_selection(contract, goal.goal_key)
                and not _dependent_route_failed(contract, evidence, goal.goal_key)
            )
            presenter = _PRESENTER_BY_GOAL.get(goal.kind)
            place_research_pending = (
                goal.kind
                in {GoalKind.PLACE_RECOMMENDATION, GoalKind.DESTINATION_SELECTION}
                and getattr(evidence, "web_research_required", False)
                and not getattr(evidence, "web_succeeded", False)
            )
            if presenter and not skip_presenter and not place_research_pending:
                names.add(presenter)
            continue
        if state in {
            GoalState.PENDING,
            GoalState.ATTEMPTED_BUT_UNAVAILABLE,
        } and contract.dependencies_ready(goal.goal_key, evidence):
            if (
                goal.kind == GoalKind.ROUTE
                and contract.route_allows_internal_discovery(goal.goal_key)
            ):
                names.add("discover_places")
            if (
                state == GoalState.PENDING
                and goal.kind
                in {GoalKind.PLACE_RECOMMENDATION, GoalKind.DESTINATION_SELECTION}
                and active_discovery
                and not (
                    getattr(evidence, "web_research_required", False)
                    and not getattr(evidence, "web_succeeded", False)
                )
                and not (
                    goal.kind == GoalKind.DESTINATION_SELECTION
                    and _feeds_route_selection(contract, goal.goal_key)
                )
            ):
                names.add("present_places")
            if (
                goal.kind == GoalKind.ROUTE
                and (
                    (state == GoalState.PENDING and (active_temporary_route or active_route))
                    or (
                        state == GoalState.ATTEMPTED_BUT_UNAVAILABLE
                        and active_route
                    )
                )
            ):
                names.add("present_route")
            capability = _CAPABILITY_BY_GOAL.get(goal.kind)
            if capability:
                names.add(capability)
    return frozenset(names)


def required_presenter_tool(
    evidence: object,
    allowed_tool_names: frozenset[str],
) -> str | None:
    """Return the one presenter required by current backend state, if any.

    The model still chooses the selected IDs and rider-facing framing. This
    only prevents it from skipping a canonical presentation obligation after
    evidence is ready. When multiple goals are ready, the model retains the
    sequencing decision instead of the backend choosing arbitrarily.
    """

    contract = getattr(evidence, "turn_contract", None)
    if contract is None:
        return None
    decision = turn_completion.evaluate_completion(contract, evidence)
    if len(decision.required_next_actions) != 1:
        return None
    action = decision.required_next_actions[0]
    if not action.startswith("present:"):
        return None
    goal = contract.get_goal(action.removeprefix("present:"))
    presenter = _PRESENTER_BY_GOAL.get(goal.kind) if goal is not None else None
    return presenter if presenter in allowed_tool_names else None


def schemas_for_state(
    registry_schemas: Iterable[Mapping[str, Any]],
    evidence: object | None,
    *,
    session: object | None = None,
    session_id: str | None = None,
) -> list[dict]:
    """Project the stable vocabulary into the current state-valid subset."""

    valid_names = state_valid_tool_names(
        evidence,
        session=session,
        session_id=session_id,
    )
    return [
        schema
        for schema in offered_custom_tools(registry_schemas)
        if schema.get("name") in valid_names
    ]


def tool_supports_goal(tool_name: str, goal_kind: GoalKind) -> bool:
    """Whether a model-visible capability may act on this outcome kind."""

    return tool_name in {
        _CAPABILITY_BY_GOAL.get(goal_kind),
        _PRESENTER_BY_GOAL.get(goal_kind),
    }


def is_evidence_capability(tool_name: str) -> bool:
    return tool_name in frozenset(_CAPABILITY_BY_GOAL.values())


def called_evidence_capabilities(tool_calls: Iterable[tuple[str, dict]]) -> tuple[str, ...]:
    """Return de-duplicated evidence capabilities used during one turn."""

    return tuple(
        dict.fromkeys(name for name, _tool_input in tool_calls if is_evidence_capability(name))
    )


def is_presenter(tool_name: str) -> bool:
    return tool_name in frozenset(_PRESENTER_BY_GOAL.values())


def is_public_tool(name: object) -> bool:
    return str(name or "") in PUBLIC_TOOL_NAMES


def is_internal_leaf_tool(name: object) -> bool:
    return str(name or "") in INTERNAL_LEAF_TOOL_NAMES
