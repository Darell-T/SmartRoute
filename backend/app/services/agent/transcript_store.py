"""Complete rider-visible conversation storage for one agent session."""

from __future__ import annotations

import json
import re
from copy import deepcopy

from app.services import cache
from app.services.agent import events as agent_events

TRANSCRIPT_KEY_PREFIX = "agent:transcript:"
REVOKED_KEY_PREFIX = "agent:sess-revoked:"
TRANSCRIPT_SCHEMA_VERSION = 1
SESSION_FIELD = "_transcript"
MAX_EARLIER_CONTEXT_BYTES = 4 * 1024
MAX_EARLIER_TURNS = 6

_WORD_RE = re.compile(r"[a-z0-9][a-z0-9'-]+", re.IGNORECASE)
_STOP_WORDS = {
    "about", "after", "again", "also", "been", "could", "from", "have",
    "just", "like", "more", "that", "their", "then", "there", "these",
    "they", "this", "what", "when", "where", "which", "with", "would",
    "your", "youre",
}


def transcript_key(session_id: str) -> str:
    return f"{TRANSCRIPT_KEY_PREFIX}{session_id}"


def revoked_key(session_id: str) -> str:
    return f"{REVOKED_KEY_PREFIX}{session_id}"


def empty() -> dict:
    return {
        "v": TRANSCRIPT_SCHEMA_VERSION,
        "history": [],
        "route_cards": [],
        "arrival_cards": [],
        "sources": [],
    }


def ensure(session: dict) -> dict:
    transcript = session.get(SESSION_FIELD)
    if isinstance(transcript, dict):
        return transcript
    transcript = empty()
    transcript["history"] = [
        dict(entry)
        for entry in session.get("history") or []
        if entry.get("role") in {"user", "assistant"}
    ]
    session[SESSION_FIELD] = transcript
    return transcript


def attach_empty(session: dict) -> None:
    session[SESSION_FIELD] = empty()


def attach_loaded(session_id: str, session: dict) -> None:
    raw = cache.cache_get(transcript_key(session_id))
    transcript = _decode(raw)
    if not transcript or transcript.get("v") != TRANSCRIPT_SCHEMA_VERSION:
        ensure(session)
        return
    transcript.setdefault("history", [])
    transcript.setdefault("route_cards", [])
    transcript.setdefault("arrival_cards", [])
    transcript["sources"] = _valid_source_entries(transcript.get("sources"))
    session[SESSION_FIELD] = transcript


def save(
    session_id: str,
    session: dict,
    ttl: int,
    *,
    refresh_ttl: bool = True,
) -> None:
    payload = json.dumps(ensure(session), separators=(",", ":"), default=str)
    key = transcript_key(session_id)
    if refresh_ttl:
        cache.cache_set(key, payload, ttl)
    else:
        cache.cache_set_preserve_ttl(key, payload)


def append_entry(session: dict, entry: dict) -> None:
    history = ensure(session).setdefault("history", [])
    repeated_failed_request = bool(
        entry.get("role") == "user"
        and history
        and history[-1].get("role") == "user"
        and history[-1].get("text") == entry.get("text")
    )
    if not repeated_failed_request:
        history.append(dict(entry))


def add_visible_events(session: dict, events: list) -> None:
    transcript = ensure(session)
    for event in events:
        event_type = getattr(event, "type", None)
        payload = (
            event.to_data()
            if event_type in {"route_card", "arrival_card", "sources"}
            else None
        )
        if event_type == "route_card" and payload is not None:
            cards = transcript.setdefault("route_cards", [])
            if not any(card.get("card_id") == payload.get("card_id") for card in cards):
                cards.append(payload)
        elif event_type == "arrival_card" and payload is not None:
            cards = transcript.setdefault("arrival_cards", [])
            identity = payload.get("turn_id"), payload.get("route_id")
            if not any(
                (card.get("turn_id"), card.get("route_id")) == identity
                for card in cards
            ):
                cards.append(payload)
        elif event_type == "sources" and payload is not None:
            turn_id = str(getattr(event, "turn_id", "") or "").strip()
            entries = _valid_source_entries(transcript.get("sources"))
            transcript["sources"] = entries
            existing = next(
                (entry for entry in entries if entry.get("turn_id") == turn_id),
                None,
            )
            if existing is None:
                entries.append({"turn_id": turn_id, "sources": payload["sources"]})
            else:
                merged = [*existing.get("sources", []), *payload["sources"]]
                validated = agent_events.SourcesEvent(
                    turn_id=turn_id,
                    sources=tuple(merged),
                )
                existing["sources"] = validated.to_data()["sources"]


def snapshot(session: dict) -> dict:
    transcript = ensure(session)
    return {
        "history": [dict(entry) for entry in transcript.get("history") or []],
        "route_cards": [dict(card) for card in transcript.get("route_cards") or []],
        "arrival_cards": [dict(card) for card in transcript.get("arrival_cards") or []],
        "sources": _valid_source_entries(transcript.get("sources")),
    }


def _valid_source_entries(value: object) -> list[dict]:
    if not isinstance(value, list):
        return []
    entries: list[dict] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        try:
            event = agent_events.SourcesEvent(
                turn_id=str(item.get("turn_id") or ""),
                sources=tuple(item.get("sources") or ()),
            )
        except (AttributeError, TypeError, ValueError):
            continue
        entries.append({"turn_id": event.turn_id, **event.to_data()})
    return entries


def active_accepted_route_card(session: object) -> dict | None:
    """Return the exact accepted route card only when transcript ownership aligns."""

    if not isinstance(session, dict):
        return None
    active = session.get("active_trip")
    if not isinstance(active, dict):
        return None
    card_id = active.get("card_id")
    itinerary = active.get("canonical_itinerary")
    if (
        not isinstance(card_id, str)
        or not card_id.strip()
        or not isinstance(itinerary, dict)
        or not isinstance(itinerary.get("legs"), list)
        or not itinerary.get("legs")
    ):
        return None
    transcript = session.get(SESSION_FIELD)
    if (
        not isinstance(transcript, dict)
        or transcript.get("v") != TRANSCRIPT_SCHEMA_VERSION
    ):
        return None
    cards = transcript.get("route_cards")
    if not isinstance(cards, list):
        return None
    matches = [
        card
        for card in cards
        if isinstance(card, dict) and card.get("card_id") == card_id
    ]
    if len(matches) != 1:
        return None
    card = matches[0]
    if (
        card.get("role") != "recommended"
        or not all(isinstance(card.get(key), dict) for key in ("origin", "destination", "summary"))
        or not isinstance(card.get("route"), list)
        or not isinstance(card.get("alerts"), list)
    ):
        return None
    transcript_itinerary = card.get("itinerary")
    if (
        not isinstance(transcript_itinerary, dict)
        or not isinstance(transcript_itinerary.get("legs"), list)
        or not transcript_itinerary.get("legs")
        or transcript_itinerary != itinerary
    ):
        return None
    return deepcopy(card)


def model_history(session: dict, recent_history: list[dict], message: str) -> list[dict]:
    transcript = ensure(session)
    return project_model_history(
        list(transcript.get("history") or []), recent_history, message
    )


def _visible(history: list[dict]) -> list[dict]:
    return [
        dict(entry)
        for entry in history
        if entry.get("role") in {"user", "assistant"}
        and isinstance(entry.get("text"), str)
    ]


def _entry_identity(entry: dict) -> tuple:
    return entry.get("role"), entry.get("text"), entry.get("turn_id")


def _older_history(full: list[dict], recent: list[dict]) -> list[dict]:
    if not recent:
        return full
    limit = min(len(full), len(recent))
    for size in range(limit, 0, -1):
        if list(map(_entry_identity, full[-size:])) == list(
            map(_entry_identity, recent[-size:])
        ):
            return full[:-size]
    return full


def _turns(history: list[dict]) -> list[list[dict]]:
    turns: list[list[dict]] = []
    for entry in history:
        if entry.get("role") == "user" or not turns:
            turns.append([entry])
        else:
            turns[-1].append(entry)
    return turns


def _terms(value: str) -> set[str]:
    return {
        token.casefold().replace("'", "")
        for token in _WORD_RE.findall(value)
        if len(token) > 2 and token.casefold().replace("'", "") not in _STOP_WORDS
    }


def _turn_bytes(turn: list[dict]) -> int:
    payload = json.dumps(turn, separators=(",", ":"), default=str).encode("utf-8")
    return len(payload)


def project_model_history(
    transcript_history: list[dict],
    recent_history: list[dict],
    current_message: str,
) -> list[dict]:
    """Return bounded relevant earlier turns followed by the exact recent replay."""
    full = _visible(transcript_history)
    recent = _visible(recent_history)
    older_turns = _turns(_older_history(full, recent))
    if not older_turns:
        return recent_history

    query_terms = _terms(current_message)
    scored = []
    for index, turn in enumerate(older_turns):
        content = " ".join(str(entry.get("text") or "") for entry in turn)
        scored.append((len(query_terms & _terms(content)), index))

    priority = [0]
    priority.extend(range(max(0, len(older_turns) - 2), len(older_turns)))
    priority.extend(index for overlap, index in sorted(scored, reverse=True) if overlap)

    selected: set[int] = set()
    used_bytes = 0
    for index in priority:
        if index in selected or len(selected) >= MAX_EARLIER_TURNS:
            continue
        turn_bytes = _turn_bytes(older_turns[index])
        if used_bytes + turn_bytes > MAX_EARLIER_CONTEXT_BYTES:
            continue
        selected.add(index)
        used_bytes += turn_bytes

    earlier = [
        entry
        for index in sorted(selected)
        for entry in older_turns[index]
    ]
    return earlier + list(recent_history)


def is_revoked(session_id: str) -> bool:
    return cache.cache_get(revoked_key(session_id)) is not None


def revoke(session_id: str, ttl: int) -> None:
    cache.cache_set(revoked_key(session_id), "1", ttl)
    cache.cache_delete(transcript_key(session_id))


def delete(session_id: str) -> None:
    cache.cache_delete(transcript_key(session_id))


def _decode(raw: object) -> dict | None:
    if raw is None:
        return None
    try:
        blob = raw.decode("utf-8") if isinstance(raw, (bytes, bytearray)) else raw
        value = json.loads(blob)
    except (ValueError, TypeError, UnicodeDecodeError):
        return None
    return value if isinstance(value, dict) else None
