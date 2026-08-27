"""Server-owned destination-branch resolution and route preparation."""

from __future__ import annotations

import math
from typing import Any

from app.services import geography as geo
from app.services.agent import candidate_store, discovery_store
from app.services.agent.tools._types import ToolContext, ToolResult
from app.services.agent.tools.location_resolution import (
    ResolvedPlace,
    resolve_destination_reference,
)
from app.services.agent.tools.route.preparation_adapter import (
    PreparedLeg,
    build_preparation_dependencies,
    new_preparation_timings,
    prepare_single_leg,
)
from app.services.trips.preparation.combine import combine_prepared_chains
from app.services.trips.preparation.prepare import (
    AggregatePreparation,
    PreparedChain,
)

MAX_DESTINATION_OPTIONS = 8


def limit_final_branch_chains(
    chains: list[PreparedChain],
    aggregate: AggregatePreparation,
    tool_input: dict[str, Any],
    *,
    narrow_pool: bool = False,
) -> list[PreparedChain]:
    """Build a reasonable branch pool, then apply the shared model budget."""

    budget = candidate_budget(tool_input)
    candidate_indexes = list(range(len(chains)))
    if narrow_pool:
        candidate_indexes = reasonable_branch_indexes(chains, aggregate)
    if len(candidate_indexes) <= budget:
        return [chains[index] for index in candidate_indexes]

    groups = _branch_candidate_groups(chains, aggregate, candidate_indexes)
    selected = _mandatory_branch_indexes(groups, aggregate, budget)
    selected = _fill_valid_branch_budget(
        selected,
        candidate_indexes,
        aggregate,
        budget,
    )
    selected.sort()
    return [chains[index] for index in selected]


def reasonable_branch_indexes(
    chains: list[PreparedChain], aggregate: AggregatePreparation
) -> list[int]:
    """Drop a data-derived route-time outlier from a current-location pool."""

    all_indexes = list(range(len(chains)))
    groups = _branch_candidate_groups(chains, aggregate, all_indexes)
    if len(groups) < 2:
        return all_indexes
    best_by_branch = _best_stage_a_branch_times(groups, aggregate)
    if best_by_branch is None or len(best_by_branch) < 2:
        return all_indexes
    keep_branches = _reasonable_branch_ids(best_by_branch)
    if keep_branches is None:
        return all_indexes
    return [
        index
        for index in all_indexes
        if _branch_key(chains, aggregate, index) in keep_branches
    ]


def stage_a_factors(
    aggregate: AggregatePreparation,
    reasonable_indexes: list[int],
    tool_input: dict[str, Any],
) -> list[dict[str, Any]]:
    raw_limit = tool_input.get("walking_tolerance_minutes")
    try:
        walking_limit = float(raw_limit)
    except (TypeError, ValueError):
        return []
    if walking_limit < 0 or not reasonable_indexes:
        return []
    for index in reasonable_indexes:
        if index >= len(aggregate.candidate_constraints):
            return []
        try:
            walking_seconds = float(
                aggregate.candidate_constraints[index].get(
                    "street_walking_seconds", float("inf")
                )
            )
        except (TypeError, ValueError):
            return []
        if walking_seconds > walking_limit * 60:
            return []
    return [
        {
            "code": "reasonable_local_option",
            "status": "validated",
            "basis": [
                "current_location",
                "canonical_total_travel",
                "walking_tolerance",
            ],
        }
    ]


def candidate_budget(tool_input: dict[str, Any]) -> int:
    try:
        return max(
            1,
            min(
                candidate_store.MAX_CANDIDATES,
                int(tool_input.get("max_candidates") or 5),
            ),
        )
    except (TypeError, ValueError):
        return 5


def _branch_candidate_groups(
    chains: list[PreparedChain],
    aggregate: AggregatePreparation,
    indexes: list[int],
) -> dict[str, list[int]]:
    groups: dict[str, list[int]] = {}
    for index in indexes:
        groups.setdefault(_branch_key(chains, aggregate, index), []).append(index)
    return groups


def _final_branch_rank(
    aggregate: AggregatePreparation,
    index: int,
) -> tuple[int, float, float, float, int]:
    constraints = (
        aggregate.candidate_constraints[index]
        if index < len(aggregate.candidate_constraints)
        else {}
    )
    return (
        0 if constraints.get("satisfied") is True else 1,
        *_canonical_stage_a_key(aggregate, index),
    )


def _mandatory_branch_indexes(
    groups: dict[str, list[int]],
    aggregate: AggregatePreparation,
    budget: int,
) -> list[int]:
    mandatory: list[int] = []
    for indexes in groups.values():
        valid = [
            index for index in indexes if _final_branch_rank(aggregate, index)[0] == 0
        ]
        if valid:
            mandatory.append(
                min(valid, key=lambda index: _final_branch_rank(aggregate, index))
            )
    return sorted(
        mandatory,
        key=lambda index: _final_branch_rank(aggregate, index),
    )[:budget]


def _fill_valid_branch_budget(
    selected: list[int],
    candidate_indexes: list[int],
    aggregate: AggregatePreparation,
    budget: int,
) -> list[int]:
    if len(selected) >= budget:
        return selected
    selected_set = set(selected)
    remaining = sorted(
        (
            index
            for index in candidate_indexes
            if index not in selected_set
            and _final_branch_rank(aggregate, index)[0] == 0
        ),
        key=lambda index: _final_branch_rank(aggregate, index),
    )
    selected.extend(remaining[: budget - len(selected)])
    return selected


def _best_stage_a_branch_times(
    groups: dict[str, list[int]],
    aggregate: AggregatePreparation,
) -> list[tuple[float, str]] | None:
    best_by_branch: list[tuple[float, str]] = []
    for branch_id, indexes in groups.items():
        valid = [
            index
            for index in indexes
            if index < len(aggregate.candidate_constraints)
            and aggregate.candidate_constraints[index].get("satisfied") is True
        ]
        if not valid:
            continue
        best = min(valid, key=lambda index: _canonical_stage_a_key(aggregate, index))
        total_seconds = _canonical_stage_a_key(aggregate, best)[0]
        if not math.isfinite(total_seconds):
            return None
        best_by_branch.append((total_seconds, branch_id))
    return best_by_branch


def _reasonable_branch_ids(
    best_by_branch: list[tuple[float, str]],
) -> set[str] | None:
    ordered = sorted(best_by_branch, key=lambda item: (item[0], item[1]))
    gaps = [
        (ordered[index + 1][0] - ordered[index][0], index + 1)
        for index in range(len(ordered) - 1)
    ]
    largest_gap, gap_end = max(gaps, key=lambda item: (item[0], -item[1]))
    lower_scores = [score for score, _branch_id in ordered[:gap_end]]
    median_lower = lower_scores[len(lower_scores) // 2]
    if largest_gap <= median_lower:
        return None
    return {branch_id for _score, branch_id in ordered[:gap_end]}


def _branch_key(
    chains: list[PreparedChain], aggregate: AggregatePreparation, index: int
) -> str:
    destination = (
        aggregate.candidate_destinations[index]
        if index < len(aggregate.candidate_destinations)
        else chains[index].legs[-1][0].destination_place
    )
    return str(destination.place_id or destination.name)


def _canonical_stage_a_key(
    aggregate: AggregatePreparation, index: int
) -> tuple[float, float, float, int]:
    """Order candidates using canonical travel facts, never the private score."""

    itinerary = (
        aggregate.candidate_itineraries[index]
        if index < len(aggregate.candidate_itineraries)
        else {}
    )
    total_seconds = _finite_nonnegative(itinerary.get("total_duration_seconds"))
    walking_seconds = _finite_nonnegative(itinerary.get("total_street_walking_seconds"))
    distance = _destination_distance_meters(aggregate, index)
    return total_seconds, distance, walking_seconds, index


def _destination_distance_meters(aggregate: AggregatePreparation, index: int) -> float:
    if index >= len(aggregate.candidate_destinations):
        return float("inf")
    origin = aggregate.origin_place
    destination = aggregate.candidate_destinations[index]
    coordinates = (
        origin.latitude,
        origin.longitude,
        destination.latitude,
        destination.longitude,
    )
    if not all(_finite_coordinate(value) for value in coordinates):
        return float("inf")
    distance = geo.distance_meters(*coordinates)
    return distance if math.isfinite(distance) and distance >= 0 else float("inf")


def _finite_nonnegative(value: Any) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return float("inf")
    return number if math.isfinite(number) and number >= 0 else float("inf")


def _finite_coordinate(value: Any) -> bool:
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError):
        return False


async def prepare_destination_branches(
    destination_options: list[tuple[ResolvedPlace, str | None]],
    merged: dict[str, Any],
    ctx: ToolContext,
) -> tuple[
    AggregatePreparation | ToolResult,
    list[PreparedChain],
    list[dict[str, str]],
]:
    """Prepare every verified branch while preserving per-branch coverage."""

    prepared_options: list[PreparedLeg] = []
    preparation_error: ToolResult | None = None
    branch_coverage: list[dict[str, str]] = []
    dependencies = build_preparation_dependencies()
    for destination_place, destination_id in destination_options:
        branch_input = {**merged, "destination": destination_place.name}
        branch_prepared = await prepare_single_leg(
            branch_input,
            ctx,
            new_preparation_timings(),
            dependencies=dependencies,
            emit_comparing_progress=False,
            resolved_destination=destination_place,
        )
        branch_record = {
            "place_id": str(destination_id or destination_place.place_id or ""),
            "name": destination_place.name,
            "status": "available",
            "coverage": "available",
        }
        if isinstance(branch_prepared, ToolResult):
            branch_record.update(status="unavailable", coverage="unavailable")
            branch_coverage.append(branch_record)
            preparation_error = branch_prepared
            continue
        branch_coverage.append(branch_record)
        prepared_options.append(branch_prepared)

    if not prepared_options:
        return (
            preparation_error
            or ToolResult(
                ok=False, error="no transit route found between those points"
            ),
            [],
            branch_coverage,
        )
    chains = [
        PreparedChain(
            legs=[(leg, route_index)],
            score=float(
                next(
                    (
                        row.get("score", 0)
                        for row in leg.scored
                        if int(row.get("index", -1)) == route_index
                    ),
                    0,
                )
            ),
        )
        for leg in prepared_options
        for route_index in range(len(leg.parsed_routes))
    ]
    aggregate = combine_prepared_chains(
        chains,
        waypoints=[],
        destination_raw=merged["destination"],
        dwell_minutes=0,
        dwell_source="user",
    )
    return aggregate, chains, branch_coverage


async def resolve_destination_options(
    tool_input: dict,
    merged: dict,
    ctx: ToolContext,
) -> tuple[
    list[tuple[ResolvedPlace, str | None]],
    ResolvedPlace | None,
    str | None,
    str | None,
    str | None,
]:
    """Resolve one destination or a server-owned verified branch set."""

    raw_ids = tool_input.get("destination_place_ids")
    if raw_ids is None:
        resolved, place_id, error, set_id = await resolve_destination_reference(
            tool_input, merged, ctx
        )
        options = [(resolved, place_id)] if resolved is not None else []
        return options, resolved, place_id, error, set_id
    place_ids, validation_error = _validated_branch_ids(tool_input, raw_ids)
    if validation_error:
        return [], None, None, validation_error, None
    expanded = False
    while True:
        options, used_set, resolution_error = await _resolve_branch_options(
            place_ids,
            tool_input,
            merged,
            ctx,
        )
        if resolution_error:
            return [], None, None, resolution_error, None
        if expanded or not used_set:
            break
        verified_ids = _verified_discovery_ids(used_set, ctx)
        next_ids, expansion_error = _expanded_branch_ids(
            place_ids,
            verified_ids,
            candidate_budget(tool_input),
        )
        if expansion_error:
            return [], None, None, expansion_error, None
        if next_ids is None:
            break
        place_ids = next_ids
        expanded = True
    if len(options) < 2:
        return (
            [],
            None,
            None,
            "destination_place_ids is comparison-only; provide at least two ids",
            None,
        )
    return options, options[0][0], None, None, used_set


def _validated_branch_ids(
    tool_input: dict[str, Any], raw_ids: Any
) -> tuple[list[str], str | None]:
    if str(tool_input.get("destination_place_id") or "").strip():
        return [], "provide destination_place_ids or destination_place_id, not both"
    if not isinstance(raw_ids, list):
        return [], "destination_place_ids must be an array"
    place_ids: list[str] = []
    for raw_id in raw_ids:
        place_id = str(raw_id or "").strip()
        if place_id and place_id not in place_ids:
            place_ids.append(place_id)
    if not place_ids:
        return [], "destination_place_ids cannot be empty"
    if len(place_ids) < 2:
        return [], "destination_place_ids is comparison-only; provide at least two ids"
    budget = candidate_budget(tool_input)
    if len(place_ids) > min(MAX_DESTINATION_OPTIONS, budget):
        return [], f"compare at most {budget} verified branch places"
    return place_ids, None


async def _resolve_branch_options(
    place_ids: list[str],
    tool_input: dict[str, Any],
    merged: dict[str, Any],
    ctx: ToolContext,
) -> tuple[list[tuple[ResolvedPlace, str]], str | None, str | None]:
    options: list[tuple[ResolvedPlace, str]] = []
    used_set: str | None = None
    for place_id in place_ids:
        branch_input = {
            **tool_input,
            "destination": "",
            "destination_place_id": place_id,
        }
        branch_merged = {**merged, "destination": ""}
        resolved, _resolved_id, error, set_id = await resolve_destination_reference(
            branch_input, branch_merged, ctx
        )
        if error or resolved is None:
            return [], None, error or "destination place reference is invalid"
        if not set_id:
            return (
                [],
                None,
                "destination branch places must come from the current discovery set",
            )
        if used_set is None:
            used_set = set_id
        elif set_id != used_set:
            return (
                [],
                None,
                "destination branch places must come from one discovery set",
            )
        options.append((resolved, place_id))
    return options, used_set, None


def _verified_discovery_ids(discovery_set_id: str, ctx: ToolContext) -> list[str]:
    record = discovery_store.load_discovery_set(
        discovery_set_id,
        session_id=_session_id(ctx),
    )
    return [
        str(place.get("place_id") or "").strip()
        for place in (record or {}).get("places") or []
        if isinstance(place, dict) and str(place.get("place_id") or "").strip()
    ]


def _expanded_branch_ids(
    place_ids: list[str], verified_ids: list[str], budget: int
) -> tuple[list[str] | None, str | None]:
    if not verified_ids or set(verified_ids) == set(place_ids):
        return None, None
    if len(verified_ids) > min(MAX_DESTINATION_OPTIONS, budget):
        return None, f"compare at most {budget} verified branch places"
    return verified_ids, None


def is_current_location_discovery(
    ctx: ToolContext, discovery_set_id: str | None
) -> bool:
    record = discovery_store.load_discovery_set(
        str(discovery_set_id or ""),
        session_id=_session_id(ctx),
    )
    scope = record.get("search_scope") if isinstance(record, dict) else None
    return isinstance(scope, dict) and scope.get("kind") == "current_location"


def aggregate_destination_ids(aggregate: AggregatePreparation) -> list[str]:
    ids: list[str] = []
    for place in aggregate.candidate_destinations:
        place_id = str(place.place_id or "").strip()
        if place_id and place_id not in ids:
            ids.append(place_id)
    return ids


def _session_id(ctx: ToolContext) -> str:
    return str(getattr(ctx, "session_id", None) or "").strip()


__all__ = (
    "MAX_DESTINATION_OPTIONS",
    "aggregate_destination_ids",
    "candidate_budget",
    "is_current_location_discovery",
    "limit_final_branch_chains",
    "prepare_destination_branches",
    "reasonable_branch_indexes",
    "resolve_destination_options",
    "stage_a_factors",
)
