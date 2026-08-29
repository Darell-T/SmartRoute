"""Rider-language safety for activity labels, framing, and recovery copy.

Display-only text never reaches capability executors, deduplication keys,
provider boundaries, or authoritative state.
"""

from __future__ import annotations

from collections.abc import Iterable

from app.services.agent import events as agent_events
from app.services.agent.turn.contract import GoalKind, GoalState

ACTIVITY_LABEL_FIELD = "activity_label"
MAX_ACTIVITY_LABEL_CHARS = 100
MAX_PRESENTATION_FRAMING_CHARS = 240
MAX_RESEARCH_PRESENTATION_FRAMING_CHARS = 640
MAX_TERMINAL_MESSAGE_CHARS = 1600

_INTERNAL_WORDS = frozenset(
    {
        "api",
        "anthropic",
        "backend",
        "candidate_id",
        "candidate_set_id",
        "claude",
        "database",
        "debug",
        "discovery_set_id",
        "evidence_set_id",
        "function",
        "goal_key",
        "google",
        "grok",
        "gtfs",
        "json",
        "model",
        "prompt",
        "provider",
        "route_id",
        "schema",
        "server",
        "sonnet",
        "tool",
        "trace",
        "xai",
    }
)
_OPAQUE_ID_FRAGMENTS = ("cd_", "ds_", "pl_", "te_", "tu_")
_TIMING_PROMISES = (
    "a couple of minutes",
    "a few minutes",
    "a few seconds",
    "in a moment",
    "right away",
    "shortly",
    "this will only take",
    "won't take long",
)
_RESULT_CLAIM_PREFIXES = (
    "confirmed ",
    "found ",
    "i found ",
    "i confirmed ",
    "i verified ",
    "i've found ",
    "no delays",
    "selected ",
    "the best option is",
    "the route is",
    "verified ",
    "we confirmed ",
    "we found ",
    "we verified ",
    "we've found ",
    "your route is",
)
_RESULT_CLAIM_FRAGMENTS = (
    " are open",
    " arrives in",
    " best route is",
    " fastest route is",
    " has no delays",
    " is open",
    " no active alerts",
    " no delays",
    " will arrive",
)
_FLUFF_ONLY = frozenset(
    {
        "got it",
        "okay",
        "ok",
        "on it",
        "sounds good",
        "sure",
        "working on it",
    }
)
_TIME_UNITS = ("second", "minute", "hour")


def _contains_numeric_timing(text: str) -> bool:
    words = [word.strip(".,:;!?()[]{}").casefold() for word in text.split()]
    for index, word in enumerate(words[:-1]):
        number = word.replace(".", "", 1)
        if number.isdigit() and words[index + 1].startswith(_TIME_UNITS):
            return True
    for word in words:
        number, separator, unit = word.partition("-")
        if separator and number.isdigit() and unit.startswith(_TIME_UNITS):
            return True
    return False


def _contains_internal_language(text: str) -> bool:
    if any("_" in word for word in text.split()):
        return True
    words = {
        word.strip(".,:;!?()[]{}—").casefold()
        for word in text.replace("/", " ").split()
    }
    return bool(words & _INTERNAL_WORDS)


def _contains_opaque_identifier(text: str) -> bool:
    normalized = text.casefold()
    if any(fragment in normalized for fragment in _OPAQUE_ID_FRAGMENTS):
        return True
    return any(
        word.casefold().startswith("chij") and len(word) >= 10
        for word in text.split()
    )


def validated_activity_label(value: object) -> str | None:
    """Return concise safe activity copy, or ``None`` for server fallback."""
    if not isinstance(value, str) or any(mark in value for mark in "\r\n\t"):
        return None
    label = " ".join(value.split()).strip()
    if not label or len(label) > MAX_ACTIVITY_LABEL_CHARS:
        return None
    normalized = label.casefold().strip(" .!?…")
    if normalized in _FLUFF_ONLY:
        return None
    if _contains_internal_language(normalized):
        return None
    if _contains_opaque_identifier(normalized):
        return None
    if any(promise in normalized for promise in _TIMING_PROMISES):
        return None
    if _contains_numeric_timing(normalized):
        return None
    if normalized.startswith(_RESULT_CLAIM_PREFIXES):
        return None
    if any(claim in normalized for claim in _RESULT_CLAIM_FRAGMENTS):
        return None
    return label


def validated_presentation_framing(
    value: object,
    *,
    max_chars: int = MAX_PRESENTATION_FRAMING_CHARS,
) -> str | None:
    """Return bounded rider-facing presenter copy, or ``None`` when unsafe."""
    if not isinstance(value, str) or any(mark in value for mark in "\r\n\t"):
        return None
    framing = " ".join(value.split()).strip()
    if len(framing) > max_chars:
        return None
    if _contains_internal_language(framing) or _contains_opaque_identifier(framing):
        return None
    return framing


def _has_unowned_continuation(message: str) -> bool:
    """Detect syntax that leaves an ordinary answer promising more work.

    This is deliberately an output-contract check, not rider-intent parsing.
    Questions belong to ``clarification`` and future first-person commitments
    require an executable, backend-owned continuation. Ordinary answers have
    neither permission, so they must stand on their own.
    """

    normalized = " " + message.casefold().replace("\u2019", "'") + " "
    if "?" in normalized:
        return True
    if any(
        future_subject in normalized
        for future_subject in (" i'll ", " i will ", " we'll ", " we will ")
    ):
        return True
    has_first_person_modal = any(
        modal in normalized
        for modal in (" i can ", " i could ", " we can ", " we could ")
    )
    has_future_condition = any(
        condition in normalized
        for condition in (" if ", " when ", " once ")
    )
    return has_first_person_modal and has_future_condition


def validated_terminal_message(
    value: object,
    *,
    outcome: str | None = None,
) -> str | None:
    """Return formatted conversational prose only when it is safe to expose."""
    if not isinstance(value, str):
        return None
    raw = value.replace("\r\n", "\n").replace("\r", "\n")
    lines = [" ".join(line.split()) for line in raw.split("\n")]
    while lines and not lines[0]:
        lines.pop(0)
    while lines and not lines[-1]:
        lines.pop()
    message = "\n".join(lines)
    if not message or len(message) > MAX_TERMINAL_MESSAGE_CHARS:
        return None
    if _contains_internal_language(message) or _contains_opaque_identifier(message):
        return None
    if outcome == "answer" and _has_unowned_continuation(message):
        return None
    return message


def pop_activity_label(tool_input: dict) -> str | None:
    """Remove and validate display metadata before capability execution."""
    return validated_activity_label(tool_input.pop(ACTIVITY_LABEL_FIELD, None))


def validated_framing(
    tool_input: dict,
    *,
    lead_in_max_chars: int = MAX_PRESENTATION_FRAMING_CHARS,
) -> tuple[str, str, str | None]:
    values: list[str] = []
    for field in ("lead_in", "follow_up"):
        max_chars = (
            lead_in_max_chars
            if field == "lead_in"
            else MAX_PRESENTATION_FRAMING_CHARS
        )
        normalized = validated_presentation_framing(
            tool_input.get(field, ""),
            max_chars=max_chars,
        )
        if normalized is None:
            return "", "", f"{field} is invalid"
        values.append(normalized)
    return values[0], values[1], None


def framed_events(
    events: Iterable[agent_events.AgentEvent],
    lead_in: str,
    follow_up: str,
) -> list[agent_events.AgentEvent]:
    """Join prose and canonical events with explicit semantic separators."""
    framed: list[agent_events.AgentEvent] = []
    if lead_in:
        framed.append(agent_events.TokenEvent(text=f"{lead_in}\n\n"))
    framed.extend(events)
    if follow_up:
        framed.append(agent_events.TokenEvent(text=f"\n\n{follow_up}"))
    return framed


def append_text(parts: list[str], text: str) -> str:
    """Append one visible segment with a stable paragraph boundary."""
    body = ("\n\n" if parts else "") + text.strip()
    parts.append(body)
    return body


_UNRESOLVED_ACTION_FALLBACK = (
    "I couldn't complete that request in this turn, so I don't have a "
    "verified result to share."
)
_RESOLVED_GOAL_STATES = frozenset(
    {GoalState.SATISFIED, GoalState.CANCELLED_BY_RIDER, GoalState.SUPERSEDED}
)


def truthful_failure_text(evidence: object) -> str:
    """Non-factual recovery copy for unresolved goals."""
    mark_terminal = getattr(evidence, "mark_terminal", None)
    if callable(mark_terminal):
        mark_terminal("truthful_failure")
    unresolved = _first_unresolved_goal(evidence)
    if (
        unresolved is not None
        and unresolved[0] == GoalKind.ROUTE
        and unresolved[1] != GoalState.BLOCKED_WAITING_FOR_RIDER
    ) or (unresolved is None and _route_goal_is_unresolved(evidence)):
        route_text = _precise_route_failure_text(evidence)
        if route_text is not None:
            return route_text
    return _unresolved_goal_failure_text(evidence) or _UNRESOLVED_ACTION_FALLBACK


def _goal_is_unresolved(evidence: object, goal_key: str) -> bool:
    state = evidence.state_for(goal_key)
    if state in _RESOLVED_GOAL_STATES:
        return False
    return not (state == GoalState.EVIDENCE_READY and evidence.presented_for(goal_key))


def _route_goal_is_unresolved(evidence: object) -> bool:
    contract = getattr(evidence, "turn_contract", None)
    if contract is None:
        return False
    route_goals = [goal for goal in contract.goals if goal.kind == GoalKind.ROUTE]
    return bool(route_goals) and any(
        _goal_is_unresolved(evidence, goal.goal_key) for goal in route_goals
    )


def _first_unresolved_goal(evidence: object) -> tuple[GoalKind, GoalState] | None:
    contract = getattr(evidence, "turn_contract", None)
    if contract is None:
        return None
    for goal in contract.goals:
        if _goal_is_unresolved(evidence, goal.goal_key):
            return goal.kind, evidence.state_for(goal.goal_key)
    return None


def _precise_route_failure_text(evidence: object) -> str | None:
    contract = getattr(evidence, "turn_contract", None)
    if contract is None:
        return None
    route_goals = [goal for goal in contract.goals if goal.kind == GoalKind.ROUTE]
    if any(
        evidence.state_for(goal.goal_key) == GoalState.EVIDENCE_READY
        and not evidence.presented_for(goal.goal_key)
        for goal in route_goals
    ):
        return (
            "I couldn't present a verified route from the prepared options "
            "in this turn."
        )
    if any(
        evidence.state_for(goal.goal_key) == GoalState.ATTEMPTED_BUT_UNAVAILABLE
        and evidence.attempted_for(goal.goal_key)
        for goal in route_goals
    ):
        return "I could not find a verified route for that trip."
    return None


def _unresolved_goal_failure_text(evidence: object) -> str | None:
    unresolved = _first_unresolved_goal(evidence)
    if unresolved is None:
        return None
    kind, state = unresolved
    if state == GoalState.BLOCKED_WAITING_FOR_RIDER:
        return "I still need one detail before I can finish this."
    if kind in {GoalKind.PLACE_RECOMMENDATION, GoalKind.DESTINATION_SELECTION}:
        return "I couldn't verify a place that matches this request."
    if kind in {
        GoalKind.SERVICE_STATUS,
        GoalKind.ARRIVALS,
        GoalKind.ACCESSIBILITY,
        GoalKind.TRANSIT_FACT,
    }:
        return "I couldn't finish checking the current transit conditions."
    if kind == GoalKind.ROUTE:
        return "I couldn't finish preparing a verified route for this trip."
    if kind in {GoalKind.EVENT_OR_CROWD, GoalKind.AREA_CONDITIONS}:
        return "I couldn't verify the crowd or area conditions for this request."
    return None


__all__ = [
    "ACTIVITY_LABEL_FIELD",
    "MAX_ACTIVITY_LABEL_CHARS",
    "MAX_PRESENTATION_FRAMING_CHARS",
    "MAX_RESEARCH_PRESENTATION_FRAMING_CHARS",
    "MAX_TERMINAL_MESSAGE_CHARS",
    "append_text",
    "framed_events",
    "pop_activity_label",
    "truthful_failure_text",
    "validated_activity_label",
    "validated_framing",
    "validated_presentation_framing",
    "validated_terminal_message",
]
