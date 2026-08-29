"""Conservative, source-aware incident evidence filtering and merging."""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from datetime import UTC, datetime, timedelta
from typing import Any

from app.services.geography import distance_meters
from app.services.trips.route_incidents.context import valid_coordinate_pair
from app.services.trips.route_incidents.matching import _as_mapping

_OFFICIAL_SOURCES = {"511ny", "mta", "mta_alert", "vehicle"}
# "Closed" often describes an active roadway closure.  Only unambiguously
# terminal semantics remove an item in the absence of an expired end time.
_TERMINAL_MARKERS = ("resolved", "cleared", "cancelled", "canceled", "expired", "ended", "completed")


def _parse_time(value: object) -> datetime | None:
    if isinstance(value, datetime):
        result = value
    elif isinstance(value, str):
        try:
            result = datetime.fromisoformat(value)
        except ValueError:
            return None
    else:
        return None
    if result.tzinfo is None or result.utcoffset() is None:
        return None
    return result.astimezone(UTC)


def _source(item: Mapping[str, Any]) -> str:
    return str(item.get("source") or "unknown").strip().casefold()


_RESOLVED_NARRATIVE = re.compile(
    r"\b(?:incident|event|condition|situation)\s+(?:has\s+been\s+|is\s+|was\s+)?"
    r"(?:resolved|cleared|cancelled|canceled)\b"
)
_OFFICIAL_FIELD_KEYS = (
    "latitude",
    "longitude",
    "lon",
    "reported_at",
    "updated_at",
    "starts_at",
    "expected_end_at",
)
_COORDINATE_KEYS = ("latitude", "longitude", "lon")
_AFFECTED_COLLECTION_KEYS = ("affected_routes", "affected_stops", "affected_modes")


def _joined_fields(item: Mapping[str, Any], keys: tuple[str, ...]) -> str:
    return " ".join(str(item.get(key) or "") for key in keys).casefold()


def _terminal_status(item: Mapping[str, Any]) -> bool:
    return any(marker in _joined_fields(item, ("status", "status_text")) for marker in _TERMINAL_MARKERS)


def _resolved_narrative(item: Mapping[str, Any]) -> bool:
    # Descriptions can legitimately say a road is "closed" during an active
    # incident. Only an explicit resolution statement in narrative text is
    # sufficient to remove it without a resolved status field.
    return bool(_RESOLVED_NARRATIVE.search(_joined_fields(item, ("description", "comment"))))


def _ended_before(item: Mapping[str, Any], now: datetime) -> bool:
    end = _parse_time(item.get("expected_end_at") or item.get("ends_at") or item.get("end_time"))
    return bool(end and end < now)


def _stale_unofficial(item: Mapping[str, Any], now: datetime, social_max_age: timedelta) -> bool:
    if _source(item) in _OFFICIAL_SOURCES:
        return False
    observed = _parse_time(item.get("observed_at") or item.get("updated_at") or item.get("reported_at"))
    return bool(observed and observed < now - social_max_age)


def _current(item: Mapping[str, Any], now: datetime, social_max_age: timedelta) -> bool:
    if _terminal_status(item) or _resolved_narrative(item) or _ended_before(item, now):
        return False
    return not _stale_unofficial(item, now, social_max_age)


def filter_current_incidents(
    incidents: Iterable[object], *, now: datetime | None = None, social_max_age_hours: float = 6.0
) -> list[dict[str, Any]]:
    """Drop resolved/expired items and stale non-official reports."""
    current_time = (now or datetime.now(UTC)).astimezone(UTC)
    max_age = timedelta(hours=max(0.0, social_max_age_hours))
    filtered: list[dict[str, Any]] = []
    for incident in incidents or []:
        item = _as_mapping(incident)
        if item is not None and _current(item, current_time, max_age):
            filtered.append(dict(item))
    return filtered


def _coordinates(item: Mapping[str, Any]) -> tuple[float, float] | None:
    return valid_coordinate_pair(item.get("latitude"), item.get("longitude", item.get("lon")))


def _event_time(item: Mapping[str, Any]) -> datetime | None:
    for key in ("updated_at", "reported_at", "observed_at", "starts_at"):
        parsed = _parse_time(item.get(key))
        if parsed:
            return parsed
    return None


def _signature(item: Mapping[str, Any]) -> set[str]:
    words = " ".join(str(item.get(key) or "") for key in ("event_type", "event_subtype", "roadway_name", "description"))
    stopwords = {"avenue", "street", "road", "lane", "near", "from", "with", "the", "and"}
    return {
        word for word in words.casefold().replace("/", " ").split()
        if len(word) > 3 and word not in stopwords
    }


def _context_keys(item: Mapping[str, Any]) -> set[str]:
    """Stable stop/station/corridor identifiers for coordinate-poor evidence."""
    values: list[object] = [
        item.get("nearby_station"), item.get("station"), item.get("stop_name"),
        item.get("roadway_name"), item.get("location"), item.get("corridor"),
    ]
    affected_stops = item.get("affected_stops")
    values.extend(affected_stops if isinstance(affected_stops, (list, tuple, set)) else [affected_stops])
    keys = set()
    for value in values:
        text = str(value or "").casefold()
        text = re.sub(r"\bavenue\b", "ave", text)
        text = re.sub(r"\bstreet\b", "st", text)
        text = re.sub(r"\bboulevard\b", "blvd", text)
        normalized = re.sub(r"[^a-z0-9]+", "", text)
        if len(normalized) >= 4:
            keys.add(normalized)
    return keys


def _times_compatible(left: Mapping[str, Any], right: Mapping[str, Any]) -> bool:
    left_time, right_time = _event_time(left), _event_time(right)
    return not (left_time and right_time and abs(left_time - right_time) > timedelta(hours=4))


def _source_identity(item: Mapping[str, Any]) -> tuple[str, str]:
    return (
        _source(item),
        str(item.get("source_id") or item.get("id") or ""),
    )


def _same_source_identity(left: Mapping[str, Any], right: Mapping[str, Any]) -> bool:
    left_id = _source_identity(left)
    return bool(left_id[1]) and left_id == _source_identity(right)


def _nearby_related(left: Mapping[str, Any], right: Mapping[str, Any]) -> bool:
    left_coords, right_coords = _coordinates(left), _coordinates(right)
    overlap = len(_signature(left).intersection(_signature(right))) >= 2
    if left_coords and right_coords:
        if distance_meters(*left_coords, *right_coords) > 250:
            return False
        # Nearby events commonly share a road name. Require two meaningful
        # terms before joining separately reported incidents on that corridor.
        return overlap
    if not _context_keys(left).intersection(_context_keys(right)):
        return False
    return overlap


def _same_incident(left: Mapping[str, Any], right: Mapping[str, Any]) -> bool:
    if _same_source_identity(left, right):
        return True
    if not _times_compatible(left, right):
        return False
    return _nearby_related(left, right)


def _official(item: Mapping[str, Any]) -> bool:
    return _source(item) in _OFFICIAL_SOURCES


def _overlay_present(base: dict[str, Any], evidence: Mapping[str, Any], keys: tuple[str, ...]) -> dict[str, Any]:
    updates = {key: evidence[key] for key in keys if evidence.get(key) is not None}
    if not updates:
        return base
    merged = dict(base)
    merged.update(updates)
    return merged


def _official_fields_win(base: dict[str, Any], evidence: dict[str, Any]) -> dict[str, Any]:
    """Official evidence may overwrite social coordinates and clocks. Social never overwrites official."""
    if _official(evidence) and not _official(base):
        return _overlay_present(base, evidence, _OFFICIAL_FIELD_KEYS)
    if not _coordinates(base) and _coordinates(evidence):
        return _overlay_present(base, evidence, _COORDINATE_KEYS)
    return base


def _nonempty_strings(values: object) -> set[str]:
    return {str(value) for value in (values or []) if value}


def _union_affected_collections(base: dict[str, Any], evidence: Mapping[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key in _AFFECTED_COLLECTION_KEYS:
        combined = _nonempty_strings(base.get(key)) | _nonempty_strings(evidence.get(key))
        if combined:
            merged[key] = sorted(combined)
    return merged


def _union_sources(base: Mapping[str, Any], evidence: Mapping[str, Any]) -> list[str]:
    return sorted(
        _nonempty_strings(base.get("sources") or [base.get("source")])
        | _nonempty_strings(evidence.get("sources") or [evidence.get("source")])
    )


def _evidence_row_identity(row: Mapping[str, Any]) -> tuple[str, str]:
    return (str(row.get("source") or ""), str(row.get("source_id") or row.get("id") or ""))


def _with_unique_evidence_row(base: dict[str, Any], evidence: dict[str, Any]) -> dict[str, Any]:
    rows = list(base.get("evidence") or [])
    identity = _evidence_row_identity(evidence)
    known = {
        _evidence_row_identity(row)
        for row in rows
        if isinstance(row, Mapping)
    }
    if identity not in known:
        rows.append(dict(evidence))
    merged = dict(base)
    merged["evidence"] = rows
    return merged


def _combine_related_records(base: dict[str, Any], evidence: dict[str, Any]) -> dict[str, Any]:
    merged = _official_fields_win(base, evidence)
    merged = _union_affected_collections(merged, evidence)
    merged["sources"] = _union_sources(merged, evidence)
    return _with_unique_evidence_row(merged, evidence)


def _seed_merged_record(item: dict[str, Any]) -> dict[str, Any]:
    initial = dict(item)
    initial["sources"] = _union_sources(initial, initial)
    initial["evidence"] = [dict(item)]
    return initial


def merge_incident_evidence(incidents: Iterable[object], *, now: datetime | None = None) -> list[dict[str, Any]]:
    """Deduplicate only clearly related current incidents, preserving evidence."""
    merged: list[dict[str, Any]] = []
    for item in filter_current_incidents(incidents, now=now):
        match_index = next(
            (index for index, existing in enumerate(merged) if _same_incident(existing, item)),
            None,
        )
        if match_index is None:
            merged.append(_seed_merged_record(item))
        else:
            merged[match_index] = _combine_related_records(merged[match_index], item)
    return merged
