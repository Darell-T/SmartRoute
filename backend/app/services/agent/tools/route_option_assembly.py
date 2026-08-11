"""Pure assembly and validation helpers for prepared route candidate sets."""

from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import Any

from app.services.agent import intelligence
from app.services.agent.tools._location import ResolvedPlace
from app.services.agent.tools.plan_trip_prepare import PreparedLeg
from app.services.agent.tools.route_option_evidence import (
    candidate_evidence_for_route,
    coverage_for_prepared,
    merge_candidate_evidence,
    merge_coverage,
    merge_evidence_envelopes,
    merge_event_status,
    merge_incident_metadata,
    serialize_evidence_envelopes,
    sum_timings,
)
from app.services.trips import event_crowd, scoring
from app.services.trips.itinerary import build_chained_itinerary
from app.services.trips.transfer_semantics import (
    route_accessibility,
    route_transfer_facts,
    route_walking_totals,
)

ROUTE_STATUSES = {
    "good",
    "degraded_usable",
    "all_materially_degraded",
    "no_hard_constraint_match",
    "insufficient_coverage",
}

_VALID_COVERAGE_STATUSES = {
    "current",
    "partial",
    "stale",
    "unavailable",
    "unscanned",
    "not_required",
}


@dataclass
class AggregatePreparation:
    parsed_routes: list[list[dict]]
    scored: list[dict]
    aggregate_segments: list[list[dict]]
    origin_place: ResolvedPlace
    destination_place: ResolvedPlace
    relevant_alerts: list[dict]
    event_impacts: list[dict]
    event_failures: list[str]
    event_evidence_status: str
    incident_scan_metadata: dict
    evidence_envelopes: dict[str, Any]
    crowd_search_metadata: dict
    collect_crowd_evidence: bool
    incidents: list[dict]
    coverage: dict[str, str]
    timings: dict[str, float]
    candidate_evidence: list[dict[str, Any]]


@dataclass
class PreparedChain:
    legs: list[tuple[PreparedLeg, int]]
    score: float


def serialize_place(place: ResolvedPlace) -> dict[str, Any]:
    return {
        "name": place.name,
        "latitude": place.latitude,
        "longitude": place.longitude,
        "source": place.source,
        "address": place.address,
        "place_id": place.place_id,
    }


def route_constraints(route: list[dict], tool_input: dict[str, Any]) -> dict[str, Any]:
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
        intelligence.normalize_route_ids(
            tool_input.get("excluded_route_ids") or []
        )
    )
    required = {
        str(value).strip().upper()
        for value in tool_input.get("required_route_ids") or []
        if str(value).strip()
    }
    street_seconds, in_station_seconds = route_walking_totals(route)
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
    return {
        "satisfied": not violations,
        "violations": violations,
        "accessibility_status": accessibility,
        "street_walking_seconds": street_seconds,
        "in_station_transfer_seconds": in_station_seconds,
        "route_modes": sorted(route_modes),
        "route_ids": sorted(route_ids),
    }


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
) -> dict[str, Any]:
    street_seconds, in_station_seconds = route_walking_totals(route)
    index = int(score.get("index") or 0)
    return {
        "candidate_id": candidate_id,
        "duration_minutes": int(score.get("total_minutes") or 0),
        "walking_minutes": round(street_seconds / 60),
        "street_walking_minutes": round(street_seconds / 60),
        "in_station_transfer_minutes": round(in_station_seconds / 60),
        "transfers": int(score.get("transfers") or 0),
        "transit_lines": _route_lines(route),
        "arrival_context": _arrival_context(route, prepared_arrival_by),
        "official_service_impacts": _alerts(alerts),
        "confirmed_incident_impacts": _incidents(incidents),
        "unconfirmed_material_claims": [],
        "event_or_crowd_impacts": [
            {
                "event_name": impact.get("title"),
                "venue_name": impact.get("venue"),
                "risk_score": impact.get("risk_score"),
            }
            for impact in event_impacts
            if impact.get("route_index") == index
        ][:3],
        "accessibility_status": hard_constraints.get("accessibility_status", "unknown"),
        "transfer_facts": _transfer_digests(route),
        "hard_constraints_satisfied": bool(hard_constraints.get("satisfied")),
        "hard_constraint_violations": list(hard_constraints.get("violations") or []),
        "score_summary": {
            "estimated_duration": score.get("total_minutes"),
            "reliability": _reliability(score),
        },
    }


def route_status(
    *,
    candidates: list[dict],
    coverage: dict[str, str],
    incident_impacts: list[dict],
) -> str:
    if not candidates:
        return "insufficient_coverage"
    if not any(item.get("hard_constraints_satisfied") is True for item in candidates):
        return "no_hard_constraint_match"
    covered = [value for value in coverage.values() if value in _VALID_COVERAGE_STATUSES]
    # not_required is neutral: it is neither positive evidence nor degradation,
    # so a map with only neutral or unusable coverage stays insufficient.
    if not covered or all(
        value in {"unavailable", "unscanned", "not_required"} for value in covered
    ):
        return "insufficient_coverage"
    usable = [item for item in candidates if item.get("hard_constraints_satisfied") is True]
    materially_degraded = [
        item
        for item in usable
        if item.get("official_service_impacts")
        or item.get("confirmed_incident_impacts")
        or any(float(i.get("risk_score") or 0) > 0 for i in item.get("event_or_crowd_impacts") or [])
    ]
    if usable and len(materially_degraded) == len(usable):
        return "all_materially_degraded"
    if any(value in {"partial", "stale", "unavailable", "unscanned"} for value in covered):
        return "degraded_usable"
    return "good"


def combine_prepared_chains(
    chains: list[PreparedChain],
    *,
    waypoints: list[str],
    destination_raw: str,
    dwell_minutes: int,
    dwell_source: str,
) -> AggregatePreparation:
    if not chains or not all(chain.legs for chain in chains):
        raise ValueError("at least one prepared route chain is required")

    parsed_routes: list[list[dict]] = []
    scored: list[dict] = []
    aggregate_segments: list[list[dict]] = []
    candidate_evidence: list[dict[str, Any]] = []
    aggregate_impacts: list[dict] = []
    for aggregate_index, chain in enumerate(chains):
        flat: list[dict] = []
        segments: list[dict] = []
        evidence_groups: list[dict[str, Any]] = []
        for segment_index, (leg, route_index) in enumerate(chain.legs):
            route = copy.deepcopy(leg.parsed_routes[route_index])
            flat.extend(_flatten_segment(route, segment_index))
            evidence = candidate_evidence_for_route(
                leg,
                route_index=route_index,
                aggregate_index=aggregate_index,
                segment_index=segment_index,
            )
            evidence_groups.append(evidence)
            aggregate_impacts.extend(evidence["event_impacts"])
            segments.append(
                {
                    "steps": route,
                    "origin_place": _place_for_segment(leg.origin_place),
                    "destination_place": _place_for_segment(leg.destination_place),
                    **(
                        {"dwell_minutes": dwell_minutes, "dwell_source": dwell_source}
                        if segment_index < len(chain.legs) - 1
                        else {}
                    ),
                }
            )
        canonical = build_chained_itinerary(
            segments,
            origin=segments[0]["origin_place"],
            final_destination=segments[-1]["destination_place"],
        )
        local_scores = [
            next(
                (value for value in leg.scored if int(value.get("index", -1)) == route_index),
                {"score": 0, "transfers": 0},
            )
            for leg, route_index in chain.legs
        ]
        canonical_transfers = int(canonical.get("transfer_count") or 0)
        total_minutes = round(int(canonical["total_duration_seconds"]) / 60)
        evidence = merge_candidate_evidence(evidence_groups)
        alert_hits = scoring._route_alert_hits(flat, evidence.get("alerts"))
        event_penalty = event_crowd.route_event_penalty(
            aggregate_index,
            _distinct_event_impacts(evidence.get("event_impacts")),
        )
        walking_penalty = _penalty_total(local_scores, "walking_penalty")
        preferred_mode_penalty = _penalty_total(local_scores, "preferred_mode_penalty")
        parsed_routes.append(flat)
        scored.append(
            {
                "index": aggregate_index,
                "score": scoring._component_score_total(
                    total_minutes=total_minutes, transfers=canonical_transfers,
                    alert_count=len(alert_hits), event_crowd_penalty=event_penalty,
                    walking_penalty=walking_penalty,
                    preferred_mode_penalty=preferred_mode_penalty,
                ),
                "total_minutes": total_minutes,
                "transfers": canonical_transfers,
                "alert_count": len(alert_hits),
                "transit_count": len(scoring._route_lines(flat)),
                "alerts": alert_hits[:2],
                "event_crowd_penalty": event_penalty,
                "street_walking_seconds": int(canonical.get("total_street_walking_seconds") or 0),
                "in_station_transfer_seconds": int(
                    canonical.get("total_in_station_transfer_seconds") or 0
                ),
                "walk_minutes": round(int(canonical.get("total_street_walking_seconds") or 0) / 60),
                "walking_penalty": walking_penalty,
                "preferred_mode_penalty": preferred_mode_penalty,
                "accessibility_status": route_accessibility(flat),
                "rank": aggregate_index + 1,
            }
        )
        aggregate_segments.append(segments)
        candidate_evidence.append(evidence)

    # Rank from final canonical score, not the incoming beam order; rows stay index-aligned.
    for position, row in enumerate(
        sorted(
            scored,
            key=lambda value: (
                value["score"], value["total_minutes"], value["transfers"], value["index"],
            ),
        )
    ):
        row["rank"] = position + 1

    all_legs = [leg for chain in chains for leg, _index in chain.legs]
    first = chains[0].legs[0][0]
    last = chains[0].legs[-1][0]
    incidents = _merge_dicts(leg.incidents for leg in all_legs)
    alerts = _merge_dicts(leg.relevant_alerts for leg in all_legs)
    failures = [failure for leg in all_legs for failure in leg.event_failures]
    return AggregatePreparation(
        parsed_routes=parsed_routes,
        scored=scored,
        aggregate_segments=aggregate_segments,
        origin_place=first.origin_place,
        destination_place=last.destination_place,
        relevant_alerts=alerts,
        event_impacts=_merge_dicts(aggregate_impacts),
        event_failures=failures,
        event_evidence_status=merge_event_status(all_legs),
        incident_scan_metadata=merge_incident_metadata(all_legs),
        evidence_envelopes=merge_evidence_envelopes(
            leg.evidence_envelopes for leg in all_legs
        ),
        crowd_search_metadata=dict(first.crowd_search_metadata),
        collect_crowd_evidence=any(leg.collect_crowd_evidence for leg in all_legs),
        incidents=incidents,
        coverage=merge_coverage(all_legs),
        timings=sum_timings(all_legs),
        candidate_evidence=candidate_evidence,
    )


def _place_for_segment(place: ResolvedPlace) -> dict[str, Any]:
    point = place.to_event_point()
    point["name"] = place.name
    return point


def _flatten_segment(route: list[dict], segment_index: int) -> list[dict]:
    """Keep semantic transfer group IDs unique after segment concatenation."""

    flattened: list[dict] = []
    for raw_step in route:
        step = {**raw_step, "segment_index": segment_index}
        group_id = str(step.get("semantic_transfer_group_id") or "").strip()
        if group_id:
            scoped_group_id = f"segment_{segment_index}_{group_id}"
            step["semantic_transfer_group_id"] = scoped_group_id
            for key in ("transfer_semantics", "semantic_transfer"):
                fact = step.get(key)
                if isinstance(fact, dict):
                    step[key] = {**fact, "group_id": scoped_group_id}
        flattened.append(step)
    return flattened


def _walking_limit(tool_input: dict[str, Any]) -> int | None:
    for key in ("max_walking_minutes", "walking_tolerance_minutes"):
        value = tool_input.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return max(0, min(180, int(round(value))))
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
            "street_walking_seconds": int(fact.get("street_walking_seconds") or 0),
            "in_station_transfer_seconds": int(
                fact.get("in_station_transfer_seconds") or 0
            ),
            "accessibility": fact.get("accessibility") or "unknown",
        }
        for fact in route_transfer_facts(route)
    ][:4]


def _arrival_context(route: list[dict], arrival_by: str | None) -> str:
    last = route[-1] if route else {}
    if last.get("arrival_time_iso"):
        return "arrival time estimated from live schedules"
    return "timed for the requested arrival" if arrival_by else "arrival time unavailable"


def _alerts(alerts: list[dict]) -> list[str]:
    return [
        str(alert.get("header") or alert.get("description") or "").strip()[:120]
        for alert in alerts
        if str(alert.get("header") or alert.get("description") or "").strip()
    ][:3]


def _incidents(incidents: list[dict]) -> list[str]:
    return [
        str(item.get("description") or item.get("location") or "").strip()[:160]
        for item in incidents
        if str(item.get("description") or item.get("location") or "").strip()
    ][:3]


def _reliability(score: dict[str, Any]) -> str:
    return (
        "medium"
        if int(score.get("alert_count") or 0) > 0
        or float(score.get("event_crowd_penalty") or 0) > 0
        else "high"
    )


def _penalty_total(local_scores: list[dict], field: str) -> int:
    """Sum one per-leg additive penalty preserved in the aggregate score."""

    return sum(int(value.get(field) or 0) for value in local_scores)


def _distinct_event_impacts(impacts: list[dict]) -> list[dict]:
    """Keep one exposure row per event so a cross-leg event is never double-counted."""

    distinct: dict[str, dict] = {}
    result: list[dict] = []
    for impact in impacts or []:
        if not isinstance(impact, dict):
            continue
        event_id = str(impact.get("event_id") or "").strip()
        if not event_id:
            result.append(impact)
            continue
        previous = distinct.get(event_id)
        if previous is None or float(impact.get("risk_score") or 0) > float(
            previous.get("risk_score") or 0
        ):
            distinct[event_id] = impact
    result.extend(distinct.values())
    return result


def _merge_dicts(groups) -> list[dict]:
    merged: list[dict] = []
    for group in groups:
        if isinstance(group, dict):
            group = [group]
        for value in group or []:
            if isinstance(value, dict) and value not in merged:
                merged.append(value)
    return merged


__all__ = (
    "AggregatePreparation",
    "PreparedChain",
    "ROUTE_STATUSES",
    "candidate_digest",
    "combine_prepared_chains",
    "coverage_for_prepared",
    "route_constraints",
    "route_status",
    "serialize_evidence_envelopes",
    "serialize_place",
)
