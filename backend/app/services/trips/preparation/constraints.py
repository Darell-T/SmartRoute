"""Canonical route constraints and passenger-safe candidate digests."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.services.mta.alerts import is_material_service_alert, project_service_alert
from app.services.trips import scoring
from app.services.trips.preparation.evidence import _vehicle_claim_is_layover
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
_NON_APPLICABLE_COVERAGE = frozenset({"unavailable", "unscanned", "not_required"})
_DEGRADED_COVERAGE = frozenset({"partial", "stale", "unavailable", "unscanned"})

ROUTE_STATUSES = {
    "good",
    "degraded_usable",
    "all_materially_degraded",
    "no_hard_constraint_match",
    "insufficient_coverage",
}


@dataclass(frozen=True)
class RouteConstraintFacts:
    route_modes: set[str]
    excluded_modes: set[str]
    route_ids: set[str]
    excluded_route_ids: set[str]
    required_route_ids: set[str]
    street_seconds: int
    in_station_seconds: int
    walking_limit: int | None
    accessibility_required: bool
    accessibility: str


@dataclass(frozen=True)
class _TimingFacts:
    duration_minutes: int
    street_seconds: int
    in_station_seconds: int
    transfers: int


def route_constraints(
    route: list[dict],
    tool_input: dict[str, Any],
    *,
    itinerary: dict[str, Any] | None = None,
) -> dict[str, Any]:
    facts = _route_constraint_facts(route, tool_input, itinerary)
    violations: list[str] = []
    if facts.route_modes & facts.excluded_modes:
        violations.append("excluded_mode")
    if facts.route_ids & facts.excluded_route_ids:
        violations.append("excluded_route")
    if facts.required_route_ids and not facts.required_route_ids.issubset(facts.route_ids):
        violations.append("required_route_missing")
    if facts.walking_limit is not None and facts.street_seconds > facts.walking_limit * 60:
        violations.append("walking_tolerance")
    if facts.accessibility_required and facts.accessibility != "accessible":
        violations.append("accessibility_unknown_or_unavailable")
    missed_arrival = _arrival_by_violation(tool_input, itinerary)
    if missed_arrival:
        violations.append(missed_arrival)
    return {
        "satisfied": not violations,
        "violations": violations,
        "accessibility_required": facts.accessibility_required,
        "accessibility_status": facts.accessibility,
        "street_walking_seconds": facts.street_seconds,
        "in_station_transfer_seconds": facts.in_station_seconds,
        "route_modes": sorted(facts.route_modes),
        "route_ids": sorted(facts.route_ids),
    }


def _route_constraint_facts(
    route: list[dict],
    tool_input: dict[str, Any],
    itinerary: dict[str, Any] | None,
) -> RouteConstraintFacts:
    route_ids, route_modes = _route_ids_and_modes(route)
    street_seconds, in_station_seconds = _walking_seconds(route, itinerary)
    return RouteConstraintFacts(
        route_modes=route_modes,
        excluded_modes=_upper_tokens(tool_input.get("exclude_modes")),
        route_ids=route_ids,
        excluded_route_ids=set(
            normalize_route_ids(tool_input.get("excluded_route_ids") or [])
        ),
        required_route_ids=set(
            normalize_route_ids(tool_input.get("required_route_ids") or [])
        ),
        street_seconds=street_seconds,
        in_station_seconds=in_station_seconds,
        walking_limit=_walking_limit(tool_input),
        accessibility_required=bool(
            tool_input.get("accessibility_required") or tool_input.get("avoid_stairs")
        ),
        accessibility=route_accessibility(route),
    )


def _upper_tokens(values: object) -> set[str]:
    return {
        str(value).strip().upper()
        for value in values or []
        if str(value).strip()
    }


def _route_ids_and_modes(route: list[dict] | None) -> tuple[set[str], set[str]]:
    route_ids: set[str] = set()
    route_modes: set[str] = set()
    for step in route or []:
        mode = scoring.normalized_mode(step.get("type"))
        if mode:
            route_modes.add(mode)
        route_id = scoring.step_route_id(step)
        if route_id:
            route_ids.add(route_id)
    return route_ids, route_modes


def _walking_seconds(
    route: list[dict], itinerary: dict[str, Any] | None
) -> tuple[int, int]:
    street_seconds, in_station_seconds = route_walking_totals(route)
    if not isinstance(itinerary, dict):
        return street_seconds, in_station_seconds
    return (
        max(0, int(itinerary.get("total_street_walking_seconds") or street_seconds)),
        max(
            0,
            int(itinerary.get("total_in_station_transfer_seconds") or in_station_seconds),
        ),
    )


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
        "branch_coverage": _dict_rows(branch_coverage),
        "stage_a_factors": _dict_rows(stage_a_factors),
        **timing,
        "transit_lines": _route_lines(route),
        "arrival_context": _arrival_context(route, prepared_arrival_by, itinerary),
        **_canonical_clock(canonical),
        "official_service_impacts": _alerts(alerts),
        "confirmed_incident_impacts": _incidents(incidents),
        "unconfirmed_material_claims": _unconfirmed_claims(unconfirmed_material_claims),
        "event_or_crowd_impacts": _candidate_event_impacts(event_impacts, index),
        **_digest_constraint_fields(hard_constraints),
        "transfer_facts": _transfer_digests(route),
        "finalized": bool(score.get("finalized") or itinerary),
        "evidence_coverage": _candidate_coverage(evidence_coverage),
        "evidence_snapshot": dict(evidence_snapshot or {}),
        "soft_preferences": dict(soft_preferences or {}),
        "score_summary": {
            "estimated_duration": timing["duration_minutes"],
            "reliability": _reliability(score),
        },
    }


def _dict_rows(items: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    return [dict(item) for item in items or [] if isinstance(item, dict)]


def _canonical_clock(canonical: dict[str, Any]) -> dict[str, Any]:
    return {
        "departure_at": canonical.get("departure_at"),
        "arrival_at": canonical.get("arrival_at"),
        "wait_minutes": round(int(canonical.get("total_wait_seconds") or 0) / 60),
        "in_vehicle_minutes": round(
            int(canonical.get("total_in_vehicle_seconds") or 0) / 60
        ),
        "waypoint_count": len(canonical.get("waypoints") or []),
    }


def _digest_constraint_fields(hard_constraints: dict[str, Any]) -> dict[str, Any]:
    return {
        "accessibility_status": hard_constraints.get("accessibility_status", "unknown"),
        "accessibility_required": bool(hard_constraints.get("accessibility_required")),
        "hard_constraints_satisfied": hard_constraints.get("satisfied") is True,
        "hard_constraint_violations": list(hard_constraints.get("violations") or []),
    }


def _candidate_timing(
    route: list[dict],
    score: dict[str, Any],
    itinerary: dict[str, Any] | None,
) -> dict[str, int]:
    """Project finalized canonical timing without exposing private ranking."""

    facts = _route_or_score_timing(route, score)
    if isinstance(itinerary, dict):
        facts = _overlay_canonical_timing(facts, itinerary)
    return {
        "duration_minutes": facts.duration_minutes,
        "walking_minutes": round(facts.street_seconds / 60),
        "street_walking_minutes": round(facts.street_seconds / 60),
        "in_station_transfer_minutes": round(facts.in_station_seconds / 60),
        "transfers": facts.transfers,
    }


def _route_or_score_timing(route: list[dict], score: dict[str, Any]) -> _TimingFacts:
    if any("segment_index" in step for step in route):
        street_seconds, in_station_seconds = (
            int(score.get("street_walking_seconds") or 0),
            int(score.get("in_station_transfer_seconds") or 0),
        )
        duration_minutes = int(score.get("total_minutes") or 0)
        transfers = int(score.get("transfers") or 0)
    else:
        street_seconds, in_station_seconds = route_walking_totals(route)
        duration_minutes = scoring.route_total_minutes(route)
        transfers = scoring.route_transfer_count(route)
    return _TimingFacts(
        duration_minutes=duration_minutes,
        street_seconds=street_seconds,
        in_station_seconds=in_station_seconds,
        transfers=transfers,
    )


def _overlay_canonical_timing(
    facts: _TimingFacts, itinerary: dict[str, Any]
) -> _TimingFacts:
    duration_minutes = facts.duration_minutes
    total_seconds = itinerary.get("total_duration_seconds")
    if isinstance(total_seconds, (int, float)) and not isinstance(total_seconds, bool):
        duration_minutes = max(1, round(float(total_seconds) / 60))
    return _TimingFacts(
        duration_minutes=duration_minutes,
        street_seconds=max(
            0,
            int(itinerary.get("total_street_walking_seconds") or facts.street_seconds),
        ),
        in_station_seconds=max(
            0,
            int(
                itinerary.get("total_in_station_transfer_seconds")
                or facts.in_station_seconds
            ),
        ),
        transfers=max(0, int(itinerary.get("transfer_count") or facts.transfers)),
    )


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
    usable = _hard_constraint_matches(candidates)
    if not usable:
        return "no_hard_constraint_match"
    covered = _usable_coverage_values(coverage)
    if _coverage_is_insufficient(covered):
        return "insufficient_coverage"
    if all(_candidate_is_materially_degraded(item) for item in usable):
        return "all_materially_degraded"
    if _coverage_is_degraded(covered):
        return "degraded_usable"
    return "good"


def _hard_constraint_matches(candidates: list[dict]) -> list[dict]:
    return [
        item
        for item in candidates
        if item.get("hard_constraints_satisfied") is True
    ]


def _usable_coverage_values(coverage: dict[str, str]) -> list[str]:
    return [value for value in coverage.values() if value in _VALID_COVERAGE_STATUSES]


def _coverage_is_insufficient(covered: list[str]) -> bool:
    return not covered or all(value in _NON_APPLICABLE_COVERAGE for value in covered)


def _coverage_is_degraded(covered: list[str]) -> bool:
    return any(value in _DEGRADED_COVERAGE for value in covered)


def _candidate_is_materially_degraded(item: dict) -> bool:
    if any(
        is_material_service_alert(alert)
        for alert in item.get("official_service_impacts") or []
    ):
        return True
    if item.get("confirmed_incident_impacts"):
        return True
    return any(
        float(impact.get("risk_score") or 0) > 0
        for impact in item.get("event_or_crowd_impacts") or []
    )


def _walking_limit(tool_input: dict[str, Any]) -> int | None:
    for key in ("max_walking_minutes", "walking_tolerance_minutes"):
        value = tool_input.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return max(0, min(180, round(value)))
    return None


def _route_lines(route: list[dict]) -> list[str]:
    lines: list[str] = []
    for step in route:
        if str(step.get("type") or "").upper() not in {"SUBWAY", "BUS", "RAIL"}:
            continue
        line = scoring.step_route_id(step)
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
        projected = _project_unconfirmed_claim(claim)
        if projected is None:
            continue
        result.append(projected)
        if len(result) >= 3:
            break
    return result


def _project_unconfirmed_claim(claim: object) -> dict[str, str] | None:
    if not isinstance(claim, dict):
        return None
    if _vehicle_claim_is_layover(claim):
        return None
    route = str(claim.get("route_id") or claim.get("route") or "").strip().upper()
    if not route:
        return None
    return {
        "mode": _passenger_claim_mode(claim),
        "route": route,
        "location": _claim_location(claim),
        "status": "possible_delay_unconfirmed",
    }


def _passenger_claim_mode(claim: dict) -> str:
    mode = str(claim.get("mode") or "transit").strip().lower()
    if mode not in {"train", "bus", "subway", "transit"}:
        mode = "transit"
    return "train" if mode in {"subway", "train"} else mode


def _claim_location(claim: dict) -> str:
    location_value = claim.get("location") or claim.get("stop_name")
    if isinstance(location_value, str):
        return location_value.strip()[:96]
    return "the route"


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
)
