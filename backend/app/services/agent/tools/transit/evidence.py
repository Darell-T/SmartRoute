"""Compact, server-owned evidence produced by the existing transit tools."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from app.services.agent.tools.transit import evidence_binding as _binding
from app.services.agent.tools.transit.direction import (
    DirectionResolution,
    direction_matches,
    normalize_direction,
    resolve_direction,
)
from app.services.agent.tools.transit.evidence_matching import (
    arrival_coverage as _arrival_coverage,
)
from app.services.agent.tools.transit.evidence_matching import (
    concern_match as _concern_match,
)
from app.services.agent.tools.transit.evidence_matching import (
    concerns as _concerns,
)
from app.services.agent.tools.transit.evidence_matching import (
    confirmed as _confirmed,
)
from app.services.agent.tools.transit.evidence_matching import (
    coverage as _coverage,
)
from app.services.agent.tools.transit.evidence_matching import (
    normalized_route_ids as _routes,
)
from app.services.agent.tools.transit.evidence_matching import (
    normalized_text as _text,
)
from app.services.agent.tools.transit.evidence_matching import (
    route_match as _route_match,
)
from app.services.agent.tools.transit.evidence_matching import (
    unique_evidence_values as _unique,
)
from app.services.agent.tools.transit.evidence_projection import (
    operation_facts as _operation_facts,
)
from app.services.agent.tools.transit.evidence_projection import (
    row_direction as _row_direction,
)
from app.services.agent.tools.transit.evidence_projection import (
    safe_accessibility as _safe_accessibility,
)
from app.services.agent.tools.transit.evidence_projection import (
    safe_alert as _safe_alert,
)
from app.services.agent.tools.transit.evidence_projection import (
    safe_incident as _safe_incident,
)
from app.services.agent.tools.transit.evidence_projection import (
    safe_result as _safe_result,
)
from app.services.agent.tools.transit.evidence_projection import (
    safe_stop as _safe_stop,
)
from app.services.agent.tools.transit.evidence_projection import (
    safe_unconfirmed_signal,
)
from app.services.agent.tools.transit.evidence_store import (
    TransitEvidenceSet,
    new_evidence_set_id,
    store_evidence_set,
)
from app.services.agent.tools.transit.evidence_store import (
    load_evidence_set as load_evidence_set,
)

bind_accessibility_target = _binding.bind_accessibility_target
accessibility_result_matches = _binding.accessibility_result_matches


def build_evidence_set(
    *,
    session_id: str,
    operation: str,
    route_ids: object = (),
    direction: object = None,
    concerns: object = (),
    result: object = None,
    evidence_set_id: str | None = None,
    direction_resolution: DirectionResolution | None = None,
    turn_id: str | None = None,
) -> tuple[str, dict[str, Any]]:
    """Persist a bounded view of one existing leaf-tool result."""
    requested_set_id = str(evidence_set_id or "").strip()
    if requested_set_id:
        existing = load_evidence_set(requested_set_id, session_id=str(session_id or ""))
        if existing is not None:
            public_existing = dict(existing)
            for key in ("session_id", "created_at", "expires_at"):
                public_existing.pop(key, None)
            return requested_set_id, public_existing

    row = result if isinstance(result, dict) else {}
    if isinstance(row.get("results"), list):
        rows = [item for item in row["results"] if isinstance(item, dict)]
    else:
        rows = [row]
    operation_name = _text(operation) or "transit"
    routes = _routes(route_ids)
    resolution = direction_resolution
    if resolution is None and direction not in (None, ""):
        resolution = resolve_direction(direction, _direction_contexts(rows))
    requested_direction = (
        resolution.resolved
        if resolution and resolution.resolved
        else normalize_direction(direction)
    )
    concern_values = _concerns(concerns)
    service: dict[str, Any] = {}
    arrivals: dict[str, list[dict[str, Any]]] = {}
    alerts: list[dict[str, Any]] = []
    incidents: list[dict[str, Any]] = []
    signals: list[dict[str, Any]] = []
    coverage: dict[str, str] = {}
    freshness: dict[str, Any] = {}
    observed_at: dict[str, str] = {}
    freshness_by_source: dict[str, Any] = {}
    unknowns: list[str] = []
    accessibility: dict[str, Any] | None = None
    safe_results: list[dict[str, Any]] = []
    for current in rows:
        safe_results.append(_safe_result(current, requested_direction))
        _ingest_row_observations(
            current,
            operation_name=operation_name,
            freshness=freshness,
            observed_at=observed_at,
        )
        _ingest_operation_row(
            current,
            operation_name=operation_name,
            routes=routes,
            concern_values=concern_values,
            coverage=coverage,
            service=service,
            alerts=alerts,
            incidents=incidents,
            signals=signals,
            arrivals=arrivals,
            requested_direction=requested_direction,
        )
        if operation_name == "accessibility":
            coverage["accessibility"] = _coverage(current)
            accessibility = _safe_accessibility(current)
        _ingest_row_signals(
            current,
            concern_values=concern_values,
            routes=routes,
            requested_direction=requested_direction,
            signals=signals,
        )
    return _store_public_evidence(
        session_id=session_id,
        requested_set_id=requested_set_id,
        operation_name=operation_name,
        row=row,
        routes=routes,
        concern_values=concern_values,
        resolution=resolution,
        requested_direction=requested_direction,
        service=service,
        arrivals=arrivals,
        alerts=alerts,
        incidents=incidents,
        signals=signals,
        coverage=coverage,
        freshness=freshness,
        observed_at=observed_at,
        freshness_by_source=freshness_by_source,
        unknowns=unknowns,
        accessibility=accessibility,
        safe_results=safe_results,
        turn_id=turn_id,
    )


def _store_public_evidence(
    *,
    session_id: str,
    requested_set_id: str,
    operation_name: str,
    row: dict[str, Any],
    routes: list[str],
    concern_values: list[str],
    resolution: DirectionResolution | None,
    requested_direction: str | None,
    service: dict[str, Any],
    arrivals: dict[str, list[dict[str, Any]]],
    alerts: list[dict[str, Any]],
    incidents: list[dict[str, Any]],
    signals: list[dict[str, Any]],
    coverage: dict[str, str],
    freshness: dict[str, Any],
    observed_at: dict[str, str],
    freshness_by_source: dict[str, Any],
    unknowns: list[str],
    accessibility: dict[str, Any] | None,
    safe_results: list[dict[str, Any]],
    turn_id: str | None,
) -> tuple[str, dict[str, Any]]:
    stalled_vehicles = [
        signal for signal in signals if _is_stalled_vehicle_signal(signal)
    ]
    _apply_evidence_freshness(row, coverage, freshness, observed_at, freshness_by_source)
    _ensure_operation_coverage(operation_name, coverage)
    available: set[str] = set(service) | set(arrivals)
    resolved, resolved_from_evidence = _resolved_evidence_direction(
        resolution, requested_direction, available, alerts
    )
    _append_evidence_unknowns(
        unknowns,
        requested_direction=requested_direction,
        resolved=resolved,
        coverage=coverage,
        signals=signals,
        alerts=alerts,
        incidents=incidents,
    )
    if coverage.get("alerts") != "current":
        _remove_false_all_clear(service)
    record = _transit_evidence_record(
        session_id=session_id,
        requested_set_id=requested_set_id,
        operation_name=operation_name,
        row=row,
        routes=routes,
        concern_values=concern_values,
        resolution=resolution,
        requested_direction=requested_direction,
        resolved=resolved,
        resolved_from_evidence=resolved_from_evidence,
        available=available,
        service=service,
        arrivals=arrivals,
        alerts=alerts,
        incidents=incidents,
        signals=signals,
        coverage=coverage,
        freshness=freshness,
        observed_at=observed_at,
        freshness_by_source=freshness_by_source,
        unknowns=unknowns,
        accessibility=accessibility,
        safe_results=safe_results,
        stalled_vehicles=stalled_vehicles,
        turn_id=turn_id,
    )
    return _persist_public_evidence(session_id, record)


def _transit_evidence_record(
    *,
    session_id: str,
    requested_set_id: str,
    operation_name: str,
    row: dict[str, Any],
    routes: list[str],
    concern_values: list[str],
    resolution: DirectionResolution | None,
    requested_direction: str | None,
    resolved: str | None,
    resolved_from_evidence: bool,
    available: set[str],
    service: dict[str, Any],
    arrivals: dict[str, list[dict[str, Any]]],
    alerts: list[dict[str, Any]],
    incidents: list[dict[str, Any]],
    signals: list[dict[str, Any]],
    coverage: dict[str, str],
    freshness: dict[str, Any],
    observed_at: dict[str, str],
    freshness_by_source: dict[str, Any],
    unknowns: list[str],
    accessibility: dict[str, Any] | None,
    safe_results: list[dict[str, Any]],
    stalled_vehicles: list[dict[str, Any]],
    turn_id: str | None,
) -> TransitEvidenceSet:
    return TransitEvidenceSet(
        evidence_set_id=requested_set_id or new_evidence_set_id(),
        session_id=str(session_id or ""),
        requested_operation=operation_name,
        concerns=tuple(concern_values),
        checked_routes=tuple(routes),
        direction_scope=_evidence_direction_scope(
            resolution, requested_direction, resolved, resolved_from_evidence, available
        ),
        service_status_by_direction=service,
        arrivals_by_direction=arrivals,
        accessibility=accessibility,
        confirmed_matching_alerts=tuple(alerts[:12]),
        unconfirmed_signals=tuple(signals[:12]),
        source_coverage=coverage,
        freshness=freshness,
        unknowns=tuple(_unique(unknowns)),
        operation_facts=_operation_facts(operation_name, row),
        results=tuple(safe_results),
        incidents=tuple(incidents[:12]),
        stalled_vehicles=tuple(stalled_vehicles[:12]),
        observed_at=observed_at,
        freshness_by_source=freshness_by_source,
        turn_id=str(turn_id or ""),
    )


def _evidence_direction_scope(
    resolution: DirectionResolution | None,
    requested_direction: str | None,
    resolved: str | None,
    resolved_from_evidence: bool,
    available: set[str],
) -> dict[str, Any]:
    requested = (
        resolution.requested
        if resolution and resolution.requested
        else requested_direction
    )
    authoritative = bool(
        (resolution.authoritative if resolution else False) or resolved_from_evidence
    )
    return {
        "requested": requested,
        "resolved": resolved,
        "authoritative": authoritative,
        "available": sorted(available - {"all", "unknown"}),
    }


def _persist_public_evidence(
    session_id: str, record: TransitEvidenceSet
) -> tuple[str, dict[str, Any]]:
    payload = record.to_dict()
    stored_id = store_evidence_set(session_id=session_id, evidence=payload)
    if stored_id != record.evidence_set_id:
        stored = load_evidence_set(stored_id, session_id=str(session_id or ""))
        if stored is not None:
            public_stored = dict(stored)
            for key in ("session_id", "created_at", "expires_at"):
                public_stored.pop(key, None)
            return stored_id, public_stored
    public_payload = dict(payload)
    public_payload.pop("session_id", None)
    return record.evidence_set_id, public_payload


def _ensure_operation_coverage(operation_name: str, coverage: dict[str, str]) -> None:
    if operation_name == "service_status":
        coverage.setdefault("gtfs_rt", "unknown")
        coverage.setdefault("incidents", "unknown")
    if operation_name not in {"service_status", "arrivals", "accessibility"}:
        return
    if coverage:
        return
    coverage["alerts" if operation_name == "service_status" else operation_name] = (
        "unknown"
    )


def _apply_evidence_freshness(
    row: dict[str, Any],
    coverage: dict[str, str],
    freshness: dict[str, Any],
    observed_at: dict[str, str],
    freshness_by_source: dict[str, Any],
) -> None:
    for key, status in coverage.items():
        freshness_by_source[key] = {
            "status": status,
            **({"observed_at": observed_at[key]} if key in observed_at else {}),
        }
    freshness["by_source"] = {
        key: dict(value) for key, value in freshness_by_source.items()
    }
    origin = _text(row.get("evidence_origin"))
    if origin:
        freshness["origin"] = origin
    marker = row.get("decision_evidence_continuity")
    if isinstance(marker, dict) and marker.get("comparable") is True:
        freshness["continuity"] = {
            "comparable": True,
            "changed": marker.get("changed") is True,
        }


def _resolved_evidence_direction(
    resolution: DirectionResolution | None,
    requested_direction: str | None,
    available: set[str],
    alerts: list[dict[str, Any]],
) -> tuple[str | None, bool]:
    resolved_from_evidence = bool(
        requested_direction
        and (
            requested_direction in available
            or any(item.get("direction_scope") == "both_directions" for item in alerts)
        )
    )
    resolved = (
        resolution.resolved
        if resolution and resolution.authoritative and resolution.resolved
        else requested_direction
        if resolved_from_evidence
        else None
    )
    return resolved, resolved_from_evidence


def _append_evidence_unknowns(
    unknowns: list[str],
    *,
    requested_direction: str | None,
    resolved: str | None,
    coverage: dict[str, str],
    signals: list[dict[str, Any]],
    alerts: list[dict[str, Any]],
    incidents: list[dict[str, Any]],
) -> None:
    if requested_direction and not resolved:
        unknowns.append(
            f"{requested_direction} direction was not resolved by the checked source"
        )
    if any(
        value in {"partial", "unavailable", "unknown", "stale", "unscanned"}
        for value in coverage.values()
    ):
        unknowns.append("the checked source has partial or missing coverage")
    if signals:
        unknowns.append("a possible delay signal is unconfirmed")
    if requested_direction and (
        _has_unscoped_findings(alerts, incidents) or _has_unscoped_signals(signals)
    ):
        unknowns.append(
            "a matching alert, incident, or vehicle signal did not specify the requested direction"
        )


def _ingest_row_observations(
    current: dict[str, Any],
    *,
    operation_name: str,
    freshness: dict[str, Any],
    observed_at: dict[str, str],
) -> None:
    observed = _observed_value(current)
    source_key = _source_key(operation_name, current)
    if observed:
        observed_text = _text(observed)
        if observed_text:
            freshness_key = (
                "alerts" if operation_name == "service_status" else operation_name
            )
            freshness[freshness_key] = observed_text
            observed_at[source_key] = observed_text
            observed_at[freshness_key] = observed_text
    observed_at.update(
        {
            source: source_observed
            for source, source_observed in _source_observations(current).items()
            if source_observed
        }
    )


def _ingest_operation_row(
    current: dict[str, Any],
    *,
    operation_name: str,
    routes: list[str],
    concern_values: list[str],
    coverage: dict[str, str],
    service: dict[str, Any],
    alerts: list,
    incidents: list,
    signals: list,
    arrivals: dict[str, list[dict[str, Any]]],
    requested_direction: str | None,
) -> None:
    if operation_name == "service_status":
        coverage["alerts"] = _coverage(current)
        if current.get("gtfs_rt_coverage"):
            coverage["gtfs_rt"] = str(current["gtfs_rt_coverage"])
        if current.get("bustime_coverage"):
            coverage["bustime"] = str(current["bustime_coverage"])
        if current.get("incident_coverage"):
            coverage["incidents"] = _coverage(
                {"freshness": current["incident_coverage"]}
            )
        _service_row(
            current,
            routes,
            concern_values,
            service,
            alerts,
            incidents,
            signals,
            requested_direction,
        )
        return
    if operation_name == "arrivals":
        coverage["arrivals"] = _arrival_coverage(current)
        _arrival_row(current, arrivals, requested_direction)


def _ingest_row_signals(
    current: dict[str, Any],
    *,
    concern_values: list[str],
    routes: list[str],
    requested_direction: str | None,
    signals: list,
) -> None:
    for signal in current.get("unconfirmed_signals") or []:
        if not isinstance(signal, dict):
            continue
        if concern_values and not _concern_match(signal, concern_values):
            continue
        safe_signal = safe_unconfirmed_signal(signal)
        if _signal_in_scope(safe_signal, routes, requested_direction):
            signals.append(safe_signal)


def _signal_in_scope(
    safe_signal: dict[str, Any],
    routes: list[str],
    requested_direction: str | None,
) -> bool:
    signal_route = str(safe_signal.get("route_id") or "").upper()
    if routes and signal_route not in routes:
        return False
    signal_direction = safe_signal.get("direction")
    return not (
        requested_direction
        and signal_direction
        and not direction_matches(signal_direction, requested_direction)
    )


def _direction_contexts(rows: list[dict[str, Any]]) -> list[dict[str, object]]:
    contexts: list[dict[str, object]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        contexts.append(row)
        groups = row.get("directions")
        if not isinstance(groups, list):
            continue
        contexts.extend(
            {
                **group,
                "direction": group.get("id"),
                "headsign": group.get("label"),
            }
            for group in groups[:8]
            if isinstance(group, dict)
        )
    return contexts


def _service_row(
    row: dict[str, Any],
    routes: list[str],
    concerns: list[str],
    service: dict,
    alerts: list,
    incidents: list,
    signals: list,
    requested_direction: str | None,
) -> None:
    source = _text(row.get("source"))
    _ingest_service_alerts(
        row, source, routes, concerns, service, alerts, signals, requested_direction
    )
    _ingest_service_incidents(
        row, routes, concerns, service, incidents, signals, requested_direction
    )
    if not service:
        status = _text(row.get("status")).casefold()
        service["all"] = {
            "status": "no_matching_alerts"
            if status == "no_active_alerts" and not requested_direction
            else "unknown",
            "alerts": [],
        }


def _ingest_service_alerts(
    row: dict[str, Any],
    source: str,
    routes: list[str],
    concerns: list[str],
    service: dict,
    alerts: list,
    signals: list,
    requested_direction: str | None,
) -> None:
    raw_alerts = row.get("alerts") if isinstance(row.get("alerts"), list) else []
    for raw in raw_alerts:
        if not _alert_row_eligible(raw, routes, concerns, requested_direction):
            continue
        direction = _row_direction(raw)
        item = _safe_alert(raw)
        if direction:
            item["direction"] = direction
        if _confirmed(raw, source):
            _record_confirmed_alert(service, alerts, item, direction)
            continue
        _append_unconfirmed_alert(signals, item, direction, requested_direction)


def _append_unconfirmed_alert(
    signals: list,
    item: dict,
    direction: str | None,
    requested_direction: str | None,
) -> None:
    if (
        requested_direction
        and direction
        and not direction_matches(direction, requested_direction)
    ):
        return
    signals.append({**item, "kind": "unconfirmed_alert", "confirmed": False})


def _alert_row_eligible(
    raw: object,
    routes: list[str],
    concerns: list[str],
    requested_direction: str | None,
) -> bool:
    if (
        not isinstance(raw, dict)
        or not _route_match(raw, routes)
        or not _concern_match(raw, concerns)
    ):
        return False
    direction = _row_direction(raw)
    return not (
        requested_direction
        and direction
        and direction != requested_direction
        and raw.get("direction_scope") != "both_directions"
    )


def _record_confirmed_alert(
    service: dict, alerts: list, item: dict, direction: str | None
) -> None:
    alerts.append(item)
    bucket = service.setdefault(direction or "all", {"status": "unknown", "alerts": []})
    bucket["status"] = "affected"
    bucket["alerts"].append(item)


def _ingest_service_incidents(
    row: dict[str, Any],
    routes: list[str],
    concerns: list[str],
    service: dict,
    incidents: list,
    signals: list,
    requested_direction: str | None,
) -> None:
    raw_incidents = (
        row.get("incidents") if isinstance(row.get("incidents"), list) else []
    )
    for raw in raw_incidents:
        if not _incident_row_eligible(raw, routes, concerns, requested_direction):
            continue
        direction = _row_direction(raw)
        item = _safe_incident(raw)
        state = _text(raw.get("state") or raw.get("confirmation")).casefold()
        incident_item = {**item, "confirmed": state == "confirmed"}
        incidents.append(incident_item)
        if state == "confirmed":
            bucket = service.setdefault(
                direction or "all", {"status": "unknown", "alerts": []}
            )
            bucket["status"] = "affected"
            bucket.setdefault("incidents", []).append(item)
            continue
        signals.append({**item, "kind": "unconfirmed_incident", "confirmed": False})


def _incident_row_eligible(
    raw: object,
    routes: list[str],
    concerns: list[str],
    requested_direction: str | None,
) -> bool:
    if (
        not isinstance(raw, dict)
        or not _incident_route_match(raw, routes)
        or not _concern_match(raw, concerns)
    ):
        return False
    direction = _row_direction(raw)
    return not (
        requested_direction
        and direction
        and not direction_matches(direction, requested_direction)
    )


def _source_key(operation: str, row: dict[str, Any]) -> str:
    """Return a stable passenger-domain source key for freshness metadata."""

    if operation == "service_status":
        return "alerts"
    if operation == "arrivals":
        envelope = row.get("evidence") if isinstance(row.get("evidence"), dict) else {}
        source = _text(envelope.get("source"))
        return source or "arrivals"
    return operation


def _source_observations(row: dict[str, Any]) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw in (row.get("observed_at_by_source"), row.get("observed_at")):
        if not isinstance(raw, dict):
            continue
        for source, observed in raw.items():
            source_name = _text(source)
            observed_text = _text(observed)
            if source_name and observed_text:
                values[source_name] = observed_text
    for source, key in (
        ("gtfs_rt", "gtfs_rt_observed_at"),
        ("bustime", "bustime_observed_at"),
        ("incidents", "incident_observed_at"),
    ):
        observed_text = _text(row.get(key))
        if observed_text:
            values[source] = observed_text
    if values:
        return values
    # Accessibility feeds do not publish a provider timestamp.  A capture
    # timestamp is still useful to make the evidence boundary explicit; the
    # operation's coverage remains authoritative for whether it is usable.
    return (
        {"accessibility": datetime.now(UTC).isoformat()}
        if "station_matched" in row
        else {}
    )


def _observed_value(row: dict[str, Any]) -> object:
    observed = row.get("observed_at")
    if isinstance(observed, dict):
        for key in ("alerts", "arrivals", "accessibility", "observed_at"):
            if observed.get(key):
                return observed[key]
        return None
    return observed or row.get("updated_at")


def _is_stalled_vehicle_signal(signal: dict[str, Any]) -> bool:
    kind = _text(signal.get("kind")).casefold().replace("-", "_")
    mode = _text(signal.get("mode")).casefold()
    return "stalled" in kind or mode in {"bus", "subway", "train"}


def _has_unscoped_findings(
    alerts: list[dict[str, Any]], incidents: list[dict[str, Any]]
) -> bool:
    return any(
        not _text(item.get("direction"))
        and item.get("direction_scope") != "both_directions"
        for item in (*alerts, *incidents)
        if isinstance(item, dict)
    )


def _has_unscoped_signals(signals: list[dict[str, Any]]) -> bool:
    return any(
        not _text(item.get("direction")) for item in signals if isinstance(item, dict)
    )


def _remove_false_all_clear(service: dict[str, Any]) -> None:
    """Do not retain a no-alert status when the alert source is incomplete."""

    for bucket in service.values():
        if isinstance(bucket, dict) and bucket.get("status") == "no_matching_alerts":
            bucket["status"] = "unknown"


def _incident_route_match(row: dict[str, Any], routes: list[str]) -> bool:
    if not routes:
        return True
    incident_routes = _routes(row.get("affected_route_ids") or row.get("route_ids"))
    return bool(set(incident_routes) & set(routes))


def _arrival_row(
    row: dict[str, Any],
    arrivals: dict[str, list[dict[str, Any]]],
    requested_direction: str | None,
) -> None:
    for group in (
        row.get("directions") if isinstance(row.get("directions"), list) else []
    ):
        if not isinstance(group, dict):
            continue
        direction = (
            normalize_direction(group.get("label"))
            or normalize_direction(group.get("id"))
            or "unknown"
        )
        if requested_direction and direction != requested_direction:
            continue
        group_arrivals = []
        for item in group.get("arrivals") or []:
            if not isinstance(item, dict):
                continue
            group_arrivals.append(
                {
                    "route_id": _text(row.get("route_id")).upper(),
                    "stop": _safe_stop(row.get("stop")),
                    "direction": direction,
                    "direction_label": _text(group.get("label")),
                    "expected_at": _text(item.get("expected_at")),
                    "minutes": item.get("minutes"),
                    "realtime": item.get("realtime"),
                }
            )
        arrivals.setdefault(direction, []).extend(group_arrivals)
