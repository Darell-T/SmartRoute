"""Live-feed snapshot assembly."""

from __future__ import annotations

import re
import time
from datetime import datetime, timezone

from app.services.mta import realtime as mta_realtime
from app.services.live_feed import vehicle_enrichment
from app.services.live_feed.network_snapshot import (
    NetworkSnapshot,
    network_snapshot_store,
)
from app.services.geography import find_nearest_stops

_LAST_EMPTY_VEHICLE_LOG = 0.0
NEARBY_ARRIVAL_RADIUS_M = 804.672
NEARBY_ARRIVAL_STOP_LIMIT = 32
_SIGNAL_DISRUPTION_KEYWORDS = (
    "SUSPEND",
    "NO ",
    "SKIP",
    "BYPASS",
    "REROUT",
    "SLOW SPEED",
    "MAJOR DELAY",
    "PART SUSPEND",
)
_SIGNAL_CAUTION_KEYWORDS = (
    "DELAY",
    "SERVICE CHANGE",
    "PLANNED WORK",
    "LOCAL TO EXPRESS",
    "EXPRESS TO LOCAL",
    "SHUTTLE",
)

MAX_NEARBY_STATION_HOPS = 3
_STALL_PATTERN = re.compile(
    r"\b(stalled|stopped|disabled|held)\b.{0,28}\btrain\b"
    r"|\btrain\b.{0,28}\b(stalled|stopped|disabled|held)\b",
    re.IGNORECASE,
)


def _normalized_ids(values) -> set[str]:
    return {
        str(value).strip().rstrip("NS")
        for value in (values or [])
        if str(value).strip()
    }


def _normalized_routes(values) -> set[str]:
    return {
        str(value).strip().upper()
        for value in (values or [])
        if str(value).strip()
    }


def _route_stop_match(index, route_id: str, origin_ids: set[str], issue_ids: set[str]):
    best = None
    for pattern in index.route_patterns.get(route_id, []):
        positions = {
            str(stop_id).rstrip("NS"): position
            for stop_id, position in pattern.get("pos", {}).items()
        }
        origin_positions = [positions[value] for value in origin_ids if value in positions]
        issue_positions = [positions[value] for value in issue_ids if value in positions]
        if not origin_positions or not issue_positions:
            continue
        for origin_position in origin_positions:
            for issue_position in issue_positions:
                hops = abs(issue_position - origin_position)
                if best is None or hops < best["hops"]:
                    stop_id = str(pattern["stop_ids"][issue_position]).rstrip("NS")
                    stop = index.stops.get(stop_id, {})
                    best = {
                        "hops": hops,
                        "stop_id": stop_id,
                        "stop_name": stop.get("name") or stop_id,
                    }
    return best


def _summary(route_id: str, match: dict, nearby_stop_name: str) -> str:
    issue_station = match["stop_name"]
    hops = match["hops"]
    if hops == 0:
        return f"{route_id} train stalled at {issue_station}"
    stop_word = "stop" if hops == 1 else "stops"
    return (
        f"{route_id} train stalled near {issue_station} "
        f"· {hops} {stop_word} from {nearby_stop_name}"
    )


def build_nearby_transit_issues(
    *,
    gtfs,
    alerts: list[dict],
    nearby_stop_id: str | None,
    nearby_stop_name: str | None,
    nearby_route_ids,
    selected_route_ids=(),
    observed_at: int,
) -> list[dict]:
    """Return at most one confirmed issue with canonical station-hop facts.

    Raw vehicle age is deliberately excluded: an old GTFS-RT timestamp is stale
    telemetry, not evidence that a train is stalled. Strong inference can enter
    this contract only after a separate repeated-observation service has
    corroborated it.
    """

    index = gtfs.__dict__.get("_pattern_index")
    origin_ids = _normalized_ids([nearby_stop_id])
    nearby_routes = _normalized_routes(nearby_route_ids)
    selected_routes = _normalized_routes(selected_route_ids)
    if index is None or not origin_ids or not nearby_routes:
        return []

    candidates = []
    for alert in alerts:
        text = " ".join(
            str(alert.get(field) or "") for field in ("header", "description")
        )
        if not _STALL_PATTERN.search(text):
            continue
        issue_stop_ids = _normalized_ids(alert.get("stop_ids"))
        alert_routes = _normalized_routes(alert.get("route_ids"))
        if not issue_stop_ids:
            continue

        for route_id in sorted(alert_routes & nearby_routes):
            match = _route_stop_match(index, route_id, origin_ids, issue_stop_ids)
            if match is None or match["hops"] > MAX_NEARBY_STATION_HOPS:
                continue
            relevance = (
                "planned_route" if route_id in selected_routes else "nearby_line"
            )
            candidates.append(
                {
                    "id": str(alert.get("alert_id") or f"mta-{route_id}-{match['stop_id']}"),
                    "route_ids": [route_id],
                    "station_id": match["stop_id"],
                    "station_name": match["stop_name"],
                    "stops_away": match["hops"],
                    "confidence": "confirmed",
                    "status": "stalled",
                    "summary": _summary(
                        route_id,
                        match,
                        nearby_stop_name or str(nearby_stop_id),
                    ),
                    "source_types": ["mta_service_alert"],
                    "observed_at": datetime.fromtimestamp(
                        observed_at, tz=timezone.utc
                    ).isoformat(),
                    "relevance": relevance,
                }
            )

    candidates.sort(
        key=lambda issue: (
            issue["stops_away"],
            issue["route_ids"][0],
            issue["id"],
        )
    )
    return candidates[:1]

def _normalize_route_ids(route_ids):
    return {
        str(route_id).strip().upper()
        for route_id in (route_ids or [])
        if str(route_id).strip()
    }


def _location_verbose_log(connection_id: int, selected_route_ids: set[str]) -> str:
    """Return location telemetry without rider-provided coordinates."""

    return (
        f"[ws_live_feed:{connection_id}] location "
        f"selected_routes={sorted(selected_route_ids)}"
    )


def _socket_failure_log(channel: str, exc: Exception) -> str:
    """Preserve failure classification without serializing provider details."""

    return f"[{channel}] failed error_type={type(exc).__name__}"


def _expand_vehicle_route_scope(route_ids):
    expanded = set(route_ids or set())
    if "S" in expanded:
        expanded.update({"FS", "GS", "H"})
    return expanded


def _build_nearby_stop_context(gtfs, stops: list[dict]) -> tuple[set[str], dict[str, dict]]:
    child_stop_ids: set[str] = set()
    stop_lookup: dict[str, dict] = {}

    for stop in stops:
        parent_id = str(stop.get("stop_id") or "").strip()
        if not parent_id:
            continue
        child_ids = set(gtfs.get_child_stop_ids(parent_id))
        child_ids.add(parent_id)
        for child_id in child_ids:
            child_key = str(child_id or "").strip()
            if not child_key:
                continue
            child_stop_ids.add(child_key)
            stop_lookup[child_key] = stop
            stop_lookup[child_key.rstrip("NS")] = stop

    return child_stop_ids, stop_lookup


def _arrival_stop_context(arrival: dict, stop_lookup: dict[str, dict]) -> dict | None:
    stop_id = str(arrival.get("stop_id") or "").strip()
    if not stop_id:
        return None
    return stop_lookup.get(stop_id) or stop_lookup.get(stop_id.rstrip("NS"))


def _realtime_trip_stop_context(
    trip_updates,
    relevant_trip_ids: set[str],
    stop_locations: dict[str, dict],
) -> dict[str, list[dict]]:
    """Build a bounded fallback when a complete static trip chain is absent."""

    context: dict[str, list[dict]] = {}
    seen: set[tuple[str, str, int | None]] = set()
    for update in trip_updates:
        trip_id = str(update.get("trip_id") or "")
        stop_id = str(update.get("stop_id") or "")
        if not trip_id or trip_id not in relevant_trip_ids or not stop_id:
            continue
        sequence = update.get("stop_sequence")
        sequence = sequence if isinstance(sequence, int) else None
        identity = (trip_id, stop_id, sequence)
        if identity in seen:
            continue
        seen.add(identity)
        location = stop_locations.get(stop_id) or stop_locations.get(
            stop_id.rstrip("NS")
        )
        context.setdefault(trip_id, []).append(
            {
                "stop_id": stop_id,
                "stop_sequence": sequence,
                "stop_name": location.get("stop_name") if location else None,
                "lat": location.get("lat") if location else None,
                "lng": location.get("lng") if location else None,
                "parent_station": (
                    location.get("parent_station")
                    if location
                    else stop_id.rstrip("NS")
                ),
            }
        )
    for stops in context.values():
        if all(stop.get("stop_sequence") is not None for stop in stops):
            stops.sort(key=lambda stop: stop["stop_sequence"])
    return context


def _signal_text(value: object) -> str:
    return " ".join(str(value or "").split()).strip()


def _alert_signal_severity(alert: dict) -> str:
    alert_text = (
        f"{_signal_text(alert.get('header'))} "
        f"{_signal_text(alert.get('description'))}"
    ).upper()
    if any(keyword in alert_text for keyword in _SIGNAL_DISRUPTION_KEYWORDS):
        return "disrupted"
    if any(keyword in alert_text for keyword in _SIGNAL_CAUTION_KEYWORDS):
        return "caution"
    return "caution"


def _derive_live_network_status(
    active_alert_count: int,
    major_alert_count: int,
    stale_count: int,
    feed_failures: int,
    vehicle_entities: int,
    vehicles_without_position: int,
) -> str:
    no_position_ratio = (
        vehicles_without_position / vehicle_entities if vehicle_entities > 0 else 0
    )
    if (
        major_alert_count >= 2
        or active_alert_count >= 8
        or stale_count >= 20
        or (feed_failures > 0 and no_position_ratio >= 0.5)
        or no_position_ratio >= 0.85
    ):
        return "disrupted"
    if (
        active_alert_count > 0
        or stale_count > 0
        or feed_failures > 0
        or no_position_ratio >= 0.35
    ):
        return "caution"
    return "healthy"


def _build_live_signals(
    parsed_alerts: list[dict],
    vehicles: list[dict],
    vehicle_debug: dict,
    updated_at: int,
) -> dict:
    affected_routes = {
        str(route_id).strip().upper()
        for alert in parsed_alerts
        for route_id in (alert.get("route_ids") or [])
        if str(route_id).strip()
    }
    major_alert_count = sum(
        1 for alert in parsed_alerts if _alert_signal_severity(alert) == "disrupted"
    )
    stale_vehicles = [vehicle for vehicle in vehicles if vehicle.get("stale")]
    vehicle_entities = int(vehicle_debug.get("vehicle_entities") or len(vehicles))
    vehicles_without_position = int(vehicle_debug.get("vehicles_without_position") or 0)
    feed_failures = int(vehicle_debug.get("feed_failures") or 0)

    return {
        "network_status": _derive_live_network_status(
            active_alert_count=len(parsed_alerts),
            major_alert_count=major_alert_count,
            stale_count=len(stale_vehicles),
            feed_failures=feed_failures,
            vehicle_entities=vehicle_entities,
            vehicles_without_position=vehicles_without_position,
        ),
        "active_alert_count": len(parsed_alerts),
        "major_alert_count": major_alert_count,
        "affected_route_count": len(affected_routes),
        "tracked_vehicle_count": len(vehicles),
        "stale_vehicle_count": len(stale_vehicles),
        "routes_reporting_count": len({
            str(vehicle.get("route_id") or "").upper()
            for vehicle in vehicles
            if vehicle.get("route_id")
        }),
        "feed_failures": feed_failures,
        "vehicles_with_position": int(vehicle_debug.get("vehicles_with_position") or 0),
        "vehicles_without_position": vehicles_without_position,
        "updated_at": updated_at,
    }


async def build_live_snapshot(
    gtfs,
    lat: float,
    lng: float,
    selected_route_ids: set[str] | None = None,
):
    """Build a rider snapshot from the process-owned realtime generation."""

    network = await network_snapshot_store.get_or_refresh()
    return await _build_live_snapshot(
        gtfs,
        network,
        lat,
        lng,
        selected_route_ids,
    )


async def _build_live_snapshot(
    gtfs,
    network: NetworkSnapshot,
    lat: float,
    lng: float,
    selected_route_ids: set[str] | None = None,
):
    global _LAST_EMPTY_VEHICLE_LOG
    _t0 = time.monotonic()

    nearest_stops = find_nearest_stops(
        lat,
        lng,
        gtfs,
        NEARBY_ARRIVAL_STOP_LIMIT,
        radius_m=NEARBY_ARRIVAL_RADIUS_M,
    )
    if not nearest_stops:
        nearest_stops = find_nearest_stops(lat, lng, gtfs, 5)
    enriched_stops = []
    selected_route_ids = _normalize_route_ids(selected_route_ids)

    for stop in nearest_stops:
        routes = gtfs.get_route_ids_for_parent_stop(stop["stop_id"])
        enriched_stops.append({**stop, "route_ids": routes})

    nearest_stop = enriched_stops[0] if enriched_stops else None
    route_ids = {
        route_id
        for stop in enriched_stops
        for route_id in stop.get("route_ids", [])
    }
    nearby_child_stop_ids, nearby_stop_lookup = _build_nearby_stop_context(
        gtfs,
        enriched_stops,
    )

    trip_updates = network.trip_updates

    now = int(time.time())
    arrivals = []
    for arrival in trip_updates:
        if not arrival.get("arrival_time") or arrival.get("arrival_time") < now - 60:
            continue
        stop_context = _arrival_stop_context(arrival, nearby_stop_lookup)
        if nearby_child_stop_ids and stop_context is None:
            continue
        record = dict(arrival)
        if stop_context:
            record["parent_stop_id"] = stop_context.get("stop_id")
            record["parent_stop_name"] = stop_context.get("stop_name")
            record["station_name"] = stop_context.get("stop_name")
            record["distance_m"] = stop_context.get("distance_m")
            record["stop_lat"] = stop_context.get("stop_lat")
            record["stop_lon"] = stop_context.get("stop_lon")
        arrivals.append(record)
    arrival_lookup = network.arrival_lookup

    # Time-to-first-arrivals: nearest-stop resolution and filtering of the
    # process-owned network generation are the only primary work before paint.
    # Measured here so production telemetry can report it.
    arrivals_ms = round((time.monotonic() - _t0) * 1000)

    vehicle_route_ids = _expand_vehicle_route_scope(set(route_ids) | selected_route_ids)

    # BusTime is explicitly secondary. Primary snapshots include a fresh cached
    # bus result when one exists, but never wait on BusTime discovery or fan-out.
    cached_bus = mta_realtime.cached_nearby_bus_update(
        lat,
        lng,
        radius_m=NEARBY_ARRIVAL_RADIUS_M,
    )
    bus_arrivals = cached_bus.get("arrivals", []) if isinstance(cached_bus, dict) else []
    bus_debug = cached_bus.get("debug", {}) if isinstance(cached_bus, dict) else {}
    bus_status = cached_bus.get("status", "pending") if isinstance(cached_bus, dict) else "pending"
    arrivals.extend(arrival for arrival in bus_arrivals if isinstance(arrival, dict))
    arrivals.sort(key=lambda a: a.get("arrival_time") or 0)
    vehicles = [
        dict(vehicle)
        for vehicle in network.vehicles
        if str(vehicle.get("route_id") or "").upper() in vehicle_route_ids
    ]
    vehicle_debug = dict(network.vehicle_debug)
    vehicle_debug["scope"] = "nearest_plus_selected"
    vehicle_debug["requested_routes"] = sorted(vehicle_route_ids)
    vehicle_debug["final_markers"] = len(vehicles)
    parsed_alerts = [dict(alert) for alert in network.alerts]
    filtered_alerts = mta_realtime.filter_alerts_for_routes(parsed_alerts, set(route_ids))
    home_issues = build_nearby_transit_issues(
        gtfs=gtfs,
        alerts=parsed_alerts,
        nearby_stop_id=nearest_stop.get("stop_id") if nearest_stop else None,
        nearby_stop_name=nearest_stop.get("stop_name") if nearest_stop else None,
        nearby_route_ids=route_ids,
        selected_route_ids=selected_route_ids,
        observed_at=now,
    )

    relevant_trip_ids = {
        str(trip_id)
        for trip_id in [
            *(arrival.get("trip_id") for arrival in arrivals),
            *(vehicle.get("trip_id") for vehicle in vehicles),
        ]
        if trip_id
    }
    stop_ids = {
        str(stop_id)
        for stop_id in [
            *(vehicle.get("stop_id") for vehicle in vehicles),
            *(update.get("stop_id") for update in trip_updates),
        ]
        if stop_id
    }
    stop_locations = gtfs.get_stop_locations(list(stop_ids))
    # Rider snapshots must never perform a static trip lookup. The relevant
    # GTFS-RT records already live in the shared network snapshot and provide
    # the bounded context available for this refresh generation.
    trip_stop_context = _realtime_trip_stop_context(
        trip_updates,
        relevant_trip_ids,
        stop_locations,
    )
    for arrival in arrivals:
        vehicle_enrichment._attach_terminal_stop(
            arrival,
            trip_stop_context.get(arrival.get("trip_id") or ""),
        )
    stop_fallback_count = 0
    segment_estimate_count = 0
    missing_stop_coord_count = 0
    for vehicle in vehicles:
        stop_id = vehicle.get("stop_id")
        trip_stops = trip_stop_context.get(vehicle.get("trip_id") or "")
        vehicle_enrichment._attach_terminal_stop(vehicle, trip_stops)
        if (
            vehicle.get("position_source") != "vehicle_position"
            and trip_stops
            and vehicle_enrichment._attach_trip_segment(vehicle, trip_stops, arrival_lookup, now)
        ):
            if vehicle.get("position_source") == "polyline_estimate":
                segment_estimate_count += 1
            else:
                stop_fallback_count += 1
        elif stop_id:
            stop_location = stop_locations.get(stop_id) or stop_locations.get(stop_id.rstrip("NS"))
            if stop_location:
                vehicle["stop_name"] = stop_location["stop_name"]
                if vehicle.get("lat") is None or vehicle.get("lng") is None:
                    vehicle["lat"] = stop_location["lat"]
                    vehicle["lng"] = stop_location["lng"]
                    vehicle["position_source"] = "stop_id"
                    stop_fallback_count += 1
            else:
                missing_stop_coord_count += 1
        vehicle["route_name"] = f"{vehicle.get('route_id', '')} train"

    vehicles = [
        vehicle
        for vehicle in vehicles
        if vehicle.get("lat") is not None
        and vehicle.get("lng") is not None
        and str(vehicle.get("route_id") or "").upper() in vehicle_route_ids
    ]
    vehicle_debug["stop_coordinate_fallbacks"] = stop_fallback_count
    vehicle_debug["segment_estimates"] = segment_estimate_count
    vehicle_debug["missing_stop_coordinates"] = missing_stop_coord_count
    vehicle_debug["final_markers_after_stop_fallback"] = len(vehicles)
    signals = _build_live_signals(parsed_alerts, vehicles, vehicle_debug, now)

    if network.feed_count and not vehicles:
        log_now = time.monotonic()
        if log_now - _LAST_EMPTY_VEHICLE_LOG > 60:
            print(
                "[live_feed] MTA snapshot had no usable vehicle or stop coordinates "
                f"for scoped vehicle feeds. nearest_routes={sorted(route_ids)} "
                f"selected_routes={sorted(selected_route_ids)} "
                f"stop_fallbacks={stop_fallback_count} "
                f"missing_stop_coords={missing_stop_coord_count}"
            )
            _LAST_EMPTY_VEHICLE_LOG = log_now

    return {
        "nearest_stop": nearest_stop,
        "stops": enriched_stops,
        "arrivals": arrivals[:40],
        "alerts": filtered_alerts,
        "nearby_issues": home_issues,
        "vehicles": vehicles,
        "signals": signals,
        "bus_status": bus_status,
        "updated_at": network.updated_at,
        "degraded": False,
        "debug": {
            "network_generation": network.generation,
            "route_ids": sorted(route_ids),
            "nearest_route_ids": sorted(nearest_stop.get("route_ids", []) if nearest_stop else []),
            "nearby_route_ids": sorted(route_ids),
            "selected_route_ids": sorted(selected_route_ids),
            "vehicle_route_ids": sorted(vehicle_route_ids),
            "arrival_radius_m": NEARBY_ARRIVAL_RADIUS_M,
            "nearby_stop_count": len(enriched_stops),
            "nearby_child_stop_count": len(nearby_child_stop_ids),
            "bus_arrivals_supported": bool(bus_debug.get("bus_arrivals_supported")),
            "nearby_bus_stop_count": int(bus_debug.get("nearby_bus_stop_count") or 0),
            "bus_arrival_count": int(bus_debug.get("bus_arrival_count") or 0),
            "bus_stop_monitoring_failures": int(bus_debug.get("bus_stop_monitoring_failures") or 0),
            "bus_arrivals_reason": bus_debug.get("reason"),
            "feed_count": network.feed_count,
            "vehicle_count": len(vehicles),
            "vehicle_scope": "nearest_plus_selected",
            "vehicle_parse": vehicle_debug,
            "arrivals_ms": arrivals_ms,
            "build_ms": round((time.monotonic() - _t0) * 1000),
        },
    }
