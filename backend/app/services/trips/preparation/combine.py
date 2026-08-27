"""Combine prepared legs into index-aligned route candidate chains."""

from __future__ import annotations

import copy
from typing import Any

from app.services.trips import scoring
from app.services.trips.crowds import event as event_crowd
from app.services.trips.preparation.evidence import (
    candidate_evidence_for_route,
    merge_candidate_evidence,
    merge_coverage,
    merge_event_status,
    merge_evidence_envelopes,
    merge_incident_metadata,
    sum_timings,
)
from app.services.trips.preparation.prepare import (
    AggregatePreparation,
    PreparedChain,
    PreparedLeg,
)
from app.services.trips.transfer_semantics import route_accessibility


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
    candidate_destinations = []
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
        from app.services.trips.itinerary import build_chained_itinerary

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
        total_minutes = round(int(canonical["total_duration_seconds"]) / 60)
        evidence = merge_candidate_evidence(evidence_groups)
        evidence["unconfirmed_material_claims"] = _merge_dicts(
            value.get("unconfirmed_material_claims") for value in evidence_groups
        )[:3]
        evidence["evidence_coverage"] = _merge_coverage(
            value.get("evidence_coverage") for value in evidence_groups
        )
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
                    total_minutes=total_minutes,
                    transfers=int(canonical.get("transfer_count") or 0),
                    alert_count=len(alert_hits),
                    event_crowd_penalty=event_penalty,
                    walking_penalty=walking_penalty,
                    preferred_mode_penalty=preferred_mode_penalty,
                    alert_penalty=scoring._route_alert_penalty(flat, evidence.get("alerts")),
                ),
                "total_minutes": total_minutes,
                "transfers": int(canonical.get("transfer_count") or 0),
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
        candidate_destinations.append(chain.legs[-1][0].destination_place)

    for position, row in enumerate(
        sorted(
            scored,
            key=lambda value: (
                value["score"],
                value["total_minutes"],
                value["transfers"],
                value["index"],
            ),
        ),
    ):
        row["rank"] = position + 1
    all_legs: list[PreparedLeg] = []
    seen_legs: set[int] = set()
    for chain in chains:
        for leg, _index in chain.legs:
            if id(leg) not in seen_legs:
                seen_legs.add(id(leg))
                all_legs.append(leg)
    first = chains[0].legs[0][0]
    last = chains[0].legs[-1][0]
    return AggregatePreparation(
        parsed_routes=parsed_routes,
        scored=scored,
        aggregate_segments=aggregate_segments,
        origin_place=first.origin_place,
        destination_place=last.destination_place,
        relevant_alerts=_merge_dicts(leg.relevant_alerts for leg in all_legs),
        event_impacts=_merge_dicts(aggregate_impacts),
        event_failures=[failure for leg in all_legs for failure in leg.event_failures],
        event_evidence_status=merge_event_status(all_legs),
        incident_scan_metadata=merge_incident_metadata(all_legs),
        evidence_envelopes=merge_evidence_envelopes(leg.evidence_envelopes for leg in all_legs),
        crowd_search_metadata=dict(first.crowd_search_metadata),
        collect_crowd_evidence=any(leg.collect_crowd_evidence for leg in all_legs),
        incidents=_merge_dicts(leg.incidents for leg in all_legs),
        coverage=merge_coverage(all_legs),
        timings=sum_timings(all_legs),
        candidate_evidence=candidate_evidence,
        candidate_destinations=candidate_destinations,
    )


def _place_for_segment(place) -> dict[str, Any]:
    """Project endpoint facts for segment display without selection identity."""

    return {
        "name": place.name,
        "address": place.address,
        "lat": place.latitude,
        "lng": place.longitude,
        "source": place.source,
    }


def _flatten_segment(route: list[dict], segment_index: int) -> list[dict]:
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


def _penalty_total(local_scores: list[dict], field: str) -> int:
    return sum(int(value.get(field) or 0) for value in local_scores)


def _distinct_event_impacts(impacts: list[dict]) -> list[dict]:
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


def _merge_coverage(groups) -> dict[str, str]:
    merged: dict[str, str] = {}
    for group in groups:
        for key, value in (group or {}).items():
            key = str(key).strip()
            value = str(value).strip()
            if not key or value not in {
                "current",
                "partial",
                "stale",
                "unavailable",
                "unscanned",
                "not_required",
            }:
                continue
            previous = merged.get(key)
            if previous is None or _coverage_rank(value) > _coverage_rank(previous):
                merged[key] = value
    return merged


def _coverage_rank(value: str) -> int:
    return {
        "current": 0,
        "partial": 1,
        "stale": 2,
        "unavailable": 3,
        "unscanned": 4,
        "not_required": -1,
    }.get(value, 4)


__all__ = ("combine_prepared_chains",)
