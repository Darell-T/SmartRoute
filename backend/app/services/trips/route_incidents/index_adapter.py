"""Pure context matching and projection for the incident-index lookup.

Normal rider routing performs exactly one immediate synchronous index lookup:
no provider scan, X/web search, cache, retry, timeout, or background queue runs
on the request path. These helpers only translate candidate stop contexts into
lookup tokens and project canonical index records into bounded advisor shapes;
the caller owns lookup orchestration and failure degradation.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from app.services.incidents.batches import coverage_batch_ids_for_point
from app.services.trips.route_incidents.association import _CANDIDATE_ROUTE_ID
from app.services.trips.route_incidents.context import CandidateStopContext

_ALLOWED_SEVERITIES = {"low", "medium", "high"}
_ALLOWED_SCOPES = {"nearby", "station_access", "subway_operations", "bus_corridor", "walking"}
_ALLOWED_MODES = {"subway", "bus", "walk", "transfer"}
_MAX_ADVISOR_INCIDENTS = 12
_MAX_WARNINGS = 16
_MAX_SOURCE_RECORDS = 8


def _text(value: object, limit: int) -> str:
    text = " ".join(str(value or "").split()).strip()
    return text[:limit] if text else ""


def _string_list(value: object, limit: int) -> list[str]:
    if not isinstance(value, (list, tuple, set)):
        return []
    result: list[str] = []
    for raw in value:
        item = _text(raw, 120)
        if item and item not in result:
            result.append(item)
            if len(result) >= limit:
                break
    return result


def _source_records(record: Mapping[str, Any]) -> list[dict[str, str]]:
    raw = record.get("source_records")
    if not isinstance(raw, (list, tuple)):
        return []
    records: list[dict[str, str]] = []
    for item in raw:
        if not isinstance(item, Mapping) or len(records) >= _MAX_SOURCE_RECORDS:
            continue
        entry = {key: value for key in ("source", "source_id", "observed_at") if (value := _text(item.get(key), 120))}
        url = _text(item.get("source_url"), 240)
        if url:
            entry["source_url"] = url
        if entry:
            records.append(entry)
    return records


def _source_label(record: Mapping[str, Any]) -> str:
    sources = [entry["source"] for entry in _source_records(record) if entry.get("source")]
    if not sources:
        sources = _string_list(record.get("source_coverage"), 8)
    return " + ".join(dict.fromkeys(sources))[:60]


def _lookup_tokens(item: CandidateStopContext) -> tuple[str, set[str], tuple[str, ...]]:
    stop_id = _text(item.stop_id, 120)
    routes = {_text(route_id, 120) for route_id in item.route_ids if _text(route_id, 120)}
    batch_ids = coverage_batch_ids_for_point(item.latitude, item.longitude)
    return stop_id, routes, batch_ids


def _matching_row(
    item: CandidateStopContext,
    stop_id: str,
    routes: set[str],
    batch_ids: tuple[str, ...],
) -> dict[str, Any]:
    return {
        "stops": {stop_id.casefold()} if stop_id else set(),
        "routes": {route.casefold() for route in routes},
        "batches": set(batch_ids),
        "associations": item.associations,
    }


class _LookupKeyIndex:
    def __init__(self) -> None:
        self.rows: list[dict[str, Any]] = []
        self.stop_ids: list[str] = []
        self.route_ids: list[str] = []
        self.coverage_ids: list[str] = []

    def add(self, item: CandidateStopContext) -> None:
        stop_id, routes, batch_ids = _lookup_tokens(item)
        if stop_id and stop_id not in self.stop_ids:
            self.stop_ids.append(stop_id)
        for route_id in sorted(route.upper() for route in routes):
            if route_id not in self.route_ids:
                self.route_ids.append(route_id)
        for batch_id in batch_ids:
            if batch_id not in self.coverage_ids:
                self.coverage_ids.append(batch_id)
        self.rows.append(_matching_row(item, stop_id, routes, batch_ids))


def extract_lookup_context(
    contexts: Iterable[object],
) -> tuple[list[dict[str, Any]], list[str], list[str], list[str]]:
    """One pass: matching rows plus deduped lookup tokens; malformed ignored."""
    index = _LookupKeyIndex()
    for item in contexts:
        if isinstance(item, CandidateStopContext):
            index.add(item)
    return index.rows, index.stop_ids, index.route_ids, index.coverage_ids


def _record_dimensions(record: Mapping[str, Any]) -> tuple[set[str], set[str], set[str]]:
    return (
        {_text(s, 120).casefold() for s in _string_list(record.get("affected_stop_ids"), 24)},
        {_text(r, 120).casefold() for r in _string_list(record.get("affected_route_ids"), 24)},
        {_text(b, 120).casefold() for b in _string_list(record.get("affected_batch_ids"), 12)},
    )


def _dimension_intersects(required: set[str], present: set[str]) -> bool:
    """A missing dimension is unrestricted; a present one must intersect."""
    return not required or bool(required & present)


def _dimensions_match_row(
    stops: set[str],
    routes: set[str],
    batches: set[str],
    row: Mapping[str, Any],
) -> bool:
    return (
        _dimension_intersects(stops, row["stops"])
        and _dimension_intersects(routes, row["routes"])
        and _dimension_intersects(batches, row["batches"])
    )


def _association_identities(association: object) -> tuple[str | None, str | None]:
    candidate = _text(getattr(association, "candidate_route_id", None), 80)
    if not candidate or not _CANDIDATE_ROUTE_ID.fullmatch(candidate):
        candidate = None
    mode = _text(getattr(association, "mode", None), 24).casefold()
    if mode not in _ALLOWED_MODES:
        mode = None
    return candidate, mode


def _row_association_identities(row: Mapping[str, Any]) -> tuple[set[str], set[str]]:
    candidate_ids: set[str] = set()
    modes: set[str] = set()
    for association in row["associations"]:
        candidate, mode = _association_identities(association)
        if candidate:
            candidate_ids.add(candidate)
        if mode:
            modes.add(mode)
    return candidate_ids, modes


def _matched_candidate_ids(
    record: Mapping[str, Any], rows: list[dict[str, Any]]
) -> tuple[list[str], list[str]]:
    """Every present dimension (stop, route, batch) must match one context;
    batch-only or corridor-only records are never precisely matched."""
    stops, routes, batches = _record_dimensions(record)
    if not (stops or routes):
        return [], []
    candidate_ids: set[str] = set()
    modes: set[str] = set()
    for row in rows:
        if not _dimensions_match_row(stops, routes, batches, row):
            continue
        row_ids, row_modes = _row_association_identities(row)
        candidate_ids.update(row_ids)
        modes.update(row_modes)
    return sorted(candidate_ids), sorted(modes)


def _project_incident(
    record: Mapping[str, Any],
    candidate_ids: list[str],
    modes: list[str],
    *,
    advisor_eligible: bool,
) -> dict[str, Any]:
    """Bounded advisor incident shape from one canonical index record."""
    severity = _text(record.get("severity"), 16).casefold()
    scope = _text(record.get("impact_scope"), 40).casefold()
    result: dict[str, Any] = {
        "incident_id": _text(record.get("incident_id"), 120),
        "state": _text(record.get("state"), 24),
        "location": _text(record.get("location_name"), 120),
        "severity": severity if severity in _ALLOWED_SEVERITIES else "medium",
        "description": _text(record.get("description"), 280),
        "impact_scope": scope if scope in _ALLOWED_SCOPES else "nearby",
        "source": _source_label(record),
        "affected_candidate_route_ids": candidate_ids,
        "affected_stop_ids": _string_list(record.get("affected_stop_ids"), 24),
        "affected_route_ids": _string_list(record.get("affected_route_ids"), 24),
        "affected_batch_ids": _string_list(record.get("affected_batch_ids"), 12),
        "affected_corridor_ids": _string_list(record.get("affected_corridor_ids"), 12),
        "source_coverage": _string_list(record.get("source_coverage"), 8),
        "source_records": _source_records(record),
        # Canonical confirmation, never source-record count: only a stored
        # confirmed state (authoritative official source or corroborated
        # scout) is verified evidence; two same-origin/unconfirmed records
        # are not proof.
        "corroborated": _text(record.get("state"), 24).casefold() == "confirmed",
        "advisor_eligible": advisor_eligible,
    }
    if modes:
        result["affected_modes"] = modes
    return result


def _warning_overlap(record: Mapping[str, Any], rows: list[dict[str, Any]]) -> bool:
    """Warnings keep any overlapping stop, route, or batch, including imprecise matches."""
    stops, routes, batches = _record_dimensions(record)
    return any(
        (stops & row["stops"]) or (routes & row["routes"]) or (batches & row["batches"])
        for row in rows
    )


def project_records(
    records: Iterable[object], rows: list[dict[str, Any]]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Advisor incidents and bounded warnings from returned index records."""
    advisor: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    for raw in records:
        if not isinstance(raw, Mapping):
            continue
        candidate_ids, modes = _matched_candidate_ids(raw, rows)
        eligible = (
            raw.get("advisor_eligible") is True
            and raw.get("state") == "confirmed"
            and bool(candidate_ids)
        )
        projected = _project_incident(raw, candidate_ids, modes, advisor_eligible=eligible)
        if eligible:
            advisor.append(projected)
        elif _warning_overlap(raw, rows):
            warnings.append(projected)
        if len(advisor) >= _MAX_ADVISOR_INCIDENTS and len(warnings) >= _MAX_WARNINGS:
            break
    return advisor[:_MAX_ADVISOR_INCIDENTS], warnings[:_MAX_WARNINGS]
