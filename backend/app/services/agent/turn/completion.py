"""Pure completion decision plus bounded continuation persistence."""

from __future__ import annotations

import dataclasses
from collections.abc import Iterable, Mapping, Sequence
from enum import StrEnum

from app.services.agent import session as session_module
from app.services.agent.turn.contract import GoalState, OutcomeGoal, TurnContract
from app.services.agent.turn.evidence import TurnEvidence


class TurnResolution(StrEnum):
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    PARTIAL_SUCCESS_WITH_RECOVERY = "partial_success_with_recovery"
    BLOCKED_WAITING_FOR_RIDER = "blocked_waiting_for_rider"
    ATTEMPTED_BUT_UNAVAILABLE = "attempted_but_unavailable"
    UNSUPPORTED = "unsupported"


@dataclasses.dataclass(frozen=True, slots=True)
class CompletionDecision:
    may_terminate: bool
    turn_resolution: TurnResolution
    remaining_goal_keys: tuple[str, ...] = ()
    required_next_actions: tuple[str, ...] = ()
    recovery_options: tuple[str, ...] = ()

    @property
    def approved_recovery_options(self) -> tuple[str, ...]:
        return self.recovery_options


_PRESENTER_TOOLS = {"present_places", "present_transit", "present_route"}
_PERSISTED_RESOLUTIONS = {
    TurnResolution.BLOCKED_WAITING_FOR_RIDER,
    TurnResolution.ATTEMPTED_BUT_UNAVAILABLE,
    TurnResolution.PARTIAL_SUCCESS_WITH_RECOVERY,
}


def _state(value: object) -> GoalState:
    value = getattr(value, "state", value)
    return value if isinstance(value, GoalState) else GoalState(str(value))


def _facts(evidence: object, key: str) -> tuple[GoalState, bool, bool, tuple[str, ...]]:
    """Read the narrow execution interface without mutating the ledger."""
    raw: object = None
    attempted = presented = False
    options: Iterable[str] = ()
    if isinstance(evidence, Mapping):
        raw = evidence.get(key)
        if isinstance(raw, Mapping):
            attempted = bool(raw.get("attempted", False))
            presented = bool(raw.get("presented", False))
            options = (
                raw.get("approved_recovery_options")
                or raw.get("recovery_options")
                or ()
            )
            raw = raw.get("state", GoalState.PENDING)
    elif evidence is not None:
        method = getattr(evidence, "state_for", None)
        if callable(method):
            raw = method(key)
        method = getattr(evidence, "attempted_for", None)
        if callable(method):
            attempted = bool(method(key))
        method = getattr(evidence, "presented_for", None)
        if callable(method):
            presented = bool(method(key))
        method = getattr(evidence, "recovery_options_for", None)
        if callable(method):
            options = method(key)
    if raw is None:
        raw = GoalState.PENDING
    return _state(raw), attempted, presented, _normalise(options)


def _normalise(values: Iterable[str] | None) -> tuple[str, ...]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values or ():
        if not isinstance(value, str) or not value.strip():
            continue
        value = value.strip()
        if value.casefold() not in seen:
            result.append(value)
            seen.add(value.casefold())
    return tuple(result)


def _terminal_resolution(successes: int, failures: list[GoalState]) -> TurnResolution:
    if not failures:
        return TurnResolution.COMPLETED
    if successes:
        return TurnResolution.PARTIAL_SUCCESS_WITH_RECOVERY
    if GoalState.ATTEMPTED_BUT_UNAVAILABLE in failures:
        return TurnResolution.ATTEMPTED_BUT_UNAVAILABLE
    if set(failures) <= {GoalState.CANCELLED_BY_RIDER, GoalState.SUPERSEDED}:
        return TurnResolution.CANCELLED
    if set(failures) <= {GoalState.UNSUPPORTED}:
        return TurnResolution.UNSUPPORTED
    return TurnResolution.BLOCKED_WAITING_FOR_RIDER


def evaluate_completion(
    contract: TurnContract,
    evidence: object = None,
    *,
    presented_goal_keys: Iterable[str] = (),
    approved_recovery_options: Iterable[str] | None = None,
) -> CompletionDecision:
    """Return the only safe terminal decision for current backend facts."""
    if not isinstance(contract, TurnContract):
        raise TypeError("contract must be a TurnContract")
    presented = {str(key) for key in presented_goal_keys}
    remaining: list[str] = []
    actions: list[str] = []
    failures: list[GoalState] = []
    successes = 0
    options: list[str] = list(_normalise(approved_recovery_options))

    for goal in contract.goals:
        goal_remaining, goal_action, failure, success, recovery = _goal_progress(
            contract,
            evidence,
            goal,
            presented,
        )
        options.extend(recovery)
        if goal_remaining is not None:
            remaining.append(goal_remaining)
        if goal_action is not None:
            actions.append(goal_action)
        if failure is not None:
            failures.append(failure)
        if success:
            successes += 1

    options_tuple = _normalise(options)
    if remaining:
        return CompletionDecision(
            False,
            TurnResolution.BLOCKED_WAITING_FOR_RIDER,
            tuple(remaining),
            tuple(actions),
            options_tuple,
        )
    return CompletionDecision(
        True,
        _terminal_resolution(successes, failures),
        recovery_options=options_tuple,
    )


def completion_telemetry(
    contract: TurnContract,
    evidence: object,
) -> dict[str, object]:
    """Project the completion decision without coupling evidence state back here."""

    decision = evaluate_completion(contract, evidence)
    return {
        "turn_resolution": decision.turn_resolution.value,
        "remaining_goal_keys": list(decision.remaining_goal_keys),
        "required_next_actions": list(decision.required_next_actions),
    }


def _goal_progress(
    contract: TurnContract,
    evidence: object,
    goal: OutcomeGoal,
    presented: set[str],
) -> tuple[str | None, str | None, GoalState | None, bool, tuple[str, ...]]:
    goal_key = str(goal.goal_key)
    state, attempted, was_presented, recovery = _facts(evidence, goal_key)
    is_presented = was_presented or goal_key in presented
    if state == GoalState.EVIDENCE_READY and not is_presented:
        return goal_key, f"present:{goal_key}", None, False, recovery
    if state == GoalState.PENDING:
        blockers = contract.dependency_blockers(goal_key, evidence)
        action = (
            f"wait_for:{goal_key}=" + ",".join(blockers)
            if blockers
            else f"execute:{goal_key}"
        )
        return goal_key, action, None, False, recovery
    if state == GoalState.IN_FLIGHT:
        return goal_key, f"await:{goal_key}", None, False, recovery
    if state == GoalState.ATTEMPTED_BUT_UNAVAILABLE and not attempted:
        return goal_key, f"attempt:{goal_key}", None, False, recovery
    if state == GoalState.SATISFIED or (
        state == GoalState.EVIDENCE_READY and is_presented
    ):
        return None, None, None, True, recovery
    if state in {
        GoalState.BLOCKED_WAITING_FOR_RIDER,
        GoalState.ATTEMPTED_BUT_UNAVAILABLE,
        GoalState.UNSUPPORTED,
        GoalState.CANCELLED_BY_RIDER,
        GoalState.SUPERSEDED,
    }:
        return None, None, state, False, recovery
    return goal_key, f"resolve:{goal_key}", None, False, recovery


def _successful_tool_names(
    tool_outcomes: Sequence[tuple[str, dict, object]],
) -> set[str]:
    """Return capability names whose provider/tool result completed successfully."""
    return {
        name
        for name, _tool_input, result in tool_outcomes
        if bool(getattr(result, "ok", False))
    }


def _presenter_path(
    tool_outcomes: Sequence[tuple[str, dict, object]],
) -> str:
    """Find the latest successful passenger presenter for terminal provenance."""
    return next(
        (
            name
            for name, _tool_input, result in reversed(tool_outcomes)
            if name in _PRESENTER_TOOLS and bool(getattr(result, "ok", False))
        ),
        "complete_turn",
    )


def _goal_kinds_in_state(
    contract: TurnContract,
    evidence: TurnEvidence,
    states: set[GoalState],
) -> list[str]:
    """Read goal kinds from the ledger for continuation persistence."""
    return [
        goal.kind.value
        for goal in contract.goals
        if evidence.state_for(goal.goal_key) in states
    ]


def _persist_recovery_continuation(
    session: dict,
    contract: TurnContract,
    evidence: TurnEvidence,
    decision: CompletionDecision,
) -> None:
    """Persist only unresolved, backend-approved recovery work."""
    unresolved = _goal_kinds_in_state(
        contract,
        evidence,
        {
            GoalState.PENDING,
            GoalState.IN_FLIGHT,
            GoalState.EVIDENCE_READY,
            GoalState.BLOCKED_WAITING_FOR_RIDER,
            GoalState.ATTEMPTED_BUT_UNAVAILABLE,
            GoalState.UNSUPPORTED,
        },
    )
    if not unresolved:
        return
    session_module.add_pending_continuation(
        session,
        session_module.PendingContinuation.create(
            unresolved,
            approved_recovery_options=decision.approved_recovery_options,
        ),
    )


def apply_completion(
    session: dict,
    evidence: TurnEvidence,
    tool_outcomes: Sequence[tuple[str, dict, object]],
    *,
    selected_by_model: bool,
) -> CompletionDecision | None:
    """Evaluate the contract and apply only backend-owned terminal effects."""
    contract = evidence.turn_contract
    if contract is None:
        return None
    decision = evaluate_completion(contract, evidence)
    if not decision.may_terminate:
        return decision

    successful_tools = _successful_tool_names(tool_outcomes)
    completed_by_presenter = (
        decision.turn_resolution == TurnResolution.COMPLETED
        and bool(successful_tools & _PRESENTER_TOOLS)
    )
    if "complete_turn" not in successful_tools and not completed_by_presenter:
        return decision

    session_module.resolve_pending_continuations(
        session,
        {
            goal.kind.value
            for goal in contract.goals
            if evidence.state_for(goal.goal_key) == GoalState.SATISFIED
        },
    )
    presenter_path = _presenter_path(tool_outcomes)
    selection_source = evidence.selection_source or (
        "model" if selected_by_model else ""
    )
    evidence.mark_terminal(
        evidence.terminal_path or presenter_path,
        selection_source=selection_source,
    )
    if decision.turn_resolution not in _PERSISTED_RESOLUTIONS:
        return decision

    _persist_recovery_continuation(session, contract, evidence, decision)
    return decision


def fallback_text(evidence: TurnEvidence, *, session_id: str, limit: int) -> str:
    """Resolve the single grounded fallback used by every loop exit."""
    from app.services.agent.passenger_output import truthful_failure_text
    from app.services.agent.tools.places import present_places

    return present_places.try_deterministic_fallback(
        evidence, session_id=session_id, limit=limit
    ) or truthful_failure_text(evidence)


__all__ = [
    "CompletionDecision",
    "TurnResolution",
    "apply_completion",
    "completion_telemetry",
    "evaluate_completion",
    "fallback_text",
]
