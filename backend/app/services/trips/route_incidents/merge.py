"""Conservative, source-aware incident evidence filtering and merging."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import re
from typing import Any, Iterable, Mapping

from app.services.trips.route_incidents.context import valid_coordinate_pair
from app.services.trips.route_incidents.matching import _as_mapping
from app.services.geography import distance_meters


_OFFICIAL_SOURCES = {"511ny", "mta", "mta_alert", "vehicle"}
# "Closed" often describes an active roadway closure.  Only unambiguously
# terminal semantics remove an item in the absence of an expired end time.
_TERMINAL_MARKERS = ("resolved", "cleared", "cancelled", "canceled", "expired", "ended", "completed")


def _parse_time(value: object) -> datetime | None:
    if isinstance(value, datetime):
        result = value
    elif isinstance(value, str):
        try:
            result = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    if result.tzinfo is None or result.utcoffset() is None:
        return None
    return result.astimezone(timezone.utc)


def _source(item: Mapping[str, Any]) -> str:
    return str(item.get("source") or "unknown").strip().casefold()


def _current(item: Mapping[str, Any], now: datetime, social_max_age: timedelta) -> bool:
    status = " ".join(str(item.get(key) or "") for key in ("status", "status_text")).casefold()
    if any(marker in status for marker in _TERMINAL_MARKERS):
        return False
    # Descriptions can legitimately say a road is "closed" during an active
    # incident.  Only an explicit resolution statement in narrative text is
    # sufficient to remove it without a resolved status field.
    narrative = " ".join(str(item.get(key) or "") for key in ("description", "comment")).casefold()
    if re.search(r"\b(?:incident|event|condition|situation)\s+(?:has\s+been\s+|is\s+|was\s+)?(?:resolved|cleared|cancelled|canceled)\b", narrative):
        return False
    end = _parse_time(item.get("expected_end_at") or item.get("ends_at") or item.get("end_time"))
    if end and end < now:
        return False
    if _source(item) not in _OFFICIAL_SOURCES:
        observed = _parse_time(item.get("observed_at") or item.get("updated_at") or item.get("reported_at"))
        if observed and observed < now - social_max_age:
            return False
    return True


def filter_current_incidents(
    incidents: Iterable[object], *, now: datetime | None = None, social_max_age_hours: float = 6.0
) -> list[dict[str, Any]]:
    """Drop resolved/expired items and stale non-official reports."""
    current_time = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
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


def _same_incident(left: Mapping[str, Any], right: Mapping[str, Any]) -> bool:
    left_source, right_source = _source(left), _source(right)
    left_id = str(left.get("source_id") or left.get("id") or "")
    right_id = str(right.get("source_id") or right.get("id") or "")
    if left_source == right_source and left_id and left_id == right_id:
        return True
    left_coords, right_coords = _coordinates(left), _coordinates(right)
    if not _times_compatible(left, right):
        return False
    meaningful_overlap = len(_signature(left).intersection(_signature(right))) >= 2
    if left_coords and right_coords:
        if distance_meters(*left_coords, *right_coords) > 250:
            return False
        # Nearby events commonly share a road name.  Require two meaningful
        # terms before joining separately reported incidents on that corridor.
        return meaningful_overlap
    if not _context_keys(left).intersection(_context_keys(right)):
        return False
    return meaningful_overlap


def _official(item: Mapping[str, Any]) -> bool:
    return _source(item) in _OFFICIAL_SOURCES


def _prefer(base: dict[str, Any], evidence: dict[str, Any]) -> None:
    """Preserve official coordinates/timing; never let social data replace it."""
    base_official, evidence_official = _official(base), _official(evidence)
    if evidence_official and not base_official:
        for key in ("latitude", "longitude", "lon", "reported_at", "updated_at", "starts_at", "expected_end_at"):
            if evidence.get(key) is not None:
                base[key] = evidence[key]
    elif not _coordinates(base) and _coordinates(evidence):
        for key in ("latitude", "longitude", "lon"):
            if evidence.get(key) is not None:
                base[key] = evidence[key]
    for key in ("affected_routes", "affected_stops", "affected_modes"):
        combined = {str(value) for value in (base.get(key) or []) if value}
        combined.update(str(value) for value in (evidence.get(key) or []) if value)
        if combined:
            base[key] = sorted(combined)
    sources = {str(value) for value in (base.get("sources") or [base.get("source")]) if value}
    sources.update(str(value) for value in (evidence.get("sources") or [evidence.get("source")]) if value)
    base["sources"] = sorted(sources)
    evidence_rows = base.setdefault("evidence", [])
    evidence_identity = (str(evidence.get("source") or ""), str(evidence.get("source_id") or evidence.get("id") or ""))
    if evidence_identity not in {(str(row.get("source") or ""), str(row.get("source_id") or row.get("id") or "")) for row in evidence_rows if isinstance(row, Mapping)}:
        evidence_rows.append(dict(evidence))


def merge_incident_evidence(incidents: Iterable[object], *, now: datetime | None = None) -> list[dict[str, Any]]:
    """Deduplicate only clearly related current incidents, preserving evidence."""
    merged: list[dict[str, Any]] = []
    for item in filter_current_incidents(incidents, now=now):
        match = next((existing for existing in merged if _same_incident(existing, item)), None)
        if match is None:
            initial = dict(item)
            initial["sources"] = sorted({str(value) for value in (initial.get("sources") or [initial.get("source")]) if value})
            initial["evidence"] = [dict(item)]
            merged.append(initial)
        else:
            _prefer(match, item)
    return merged
