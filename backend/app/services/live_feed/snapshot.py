from __future__ import annotations

import re
import time
from datetime import UTC, datetime

from app.services.geography import find_nearest_stops
from app.services.live_feed import vehicle_enrichment
from app.services.live_feed.network_snapshot import (
    NetworkSnapshot,
    network_snapshot_store,
)
from app.services.mta import realtime as mta_realtime

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
        if value is not None and str(value).strip()
    }


def _normalized_routes(values) -> set[str]:
    return {
        str(value).strip().upper()
        for value in (values or [])
        if str(value).strip()
    }


def _location_verbose_log(connection_id: int, selected_route_ids: set[str]) -> str:
    return (
        f"[ws_live_feed:{connection_id}] location "
        f"selected_routes={sorted(selected_route_ids)}"
    )


def _socket_failure_log(channel: str, exc: Exception) -> str:
    return f"[{channel}] failed error_type={type(exc).__name__}"


def _expand_vehicle_route_scope(route_ids):
    expanded = set(route_ids or set())
    if "S" in expanded:
        expanded.update({"FS", "GS", "H"})
    return expanded


def _pattern_parent_positions(pattern) -> dict[str, int]:
    return {
        str(stop_id).rstrip("NS"): position
        for stop_id, position in pattern.get("pos", {}).items()
    }


def _positions_on_pattern(positions: dict[str, int], stop_ids) -> list[int]:
    return [positions[value] for value in stop_ids if value in positions]


def _best_hops_on_pattern(pattern, origin_ids: set[str], issue_ids: set[str], index):
    positions = _pattern_parent_positions(pattern)
    origin_positions = _positions_on_pattern(positions, origin_ids)
    issue_positions = _positions_on_pattern(positions, issue_ids)
    if not origin_positions:
        return None
    if not issue_positions:
        return None
    best = None
    for origin_position in origin_positions:
        for issue_position in issue_positions:
            hops = abs(issue_position - origin_position)
            if best is not None and hops >= best["hops"]:
                continue
            stop_id = str(pattern["stop_ids"][issue_position]).rstrip("NS")
            stop = index.stops.get(stop_id, {})
            best = {
                "hops": hops,
                "stop_id": stop_id,
                "stop_name": stop.get("name") or stop_id,
            }
    return best


def _route_stop_match(index, route_id: str, origin_ids: set[str], issue_ids: set[str]):
    best = None
    for pattern in index.route_patterns.get(route_id, []):
        match = _best_hops_on_pattern(pattern, origin_ids, issue_ids, index)
        if match is None:
            continue
        if best is None or match["hops"] < best["hops"]:
            best = match
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


def _confirmed_stall_issue(
    alert: dict,
    route_id: str,
    match: dict,
    selected_routes: set[str],
    nearby_stop_label: str,
    observed: str,
) -> dict:
    relevance = "planned_route" if route_id in selected_routes else "nearby_line"
    alert_id = alert.get("alert_id") or f"mta-{route_id}-{match['stop_id']}"
    return {
        "id": str(alert_id),
        "route_ids": [route_id],
        "station_id": match["stop_id"],
        "station_name": match["stop_name"],
        "stops_away": match["hops"],
        "confidence": "confirmed",
        "status": "stalled",
        "summary": _summary(route_id, match, nearby_stop_label),
        "source_types": ["mta_service_alert"],
        "observed_at": observed,
        "relevance": relevance,
    }


def _stall_candidates_for_alert(
    alert: dict,
    index,
    origin_ids: set[str],
    nearby_routes: set[str],
    selected_routes: set[str],
    nearby_stop_name: str | None,
    nearby_stop_id: str | None,
    observed_at: int,
) -> list[dict]:
    text = " ".join(str(alert.get(field) or "") for field in ("header", "description"))
    if not _STALL_PATTERN.search(text):
        return []
    issue_stop_ids = _normalized_ids(alert.get("stop_ids"))
    if not issue_stop_ids:
        return []
    observed = datetime.fromtimestamp(observed_at, tz=UTC).isoformat()
    nearby_stop_label = nearby_stop_name or str(nearby_stop_id)
    candidates = []
    for route_id in sorted(_normalized_routes(alert.get("route_ids")) & nearby_routes):
        match = _route_stop_match(index, route_id, origin_ids, issue_stop_ids)
        if match is None:
            continue
        if match["hops"] > MAX_NEARBY_STATION_HOPS:
            continue
        candidates.append(
            _confirmed_stall_issue(
                alert,
                route_id,
                match,
                selected_routes,
                nearby_stop_label,
                observed,
            )
        )
    return candidates


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
    """Raw vehicle age is stale telemetry, not stall evidence.

    Strong inference can enter this contract only after a separate
    repeated-observation service has corroborated it.
    """

    index = gtfs.__dict__.get("_pattern_index")
    origin_ids = _normalized_ids([nearby_stop_id])
    nearby_routes = _normalized_routes(nearby_route_ids)
    selected_routes = _normalized_routes(selected_route_ids)
    if index is None:
        return []
    if not origin_ids:
        return []
    if not nearby_routes:
        return []

    candidates = []
    for alert in alerts:
        candidates.extend(
            _stall_candidates_for_alert(
                alert,
                index,
                origin_ids,
                nearby_routes,
                selected_routes,
                nearby_stop_name,
                nearby_stop_id,
                observed_at,
            )
        )
    candidates.sort(
        key=lambda issue: (
            issue["stops_away"],
            issue["route_ids"][0],
            issue["id"],
        )
    )
    return candidates[:1]


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


def _nearby_stop_projection(gtfs, lat: float, lng: float) -> dict:
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
    for stop in nearest_stops:
        routes = gtfs.get_route_ids_for_parent_stop(stop["stop_id"])
        enriched_stops.append({**stop, "route_ids": routes})
    nearest_stop = enriched_stops[0] if enriched_stops else None
    route_ids = {
        route_id
        for stop in enriched_stops
        for route_id in stop.get("route_ids", [])
    }
    child_stop_ids, stop_lookup = _build_nearby_stop_context(gtfs, enriched_stops)
    return {
        "stops": enriched_stops,
        "nearest_stop": nearest_stop,
        "route_ids": route_ids,
        "child_stop_ids": child_stop_ids,
        "stop_lookup": stop_lookup,
    }


def _arrival_stop_context(arrival: dict, stop_lookup: dict[str, dict]) -> dict | None:
    stop_id = str(arrival.get("stop_id") or "").strip()
    if not stop_id:
        return None
    return stop_lookup.get(stop_id) or stop_lookup.get(stop_id.rstrip("NS"))


def _nearby_arrivals(trip_updates, nearby_child_stop_ids, nearby_stop_lookup, now: int) -> list[dict]:
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
    return arrivals


def _overlay_cached_bus_arrivals(arrivals: list[dict], lat: float, lng: float):
    cached_bus = mta_realtime.cached_nearby_bus_update(
        lat,
        lng,
        radius_m=NEARBY_ARRIVAL_RADIUS_M,
    )
    bus_debug: dict = {}
    bus_status = "pending"
    if isinstance(cached_bus, dict):
        arrivals.extend(
            arrival
            for arrival in cached_bus.get("arrivals", [])
            if isinstance(arrival, dict)
        )
        bus_debug = cached_bus.get("debug", {})
        bus_status = cached_bus.get("status", "pending")
    arrivals.sort(key=lambda arrival: arrival.get("arrival_time") or 0)
    return arrivals, bus_debug, bus_status


def _scoped_network_vehicles(network: NetworkSnapshot, route_ids, selected_route_ids):
    vehicle_route_ids = _expand_vehicle_route_scope(set(route_ids) | selected_route_ids)
    vehicles = [
        dict(vehicle)
        for vehicle in network.vehicles
        if str(vehicle.get("route_id") or "").upper() in vehicle_route_ids
    ]
    vehicle_debug = dict(network.vehicle_debug)
    vehicle_debug["scope"] = "nearest_plus_selected"
    vehicle_debug["requested_routes"] = sorted(vehicle_route_ids)
    vehicle_debug["final_markers"] = len(vehicles)
    return vehicles, vehicle_debug, vehicle_route_ids


def _field_ids(rows, key: str) -> set[str]:
    return {str(row.get(key)) for row in rows if row.get(key)}


def _append_realtime_trip_stop(
    context: dict[str, list[dict]],
    seen: set[tuple[str, str, int | None]],
    update,
    relevant_trip_ids: set[str],
    stop_locations: dict[str, dict],
) -> None:
    trip_id = str(update.get("trip_id") or "")
    stop_id = str(update.get("stop_id") or "")
    if not trip_id:
        return
    if trip_id not in relevant_trip_ids:
        return
    if not stop_id:
        return
    sequence = update.get("stop_sequence")
    if not isinstance(sequence, int):
        sequence = None
    identity = (trip_id, stop_id, sequence)
    if identity in seen:
        return
    seen.add(identity)
    location = stop_locations.get(stop_id)
    if location is None:
        location = stop_locations.get(stop_id.rstrip("NS"))
    row = {
        "stop_id": stop_id,
        "stop_sequence": sequence,
        "stop_name": None,
        "lat": None,
        "lng": None,
        "parent_station": stop_id.rstrip("NS"),
    }
    if location:
        row["stop_name"] = location.get("stop_name")
        row["lat"] = location.get("lat")
        row["lng"] = location.get("lng")
        row["parent_station"] = location.get("parent_station")
    context.setdefault(trip_id, []).append(row)


def _realtime_trip_stop_context(
    trip_updates,
    relevant_trip_ids: set[str],
    stop_locations: dict[str, dict],
) -> dict[str, list[dict]]:
    context: dict[str, list[dict]] = {}
    seen: set[tuple[str, str, int | None]] = set()
    for update in trip_updates:
        _append_realtime_trip_stop(
            context, seen, update, relevant_trip_ids, stop_locations
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


def _alert_affected_routes(parsed_alerts: list[dict]) -> set[str]:
    routes: set[str] = set()
    for alert in parsed_alerts:
        routes |= _normalized_routes(alert.get("route_ids"))
    return routes


def _vehicle_reporting_routes(vehicles: list[dict]) -> set[str]:
    return {
        str(vehicle.get("route_id") or "").upper()
        for vehicle in vehicles
        if vehicle.get("route_id")
    }


def _log_empty_vehicle_scope(
    route_ids, selected_route_ids, stop_fallback_count, missing_stop_coord_count
) -> None:
    global _LAST_EMPTY_VEHICLE_LOG
    log_now = time.monotonic()
    if log_now - _LAST_EMPTY_VEHICLE_LOG <= 60:
        return
    print(
        "[live_feed] MTA snapshot had no usable vehicle or stop coordinates "
        f"for scoped vehicle feeds. nearest_routes={sorted(route_ids)} "
        f"selected_routes={sorted(selected_route_ids)} "
        f"stop_fallbacks={stop_fallback_count} "
        f"missing_stop_coords={missing_stop_coord_count}"
    )
    _LAST_EMPTY_VEHICLE_LOG = log_now


def _build_live_signals(
    parsed_alerts: list[dict],
    vehicles: list[dict],
    vehicle_debug: dict,
    updated_at: int,
) -> dict:
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
        "affected_route_count": len(_alert_affected_routes(parsed_alerts)),
        "tracked_vehicle_count": len(vehicles),
        "stale_vehicle_count": len(stale_vehicles),
        "routes_reporting_count": len(_vehicle_reporting_routes(vehicles)),
        "feed_failures": feed_failures,
        "vehicles_with_position": int(vehicle_debug.get("vehicles_with_position") or 0),
        "vehicles_without_position": vehicles_without_position,
        "updated_at": updated_at,
    }


def _rider_snapshot_debug(
    *,
    network: NetworkSnapshot,
    nearby: dict,
    selected_route_ids,
    vehicle_route_ids,
    bus_debug: dict,
    vehicles: list[dict],
    vehicle_debug: dict,
    arrivals_ms: int,
    started: float,
) -> dict:
    nearest_stop = nearby["nearest_stop"]
    route_ids = nearby["route_ids"]
    nearest_route_ids = nearest_stop.get("route_ids", []) if nearest_stop else []
    return {
        "network_generation": network.generation,
        "route_ids": sorted(route_ids),
        "nearest_route_ids": sorted(nearest_route_ids),
        "nearby_route_ids": sorted(route_ids),
        "selected_route_ids": sorted(selected_route_ids),
        "vehicle_route_ids": sorted(vehicle_route_ids),
        "arrival_radius_m": NEARBY_ARRIVAL_RADIUS_M,
        "nearby_stop_count": len(nearby["stops"]),
        "nearby_child_stop_count": len(nearby["child_stop_ids"]),
        "bus_arrivals_supported": bool(bus_debug.get("bus_arrivals_supported")),
        "nearby_bus_stop_count": int(bus_debug.get("nearby_bus_stop_count") or 0),
        "bus_arrival_count": int(bus_debug.get("bus_arrival_count") or 0),
        "bus_stop_monitoring_failures": int(
            bus_debug.get("bus_stop_monitoring_failures") or 0
        ),
        "bus_arrivals_reason": bus_debug.get("reason"),
        "feed_count": network.feed_count,
        "vehicle_count": len(vehicles),
        "vehicle_scope": "nearest_plus_selected",
        "vehicle_parse": vehicle_debug,
        "arrivals_ms": arrivals_ms,
        "build_ms": round((time.monotonic() - started) * 1000),
    }


async def build_live_snapshot(
    gtfs,
    lat: float,
    lng: float,
    selected_route_ids: set[str] | None = None,
):
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
    started = time.monotonic()
    nearby = _nearby_stop_projection(gtfs, lat, lng)
    selected_route_ids = _normalized_routes(selected_route_ids)
    now = int(time.time())
    arrivals = _nearby_arrivals(
        network.trip_updates,
        nearby["child_stop_ids"],
        nearby["stop_lookup"],
        now,
    )
    arrivals_ms = round((time.monotonic() - started) * 1000)
    arrivals, bus_debug, bus_status = _overlay_cached_bus_arrivals(arrivals, lat, lng)
    vehicles, vehicle_debug, vehicle_route_ids = _scoped_network_vehicles(
        network, nearby["route_ids"], selected_route_ids
    )
    parsed_alerts = [dict(alert) for alert in network.alerts]
    filtered_alerts = mta_realtime.filter_alerts_for_routes(
        parsed_alerts, set(nearby["route_ids"])
    )
    nearest_stop = nearby["nearest_stop"]
    nearby_issues = build_nearby_transit_issues(
        gtfs=gtfs,
        alerts=parsed_alerts,
        nearby_stop_id=nearest_stop.get("stop_id") if nearest_stop else None,
        nearby_stop_name=nearest_stop.get("stop_name") if nearest_stop else None,
        nearby_route_ids=nearby["route_ids"],
        selected_route_ids=selected_route_ids,
        observed_at=now,
    )
    stop_locations = gtfs.get_stop_locations(
        list(_field_ids(vehicles, "stop_id") | _field_ids(network.trip_updates, "stop_id"))
    )
    trip_stop_context = _realtime_trip_stop_context(
        network.trip_updates,
        _field_ids(arrivals, "trip_id") | _field_ids(vehicles, "trip_id"),
        stop_locations,
    )
    for arrival in arrivals:
        vehicle_enrichment._attach_terminal_stop(
            arrival,
            trip_stop_context.get(arrival.get("trip_id") or ""),
        )
    vehicles, placement = vehicle_enrichment.place_vehicle_markers(
        vehicles,
        trip_stop_context=trip_stop_context,
        stop_locations=stop_locations,
        arrival_lookup=network.arrival_lookup,
        now=now,
        vehicle_route_ids=vehicle_route_ids,
    )
    vehicle_debug["stop_coordinate_fallbacks"] = placement["stop_coordinate_fallbacks"]
    vehicle_debug["segment_estimates"] = placement["segment_estimates"]
    vehicle_debug["missing_stop_coordinates"] = placement["missing_stop_coordinates"]
    vehicle_debug["final_markers_after_stop_fallback"] = placement[
        "final_markers_after_stop_fallback"
    ]
    signals = _build_live_signals(parsed_alerts, vehicles, vehicle_debug, now)
    if network.feed_count and not vehicles:
        _log_empty_vehicle_scope(
            nearby["route_ids"],
            selected_route_ids,
            placement["stop_coordinate_fallbacks"],
            placement["missing_stop_coordinates"],
        )
    return {
        "nearest_stop": nearest_stop,
        "stops": nearby["stops"],
        "arrivals": arrivals[:40],
        "alerts": filtered_alerts,
        "nearby_issues": nearby_issues,
        "vehicles": vehicles,
        "signals": signals,
        "bus_status": bus_status,
        "updated_at": network.updated_at,
        "degraded": False,
        "debug": _rider_snapshot_debug(
            network=network,
            nearby=nearby,
            selected_route_ids=selected_route_ids,
            vehicle_route_ids=vehicle_route_ids,
            bus_debug=bus_debug,
            vehicles=vehicles,
            vehicle_debug=vehicle_debug,
            arrivals_ms=arrivals_ms,
            started=started,
        ),
    }
