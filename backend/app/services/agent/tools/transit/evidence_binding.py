"""Bind provider transit evidence to entities in the accepted itinerary."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from app.services import evidence as evidence_service
from app.services.agent import candidate_store, trip_state
from app.services.mta.alerts import project_service_alert

_STATION_ENTITY_TYPES = frozenset(
    {"SUBWAY_STATION", "AIRTRAIN_STATION", "RAIL_STATION"}
)
_MODE_ENTITY_TYPES = {
    "SUBWAY": "SUBWAY_STATION",
    "AIRTRAIN": "AIRTRAIN_STATION",
    "RAIL": "RAIL_STATION",
    "TRAIN": "RAIL_STATION",
}
_NAME_TOKEN_MAP = {
    "st": "street",
    "av": "avenue",
    "ave": "avenue",
    "blvd": "boulevard",
    "sq": "square",
}
_OFFICIAL_ALERT_SOURCE = "mta_service_alerts"
_ALERT_LIMIT = 12
_ROW_LIMIT = 12


def _text(value: object) -> str:
    return " ".join(str(value or "").split()).strip()


def bind_accessibility_target(
    station: object,
    session: object,
    requested_route_ids: object = (),
) -> tuple[dict[str, Any] | None, str | None]:
    """Bind an accessibility request to one accepted itinerary entity."""

    active = session.get("active_trip") if isinstance(session, Mapping) else None
    if not isinstance(active, Mapping):
        return None, None
    itinerary = active.get("canonical_itinerary") or active.get("itinerary")
    if not isinstance(itinerary, Mapping) or not isinstance(itinerary.get("legs"), list):
        return None, None

    requested = _route_values(requested_route_ids)
    routes = set(_route_values(active.get("lines")))
    entities: list[dict[str, str]] = []
    for raw_leg in itinerary.get("legs") or []:
        if not isinstance(raw_leg, Mapping):
            continue
        mode = _text(raw_leg.get("mode") or raw_leg.get("type")).upper()
        route_id = _text(raw_leg.get("service_id") or raw_leg.get("route_id")).upper()
        if route_id:
            routes.add(route_id)
        default_type = "BUS_STOP" if mode == "BUS" else _MODE_ENTITY_TYPES.get(mode, "")
        for side in ("board", "alight"):
            name = raw_leg.get(side) or raw_leg.get(
                "departure_stop" if side == "board" else "arrival_stop"
            )
            if name in (None, ""):
                continue
            entities.append(
                _entity(
                    name=name,
                    entity_id=(
                        raw_leg.get(f"{side}_stop_id")
                        or raw_leg.get(f"{side}_parent_station")
                    ),
                    entity_type=raw_leg.get(f"{side}_entity_type") or default_type,
                    mode=mode,
                    route_id=route_id,
                )
            )
        for raw_stop in raw_leg.get("stops") or []:
            if not isinstance(raw_stop, Mapping):
                continue
            name = raw_stop.get("name") or raw_stop.get("stop_name")
            if name in (None, ""):
                continue
            entities.append(
                _entity(
                    name=name,
                    entity_id=(
                        raw_stop.get("id")
                        or raw_stop.get("stop_id")
                        or raw_stop.get("parent_station")
                    ),
                    entity_type=raw_stop.get("entity_type") or default_type,
                    mode=mode,
                    route_id=route_id,
                )
            )

    matching_routes = requested.intersection(routes) if requested else routes
    if requested and routes and not matching_routes:
        return None, "accessibility target is outside the accepted route"
    query = _text(station)
    if not query:
        return None, "accessibility requires a station"
    scoped_entities = [
        item
        for item in entities
        if not requested or item["route_id"] in matching_routes
    ]
    matches = _matching_entities(query, scoped_entities)
    if not matches:
        route_query = query.upper()
        if route_query in routes and any(
            item["route_id"] == route_query and item["entity_type"] == "BUS_STOP"
            for item in scoped_entities
        ):
            return None, "accessibility target is a bus stop, not a subway station"
        return None, "accessibility target is not a station on the accepted itinerary"

    station_matches = [
        item for item in matches if item["entity_type"] in _STATION_ENTITY_TYPES
    ]
    if not station_matches:
        if any(item["entity_type"] == "BUS_STOP" for item in matches):
            return None, "accessibility target is a bus stop, not a subway station"
        return None, "accessibility target is not a supported station entity"
    selected = station_matches[0]
    return {
        "bound": True,
        "card_id": _text(active.get("card_id")) or None,
        "route_ids": sorted(
            route for route in (matching_routes or {selected["route_id"]}) if route
        ),
        "mode": selected["mode"].lower(),
        "station": selected["name"],
        "station_id": selected["id"] or None,
        "entity_type": selected["entity_type"],
    }, None


def accessibility_result_matches(result: object, binding: Mapping[str, Any]) -> bool:
    """Ensure provider-normalized station text remains on the bound entity."""

    row = result if isinstance(result, Mapping) else {}
    return _name_matches(row.get("station_matched"), binding.get("station"))


def _entity(
    *, name: object, entity_id: object, entity_type: object, mode: str, route_id: str
) -> dict[str, str]:
    return {
        "name": _text(name),
        "id": _text(entity_id),
        "entity_type": _text(entity_type).upper(),
        "mode": mode,
        "route_id": route_id,
    }


def _matching_entities(
    query: str, entities: list[dict[str, str]]
) -> list[dict[str, str]]:
    exact_ids = [item for item in entities if query.casefold() == item["id"].casefold()]
    if exact_ids:
        return exact_ids
    return [item for item in entities if _name_matches(query, item["name"])]


def _name_matches(left: object, right: object) -> bool:
    left_norm = _normalized_name(left)
    right_norm = _normalized_name(right)
    return bool(left_norm and right_norm and left_norm == right_norm)


def _normalized_name(value: object) -> str:
    tokens = _text(value).casefold().replace("-", " ").replace("/", " ").split()
    return " ".join(_NAME_TOKEN_MAP.get(token, token) for token in tokens)


def _route_values(value: object) -> set[str]:
    values = value if isinstance(value, (list, tuple, set)) else []
    return {_text(item).upper() for item in values if _text(item)}


def decision_evidence_for_status(
    route_ids: object,
    session: object,
    session_id: object,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Return current candidate evidence, or stale IDs for one refresh.

    Candidate rows are matched by their stored index and route digest.  This
    intentionally scans alternatives as well as the selected entry: a status
    follow-up can ask about a route from a non-selected candidate.
    """

    requested = sorted(_route_values(route_ids))
    empty = {
        "reused": False,
        "previous_alert_ids": None,
        "comparable": False,
        "route_ids": requested,
    }
    owner = _text(session_id)
    if not requested or not owner or not isinstance(session, Mapping):
        return empty
    state = trip_state.get_trip_state(dict(session))
    set_id = _text(state.get("active_candidate_set_id"))
    if not set_id:
        return empty
    record = candidate_store.load_candidate_set(set_id, session_id=owner)
    if not isinstance(record, dict):
        return empty
    candidates = record.get("candidates")
    evidence_rows = record.get("candidate_evidence")
    if not isinstance(candidates, list) or not isinstance(evidence_rows, list):
        return empty
    now_utc = (now or datetime.now(UTC)).astimezone(UTC)
    root_envelope = (record.get("evidence_envelopes") or {}).get("alerts")
    matches: list[dict[str, Any]] = []
    for index, candidate in enumerate(candidates[:8]):
        if not isinstance(candidate, Mapping) or index >= len(evidence_rows):
            continue
        digest = candidate.get("digest")
        candidate_routes = _route_values(

                digest.get("transit_lines") or digest.get("route_ids")
                if isinstance(digest, Mapping)
                else ()

        )
        if not candidate_routes.intersection(requested):
            continue
        evidence = evidence_rows[index]
        if not isinstance(evidence, Mapping):
            continue
        alert_rows = _official_alert_rows(evidence.get("alerts"), requested)
        if alert_rows is None:
            continue
        envelopes = evidence.get("evidence_envelopes")
        envelope = (
            envelopes.get("alerts")
            if isinstance(envelopes, Mapping) and "alerts" in envelopes
            else root_envelope
        )
        envelope_info = _alert_envelope(envelope, requested, now_utc)
        if envelope_info is None:
            matches.append({"rows": alert_rows, "envelope": None})
            continue
        candidate_ids = _alert_ids(alert_rows)
        if not candidate_ids or not candidate_ids.issubset(envelope_info["ids"]):
            matches.append({"rows": alert_rows, "envelope": None})
            continue
        matches.append(
            {
                "rows": alert_rows,
                "envelope": envelope_info,
                "evidence": evidence,
            }
        )
    if not matches:
        return empty
    comparable = all(item.get("envelope") is not None for item in matches)
    previous_ids = sorted(
        {
            alert_id
            for item in matches
            for alert_id in _alert_ids(item.get("rows") or [])
        }
    )
    if not comparable or not previous_ids:
        return empty
    if all(item["envelope"]["current"] for item in matches):
        return {
            **empty,
            "reused": True,
            "previous_alert_ids": previous_ids,
            "comparable": True,
            "data": _decision_status_data(matches, requested),
        }
    return {
        **empty,
        "previous_alert_ids": previous_ids,
        "comparable": True,
    }


def decision_alert_continuity(
    binding: Mapping[str, Any] | None,
    alerts: object,
) -> dict[str, Any] | None:
    """Compare exact official IDs after a stale candidate-evidence refresh."""

    if not isinstance(binding, Mapping) or binding.get("comparable") is not True:
        return None
    previous = binding.get("previous_alert_ids")
    if not isinstance(previous, list) or not all(_text(item) for item in previous):
        return None
    current = _official_alert_ids(alerts, binding.get("route_ids") or ())
    if current is None:
        return None
    return {
        "comparable": True,
        "changed": sorted(_text(item) for item in previous) != sorted(current),
    }


def _official_alert_rows(value: object, routes: object) -> list[dict[str, Any]] | None:
    if not isinstance(value, list):
        return None
    wanted = _route_values(routes)
    rows: list[dict[str, Any]] = []
    for raw in value:
        projected = project_service_alert(raw)
        if (
            not isinstance(projected, dict)
            or projected.get("source") != _OFFICIAL_ALERT_SOURCE
            or not _text(projected.get("source_id"))
            or (wanted
            and not _route_values(projected.get("route_ids")).intersection(wanted))
        ):
            continue
        rows.append(projected)
        if len(rows) >= _ALERT_LIMIT:
            break
    return rows or None


def _official_alert_ids(value: object, routes: object) -> set[str] | None:
    if not isinstance(value, list):
        return None
    wanted = _route_values(routes)
    ids: set[str] = set()
    for raw in value:
        projected = project_service_alert(raw)
        if not isinstance(projected, dict) or projected.get("source") != _OFFICIAL_ALERT_SOURCE:
            continue
        if wanted and not _route_values(projected.get("route_ids")).intersection(wanted):
            continue
        source_id = _text(projected.get("source_id"))
        if source_id:
            ids.add(source_id)
    if ids or not value:
        return ids
    return None


def _alert_ids(rows: object) -> set[str]:
    return {
        _text(row.get("source_id"))
        for row in rows if isinstance(row, Mapping) and _text(row.get("source_id"))
    }


def _alert_envelope(
    value: object,
    routes: object,
    now: datetime,
) -> dict[str, Any] | None:
    if not isinstance(value, Mapping):
        return None
    if _text(value.get("source")) != _OFFICIAL_ALERT_SOURCE:
        return None
    observed = evidence_service.parse_timestamp(value.get("observedAt"))
    valid_until = evidence_service.parse_timestamp(value.get("validUntil"))
    status = _text(value.get("status")).casefold()
    payload = _official_alert_rows(value.get("payload"), routes)
    if observed is None or valid_until is None or status not in {"current", "stale"}:
        return None
    if payload is None:
        return None
    return {
        "current": status == "current" and valid_until > now,
        "ids": _alert_ids(payload),
        "observed_at": observed.isoformat(),
    }


def _decision_status_data(
    matches: list[dict[str, Any]], routes: list[str]
) -> dict[str, Any]:
    alerts: list[dict[str, Any]] = []
    incidents: list[dict[str, Any]] = []
    signals: list[dict[str, Any]] = []
    seen_alerts: set[str] = set()
    seen_incidents: set[str] = set()
    seen_signals: set[tuple[str, str]] = set()
    coverage: dict[str, str] = {}
    observed_at = ""
    for match in matches:
        for alert in match.get("rows") or []:
            alert_id = _text(alert.get("source_id"))
            if alert_id and alert_id not in seen_alerts:
                seen_alerts.add(alert_id)
                alerts.append(alert)
        envelope = match.get("envelope") or {}
        observed_at = observed_at or _text(envelope.get("observed_at"))
        evidence = match.get("evidence") or {}
        evidence_coverage = evidence.get("evidence_coverage")
        if isinstance(evidence_coverage, Mapping):
            _merge_coverage(
                coverage,
                "vehicles",
                evidence_coverage.get("vehicles") or evidence_coverage.get("gtfs_rt"),
            )
            _merge_coverage(coverage, "incidents", evidence_coverage.get("incidents"))
        for raw in evidence.get("incidents") or []:
            incident = _incident_projection(raw, routes)
            incident_id = _text(incident.get("incident_id"))
            if incident and incident_id and incident_id not in seen_incidents:
                seen_incidents.add(incident_id)
                incidents.append(incident)
        for raw in evidence.get("unconfirmed_material_claims") or []:
            signal = _signal_projection(raw, routes)
            key = (_text(signal.get("route_id")), _text(signal.get("location")))
            if signal and key not in seen_signals:
                seen_signals.add(key)
                signals.append(signal)
    return {
        "source": _OFFICIAL_ALERT_SOURCE,
        "freshness": "current",
        "status": "active_alerts",
        "requested_routes": routes,
        "affected_routes": sorted(
            {
                route
                for alert in alerts
                for route in _route_values(alert.get("route_ids"))
            }
        ),
        "alerts": alerts[:_ALERT_LIMIT],
        "incidents": incidents[:_ROW_LIMIT],
        "unconfirmed_signals": signals[:_ROW_LIMIT],
        "gtfs_rt_coverage": coverage.get("vehicles") or "unknown",
        "incident_coverage": coverage.get("incidents") or "unknown",
        "evidence_origin": "accepted_candidate_evidence",
        **({"observed_at": observed_at} if observed_at else {}),
    }


def _merge_coverage(target: dict[str, str], key: str, value: object) -> None:
    status = _text(value).casefold()
    if status in {"current", "partial", "stale", "unavailable", "unscanned"}:
        target[key] = status


def _incident_projection(value: object, routes: list[str]) -> dict[str, Any]:
    row = value if isinstance(value, Mapping) else {}
    route_ids = sorted(_route_values(row.get("affected_route_ids") or row.get("route_ids")))
    if routes and not set(route_ids).intersection(routes):
        return {}
    result = {
        "incident_id": _text(row.get("incident_id") or row.get("id")),
        "header": _text(row.get("location_name") or row.get("location")),
        "description": _text(row.get("description"))[:500],
        "route_ids": route_ids,
        "state": _text(row.get("state") or row.get("confirmation")),
    }
    direction = _text(row.get("direction") or row.get("direction_label"))
    if direction:
        result["direction"] = direction[:80]
    return {key: value for key, value in result.items() if value not in (None, "", [])}


def _signal_projection(value: object, routes: list[str]) -> dict[str, Any]:
    row = value if isinstance(value, Mapping) else {}
    route_id = _text(row.get("route_id") or row.get("route")).upper()
    if routes and route_id not in routes:
        return {}
    result = {
        "kind": _text(row.get("kind") or row.get("status")) or "possible_delay_unconfirmed",
        "route_id": route_id,
        "mode": _text(row.get("mode")).casefold(),
        "location": _text(row.get("location"))[:96],
        "confirmed": False,
    }
    return {key: value for key, value in result.items() if value not in (None, "", [])}


__all__ = [
    "accessibility_result_matches",
    "bind_accessibility_target",
    "decision_alert_continuity",
    "decision_evidence_for_status",
]
