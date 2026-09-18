"""Combine prepared legs into index-aligned route candidate chains."""

from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import Any

from app.services.trips import scoring
from app.services.trips.crowds import event as event_crowd
from app.services.trips.itinerary import build_chained_itinerary
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


@dataclass(frozen=True)
class _ChainBuild:
    flat: list[dict]
    segments: list[dict]
    evidence_groups: list[dict[str, Any]]
    event_impacts: list[dict]
    destination_place: Any


@dataclass(frozen=True)
class _AssembledChain:
    flat: list[dict]
    segments: list[dict]
    evidence: dict[str, Any]
    score: dict[str, Any]
    destination_place: Any
    event_impacts: list[dict]


def combine_prepared_chains(
    chains: list[PreparedChain],
    *,
    waypoints: list[str],
    destination_raw: str,
    dwell_minutes: int,
    dwell_source: str,
) -> AggregatePreparation:
    del waypoints, destination_raw
    if not chains or not all(chain.legs for chain in chains):
        raise ValueError("at least one prepared route chain is required")
    assembled: list[_AssembledChain] = []
    for aggregate_index, chain in enumerate(chains):
        built = _chain_segments(chain, aggregate_index, dwell_minutes, dwell_source)
        score, evidence = _score_chain(chain, built, aggregate_index)
        assembled.append(
            _AssembledChain(
                flat=built.flat,
                segments=built.segments,
                evidence=evidence,
                score=score,
                destination_place=built.destination_place,
                event_impacts=built.event_impacts,
            )
        )
    ranked = sorted(
        (item.score for item in assembled),
        key=lambda value: (
            value["score"],
            value["total_minutes"],
            value["transfers"],
            value["index"],
        ),
    )
    for position, row in enumerate(ranked):
        row["rank"] = position + 1
    return _aggregate_from_chains(assembled, chains)


def _aggregate_from_chains(
    assembled: list[_AssembledChain],
    chains: list[PreparedChain],
) -> AggregatePreparation:
    all_legs = _unique_legs(chains)
    first = chains[0].legs[0][0]
    last = chains[0].legs[-1][0]
    return AggregatePreparation(
        parsed_routes=[item.flat for item in assembled],
        scored=[item.score for item in assembled],
        aggregate_segments=[item.segments for item in assembled],
        origin_place=first.origin_place,
        destination_place=last.destination_place,
        relevant_alerts=_merge_dicts(leg.relevant_alerts for leg in all_legs),
        event_impacts=_merged_chain_impacts(assembled),
        event_failures=_leg_failures(all_legs),
        event_evidence_status=merge_event_status(all_legs),
        incident_scan_metadata=merge_incident_metadata(all_legs),
        evidence_envelopes=merge_evidence_envelopes(
            leg.evidence_envelopes for leg in all_legs
        ),
        crowd_search_metadata=dict(first.crowd_search_metadata),
        collect_crowd_evidence=any(leg.collect_crowd_evidence for leg in all_legs),
        incidents=_merge_dicts(leg.incidents for leg in all_legs),
        coverage=merge_coverage(all_legs),
        timings=sum_timings(all_legs),
        candidate_evidence=[item.evidence for item in assembled],
        candidate_destinations=[item.destination_place for item in assembled],
    )


def _merged_chain_impacts(assembled: list[_AssembledChain]) -> list[dict]:
    return _merge_dicts(
        impact for item in assembled for impact in item.event_impacts
    )


def _leg_failures(all_legs: list[PreparedLeg]) -> list:
    return [failure for leg in all_legs for failure in leg.event_failures]


def _chain_segments(
    chain: PreparedChain,
    aggregate_index: int,
    dwell_minutes: int,
    dwell_source: str,
) -> _ChainBuild:
    flat: list[dict] = []
    segments: list[dict] = []
    evidence_groups: list[dict[str, Any]] = []
    event_impacts: list[dict] = []
    last_index = len(chain.legs) - 1
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
        event_impacts.extend(evidence["event_impacts"])
        dwell = (
            {"dwell_minutes": dwell_minutes, "dwell_source": dwell_source}
            if segment_index < last_index
            else {}
        )
        segments.append(
            {
                "steps": route,
                "origin_place": _place_for_segment(leg.origin_place),
                "destination_place": _place_for_segment(leg.destination_place),
                **dwell,
            }
        )
    return _ChainBuild(
        flat=flat,
        segments=segments,
        evidence_groups=evidence_groups,
        event_impacts=event_impacts,
        destination_place=chain.legs[-1][0].destination_place,
    )


def _score_chain(
    chain: PreparedChain,
    built: _ChainBuild,
    aggregate_index: int,
) -> tuple[dict[str, Any], dict[str, Any]]:
    canonical = build_chained_itinerary(
        built.segments,
        origin=built.segments[0]["origin_place"],
        final_destination=built.segments[-1]["destination_place"],
    )
    local_scores = [_local_score(leg, route_index) for leg, route_index in chain.legs]
    total_minutes = round(int(canonical["total_duration_seconds"]) / 60)
    evidence = merge_candidate_evidence(built.evidence_groups)
    alert_hits = scoring.route_alert_hits(built.flat, evidence.get("alerts"))
    event_penalty = event_crowd.route_event_penalty(
        aggregate_index,
        _distinct_event_impacts(evidence.get("event_impacts")),
    )
    walking_penalty = _penalty_total(local_scores, "walking_penalty")
    preferred_mode_penalty = _penalty_total(local_scores, "preferred_mode_penalty")
    transfers = int(canonical.get("transfer_count") or 0)
    score = {
        "index": aggregate_index,
        "score": scoring.component_score_total(
            total_minutes=total_minutes,
            transfers=transfers,
            alert_count=len(alert_hits),
            event_crowd_penalty=event_penalty,
            walking_penalty=walking_penalty,
            preferred_mode_penalty=preferred_mode_penalty,
            alert_penalty=scoring.route_alert_penalty(
                built.flat, evidence.get("alerts")
            ),
        ),
        "total_minutes": total_minutes,
        "transfers": transfers,
        "alert_count": len(alert_hits),
        "transit_count": len(scoring._route_lines(built.flat)),
        "alerts": alert_hits[:2],
        "event_crowd_penalty": event_penalty,
        "street_walking_seconds": int(
            canonical.get("total_street_walking_seconds") or 0
        ),
        "in_station_transfer_seconds": int(
            canonical.get("total_in_station_transfer_seconds") or 0
        ),
        "walk_minutes": round(
            int(canonical.get("total_street_walking_seconds") or 0) / 60
        ),
        "walking_penalty": walking_penalty,
        "preferred_mode_penalty": preferred_mode_penalty,
        "accessibility_status": route_accessibility(built.flat),
        "rank": aggregate_index + 1,
    }
    return score, evidence


def _local_score(leg: PreparedLeg, route_index: int) -> dict:
    return next(
        (
            value
            for value in leg.scored
            if int(value.get("index", -1)) == route_index
        ),
        {"score": 0, "transfers": 0},
    )


def _unique_legs(chains: list[PreparedChain]) -> list[PreparedLeg]:
    all_legs: list[PreparedLeg] = []
    seen_legs: set[int] = set()
    for chain in chains:
        for leg, _index in chain.legs:
            if id(leg) in seen_legs:
                continue
            seen_legs.add(id(leg))
            all_legs.append(leg)
    return all_legs


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


__all__ = ("combine_prepared_chains",)
