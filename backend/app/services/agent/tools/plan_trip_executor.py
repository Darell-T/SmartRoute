"""Single-leg planning execution for the ``plan_trip`` compatibility facade."""

from __future__ import annotations

import asyncio
import math
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable

from app.services.agent.tools._location import ResolvedPlace
from app.services.agent.tools._types import ToolContext, ToolResult


@dataclass(frozen=True)
class PlanTripDependencies:
    """Runtime bindings kept in the facade so its public patch seams survive."""

    directions_service: Any
    route_with_recovery: Callable[..., Awaitable[dict]]
    derive_arrive_by_departure: Callable[..., Awaitable[str]]
    resolve_named_place: Callable[
        ..., Awaitable[tuple[ResolvedPlace | None, str | None]]
    ]
    collect_alerts: Callable[[], Awaitable[Any]]
    collect_stalled_trains: Callable[[set[str]], Awaitable[Any]]
    collect_stalled_buses: Callable[[set[str]], Awaitable[Any]]
    parse_service_alerts: Callable[[Any], list]
    filter_alerts_for_routes: Callable[[list, set[str]], list]
    ai_advisor: Any
    advisor_context: Any
    candidates: Any
    crowd_evidence: Any
    crowd_hotspots: Any
    enrichment: Any
    scoring: Any
    trip_incidents: Any
    geo: Any
    current_payload: Callable[..., list]
    evidence_envelope: Callable[..., Any]
    project: Callable[..., ToolResult]
    route_service_ids: Callable[[list[dict]], set[str]]
    context_timeout_seconds: float
    advisor_timeout_seconds: float
    incident_timeout_seconds: float
    live_evidence_ttl_seconds: int
    event_evidence_ttl_seconds: int


async def execute_single_leg(
    tool_input: dict,
    ctx: ToolContext,
    timings: dict[str, float],
    *,
    dependencies: PlanTripDependencies,
) -> ToolResult:
    """Resolve, route, enrich, score, then hand canonical facts to projection."""
    origin_raw = str(tool_input.get("origin") or "")
    destination_raw = str(tool_input.get("destination") or "").strip()
    if not destination_raw:
        return ToolResult(ok=False, error="destination is required")

    excluded = {
        str(mode).strip().upper()
        for mode in (tool_input.get("exclude_modes") or [])
    }
    allowed_modes = [mode for mode in ("SUBWAY", "BUS") if mode not in excluded]
    if not allowed_modes:
        return ToolResult(
            ok=False,
            error="no transit modes left after excluding all of them",
        )

    place_started = time.monotonic()
    origin_place, origin_error = await dependencies.resolve_named_place(
        origin_raw,
        ctx,
        missing_location_message=(
            "I need your current location to plan from 'origin' -- share GPS "
            "or give me an address instead."
        ),
    )
    if origin_place is None:
        return ToolResult(ok=False, error=origin_error or "could not resolve the origin")
    destination_place, destination_error = await dependencies.resolve_named_place(
        destination_raw,
        ctx,
        missing_location_message=(
            "I need your current location to plan from 'destination' -- share "
            "GPS or give me an address instead."
        ),
    )
    if destination_place is None:
        return ToolResult(
            ok=False,
            error=destination_error or "could not find that destination in NYC",
        )
    timings["place_resolution_ms"] = (time.monotonic() - place_started) * 1000

    routing_preference = tool_input.get("routing_preference") or "FEWER_TRANSFERS"
    departure_time = tool_input.get("departure_time") or None
    arrival_by = tool_input.get("arrival_by") or None
    if departure_time and arrival_by:
        return ToolResult(
            ok=False,
            error="use either departure_time or arrival_by, not both",
        )
    if arrival_by:
        try:
            departure_time = await dependencies.derive_arrive_by_departure(
                origin=origin_place,
                destination=destination_place,
                destination_query=destination_raw,
                arrival_by=str(arrival_by),
                allowed_modes=allowed_modes,
                routing_preference=routing_preference,
            )
        except ValueError as exc:
            return ToolResult(ok=False, error=str(exc))
        except dependencies.directions_service.GoogleRoutesError as exc:
            return ToolResult(
                ok=False,
                error=f"could not estimate an arrive-by departure ({exc.code})",
            )

    route_started = time.monotonic()
    try:
        response = await dependencies.route_with_recovery(
            origin=origin_place,
            destination=destination_place,
            destination_query=destination_raw,
            allowed_modes=allowed_modes,
            routing_preference=routing_preference,
            departure_time=departure_time,
        )
    except dependencies.directions_service.GoogleRoutesError as exc:
        print(f"[agent-plan_trip] routing failed code={exc.code}")
        return ToolResult(ok=False, error=f"routing failed ({exc.code})")
    timings["route_provider_ms"] = (time.monotonic() - route_started) * 1000

    parsed_routes = dependencies.directions_service.parse_response(response)
    if not parsed_routes:
        return ToolResult(ok=False, error="no transit route found between those points")
    required_route_ids = {
        str(route_id).strip().upper()
        for route_id in tool_input.get("required_route_ids") or []
        if str(route_id).strip()
    }
    if required_route_ids:
        parsed_routes = [
            route
            for route in parsed_routes
            if required_route_ids.issubset(dependencies.route_service_ids(route))
        ]
        if not parsed_routes:
            requested = "/".join(sorted(required_route_ids))
            return ToolResult(
                ok=False,
                error=f"no route candidate used the requested {requested} service",
            )
    try:
        max_candidates = max(
            1,
            int(tool_input.get("max_candidates") or len(parsed_routes)),
        )
    except (TypeError, ValueError):
        max_candidates = len(parsed_routes)
    parsed_routes = parsed_routes[:max_candidates]

    route_ids, bus_route_ids = dependencies.candidates._collect_route_and_bus_ids(
        parsed_routes
    )
    avoid_crowds = bool(tool_input.get("avoid_crowds"))
    hotspot_hits = dependencies.crowd_hotspots.find_hotspot_hits(ctx.gtfs, parsed_routes)
    collect_crowd_evidence = avoid_crowds or bool(hotspot_hits)
    allow_live_crowd_search = (
        collect_crowd_evidence
        and str(tool_input.get("crowd_search_mode") or "auto") == "auto"
    )

    async def collect_event_evidence() -> tuple[Any, list[dict], list[str], dict]:
        started = time.monotonic()
        try:
            return await dependencies.crowd_evidence.collect(
                parsed_routes,
                ctx,
                hotspot_hits=hotspot_hits,
                explicit_crowd_request=avoid_crowds,
                allow_live_search=allow_live_crowd_search,
            )
        finally:
            timings["ticketmaster_ms"] = (time.monotonic() - started) * 1000

    event_task = (
        asyncio.create_task(collect_event_evidence())
        if collect_crowd_evidence
        else None
    )
    incident_task = None
    if tool_input.get("include_incident_scan"):
        incident_context = dependencies.trip_incidents.build_candidate_stop_context(
            ctx.gtfs,
            parsed_routes,
        )
        incident_task = asyncio.create_task(
            asyncio.wait_for(
                dependencies.trip_incidents._scan_route_incidents(incident_context),
                timeout=dependencies.incident_timeout_seconds,
            )
        )

    context_collection_timed_out = False
    mta_started = time.monotonic()
    try:
        raw_alerts, stalled, stalled_buses = await asyncio.wait_for(
            asyncio.gather(
                dependencies.collect_alerts(),
                dependencies.collect_stalled_trains(route_ids),
                dependencies.collect_stalled_buses(bus_route_ids),
                return_exceptions=True,
            ),
            timeout=dependencies.context_timeout_seconds,
        )
    except asyncio.TimeoutError:
        context_collection_timed_out = True
        raw_alerts, stalled, stalled_buses = [], [], []
    alerts_available = not context_collection_timed_out and not isinstance(
        raw_alerts,
        BaseException,
    )
    subway_vehicles_available = not context_collection_timed_out and not isinstance(
        stalled,
        BaseException,
    )
    bus_vehicles_available = not context_collection_timed_out and not isinstance(
        stalled_buses,
        BaseException,
    )
    raw_alerts = [] if isinstance(raw_alerts, BaseException) else raw_alerts
    stalled = [] if isinstance(stalled, BaseException) else stalled
    stalled_buses = [] if isinstance(stalled_buses, BaseException) else stalled_buses
    timings["mta_ms"] = (time.monotonic() - mta_started) * 1000
    parsed_alerts = dependencies.parse_service_alerts(raw_alerts) if raw_alerts else []
    relevant_alerts = dependencies.filter_alerts_for_routes(parsed_alerts, route_ids)

    event_evidence_status = "not_required"
    event_impacts: list[dict] = []
    event_failures: list[str] = []
    crowd_search_metadata: dict = {"grok_status": "not_required"}
    if event_task is not None:
        try:
            (
                event_evidence_status,
                event_impacts,
                event_failures,
                crowd_search_metadata,
            ) = await event_task
        except Exception as exc:
            print(f"[agent-plan_trip] event enrichment failed: {type(exc).__name__}")
            event_evidence_status = "provider_unavailable"
            event_impacts = []
            event_failures = [type(exc).__name__]

    incidents: list = []
    advisor_evidence_available = False
    if incident_task is not None:
        try:
            incidents = await incident_task
            advisor_evidence_available = True
        except asyncio.TimeoutError:
            print(
                "[agent-plan_trip] incident scan timed out "
                f"({dependencies.incident_timeout_seconds:.0f}s)"
            )
        except Exception as exc:
            print(f"[agent-plan_trip] incident scan failed: {type(exc).__name__}")

    observed_at = datetime.now(timezone.utc)
    evidence_envelopes = {
        "alerts": dependencies.evidence_envelope(
            "mta_service_alerts",
            relevant_alerts,
            observed_at=observed_at,
            ttl_seconds=dependencies.live_evidence_ttl_seconds,
            available=alerts_available,
        ),
        "subway_vehicles": dependencies.evidence_envelope(
            "mta_subway_vehicle_positions",
            stalled,
            observed_at=observed_at,
            ttl_seconds=dependencies.live_evidence_ttl_seconds,
            available=subway_vehicles_available,
        ),
        "bus_vehicles": dependencies.evidence_envelope(
            "mta_bus_vehicle_positions",
            stalled_buses,
            observed_at=observed_at,
            ttl_seconds=dependencies.live_evidence_ttl_seconds,
            available=bus_vehicles_available,
        ),
        "events": dependencies.evidence_envelope(
            "crowd_events",
            event_impacts,
            observed_at=observed_at,
            ttl_seconds=dependencies.event_evidence_ttl_seconds,
            available=event_evidence_status != "provider_unavailable",
        ),
        "advisor": dependencies.evidence_envelope(
            "route_incident_advisor",
            incidents,
            observed_at=observed_at,
            ttl_seconds=dependencies.live_evidence_ttl_seconds,
            available=advisor_evidence_available,
        ),
    }
    # These values are still current immediately after collection. Keeping the
    # suppression at this boundary makes expiry deterministic if collection is
    # later cached or replayed.
    relevant_alerts = dependencies.current_payload(evidence_envelopes["alerts"], empty=[])
    stalled = dependencies.current_payload(evidence_envelopes["subway_vehicles"], empty=[])
    stalled_buses = dependencies.current_payload(
        evidence_envelopes["bus_vehicles"],
        empty=[],
    )
    event_impacts = dependencies.current_payload(evidence_envelopes["events"], empty=[])
    incidents = dependencies.current_payload(evidence_envelopes["advisor"], empty=[])

    scoring_started = time.monotonic()
    judge_payload = dependencies.advisor_context.build_advisor_payload(
        routes=parsed_routes,
        service_alerts=relevant_alerts,
        incidents=incidents,
        stalled_trains=stalled,
        stalled_buses=stalled_buses,
        ticketmaster_event_impacts=event_impacts,
        evidence=evidence_envelopes,
        mode=dependencies.advisor_context.PlanningMode.INTELLIGENCE,
    )
    try:
        recommendation = await asyncio.wait_for(
            dependencies.ai_advisor.collect_recommendation(judge_payload),
            timeout=dependencies.advisor_timeout_seconds,
        )
    except asyncio.TimeoutError:
        print(
            "[agent-plan_trip] advisor timed out "
            f"({dependencies.advisor_timeout_seconds:.2f}s)"
        )
        recommendation = "[ROUTE:0] Live reasoning timed out; showing the fastest option."
    except Exception as exc:
        print(f"[agent-plan_trip] advisor unavailable type={type(exc).__name__}")
        recommendation = "[ROUTE:0] Live reasoning was unavailable; showing the fastest option."
    chosen_index, candidate_analysis = (
        dependencies.advisor_context.parse_advisor_selection(
            recommendation,
            len(parsed_routes),
        )
    )
    scored = dependencies.scoring._score_routes(
        parsed_routes,
        relevant_alerts,
        ticketmaster_event_impacts=event_impacts,
    )
    timings["scoring_ms"] = (time.monotonic() - scoring_started) * 1000
    decision_reason = "advisor_tiebreak"
    selection_log_reason = "advisor_selection"
    scoring_event_impacts = [
        impact
        for impact in event_impacts
        if float(impact.get("risk_score") or 0) > 0
    ]
    if scoring_event_impacts:
        # Crowd avoidance is a hard evidence contract. The model may explain
        # the evidence, but the actual route choice remains deterministic.
        chosen_index = min(
            scored,
            key=lambda row: (
                row["score"],
                row["event_crowd_penalty"],
                row["total_minutes"],
                row["index"],
            ),
        )["index"]
        decision_reason = "lowest_final_score"
        selection_log_reason = "risk_adjusted_event_score"

    chosen_route = parsed_routes[chosen_index]
    await dependencies.enrichment._enrich_route(ctx.gtfs, chosen_route)
    first_leg_arrival_context = await _first_leg_arrival_context(
        tool_input,
        ctx,
        origin_place,
        chosen_route,
        dependencies,
    )
    return dependencies.project(
        tool_input=tool_input,
        ctx=ctx,
        timings=timings,
        parsed_routes=parsed_routes,
        origin_raw=origin_raw,
        destination_raw=destination_raw,
        origin_place=origin_place,
        destination_place=destination_place,
        departure_time=departure_time,
        arrival_by=arrival_by,
        excluded=excluded,
        relevant_alerts=relevant_alerts,
        event_evidence_status=event_evidence_status,
        event_impacts=event_impacts,
        event_failures=event_failures,
        crowd_search_metadata=crowd_search_metadata,
        evidence_envelopes=evidence_envelopes,
        collect_crowd_evidence=collect_crowd_evidence,
        chosen_index=chosen_index,
        candidate_analysis=candidate_analysis,
        scored=scored,
        decision_reason=decision_reason,
        selection_log_reason=selection_log_reason,
        scoring_event_impacts=scoring_event_impacts,
        first_leg_arrival_context=first_leg_arrival_context,
    )


async def _first_leg_arrival_context(
    tool_input: dict,
    ctx: ToolContext,
    origin_place: ResolvedPlace,
    chosen_route: list[dict],
    dependencies: PlanTripDependencies,
) -> dict | None:
    if not tool_input.get("include_first_leg_arrivals"):
        return None
    first_transit = next(
        (step for step in chosen_route if step.get("type") in {"SUBWAY", "BUS"}),
        None,
    )
    if not first_transit:
        return None
    departure_coords = first_transit.get("departure_coords") or {}
    try:
        latitude = float(departure_coords.get("latitude", departure_coords.get("lat")))
        longitude = float(departure_coords.get("longitude", departure_coords.get("lng")))
        walking_minutes = max(
            0,
            math.ceil(
                dependencies.geo.distance_meters(
                    origin_place.latitude,
                    origin_place.longitude,
                    latitude,
                    longitude,
                )
                / 80
            ),
        )
        from app.services.agent.tools import lookup_arrivals

        result = await asyncio.wait_for(
            lookup_arrivals.execute(
                {
                    "mode": str(first_transit.get("type") or "").lower(),
                    "route_id": dependencies.scoring._step_route_id(first_transit),
                    "stop_query": first_transit.get("departure_stop"),
                    "direction": first_transit.get("direction"),
                    "user_location": {
                        "latitude": latitude,
                        "longitude": longitude,
                    },
                    "walking_minutes": walking_minutes,
                    "limit": 3,
                },
                ctx,
            ),
            timeout=3.0,
        )
        if (
            result.ok
            and isinstance(result.data, dict)
            and isinstance(result.data.get("catchability"), dict)
        ):
            catchability = result.data["catchability"]
            return {
                "route_id": dependencies.scoring._step_route_id(first_transit),
                "stop_name": first_transit.get("departure_stop"),
                "source_status": result.data.get("source_status"),
                "walking_minutes": catchability.get("walking_minutes"),
                "catchable_arrival_minutes": catchability.get(
                    "catchable_arrival_minutes"
                ),
                "arrival_minutes": catchability.get("arrival_minutes") or [],
            }
    except (asyncio.TimeoutError, TypeError, ValueError):
        pass
    return None
