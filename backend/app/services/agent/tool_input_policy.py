"""Validate and normalize model capability inputs before execution."""

from __future__ import annotations

from app.services.agent import public_surface
from app.services.agent.model import policy as agent_policy
from app.services.agent.tools import ToolContext
from app.services.agent.turn.contract import GoalKind, GoalState
from app.services.trips.preparation.input import normalize_route_ids

WEB_PLACE_REQUIRED_ERROR = (
    "web-introduced places must be verified with discover_places before "
    "routing; pass destination_place_id from the current discovery set"
)
DISCOVERY_PLACE_REQUIRED_ERROR = (
    "destination place reference required; pass destination_place_id from "
    "the current discover_places set"
)
GOALS_REQUIRED_ERROR = "declare_goals must succeed before this capability"
DESTINATION_SOURCE_REQUIRED_ERROR = (
    "destination_source must be current_turn or accepted_trip"
)
CURRENT_DESTINATION_REQUIRED_ERROR = (
    "the current-turn destination is missing; pass destination or a verified "
    "destination_place_id, or clarify which place the rider means"
)


def _stripped(value: object) -> str:
    return str(value or "").strip()


def _complete_turn_goal_error(tool_input: dict, contract) -> str | None:
    goal_keys = tool_input.get("goal_keys")
    if not isinstance(goal_keys, list) or not goal_keys:
        return "complete_turn requires at least one declared goal_key"
    if any(contract.get_goal(str(key or "").strip()) is None for key in goal_keys):
        return "complete_turn referenced an unknown goal_key"
    return None


def _evidence_capability_error(evidence, contract, goal_key: str) -> str | None:
    if evidence.state_for(goal_key) not in {
        GoalState.PENDING,
        GoalState.ATTEMPTED_BUT_UNAVAILABLE,
    }:
        return "evidence capability is not valid for the goal's current state"
    blockers = contract.dependency_blockers(goal_key, evidence)
    if blockers:
        return "goal dependencies are not ready: " + ", ".join(blockers)
    return None


def _place_research_error(name: str, tool_input: dict, evidence) -> str | None:
    if name != "present_places" or not getattr(
        evidence, "web_research_required", False
    ):
        return None
    if not getattr(evidence, "web_succeeded", False):
        return (
            "present_places requires successful current-turn web research "
            "before presenting details from a verified place"
        )
    if tool_input.get("research_used") is not True:
        return "present_places must present the successful current-turn research"
    return None


def _tool_satisfies_declared_goal(name: str, goal, contract, goal_key: str) -> bool:
    if public_surface.tool_supports_goal(name, goal.kind):
        return True
    return (
        name == "discover_places"
        and goal.kind == GoalKind.ROUTE
        and contract.route_allows_internal_discovery(goal_key)
    )


def _presenter_reuse_allowed(
    name: str,
    tool_input: dict,
    ctx: ToolContext,
    evidence,
    contract,
    goal_key: str,
    state,
) -> bool:
    if state != GoalState.PENDING:
        return False
    if contract.dependency_blockers(goal_key, evidence):
        return False
    if name == "present_places":
        requested = _stripped(tool_input.get("discovery_set_id"))
        active = public_surface.active_discovery_set_id(
            ctx.session,
            session_id=ctx.session_id,
        )
        return requested == active
    if name != "present_route":
        return False
    preview = public_surface.active_temporary_route_preview(
        ctx.session,
        session_id=ctx.session_id,
    )
    if preview is None:
        return False
    return preview[1] == _stripped(tool_input.get("candidate_id"))


def goal_error(name: str, tool_input: dict, ctx: ToolContext) -> str | None:
    """Return a bounded contract error for an invalid capability action."""

    if name in {"declare_goals", "web_search"} or not public_surface.is_public_tool(
        name
    ):
        return None
    evidence = getattr(ctx, "turn_evidence", None)
    contract = getattr(evidence, "turn_contract", None)
    if contract is None:
        return GOALS_REQUIRED_ERROR
    if name == "complete_turn":
        return _complete_turn_goal_error(tool_input, contract)

    goal_key = _stripped(tool_input.get("goal_key"))
    goal = contract.get_goal(goal_key)
    if goal is None:
        return "capability requires a declared goal_key"
    if not _tool_satisfies_declared_goal(name, goal, contract, goal_key):
        return f"{name} cannot satisfy the declared {goal.kind.value} outcome"
    if public_surface.is_evidence_capability(name):
        return _evidence_capability_error(evidence, contract, goal_key)
    if not public_surface.is_presenter(name):
        return None
    research_error = _place_research_error(name, tool_input, evidence)
    if research_error:
        return research_error
    state = evidence.state_for(goal_key)
    if state != GoalState.EVIDENCE_READY and not _presenter_reuse_allowed(
        name, tool_input, ctx, evidence, contract, goal_key, state
    ):
        return "presenter requires ready server-owned evidence"
    return None


def _has_destination_ids(destination_ids: object) -> bool:
    if not isinstance(destination_ids, list):
        return False
    return any(_stripped(value) for value in destination_ids)


def _current_turn_destination_error(tool_input: dict, evidence) -> str | None:
    destination_id = _stripped(tool_input.get("destination_place_id"))
    has_destination_ids = _has_destination_ids(tool_input.get("destination_place_ids"))
    destination = _stripped(tool_input.get("destination"))
    if destination_id or has_destination_ids:
        return None
    source = _stripped(tool_input.get("destination_source"))
    if source == "current_turn" and not destination:
        return CURRENT_DESTINATION_REQUIRED_ERROR
    if evidence is None:
        return None
    if destination.startswith("pl_"):
        return None
    if getattr(evidence, "web_used", False):
        return WEB_PLACE_REQUIRED_ERROR
    if getattr(evidence, "discovery_set_id", None) and getattr(
        evidence, "verified_place_count", 0
    ):
        return DISCOVERY_PLACE_REQUIRED_ERROR
    return None


def missing_verified_destination(
    name: str,
    tool_input: dict,
    ctx: ToolContext,
) -> str | None:
    """Require an opaque verified place after discovery or current-turn web."""

    if name != "prepare_route_options":
        return None
    destination_source = _stripped((tool_input or {}).get("destination_source"))
    if destination_source not in {"current_turn", "accepted_trip"}:
        return DESTINATION_SOURCE_REQUIRED_ERROR
    evidence = getattr(ctx, "turn_evidence", None)
    return _current_turn_destination_error(tool_input or {}, evidence)


def authoritative_discovery_input(
    name: str,
    tool_input: dict,
    ctx: ToolContext,
) -> dict:
    """Keep a bounded search refinement tied to its server-owned query."""

    refinement = getattr(ctx, "discovery_refinement", None)
    if name != "discover_places" or not isinstance(refinement, dict):
        return tool_input
    return dict(refinement)


def rider_excluded_modes(message: str, session: dict) -> set[str]:
    """Return only constraints already accepted into server-owned state."""

    del message
    constraints = (session.get("slots") or {}).get("constraints") or {}
    return {
        str(mode).strip().upper()
        for mode in (constraints.get("exclude_modes") or [])
        if str(mode).strip()
    }


def rider_excluded_route_ids(message: str, session: dict) -> tuple[str, ...]:
    """Return route exclusions already accepted into server-owned state."""

    del message
    constraints = (session.get("slots") or {}).get("constraints") or {}
    return normalize_route_ids(constraints.get("excluded_route_ids") or [])


def _constrained_route_modes(normalized: dict, excluded_modes: set[str]) -> None:
    allowed_modes = {
        str(mode).strip().upper()
        for mode in (normalized.get("allowed_modes") or [])
        if str(mode).strip().upper() in {"BUS", "SUBWAY", "RAIL"}
    }
    requested_modes = {
        str(mode).strip().upper()
        for mode in (normalized.get("exclude_modes") or [])
        if str(mode).strip().upper() in {"BUS", "SUBWAY", "RAIL"}
    }
    effective_modes = (excluded_modes - allowed_modes) | requested_modes
    if excluded_modes or allowed_modes or "exclude_modes" in normalized:
        normalized["exclude_modes"] = sorted(effective_modes)


def _constrained_route_ids(
    normalized: dict, excluded_route_ids: tuple[str, ...]
) -> None:
    excluded_routes = set(normalize_route_ids(excluded_route_ids))
    allowed_routes = set(
        normalize_route_ids(normalized.get("allowed_route_ids") or [])
    )
    excluded_routes.difference_update(allowed_routes)
    excluded_routes.update(
        normalize_route_ids(normalized.get("excluded_route_ids") or [])
    )
    if excluded_routes:
        normalized["excluded_route_ids"] = sorted(excluded_routes)
    elif excluded_route_ids or allowed_routes or "excluded_route_ids" in normalized:
        normalized["excluded_route_ids"] = []


def constrained_tool_input(
    name: str,
    tool_input: dict,
    excluded_modes: set[str],
    *,
    mode_policy: agent_policy.AgentModePolicy,
    excluded_route_ids: tuple[str, ...] = (),
) -> dict:
    """Merge model arguments with authoritative persisted route constraints."""

    normalized = dict(tool_input)
    if name == "prepare_route_options":
        _constrained_route_modes(normalized, excluded_modes)
        _constrained_route_ids(normalized, excluded_route_ids)
        normalized.pop("allowed_modes", None)
        normalized.pop("allowed_route_ids", None)
        normalized["max_candidates"] = mode_policy.max_route_candidates
        avoid_crowds = bool(normalized.get("avoid_crowds"))
        normalized["avoid_crowds"] = avoid_crowds
        if mode_policy.mode != "auto" or avoid_crowds:
            normalized["crowd_search_mode"] = (
                "auto" if avoid_crowds else mode_policy.mode
            )
        normalized["include_first_leg_arrivals"] = mode_policy.optional_enrichment
    return normalized


__all__ = [
    "CURRENT_DESTINATION_REQUIRED_ERROR",
    "DESTINATION_SOURCE_REQUIRED_ERROR",
    "authoritative_discovery_input",
    "constrained_tool_input",
    "goal_error",
    "missing_verified_destination",
    "rider_excluded_modes",
    "rider_excluded_route_ids",
]
