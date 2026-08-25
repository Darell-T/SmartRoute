"""Gather, enrich, and score route candidates without nested model selection.

Shared by the conversational ``prepare_route_options`` path and the direct
Live Map trip endpoint.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field as dataclass_field
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable

from app.services.trips.location import ResolvedPlace
from app.services.trips.preparation.input import parse_rfc3339, prepare_structural_candidates
from app.services.trips.preparation.context import (
    RoutePreparationContext,
    RoutePreparationFailure,
)
from app.services.trips.transfer_semantics import normalize_routes


@dataclass(frozen=True)
class PreparationDependencies:
    """Runtime bindings ``prepare_single_leg`` actually reads.

    Kept injectable so provider and route-preparation tests can substitute
    boundaries. Selection, projection, enrichment, and geo bindings are
    deliberately not part of preparation.
    """

    directions_service: Any
    route_with_recovery: Callable[..., Awaitable[list]]
    derive_arrive_by_departure: Callable[..., Awaitable[str]]
    resolve_named_place: Callable[
        ..., Awaitable[tuple[ResolvedPlace | None, str | None]]
    ]
    collect_alerts: Callable[[], Awaitable[Any]]
    collect_stalled_trains: Callable[[set[str]], Awaitable[Any]]
    collect_stalled_buses: Callable[[set[str]], Awaitable[Any]]
    parse_service_alerts: Callable[[Any], list]
    filter_alerts_for_routes: Callable[[list, set[str]], list]
    candidates: Any
    crowd_evidence: Any
    crowd_hotspots: Any
    scoring: Any
    trip_incidents: Any
    current_payload: Callable[..., list]
    evidence_envelope: Callable[..., Any]
    route_service_ids: Callable[[list[dict]], set[str]]
    context_timeout_seconds: float
    live_evidence_ttl_seconds: int
    event_evidence_ttl_seconds: int
    normalize_routes: Callable[..., Any] | None = None
    record_phase_ms: Callable[[dict[str, Any], str, float], None] | None = None


@dataclass
class PreparedLeg:
    """Server-owned intermediate trip state after scoring, before selection."""

    tool_input: dict
    origin_raw: str
    destination_raw: str
    origin_place: ResolvedPlace
    destination_place: ResolvedPlace
    departure_time: str | None
    arrival_by: str | None
    excluded: set[str]
    parsed_routes: list
    scored: list[dict]
    relevant_alerts: list
    event_evidence_status: str
    event_impacts: list[dict]
    event_failures: list[str]
    crowd_search_metadata: dict
    incident_scan_metadata: dict
    evidence_envelopes: dict[str, Any]
    collect_crowd_evidence: bool
    incidents: list
    stalled: list
    stalled_buses: list
    timings: dict[str, float]
    leg_telemetry: dict | None
    plan_origin: float


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
    candidate_itineraries: list[dict[str, Any]] = dataclass_field(default_factory=list)
    candidate_constraints: list[dict[str, Any]] = dataclass_field(default_factory=list)
    candidate_destinations: list[ResolvedPlace] = dataclass_field(default_factory=list)
    branch_coverage: list[dict[str, Any]] = dataclass_field(default_factory=list)
    stage_a_factors: list[dict[str, Any]] = dataclass_field(default_factory=list)
    snapshot_id: str | None = None
    snapshot_observed_at: str | None = None
    finalized: bool = False


@dataclass
class PreparedChain:
    legs: list[tuple[PreparedLeg, int]]
    score: float


async def _drain_owned_evidence_tasks(
    event_task: asyncio.Task[Any] | None,
    incident_task: asyncio.Task[Any],
) -> None:
    """Cancel and drain request-owned evidence tasks when preparation exits.

    Runs from the finally spanning every await after task creation, so caller
    cancellation tears down both children before CancelledError propagates.
    No-op on success; never raises; results are read from the task objects.
    """
    owned = [task for task in (event_task, incident_task) if task is not None]
    for task in owned:
        if not task.done():
            task.cancel()
    await asyncio.gather(*owned, return_exceptions=True)


async def prepare_single_leg(
    tool_input: dict,
    ctx: RoutePreparationContext,
    timings: dict[str, float],
    *,
    dependencies: PreparationDependencies,
    emit_comparing_progress: bool = True,
    resolved_origin: ResolvedPlace | None = None,
    resolved_destination: ResolvedPlace | None = None,
) -> PreparedLeg | RoutePreparationFailure:
    """Resolve places, fetch routes/evidence, score. Never calls a model."""

    origin_raw = str(tool_input.get("origin") or "")
    destination_raw = str(tool_input.get("destination") or "").strip()
    if not destination_raw:
        return RoutePreparationFailure("destination is required")

    for field in ("departure_time", "arrival_by"):
        value = tool_input.get(field)
        if value:
            try:
                parse_rfc3339(value, field=field)
            except (TypeError, ValueError) as exc:
                return RoutePreparationFailure(str(exc))

    excluded = {str(mode).strip().upper() for mode in (tool_input.get("exclude_modes") or [])}
    allowed_modes = [mode for mode in ("SUBWAY", "BUS") if mode not in excluded]
    if not allowed_modes:
        return RoutePreparationFailure("no transit modes left after excluding all of them")

    plan_origin = time.monotonic()
    leg_telemetry = ctx.telemetry.get("_plan_trip_active_leg")
    if not isinstance(leg_telemetry, dict):
        leg_telemetry = ctx.telemetry["route_candidate_diagnostics"] = {}
    leg_telemetry["_plan_origin_monotonic"] = plan_origin

    def mark(name: str) -> None:
        elapsed = (time.monotonic() - plan_origin) * 1000
        timings[name] = elapsed
        record_phase = getattr(dependencies, "record_phase_ms", None)
        if record_phase is not None:
            record_phase(ctx.telemetry, name, elapsed)

    place_started = time.monotonic()
    if resolved_origin is not None:
        origin_place = resolved_origin
        origin_error = None
    else:
        origin_place, origin_error = await dependencies.resolve_named_place(
            origin_raw,
            ctx,
            missing_location_message=(
                "I need your current location to plan from 'origin' -- share GPS "
                "or give me an address instead."
            ),
        )
    if origin_place is None:
        return RoutePreparationFailure(origin_error or "could not resolve the origin")
    if resolved_destination is not None:
        destination_place = resolved_destination
        destination_error = None
    else:
        destination_place, destination_error = await dependencies.resolve_named_place(
            destination_raw,
            ctx,
            missing_location_message=(
                "I need your current location to plan from 'destination' -- share "
                "GPS or give me an address instead."
            ),
        )
    if destination_place is None:
        return RoutePreparationFailure(
            destination_error or "could not find that destination in NYC"
        )
    timings["place_resolution_ms"] = (time.monotonic() - place_started) * 1000
    mark("place_resolution_complete_ms")
    # Use canonical discovery/GTFS labels; opaque IDs are never re-geocoded.
    destination_query = destination_raw
    if destination_place.source in {"discovery", "gtfs"}:
        destination_query = (
            str(destination_place.address or "").strip()
            or str(destination_place.name or "").strip()
            or destination_raw
        )

    routing_preference = tool_input.get("routing_preference") or "FEWER_TRANSFERS"
    departure_time = tool_input.get("departure_time") or None
    arrival_by = tool_input.get("arrival_by") or None
    if departure_time and arrival_by:
        return RoutePreparationFailure("use either departure_time or arrival_by, not both")
    if arrival_by:
        try:
            departure_time = await dependencies.derive_arrive_by_departure(
                origin=origin_place,
                destination=destination_place,
                destination_query=destination_query,
                arrival_by=str(arrival_by),
                allowed_modes=allowed_modes,
                routing_preference=routing_preference,
            )
        except ValueError as exc:
            return RoutePreparationFailure(str(exc))
        except dependencies.directions_service.GoogleRoutesError as exc:
            return RoutePreparationFailure(
                f"could not estimate an arrive-by departure ({exc.code})"
            )

    route_started = time.monotonic()
    try:
        parsed_routes = await dependencies.route_with_recovery(
            origin=origin_place,
            destination=destination_place,
            destination_query=destination_query,
            allowed_modes=allowed_modes,
            routing_preference=routing_preference,
            departure_time=departure_time,
        )
    except dependencies.directions_service.GoogleRoutesError as exc:
        print(f"[agent-plan_trip] routing failed code={exc.code}")
        return RoutePreparationFailure(f"routing failed ({exc.code})")
    timings["route_provider_ms"] = (time.monotonic() - route_started) * 1000
    mark("google_routes_complete_ms")

    if not parsed_routes:
        return RoutePreparationFailure("no transit route found between those points")
    parsed_routes = await prepare_structural_candidates(
        parsed_routes,
        tool_input=tool_input,
        ctx=ctx,
        dependencies=dependencies,
        origin=origin_place,
        destination=destination_place,
        destination_query=destination_query,
        departure_time=departure_time,
        allowed_modes=allowed_modes,
        routing_preference=routing_preference,
        telemetry=leg_telemetry,
        timings=timings,
    )
    # Normalize every provider-backed route before constraints/evidence consume it.
    normalizer = getattr(dependencies, "normalize_routes", None) or normalize_routes
    normalizer(parsed_routes, ctx.gtfs)
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
            return RoutePreparationFailure(
                f"no route candidate used the requested {requested} service"
            )
    await ctx.emit_progress("finding_routes", "complete")

    route_ids, bus_route_ids = dependencies.candidates._collect_route_and_bus_ids(
        parsed_routes
    )
    avoid_crowds = bool(tool_input.get("avoid_crowds"))
    hotspot_hits = dependencies.crowd_hotspots.find_hotspot_hits(ctx.gtfs, parsed_routes)
    collect_crowd_evidence = avoid_crowds or bool(hotspot_hits)
    allow_live_crowd_search = collect_crowd_evidence and str(
        tool_input.get("crowd_search_mode") or "auto"
    ) == "auto"

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

    async def collect_incident_evidence() -> dict[str, Any]:
        started = time.monotonic()
        try:
            return await dependencies.trip_incidents.scan_route_incidents(
                incident_context,
                travel_at=departure_time,
            )
        finally:
            timings["incident_ms"] = (time.monotonic() - started) * 1000

    await ctx.emit_progress("checking_live_conditions", "active")
    event_task = asyncio.create_task(collect_event_evidence()) if collect_crowd_evidence else None
    incident_context = dependencies.trip_incidents.build_candidate_stop_context(
        ctx.gtfs,
        parsed_routes,
    )
    mark("incident_start_ms")
    incident_task = asyncio.create_task(collect_incident_evidence())
    try:
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
        mark("mta_context_complete_ms")
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
        incident_scan_metadata: dict = {
            "status": "unscanned",
            "lookup_status": "unavailable",
            "coverage_status": "unscanned",
            "lookup_kind": "index",
            "warning_count": 0,
            "cache_hit": False,
            "sources": {"attempted": [], "completed": []},
        }
        advisor_evidence_available = False
        try:
            incident_scan = await incident_task
            metadata = incident_scan.get("scan_metadata") if isinstance(incident_scan, dict) else None
            if isinstance(incident_scan, dict) and isinstance(
                incident_scan.get("incidents"), list
            ):
                incidents = [
                    incident
                    for incident in incident_scan["incidents"]
                    if isinstance(incident, dict)
                    and incident.get("advisor_eligible") is True
                ]
            if isinstance(metadata, dict):
                incident_scan_metadata = metadata
            if isinstance(leg_telemetry, dict):
                leg_telemetry["incident_status"] = str(
                    incident_scan_metadata.get("status") or "unscanned"
                )
                cache_hit = incident_scan_metadata.get("cache_hit")
                leg_telemetry["incident_cache_hit"] = (
                    cache_hit if isinstance(cache_hit, bool) else None
                )
            # Indexed confirmed incidents are usable when the index lookup itself
            # succeeded, even when some unrelated coverage batch is stale/partial.
            advisor_evidence_available = (
                dependencies.trip_incidents.incident_lookup_succeeded(
                    incident_scan_metadata
                )
                and bool(incidents)
            )
        except Exception as exc:
            if isinstance(leg_telemetry, dict):
                leg_telemetry["incident_status"] = "failed"
                leg_telemetry["incident_cache_hit"] = False
            print(f"[agent-plan_trip] incident index lookup failed: {type(exc).__name__}")
        mark("incident_complete_ms")
        await ctx.emit_progress("checking_live_conditions", "complete")
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
        relevant_alerts = dependencies.current_payload(evidence_envelopes["alerts"], empty=[])
        stalled = dependencies.current_payload(evidence_envelopes["subway_vehicles"], empty=[])
        stalled_buses = dependencies.current_payload(
            evidence_envelopes["bus_vehicles"],
            empty=[],
        )
        event_impacts = dependencies.current_payload(evidence_envelopes["events"], empty=[])
        incidents = dependencies.current_payload(evidence_envelopes["advisor"], empty=[])

        scoring_started = time.monotonic()
        scored = dependencies.scoring._score_routes(
            parsed_routes,
            relevant_alerts,
            ticketmaster_event_impacts=event_impacts,
            routing_preference=str(routing_preference),
            preferred_modes=list(tool_input.get("preferred_modes") or []),
        )
        timings["scoring_ms"] = (time.monotonic() - scoring_started) * 1000
        if emit_comparing_progress:
            await ctx.emit_progress("comparing_options", "active")

        return PreparedLeg(
            tool_input=tool_input,
            origin_raw=origin_raw,
            destination_raw=destination_raw,
            origin_place=origin_place,
            destination_place=destination_place,
            departure_time=departure_time,
            arrival_by=arrival_by,
            excluded=excluded,
            parsed_routes=parsed_routes,
            scored=scored,
            relevant_alerts=relevant_alerts,
            event_evidence_status=event_evidence_status,
            event_impacts=event_impacts,
            event_failures=event_failures,
            crowd_search_metadata=crowd_search_metadata,
            incident_scan_metadata=incident_scan_metadata,
            evidence_envelopes=evidence_envelopes,
            collect_crowd_evidence=collect_crowd_evidence,
            incidents=incidents,
            stalled=stalled,
            stalled_buses=stalled_buses,
            timings=timings,
            leg_telemetry=leg_telemetry if isinstance(leg_telemetry, dict) else None,
            plan_origin=plan_origin,
        )
    finally:
        await _drain_owned_evidence_tasks(event_task, incident_task)


__all__ = ("PreparationDependencies", "PreparedLeg", "prepare_single_leg")
