"""Deterministic cache-backed incident index and coverage metadata.

Background workers write; the rider-request path only reads. Identity is stable
(SHA-256 over normalized source identities or a canonical fallback) so
equivalent records dedupe. Empty lookups never imply all-clear; coverage truth
comes only from explicit coverage records. Scalar strings are single values,
never character sequences.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from datetime import datetime, timezone
from typing import Any, Iterable

from app.services.incidents.normalization import (
    ALLOWED_COVERAGE,
    ALLOWED_STATES,
    DEFAULT_COVERAGE,
    LIST_FIELDS,
    bounded_int,
    bounded_text,
    incident_id_for,
    normalize_enum,
    normalize_incident_record,
    record_is_expired,
)
from app.services import cache

INCIDENT_PREFIX = "incident:idx:"
COVERAGE_PREFIX = "incident:cov:"
STOP_INDEX_PREFIX = "incident:stop:"
ROUTE_INDEX_PREFIX = "incident:route:"
CORRIDOR_INDEX_PREFIX = "incident:corridor:"
BATCH_INDEX_PREFIX = "incident:batch:"
DEFAULT_TTL_S = 3600
INCIDENT_LOOKUP_TIMEOUT_S = float(os.getenv("INCIDENT_LOOKUP_TIMEOUT_S", "1.5"))
_INDEX_LIMIT = 64
_FILTERED_STATES = {"rejected", "resolved"}
_UNUSABLE_COVERAGE = {"missing", "unavailable", "unscanned"}

_INDEX_PREFIXES = {
    "affected_stop_ids": STOP_INDEX_PREFIX,
    "affected_route_ids": ROUTE_INDEX_PREFIX,
    "affected_corridor_ids": CORRIDOR_INDEX_PREFIX,
    "affected_batch_ids": BATCH_INDEX_PREFIX,
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _as_list(raw: Iterable[str] | None) -> list[str]:
    if raw is None:
        return []
    if isinstance(raw, str):
        return [raw]
    return list(raw)


def upsert_incident(incident: dict[str, Any], *, ttl_seconds: int = DEFAULT_TTL_S) -> str:
    """Store one normalized incident and maintain reverse indexes deterministically."""
    incident_id = incident_id_for(incident)
    key = f"{INCIDENT_PREFIX}{incident_id}"
    existing = _load_json(key)
    record = normalize_incident_record(
        incident,
        incident_id=incident_id,
        now=time.time(),
        now_iso=_now_iso(),
        existing=existing if isinstance(existing, dict) else None,
        ttl_seconds=ttl_seconds,
    )
    cache.cache_set(key, json.dumps(record, separators=(",", ":"), default=str), int(ttl_seconds))
    previous_index_keys = (
        set(_index_keys_for(existing)) if isinstance(existing, dict) else set()
    )
    current_index_keys = set(_index_keys_for(record))
    for key in previous_index_keys - current_index_keys:
        _update_index(key, incident_id, ttl_seconds, remove=True)
    for key in current_index_keys - previous_index_keys:
        _update_index(key, incident_id, ttl_seconds, remove=False)
    return incident_id


def set_incident_state(
    incident_id: str, state: str, *, ttl_seconds: int = DEFAULT_TTL_S
) -> bool:
    """Persist a lifecycle state; ValueError on unknown state, False when missing."""
    normalized = normalize_enum(state, ALLOWED_STATES, "")
    if not normalized:
        raise ValueError(f"unknown incident state: {state!r}")
    key = f"{INCIDENT_PREFIX}{incident_id}"
    record = _load_json(key)
    if not isinstance(record, dict):
        return False
    record["state"] = normalized
    record["updated_at"] = _now_iso()
    cache.cache_set(key, json.dumps(record, separators=(",", ":"), default=str), int(ttl_seconds))
    return True


def set_coverage(coverage: dict[str, Any], *, ttl_seconds: int = DEFAULT_TTL_S) -> None:
    """Persist explicit coverage metadata; never infer status from incident count."""
    coverage_id = bounded_text(coverage.get("coverage_id"), 120)
    if not coverage_id:
        return
    record = {
        "coverage_id": coverage_id,
        "last_attempted_at": coverage.get("last_attempted_at"),
        "last_successful_x_scan_at": coverage.get("last_successful_x_scan_at"),
        "last_official_refresh_at": coverage.get("last_official_refresh_at"),
        "x_status": bounded_text(coverage.get("x_status"), 40) or "not_triggered",
        "web_status": bounded_text(coverage.get("web_status"), 40) or "not_triggered",
        "coverage_status": normalize_enum(
            coverage.get("coverage_status"), ALLOWED_COVERAGE, DEFAULT_COVERAGE
        ),
        "expires_at": coverage.get("expires_at") or (time.time() + ttl_seconds),
        "incidents_found": bounded_int(coverage.get("incidents_found")),
    }
    cache.cache_set(
        f"{COVERAGE_PREFIX}{coverage_id}",
        json.dumps(record, separators=(",", ":"), default=str),
        int(ttl_seconds),
    )


def get_coverage(coverage_id: str) -> dict[str, Any] | None:
    """Return stored coverage; an expired record reads as stale, never current."""
    record = _load_json(f"{COVERAGE_PREFIX}{bounded_text(coverage_id, 120)}")
    if not isinstance(record, dict):
        return None
    return _coverage_from_record(record)


def _coverage_from_record(record: dict[str, Any]) -> dict[str, Any]:
    """Expired coverage reads as stale, never current."""
    status = str(record.get("coverage_status") or DEFAULT_COVERAGE)
    if record_is_expired(record) and status not in _UNUSABLE_COVERAGE:
        copy = dict(record)
        copy["coverage_status"] = "stale"
        return copy
    return record


def lookup_incidents(
    *,
    stop_ids: Iterable[str] | None = None,
    route_ids: Iterable[str] | None = None,
    corridor_ids: Iterable[str] | None = None,
    coverage_ids: Iterable[str] | None = None,
) -> dict[str, Any]:
    """Immediate index lookup. Never implies all-clear without coverage.

    Requested coverage IDs also read the affected-batch reverse index, so
    batch-scoped background scout incidents reach a rider in the same lookup
    that returns explicit coverage metadata.
    """
    index_keys = _index_keys_for_lookup(
        stop_ids=stop_ids,
        route_ids=route_ids,
        corridor_ids=corridor_ids,
        coverage_ids=coverage_ids,
    )
    index_blobs = cache.cache_get_many(index_keys)
    incident_ids: list[str] = []
    for key in index_keys:
        parsed = _parse_json(index_blobs.get(key))
        if isinstance(parsed, list):
            incident_ids.extend(str(item) for item in parsed if str(item).strip())

    unique_ids = list(dict.fromkeys(incident_ids))[:32]
    record_keys = [f"{INCIDENT_PREFIX}{incident_id}" for incident_id in unique_ids]
    record_blobs = cache.cache_get_many(record_keys)
    incidents: list[dict[str, Any]] = []
    for incident_id, key in zip(unique_ids, record_keys, strict=True):
        record = _parse_json(record_blobs.get(key))
        if not isinstance(record, dict):
            continue
        if record_is_expired(record) and record.get("state") not in _FILTERED_STATES:
            record["state"] = "stale"
        if record.get("state") in _FILTERED_STATES:
            continue
        incidents.append(record)
    requested = [
        cid
        for cid in dict.fromkeys(bounded_text(cid, 120) for cid in _as_list(coverage_ids))
        if cid
    ]
    coverage_keys = [f"{COVERAGE_PREFIX}{coverage_id}" for coverage_id in requested]
    coverage_blobs = cache.cache_get_many(coverage_keys)
    coverage_by_id: dict[str, dict[str, Any]] = {}
    for coverage_id, key in zip(requested, coverage_keys, strict=True):
        record = _parse_json(coverage_blobs.get(key))
        if isinstance(record, dict):
            coverage_by_id[coverage_id] = _coverage_from_record(record)
    return {
        "incidents": incidents,
        "coverage": list(coverage_by_id.values()),
        "coverage_status": _coverage_status(requested, coverage_by_id),
        "lookup_kind": "index",
    }


async def lookup_incidents_async(
    *,
    stop_ids: Iterable[str] | None = None,
    route_ids: Iterable[str] | None = None,
    corridor_ids: Iterable[str] | None = None,
    coverage_ids: Iterable[str] | None = None,
) -> dict[str, Any]:
    """Run the bounded, batched lookup stages off the request event loop."""
    return await asyncio.wait_for(
        asyncio.to_thread(
            lookup_incidents,
            stop_ids=stop_ids,
            route_ids=route_ids,
            corridor_ids=corridor_ids,
            coverage_ids=coverage_ids,
        ),
        timeout=INCIDENT_LOOKUP_TIMEOUT_S,
    )


def _index_keys_for_lookup(
    *,
    stop_ids: Iterable[str] | None,
    route_ids: Iterable[str] | None,
    corridor_ids: Iterable[str] | None,
    coverage_ids: Iterable[str] | None,
) -> list[str]:
    """All reverse-index keys one lookup reads, in deterministic order."""
    keys: list[str] = []
    for value, prefix, upper in (
        *((item, STOP_INDEX_PREFIX, False) for item in _as_list(stop_ids)),
        *((item, ROUTE_INDEX_PREFIX, True) for item in _as_list(route_ids)),
        *((item, CORRIDOR_INDEX_PREFIX, False) for item in _as_list(corridor_ids)),
        *((item, BATCH_INDEX_PREFIX, False) for item in _as_list(coverage_ids)),
    ):
        token = bounded_text(value, 120)
        keys.append(f"{prefix}{token.upper() if upper else token}")
    return keys


def _coverage_status(
    requested_ids: list[str], records_by_id: dict[str, dict[str, Any]]
) -> str:
    """Aggregate status derived only from the requested coverage records."""
    if not requested_ids:
        return DEFAULT_COVERAGE
    statuses = {
        str(records_by_id[cid].get("coverage_status") or DEFAULT_COVERAGE)
        for cid in requested_ids
        if cid in records_by_id
    }
    statuses.update("missing" for cid in requested_ids if cid not in records_by_id)
    if "partial" in statuses:
        return "partial"
    if statuses & {"current", "partial"} and statuses - {"current", "partial"}:
        # Usable (current/partial) mixed with missing/unavailable/unscanned/stale.
        return "partial"
    if statuses == {"current"}:
        return "current"
    if "stale" in statuses:
        return "stale"
    if "unavailable" in statuses:
        return "unavailable"
    return DEFAULT_COVERAGE


def _index_keys_for(record: dict[str, Any]) -> list[str]:
    keys: list[str] = []
    for canonical, _alias, _bound, _upper in LIST_FIELDS:
        prefix = _INDEX_PREFIXES[canonical]
        for value in record.get(canonical) or []:
            keys.append(f"{prefix}{value}")
    return keys


def _update_index(key: str, incident_id: str, ttl_seconds: int, *, remove: bool) -> None:
    existing = _read_index(key)
    if remove:
        if incident_id not in existing:
            return
        existing = [item for item in existing if item != incident_id]
    elif incident_id in existing:
        return
    else:
        existing.append(incident_id)
    cache.cache_set(
        key, json.dumps(existing[-_INDEX_LIMIT:], separators=(",", ":")), int(ttl_seconds)
    )


def _read_index(key: str) -> list[str]:
    raw = _load_json(key)
    if isinstance(raw, list):
        return [str(item) for item in raw if str(item).strip()]
    return []


def _load_json(key: str) -> Any:
    return _parse_json(cache.cache_get(key))


def _parse_json(raw: Any) -> Any:
    """Parse one cached blob; malformed or missing blobs read as None."""
    if raw is None:
        return None
    try:
        blob = raw.decode("utf-8") if isinstance(raw, (bytes, bytearray)) else raw
        return json.loads(blob)
    except (ValueError, TypeError, UnicodeDecodeError):
        return None
