"""Evidence scoping and coverage helpers for prepared route options."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from app.services.trips import scoring
from app.services.trips.preparation.prepare import PreparedLeg

_COVERAGE_STATUSES = {"current", "partial", "stale", "unavailable", "unscanned"}
_STATUS_ORDER = {"current": 0, "partial": 1, "stale": 2, "unavailable": 3, "unscanned": 4}
_PROVIDER_UNAVAILABLE = {"failed", "timeout", "unavailable", "provider_unavailable"}


def _direction_matches(left: object, right: object) -> bool:
    """Match the bounded direction labels needed by route-scoped evidence."""

    def normalize(value: object) -> str:
        return " ".join(
            str(value or "")
            .replace("\u2013", "-")
            .replace("\u2014", "-")
            .replace("_", " ")
            .split()
        ).casefold()

    left_text = normalize(left)
    right_text = normalize(right)
    if not left_text or not right_text:
        return False
    if left_text == right_text:
        return True
    aliases = {
        "uptown": {"uptown", "northbound"},
        "downtown": {"downtown", "southbound"},
    }
    for values in aliases.values():
        if left_text in values and right_text in values:
            return True
    return False


class _MergedEnvelope:
    """Small serializable envelope preserving the worst source status."""

    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = payload

    def status_at(self, now: Any = None) -> str:
        del now
        return str(self._payload.get("status") or "unavailable")

    def current_payload(self, now: Any = None) -> Any:
        if self.status_at(now) != "current":
            return None
        return self._payload.get("payload")

    def to_model_dict(self, *, empty: Any, now: Any = None) -> dict[str, Any]:
        del now
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

    scoped = _route_scoped_live_facts(
        leg,
        route_index=route_index,
        aggregate_index=aggregate_index,
        segment_index=segment_index,
    )
    return {
        "alerts": scoped["alerts"],
        "incidents": scoped["incidents"],
        "event_impacts": scoped["event_impacts"],
        "event_failures": list(leg.event_failures or []),
        "event_evidence_status": str(leg.event_evidence_status or "unscanned"),
        "incident_scan_metadata": dict(leg.incident_scan_metadata or {}),
        "evidence_envelopes": serialize_evidence_envelopes(leg.evidence_envelopes),
        "crowd_search_metadata": dict(leg.crowd_search_metadata or {}),
        "collect_crowd_evidence": bool(leg.collect_crowd_evidence),
        "unconfirmed_material_claims": scoped["unconfirmed_material_claims"],
        "evidence_coverage": coverage_for_prepared(leg),
    }


def _route_scoped_live_facts(
    leg: PreparedLeg,
    *,
    route_index: int,
    aggregate_index: int,
    segment_index: int,
) -> dict[str, Any]:
    route = leg.parsed_routes[route_index] if route_index < len(leg.parsed_routes) else []
    route_ids = _route_ids(route)
    local_candidate_id = f"candidate-{route_index}"
    aggregate_candidate_id = f"candidate-{aggregate_index}"
    return {
        "alerts": _scoped_alerts(leg, local_candidate_id, route_ids),
        "incidents": _scoped_incidents(
            leg, local_candidate_id, aggregate_candidate_id, route_ids, segment_index
        ),
        "event_impacts": _scoped_impacts(leg, route_index, aggregate_index, segment_index),
        "unconfirmed_material_claims": vehicle_claims_for_route(
            route,
            trains=getattr(leg, "stalled", None),
            buses=getattr(leg, "stalled_buses", None),
        ),
    }


def _scoped_alerts(
    leg: PreparedLeg, local_candidate_id: str, route_ids: set[str]
) -> list[dict]:
    return [
        alert
        for alert in leg.relevant_alerts or []
        if _matches_candidate(alert, local_candidate_id, route_ids)
    ]


def _scoped_incidents(
    leg: PreparedLeg,
    local_candidate_id: str,
    aggregate_candidate_id: str,
    route_ids: set[str],
    segment_index: int,
) -> list[dict]:
    return [
        _remap_incident(
            incident, local_candidate_id, aggregate_candidate_id, segment_index
        )
        for incident in (leg.incidents or [])
        if _matches_candidate(incident, local_candidate_id, route_ids)
    ]


def _scoped_impacts(
    leg: PreparedLeg, route_index: int, aggregate_index: int, segment_index: int
) -> list[dict]:
    return [
        _remap_impact(impact, aggregate_index, segment_index)
        for impact in (leg.event_impacts or [])
        if _route_index(impact) == route_index
    ]


def merge_candidate_evidence(groups: Iterable[dict[str, Any]]) -> dict[str, Any]:
    values = list(groups)
    crowd = _merge_evidence_rows(values, "crowd_search_metadata")
    return {
        "alerts": _merge_evidence_rows(values, "alerts"),
        "incidents": _merge_evidence_rows(values, "incidents"),
        "event_impacts": _merge_evidence_rows(values, "event_impacts"),
        "event_failures": _merge_strings(value.get("event_failures") for value in values),
        "event_evidence_status": merge_event_status(
            str(value.get("event_evidence_status") or "unscanned") for value in values
        ),
        "incident_scan_metadata": merge_incident_metadata_values(
            value.get("incident_scan_metadata") or {} for value in values
        ),
        "evidence_envelopes": merge_serialized_envelopes(
            value.get("evidence_envelopes") for value in values
        ),
        "crowd_search_metadata": crowd[0] if crowd else {},
        "collect_crowd_evidence": any(
            bool(value.get("collect_crowd_evidence")) for value in values
        ),
        "unconfirmed_material_claims": _merge_evidence_rows(
            values, "unconfirmed_material_claims"
        )[:3],
        "evidence_coverage": _merge_coverage_maps(
            value.get("evidence_coverage") for value in values
        ),
    }


def _merge_evidence_rows(values: list[dict[str, Any]], key: str) -> list[dict[str, Any]]:
    return _merge_dicts(value.get(key) for value in values)


def vehicle_claims_for_route(
    route: list[dict],
    *,
    trains: list[dict] | None,
    buses: list[dict] | None,
) -> list[dict[str, str]]:
    """Project route-scoped vehicle signals without asserting a delay."""

    route_lines = _route_ids(route)
    claims: list[dict[str, str]] = []
    for mode, values in (("train", trains), ("bus", buses)):
        for value in values or []:
            claim = _route_scoped_vehicle_claim(
                value, mode=mode, route=route, route_lines=route_lines
            )
            if claim is None:
                continue
            claims.append(claim)
            if len(claims) >= 3:
                return claims
    return claims


def _route_scoped_vehicle_claim(
    value: object,
    *,
    mode: str,
    route: list[dict],
    route_lines: set[str],
) -> dict[str, str] | None:
    if not isinstance(value, dict):
        return None
    signal_route = str(value.get("route_id") or value.get("route") or "").strip().upper()
    if not signal_route or signal_route not in route_lines:
        return None
    if not _vehicle_signal_direction_matches(value, route, signal_route):
        return None
    if _vehicle_claim_is_layover(value):
        return None
    location_value = value.get("location") or value.get("stop_name")
    location = (
        location_value.strip() if isinstance(location_value, str) else "the route"
    )
    return {
        "mode": mode,
        "route": signal_route,
        "location": location[:96],
        "status": "possible_delay_unconfirmed",
    }


def _vehicle_claim_is_layover(claim: dict) -> bool:
    status_text = " ".join(
        str(claim.get(key) or "")
        for key in ("status", "progress_status", "ProgressStatus")
    ).casefold()
    return "layover" in status_text


def _vehicle_signal_direction_matches(
    signal: dict, route: list[dict], signal_route: str
) -> bool:
    requested = next(
        (
            signal.get(key)
            for key in ("direction_id", "direction", "direction_label", "headsign")
            if signal.get(key) not in (None, "")
        ),
        None,
    )
    if requested is None:
        return True
    route_values = [
        step.get(key)
        for step in route
        if str(step.get("route_id") or step.get("train_line") or "").strip().upper()
        == signal_route
        for key in ("direction_id", "direction", "direction_label", "headsign")
        if step.get(key) not in (None, "")
    ]
    return bool(route_values) and any(
        _direction_matches(requested, value) for value in route_values
    )


def _select_coverage(current: str | None, incoming: object) -> str | None:
    """Worse official-source status wins. not_required never confirms safety."""

    if incoming == "not_required":
        return incoming if current is None else current
    if incoming not in _COVERAGE_STATUSES:
        return current
    if current in {None, "not_required"} or _STATUS_ORDER.get(
        incoming, 4
    ) > _STATUS_ORDER.get(current, 0):
        return str(incoming)
    return current


def _merge_coverage_maps(groups: Iterable[Any]) -> dict[str, str]:
    merged: dict[str, str] = {}
    for group in groups:
        for key, value in (group or {}).items():
            selected = _select_coverage(merged.get(str(key)), value)
            if selected is not None:
                merged[str(key)] = selected
    return merged


def merge_coverage(legs: list[PreparedLeg]) -> dict[str, str]:
    return _merge_coverage_maps(coverage_for_prepared(leg) for leg in legs)


def merge_incident_metadata(legs: list[PreparedLeg]) -> dict[str, Any]:
    return merge_incident_metadata_values(
        [leg.incident_scan_metadata for leg in legs]
    )


def merge_incident_metadata_values(metadata_values: Iterable[dict[str, Any]]) -> dict[str, Any]:
    metadata = [value for value in metadata_values if isinstance(value, dict)]
    statuses = [str(value.get("status") or "unavailable") for value in metadata]
    return {
        "status": _incident_scan_status(statuses),
        "sources": {
            "legs": len(metadata),
            "attempted": _source_names(metadata, "attempted"),
            "completed": _source_names(metadata, "completed"),
            "leg_statuses": statuses,
        },
    }


def _incident_scan_status(statuses: list[str]) -> str:
    if statuses and all(value == "complete" for value in statuses):
        return "complete"
    if statuses and all(value in _PROVIDER_UNAVAILABLE for value in statuses):
        return "unavailable"
    return "partial"


def _source_names(metadata: list[dict[str, Any]], field: str) -> list[str]:
    return _merge_strings(
        (value.get("sources") or {}).get(field, [])
        for value in metadata
        if isinstance(value.get("sources"), dict)
    )


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
        line
        for step in route or []
        if (line := scoring._step_route_id(step))
    }


def _envelope_status(envelope: Any) -> str:
    if envelope is None:
        return "unscanned"
    value = getattr(envelope, "status_at", lambda: "unavailable")()
    return value if value in _COVERAGE_STATUSES else "unavailable"


def _event_coverage(status: str) -> str:
    if status == "not_required":
        return "not_required"
    if status == "partial":
        return "partial"
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
    "merge_coverage",
    "merge_event_status",
    "merge_evidence_envelopes",
    "merge_incident_metadata",
    "merge_incident_metadata_values",
    "merge_serialized_envelopes",
    "serialize_evidence_envelopes",
    "sum_timings",
    "vehicle_claims_for_route",
)
