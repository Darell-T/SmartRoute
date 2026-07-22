"""Bounded candidate evidence associations shared by incident handoff paths.

This leaf module deliberately has no dependency on the incident scanner, route
advisor, or provider clients.  It establishes one serialization contract for
the candidate-scoped association returned by the local cached-511NY matcher.
"""

from __future__ import annotations

from math import isfinite
import re
from typing import Any, Mapping


MAX_ASSOCIATED_CANDIDATES = 12
MAX_ASSOCIATED_MODES = 4
MAX_NEARBY_STOP_ASSOCIATIONS = 8
ALLOWED_ASSOCIATED_MODES = frozenset({"bus", "subway", "transfer", "walk"})
ALLOWED_RELEVANCE = frozenset({
    "nearby",
    "nearby_unconfirmed",
    "potential_bus_corridor",
    "station_access_only",
})
ALLOWED_IMPACT_SCOPES = frozenset({"nearby", "roadway", "station_access"})
ALLOWED_MATCH_SOURCES = frozenset({"point", "geometry", "polyline"})
_CANDIDATE_ROUTE_ID = re.compile(r"candidate-\d+")


def _text(value: object, limit: int) -> str:
    value = " ".join(str(value or "").split()).strip()
    return value if len(value) <= limit else value[:limit]


def _bounded_strings(
    value: object,
    *,
    allowed: frozenset[str] | None = None,
    limit: int,
) -> list[str]:
    if not isinstance(value, (list, tuple, set)):
        return []
    result: list[str] = []
    for raw in value:
        item = _text(raw, 80)
        if not item or (allowed is not None and item not in allowed):
            continue
        if item not in result:
            result.append(item)
        if len(result) >= limit:
            break
    return result


def _candidate_ids(value: object) -> list[str]:
    if not isinstance(value, (list, tuple, set)):
        return []
    result: list[str] = []
    for raw in value:
        item = _text(raw, 80)
        if not item or not _CANDIDATE_ROUTE_ID.fullmatch(item) or item in result:
            continue
        result.append(item)
        if len(result) >= MAX_ASSOCIATED_CANDIDATES:
            break
    return result


def _stop_association(value: object) -> dict[str, Any] | None:
    if not isinstance(value, Mapping):
        return None
    result: dict[str, Any] = {}
    for key in ("stop_id", "stop_name"):
        item = _text(value.get(key), 80)
        if item:
            result[key] = item
    match_source = _text(value.get("match_source"), 20)
    if match_source in ALLOWED_MATCH_SOURCES:
        result["match_source"] = match_source
    distance = value.get("distance_meters")
    if isinstance(distance, (int, float)) and not isinstance(distance, bool) and isfinite(distance) and 0 <= distance <= 2_000:
        result["distance_meters"] = round(float(distance), 1)
    candidate_ids = _candidate_ids(value.get("candidate_route_ids"))
    if candidate_ids:
        result["candidate_route_ids"] = candidate_ids
    modes = _bounded_strings(value.get("modes"), allowed=ALLOWED_ASSOCIATED_MODES, limit=MAX_ASSOCIATED_MODES)
    if modes:
        result["modes"] = modes
    return result or None


def normalize_matcher_association(value: Mapping[str, Any]) -> dict[str, Any]:
    """Return only bounded data produced by the local candidate matcher."""
    result: dict[str, Any] = {}
    candidate_ids = _candidate_ids(value.get("affected_candidate_route_ids"))
    if candidate_ids:
        result["affected_candidate_route_ids"] = candidate_ids
    modes = _bounded_strings(value.get("affected_modes"), allowed=ALLOWED_ASSOCIATED_MODES, limit=MAX_ASSOCIATED_MODES)
    if modes:
        result["affected_modes"] = modes
    relevance = value.get("relevance_by_mode")
    if isinstance(relevance, Mapping):
        normalized_relevance = {
            mode: _text(level, 80)
            for mode, level in relevance.items()
            if isinstance(mode, str)
            and mode in ALLOWED_ASSOCIATED_MODES
            and _text(level, 80) in ALLOWED_RELEVANCE
        }
        if normalized_relevance:
            result["relevance_by_mode"] = dict(sorted(normalized_relevance.items()))
    impact_scope = _text(value.get("impact_scope"), 40)
    if impact_scope in ALLOWED_IMPACT_SCOPES:
        result["impact_scope"] = impact_scope
    nearest_stop = _stop_association(value.get("nearest_stop"))
    if nearest_stop:
        result["nearest_stop"] = nearest_stop
    nearby_stops = value.get("nearby_stops")
    if isinstance(nearby_stops, list):
        normalized_stops = [
            stop for item in nearby_stops[:MAX_NEARBY_STOP_ASSOCIATIONS]
            if (stop := _stop_association(item)) is not None
        ]
        if normalized_stops:
            result["nearby_stops"] = normalized_stops
    return result


def attach_verified_match_association(
    authoritative_incident: Mapping[str, Any], matcher_result: Mapping[str, Any]
) -> dict[str, Any]:
    """Attach one sanitized exact matcher association to authoritative data.

    The caller owns the provider-row allowlist and must call this only after a
    valid exact source reference was selected.  This adapter owns the common
    association shape and the internal provenance bit consumed by the advisor
    handoff.  It never adds provider data or model text itself.
    """
    result = dict(authoritative_incident)
    result.update(normalize_matcher_association(matcher_result))
    result["_verified_511ny_match"] = True
    return result


def verified_match_association(incident: Mapping[str, Any]) -> dict[str, Any]:
    """Recover association only from an exact-match provenance-marked row.

    The provenance bit is assigned in ``incident_monitor`` after a model chose
    an exact local-tool ``source_ref``.  It may survive only inside merge
    evidence; neither model JSON nor arbitrary incident fields can create an
    advisor-visible association without it.
    """
    candidates: list[Mapping[str, Any]] = [incident]
    evidence = incident.get("evidence")
    if isinstance(evidence, list):
        candidates.extend(item for item in evidence if isinstance(item, Mapping))
    for candidate in candidates:
        if candidate.get("_verified_511ny_match") is True:
            return normalize_matcher_association(candidate)
    return {}
