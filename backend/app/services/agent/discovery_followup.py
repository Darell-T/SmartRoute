"""Bounded state-aware discovery-result follow-up policy.

Deterministic, state-aware follow-up actions extend the existing
new-trip handoff reset (C-DISC P1):

- SELECT: a pure, unambiguous ordinal-result reference ("The second one.")
  against a live active discovery set resolves exactly ONE place through the
  real ``get_place_details`` executor -- no search, route, status, or web
  surface on that turn.
- ROUTE_SELECTED: an unambiguous navigation pronoun ("Take me there.") when
  the same session already bound BOTH an active discovery set and a selected
  place reuses the canonical route profile (get_place_details,
  prepare_route_options, present_route, accessibility_status) with no
  discovery search or web/status surface.
- ADD_WAYPOINT: a navigation handoff with the explicit "first" modifier
  ("Take me to the second one first.") when the same session holds an
  accepted destination route AND a live active discovery set. It is NOT a
  new trip: the whole accepted trip, discovery set, and waypoint state
  survive until canonical preparation succeeds, and the turn offers only
  the canonical route profile (the model resolves the ordinal through
  ``get_place_details`` and adds the opaque id as the ordered waypoint).
- REMOVE_ONLY_WAYPOINT: an explicit removal ("Actually remove the pizza
  stop.") when the session holds an accepted destination route and EXACTLY
  ONE current waypoint, and the descriptive token is generic or matches the
  sole server-owned waypoint label. The turn offers only the canonical
  route profile; no discovery search or ``get_place_details`` executes.
- SEARCH_AGAIN: an exact recovery ("Okay, search again.") when the same
  session still binds an active discovery-set reference. The turn offers
  exactly the one structured ``search_local_places`` client tool, which
  binds a fresh server-owned set and clears the stale selected-place
  association; no route/status/web surface and no reactivation of the
  expired set. Only punctuation/case variants of that exact phrase count;
  extra clauses, multi-intent text, injection tails, or no active discovery
  context never activate it.

Recognition is a tiny deterministic grammar (bounded ordinal reference /
navigation pronoun / waypoint mutation), gated on actually bound
server-owned state, and never fires on unrelated, ambiguous, multi-intent,
out-of-range, or missing-context text. With no active set, no selected
place, no accepted trip, no waypoint, or an out-of-range ordinal no action
activates and no state is fabricated. The actions never broaden the global
stateless ``parse_intent`` grammar.

An accepted trip means presentation/acceptance actually happened: the
session owns a destination, the active candidate set, AND the presented
selected candidate bound by ``present_route`` / ``commit_scenario``. A
merely prepared route (``prepare_route_options`` binds the set but clears
``selected_candidate_id``) never counts as accepted, so it can never
activate a waypoint mutation or suppress an ordinary new-trip reset.

The new-trip handoff reset ("Take me to the second one.") keeps only the
active discovery SET id alive long enough for the real ``get_place_details``
executor to resolve the ordinal against the real store; ``selected_place_id``
stays cleared until ``get_place_details`` binds the real place.
"""

from __future__ import annotations

import enum
import re

from app.services.agent import discovery_store
from app.services.agent import session as session_module
from app.services.agent import trip_state as trip_state_module

_ORDINAL_WORDS = (
    r"first|second|third|fourth|fifth|sixth|seventh|eighth"
)
# Authoritative bound: the store keeps at most MAX_PLACES places, so only
# ordinals 1..8 are supported (numeric forms are 1-8; word forms stop at
# "eighth"). Imported once at module load, never at request time.
_NUMERIC_ORDINAL = rf"[1-{discovery_store.MAX_PLACES}]"
# One bounded ordinal-result reference: "the second one", "the 2nd", "#2",
# "number two", "the third option", etc. Numeric forms carry an explicit
# ordinal marker (#, st/nd/rd/th, or the word "number") so bare digits never
# match; ordinals beyond the store's MAX_PLACES vocabulary (e.g. #9, 99th,
# number nine) never match.
_REFERENCE = re.compile(
    r"(?:the\s+)?(?:"
    rf"#{_NUMERIC_ORDINAL}|"
    rf"(?:{_ORDINAL_WORDS})|"
    rf"{_NUMERIC_ORDINAL}(?:st|nd|rd|th)|"
    rf"number\s+(?:one|two|three|four|five|six|seven|eight|{_NUMERIC_ORDINAL})"
    r")(?:\s+(?:one|option|result|place|pick|choice|spot))?"
)
# Full navigation handoff: a new-trip imperative plus the reference, with
# nothing else. Verbs mirror the new-trip detector's navigation verbs so the
# reset path this policy guards is exactly the path that runs.
_HANDOFF = re.compile(
    r"^(?:take|get|route|head|go)\s+(?:me\s+)?(?:to|over\s+to)\s+"
    + _REFERENCE.pattern
    + r"[!.?]*$",
    re.IGNORECASE,
)
# Pure selection: the entire message is exactly one bounded ordinal-result
# reference ("The second one."), with no verb and no extra clause.
_SELECTION = re.compile(
    r"^" + _REFERENCE.pattern + r"[!.?]*$",
    re.IGNORECASE,
)
# Navigation pronoun: the entire message is one unambiguous reference to the
# already-selected place ("Take me there."), with no destination clause.
_NAVIGATION_PRONOUN = re.compile(
    r"^(?:take|get|route|head|go)\s+(?:me\s+)?there[!.?]*$",
    re.IGNORECASE,
)
# Waypoint-first navigation: the bounded ordinal-reference handoff plus the
# explicit "first" modifier ("Take me to the second one first."). The
# trailing "first" is what makes this a waypoint mutation, never a
# destination handoff, so the direct handoff grammar above stays untouched.
_WAYPOINT_FIRST = re.compile(
    r"^(?:take|get|route|head|go)\s+(?:me\s+)?(?:to|over\s+to)\s+"
    + _REFERENCE.pattern
    + r"\s+first[!.?]*$",
    re.IGNORECASE,
)
# Generic removal ("remove that stop", "remove the waypoint"): no descriptor.
_REMOVE_GENERIC = re.compile(
    r"^(?:actually\s+)?remove\s+(?:that|the)\s+(?:stop|waypoint)[!.?]*$",
    re.IGNORECASE,
)
# Described removal ("remove the pizza stop"): one bounded word descriptor.
_REMOVE_DESCRIBED = re.compile(
    r"^(?:actually\s+)?remove\s+the\s+"
    r"(?P<descriptor>[a-z0-9]+(?:\s+[a-z0-9]+)*)"
    r"\s+(?:stop|waypoint)[!.?]*$",
    re.IGNORECASE,
)


class DiscoveryFollowupAction(enum.Enum):
    """State-aware discovery-result follow-up action for one turn."""

    SELECT = "select"
    ROUTE_SELECTED = "route_selected"
    ADD_WAYPOINT = "add_waypoint"
    REMOVE_ONLY_WAYPOINT = "remove_only_waypoint"
    SEARCH_AGAIN = "search_again"


# Exact recovery: the whole message is "Okay, search again." with only
# punctuation/case variants. The "okay" prefix and the trailing "again" are
# required so bare "Search again.", other prefixes, destination clauses,
# multi-intent text, and injection suffixes never match.
_SEARCH_AGAIN = re.compile(
    r"^(?:okay)[,.]?\s+search\s+again[!.?]*$",
    re.IGNORECASE,
)


def detect_followup_action(
    session: dict | None, message: object
) -> DiscoveryFollowupAction | None:
    """Return the bounded discovery follow-up action for this turn, or None.

    SELECT requires a live active discovery set AND the entire normalized
    message being one unambiguous bounded ordinal-result reference. With no
    active set, unrelated text, ambiguous text, or an out-of-range ordinal:
    no action and no fabricated state.

    ROUTE_SELECTED requires the same session to bind BOTH an active
    discovery set AND a selected place, and the entire normalized message
    being an unambiguous navigation pronoun referring to that selection.
    With no selected place, no active set, unrelated text, or ambiguous
    text: no action.

    ADD_WAYPOINT requires an accepted destination route (destination plus
    the active candidate set AND its presented selected candidate -- the
    server-owned acceptance evidence bound by ``present_route`` or
    ``commit_scenario``), a live active discovery set, room for another
    waypoint, and the entire normalized message being the bounded
    waypoint-first handoff. REMOVE_ONLY_WAYPOINT requires an accepted
    destination route, exactly one current waypoint, and the entire
    normalized message being a bounded removal whose descriptor is generic
    or matches the sole server-owned waypoint label. Any missing gate,
    mismatch, extra clause, or multi-intent text: no action.

    SEARCH_AGAIN requires a live active discovery-set reference in the same
    session AND the entire normalized message being the exact recovery
    phrase "Okay, search again." (punctuation/case variants only). With no
    active set, extra text, a destination clause, multi-intent text, or an
    injection tail: no action and no fabricated state.
    """

    if not isinstance(session, dict):
        return None
    state = trip_state_module.get_trip_state(session)
    has_active_set = bool(state.get("active_discovery_set_id"))
    normalized = _normalized(message)
    if _SEARCH_AGAIN.fullmatch(normalized):
        return DiscoveryFollowupAction.SEARCH_AGAIN if has_active_set else None
    if _SELECTION.fullmatch(normalized):
        return DiscoveryFollowupAction.SELECT if has_active_set else None
    if _NAVIGATION_PRONOUN.fullmatch(normalized):
        if has_active_set and bool(state.get("selected_place_id")):
            return DiscoveryFollowupAction.ROUTE_SELECTED
        return None
    if _WAYPOINT_FIRST.fullmatch(normalized):
        if (
            _has_accepted_trip(state)
            and has_active_set
            and len(state.get("waypoints") or []) < trip_state_module.MAX_WAYPOINTS
        ):
            return DiscoveryFollowupAction.ADD_WAYPOINT
        return None
    if _remove_descriptor(normalized) is not None:
        if _has_accepted_trip(state) and _sole_waypoint_matches(state, normalized):
            return DiscoveryFollowupAction.REMOVE_ONLY_WAYPOINT
        return None
    return None


def should_preserve_discovery_set(session: dict, message: object) -> bool:
    """True only for an unambiguous discovery handoff against a live set.

    The message must match the full bounded grammar AND the session must
    currently bind a server-owned active discovery set. With no active set
    nothing is preserved and no state is fabricated; ordinary new-trip
    requests and unrelated text never preserve.
    """

    if not _HANDOFF.fullmatch(_normalized(message)):
        return False
    state = trip_state_module.get_trip_state(session)
    return bool(state.get("active_discovery_set_id"))


def reset_preserving_discovery(session: dict, message: object) -> None:
    """Reset for the new trip, keeping only the discovery set on a handoff.

    Delegates to the session-level new-trip reset with the policy-selected
    ``preserve_active_discovery_set`` option; every other route, candidate,
    scenario, and place field is cleared exactly as the ordinary reset does.

    The strict accepted-trip waypoint mutation ("Take me to the second one
    first.") is deliberately NOT a new trip: the accepted destination,
    discovery set, candidate state, active trip/card, and existing waypoints
    must all survive until canonical preparation succeeds, so this reset is a
    no-op for that exact state-aware action. Direct destination handoffs and
    every ordinary new-trip request keep their existing reset behavior.
    """

    if (
        detect_followup_action(session, message)
        is DiscoveryFollowupAction.ADD_WAYPOINT
    ):
        return
    session_module.reset_for_new_trip(
        session,
        preserve_active_discovery_set=should_preserve_discovery_set(
            session, message
        ),
    )


def _has_accepted_trip(state: dict) -> bool:
    """True only when the session owns an accepted destination route.

    Acceptance is the server-owned selection bound by ``present_route``
    (active path) or ``commit_scenario`` (committed what-if): a non-empty
    ``selected_candidate_id`` in the ACTIVE candidate slot, alongside the
    active candidate set it belongs to and the destination it serves.
    ``prepare_route_options`` binds the active candidate set and explicitly
    clears ``selected_candidate_id`` before any candidate is presented, so a
    merely prepared, unpresented route never counts as accepted here --
    waypoint mutations require presentation/acceptance evidence. The
    temporary what-if preview slot is deliberately never consulted.
    """

    return (
        bool(state.get("destination"))
        and bool(state.get("active_candidate_set_id"))
        and bool(state.get("selected_candidate_id"))
    )


def _remove_descriptor(normalized: str) -> str | None:
    """The bounded removal descriptor, or None when the text is not removal.

    A generic phrase returns ""; a described phrase returns the normalized
    word descriptor; anything else (extra clauses, punctuation, multi-intent
    text) returns None so removal never activates.
    """

    if _REMOVE_GENERIC.fullmatch(normalized):
        return ""
    match = _REMOVE_DESCRIBED.fullmatch(normalized)
    if match is not None:
        return " ".join(match.group("descriptor").split())
    return None


def _sole_waypoint_matches(state: dict, normalized: str) -> bool:
    """True only when exactly one waypoint matches the removal descriptor.

    Zero or multiple waypoints never match (removal would be ambiguous). A
    generic descriptor matches the sole waypoint; a described descriptor
    must appear as a whole normalized word inside the sole server-owned
    waypoint label (so "pizza" matches "B Pizza" but a mismatched descriptor
    never does).
    """

    waypoints = list(state.get("waypoints") or [])
    if len(waypoints) != 1:
        return False
    descriptor = _remove_descriptor(normalized)
    if descriptor is None:
        return False
    if not descriptor:
        return True
    label = " ".join(str(waypoints[0] or "").casefold().split())
    return re.search(rf"\b{re.escape(descriptor)}\b", label) is not None


def _normalized(message: object) -> str:
    return " ".join(str(message or "").split()).casefold()


__all__ = (
    "DiscoveryFollowupAction",
    "detect_followup_action",
    "reset_preserving_discovery",
    "should_preserve_discovery_set",
)
