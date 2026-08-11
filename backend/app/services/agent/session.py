"""Conversational-agent session state, persisted through utils/cache.py.

One JSON blob per session, keyed `agent:sess:{token}`, TTL refreshed on every
save. Redis-backed in production (utils/cache.py falls back to an in-memory
dict when REDIS_URL is unset -- fine for local dev/tests, but not durable
across processes, which is why the agent router gates on REDIS_URL by
default; see routers/agent_chat.py).

History deliberately never stores tool_use/tool_result blocks -- only plain
text turns and one-line tool summaries -- so it stays cheap to replay into a
fresh model context each turn (see agent/loop.py).
"""

from __future__ import annotations

import json
import math
import os
import secrets
import time

from app.utils import cache
from app.services.agent import intelligence
from app.services.agent import profile as profile_module
from app.services.agent import trip_state as trip_state_module

SESSION_KEY_PREFIX = "agent:sess:"
SCHEMA_VERSION = 2

AGENT_SESSION_TTL_S = float(os.getenv("AGENT_SESSION_TTL_S", "1800"))

# One active chat turn per session is enforced with a per-session lease keyed
# separately from the session blob. The lease TTL must outlive a bounded live
# turn (the configured run_agent_turn deadline, default 50s in loop.py) plus
# cleanup margin so a live turn can never outlast its own lease; process
# death eventually releases the lease through the TTL. The 120s floor keeps
# it comfortably above the admission lease TTL for short deadlines.
SESSION_LEASE_KEY_PREFIX = "agent:sess-lease:"
AGENT_TURN_DEADLINE_S = float(os.getenv("AGENT_TURN_DEADLINE_S", "50"))


def _session_lease_ttl_s(turn_deadline_s: float) -> int:
    # ceil so a fractional configured deadline is never truncated below
    # deadline + the cleanup margin.
    return max(120, math.ceil(turn_deadline_s + 70))


SESSION_LEASE_TTL_S = _session_lease_ttl_s(AGENT_TURN_DEADLINE_S)

MAX_HISTORY_MESSAGES = 12
MAX_HISTORY_BYTES = 6 * 1024
MAX_SESSION_BYTES = 64 * 1024
MAX_ROUTE_CARDS = 8


def _session_key(session_id: str) -> str:
    return f"{SESSION_KEY_PREFIX}{session_id}"


def _session_lease_key(session_id: str) -> str:
    return f"{SESSION_LEASE_KEY_PREFIX}{session_id}"


def acquire_session_lease(session_id: str) -> str | None:
    """Atomically claim the one-active-turn-per-session lease.

    Returns an opaque ownership token, or None when another turn is already
    running for this session. Built on the shared atomic cache primitive
    (cache_add) so the guarantee holds across processes under Redis and with
    in-memory parity in dev/tests. The token is never exposed to clients.
    """
    token = secrets.token_urlsafe(24)
    if not cache.cache_add(_session_lease_key(session_id), token, SESSION_LEASE_TTL_S):
        return None
    return token


def release_session_lease(session_id: str, token: str | None) -> bool:
    """Ownership-safe release via compare-and-delete: only the current
    owner's token removes the lease, so a stale turn can never delete a
    newer turn's lease. No-op (False) for a missing or None token."""
    if not token:
        return False
    return cache.cache_delete_if_value(_session_lease_key(session_id), token)


def new_session_id() -> str:
    return secrets.token_urlsafe(24)


def new_session() -> tuple[str, dict]:
    """Mint a fresh session id + the blob shape saved under it."""
    session_id = new_session_id()
    now = time.time()
    profile = profile_module.empty_profile()
    session = {
        "v": SCHEMA_VERSION,
        "created_at": now,
        "updated_at": now,
        "turn_seq": 0,
        "slots": {},
        "route_cards": [],
        "active_trip": None,
        "pending_trip": {"status": "none", "resume_offered": False},
        "profile": profile,
        "trip_state": trip_state_module.empty_trip_state(profile["preferences"]),
        "history": [],
    }
    return session_id, session


def load_session(session_id: str) -> dict | None:
    """Returns None for a missing/expired/corrupt session id -- callers
    surface that as the `session_expired` SSE error, never a 500."""
    if not session_id:
        return None
    raw = cache.cache_get(_session_key(session_id))
    if raw is None:
        return None
    try:
        blob = raw.decode("utf-8") if isinstance(raw, (bytes, bytearray)) else raw
        session = json.loads(blob)
    except (ValueError, TypeError, UnicodeDecodeError) as exc:
        print(f"[agent-session] corrupt session blob sess[{session_id[:6]}]: {exc!r}")
        return None
    if not isinstance(session, dict) or session.get("v") not in {1, SCHEMA_VERSION}:
        return None
    if session.get("v") == 1:
        session["v"] = SCHEMA_VERSION
        session.setdefault("active_trip", None)
        session.setdefault("pending_trip", {"status": "none", "resume_offered": False})
    profile_module.get_profile(session)
    trip_state_module.get_trip_state(session)
    return session


def _history_bytes(history: list) -> int:
    return len(json.dumps(history, separators=(",", ":"), default=str).encode("utf-8"))


def _trim_history(history: list) -> list:
    trimmed = list(history)[-MAX_HISTORY_MESSAGES:]
    while len(trimmed) > 1 and _history_bytes(trimmed) > MAX_HISTORY_BYTES:
        trimmed = trimmed[1:]
    return trimmed


def _trim_route_cards(cards: list) -> list:
    # Drop oldest cards first -- the list is append order, so the cap keeps
    # the tail (most recent turns' cards).
    return list(cards)[-MAX_ROUTE_CARDS:]


def save_session(session_id: str, session: dict) -> None:
    """Persist the session, refreshing its TTL. Enforces the 64KB total cap
    by dropping oldest route cards first, then oldest history -- never
    raises on an oversized session."""
    session["updated_at"] = time.time()
    session["history"] = _trim_history(session.get("history") or [])
    session["route_cards"] = _trim_route_cards(session.get("route_cards") or [])

    blob = json.dumps(session, separators=(",", ":"), default=str)
    while len(blob.encode("utf-8")) > MAX_SESSION_BYTES:
        if session["route_cards"]:
            session["route_cards"].pop(0)
        elif session["history"]:
            session["history"].pop(0)
        else:
            break
        blob = json.dumps(session, separators=(",", ":"), default=str)

    cache.cache_set(_session_key(session_id), blob, int(AGENT_SESSION_TTL_S))


def append_history(session: dict, role: str, text: str) -> None:
    session.setdefault("history", []).append({"role": role, "text": text})


def append_tool_summary(session: dict, tool: str, summary: str) -> None:
    session.setdefault("history", []).append({"role": "tool", "tool": tool, "text": summary})


def add_route_cards(session: dict, cards: list[dict]) -> None:
    session["route_cards"] = _trim_route_cards(list(session.get("route_cards") or []) + list(cards))
    recommended = next(
        (card for card in reversed(cards) if card.get("role") == "recommended"),
        None,
    )
    if recommended:
        session["active_trip"] = recommended


def pending_trip(session: dict) -> dict:
    value = session.get("pending_trip")
    if not isinstance(value, dict):
        value = {"status": "none", "resume_offered": False}
        session["pending_trip"] = value
    return value


def mark_pending_trip_failed(session: dict, tool_input: dict, error: str) -> None:
    destination = str(tool_input.get("destination") or "").strip()
    summary = f"the trip to {destination}" if destination else "the unfinished trip"
    session["pending_trip"] = {
        "status": "failed",
        "summary": summary,
        "request": {
            key: tool_input.get(key)
            for key in (
                "origin",
                "destination",
                "exclude_modes",
                "routing_preference",
                "departure_time",
                "arrival_by",
                "waypoints",
                "avoid_crowds",
            )
            if tool_input.get(key) is not None
        },
        "last_error": str(error or "route planning failed")[:160],
        "last_attempt_at": time.time(),
        "resume_offered": False,
    }


def clear_pending_trip(session: dict) -> None:
    session["pending_trip"] = {"status": "none", "resume_offered": False}


def consume_resume_offer(session: dict) -> str | None:
    pending = pending_trip(session)
    if pending.get("status") != "failed" or pending.get("resume_offered"):
        return None
    pending["resume_offered"] = True
    pending["status"] = "awaiting_confirmation"
    summary = str(pending.get("summary") or "the unfinished trip")
    return f"Do you want me to retry {summary}?"


def reset_for_new_trip(
    session: dict,
    *,
    preserve_active_discovery_set: bool = False,
) -> None:
    """Drop stale trip constraints without erasing ordinary conversation.

    ``preserve_active_discovery_set`` keeps only the server-owned active
    discovery set id so an unambiguous discovery-result handoff ("Take me to
    the second one.") can resolve against the real store on the next turn;
    route slots, cards, pending trip, and all other trip-state fields still
    reset.
    """

    slots = session.setdefault("slots", {})
    slots.pop("origin", None)
    slots.pop("destination", None)
    slots.pop("time_anchor", None)
    slots.pop("constraints", None)
    session["active_trip"] = None
    session["route_cards"] = []
    clear_pending_trip(session)
    trip_state_module.reset_for_new_trip(
        session,
        preserve_active_discovery_set=preserve_active_discovery_set,
    )


def next_turn_id(session: dict) -> str:
    session["turn_seq"] = int(session.get("turn_seq", 0)) + 1
    return f"t{session['turn_seq']}"


def extract_slots(session: dict, tool_calls: list[tuple[str, dict]]) -> None:
    """Deterministically update session slots from this turn's ACTUAL tool
    calls -- never from model prose, which can drift from what really ran."""
    slots = session.setdefault("slots", {})
    for name, tool_input in tool_calls:
        if name not in {"plan_trip", "prepare_route_options"} or not isinstance(tool_input, dict):
            continue
        if name == "prepare_route_options" and (
            tool_input.get("what_if") is True
            or tool_input.get("scenario") == "what_if"
        ):
            # A hypothetical preparation is intentionally isolated from the
            # active trip; present_route commits it only on explicit intent.
            continue
        origin = tool_input.get("origin")
        if origin:
            slots["origin"] = origin
        destination = tool_input.get("destination")
        if destination:
            slots["destination"] = destination
        exclude_modes = tool_input.get("exclude_modes")
        if exclude_modes is not None:
            slots.setdefault("constraints", {})["exclude_modes"] = list(exclude_modes)
        excluded_route_ids = tool_input.get("excluded_route_ids")
        if excluded_route_ids is not None:
            slots.setdefault("constraints", {})["excluded_route_ids"] = list(
                intelligence.normalize_route_ids(excluded_route_ids)
            )
        routing_preference = tool_input.get("routing_preference")
        if routing_preference:
            slots.setdefault("constraints", {})["routing_preference"] = routing_preference
        departure_time = tool_input.get("departure_time")
        if departure_time:
            slots["time_anchor"] = departure_time
        # The canonical prepare_route_options executor owns trip-state
        # mutation (route fields, active candidate set, selected candidate).
        # Finalization mirrors only conversational slots, so a non-presentable
        # active replan can never overwrite the preserved accepted selection.
