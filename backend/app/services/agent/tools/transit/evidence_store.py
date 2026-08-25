"""Bounded persistence for server-owned transit evidence sets."""

from __future__ import annotations

import dataclasses
import json
import secrets
import time
from collections.abc import Mapping
from typing import Any

from app.services import cache

EVIDENCE_SET_PREFIX = "agent:transit-evidence:"
DEFAULT_TTL_S = 300


@dataclasses.dataclass(frozen=True, slots=True)
class TransitEvidenceSet:
    evidence_set_id: str
    session_id: str
    requested_operation: str
    concerns: tuple[str, ...]
    checked_routes: tuple[str, ...]
    direction_scope: dict[str, Any]
    service_status_by_direction: dict[str, Any]
    arrivals_by_direction: dict[str, list[dict[str, Any]]]
    accessibility: dict[str, Any] | None
    confirmed_matching_alerts: tuple[dict[str, Any], ...]
    unconfirmed_signals: tuple[dict[str, Any], ...]
    source_coverage: dict[str, str]
    freshness: dict[str, Any]
    unknowns: tuple[str, ...]
    operation_facts: dict[str, Any]
    results: tuple[dict[str, Any], ...]
    # These fields make the evidence contract explicit for consumers that
    # need to distinguish a confirmed incident from a possible vehicle
    # signal.  They intentionally live alongside the older, compatible
    # aggregate fields above.
    incidents: tuple[dict[str, Any], ...] = ()
    stalled_vehicles: tuple[dict[str, Any], ...] = ()
    observed_at: dict[str, str] = dataclasses.field(default_factory=dict)
    freshness_by_source: dict[str, Any] = dataclasses.field(default_factory=dict)
    turn_id: str = ""

    def to_dict(self) -> dict[str, Any]:
        payload = dataclasses.asdict(self)
        for key in (
            "concerns",
            "checked_routes",
            "confirmed_matching_alerts",
            "unconfirmed_signals",
            "unknowns",
            "results",
            "incidents",
            "stalled_vehicles",
        ):
            payload[key] = list(payload[key])

        # Keep the original names for existing callers while exposing a
        # compact typed vocabulary to new consumers.  These are copies, so a
        # caller cannot mutate one view and accidentally alter another.
        payload["service_conditions"] = dict(payload["service_status_by_direction"])
        payload["arrivals"] = {
            key: list(value) for key, value in payload["arrivals_by_direction"].items()
        }
        payload["direction"] = dict(payload["direction_scope"])
        payload["coverage"] = dict(payload["source_coverage"])
        return payload


def new_evidence_set_id() -> str:
    return f"te_{secrets.token_urlsafe(12)}"


def store_evidence_set(
    *,
    session_id: str,
    evidence: TransitEvidenceSet | Mapping[str, Any],
    ttl_seconds: int = DEFAULT_TTL_S,
) -> str:
    payload = evidence.to_dict() if isinstance(evidence, TransitEvidenceSet) else dict(evidence)
    set_id = str(payload.get("evidence_set_id") or new_evidence_set_id()).strip()
    # Evidence handles are immutable within their session.  A retry or a
    # duplicated model call with the same handle must observe the first
    # snapshot, never silently replace the facts that were already presented.
    existing = _load_record(set_id)
    if existing is not None:
        if str(existing.get("session_id") or "") == str(session_id or ""):
            return set_id
        # A caller-supplied handle may collide across sessions.  Never let
        # that caller replace another session's immutable snapshot.
        set_id = new_evidence_set_id()
    now = time.time()
    ttl = max(30, int(ttl_seconds))
    payload.update(
        {
            "evidence_set_id": set_id,
            "session_id": str(session_id or ""),
            "created_at": now,
            "expires_at": now + ttl,
        }
    )
    cache.cache_set(
        f"{EVIDENCE_SET_PREFIX}{set_id}",
        json.dumps(payload, separators=(",", ":"), default=str),
        ttl,
        fail_open=True,
    )
    return set_id


def load_evidence_set(
    evidence_set_id: str, *, session_id: str
) -> dict[str, Any] | None:
    if not str(evidence_set_id or "").strip() or not str(session_id or "").strip():
        return None
    record = _load_record(evidence_set_id)
    if record is None or str(record.get("session_id") or "") != str(session_id):
        return None
    return record


def _load_record(evidence_set_id: str) -> dict[str, Any] | None:
    raw = cache.cache_get(
        f"{EVIDENCE_SET_PREFIX}{str(evidence_set_id).strip()}",
        fail_open=True,
    )
    if raw is None:
        return None
    try:
        value = raw.decode("utf-8") if isinstance(raw, (bytes, bytearray)) else raw
        record = json.loads(value)
    except (TypeError, ValueError, UnicodeDecodeError):
        return None
    if not isinstance(record, dict):
        return None
    try:
        if float(record.get("expires_at") or 0) < time.time():
            return None
    except (TypeError, ValueError):
        return None
    return record


__all__ = [
    "DEFAULT_TTL_S",
    "EVIDENCE_SET_PREFIX",
    "TransitEvidenceSet",
    "load_evidence_set",
    "new_evidence_set_id",
    "store_evidence_set",
]
