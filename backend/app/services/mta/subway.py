from __future__ import annotations

import os
from datetime import datetime

from app.services.mta.config import NYC_TZ, get_route_color, route_to_feed
from app.services.mta.feeds import _gtfs_realtime_pb2, fetch_feeds


def _vehicle_status_name(vehicle) -> str:
    try:
        return _gtfs_realtime_pb2().VehiclePosition.VehicleStopStatus.Name(vehicle.current_status)
    except (ValueError, TypeError, AttributeError):
        return str(vehicle.current_status)


def _record_unpositioned_vehicle(entity, vehicle, route_id, stats) -> None:
    stats["vehicles_without_position"] += 1
    if len(stats["sample_without_position"]) < 3:
        stats["sample_without_position"].append({
            "entity_id": entity.id,
            "trip_id": vehicle.trip.trip_id,
            "route_id": route_id,
            "stop_id": vehicle.stop_id,
            "current_stop_sequence": vehicle.current_stop_sequence or None,
            "status": _vehicle_status_name(vehicle),
        })


def _stop_only_vehicle(entity, vehicle, route_id) -> dict:
    return {
        "id": entity.id or vehicle.trip.trip_id or f"{route_id}-{vehicle.stop_id}",
        "trip_id": vehicle.trip.trip_id or None,
        "route_id": route_id,
        "lat": None,
        "lng": None,
        "stop_id": vehicle.stop_id,
        "status": _vehicle_status_name(vehicle),
        "current_stop_sequence": vehicle.current_stop_sequence or None,
        "timestamp": vehicle.timestamp or None,
        "color": get_route_color(route_id),
        "position_source": "stop_id_pending_coords",
    }


def _positioned_vehicle(entity, vehicle, stats) -> dict:
    trip_id = vehicle.trip.trip_id
    route_id = vehicle.trip.route_id
    timestamp = vehicle.timestamp
    stats["vehicles_with_position"] += 1
    if not route_id:
        stats["missing_route"] += 1
    if vehicle.position.latitude == 0 and vehicle.position.longitude == 0:
        stats["zero_coordinates"] += 1
    else:
        stats["valid_positions"] += 1
    return {
        "id": entity.id or trip_id or f"{route_id}-{vehicle.stop_id}-{timestamp}",
        "trip_id": trip_id,
        "route_id": route_id,
        "coordinates": (vehicle.position.latitude, vehicle.position.longitude),
        "lat": vehicle.position.latitude,
        "lng": vehicle.position.longitude,
        "stop_id": vehicle.stop_id,
        "status": _vehicle_status_name(vehicle),
        "current_stop_sequence": vehicle.current_stop_sequence or None,
        "timestamp": timestamp,
        "color": get_route_color(route_id),
        "position_source": "vehicle_position",
    }


def _positions_for_entity(entity, include_stop_only: bool, stats: dict) -> list:
    if entity.HasField("trip_update"):
        stats["trip_updates"] += 1
    if not entity.HasField("vehicle"):
        return []
    stats["vehicle_entities"] += 1
    vehicle = entity.vehicle
    route_id = vehicle.trip.route_id or "?"
    stats["routes"][route_id] = stats["routes"].get(route_id, 0) + 1
    if not vehicle.HasField("position"):
        _record_unpositioned_vehicle(entity, vehicle, route_id, stats)
        if include_stop_only and route_id != "?" and vehicle.stop_id:
            return [_stop_only_vehicle(entity, vehicle, route_id)]
        return []
    return [_positioned_vehicle(entity, vehicle, stats)]


def parse_vehicle_positions(
    raw_bytes: bytes,
    source: str = "unknown",
    diagnostics: list[dict] | None = None,
    include_stop_only: bool = False,
) -> list:
    locations = _gtfs_realtime_pb2().FeedMessage()
    locations.ParseFromString(raw_bytes)
    stats = {
        "source": source,
        "bytes": len(raw_bytes),
        "entities": len(locations.entity),
        "trip_updates": 0,
        "vehicle_entities": 0,
        "vehicles_with_position": 0,
        "vehicles_without_position": 0,
        "zero_coordinates": 0,
        "missing_route": 0,
        "valid_positions": 0,
        "routes": {},
        "sample_without_position": [],
    }
    vehicle_positions = []
    for entity in locations.entity:
        vehicle_positions.extend(_positions_for_entity(entity, include_stop_only, stats))
    if diagnostics is not None:
        diagnostics.append(stats)
    return vehicle_positions


def _log_vehicle_diagnostics(debug: dict):
    if os.getenv("BACKEND_VERBOSE_LOGS", "0") != "1":
        return
    print(
        "[mta_feed][vehicles] "
        f"scope={debug['scope']} requested_routes={debug['requested_routes']} "
        f"feeds_ok={debug['feeds_ok']} feed_failures={debug['feed_failures']} "
        f"entities={debug['entities']} trip_updates={debug['trip_updates']} "
        f"vehicle_entities={debug['vehicle_entities']} "
        f"with_position={debug['vehicles_with_position']} "
        f"without_position={debug['vehicles_without_position']} "
        f"zero_coords={debug['zero_coordinates']} raw_positions={debug['raw_positions']} "
        f"stop_only_candidates={debug.get('stop_only_candidates', 0)} "
        f"final_markers={debug['final_markers']}"
    )
    for feed in debug["feeds"]:
        sample = feed.get("sample_without_position") or []
        print(
            "[mta_feed][vehicles][feed] "
            f"{feed['source']} bytes={feed['bytes']} entities={feed['entities']} "
            f"trip_updates={feed['trip_updates']} vehicle_entities={feed['vehicle_entities']} "
            f"with_position={feed['vehicles_with_position']} "
            f"without_position={feed['vehicles_without_position']} "
            f"valid_positions={feed['valid_positions']} routes={feed['routes']} "
            f"sample_without_position={sample}"
        )


def _unique_requested_vehicle_id(pos, requested_set, seen_ids):
    route_id = pos.get("route_id")
    if not route_id:
        return None
    if requested_set and route_id not in requested_set:
        return None
    vehicle_id = pos.get("id") or f"{route_id}-{pos.get('trip_id', '')}-{pos.get('stop_id', '')}"
    if vehicle_id in seen_ids:
        return None
    seen_ids.add(vehicle_id)
    return vehicle_id


def _accepted_subway_vehicle_id(pos, requested_set, include_stop_only, seen_ids):
    lat = pos.get("lat")
    lng = pos.get("lng")
    if lat == 0 and lng == 0:
        return None
    if (lat is None or lng is None) and not include_stop_only:
        return None
    return _unique_requested_vehicle_id(pos, requested_set, seen_ids)


def _subway_vehicle_marker(pos, vehicle_id, now):
    timestamp = pos.get("timestamp") or None
    age_seconds = None
    stale = False
    if timestamp:
        age_seconds = round(now - timestamp)
        stale = age_seconds > 300
    return {
        "id": vehicle_id,
        "trip_id": pos.get("trip_id") or None,
        "route_id": pos.get("route_id"),
        "lat": pos.get("lat"),
        "lng": pos.get("lng"),
        "stop_id": pos.get("stop_id") or None,
        "status": pos.get("status") or None,
        "current_stop_sequence": pos.get("current_stop_sequence") or None,
        "timestamp": timestamp,
        "age_seconds": age_seconds,
        "stale": stale,
        "color": get_route_color(pos.get("route_id")),
        "position_source": pos.get("position_source") or "vehicle_position",
    }


def _select_subway_vehicle_markers(all_positions, requested_set, include_stop_only, now):
    vehicles = []
    seen_ids = set()
    for pos in all_positions:
        vehicle_id = _accepted_subway_vehicle_id(
            pos, requested_set, include_stop_only, seen_ids
        )
        if vehicle_id is None:
            continue
        vehicles.append(_subway_vehicle_marker(pos, vehicle_id, now))
    return vehicles


def _vehicle_position_debug(
    raw_feeds, requested_set, route_ids, all_positions, vehicles, feed_diagnostics
):
    scope = "nearest_routes"
    if route_ids is None:
        scope = "all_subway"
    expected_feeds = {route_to_feed[route] for route in requested_set}
    stop_only_candidates = 0
    entities = 0
    trip_updates = 0
    vehicle_entities = 0
    vehicles_with_position = 0
    vehicles_without_position = 0
    zero_coordinates = 0
    for pos in all_positions:
        if pos.get("position_source") == "stop_id_pending_coords":
            stop_only_candidates += 1
    for item in feed_diagnostics:
        entities += item["entities"]
        trip_updates += item["trip_updates"]
        vehicle_entities += item["vehicle_entities"]
        vehicles_with_position += item["vehicles_with_position"]
        vehicles_without_position += item["vehicles_without_position"]
        zero_coordinates += item["zero_coordinates"]
    return {
        "scope": scope,
        "requested_routes": sorted(requested_set),
        "feeds_ok": len(raw_feeds),
        "feed_failures": len(expected_feeds) - len(raw_feeds),
        "entities": entities,
        "trip_updates": trip_updates,
        "vehicle_entities": vehicle_entities,
        "vehicles_with_position": vehicles_with_position,
        "vehicles_without_position": vehicles_without_position,
        "zero_coordinates": zero_coordinates,
        "raw_positions": len(all_positions),
        "final_markers": len(vehicles),
        "stop_only_candidates": stop_only_candidates,
        "feeds": feed_diagnostics,
    }


def build_subway_vehicle_positions(raw_feeds, requested_set, route_ids, debug, include_stop_only):
    all_positions = []
    feed_diagnostics: list[dict] = []
    for feed in raw_feeds:
        all_positions.extend(
            parse_vehicle_positions(
                feed["content"],
                source=feed["suffix"],
                diagnostics=feed_diagnostics,
                include_stop_only=include_stop_only,
            )
        )
    vehicles = _select_subway_vehicle_markers(
        all_positions,
        requested_set,
        include_stop_only,
        datetime.now(tz=NYC_TZ).timestamp(),
    )
    if not debug:
        return vehicles
    debug_payload = _vehicle_position_debug(
        raw_feeds, requested_set, route_ids, all_positions, vehicles, feed_diagnostics
    )
    _log_vehicle_diagnostics(debug_payload)
    return vehicles, debug_payload


async def get_stalled_trains(route_ids: set) -> list:
    if not route_ids:
        return []

    raw_feeds = await fetch_feeds(list(route_ids))
    all_positions = []
    for feed in raw_feeds:
        all_positions.extend(parse_vehicle_positions(feed))

    return detect_stalled_trains(
        all_positions,
        route_ids,
        now_timestamp=datetime.now(tz=NYC_TZ).timestamp(),
    )


def detect_stalled_trains(
    positions: list[dict],
    route_ids: set[str],
    *,
    now_timestamp: float,
) -> list[dict]:
    """Return the existing stalled-train signal from parsed GTFS-RT positions.

    Fetching and protobuf parsing remain separate.  This pure detection step
    permits recorded feeds to exercise the exact live stale-position rule.
    """
    stalled = []
    for pos in positions:
        if not isinstance(pos, dict):
            continue
        timestamp = pos.get("timestamp")
        route_id = pos.get("route_id")
        if route_id in route_ids and timestamp and (now_timestamp - timestamp) > 300:
            stalled.append({
                "route_id": route_id,
                "stop_id": pos.get("stop_id"),
                "status": pos.get("status"),
                "stalled_minutes": round((now_timestamp - timestamp) / 60),
            })
    return stalled
