"""Live-feed snapshot assembly."""

from __future__ import annotations

import asyncio
import time

from app.services import mta_feed
from app.services.live_feed import vehicle_enrichment
from app.utils.geo import find_nearest_stops

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


async def _safe_nearby_bus_arrivals(lat: float, lng: float) -> tuple[list[dict], dict]:
    try:
        return await mta_feed.fetch_nearby_bus_arrivals(
            lat,
            lng,
            radius_m=NEARBY_ARRIVAL_RADIUS_M,
        )
    except Exception as exc:
        print(f"[live_feed] BusTime nearby arrivals failed: {type(exc).__name__}: {exc!r}")
        return [], {
            "bus_arrivals_supported": False,
            "reason": type(exc).__name__,
            "bus_arrival_count": 0,
        }


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


async def _build_live_snapshot(
    gtfs, lat: float, lng: float, selected_route_ids: set[str] | None = None,
    emit=None,
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

    feeds = await mta_feed.fetch_feeds(route_ids, "arrivals_nearest")

    parse_tasks = [asyncio.to_thread(mta_feed.parse_bytes, feed) for feed in feeds]
    parsed_lists = await asyncio.gather(*parse_tasks, return_exceptions=True)

    trip_updates = []
    for parsed in parsed_lists:
        if isinstance(parsed, Exception):
            print(f"[live_feed] parse_bytes failed: {parsed}")
            continue
        trip_updates.extend(parsed)

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
    arrival_lookup = {
        key: arrival["arrival_time"]
        for arrival in trip_updates
        if arrival.get("arrival_time")
        for key in [
            vehicle_enrichment._arrival_lookup_key(
                arrival.get("trip_id"),
                arrival.get("stop_id"),
            )
        ]
        if key
    }

    # Time-to-first-arrivals: everything above (nearest stops + the nearby
    # subway feed, served from the warm cache) is all the rider waits on for the
    # first paint. Measured here so production telemetry can report it.
    arrivals_ms = round((time.monotonic() - _t0) * 1000)

    # Progressive first paint: the nearby subway arrivals are ready now (and
    # served from the warm feed cache), so push them immediately instead of
    # making the rider wait on the slower vehicles/alerts/bus fan-out below.
    # Only the FIRST snapshot for a location passes an
    # emit callback; periodic refreshes send the full snapshot in one message.
    if emit is not None:
        await emit({
            "nearest_stop": nearest_stop,
            "stops": enriched_stops,
            "arrivals": sorted(arrivals, key=lambda a: a.get("arrival_time") or 0)[:40],
            "alerts": [],
            "vehicles": [],
            "signals": None,
            "updated_at": now,
            "degraded": False,
            "debug": {"partial": True, "arrivals_ms": arrivals_ms},
            "partial": True,
        })

    vehicle_route_ids = _expand_vehicle_route_scope(set(route_ids) | selected_route_ids)

    raw_alerts, vehicle_result, bus_result = await asyncio.gather(
        mta_feed.fetch_service_alerts(),
        mta_feed.get_all_subway_vehicle_positions(
            vehicle_route_ids,
            debug=True,
            include_stop_only=True,
        ),
        _safe_nearby_bus_arrivals(lat, lng),
    )
    bus_arrivals, bus_debug = bus_result
    arrivals.extend(bus_arrivals)
    arrivals.sort(key=lambda a: a.get("arrival_time") or 0)
    vehicles, vehicle_debug = vehicle_result
    parsed_alerts = mta_feed.parse_service_alerts(raw_alerts) if raw_alerts else []
    filtered_alerts = mta_feed.filter_alerts_for_routes(parsed_alerts, set(route_ids))

    stop_ids = [v.get("stop_id") for v in vehicles if v.get("stop_id")]
    stop_locations = gtfs.get_stop_locations(stop_ids)
    trip_stop_context = gtfs.get_trip_stop_context([
        *(arrival.get("trip_id") for arrival in arrivals if arrival.get("trip_id")),
        *(v.get("trip_id") for v in vehicles if v.get("trip_id")),
    ])
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

    if feeds and not vehicles:
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
        "vehicles": vehicles,
        "signals": signals,
        "updated_at": now,
        "degraded": len(feeds) == 0 and len(route_ids) > 0,
        "debug": {
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
            "feed_count": len(feeds),
            "vehicle_count": len(vehicles),
            "vehicle_scope": "nearest_plus_selected",
            "vehicle_parse": vehicle_debug,
            "arrivals_ms": arrivals_ms,
            "build_ms": round((time.monotonic() - _t0) * 1000),
        },
    }
