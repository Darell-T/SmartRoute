"""Bounded state-aware scenario follow-up actions for live previews.

After a real what-if preview, "Use that instead." accepts the preview and
"Never mind." / "Nevermind." discards it. Recognition is exact (harmless
case/terminal-punctuation normalization only), gated on server-owned trip
state, and never fires on multi-intent or unrelated turns. The turn stream
and tool-profile resolution consume the action before any tool selection or
context building; the canonical ``present_route`` executor and its one-time
presentation consumption stay authoritative -- nothing is auto-committed
from prose and the stored candidate record is never touched.
"""

from __future__ import annotations

import enum

from app.services.agent import trip_state as trip_state_module

_ACCEPT_PHRASE = "use that instead"
_REJECT_PHRASES = frozenset({"never mind", "nevermind"})
_TERMINAL_PUNCTUATION = "!.?"


class ScenarioAction(enum.Enum):
    """Scenario-only follow-up action resolved from live trip state."""

    ACCEPT = "accept"
    REJECT = "reject"


def detect_scenario_action(session: dict, message: object) -> ScenarioAction | None:
    """Return the bounded scenario action for this turn, or None.

    Acceptance requires a live temporary candidate set AND a temporary
    selected candidate (a complete preview). Rejection requires a bound
    temporary scenario. With no temporary scenario both phrases stay
    ordinary turns and this detector never mutates state.
    """

    state = trip_state_module.get_trip_state(session)
    has_temporary_scenario = bool(state.get("temporary_candidate_set_id"))
    normalized = _normalize(message)
    if normalized == _ACCEPT_PHRASE:
        if has_temporary_scenario and bool(
            state.get("temporary_selected_candidate_id")
        ):
            return ScenarioAction.ACCEPT
        return None
    if normalized in _REJECT_PHRASES:
        return ScenarioAction.REJECT if has_temporary_scenario else None
    return None


def _normalize(message: object) -> str:
    text = " ".join(str(message or "").split()).casefold()
    return text.rstrip(_TERMINAL_PUNCTUATION)


__all__ = ("ScenarioAction", "detect_scenario_action")
