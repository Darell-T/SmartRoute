"""Terminal conversational answer, clarification, refusal, or unavailable outcome."""

from __future__ import annotations

from typing import Any

from app.services.agent import events as agent_events
from app.services.agent import trip_state as trip_state_module
from app.services.agent.passenger_output import validated_terminal_message
from app.services.agent.tools._types import ToolContext, ToolResult
from app.services.agent.turn import completion as turn_completion
from app.services.agent.turn.contract import GoalKind, GoalState, TurnContract

COMPLETE_TURN_SCHEMA = {
    "name": "complete_turn",
    "description": (
        "End the turn with a rider-facing answer, a clarification question, "
        "a truthful refusal, cancellation, or an unavailable statement. Do not use "
        "this to present provider-grounded place, transit, or route facts, except "
        "a conversational why-not explanation based only on the server-projected "
        "accepted_route_comparison already in context; do not add a route, card, "
        "or canonical arithmetic."
    ),
    "strict": True,
    "input_schema": {
        "type": "object",
        "properties": {
            "goal_keys": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "Declared turn goals this outcome resolves. For unavailable, "
                    "include only goals whose capability was attempted but unavailable; "
                    "never include a satisfied or canonically presented goal."
                ),
            },
            "outcome": {
                "type": "string",
                "enum": [
                    "answer",
                    "clarification",
                    "refusal",
                    "unavailable",
                    "cancelled",
                ],
            },
            "message": {
                "type": "string",
                "description": (
                    "Rider-facing prose with no tool names or internal IDs. An "
                    "ordinary answer must be self-contained: do not append a question, "
                    "offer optional work, promise future monitoring, or imply another "
                    "action. Use clarification only when rider input is required. A "
                    "truthful unavailable outcome may offer a retry after the relevant "
                    "capability was actually attempted."
                ),
            },
        },
        "required": ["goal_keys", "outcome", "message"],
        "additionalProperties": False,
    },
}

_ROUTE_UNAVAILABLE_MESSAGE = "I could not find a verified route for this request."


def _parse_goal_keys(
    tool_input: dict[str, Any],
    contract: TurnContract,
) -> tuple[tuple[str, ...], ToolResult | None]:
    raw = tool_input.get("goal_keys")
    if not isinstance(raw, list):
        return (), ToolResult(
            ok=False,
            error="goal_keys must be an array",
            internal_diagnostic=True,
        )
    if not raw:
        return (), ToolResult(
            ok=False,
            error="goal_keys must identify at least one declared goal",
            internal_diagnostic=True,
        )
    keys: list[str] = []
    seen: set[str] = set()
    for raw_key in raw:
        if not isinstance(raw_key, str) or not raw_key.strip():
            return (), ToolResult(
                ok=False,
                error="goal_keys must contain non-empty strings",
                internal_diagnostic=True,
            )
        key = raw_key.strip()
        if key in seen:
            return (), ToolResult(
                ok=False,
                error="goal_keys must not contain duplicates",
                internal_diagnostic=True,
            )
        seen.add(key)
        keys.append(key)
    unknown = [key for key in keys if contract.get_goal(key) is None]
    if unknown:
        return (), ToolResult(
            ok=False,
            error="goal_keys contains an unknown declared goal",
            internal_diagnostic=True,
        )
    return tuple(keys), None


def _goal_state(evidence: object, goal_key: str) -> GoalState:
    state_for = getattr(evidence, "state_for", None)
    raw = state_for(goal_key) if callable(state_for) else None
    if raw is None:
        states = getattr(evidence, "goal_states", {})
        raw = states.get(goal_key) if isinstance(states, dict) else None
    if raw is None:
        return GoalState.PENDING
    return raw if isinstance(raw, GoalState) else GoalState(str(raw))


def _goal_attempted(evidence: object, goal_key: str) -> bool:
    attempted_for = getattr(evidence, "attempted_for", None)
    if callable(attempted_for):
        return bool(attempted_for(goal_key))
    attempted = getattr(evidence, "goal_attempted", set())
    return goal_key in attempted if isinstance(attempted, set) else False


def _goal_presented(evidence: object, goal_key: str) -> bool:
    presented_for = getattr(evidence, "presented_for", None)
    if callable(presented_for):
        return bool(presented_for(goal_key))
    presented = getattr(evidence, "goal_presented", set())
    return goal_key in presented if isinstance(presented, set) else False


def _projected_facts(
    evidence: object,
    contract: TurnContract,
    goal_keys: tuple[str, ...],
    outcome: str,
) -> tuple[dict[str, dict[str, object]], ToolResult | None]:
    facts = {
        goal.goal_key: {
            "state": _goal_state(evidence, goal.goal_key),
            "attempted": _goal_attempted(evidence, goal.goal_key),
            "presented": _goal_presented(evidence, goal.goal_key),
        }
        for goal in contract.goals
    }
    if outcome == "answer":
        if any(
            contract.goal(key).kind != GoalKind.GENERAL_RESPONSE for key in goal_keys
        ):
            return {}, ToolResult(
                ok=False,
                error=(
                    "answer may target only general_response goals; provider-grounded "
                    "goals use their canonical presenter, and any separately attempted "
                    "but unavailable goals must use outcome=unavailable with only those "
                    "goal_keys without repeating presented facts"
                ),
                internal_diagnostic=True,
            )
        for key in goal_keys:
            facts[key] = {
                "state": GoalState.SATISFIED,
                "attempted": facts[key]["attempted"],
                "presented": facts[key]["presented"],
            }
        return facts, None

    if outcome in {"clarification", "refusal", "cancelled"}:
        for key in goal_keys:
            goal = contract.goal(key)
            if outcome == "refusal" and goal.kind != GoalKind.GENERAL_RESPONSE:
                return {}, ToolResult(
                    ok=False,
                    error="refusal may target only general_response goals",
                    internal_diagnostic=True,
                )
            state = facts[key]["state"]
            if (
                state == GoalState.EVIDENCE_READY
                and goal.kind != GoalKind.GENERAL_RESPONSE
            ):
                return {}, ToolResult(
                    ok=False,
                    error="provider-grounded evidence is ready and must be presented",
                    internal_diagnostic=True,
                )
            if state in {
                GoalState.SATISFIED,
                GoalState.UNSUPPORTED,
                GoalState.CANCELLED_BY_RIDER,
                GoalState.SUPERSEDED,
            }:
                return {}, ToolResult(
                    ok=False,
                    error="goal_keys must target unresolved goals",
                    internal_diagnostic=True,
                )
            state_by_outcome = {
                "clarification": GoalState.BLOCKED_WAITING_FOR_RIDER,
                "refusal": GoalState.UNSUPPORTED,
                "cancelled": GoalState.CANCELLED_BY_RIDER,
            }
            facts[key] = {
                "state": state_by_outcome[outcome],
                "attempted": facts[key]["attempted"],
                "presented": facts[key]["presented"],
            }
        return facts, None

    invalid_keys = [
        key
        for key in goal_keys
        if facts[key]["state"] != GoalState.ATTEMPTED_BUT_UNAVAILABLE
        or not facts[key]["attempted"]
    ]
    if invalid_keys:
        unavailable_keys = [
            goal.goal_key
            for goal in contract.goals
            if facts[goal.goal_key]["state"]
            == GoalState.ATTEMPTED_BUT_UNAVAILABLE
            and facts[goal.goal_key]["attempted"]
        ]
        return {}, ToolResult(
            ok=False,
            error=(
                "outcome=unavailable may target only attempted-but-unavailable "
                f"goals; remove resolved or unattempted goal_keys: {invalid_keys!r}; "
                f"use only these unavailable goal_keys: {unavailable_keys!r}; do not "
                "repeat facts already emitted by a canonical presenter"
            ),
            internal_diagnostic=True,
        )
    return facts, None


def _pending_goal_instruction(
    evidence: object,
    contract: TurnContract,
    goal_keys: tuple[str, ...],
) -> str | None:
    """Give the model a state-derived next action before terminal projection.

    This is an internal tool-result correction.  It never parses rider prose
    and never reaches the passenger, but it prevents a model from repeatedly
    trying to close an already-satisfied goal while silently abandoning a
    dependent outcome.
    """

    resolved = {
        GoalState.SATISFIED,
        GoalState.UNSUPPORTED,
        GoalState.CANCELLED_BY_RIDER,
        GoalState.SUPERSEDED,
    }
    for goal in contract.goals:
        if goal.goal_key in goal_keys:
            continue
        state = _goal_state(evidence, goal.goal_key)
        if state in resolved or (
            state == GoalState.EVIDENCE_READY
            and _goal_presented(evidence, goal.goal_key)
        ):
            continue
        if goal.kind == GoalKind.ROUTE and contract.dependencies_ready(
            goal.goal_key, evidence
        ):
            return (
                "a declared route outcome is still unresolved; choose one "
                "verified destination_place_id from the discovery evidence, "
                "call prepare_route_options with that route goal_key, then "
                "call present_route"
            )
        return f"declared goal {goal.goal_key!r} is still unresolved"
    return None


def _record_outcome_goals(
    evidence: object,
    goal_keys: tuple[str, ...],
    outcome: str,
) -> None:
    if outcome == "answer":
        state = GoalState.SATISFIED
    elif outcome == "clarification":
        state = GoalState.BLOCKED_WAITING_FOR_RIDER
    elif outcome == "refusal":
        state = GoalState.UNSUPPORTED
    elif outcome == "cancelled":
        state = GoalState.CANCELLED_BY_RIDER
    else:
        return
    record_goal = getattr(evidence, "record_goal", None)
    if not callable(record_goal):
        return
    for key in goal_keys:
        record_goal(key, state)


def _apply_complete_turn_side_effects(
    ctx: ToolContext,
    evidence: object,
    outcome: str,
) -> None:
    if outcome == "cancelled" and isinstance(ctx.session, dict):
        trip_state_module.discard_scenario(ctx.session)
    if evidence is not None:
        evidence.mark_terminal("complete_turn")


def _parsed_complete_outcome(tool_input: dict) -> tuple[str, ToolResult | None]:
    outcome = str(tool_input.get("outcome") or "").strip()
    if outcome in {
        "answer",
        "clarification",
        "refusal",
        "unavailable",
        "cancelled",
    }:
        return outcome, None
    return "", ToolResult(
        ok=False,
        error=(
            "outcome must be answer, clarification, refusal, unavailable, "
            "or cancelled"
        ),
        internal_diagnostic=True,
    )


async def execute(tool_input: dict, ctx: ToolContext) -> ToolResult:
    outcome, outcome_error = _parsed_complete_outcome(tool_input)
    if outcome_error:
        return outcome_error
    message = validated_terminal_message(
        tool_input.get("message"),
        outcome=outcome,
    )
    if not message:
        return ToolResult(
            ok=False,
            error=(
                "message is missing, contains unsafe internal language, or adds "
                "an unowned optional/future action; ordinary answers must end "
                "without a question or promise"
            ),
            internal_diagnostic=True,
        )
    evidence = getattr(ctx, "turn_evidence", None)
    contract = getattr(evidence, "turn_contract", None)
    if not isinstance(contract, TurnContract):
        return ToolResult(
            ok=False,
            error=(
                "complete_turn requires a bound TurnContract; declare the turn's "
                "goals before choosing a terminal outcome"
            ),
            internal_diagnostic=True,
        )
    goal_keys, goal_error = _parse_goal_keys(tool_input, contract)
    if goal_error:
        return goal_error

    pending_instruction = _pending_goal_instruction(
        evidence,
        contract,
        goal_keys,
    )
    if pending_instruction:
        return ToolResult(
            ok=False,
            error="cannot complete this turn while " + pending_instruction,
            internal_diagnostic=True,
        )
    projected, projection_error = _projected_facts(
        evidence,
        contract,
        goal_keys,
        outcome,
    )
    if projection_error:
        return projection_error
    if outcome == "unavailable" and any(
        contract.goal(key).kind == GoalKind.ROUTE for key in goal_keys
    ):
        message = _ROUTE_UNAVAILABLE_MESSAGE
    decision = turn_completion.evaluate_completion(contract, projected)
    if not decision.may_terminate:
        detail = ", ".join(decision.required_next_actions) or ", ".join(
            decision.remaining_goal_keys
        )
        return ToolResult(
            ok=False,
            error=(
                "cannot complete this turn while declared goals remain unresolved"
                + (f": {detail}" if detail else "")
            ),
            internal_diagnostic=True,
        )
    _record_outcome_goals(evidence, goal_keys, outcome)
    _apply_complete_turn_side_effects(ctx, evidence, outcome)
    return ToolResult(
        ok=True,
        data={
            "outcome": outcome,
            "message": message,
            **(
                {"turn_resolution": decision.turn_resolution.value}
            ),
        },
        summary="Completed the turn",
        events=[agent_events.TokenEvent(text=message)],
        terminal=True,
        terminal_path="complete_turn",
    )
