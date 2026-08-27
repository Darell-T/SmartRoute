"""Passenger-safe projection helpers for typed transit evidence."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from app.services.agent.tools.transit.direction import (
    normalize_direction,
    stop_id_direction,
)
from app.services.agent.tools.transit.evidence_matching import (
    normalized_route_ids,
    normalized_text,
)
from app.services.mta.alerts import project_service_alert


def safe_result(
    row: dict[str, Any],
    requested_direction: str | None = None,
) -> dict[str, Any]:
    result = {
        key: row[key]
        for key in ("route_id", "updated_at", "source_status")
        if key in row
    }
    if "stop" in row:
        result["stop"] = safe_stop(row.get("stop"))
    directions = []
    matched_direction = False
    for group in row.get("directions") or []:
        if not isinstance(group, dict):
            continue
        group_direction = normalize_direction(
            group.get("label")
        ) or normalize_direction(group.get("id"))
        if requested_direction and group_direction != requested_direction:
            continue
        if requested_direction:
            matched_direction = True
        safe_arrivals = []
        for item in group.get("arrivals") or []:
            if isinstance(item, dict):
                safe_arrivals.append(
                    {
                        key: item[key]
                        for key in ("expected_at", "minutes", "realtime")
                        if key in item
                    }
                )
        directions.append(
            {
                "id": group.get("id"),
                "label": group.get("label"),
                "arrivals": safe_arrivals,
            }
        )
    result["directions"] = directions
    catchability = row.get("catchability")
    if requested_direction and matched_direction and isinstance(catchability, dict):
        projected = _safe_catchability(catchability, directions)
        if projected is not None:
            result["catchability"] = projected
    return result


def _safe_catchability(
    catchability: dict[str, Any], directions: list[dict[str, Any]]
) -> dict[str, Any] | None:
    """Re-derive catchability from the direction-filtered predictions."""

    walking = _integer(catchability.get("walking_minutes"))
    buffer = _integer(catchability.get("boarding_buffer_minutes"))
    if walking is None or buffer is None:
        return None
    minutes = sorted(
        {
            parsed
            for direction in directions
            for arrival in direction.get("arrivals") or []
            if isinstance(arrival, dict)
            for parsed in [_integer(arrival.get("minutes"))]
            if parsed is not None and parsed > 0
        }
    )
    threshold = max(0, walking) + max(0, buffer)
    safe = {
        "walking_minutes": max(0, walking),
        "boarding_buffer_minutes": max(0, buffer),
        "arrival_minutes": minutes,
        "catchable_arrival_minutes": next(
            (value for value in minutes if value >= threshold), None
        ),
    }
    if "confidence" in catchability:
        safe["confidence"] = catchability["confidence"] if minutes else 0.0
    return safe


def _integer(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def operation_facts(operation: str, row: dict[str, Any]) -> dict[str, Any]:
    """Keep only passenger-presentable fields for non-arrival operations."""

    if operation == "fact":
        source = row.get("source") if isinstance(row.get("source"), dict) else {}
        return {
            "topic": normalized_text(row.get("topic")),
            "text": normalized_text(row.get("text"))[:1600],
            "source": {
                key: source[key]
                for key in ("name", "effective_date", "version")
                if key in source
            },
        }
    if operation == "area_conditions":
        return {
            "area": normalized_text(row.get("area") or row.get("resolved_area")),
            "incidents": [
                _safe_named_item(item, ("severity", "category", "start_iso"))
                for item in (row.get("incidents") or [])[:8]
                if isinstance(item, dict)
            ],
            "events": [
                _safe_named_item(
                    item,
                    ("category", "venue_name", "start_iso", "estimated_end_iso"),
                )
                for item in (row.get("events") or [])[:8]
                if isinstance(item, dict)
            ],
            "incident_status": _evidence_status(row.get("incident_evidence")),
            "event_status": _evidence_status(row.get("event_evidence")),
        }
    if operation == "event_schedule":
        return {
            "events": [
                _safe_named_item(
                    item,
                    ("venue_name", "start_iso", "estimated_end_iso"),
                )
                for item in (row.get("events") or [])[:8]
                if isinstance(item, dict)
            ],
            "note": normalized_text(row.get("note"))[:300],
        }
    if operation == "venue_crowd_window":
        return {
            key: row[key]
            for key in (
                "venue",
                "surge_start_iso",
                "surge_end_iso",
                "pre_event_start_iso",
                "pre_event_end_iso",
                "stations",
                "lines",
                "is_heuristic",
            )
            if key in row
        }
    return {}


def renderable_arrival_card(row: dict[str, Any]) -> bool:
    """Cards require a resolved stop and actual direction predictions."""

    status = str(row.get("source_status") or "").casefold()
    if status in {"stop_not_resolved", "provider_unavailable", "no_predictions"}:
        return False
    stop = row.get("stop") if isinstance(row.get("stop"), dict) else {}
    if not (stop.get("id") or stop.get("name")):
        return False
    return bool(row.get("directions"))


def accessibility_text(evidence: dict[str, Any], unknowns: tuple[str, ...]) -> str:
    accessibility = evidence.get("accessibility")
    binding = accessibility.get("binding") if isinstance(accessibility, dict) else None
    if not isinstance(binding, dict) and isinstance(accessibility, dict):
        binding = accessibility
    if isinstance(binding, dict) and (
        str(binding.get("entity_type") or "").upper() == "BUS_STOP"
        or str(binding.get("mode") or "").upper() == "BUS"
    ):
        return "Accessibility information is unavailable for that bus stop."
    station_name = (
        str(binding.get("station") or "").strip()
        if isinstance(binding, dict)
        else ""
    ) or str(accessibility.get("station_matched") or "").strip()
    subject = station_name or "The station"
    outages = accessibility.get("elevator_outages") if isinstance(accessibility, dict) else []
    if outages:
        return f"{subject} has {len(outages)} reported elevator outage(s)."
    if unknowns:
        return "Accessibility information is unavailable right now."
    return f"No elevator outages were reported at {subject}."


def operation_facts_text(operation: str, facts: dict[str, Any]) -> str:
    if operation == "fact":
        return str(facts.get("text") or "That transit fact is unavailable.")
    if operation == "area_conditions":
        area = str(facts.get("area") or "the checked area")
        rows = [
            *_named_lines("Incident", facts.get("incidents")),
            *_named_lines("Event", facts.get("events")),
        ]
        if rows:
            return "\n".join([f"Current conditions near {area}:", *rows])
        statuses = {
            str(facts.get("incident_status") or "unknown"),
            str(facts.get("event_status") or "unknown"),
        }
        if statuses == {"complete"}:
            return f"No matching incident or event reports were returned near {area}."
        return f"Current condition coverage near {area} is incomplete."
    if operation == "event_schedule":
        rows = _event_lines(facts.get("events"))
        return (
            "\n".join(["Things happening nearby:", *rows])
            if rows
            else "I didn't find matching events for that schedule."
        )
    if operation == "venue_crowd_window":
        venue = str(facts.get("venue") or "the venue")
        start = str(facts.get("surge_start_iso") or "").strip()
        end = str(facts.get("surge_end_iso") or "").strip()
        if start and end:
            return f"Estimated post-event crowd pressure near {venue}: {start} to {end}."
        return f"A crowd window is unavailable for {venue}."
    return "Transit information is unavailable for that request."


def _named_lines(label: str, value: object) -> list[str]:
    items = value if isinstance(value, list) else []
    return [
        f"{label}: {str(item.get('name') or 'Unnamed').strip()}"
        for item in items[:5]
        if isinstance(item, dict)
    ]


def _event_lines(value: object) -> list[str]:
    items = value if isinstance(value, list) else []
    rows: list[str] = []
    for item in items[:5]:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "Unnamed event").strip()
        venue = str(item.get("venue_name") or "").strip()
        start = _event_start_text(item.get("start_iso"))
        details = [detail for detail in (venue, start) if detail]
        suffix = f" at {details[0]}" if details else ""
        if len(details) > 1:
            suffix += f", {details[1]}"
        rows.append(f"- {name}{suffix}")
    return rows


def _event_start_text(value: object) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    try:
        parsed = datetime.fromisoformat(raw)
        if parsed.tzinfo is not None:
            parsed = parsed.astimezone(ZoneInfo("America/New_York"))
    except ValueError:
        return ""
    month_day = parsed.strftime("%b %d").replace(" 0", " ")
    clock = parsed.strftime("%I:%M %p").lstrip("0")
    return f"{month_day} at {clock}"


def arrivals_text(evidence: dict[str, Any], routes: str) -> str:
    """Describe arrival coverage without implying a prediction exists."""

    rows = [
        row for row in evidence.get("results") or [] if isinstance(row, dict)
    ]
    count = sum(
        len(value)
        for value in (evidence.get("arrivals_by_direction") or {}).values()
        if isinstance(value, list)
    )
    if count:
        return f"I found {count} upcoming arrival estimate(s) for {routes}."
    statuses = {str(row.get("source_status") or "").casefold() for row in rows}
    if statuses and statuses <= {"no_predictions"}:
        return f"No upcoming arrivals were returned for {routes} in the available information."
    if "stale" in statuses:
        return f"The live arrival information for {routes} is out of date."
    if "provider_unavailable" in statuses:
        return f"Live arrival information for {routes} is temporarily unavailable."
    return f"Arrival predictions for {routes} are unavailable right now."


def _safe_named_item(
    item: dict[str, Any], extra_fields: tuple[str, ...]
) -> dict[str, Any]:
    name = normalized_text(item.get("name") or item.get("title") or item.get("header"))
    result: dict[str, Any] = {"name": name}
    description = normalized_text(item.get("description"))
    if description:
        result["description"] = description[:500]
    for key in extra_fields:
        if key in item and item[key] not in (None, "", []):
            result[key] = item[key]
    return {key: value for key, value in result.items() if value not in (None, "", [])}


def _evidence_status(value: object) -> str:
    row = value if isinstance(value, dict) else {}
    return normalized_text(row.get("status")) or "unknown"


def safe_stop(value: object) -> dict[str, Any]:
    row = value if isinstance(value, dict) else {}
    return {
        key: row[key]
        for key in ("id", "name", "distance_meters", "latitude", "longitude")
        if key in row
    }


def safe_accessibility(row: dict[str, Any]) -> dict[str, Any]:
    result = {
        key: row[key]
        for key in (
            "station_matched",
            "elevator_outages",
            "escalator_outages_count",
            "checked_at_note",
            "mode",
            "entity_type",
        )
        if key in row
    }
    observed = normalized_text(row.get("observed_at") or row.get("checked_at"))
    if observed:
        result["observed_at"] = observed
    binding = row.get("binding")
    if not isinstance(binding, dict):
        top_level_binding = {
            key: row[key]
            for key in ("mode", "entity_type", "station", "station_id")
            if key in row and row[key] not in (None, "", [])
        }
        binding = top_level_binding or None
    if isinstance(binding, dict):
        safe_binding = {
            key: binding[key]
            for key in (
                "bound",
                "card_id",
                "route_ids",
                "mode",
                "station",
                "station_id",
                "entity_type",
            )
            if key in binding and binding[key] not in (None, "", [])
        }
        if safe_binding:
            result["binding"] = safe_binding
    return result


def safe_alert(row: dict[str, Any]) -> dict[str, Any]:
    source = (
        row
        if row.get("header") or not row.get("title")
        else {**row, "header": row.get("title")}
    )
    result = project_service_alert(source) or {}
    direction = row_direction(row) or normalized_text(
        row.get("direction") or row.get("direction_label")
    )
    if direction:
        result["direction"] = direction
    return result


def safe_incident(row: dict[str, Any]) -> dict[str, Any]:
    result = {
        "incident_id": normalized_text(row.get("incident_id") or row.get("id")),
        "header": normalized_text(row.get("location_name") or row.get("location"))
        or "A current transit incident was reported",
        "description": normalized_text(row.get("description")),
        "route_ids": normalized_route_ids(
            row.get("affected_route_ids") or row.get("route_ids")
        ),
        "state": normalized_text(row.get("state") or row.get("confirmation")),
    }
    direction = row_direction(row)
    if direction:
        result["direction"] = direction
    return {key: value for key, value in result.items() if value not in (None, "", [])}


def safe_unconfirmed_signal(row: object) -> dict[str, Any]:
    """Project one provider stalled signal before it enters model evidence."""

    source = row if isinstance(row, dict) else {}
    mode = normalized_text(source.get("mode")).casefold()
    supplied_kind = normalized_text(source.get("kind"))
    default_kind = (
        "stalled_train"
        if mode in {"subway", "train"}
        else "stalled_bus"
        if mode == "bus"
        else "possible_delay"
    )
    result = {
        "kind": supplied_kind or default_kind,
        "route_id": normalized_text(source.get("route_id")).upper(),
        "stop_id": normalized_text(source.get("stop_id")),
        "reason": normalized_text(source.get("reason")) or "stale vehicle timestamp",
        "confirmed": False,
    }
    if mode:
        result["mode"] = mode
    observed = normalized_text(
        source.get("observed_at")
        or source.get("time_recorded")
        or source.get("updated_at")
    )
    if observed:
        result["observed_at"] = observed
    direction = row_direction(source)
    if not direction and mode in {"subway", "train"}:
        direction = stop_id_direction(source.get("stop_id"))
    if direction:
        result["direction"] = direction
    return {key: value for key, value in result.items() if value not in (None, "")}


def row_direction(row: dict[str, Any]) -> str | None:
    for key in (
        "canonical_direction",
        "semantic_direction",
        "direction",
        "direction_label",
        "label",
        "headsign",
    ):
        direction = normalize_direction(row.get(key))
        if direction:
            return direction
    return None
