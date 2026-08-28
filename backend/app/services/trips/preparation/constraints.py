"""Canonical route constraints and passenger-safe candidate digests."""

from __future__ import annotations

from typing import Any

from app.services.mta.alerts import is_material_service_alert, project_service_alert
from app.services.trips import scoring
from app.services.trips.location import ResolvedPlace
from app.services.trips.preparation.input import normalize_route_ids, parse_rfc3339
from app.services.trips.transfer_semantics import (
    route_accessibility,
    route_transfer_facts,
    route_walking_totals,
)

_VALID_COVERAGE_STATUSES = {
    "current",
    "partial",
    "stale",
    "unavailable",
    "unscanned",
    "not_required",
}

ROUTE_STATUSES = {
    "good",
    "degraded_usable",
    "all_materially_degraded",
    "no_hard_constraint_match",
    "insufficient_coverage",
}


def serialize_place(place: ResolvedPlace) -> dict[str, Any]:
    return {
        "name": place.name,
        "latitude": place.latitude,
        "longitude": place.longitude,
        "source": place.source,
        "address": place.address,
        "place_id": place.place_id,
    }


def route_constraints(
    route: list[dict],
    tool_input: dict[str, Any],
    *,
    itinerary: dict[str, Any] | None = None,
) -> dict[str, Any]:
    excluded = {
        str(value).strip().upper()
        for value in tool_input.get("exclude_modes") or []
        if str(value).strip()
    }
    route_modes = {
        _constraint_mode(step.get("type"))
        for step in route or []
        if _constraint_mode(step.get("type"))
    }
    route_ids = {
        str(step.get("route_id") or step.get("train_line") or "").strip().upper()
        for step in route or []
        if str(step.get("route_id") or step.get("train_line") or "").strip()
    }
    excluded_route_ids = set(
        normalize_route_ids(tool_input.get("excluded_route_ids") or [])
    )
    required = {
        str(value).strip().upper()
        for value in tool_input.get("required_route_ids") or []
        if str(value).strip()
    }
    street_seconds, in_station_seconds = route_walking_totals(route)
    if isinstance(itinerary, dict):
        street_seconds = max(
            0,
            int(itinerary.get("total_street_walking_seconds") or street_seconds),
        )
        in_station_seconds = max(
            0,
            int(
                itinerary.get("total_in_station_transfer_seconds")
                or in_station_seconds
            ),
        )
    walking_limit = _walking_limit(tool_input)
    accessibility_required = bool(
        tool_input.get("accessibility_required") or tool_input.get("avoid_stairs")
    )
    accessibility = route_accessibility(route)
    violations: list[str] = []
    if route_modes & excluded:
        violations.append("excluded_mode")
    if route_ids & excluded_route_ids:
        violations.append("excluded_route")
    if required and not required.issubset(route_ids):
        violations.append("required_route_missing")
    if walking_limit is not None and street_seconds > walking_limit * 60:
        violations.append("walking_tolerance")
    if accessibility_required and accessibility != "accessible":
        violations.append("accessibility_unknown_or_unavailable")
    missed_arrival = _arrival_by_violation(tool_input, itinerary)
    if missed_arrival:
        violations.append(missed_arrival)
    return {
        "satisfied": not violations,
        "violations": violations,
        "accessibility_required": accessibility_required,
        "accessibility_status": accessibility,
        "street_walking_seconds": street_seconds,
        "in_station_transfer_seconds": in_station_seconds,
        "route_modes": sorted(route_modes),
        "route_ids": sorted(route_ids),
    }


def _arrival_by_violation(
    tool_input: dict[str, Any], itinerary: dict[str, Any] | None
) -> str | None:
    requested_arrival = tool_input.get("arrival_by")
    finalized_arrival = (
        itinerary.get("arrival_at") if isinstance(itinerary, dict) else None
    )
    if not requested_arrival or not finalized_arrival:
        return None
    try:
        requested_target = parse_rfc3339(requested_arrival, field="arrival_by")
        actual_arrival = parse_rfc3339(finalized_arrival, field="arrival_at")
    except (TypeError, ValueError):
        return None
    if actual_arrival > requested_target:
        return "arrival_by_missed"
    return None


def candidate_digest(
    *,
    route: list[dict],
    candidate_id: str,
    score: dict[str, Any],
    alerts: list[dict],
    incidents: list[dict],
    event_impacts: list[dict],
    prepared_arrival_by: str | None,
    hard_constraints: dict[str, Any],
    unconfirmed_material_claims: list[dict[str, Any]] | None = None,
    evidence_coverage: dict[str, str] | None = None,
    itinerary: dict[str, Any] | None = None,
    evidence_snapshot: dict[str, str] | None = None,
    soft_preferences: dict[str, Any] | None = None,
    destination_place_id: str | None = None,
    destination_name: str | None = None,
    branch_coverage: list[dict[str, Any]] | None = None,
    stage_a_factors: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    index = int(score.get("index") or 0)
    timing = _candidate_timing(route, score, itinerary)
    canonical = itinerary or {}
    return {
        "candidate_id": candidate_id,
        "destination_place_id": destination_place_id,
        "destination_name": destination_name,
        "branch_coverage": [
            dict(item) for item in branch_coverage or [] if isinstance(item, dict)
        ],
        "stage_a_factors": [
            dict(item) for item in stage_a_factors or [] if isinstance(item, dict)
        ],
        **timing,
        "transit_lines": _route_lines(route),
        "arrival_context": _arrival_context(route, prepared_arrival_by, itinerary),
        "departure_at": canonical.get("departure_at"),
        "arrival_at": canonical.get("arrival_at"),
        "wait_minutes": round(int(canonical.get("total_wait_seconds") or 0) / 60),
        "in_vehicle_minutes": round(
            int(canonical.get("total_in_vehicle_seconds") or 0) / 60
        ),
        "waypoint_count": len(canonical.get("waypoints") or []),
        "official_service_impacts": _alerts(alerts),
        "confirmed_incident_impacts": _incidents(incidents),
        "unconfirmed_material_claims": _unconfirmed_claims(
            unconfirmed_material_claims
        ),
        "event_or_crowd_impacts": _candidate_event_impacts(event_impacts, index),
        "accessibility_status": hard_constraints.get(
            "accessibility_status", "unknown"
        ),
        "accessibility_required": bool(
            hard_constraints.get("accessibility_required")
        ),
        "transfer_facts": _transfer_digests(route),
        "hard_constraints_satisfied": hard_constraints.get("satisfied") is True,
        "hard_constraint_violations": list(
            hard_constraints.get("violations") or []
        ),
        "finalized": bool(score.get("finalized") or itinerary),
        "evidence_coverage": _candidate_coverage(evidence_coverage),
        "evidence_snapshot": dict(evidence_snapshot or {}),
        "soft_preferences": dict(soft_preferences or {}),
        "score_summary": {
            "estimated_duration": timing["duration_minutes"],
            "reliability": _reliability(score),
        },
    }


def _candidate_timing(
    route: list[dict],
    score: dict[str, Any],
    itinerary: dict[str, Any] | None,
) -> dict[str, int]:
    """Project finalized canonical timing without exposing private ranking."""

    aggregate_route = any("segment_index" in step for step in route)
    route_street_seconds, route_transfer_seconds = route_walking_totals(route)
    street_seconds = (
        int(score.get("street_walking_seconds") or 0)
        if aggregate_route
        else route_street_seconds
    )
    in_station_seconds = (
        int(score.get("in_station_transfer_seconds") or 0)
        if aggregate_route
        else route_transfer_seconds
    )
    if isinstance(itinerary, dict):
        street_seconds = max(
            0,
            int(itinerary.get("total_street_walking_seconds") or street_seconds),
        )
        in_station_seconds = max(
            0,
            int(
                itinerary.get("total_in_station_transfer_seconds")
                or in_station_seconds
            ),
        )
    duration_minutes = (
        int(score.get("total_minutes") or 0)
        if aggregate_route
        else scoring._route_total_minutes(route)
    )
    if isinstance(itinerary, dict):
        total_seconds = itinerary.get("total_duration_seconds")
        if isinstance(total_seconds, (int, float)) and not isinstance(
            total_seconds, bool
        ):
            duration_minutes = max(1, round(float(total_seconds) / 60))
    transfers = (
        int(score.get("transfers") or 0)
        if aggregate_route
        else scoring._route_transfer_count(route)
    )
    if isinstance(itinerary, dict):
        transfers = max(0, int(itinerary.get("transfer_count") or transfers))
    return {
        "duration_minutes": duration_minutes,
        "walking_minutes": round(street_seconds / 60),
        "street_walking_minutes": round(street_seconds / 60),
        "in_station_transfer_minutes": round(in_station_seconds / 60),
        "transfers": transfers,
    }


def _candidate_event_impacts(
    event_impacts: list[dict], index: int
) -> list[dict[str, Any]]:
    return [
        {
            "event_name": impact.get("title"),
            "venue_name": impact.get("venue"),
            "exposure_window": impact.get("exposure_window"),
            "crowd_level": impact.get("crowd_level"),
            "confidence": impact.get("confidence"),
            "risk_score": impact.get("risk_score"),
        }
        for impact in event_impacts
        if impact.get("route_index") == index
    ][:3]


def _candidate_coverage(
    evidence_coverage: dict[str, str] | None,
) -> dict[str, str]:
    return {
        str(key): str(value)
        for key, value in (evidence_coverage or {}).items()
        if str(key).strip() and str(value).strip() in _VALID_COVERAGE_STATUSES
    }


def route_status(
    *,
    candidates: list[dict],
    coverage: dict[str, str],
    incident_impacts: list[dict],
) -> str:
    del incident_impacts
    if not candidates:
        return "insufficient_coverage"
    if not any(item.get("hard_constraints_satisfied") is True for item in candidates):
        return "no_hard_constraint_match"
    covered = [
        value for value in coverage.values() if value in _VALID_COVERAGE_STATUSES
    ]
    if not covered or all(
        value in {"unavailable", "unscanned", "not_required"} for value in covered
    ):
        return "insufficient_coverage"
    usable = [
        item
        for item in candidates
        if item.get("hard_constraints_satisfied") is True
    ]
    materially_degraded = [
        item
        for item in usable
        if any(
            is_material_service_alert(alert)
            for alert in item.get("official_service_impacts") or []
        )
        or item.get("confirmed_incident_impacts")
        or any(
            float(impact.get("risk_score") or 0) > 0
            for impact in item.get("event_or_crowd_impacts") or []
        )
    ]
    if usable and len(materially_degraded) == len(usable):
        return "all_materially_degraded"
    if any(
        value in {"partial", "stale", "unavailable", "unscanned"}
        for value in covered
    ):
        return "degraded_usable"
    return "good"


def _walking_limit(tool_input: dict[str, Any]) -> int | None:
    for key in ("max_walking_minutes", "walking_tolerance_minutes"):
        value = tool_input.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return max(0, min(180, round(value)))
    return None


def _constraint_mode(value: object) -> str:
    mode = str(value or "").strip().upper()
    if mode in {"TRAIN", "LIGHT_RAIL", "TRAM"}:
        return "RAIL"
    return mode


def _route_lines(route: list[dict]) -> list[str]:
    lines: list[str] = []
    for step in route:
        if str(step.get("type") or "").upper() not in {"SUBWAY", "BUS", "RAIL"}:
            continue
        line = str(step.get("route_id") or step.get("train_line") or "").strip().upper()
        if line and line not in lines:
            lines.append(line)
    return lines


def _transfer_digests(route: list[dict]) -> list[dict[str, Any]]:
    return [
        {
            "kind": fact.get("kind"),
            "total_seconds": int(fact.get("total_seconds") or 0),
            "street_walking_seconds": int(
                fact.get("street_walking_seconds") or 0
            ),
            "in_station_transfer_seconds": int(
                fact.get("in_station_transfer_seconds") or 0
            ),
            "accessibility": fact.get("accessibility") or "unknown",
        }
        for fact in route_transfer_facts(route)
    ][:4]


def _arrival_context(
    route: list[dict],
    arrival_by: str | None,
    itinerary: dict[str, Any] | None = None,
) -> str:
    if isinstance(itinerary, dict) and itinerary.get("arrival_at"):
        return "arrival time estimated from the finalized itinerary"
    last = route[-1] if route else {}
    if last.get("arrival_time_iso"):
        return "arrival time estimated from live schedules"
    return "timed for the requested arrival" if arrival_by else "arrival time unavailable"


def _alerts(alerts: list[dict]) -> list[dict[str, object]]:
    projected = [project_service_alert(alert) for alert in alerts]
    return [alert for alert in projected if alert is not None][:3]


def _incidents(incidents: list[dict]) -> list[str]:
    return [
        str(item.get("description") or item.get("location") or "").strip()[:160]
        for item in incidents
        if str(item.get("description") or item.get("location") or "").strip()
    ][:3]


def _unconfirmed_claims(
    claims: list[dict[str, Any]] | None,
) -> list[dict[str, str]]:
    """Keep possible vehicle signals passenger-safe and provider-agnostic."""

    result: list[dict[str, str]] = []
    for claim in claims or []:
        if not isinstance(claim, dict):
            continue
        status_text = " ".join(
            str(claim.get(key) or "")
            for key in ("status", "progress_status", "ProgressStatus")
        ).casefold()
        if "layover" in status_text:
            continue
        mode = str(claim.get("mode") or "transit").strip().lower()
        if mode not in {"train", "bus", "subway", "transit"}:
            mode = "transit"
        route = str(claim.get("route_id") or claim.get("route") or "").strip().upper()
        if not route:
            continue
        location_value = claim.get("location") or claim.get("stop_name")
        location = (
            location_value.strip()
            if isinstance(location_value, str)
            else "the route"
        )
        result.append(
            {
                "mode": "train" if mode in {"subway", "train"} else mode,
                "route": route,
                "location": location[:96],
                "status": "possible_delay_unconfirmed",
            }
        )
        if len(result) >= 3:
            break
    return result


def _reliability(score: dict[str, Any]) -> str:
    return (
        "medium"
        if int(score.get("alert_count") or 0) > 0
        or float(score.get("event_crowd_penalty") or 0) > 0
        else "high"
    )


__all__ = (
    "ROUTE_STATUSES",
    "candidate_digest",
    "route_constraints",
    "route_status",
    "serialize_place",
)
