"""Normalization and deterministic identity for incident records.

Cohesive helpers behind the incident index: bounded scalar text, bounded/deduped ID
lists (a scalar string is one value, never characters), enum normalization, the
source-record allowlist, and the stable SHA-256 identity. Kept separate so the
cache-backed index stays small and focused.
"""

from __future__ import annotations

import hashlib
import json
import time
from typing import Any

ALLOWED_STATES = frozenset(
    {"unconfirmed", "confirmed", "rejected", "refreshing", "stale", "resolved"}
)
ALLOWED_COVERAGE = frozenset({"current", "partial", "stale", "unavailable", "unscanned"})
DEFAULT_STATE = "unconfirmed"
DEFAULT_COVERAGE = "unscanned"

# (canonical field, alias, bound, uppercase)
LIST_FIELDS = (
    ("affected_stop_ids", "stop_ids", 24, False),
    ("affected_route_ids", "route_ids", 24, True),
    ("affected_corridor_ids", "corridor_ids", 12, False),
    ("affected_batch_ids", "batch_ids", 12, False),
)
SOURCE_COVERAGE_BOUND = 8
SOURCE_RECORDS_BOUND = 8

# (canonical field, aliases, bound) for the shallow source-record allowlist
_SOURCE_RECORD_FIELDS = (
    ("source", ("source", "provider", "source_type"), 80),
    ("source_id", ("source_id", "id", "source_identity"), 120),
    ("source_url", ("source_url", "citation_url"), 240),
    ("observed_at", ("observed_at",), 64),
)
# Container values are never stringified into shallow provenance fields.
_CONTAINER_TYPES = (dict, list, tuple, set, frozenset)


def identity_text(value: object) -> str:
    """Case- and whitespace-insensitive text, used only for identity hashing."""
    return " ".join(str(value or "").split()).strip().casefold()


def bounded_text(value: object, limit: int) -> str:
    text = " ".join(str(value or "").split()).strip()
    return text[:limit]


def bounded_int(value: object) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def bounded_ids(raw: object, limit: int, *, upper: bool = False) -> list[str]:
    """Normalize an ID sequence; a scalar string is one value, never characters."""
    items = [raw] if isinstance(raw, str) else raw or ()
    out: list[str] = []
    for item in items:
        text = " ".join(str(item).split()).strip()
        text = text.upper() if upper else text
        if text and text not in out:
            out.append(text)
    return out[:limit]


def normalize_enum(value: object, allowed: frozenset[str], default: str) -> str:
    text = identity_text(value)
    return text if text in allowed else default


def sanitize_source_records(raw: object) -> list[dict[str, str]]:
    """Shallow provenance records from an allowlist; never keep raw payloads.

    Only list/tuple containers are a valid record sequence; any other top-level
    value (scalar string, mapping, set) is malformed and yields no records.
    Container values in allowlisted fields are treated as absent, so they are
    never stringified or retained.
    """
    if not isinstance(raw, (list, tuple)):
        return []
    records: list[dict[str, str]] = []
    for item in raw:
        if len(records) >= SOURCE_RECORDS_BOUND:
            break
        if not isinstance(item, dict):
            continue
        record: dict[str, str] = {}
        for canonical, aliases, limit in _SOURCE_RECORD_FIELDS:
            value = next(
                (
                    item.get(alias)
                    for alias in aliases
                    if item.get(alias) is not None
                    and not isinstance(item.get(alias), _CONTAINER_TYPES)
                ),
                None,
            )
            if value is None:
                continue
            text = bounded_text(value, limit)
            if text:
                record[canonical] = text
        if record:
            records.append(record)
    return records


def source_identity_pairs(incident: dict[str, Any]) -> list[tuple[str, str]]:
    """Stable (source, source_id) pairs used as deterministic identity.

    A valid top-level source/source_id pair is the primary identity and
    supersedes corroborating source_records; sorted, deduplicated record pairs
    are used only when the top-level pair is absent.
    """
    source = identity_text(incident.get("source"))
    source_id = identity_text(incident.get("source_id"))
    if source and source_id:
        return [(source, source_id)]
    pairs: set[tuple[str, str]] = set()
    for record in sanitize_source_records(incident.get("source_records")):
        rec_source = identity_text(record.get("source"))
        rec_id = identity_text(record.get("source_id"))
        if rec_source and rec_id:
            pairs.add((rec_source, rec_id))
    return sorted(pairs)


def _unique_sorted_ids(raw: object) -> list[str]:
    items = [raw] if isinstance(raw, str) else raw or ()
    return sorted({text for text in (identity_text(item) for item in items) if text})


def fallback_material(incident: dict[str, Any]) -> dict[str, Any]:
    """Canonical fallback identity; order, case, whitespace, duplicates do not matter."""
    return {
        "location_name": identity_text(incident.get("location_name")),
        "description": identity_text(incident.get("description")),
        "stop_ids": _unique_sorted_ids(
            incident.get("affected_stop_ids") or incident.get("stop_ids")
        ),
        "route_ids": _unique_sorted_ids(
            incident.get("affected_route_ids") or incident.get("route_ids")
        ),
        "corridor_ids": _unique_sorted_ids(
            incident.get("affected_corridor_ids") or incident.get("corridor_ids")
        ),
        "impact_scope": identity_text(incident.get("impact_scope")),
    }


def incident_id_for(incident: dict[str, Any]) -> str:
    """Stable deterministic incident id: ``inc_`` plus a lowercase SHA-256 hex suffix."""
    pairs = source_identity_pairs(incident)
    if pairs:
        material = json.dumps(pairs, separators=(",", ":"))
    else:
        material = json.dumps(fallback_material(incident), separators=(",", ":"), sort_keys=True)
    return f"inc_{hashlib.sha256(material.encode('utf-8')).hexdigest()}"


def normalize_incident_record(
    incident: dict[str, Any],
    *,
    incident_id: str,
    now: float,
    now_iso: str,
    existing: dict[str, Any] | None,
    ttl_seconds: int,
) -> dict[str, Any]:
    """Canonical stored record; an invalid state becomes unconfirmed."""
    state = normalize_enum(incident.get("state"), ALLOWED_STATES, DEFAULT_STATE)
    lists = {
        canonical: bounded_ids(
            incident.get(canonical) or incident.get(alias), bound, upper=upper
        )
        for canonical, alias, bound, upper in LIST_FIELDS
    }
    return {
        "incident_id": incident_id,
        "state": state,
        "location_name": bounded_text(incident.get("location_name"), 120),
        "impact_scope": bounded_text(incident.get("impact_scope"), 40) or "nearby",
        "severity": bounded_text(incident.get("severity"), 16) or "medium",
        "description": bounded_text(incident.get("description"), 280),
        "observed_at": bounded_text(incident.get("observed_at"), 64) or None,
        "last_verified_at": bounded_text(incident.get("last_verified_at"), 64) or None,
        "expires_at": incident.get("expires_at") or (now + ttl_seconds),
        "source_coverage": bounded_ids(incident.get("source_coverage"), SOURCE_COVERAGE_BOUND),
        "corroboration_state": bounded_text(incident.get("corroboration_state"), 32) or state,
        "advisor_eligible": bool(incident.get("advisor_eligible")),
        "source_records": sanitize_source_records(incident.get("source_records")),
        "first_seen_at": (existing or {}).get("first_seen_at") or now_iso,
        "updated_at": now_iso,
        **lists,
    }


def record_is_expired(record: dict[str, Any]) -> bool:
    """True when the stored record's ``expires_at`` is in the past."""
    expires = record.get("expires_at")
    if expires is None:
        return False
    try:
        return float(expires) < time.time()
    except (TypeError, ValueError):
        return False
