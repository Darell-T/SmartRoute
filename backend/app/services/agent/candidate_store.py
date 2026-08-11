"""Server-owned route candidate sets for outer-agent selection.

Candidate IDs are opaque, session-scoped, expiring, and rejected when invented
or reused across sessions. Full canonical route objects stay server-side;
the model only sees compact comparison digests.
"""

from __future__ import annotations

import json
import logging
import secrets
import threading
import time
from typing import Any

from redis.exceptions import WatchError

from app.utils import cache

CANDIDATE_SET_PREFIX = "agent:cset:"
DEFAULT_TTL_S = 900
MAX_CANDIDATES = 8
_PRESENTATION_LOCK = threading.Lock()
_LOGGER = logging.getLogger(__name__)


def new_candidate_set_id() -> str:
    return f"cs_{secrets.token_urlsafe(12)}"


def new_candidate_id() -> str:
    return f"cd_{secrets.token_urlsafe(10)}"


def _key(candidate_set_id: str) -> str:
    return f"{CANDIDATE_SET_PREFIX}{candidate_set_id}"


def store_candidate_set(
    *,
    session_id: str,
    payload: dict[str, Any],
    ttl_seconds: int = DEFAULT_TTL_S,
) -> str:
    """Persist a prepared candidate set. Returns the opaque set id."""

    set_id = new_candidate_set_id()
    now = time.time()
    candidates = list(payload.get("candidates") or [])[:MAX_CANDIDATES]
    ttl = max(30, int(ttl_seconds))
    record = {
        "candidate_set_id": set_id,
        "session_id": session_id,
        "created_at": now,
        "expires_at": now + max(30, int(ttl_seconds)),
        "presented": False,
        "selected_candidate_id": None,
        "presentation_reserved_at": None,
        "tool_input": payload.get("tool_input") or {},
        "discovery_set_id": str(payload.get("discovery_set_id") or "").strip() or None,
        "destination_place_id": str(payload.get("destination_place_id") or "").strip() or None,
        "origin_raw": payload.get("origin_raw"),
        "destination_raw": payload.get("destination_raw"),
        "origin_place": payload.get("origin_place"),
        "destination_place": payload.get("destination_place"),
        "departure_time": payload.get("departure_time"),
        "arrival_by": payload.get("arrival_by"),
        "excluded": list(payload.get("excluded") or []),
        "excluded_route_ids": list(payload.get("excluded_route_ids") or []),
        "parsed_routes": payload.get("parsed_routes") or [],
        "scored": payload.get("scored") or [],
        "relevant_alerts": payload.get("relevant_alerts") or [],
        "incidents": payload.get("incidents") or [],
        "event_evidence_status": payload.get("event_evidence_status") or "not_required",
        "event_impacts": payload.get("event_impacts") or [],
        "event_failures": payload.get("event_failures") or [],
        "crowd_search_metadata": payload.get("crowd_search_metadata") or {},
        "incident_scan_metadata": payload.get("incident_scan_metadata") or {},
        "evidence_envelopes": payload.get("evidence_envelopes") or {},
        "candidate_evidence": payload.get("candidate_evidence") or [],
        "collect_crowd_evidence": bool(payload.get("collect_crowd_evidence")),
        "candidates": candidates,
        "evidence_coverage": payload.get("evidence_coverage") or {},
        "first_leg_arrival_context": payload.get("first_leg_arrival_context"),
        "timings": payload.get("timings") or {},
        "route_status": str(payload.get("route_status") or "good"),
        "hard_constraints": payload.get("hard_constraints") or {},
        "candidate_kind": str(payload.get("candidate_kind") or "single_leg"),
        "aggregate_segments": payload.get("aggregate_segments") or [],
        "scenario_mode": str(payload.get("scenario_mode") or "active"),
        "waypoints": list(payload.get("waypoints") or [])[:3],
    }
    cache.cache_set(_key(set_id), json.dumps(record, separators=(",", ":"), default=str), ttl)
    return set_id


def load_candidate_set(candidate_set_id: str, *, session_id: str) -> dict[str, Any] | None:
    """Load a set only when it belongs to this session and has not expired."""

    if not candidate_set_id or not session_id:
        return None
    raw = cache.cache_get(_key(candidate_set_id))
    if raw is None:
        return None
    record = _decode_record(raw)
    if not isinstance(record, dict):
        return None
    if str(record.get("session_id") or "") != session_id:
        return None
    try:
        expired = float(record.get("expires_at") or 0) < time.time()
    except (TypeError, ValueError):
        expired = True
    if expired:
        return None
    return record


def get_candidate(
    candidate_set_id: str,
    candidate_id: str,
    *,
    session_id: str,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None, str | None]:
    """Return (record, candidate_entry, error)."""

    record = load_candidate_set(candidate_set_id, session_id=session_id)
    if record is None:
        return None, None, "candidate set is unknown, expired, or not owned by this session"
    for entry in record.get("candidates") or []:
        if isinstance(entry, dict) and str(entry.get("candidate_id") or "") == candidate_id:
            return record, entry, None
    return record, None, "candidate id is unknown for this set"


def mark_presented(
    candidate_set_id: str,
    candidate_id: str,
    *,
    session_id: str,
    ttl_seconds: int = DEFAULT_TTL_S,
) -> str | None:
    """Mark a set as presented once. Returns error string on failure."""

    if getattr(cache, "redis_client", None) is not None:
        return _mark_presented_redis(
            candidate_set_id,
            candidate_id,
            session_id=session_id,
            ttl_seconds=ttl_seconds,
        )
    # The local fallback is the test/dev cache and is shared by concurrent
    # asyncio tasks. Holding the lock across load and write makes presentation
    # a one-time reservation instead of a racy read/modify/write pair.
    with _PRESENTATION_LOCK:
        record = load_candidate_set(candidate_set_id, session_id=session_id)
        error = _reserve_record(record, candidate_id)
        if error:
            return error
        _save_reserved_record(candidate_set_id, record, ttl_seconds)
        return None


def _mark_presented_redis(
    candidate_set_id: str,
    candidate_id: str,
    *,
    session_id: str,
    ttl_seconds: int,
) -> str | None:
    client = cache.redis_client
    key = _key(candidate_set_id)
    for _attempt in range(3):
        try:
            with client.pipeline() as pipe:
                pipe.watch(key)
                record = _decode_record(pipe.get(key))
                if record is None or str(record.get("session_id") or "") != session_id:
                    return "candidate set is unknown, expired, or not owned by this session"
                try:
                    if float(record.get("expires_at") or 0) < time.time():
                        return "candidate set is unknown, expired, or not owned by this session"
                except (TypeError, ValueError):
                    return "candidate set expiry is invalid"
                error = _reserve_record(record, candidate_id)
                if error:
                    return error
                pipe.multi()
                _queue_reserved_record(pipe, key, record, ttl_seconds)
                pipe.execute()
                return None
        except WatchError:
            continue
        except Exception as exc:
            _LOGGER.warning(
                "atomic candidate presentation failed type=%s",
                type(exc).__name__,
            )
            return "candidate presentation store unavailable"
    return "candidate set changed while presenting; try again"


def _reserve_record(record: dict[str, Any] | None, candidate_id: str) -> str | None:
    if record is None:
        return "candidate set is unknown, expired, or not owned by this session"
    if record.get("presented"):
        return "route already presented for this candidate set"
    found = any(
        isinstance(entry, dict)
        and str(entry.get("candidate_id") or "") == candidate_id
        for entry in (record.get("candidates") or [])
    )
    if not found:
        return "candidate id is unknown for this set"
    record["presented"] = True
    record["presentation_reserved_at"] = time.time()
    record["selected_candidate_id"] = candidate_id
    return None


def _save_reserved_record(candidate_set_id: str, record: dict[str, Any], ttl_seconds: int) -> None:
    _cache_reserved_record(_key(candidate_set_id), record, ttl_seconds)


def _queue_reserved_record(pipe: Any, key: str, record: dict[str, Any], ttl_seconds: int) -> None:
    try:
        remaining = max(30, int(float(record.get("expires_at") or time.time()) - time.time()))
    except (TypeError, ValueError):
        raise ValueError("candidate set expiry is invalid")
    pipe.setex(
        key,
        min(max(30, int(ttl_seconds)), remaining),
        json.dumps(record, separators=(",", ":"), default=str),
    )


def _cache_reserved_record(key: str, record: dict[str, Any], ttl_seconds: int) -> None:
    try:
        remaining = max(30, int(float(record.get("expires_at") or time.time()) - time.time()))
    except (TypeError, ValueError):
        raise ValueError("candidate set expiry is invalid")
    cache.cache_set(
        key,
        json.dumps(record, separators=(",", ":"), default=str),
        min(max(30, int(ttl_seconds)), remaining),
    )


def _decode_record(raw: object) -> dict[str, Any] | None:
    try:
        blob = raw.decode("utf-8") if isinstance(raw, (bytes, bytearray)) else raw
        record = json.loads(blob)
    except (ValueError, TypeError, UnicodeDecodeError):
        return None
    return record if isinstance(record, dict) else None
