"""Bounded, candidate-dependent preparation for ordered multi-stop trips."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta

from app.services.trips.preparation.prepare import PreparedLeg
from app.services.trips.preparation.combine import combine_prepared_chains
from app.services.trips.preparation.constraints import route_constraints
from app.services.trips.preparation.prepare import (
    AggregatePreparation,
    PreparedChain,
)
from app.services.trips.preparation.context import (
    RoutePreparationContext,
    RoutePreparationFailure,
    is_route_preparation_failure,
)
from app.services.trips import candidates

MULTI_STOP_BEAM_WIDTH = 3
MULTI_STOP_PROVIDER_WIDTH = 5
MAX_CANDIDATES = 8
DEFAULT_DWELL_MINUTES = 25

PrepareSegment = Callable[
    [dict, RoutePreparationContext],
    Awaitable[PreparedLeg | RoutePreparationFailure],
]


async def prepare_multi_stop(
    tool_input: dict,
    ctx: RoutePreparationContext,
    timings: dict[str, float],
    waypoints: list[str],
    *,
    prepare_segment: PrepareSegment,
    waypoint_labels: list[str] | None = None,
    destination_raw: str | None = None,
) -> AggregatePreparation | RoutePreparationFailure:
    destinations = [*waypoints, str(tool_input["destination"])]
    dwell_minutes, dwell_source = _dwell(tool_input.get("waypoint_dwell_minutes"))
    max_candidates = _max_candidates(tool_input.get("max_candidates"))
    beam_width = max(1, min(MULTI_STOP_BEAM_WIDTH, max_candidates))
    provider_width = max(1, min(MULTI_STOP_PROVIDER_WIDTH, max_candidates))
    previous_sink = ctx.progress_sink

    async def progress_without_intermediate_complete(stage: str, status: str) -> None:
        if (
            stage in {"finding_routes", "checking_live_conditions"}
            and status == "complete"
        ):
            return
        if previous_sink is not None:
            await previous_sink(stage, status)

    ctx.progress_sink = progress_without_intermediate_complete
    try:
        first_input = _segment_input(
            tool_input,
            origin=str(tool_input.get("origin") or "user"),
            destination=destinations[0],
            departure_time=tool_input.get("departure_time"),
        )
        first_input["max_candidates"] = provider_width
        first = await prepare_segment(first_input, ctx)
        if is_route_preparation_failure(first):
            return RoutePreparationFailure(
                f"could not prepare stop 1: {getattr(first, 'error', None) or 'routing failed'}"
            )
        partials = [
            PreparedChain(
                legs=[(first, route_index)],
                score=_route_score(first, route_index),
            )
            for route_index in _candidate_choices(first, provider_width)
        ]
        for segment_index, destination in enumerate(destinations[1:], start=1):
            expanded: list[PreparedChain] = []
            for partial in partials:
                previous_leg, previous_index = partial.legs[-1]
                departure_time = _next_departure_for_route(
                    previous_leg.parsed_routes[previous_index],
                    dwell_minutes,
                )
                leg_input = _segment_input(
                    tool_input,
                    origin=destinations[segment_index - 1],
                    destination=destination,
                    departure_time=departure_time,
                )
                leg_input["max_candidates"] = provider_width
                leg_input.pop("arrival_by", None)
                prepared = await prepare_segment(leg_input, ctx)
                if is_route_preparation_failure(prepared):
                    continue
                for route_index in _candidate_choices(prepared, provider_width):
                    expanded.append(
                        PreparedChain(
                            legs=[*partial.legs, (prepared, route_index)],
                            score=partial.score + _route_score(prepared, route_index),
                        )
                    )
            if not expanded:
                return RoutePreparationFailure(
                    f"could not prepare stop {segment_index + 1}: routing failed"
                )
            # Keep the provider's order as the model-visible order.  The
            # private composite score is only a fallback input after the
            # model has had its chance to choose; it must not decide which
            # multi-stop chains survive the beam.
            partials = _bounded_provider_order(expanded, beam_width)
    finally:
        ctx.progress_sink = previous_sink
    if previous_sink is not None:
        for stage in ("finding_routes", "checking_live_conditions"):
            await previous_sink(stage, "complete")
    return combine_prepared_chains(
        partials,
        waypoints=(waypoint_labels if waypoint_labels is not None else waypoints),
        destination_raw=(
            destination_raw
            if destination_raw is not None
            else str(tool_input.get("destination") or "")
        ),
        dwell_minutes=dwell_minutes,
        dwell_source=dwell_source,
    )


def _dwell(value: object) -> tuple[int, str]:
    if value is None:
        return DEFAULT_DWELL_MINUTES, "default"
    try:
        return max(0, min(180, int(round(float(value))))), "user"
    except (TypeError, ValueError):
        return DEFAULT_DWELL_MINUTES, "default"


def _next_departure_for_route(route: list[dict], dwell_minutes: int) -> str | None:
    for step in reversed(route):
        value = step.get("arrival_time_iso")
        if isinstance(value, str) and value.strip():
            try:
                return (
                    datetime.fromisoformat(value.replace("Z", "+00:00"))
                    + timedelta(minutes=dwell_minutes)
                ).isoformat()
            except ValueError:
                return None
    return None


def _segment_input(
    tool_input: dict,
    *,
    origin: str,
    destination: str,
    departure_time: object,
) -> dict:
    result = {
        key: value
        for key, value in tool_input.items()
        if key
        not in {
            "origin",
            "destination",
            "waypoints",
            "waypoint_dwell_minutes",
            "scenario",
        }
    }
    result.update({"origin": origin, "destination": destination})
    if departure_time:
        result["departure_time"] = departure_time
    else:
        result.pop("departure_time", None)
    return result


def _candidate_choices(prepared: PreparedLeg, width: int) -> list[int]:
    """Return bounded provider-order choices without score-shaped selection.

    A route row's private score is useful for deterministic fallback after the
    outer model fails to select a candidate.  It is not a valid reason to
    hide a route from the model, however.  We therefore preserve parsed
    provider order and apply only conservative dominance pruning.  A route is
    prunable only when it has the same route signature as an earlier choice,
    is no better on every available factual factor, and is strictly worse on
    at least one factor.  Missing factors disable dominance rather than
    guessing.
    """
    indexes = list(range(len(prepared.parsed_routes)))
    indexes = _retain_preliminary_hard_constraint_choices(prepared, indexes)
    indexes = _pareto_provider_order(
        indexes,
        lambda index: _route_facts(prepared, index),
    )
    return indexes[: max(1, width)]


def _route_score(prepared: PreparedLeg, route_index: int) -> float:
    return float(
        next(
            (
                row.get("score")
                for row in prepared.scored
                if int(row.get("index", -1)) == route_index
            ),
            0,
        )
        or 0
    )


_LOWER_IS_BETTER_FACTORS = (
    "total_minutes",
    "transfers",
    "street_walking_seconds",
    "in_station_transfer_seconds",
    "alert_penalty",
    "event_crowd_penalty",
    "preferred_mode_penalty",
)


def _bounded_provider_order(
    chains: list[PreparedChain],
    width: int,
) -> list[PreparedChain]:
    """Apply the same conservative frontier rule to the multi-stop beam."""

    frontier = _pareto_provider_order(
        list(range(len(chains))),
        lambda index: _chain_facts(chains[index]),
    )
    return [chains[index] for index in frontier[: max(1, width)]]


def _pareto_provider_order(
    indexes: list[int],
    facts_for: Callable[[int], dict[str, object]],
) -> list[int]:
    """Return a stable, conservatively pruned provider-order frontier.

    We intentionally do not use a weighted score here.  The route signature
    guard prevents a faster-looking route on one line from removing a
    different line that the Agent may reasonably prefer for an unrepresented
    factor.  The function is generic so the same rule applies to individual
    legs and partial chains without introducing another ranking abstraction.
    """

    kept: list[int] = []
    for candidate_index in indexes:
        candidate = facts_for(candidate_index)
        if any(_dominates(facts_for(other), candidate) for other in kept):
            continue
        kept = [other for other in kept if not _dominates(candidate, facts_for(other))]
        kept.append(candidate_index)
    return kept


def _dominates(left: dict[str, object], right: dict[str, object]) -> bool:
    if left.get("signature") != right.get("signature"):
        return False
    values: list[tuple[float, float]] = []
    for name in _LOWER_IS_BETTER_FACTORS:
        left_value = _finite_number(left.get(name))
        right_value = _finite_number(right.get(name))
        if left_value is None or right_value is None:
            return False
        values.append((left_value, right_value))
    return all(left_value <= right_value for left_value, right_value in values) and any(
        left_value < right_value for left_value, right_value in values
    )


def _route_facts(prepared: PreparedLeg, route_index: int) -> dict[str, object]:
    rows = {
        int(row.get("index")): row
        for row in prepared.scored
        if isinstance(row, dict) and "index" in row
    }
    row = rows.get(route_index, {})
    return {
        "signature": candidates.route_family_signature(
            prepared.parsed_routes[route_index]
        ),
        **{name: row.get(name) for name in _LOWER_IS_BETTER_FACTORS},
    }


def _chain_facts(chain: PreparedChain) -> dict[str, object]:
    rows = [_route_facts(leg, route_index) for leg, route_index in chain.legs]
    if not rows:
        return {"signature": ()}
    values: dict[str, object] = {"signature": tuple(row["signature"] for row in rows)}
    for name in _LOWER_IS_BETTER_FACTORS:
        numbers = [_finite_number(row.get(name)) for row in rows]
        values[name] = (
            sum(numbers) if all(value is not None for value in numbers) else None
        )
    return values


def _retain_preliminary_hard_constraint_choices(
    prepared: PreparedLeg,
    indexes: list[int],
) -> list[int]:
    """Prefer preliminary hard-constraint-valid routes when known.

    Arrival-by and canonical itinerary constraints are finalized later, so
    this check is deliberately conservative.  If every route appears invalid
    at this stage, retain all routes and let finalization produce the truthful
    no-good result rather than silently narrowing the evidence set.
    """
    valid = [
        index
        for index in indexes
        if route_constraints(
            prepared.parsed_routes[index],
            prepared.tool_input,
        ).get("satisfied")
        is True
    ]
    return valid or indexes


def _finite_number(value: object) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if number == number and abs(number) != float("inf") else None


def _max_candidates(value: object) -> int:
    try:
        return max(1, min(MAX_CANDIDATES, int(value or 5)))
    except (TypeError, ValueError):
        return 5
