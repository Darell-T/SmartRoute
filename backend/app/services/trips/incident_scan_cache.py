"""Bounded cache serialization for normalized route incident scans."""

from __future__ import annotations

import hashlib
import json
import re
import time
from collections import OrderedDict
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping
from zoneinfo import ZoneInfo

from app.services.trips.incident_evidence import (
    canonical_citation_url,
    source_identity_from_url,
    source_type_matches_url,
)
from app.services.trips.incident_context import CandidateStopContext
from app.utils import cache


COMPLETE_CACHE_TTL_S = 180
PARTIAL_CACHE_TTL_S = 30
FAILURE_BACKOFF_TTL_S = 20
CACHE_PREFIX = "agent:route-incidents:"
LOCAL_FALLBACK_MAX_ENTRIES = 128
_NYC_TZ = ZoneInfo("America/New_York")
_ALLOWED_STATUSES = {"complete", "partial", "failed", "disabled"}
_ALLOWED_SOURCES = {"x_search", "web_search"}
_ALLOWED_SCOPES = {"nearby", "station_access", "subway_operations", "bus_corridor", "walking"}
_ALLOWED_SEVERITIES = {"low", "medium", "high"}
_local_fallback: OrderedDict[str, tuple[float, str]] = OrderedDict()


def _text(value: object, limit: int) -> str:
    text = " ".join(str(value or "").split()).strip()
    return text[:limit] if text else ""


def station_key(value: object) -> str:
    return re.sub(r"[^a-z0-9]+", "", _text(value, 160).casefold())


def _timestamp(value: object) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _string_list(value: object, *, limit: int, allowed: set[str] | None = None) -> list[str]:
    if not isinstance(value, (list, tuple, set)):
        return []
    result: list[str] = []
    for raw in value:
        item = _text(raw, 120)
        if not item or (allowed is not None and item not in allowed) or item in result:
            continue
        result.append(item)
        if len(result) >= limit:
            break
    return result


def normalize_advisor_incident(incident: Mapping[str, Any]) -> dict[str, Any]:
    """Preserve only bounded, already-normalized scanner evidence."""
    severity = _text(incident.get("severity"), 16).lower()
    scope = _text(incident.get("impact_scope"), 40).lower()
    result: dict[str, Any] = {
        "location": _text(incident.get("location"), 120),
        "nearby_station": _text(incident.get("nearby_station"), 100),
        "severity": severity if severity in _ALLOWED_SEVERITIES else "medium",
        "description": _text(incident.get("description"), 280),
        "source": _text(incident.get("source"), 60),
    }
    if scope in _ALLOWED_SCOPES:
        result["impact_scope"] = scope
    candidates = [
        value for value in _string_list(incident.get("affected_candidate_route_ids"), limit=12)
        if re.fullmatch(r"candidate-\d+", value)
    ]
    if candidates:
        result["affected_candidate_route_ids"] = candidates
    modes = _string_list(incident.get("affected_modes"), limit=4, allowed={"subway", "bus", "walk", "transfer"})
    if modes:
        result["affected_modes"] = modes
    evidence: list[dict[str, str]] = []
    for raw in incident.get("evidence", []) if isinstance(incident.get("evidence"), list) else []:
        if not isinstance(raw, Mapping):
            continue
        source_type = _text(raw.get("source_type"), 24)
        source_url = canonical_citation_url(raw.get("source_url"))
        source_origin = _text(raw.get("source_origin"), 160)
        observed_at = _timestamp(raw.get("observed_at"))
        source_identity = source_identity_from_url(source_url)
        if (
            source_type not in _ALLOWED_SOURCES
            or not source_url
            or not source_origin
            or not observed_at
            or source_identity is None
            or not source_type_matches_url(source_type, source_url)
        ):
            continue
        evidence.append(
            {
                "source_type": source_type,
                "source_url": source_url,
                "source_origin": source_origin,
                "source_identity": source_identity,
                "observed_at": observed_at,
            }
        )
        if len(evidence) >= 6:
            break
    if evidence:
        result["evidence"] = evidence
    distinct_identities = {entry["source_identity"] for entry in evidence}
    declared_origins = {
        canonical_citation_url(entry["source_origin"]) or entry["source_origin"].casefold()
        for entry in evidence
    }
    source_types = {entry["source_type"] for entry in evidence}
    corroborated = len(distinct_identities) >= 2
    result["corroborated"] = corroborated
    result["advisor_eligible"] = bool(
        incident.get("advisor_eligible") is True
        and corroborated
        and source_types == _ALLOWED_SOURCES
        and len(declared_origins) == len(evidence)
        and candidates
        and scope in _ALLOWED_SCOPES - {"nearby"}
    )
    return result


def _normalize_metadata(value: object) -> dict[str, Any] | None:
    if not isinstance(value, Mapping):
        return None
    status = value.get("status")
    scanned_at = _timestamp(value.get("scanned_at"))
    if status not in _ALLOWED_STATUSES or scanned_at is None:
        return None
    sources = value.get("sources") if isinstance(value.get("sources"), Mapping) else {}
    normalized: dict[str, Any] = {
        "status": status,
        "scanned_at": scanned_at,
        "cache_hit": bool(value.get("cache_hit")),
        "sources": {
            "attempted": _string_list(sources.get("attempted"), limit=2, allowed=_ALLOWED_SOURCES),
            "completed": _string_list(sources.get("completed"), limit=2, allowed=_ALLOWED_SOURCES),
        },
    }
    errors = _string_list(sources.get("errors"), limit=3)
    if errors:
        normalized["sources"]["errors"] = errors
    rounds = value.get("tool_rounds")
    if isinstance(rounds, int) and not isinstance(rounds, bool):
        normalized["tool_rounds"] = max(0, min(rounds, 2))
    warning_count = value.get("warning_count")
    if isinstance(warning_count, int) and not isinstance(warning_count, bool):
        normalized["warning_count"] = max(0, min(warning_count, 32))
    return normalized


def _travel_bucket(value: datetime | str | None) -> str:
    if isinstance(value, datetime):
        when = value
    elif isinstance(value, str):
        try:
            when = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            when = datetime.now(timezone.utc)
    else:
        when = datetime.now(timezone.utc)
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    local = when.astimezone(_NYC_TZ)
    return local.replace(minute=(local.minute // 15) * 15, second=0, microsecond=0).isoformat()


def incident_cache_key(route_context: Iterable[object], travel_at: datetime | str | None) -> str | None:
    stations: set[str] = set()
    routes: set[str] = set()
    for item in route_context:
        if isinstance(item, CandidateStopContext):
            station = item.stop_reference
            routes.update(route.upper() for route in item.route_ids if route)
        elif isinstance(item, Mapping):
            station = station_key(item.get("stop_name") or item.get("name"))
            routes.update(_string_list(item.get("route_ids"), limit=32))
        else:
            station = station_key(item)
        if station:
            stations.add(station)
    if not stations:
        return None
    material = json.dumps(
        {"stations": sorted(stations), "routes": sorted(routes), "travel_bucket": _travel_bucket(travel_at)},
        separators=(",", ":"),
    )
    return CACHE_PREFIX + hashlib.sha256(material.encode("utf-8")).hexdigest()


def cached_scan_contract(value: object) -> dict[str, Any] | None:
    if isinstance(value, bytes):
        value = value.decode("utf-8", errors="replace")
    try:
        payload = json.loads(value) if isinstance(value, str) else value
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, Mapping):
        return None
    metadata = _normalize_metadata(payload.get("scan_metadata"))
    incidents = payload.get("incidents")
    warnings = payload.get("warnings")
    if metadata is None or not isinstance(incidents, list) or not isinstance(warnings, list):
        return None
    metadata["cache_hit"] = True
    return {
        "incidents": [normalize_advisor_incident(item) for item in incidents if isinstance(item, Mapping)],
        "warnings": [normalize_advisor_incident(item) for item in warnings if isinstance(item, Mapping)],
        "scan_metadata": metadata,
    }


def _prune_local_fallback(now: float) -> None:
    for cache_key, (expires_at, _value) in list(_local_fallback.items()):
        if expires_at <= now:
            _local_fallback.pop(cache_key, None)


def _local_get(key: str) -> str | None:
    now = time.monotonic()
    _prune_local_fallback(now)
    entry = _local_fallback.get(key)
    if entry is None:
        return None
    _local_fallback.move_to_end(key)
    return entry[1]


def _local_set(key: str, value: str, ttl: int) -> None:
    now = time.monotonic()
    _prune_local_fallback(now)
    _local_fallback[key] = (now + ttl, value)
    _local_fallback.move_to_end(key)
    while len(_local_fallback) > LOCAL_FALLBACK_MAX_ENTRIES:
        _local_fallback.popitem(last=False)


def _load_cached_value(key: str) -> object:
    """Use Redis when configured; a local fallback is always capacity-bounded."""
    redis_client = cache.redis_client
    if redis_client is not None:
        try:
            cached = redis_client.get(key)
        except Exception:
            return _local_get(key)
        if cached is not None:
            return cached
    return _local_get(key)


def load_cached_scan(key: str) -> dict[str, Any] | None:
    return cached_scan_contract(_load_cached_value(key))


def cache_scan_contract(key: str, result: Mapping[str, Any]) -> None:
    metadata = result.get("scan_metadata")
    status = metadata.get("status") if isinstance(metadata, Mapping) else None
    ttl = {
        "complete": COMPLETE_CACHE_TTL_S,
        "partial": PARTIAL_CACHE_TTL_S,
        "failed": FAILURE_BACKOFF_TTL_S,
    }.get(status)
    if ttl is None:
        return
    serialized = json.dumps(result, separators=(",", ":"))
    redis_client = cache.redis_client
    if redis_client is not None:
        try:
            redis_client.setex(key, ttl, serialized)
            return
        except Exception:
            pass
    try:
        _local_set(key, serialized, ttl)
    except Exception:
        return
