"""Server-owned route candidate sets for outer-agent selection.

Candidate IDs are opaque, session-scoped, expiring, and rejected when invented
or reused across sessions. Full canonical route objects stay server-side;
the model only sees compact comparison digests.
"""

from __future__ import annotations

import json
import logging
import math
import secrets
import threading
import time
from typing import Any

from redis.exceptions import RedisError, WatchError

from app.services import cache
from app.services.mta.alerts import is_material_service_alert, project_service_alert

CANDIDATE_SET_PREFIX = "agent:cset:"
DEFAULT_TTL_S = 900
MAX_CANDIDATES = 8
_PRESENTATION_LOCK = threading.Lock()
_LOGGER = logging.getLogger(__name__)


class _RedisKeyMissing:
    __slots__ = ()


_REDIS_KEY_MISSING = _RedisKeyMissing()


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
        "destination_discovery_set_id": (
            str(payload.get("destination_discovery_set_id") or "").strip() or None
        ),
        "waypoint_discovery_set_id": (
            str(payload.get("waypoint_discovery_set_id") or "").strip() or None
        ),
        "destination_place_id": str(payload.get("destination_place_id") or "").strip() or None,
        "destination_place_ids": [
            str(place_id).strip()
            for place_id in payload.get("destination_place_ids") or []
            if str(place_id or "").strip()
        ],
        "destination_selection_mode": str(
            payload.get("destination_selection_mode") or "single"
        ),
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
        "branch_coverage": payload.get("branch_coverage") or [],
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
    cache.cache_set(
        _key(set_id),
        json.dumps(record, separators=(",", ":"), default=str),
        ttl,
        fail_open=True,
    )
    return set_id


def load_candidate_set(candidate_set_id: str, *, session_id: str) -> dict[str, Any] | None:
    """Load a set only when it belongs to this session and has not expired."""

    if not candidate_set_id or not session_id:
        return None
    raw = cache.cache_get(_key(candidate_set_id), fail_open=True)
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


def accepted_route_comparison(
    record: dict[str, Any], selected_candidate_id: str
) -> dict[str, Any] | None:
    """Project the accepted candidate set for a passenger-safe comparison."""

    options: list[dict[str, Any]] = []
    selected_found = False
    for entry in record.get("candidates") or []:
        if not isinstance(entry, dict):
            continue
        digest = entry.get("digest")
        if not isinstance(digest, dict):
            continue
        is_selected = str(entry.get("candidate_id") or "") == selected_candidate_id
        selected_found = selected_found or is_selected
        timing = {
            key: value
            for key in ("duration_minutes", "walking_minutes", "transfers")
            if (value := _finite_metric(digest.get(key))) is not None
        }
        official_alerts = _official_alert_comparison(
            digest.get("official_service_impacts")
        )
        conditions = {
            "official_service_impact": any(
                is_material_service_alert(alert)
                for alert in digest.get("official_service_impacts") or []
            ),
            "official_service_change": any(
                isinstance(alert, dict)
                and alert.get("material_disruption") is False
                for alert in digest.get("official_service_impacts") or []
            ),
            "confirmed_incident": bool(digest.get("confirmed_incident_impacts")),
            "possible_service_signal": bool(digest.get("unconfirmed_material_claims")),
            "potential_event_risk": any(
                isinstance(impact, dict)
                for impact in digest.get("event_or_crowd_impacts") or []
            ),
        }
        options.append(
            {
                "selected": is_selected,
                "destination": str(digest.get("destination_name") or "") or None,
                "lines": [
                    str(line).strip()
                    for line in digest.get("transit_lines") or []
                    if str(line).strip()
                ],
                **timing,
                "service_conditions": conditions,
                "official_alerts": official_alerts,
            }
        )
    if not selected_found:
        return None
    return {"options": options}


def _official_alert_comparison(alerts: object) -> list[dict[str, Any]]:
    if not isinstance(alerts, list):
        return []
    projected: list[dict[str, Any]] = []
    for alert in alerts:
        value = project_service_alert(alert)
        if value is None:
            continue
        projected.append(value)
        if len(projected) >= 3:
            break
    return projected


def load_accepted_route_comparison(
    candidate_set_id: str,
    selected_candidate_id: str,
    *,
    session_id: str,
) -> dict[str, Any] | None:
    record = load_candidate_set(candidate_set_id, session_id=session_id)
    return (
        accepted_route_comparison(record, selected_candidate_id)
        if record is not None
        else None
    )


def _finite_metric(value: object) -> int | float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    return int(number) if number.is_integer() else number


def mark_presented(
    candidate_set_id: str,
    candidate_id: str,
    *,
    session_id: str,
    ttl_seconds: int = DEFAULT_TTL_S,
) -> str | None:
    """Mark a set as presented once. Returns error string on failure."""

    if getattr(cache, "redis_client", None) is not None:
        try:
            result = _mark_presented_redis(
                candidate_set_id,
                candidate_id,
                session_id=session_id,
                ttl_seconds=ttl_seconds,
            )
            if not isinstance(result, _RedisKeyMissing):
                return result
        except RedisError:
            # Candidate sets are ephemeral; the shared cache wrapper mirrors
            # fail-open writes into process memory for this fallback.
            pass
    return _mark_presented_memory(
        candidate_set_id,
        candidate_id,
        session_id=session_id,
        ttl_seconds=ttl_seconds,
    )


def _mark_presented_memory(
    candidate_set_id: str,
    candidate_id: str,
    *,
    session_id: str,
    ttl_seconds: int,
) -> str | None:
    # The local fallback is the test/dev cache and is shared by concurrent
    # asyncio tasks. Holding the lock across load and write makes presentation
    # a one-time reservation instead of a racy read/modify/write pair.
    with _PRESENTATION_LOCK:
        record = load_candidate_set(candidate_set_id, session_id=session_id)
        error = _reserve_record(record, candidate_id)
        if error:
            return error
        _cache_reserved_record(_key(candidate_set_id), record, ttl_seconds)
        return None


def _mark_presented_redis(
    candidate_set_id: str,
    candidate_id: str,
    *,
    session_id: str,
    ttl_seconds: int,
) -> str | None | _RedisKeyMissing:
    client = cache.redis_client
    key = _key(candidate_set_id)
    for _attempt in range(3):
        try:
            with client.pipeline() as pipe:
                pipe.watch(key)
                raw = pipe.get(key)
                if raw is None:
                    return _REDIS_KEY_MISSING
                record = _decode_record(raw)
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
        except RedisError as exc:
            _LOGGER.warning(
                "atomic candidate presentation failed type=%s",
                type(exc).__name__,
            )
            raise
        except (RuntimeError, TypeError, ValueError, KeyError, AttributeError, OSError) as exc:
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


def _queue_reserved_record(pipe: Any, key: str, record: dict[str, Any], ttl_seconds: int) -> None:
    try:
        remaining = max(30, int(float(record.get("expires_at") or time.time()) - time.time()))
    except (TypeError, ValueError) as exc:
        raise ValueError("candidate set expiry is invalid") from exc
    pipe.setex(
        key,
        min(max(30, int(ttl_seconds)), remaining),
        json.dumps(record, separators=(",", ":"), default=str),
    )


def _cache_reserved_record(key: str, record: dict[str, Any], ttl_seconds: int) -> None:
    try:
        remaining = max(30, int(float(record.get("expires_at") or time.time()) - time.time()))
    except (TypeError, ValueError) as exc:
        raise ValueError("candidate set expiry is invalid") from exc
    cache.cache_set(
        key,
        json.dumps(record, separators=(",", ":"), default=str),
        min(max(30, int(ttl_seconds)), remaining),
        fail_open=True,
    )


def _decode_record(raw: object) -> dict[str, Any] | None:
    try:
        blob = raw.decode("utf-8") if isinstance(raw, (bytes, bytearray)) else raw
        record = json.loads(blob)
    except (ValueError, TypeError, UnicodeDecodeError):
        return None
    return record if isinstance(record, dict) else None
