"""Evidence scoping and coverage helpers for prepared route options."""

from __future__ import annotations

from typing import Any, Iterable

from app.services.agent.tools.plan_trip_prepare import PreparedLeg

_COVERAGE_STATUSES = {"current", "partial", "stale", "unavailable", "unscanned"}
_STATUS_ORDER = {"current": 0, "partial": 1, "stale": 2, "unavailable": 3, "unscanned": 4}
_PROVIDER_UNAVAILABLE = {"failed", "timeout", "unavailable", "provider_unavailable"}


class _MergedEnvelope:
    """Small serializable envelope preserving the worst source status."""

    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = payload

    def status_at(self, now: Any = None) -> str:
        return str(self._payload.get("status") or "unavailable")

    def current_payload(self, now: Any = None) -> Any:
        if self.status_at(now) != "current":
            return None
        return self._payload.get("payload")

    def to_model_dict(self, *, empty: Any, now: Any = None) -> dict[str, Any]:
        result = dict(self._payload)
        if result.get("status") != "current":
            result["payload"] = empty
        return result


def coverage_for_prepared(prepared: PreparedLeg | Any) -> dict[str, str]:
    incident = str(getattr(prepared, "incident_scan_metadata", {}).get("status") or "unavailable")
    if incident == "complete":
        incident = "current"
    elif incident not in _COVERAGE_STATUSES:
        incident = "unavailable"
    envelopes = getattr(prepared, "evidence_envelopes", {}) or {}
    return {
        "mta": _envelope_status(envelopes.get("alerts")),
        "vehicles": _envelope_status(envelopes.get("subway_vehicles")),
        "incidents": incident,
        "events": _event_coverage(str(getattr(prepared, "event_evidence_status", "unscanned"))),
    }


def serialize_evidence_envelopes(envelopes: dict[str, Any]) -> dict[str, Any]:
    return {
        name: envelope.to_model_dict(empty=[])
        for name, envelope in (envelopes or {}).items()
        if hasattr(envelope, "to_model_dict")
    }


def merge_evidence_envelopes(groups: Iterable[dict[str, Any]]) -> dict[str, Any]:
    by_source: dict[str, list[dict[str, Any]]] = {}
    for envelopes in groups:
        for name, envelope in serialize_evidence_envelopes(envelopes).items():
            if isinstance(envelope, dict):
                by_source.setdefault(name, []).append(envelope)
    return {
        name: _MergedEnvelope(_merge_envelope_rows(rows))
        for name, rows in by_source.items()
    }


def merge_serialized_envelopes(groups: Iterable[dict[str, Any]]) -> dict[str, Any]:
    by_source: dict[str, list[dict[str, Any]]] = {}
    for envelopes in groups:
        for name, envelope in (envelopes or {}).items():
            if isinstance(envelope, dict):
                by_source.setdefault(name, []).append(envelope)
    return {name: _merge_envelope_rows(rows) for name, rows in by_source.items()}


def candidate_evidence_for_route(
    leg: PreparedLeg,
    *,
    route_index: int,
    aggregate_index: int,
    segment_index: int = 0,
) -> dict[str, Any]:
    """Project only facts associated with one local route into one aggregate."""

    route = leg.parsed_routes[route_index] if route_index < len(leg.parsed_routes) else []
    route_ids = _route_ids(route)
    local_candidate_id = f"candidate-{route_index}"
    aggregate_candidate_id = f"candidate-{aggregate_index}"
    alerts = [
        alert
        for alert in leg.relevant_alerts or []
        if _matches_candidate(alert, local_candidate_id, route_ids)
    ]
    incidents = [
        _remap_incident(incident, local_candidate_id, aggregate_candidate_id, segment_index)
        for incident in (leg.incidents or [])
        if _matches_candidate(incident, local_candidate_id, route_ids)
    ]
    impacts = [
        _remap_impact(impact, aggregate_index, segment_index)
        for impact in (leg.event_impacts or [])
        if _route_index(impact) == route_index
    ]
    return {
        "alerts": alerts,
        "incidents": incidents,
        "event_impacts": impacts,
        "event_failures": list(leg.event_failures or []),
        "event_evidence_status": str(leg.event_evidence_status or "unscanned"),
        "incident_scan_metadata": dict(leg.incident_scan_metadata or {}),
        "evidence_envelopes": serialize_evidence_envelopes(leg.evidence_envelopes),
        "crowd_search_metadata": dict(leg.crowd_search_metadata or {}),
        "collect_crowd_evidence": bool(leg.collect_crowd_evidence),
    }


def merge_candidate_evidence(groups: Iterable[dict[str, Any]]) -> dict[str, Any]:
    values = list(groups)
    metadata = [value.get("incident_scan_metadata") or {} for value in values]
    statuses = [str(value.get("event_evidence_status") or "unscanned") for value in values]
    return {
        "alerts": _merge_dicts(value.get("alerts") for value in values),
        "incidents": _merge_dicts(value.get("incidents") for value in values),
        "event_impacts": _merge_dicts(value.get("event_impacts") for value in values),
        "event_failures": _merge_strings(value.get("event_failures") for value in values),
        "event_evidence_status": merge_event_status(statuses),
        "incident_scan_metadata": merge_incident_metadata_values(metadata),
        "evidence_envelopes": merge_serialized_envelopes(
            value.get("evidence_envelopes") for value in values
        ),
        "crowd_search_metadata": _merge_dicts(
            value.get("crowd_search_metadata") for value in values
        )[0]
        if _merge_dicts(value.get("crowd_search_metadata") for value in values)
        else {},
        "collect_crowd_evidence": any(
            bool(value.get("collect_crowd_evidence")) for value in values
        ),
    }


def merge_coverage(legs: list[PreparedLeg]) -> dict[str, str]:
    result: dict[str, str] = {}
    for leg in legs:
        for key, value in coverage_for_prepared(leg).items():
            if value == "not_required":
                # Neutral, non-applicable coverage: fill an empty slot but
                # never override an applicable status or block a later one.
                result.setdefault(key, value)
                continue
            current = result.get(key)
            if current in {None, "not_required"}:
                result[key] = value
            elif _STATUS_ORDER.get(value, 4) > _STATUS_ORDER.get(current, 0):
                result[key] = value
    return result


def merge_incident_metadata(legs: list[PreparedLeg]) -> dict[str, Any]:
    return merge_incident_metadata_values(
        [leg.incident_scan_metadata for leg in legs]
    )


def merge_incident_metadata_values(metadata_values: Iterable[dict[str, Any]]) -> dict[str, Any]:
    metadata = [value for value in metadata_values if isinstance(value, dict)]
    statuses = [str(value.get("status") or "unavailable") for value in metadata]
    if statuses and all(value == "complete" for value in statuses):
        status = "complete"
    elif statuses and all(value in _PROVIDER_UNAVAILABLE for value in statuses):
        status = "unavailable"
    else:
        status = "partial"
    attempted = _merge_strings(
        (value.get("sources") or {}).get("attempted", [])
        for value in metadata
        if isinstance(value.get("sources"), dict)
    )
    completed = _merge_strings(
        (value.get("sources") or {}).get("completed", [])
        for value in metadata
        if isinstance(value.get("sources"), dict)
    )
    return {
        "status": status,
        "sources": {
            "legs": len(metadata),
            "attempted": attempted,
            "completed": completed,
            "leg_statuses": statuses,
        },
    }


def merge_event_status(legs_or_statuses: Iterable[PreparedLeg | str]) -> str:
    values = {
        str(value.event_evidence_status if isinstance(value, PreparedLeg) else value or "unscanned")
        for value in legs_or_statuses
    }
    if values == {"not_required"}:
        return "not_required"
    if "provider_unavailable" in values or "failed" in values or "timeout" in values:
        return "provider_unavailable"
    if len(values) == 1:
        return next(iter(values))
    return "partial"


def sum_timings(legs: list[PreparedLeg]) -> dict[str, float]:
    totals: dict[str, float] = {}
    for leg in legs:
        for key, value in leg.timings.items():
            if isinstance(value, (int, float)):
                totals[key] = totals.get(key, 0.0) + max(0.0, float(value))
    return totals


def _merge_envelope_rows(rows: list[dict[str, Any]]) -> dict[str, Any]:
    if not rows:
        return {"status": "unavailable", "payload": []}
    status = max(
        (str(row.get("status") or "unavailable") for row in rows),
        key=lambda value: _STATUS_ORDER.get(value, 4),
    )
    result: dict[str, Any] = {
        "source": next((row.get("source") for row in rows if row.get("source")), "unknown"),
        "observedAt": min(
            (str(row.get("observedAt")) for row in rows if row.get("observedAt")),
            default="",
        ),
        "status": status,
        "payload": _merge_dicts(row.get("payload") for row in rows),
    }
    valid_until = [str(row.get("validUntil")) for row in rows if row.get("validUntil")]
    if valid_until:
        result["validUntil"] = min(valid_until)
    return result


def _matches_candidate(item: dict[str, Any], candidate_id: str, route_ids: set[str]) -> bool:
    associations = _string_values(
        item.get("affected_candidate_route_ids") or item.get("candidate_route_ids")
    )
    if associations:
        return candidate_id in associations
    item_route_ids = {value.upper() for value in _string_values(item.get("route_ids"))}
    return not item_route_ids or bool(item_route_ids & route_ids)


def _remap_incident(
    incident: dict[str, Any],
    local_candidate_id: str,
    aggregate_candidate_id: str,
    segment_index: int,
) -> dict[str, Any]:
    result = dict(incident)
    for key in ("affected_candidate_route_ids", "candidate_route_ids"):
        values = _string_values(result.get(key))
        if values:
            result[key] = [aggregate_candidate_id]
    result["segment_index"] = segment_index
    result["source_candidate_route_id"] = local_candidate_id
    return result


def _remap_impact(impact: dict[str, Any], aggregate_index: int, segment_index: int) -> dict[str, Any]:
    return {
        **impact,
        "route_index": aggregate_index,
        "segment_index": segment_index,
        "source_route_index": _route_index(impact),
    }


def _route_index(value: dict[str, Any]) -> int:
    try:
        return int(value.get("route_index", -1))
    except (TypeError, ValueError):
        return -1


def _route_ids(route: list[dict]) -> set[str]:
    return {
        str(step.get("route_id") or step.get("train_line") or "").strip().upper()
        for step in route or []
        if str(step.get("route_id") or step.get("train_line") or "").strip()
    }


def _envelope_status(envelope: Any) -> str:
    if envelope is None:
        return "unscanned"
    value = getattr(envelope, "status_at", lambda: "unavailable")()
    return value if value in _COVERAGE_STATUSES else "unavailable"


def _event_coverage(status: str) -> str:
    if status == "not_required":
        return "not_required"
    if status in {"available", "no_relevant_events", "complete"}:
        return "current"
    if status in {"provider_unavailable", "failed", "timeout"}:
        return "unavailable"
    return "unscanned"


def _merge_dicts(groups: Iterable[Any]) -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = []
    for group in groups:
        if isinstance(group, dict):
            group = [group]
        for value in group or []:
            if isinstance(value, dict) and value not in merged:
                merged.append(value)
    return merged


def _merge_strings(groups: Iterable[Any]) -> list[str]:
    result: list[str] = []
    for group in groups:
        for value in group or []:
            text = str(value or "").strip()
            if text and text not in result:
                result.append(text)
    return result


def _string_values(value: object) -> list[str]:
    if not isinstance(value, (list, tuple, set)):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


__all__ = (
    "candidate_evidence_for_route",
    "coverage_for_prepared",
    "merge_candidate_evidence",
    "merge_evidence_envelopes",
    "merge_event_status",
    "merge_incident_metadata",
    "merge_incident_metadata_values",
    "merge_coverage",
    "merge_serialized_envelopes",
    "serialize_evidence_envelopes",
    "sum_timings",
)
