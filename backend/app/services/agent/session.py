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
import os
import secrets
import time

from app.utils import cache

SESSION_KEY_PREFIX = "agent:sess:"
SCHEMA_VERSION = 1

AGENT_SESSION_TTL_S = float(os.getenv("AGENT_SESSION_TTL_S", "1800"))

MAX_HISTORY_MESSAGES = 12
MAX_HISTORY_BYTES = 6 * 1024
MAX_SESSION_BYTES = 64 * 1024
MAX_ROUTE_CARDS = 8


def _session_key(session_id: str) -> str:
    return f"{SESSION_KEY_PREFIX}{session_id}"


def new_session_id() -> str:
    return secrets.token_urlsafe(24)


def new_session() -> tuple[str, dict]:
    """Mint a fresh session id + the blob shape saved under it."""
    session_id = new_session_id()
    now = time.time()
    session = {
        "v": SCHEMA_VERSION,
        "created_at": now,
        "updated_at": now,
        "turn_seq": 0,
        "slots": {},
        "route_cards": [],
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
    if not isinstance(session, dict) or session.get("v") != SCHEMA_VERSION:
        return None
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


def next_turn_id(session: dict) -> str:
    session["turn_seq"] = int(session.get("turn_seq", 0)) + 1
    return f"t{session['turn_seq']}"


def extract_slots(session: dict, tool_calls: list[tuple[str, dict]]) -> None:
    """Deterministically update session slots from this turn's ACTUAL tool
    calls -- never from model prose, which can drift from what really ran."""
    slots = session.setdefault("slots", {})
    for name, tool_input in tool_calls:
        if name != "plan_trip" or not isinstance(tool_input, dict):
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
        routing_preference = tool_input.get("routing_preference")
        if routing_preference:
            slots.setdefault("constraints", {})["routing_preference"] = routing_preference
        departure_time = tool_input.get("departure_time")
        if departure_time:
            slots["time_anchor"] = departure_time
