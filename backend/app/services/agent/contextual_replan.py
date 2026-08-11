"""Bounded state-aware recognition for accepted-trip replanning."""

from __future__ import annotations

import re

from app.services.agent import intelligence
from app.services.agent import profile as profile_module
from app.services.agent import trip_state as trip_state_module

_FROM_SAVED_TO_SAVED = re.compile(
    r"^from\s+(?P<origin>(?:my\s+)?(?:home|work))\s+to\s+"
    r"(?P<destination>(?:my\s+)?(?:home|work))[.!?]*$",
    re.IGNORECASE,
)
_SAME_DESTINATION_SUFFIX = re.compile(
    r"^(?:[.!?]*|\s+(?:but|using|use|via|with|without|instead)\b.*)$",
    re.IGNORECASE,
)


def is_contextual_replan(session: dict | None, message: object) -> bool:
    """True only for a new-trip-shaped reference to accepted endpoints."""

    if not isinstance(session, dict):
        return False
    text = " ".join(str(message or "").split())
    if not text or not intelligence.is_new_trip_request(text):
        return False
    state = trip_state_module.get_trip_state(session)
    if not _has_accepted_trip(state):
        return False

    destination_aliases = _endpoint_aliases(session, state.get("destination"))
    from_to = _FROM_SAVED_TO_SAVED.fullmatch(text)
    if from_to is not None:
        origin_aliases = _endpoint_aliases(session, state.get("origin"))
        return (
            _normalize_saved_reference(from_to.group("origin")) in origin_aliases
            and _normalize_saved_reference(from_to.group("destination"))
            in destination_aliases
        )
    return any(_references_same_destination(text, alias) for alias in destination_aliases)


def has_authoritative_endpoints(session: dict | None, message: object) -> bool:
    """Whether an accepted-trip replan can prepare without endpoint prose.

    The backend owns these endpoints. This predicate is used to require the
    canonical preparation tool, never to ask the model to copy coordinates or
    reconstruct addresses from prompt text.
    """

    if not isinstance(session, dict):
        return False
    state = trip_state_module.get_trip_state(session)
    if not state.get("origin") or not _has_accepted_trip(state):
        return False
    parsed = intelligence.parse_intent(str(message or ""))
    if parsed.intent != "route_planning":
        return False
    if intelligence.is_new_trip_request(str(message or "")):
        return is_contextual_replan(session, message)
    return True


def _has_accepted_trip(state: dict) -> bool:
    return (
        bool(state.get("destination"))
        and bool(state.get("active_candidate_set_id"))
        and bool(state.get("selected_candidate_id"))
    )


def _endpoint_aliases(session: dict, endpoint: object) -> set[str]:
    canonical = _normalize(endpoint)
    if not canonical:
        return set()
    aliases = {canonical}
    for slot in ("home", "work"):
        saved = profile_module.profile_place(session, slot)
        if saved is None:
            continue
        saved_label = _normalize(saved.get("label"))
        if saved_label == canonical:
            aliases.update({slot, saved_label})
    return aliases


def _references_same_destination(text: str, alias: str) -> bool:
    match = re.search(
        rf"\bto\s+(?:my\s+)?{re.escape(alias)}(?P<suffix>.*)$",
        text,
        re.IGNORECASE,
    )
    return bool(match and _SAME_DESTINATION_SUFFIX.fullmatch(match.group("suffix")))


def _normalize_saved_reference(value: object) -> str:
    return _normalize(value).removeprefix("my ")


def _normalize(value: object) -> str:
    return " ".join(str(value or "").casefold().split()).strip(".!?")


__all__ = ("has_authoritative_endpoints", "is_contextual_replan")
