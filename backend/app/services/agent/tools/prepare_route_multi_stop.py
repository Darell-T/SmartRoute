"""Bounded, candidate-dependent preparation for ordered multi-stop trips."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta

from app.services.agent.tools._types import ToolContext, ToolResult
from app.services.agent.tools.plan_trip_prepare import PreparedLeg
from app.services.agent.tools.route_option_assembly import (
    AggregatePreparation,
    PreparedChain,
    combine_prepared_chains,
)

MULTI_STOP_BEAM_WIDTH = 3
MULTI_STOP_PROVIDER_WIDTH = 5
MAX_CANDIDATES = 8
DEFAULT_DWELL_MINUTES = 25

PrepareSegment = Callable[
    [dict, ToolContext],
    Awaitable[PreparedLeg | ToolResult],
]


async def prepare_multi_stop(
    tool_input: dict,
    ctx: ToolContext,
    timings: dict[str, float],
    waypoints: list[str],
    *,
    prepare_segment: PrepareSegment,
    waypoint_labels: list[str] | None = None,
    destination_raw: str | None = None,
) -> AggregatePreparation | ToolResult:
    destinations = [*waypoints, str(tool_input["destination"])]
    dwell_minutes, dwell_source = _dwell(tool_input.get("waypoint_dwell_minutes"))
    max_candidates = _max_candidates(tool_input.get("max_candidates"))
    beam_width = max(1, min(MULTI_STOP_BEAM_WIDTH, max_candidates))
    provider_width = max(1, min(MULTI_STOP_PROVIDER_WIDTH, max_candidates))
    previous_sink = ctx.progress_sink

    async def progress_without_intermediate_complete(stage: str, status: str) -> None:
        if stage in {"finding_routes", "checking_live_conditions"} and status == "complete":
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
        if isinstance(first, ToolResult):
            return ToolResult(
                ok=False,
                error=f"could not prepare stop 1: {first.error or 'routing failed'}",
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
                if isinstance(prepared, ToolResult):
                    continue
                for route_index in _candidate_choices(prepared, provider_width):
                    expanded.append(
                        PreparedChain(
                            legs=[*partial.legs, (prepared, route_index)],
                            score=partial.score + _route_score(prepared, route_index),
                        )
                    )
            if not expanded:
                return ToolResult(
                    ok=False,
                    error=f"could not prepare stop {segment_index + 1}: routing failed",
                )
            partials = sorted(
                expanded,
                key=lambda chain: (chain.score, _chain_indexes(chain)),
            )[:beam_width]
    finally:
        ctx.progress_sink = previous_sink
    if previous_sink is not None:
        for stage in ("finding_routes", "checking_live_conditions"):
            await previous_sink(stage, "complete")
    return combine_prepared_chains(
        partials,
        waypoints=(
            waypoint_labels if waypoint_labels is not None else waypoints
        ),
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
        if key not in {
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
    rows = {
        int(row.get("index")): row
        for row in prepared.scored
        if isinstance(row, dict) and "index" in row
    }
    return sorted(
        range(len(prepared.parsed_routes)),
        key=lambda index: (float(rows.get(index, {}).get("score") or 0), index),
    )[: max(1, width)]


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


def _chain_indexes(chain: PreparedChain) -> list[int]:
    return [route_index for _leg, route_index in chain.legs]


def _max_candidates(value: object) -> int:
    try:
        return max(1, min(MAX_CANDIDATES, int(value or 5)))
    except (TypeError, ValueError):
        return 5
