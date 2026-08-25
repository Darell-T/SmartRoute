from __future__ import annotations

import os
from datetime import datetime

from app.services.mta.config import NYC_TZ, get_route_color, route_to_feed
from app.services.mta.feeds import _gtfs_realtime_pb2, fetch_feeds


def _vehicle_status_name(vehicle) -> str:
    try:
        return _gtfs_realtime_pb2().VehiclePosition.VehicleStopStatus.Name(vehicle.current_status)
    except Exception:
        return str(vehicle.current_status)


def parse_vehicle_positions(
    rawBytes: bytes,
    source: str = "unknown",
    diagnostics: list[dict] | None = None,
    include_stop_only: bool = False,
) -> list:
    locations = _gtfs_realtime_pb2().FeedMessage()
    locations.ParseFromString(rawBytes)

    vehicle_positions = []
    stats = {
        "source": source,
        "bytes": len(rawBytes),
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

    for entity in locations.entity:
        if entity.HasField("trip_update"):
            stats["trip_updates"] += 1
        if entity.HasField("vehicle"):
            stats["vehicle_entities"] += 1
            vehicle = entity.vehicle
            route_id_for_stats = vehicle.trip.route_id or "?"
            stats["routes"][route_id_for_stats] = stats["routes"].get(route_id_for_stats, 0) + 1

            if not vehicle.HasField("position"):
                stats["vehicles_without_position"] += 1
                if len(stats["sample_without_position"]) < 3:
                    stats["sample_without_position"].append({
                        "entity_id": entity.id,
                        "trip_id": vehicle.trip.trip_id,
                        "route_id": route_id_for_stats,
                        "stop_id": vehicle.stop_id,
                        "current_stop_sequence": vehicle.current_stop_sequence or None,
                        "status": _vehicle_status_name(vehicle),
                    })
                if include_stop_only and route_id_for_stats != "?" and vehicle.stop_id:
                    vehicle_positions.append({
                        "id": entity.id or vehicle.trip.trip_id or f"{route_id_for_stats}-{vehicle.stop_id}",
                        "trip_id": vehicle.trip.trip_id or None,
                        "route_id": route_id_for_stats,
                        "lat": None,
                        "lng": None,
                        "stop_id": vehicle.stop_id,
                        "status": _vehicle_status_name(vehicle),
                        "current_stop_sequence": vehicle.current_stop_sequence or None,
                        "timestamp": vehicle.timestamp or None,
                        "color": get_route_color(route_id_for_stats),
                        "position_source": "stop_id_pending_coords",
                    })
                continue

            trip_id = vehicle.trip.trip_id
            route_id = vehicle.trip.route_id
            coordinates = (vehicle.position.latitude, vehicle.position.longitude)
            stop_id = vehicle.stop_id
            status = _vehicle_status_name(vehicle)
            timestamp = vehicle.timestamp

            stats["vehicles_with_position"] += 1
            if not route_id:
                stats["missing_route"] += 1
            if vehicle.position.latitude == 0 and vehicle.position.longitude == 0:
                stats["zero_coordinates"] += 1
            else:
                stats["valid_positions"] += 1

            vehicle_positions.append({
                "id": entity.id or trip_id or f"{route_id}-{stop_id}-{timestamp}",
                "trip_id": trip_id,
                "route_id": route_id,
                "coordinates": coordinates,
                "lat": vehicle.position.latitude,
                "lng": vehicle.position.longitude,
                "stop_id": stop_id,
                "status": status,
                "current_stop_sequence": vehicle.current_stop_sequence or None,
                "timestamp": timestamp,
                "color": get_route_color(route_id),
                "position_source": "vehicle_position",
            })

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


def _build_subway_vehicle_positions(raw_feeds, requested_set, route_ids, debug, include_stop_only):
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

    now = datetime.now(tz=NYC_TZ).timestamp()
    vehicles = []
    seen_ids = set()
    for pos in all_positions:
        lat = pos.get("lat")
        lng = pos.get("lng")
        route_id = pos.get("route_id")
        if (lat is None or lng is None) and not include_stop_only:
            continue
        if lat is not None and lng is not None and lat == 0 and lng == 0:
            continue
        if not route_id:
            continue
        if requested_set and route_id not in requested_set:
            continue

        vehicle_id = pos.get("id") or f"{route_id}-{pos.get('trip_id', '')}-{pos.get('stop_id', '')}"
        if vehicle_id in seen_ids:
            continue
        seen_ids.add(vehicle_id)

        timestamp = pos.get("timestamp") or None
        age_seconds = round(now - timestamp) if timestamp else None
        vehicles.append({
            "id": vehicle_id,
            "trip_id": pos.get("trip_id") or None,
            "route_id": route_id,
            "lat": lat,
            "lng": lng,
            "stop_id": pos.get("stop_id") or None,
            "status": pos.get("status") or None,
            "current_stop_sequence": pos.get("current_stop_sequence") or None,
            "timestamp": timestamp,
            "age_seconds": age_seconds,
            "stale": bool(age_seconds is not None and age_seconds > 300),
            "color": get_route_color(route_id),
            "position_source": pos.get("position_source") or "vehicle_position",
        })

    if debug:
        debug_payload = {
            "scope": "all_subway" if route_ids is None else "nearest_routes",
            "requested_routes": sorted(requested_set),
            "feeds_ok": len(raw_feeds),
            "feed_failures": len({route_to_feed[route] for route in requested_set}) - len(raw_feeds),
            "entities": sum(item["entities"] for item in feed_diagnostics),
            "trip_updates": sum(item["trip_updates"] for item in feed_diagnostics),
            "vehicle_entities": sum(item["vehicle_entities"] for item in feed_diagnostics),
            "vehicles_with_position": sum(item["vehicles_with_position"] for item in feed_diagnostics),
            "vehicles_without_position": sum(item["vehicles_without_position"] for item in feed_diagnostics),
            "zero_coordinates": sum(item["zero_coordinates"] for item in feed_diagnostics),
            "raw_positions": len(all_positions),
            "final_markers": len(vehicles),
            "stop_only_candidates": sum(
                1 for pos in all_positions if pos.get("position_source") == "stop_id_pending_coords"
            ),
            "feeds": feed_diagnostics,
        }
        _log_vehicle_diagnostics(debug_payload)
        return vehicles, debug_payload

    return vehicles


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
