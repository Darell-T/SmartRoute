"""GTFS + bus leg enrichment for a single route.

Independent module: enriches a route's SUBWAY/BUS steps in place with
intermediate stop names + coordinates. Strictly fail-open per leg.
"""

import asyncio
import os

from app.services.bus_routes import fetch_bus_route_stop_groups, slice_route_stops

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


async def _enrich_subway_legs(gtfs, steps: list[dict]) -> dict:
    """Enrich the SUBWAY steps of a single route in place (intermediate stop
    names + coordinates). Parallel, cached, strictly fail-open per leg. Returns
    {"legs": n, "with_stops": k}."""
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
    for step, result in zip(subway_steps, results):
        if isinstance(result, asyncio.TimeoutError):
            print(
                f"[trip] subway stop enrichment timed out "
                f"({step.get('route_id')}, {TRIP_GTFS_ENRICH_TIMEOUT_S:.2f}s)"
            )
            located = []
        elif isinstance(result, BaseException):
            print(f"[trip] subway stop enrichment skipped ({step.get('route_id')}): {result}")
            located = []
        else:
            located = result
        step["intermediate_stop_locations"] = located
        step["intermediate_stops"] = [s["name"] for s in located]
        if located:
            metrics["with_stops"] += 1
        else:
            print(
                "[trip] subway leg has no intermediate stops "
                f"({step.get('route_id')}: {step.get('departure_stop')} "
                f"-> {step.get('arrival_stop')})"
            )
    return metrics


async def _enrich_bus_legs(steps: list[dict]) -> dict:
    """Enrich the BUS steps of a single route in place via OneBusAway
    stops-for-route. Strictly fail-open. Returns {"legs": n, "with_stops": k}."""
    bus_steps = [s for s in steps if s.get("type") == "BUS"]
    metrics = {"legs": len(bus_steps), "with_stops": 0}
    for step in bus_steps:
        step.setdefault("intermediate_stops", [])
        step.setdefault("intermediate_stop_locations", [])
    bus_route_ids = sorted({s["route_id"] for s in bus_steps if s.get("route_id")})
    if not bus_route_ids:
        return metrics
    try:
        results = await asyncio.gather(
            *(fetch_bus_route_stop_groups(rid) for rid in bus_route_ids),
            return_exceptions=True,
        )
        groups_by_route = {
            rid: result
            for rid, result in zip(bus_route_ids, results)
            if isinstance(result, dict)
        }
        for step in bus_steps:
            parsed_groups = groups_by_route.get(step["route_id"])
            if not parsed_groups:
                continue
            located = slice_route_stops(
                parsed_groups,
                step.get("departure_coords") or {},
                step.get("arrival_coords") or {},
            )
            if located:
                step["intermediate_stop_locations"] = located
                step["intermediate_stops"] = [s["name"] for s in located]
                metrics["with_stops"] += 1
    except Exception as exc:
        print(f"[trip] bus stop enrichment skipped: {exc}")
    return metrics


async def _enrich_route(gtfs, route: list[dict]) -> dict:
    """Enrich one route's SUBWAY + BUS legs in place. Returns leg metrics."""
    sub = await _enrich_subway_legs(gtfs, route)
    bus = await _enrich_bus_legs(route)
    return {
        "subway_legs": sub["legs"],
        "subway_with_stops": sub["with_stops"],
        "bus_legs": bus["legs"],
        "bus_with_stops": bus["with_stops"],
    }
