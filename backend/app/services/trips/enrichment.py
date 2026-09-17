"""GTFS + bus leg enrichment for a single route.

Independent module: enriches a route's SUBWAY/BUS steps in place with
intermediate stop names + coordinates. Strictly fail-open per leg.
"""

import asyncio
import logging
import os

from app.services.mta.bus import fetch_bus_route_stop_groups, slice_route_stops

_LOGGER = logging.getLogger(__name__)

# The per-leg GTFS stop enrichment runs a GROUP BY over all of a route's
# stop_times on a remote Postgres; 1.25s was too tight and timed out, dropping
# the station NAMES (the map then showed unlabeled dots). Give the query real
# headroom -- it runs in a worker thread, so a longer wait never blocks the
# event loop. Tunable via env.
TRIP_GTFS_ENRICH_TIMEOUT_S = float(os.getenv("TRIP_GTFS_ENRICH_TIMEOUT_S", "6.0"))


async def _enrich_subway_step_with_gtfs(gtfs, step: dict) -> list[dict]:
    return await asyncio.wait_for(
        asyncio.to_thread(
            gtfs.get_intermediate_stops_with_coords,
            step["route_id"],
            step["departure_stop"],
            step["arrival_stop"],
            step.get("departure_coords"),
            step.get("arrival_coords"),
        ),
        timeout=TRIP_GTFS_ENRICH_TIMEOUT_S,
    )


def _select_subway_located_stops(step: dict, result: object) -> list[dict]:
    if isinstance(result, asyncio.TimeoutError):
        _LOGGER.warning(
            "[trip] subway stop enrichment timed out (%s, %.2fs)",
            step.get("route_id"),
            TRIP_GTFS_ENRICH_TIMEOUT_S,
        )
        return []
    if isinstance(result, BaseException):
        _LOGGER.warning(
            "[trip] subway stop enrichment skipped (%s): %s",
            step.get("route_id"),
            result,
        )
        return []
    return list(result)


async def _enrich_subway_legs(gtfs, steps: list[dict]) -> dict:
    """Enrich SUBWAY steps in place with intermediate stop names and coordinates.

    Parallel, cached, and strictly fail-open per leg.
    """
    subway_steps = [s for s in steps if s.get("type") == "SUBWAY"]
    metrics = {"legs": len(subway_steps), "with_stops": 0}
    for step in subway_steps:
        step.setdefault("intermediate_stops", [])
        step.setdefault("intermediate_stop_locations", [])
    if not subway_steps or not gtfs:
        return metrics
    results = await asyncio.gather(
        *(_enrich_subway_step_with_gtfs(gtfs, step) for step in subway_steps),
        return_exceptions=True,
    )
    for step, result in zip(subway_steps, results, strict=False):
        located = _select_subway_located_stops(step, result)
        step["intermediate_stop_locations"] = located
        step["intermediate_stops"] = [s["name"] for s in located]
        if located:
            metrics["with_stops"] += 1
            continue
    return metrics


def _select_bus_steps(steps: list[dict]) -> list[dict]:
    bus_steps = [step for step in steps if step.get("type") == "BUS"]
    for step in bus_steps:
        step.setdefault("intermediate_stops", [])
        step.setdefault("intermediate_stop_locations", [])
    return bus_steps


def _parse_bus_groups_by_route(route_ids: list[str], results: list) -> dict:
    return {
        rid: result
        for rid, result in zip(route_ids, results, strict=False)
        if isinstance(result, dict)
    }


def _select_bus_leg_stops(step: dict, groups_by_route: dict) -> list:
    parsed_groups = groups_by_route.get(step["route_id"])
    if not parsed_groups:
        return []
    return slice_route_stops(
        parsed_groups,
        step.get("departure_coords") or {},
        step.get("arrival_coords") or {},
    )


async def _enrich_bus_legs(steps: list[dict]) -> dict:
    """Enrich BUS steps in place via OneBusAway stops-for-route. Strictly fail-open."""
    bus_steps = _select_bus_steps(steps)
    metrics = {"legs": len(bus_steps), "with_stops": 0}
    bus_route_ids = sorted({s["route_id"] for s in bus_steps if s.get("route_id")})
    if not bus_route_ids:
        return metrics
    try:
        results = await asyncio.gather(
            *(fetch_bus_route_stop_groups(rid) for rid in bus_route_ids),
            return_exceptions=True,
        )
        groups_by_route = _parse_bus_groups_by_route(bus_route_ids, results)
        for step in bus_steps:
            located = _select_bus_leg_stops(step, groups_by_route)
            if not located:
                continue
            step["intermediate_stop_locations"] = located
            step["intermediate_stops"] = [s["name"] for s in located]
            metrics["with_stops"] += 1
    except Exception as exc:  # noqa: BLE001 bus-stop enrichment faults skip intermediates
        _LOGGER.warning("[trip] bus stop enrichment skipped: %s", exc)
    return metrics


async def enrich_route(gtfs, route: list[dict]) -> dict:
    """Enrich one route's SUBWAY + BUS legs in place. Returns leg metrics."""
    sub = await _enrich_subway_legs(gtfs, route)
    bus = await _enrich_bus_legs(route)
    return {
        "subway_legs": sub["legs"],
        "subway_with_stops": sub["with_stops"],
        "bus_legs": bus["legs"],
        "bus_with_stops": bus["with_stops"],
    }
