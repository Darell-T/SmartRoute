"""Conversational-agent session state, persisted through utils/cache.py.

The bounded model/session state and the complete rider-visible transcript are
stored under separate keys with the same sliding TTL. This prevents the model
context limits below from erasing the transcript restored after a page refresh.

History deliberately never stores tool_use/tool_result blocks -- only plain
text turns and one-line tool summaries -- so it stays cheap to replay into a
fresh model context each turn (see agent/loop.py).
"""

from __future__ import annotations

import dataclasses
import json
import logging
import math
import os
import secrets
import time
from collections.abc import Iterable, Mapping, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

from app.services import cache
from app.services.agent import profile as profile_module
from app.services.agent import transcript_store
from app.services.agent import trip_state as trip_state_module
from app.services.geography import NYC_BOUNDS
from app.services.trips.preparation.input import normalize_route_ids

_LOGGER = logging.getLogger(__name__)

DEFAULT_TTL = timedelta(minutes=30)
MAX_PERSISTED_CONTINUATIONS = 3
MAX_CONTINUATION_ATTEMPTS = 3


def _now() -> datetime:
    return datetime.now(UTC)


def _items(value: object, field: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, (str, bytes, Mapping)):
        raise ValueError(f"{field} must be a sequence of strings")
    if not isinstance(value, Sequence):
        raise ValueError(f"{field} must be a sequence of strings")
    result: list[str] = []
    seen: set[str] = set()
    for item in value:
        if not isinstance(item, str) or not item.strip():
            raise ValueError(f"{field} must contain non-empty strings")
        item = item.strip()
        if item.casefold() not in seen:
            result.append(item)
            seen.add(item.casefold())
    return tuple(result)


def _utc(value: datetime) -> datetime:
    if not isinstance(value, datetime):
        raise ValueError("expiry metadata must be datetime values")
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


@dataclasses.dataclass(frozen=True, slots=True)
class PendingContinuation:
    """Only unresolved outcome metadata, never provider response data."""

    unresolved_outcomes: tuple[str, ...]
    missing_fields: tuple[str, ...] = ()
    constraints: tuple[str, ...] = ()
    references: tuple[str, ...] = ()
    approved_recovery_options: tuple[str, ...] = ()
    attempt_count: int = 1
    created_at: datetime = dataclasses.field(default_factory=_now)
    expires_at: datetime | None = None

    def __post_init__(self) -> None:
        for name in (
            "unresolved_outcomes",
            "missing_fields",
            "constraints",
            "references",
            "approved_recovery_options",
        ):
            object.__setattr__(self, name, _items(getattr(self, name), name))
        if isinstance(self.attempt_count, bool) or not isinstance(
            self.attempt_count, int
        ):
            raise ValueError("attempt_count must be an integer")
        if not 1 <= self.attempt_count <= MAX_CONTINUATION_ATTEMPTS:
            raise ValueError(
                f"attempt_count must be between 1 and {MAX_CONTINUATION_ATTEMPTS}"
            )
        created = _utc(self.created_at)
        expires = (
            _utc(self.expires_at)
            if self.expires_at is not None
            else created + DEFAULT_TTL
        )
        if expires <= created:
            raise ValueError("expires_at must be after created_at")
        object.__setattr__(self, "created_at", created)
        object.__setattr__(self, "expires_at", expires)

    @classmethod
    def create(
        cls,
        unresolved_outcomes: Iterable[str],
        *,
        missing_fields: Iterable[str] = (),
        constraints: Iterable[str] = (),
        references: Iterable[str] = (),
        approved_recovery_options: Iterable[str] = (),
        attempt_count: int = 1,
        now: datetime | None = None,
        ttl: timedelta = DEFAULT_TTL,
    ) -> PendingContinuation:
        created = _utc(now or _now())
        if ttl <= timedelta(0):
            raise ValueError("ttl must be positive")
        return cls(
            unresolved_outcomes=tuple(unresolved_outcomes),
            missing_fields=tuple(missing_fields),
            constraints=tuple(constraints),
            references=tuple(references),
            approved_recovery_options=tuple(approved_recovery_options),
            attempt_count=attempt_count,
            created_at=created,
            expires_at=created + ttl,
        )

    @property
    def recovery_options(self) -> tuple[str, ...]:
        return self.approved_recovery_options

    def is_expired(self, now: datetime | None = None) -> bool:
        expires = self.expires_at
        if expires is None:
            return False
        return _utc(now or _now()) >= expires

    def to_dict(self) -> dict[str, Any]:
        expires = self.expires_at
        if expires is None:
            raise ValueError("continuation expiry metadata is missing")
        return {
            "unresolved_outcomes": list(self.unresolved_outcomes),
            "missing_fields": list(self.missing_fields),
            "constraints": list(self.constraints),
            "references": list(self.references),
            "approved_recovery_options": list(self.approved_recovery_options),
            "attempt_count": self.attempt_count,
            "created_at": self.created_at.isoformat(),
            "expires_at": expires.isoformat(),
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> PendingContinuation:
        allowed = {
            "unresolved_outcomes",
            "missing_fields",
            "constraints",
            "references",
            "approved_recovery_options",
            "attempt_count",
            "created_at",
            "expires_at",
        }
        if not isinstance(payload, Mapping) or set(payload) - allowed:
            raise ValueError("continuation contains unsupported fields")
        try:
            created = datetime.fromisoformat(str(payload["created_at"]))
            expires = datetime.fromisoformat(str(payload["expires_at"]))
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("continuation expiry metadata is invalid") from exc
        return cls(
            unresolved_outcomes=payload.get("unresolved_outcomes", ()),
            missing_fields=payload.get("missing_fields", ()),
            constraints=payload.get("constraints", ()),
            references=payload.get("references", ()),
            approved_recovery_options=payload.get("approved_recovery_options", ()),
            attempt_count=payload.get("attempt_count", 1),
            created_at=created,
            expires_at=expires,
        )


def retain_continuations(
    continuations: Iterable[PendingContinuation],
    *,
    now: datetime | None = None,
    max_count: int = MAX_PERSISTED_CONTINUATIONS,
) -> tuple[PendingContinuation, ...]:
    """Drop expired entries and keep the newest bounded set."""

    if max_count < 1:
        raise ValueError("max_count must be positive")
    active = [
        item
        for item in continuations
        if isinstance(item, PendingContinuation) and not item.is_expired(now)
    ]
    active.sort(key=lambda item: item.created_at, reverse=True)
    return tuple(active[:max_count])


def _normalise_pending_continuations(
    session: dict,
    *,
    now: datetime | None = None,
) -> tuple[PendingContinuation, ...]:
    raw = session.get("pending_continuations")
    if not isinstance(raw, list):
        session["pending_continuations"] = []
        return ()
    parsed: list[PendingContinuation] = []
    for item in raw:
        if isinstance(item, PendingContinuation):
            parsed.append(item)
            continue
        if not isinstance(item, dict):
            continue
        try:
            parsed.append(PendingContinuation.from_dict(item))
        except (TypeError, ValueError):
            continue
    active = retain_continuations(parsed, now=now)
    session["pending_continuations"] = [item.to_dict() for item in active]
    return active


def get_pending_continuations(
    session: dict,
    *,
    now: datetime | None = None,
) -> tuple[PendingContinuation, ...]:
    """Return active metadata-only continuations and prune stale entries."""

    return _normalise_pending_continuations(session, now=now)


def add_pending_continuation(
    session: dict,
    continuation: PendingContinuation,
    *,
    now: datetime | None = None,
) -> tuple[PendingContinuation, ...]:
    """Record one bounded attempt without extending its original lifetime."""

    if not isinstance(continuation, PendingContinuation):
        raise TypeError("continuation must be a PendingContinuation")
    active = list(_normalise_pending_continuations(session, now=now))
    identity = _continuation_identity(continuation)
    previous = next(
        (item for item in active if _continuation_identity(item) == identity),
        None,
    )
    if previous is not None:
        active.remove(previous)
        next_attempt = previous.attempt_count + 1
        if next_attempt <= MAX_CONTINUATION_ATTEMPTS:
            continuation = PendingContinuation(
                unresolved_outcomes=continuation.unresolved_outcomes,
                missing_fields=continuation.missing_fields or previous.missing_fields,
                constraints=continuation.constraints or previous.constraints,
                references=continuation.references or previous.references,
                approved_recovery_options=(
                    continuation.approved_recovery_options
                    or previous.approved_recovery_options
                ),
                attempt_count=next_attempt,
                created_at=previous.created_at,
                expires_at=previous.expires_at,
            )
            active.append(continuation)
    else:
        active.append(continuation)
    kept = retain_continuations(active, now=now)
    session["pending_continuations"] = [item.to_dict() for item in kept]
    return kept


def _continuation_identity(continuation: PendingContinuation) -> tuple[str, ...]:
    return tuple(sorted(value.casefold() for value in continuation.unresolved_outcomes))


def clear_pending_continuations(session: dict) -> None:
    session["pending_continuations"] = []


def resolve_pending_continuations(
    session: dict,
    outcome_kinds: set[str] | tuple[str, ...],
) -> None:
    """Remove only continuation outcomes satisfied by a later turn."""

    resolved = {str(value).strip() for value in outcome_kinds if str(value).strip()}
    if not resolved:
        return
    retained: list[PendingContinuation] = []
    for item in get_pending_continuations(session):
        remaining = tuple(
            outcome for outcome in item.unresolved_outcomes if outcome not in resolved
        )
        if not remaining:
            continue
        retained.append(
            PendingContinuation(
                unresolved_outcomes=remaining,
                missing_fields=item.missing_fields,
                constraints=item.constraints,
                references=item.references,
                approved_recovery_options=item.approved_recovery_options,
                attempt_count=item.attempt_count,
                created_at=item.created_at,
                expires_at=item.expires_at,
            )
        )
    session["pending_continuations"] = [item.to_dict() for item in retained]

SESSION_KEY_PREFIX = "agent:sess:"
SCHEMA_VERSION = 2
_TRANSCRIPT_FIELD = transcript_store.SESSION_FIELD

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


def _transcript_key(session_id: str) -> str:
    return transcript_store.transcript_key(session_id)


def _revoked_key(session_id: str) -> str:
    return transcript_store.revoked_key(session_id)


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
        "current_location": None,
        "slots": {},
        "route_cards": [],
        "active_trip": None,
        "pending_trip": {"status": "none", "resume_offered": False},
        "pending_continuations": [],
        # Grounded places are added here only after the canonical presenter
        # emits them. Discovery searches never overwrite this registry.
        "presented_entity_registry": [],
        "profile": profile,
        "trip_state": trip_state_module.empty_trip_state(profile["preferences"]),
        "history": [],
    }
    transcript_store.attach_empty(session)
    return session_id, session


def _validated_current_location(value: object) -> dict[str, float] | None:
    if not isinstance(value, dict):
        return None
    try:
        lat = float(value["lat"])
        lng = float(value["lng"])
    except (KeyError, TypeError, ValueError):
        return None
    if not math.isfinite(lat) or not math.isfinite(lng):
        return None
    if not (
        NYC_BOUNDS["min_lat"] <= lat <= NYC_BOUNDS["max_lat"]
        and NYC_BOUNDS["min_lon"] <= lng <= NYC_BOUNDS["max_lon"]
    ):
        return None
    return {"lat": lat, "lng": lng}


def current_location(session: dict) -> dict[str, float] | None:
    """Return the session-owned rider location when it is still valid."""

    location = _validated_current_location(session.get("current_location"))
    if location is None:
        session["current_location"] = None
        return None
    return location


def update_current_location(
    session: dict,
    incoming_origin: object,
) -> dict[str, float] | None:
    """Store a valid device fix, or reuse the last valid session fix.

    Missing or invalid request data never erases a location that was already
    accepted at the API boundary. A new valid fix becomes authoritative for
    discovery and routing on this turn and later turns in the same session.
    """

    location = _validated_current_location(incoming_origin)
    if location is not None:
        session["current_location"] = location
        return dict(location)
    return current_location(session)


def _decode_json_object(raw: object) -> dict | None:
    if raw is None:
        return None
    try:
        blob = raw.decode("utf-8") if isinstance(raw, (bytes, bytearray)) else raw
        value = json.loads(blob)
    except (ValueError, TypeError, UnicodeDecodeError):
        return None
    return value if isinstance(value, dict) else None


def load_session(session_id: str) -> dict | None:
    """Returns None for a missing/expired/corrupt session id -- callers
    surface that as the `session_expired` SSE error, never a 500."""
    if not session_id:
        return None
    if transcript_store.is_revoked(session_id):
        return None
    raw = cache.cache_get(_session_key(session_id))
    if raw is None:
        return None
    session = _decode_json_object(raw)
    if session is None:
        _LOGGER.warning("corrupt agent session blob")
        return None
    if not isinstance(session, dict) or session.get("v") not in {1, SCHEMA_VERSION}:
        return None
    if session.get("v") == 1:
        session["v"] = SCHEMA_VERSION
        session.setdefault("active_trip", None)
        session.setdefault("pending_trip", {"status": "none", "resume_offered": False})
    session.setdefault("current_location", None)
    session.setdefault("pending_continuations", [])
    session.setdefault("presented_entity_registry", [])
    _normalise_pending_continuations(session)
    profile_module.get_profile(session)
    trip_state_module.get_trip_state(session)
    transcript_store.attach_loaded(session_id, session)
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


def save_session(
    session_id: str,
    session: dict,
    *,
    refresh_ttl: bool = True,
) -> None:
    """Persist session state without changing its retention policy.

    Accepted prompts and successful rider-facing responses use the default
    full inactivity-window refresh. Failed, aborted, or disconnected turns
    pass ``refresh_ttl=False`` so their diagnostic mutations can be retained
    without extending the conversation beyond the expiry already earned by
    the accepted prompt.
    """
    if transcript_store.is_revoked(session_id):
        cache.cache_delete(_session_key(session_id))
        transcript_store.delete(session_id)
        return

    session["updated_at"] = time.time()
    session["history"] = _trim_history(session.get("history") or [])
    session["route_cards"] = _trim_route_cards(session.get("route_cards") or [])
    # Discovery storage owns freshness and shape normalization for the
    # presented-entity registry. Keep it bounded before serializing the
    # session so an old search can never crowd out current trip state.
    from app.services.agent import discovery_store

    discovery_store.presented_entity_registry(session)
    _normalise_pending_continuations(session)

    core_session = {
        key: value for key, value in session.items() if key != _TRANSCRIPT_FIELD
    }
    blob = json.dumps(core_session, separators=(",", ":"), default=str)
    while len(blob.encode("utf-8")) > MAX_SESSION_BYTES:
        if session["route_cards"]:
            session["route_cards"].pop(0)
        elif session["history"]:
            session["history"].pop(0)
        elif session["pending_continuations"]:
            session["pending_continuations"].pop(0)
        else:
            break
        core_session["route_cards"] = session["route_cards"]
        core_session["history"] = session["history"]
        core_session["pending_continuations"] = session["pending_continuations"]
        blob = json.dumps(core_session, separators=(",", ":"), default=str)

    ttl = int(AGENT_SESSION_TTL_S)
    transcript_store.save(session_id, session, ttl, refresh_ttl=refresh_ttl)
    if transcript_store.is_revoked(session_id):
        transcript_store.delete(session_id)
        cache.cache_delete(_session_key(session_id))
        return
    if refresh_ttl:
        cache.cache_set(_session_key(session_id), blob, ttl)
    else:
        cache.cache_set_preserve_ttl(_session_key(session_id), blob)
    if transcript_store.is_revoked(session_id):
        cache.cache_delete(_session_key(session_id))
        transcript_store.delete(session_id)


def append_history(
    session: dict,
    role: str,
    text: str,
    *,
    turn_id: str | None = None,
) -> None:
    visible = role in {"user", "assistant"}
    if visible:
        transcript_store.ensure(session)
    entry = {"role": role, "text": text}
    if turn_id:
        entry["turn_id"] = turn_id
    session.setdefault("history", []).append(entry)
    if visible:
        transcript_store.append_entry(session, entry)


def append_tool_summary(session: dict, tool: str, summary: str) -> None:
    session.setdefault("history", []).append(
        {"role": "tool", "tool": tool, "text": summary}
    )


def add_route_cards(session: dict, cards: list[dict]) -> None:
    session["route_cards"] = _trim_route_cards(
        list(session.get("route_cards") or []) + list(cards)
    )
    recommended = next(
        (card for card in reversed(cards) if card.get("role") == "recommended"),
        None,
    )
    if recommended:
        session["active_trip"] = recommended


def add_visible_events(session: dict, events: list) -> None:
    """Retain only canonical rider-visible card payloads for restoration."""
    transcript_store.add_visible_events(session, events)


def transcript_snapshot(session: dict) -> dict:
    return transcript_store.snapshot(session)


def model_context_history(session: dict, current_message: str) -> list[dict]:
    """Project complete session history into the bounded model replay."""
    recent_history = _trim_history(list(session.get("history") or []))
    return transcript_store.model_history(session, recent_history, current_message)


def delete_session(session_id: str) -> None:
    """Irreversibly end a conversation and prevent a late stream save."""
    if not session_id:
        return
    ttl = max(1, int(AGENT_SESSION_TTL_S))
    transcript_store.revoke(session_id, ttl)
    cache.cache_delete(_session_key(session_id))


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
    clear_pending_continuations(session)
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
        if name != "prepare_route_options" or not isinstance(tool_input, dict):
            continue
        if tool_input.get("what_if") is True or tool_input.get("scenario") == "what_if":
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
                normalize_route_ids(excluded_route_ids)
            )
        routing_preference = tool_input.get("routing_preference")
        if routing_preference:
            slots.setdefault("constraints", {})["routing_preference"] = (
                routing_preference
            )
        departure_time = tool_input.get("departure_time")
        if departure_time:
            slots["time_anchor"] = departure_time
        # The canonical prepare_route_options executor owns trip-state
        # mutation (route fields, active candidate set, selected candidate).
        # Finalization mirrors only conversational slots, so a non-presentable
        # active replan can never overwrite the preserved accepted selection.
