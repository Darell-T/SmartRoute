"""Place a train along its trip stop sequence when GTFS-RT omits coordinates."""


def _base_stop_id(stop_id: str | None) -> str | None:
    if not stop_id:
        return None
    return stop_id[:-1] if stop_id[-1:] in {"N", "S"} else stop_id


def _find_trip_stop_index(stops: list[dict], stop_id: str | None, sequence: int | None):
    if sequence is not None:
        for idx, stop in enumerate(stops):
            if stop.get("stop_sequence") == sequence:
                return idx

    base = _base_stop_id(stop_id)
    for idx, stop in enumerate(stops):
        sid = stop.get("stop_id")
        if sid == stop_id or _base_stop_id(sid) == base:
            return idx
    return None


def _arrival_lookup_key(trip_id: str | None, stop_id: str | None):
    if not trip_id or not stop_id:
        return None
    return (trip_id, stop_id)


def _attach_terminal_stop(record: dict, trip_stops: list[dict] | None):
    if not trip_stops:
        return
    terminal = trip_stops[-1]
    if not terminal:
        return
    record["terminal_stop_id"] = terminal.get("stop_id")
    record["terminal_stop_name"] = terminal.get("stop_name")


def _estimate_segment_progress(vehicle: dict, arrival_lookup: dict[tuple[str, str], int], now: int) -> float:
    status = str(vehicle.get("status") or "").upper()
    if "STOPPED_AT" in status:
        return 1.0
    if "INCOMING_AT" in status:
        return 0.88

    key = _arrival_lookup_key(vehicle.get("trip_id"), vehicle.get("stop_id"))
    arrival_time = arrival_lookup.get(key) if key else None
    if arrival_time:
        eta_seconds = arrival_time - now
        if eta_seconds <= 0:
            return 0.94
        if eta_seconds >= 180:
            return 0.22
        return max(0.22, min(0.94, 1 - (eta_seconds / 180) * 0.72))

    if "IN_TRANSIT_TO" in status:
        return 0.55
    return 0.72


def _interpolate_stop_segment(start: dict, end: dict, progress: float):
    progress = max(0, min(1, progress))
    return {
        "lat": start["lat"] + (end["lat"] - start["lat"]) * progress,
        "lng": start["lng"] + (end["lng"] - start["lng"]) * progress,
    }


def _segment_has_coordinates(start: dict, end: dict) -> bool:
    return all(
        isinstance(stop.get(axis), (int, float))
        for stop in (start, end)
        for axis in ("lat", "lng")
    )


def _attach_trip_segment(vehicle: dict, trip_stops: list[dict], arrival_lookup: dict[tuple[str, str], int], now: int):
    idx = _find_trip_stop_index(
        trip_stops,
        vehicle.get("stop_id"),
        vehicle.get("current_stop_sequence"),
    )
    if idx is None:
        return False

    target = trip_stops[idx]
    previous = trip_stops[idx - 1] if idx > 0 else None
    nxt = trip_stops[idx + 1] if idx + 1 < len(trip_stops) else None
    status = str(vehicle.get("status") or "").upper()

    if previous and "STOPPED_AT" not in status:
        start = previous
        end = target
        progress = _estimate_segment_progress(vehicle, arrival_lookup, now)
    elif previous:
        start = previous
        end = target
        progress = 1.0
    elif nxt:
        start = target
        end = nxt
        progress = 0.0
    else:
        return False

    if not _segment_has_coordinates(start, end):
        return False

    estimate = _interpolate_stop_segment(start, end, progress)
    vehicle["lat"] = estimate["lat"]
    vehicle["lng"] = estimate["lng"]
    vehicle["stop_name"] = target["stop_name"]
    vehicle["position_source"] = "stop_id" if progress >= 0.99 else "polyline_estimate"
    vehicle["segment"] = {
        "from_stop_id": start["stop_id"],
        "from_stop_name": start["stop_name"],
        "from_lat": start["lat"],
        "from_lng": start["lng"],
        "to_stop_id": end["stop_id"],
        "to_stop_name": end["stop_name"],
        "to_lat": end["lat"],
        "to_lng": end["lng"],
        "progress": progress,
    }
    return True


def _attach_map_position(
    vehicle: dict,
    trip_stops: list[dict] | None,
    stop_locations: dict,
    arrival_lookup: dict,
    now: int,
) -> str | None:
    if (
        vehicle.get("position_source") != "vehicle_position"
        and trip_stops
        and _attach_trip_segment(vehicle, trip_stops, arrival_lookup, now)
    ):
        source = vehicle.get("position_source")
        return source if source in {"polyline_estimate", "stop_id"} else "stop_id"
    stop_id = vehicle.get("stop_id")
    if not stop_id:
        return None
    stop_location = stop_locations.get(stop_id) or stop_locations.get(stop_id.rstrip("NS"))
    if not stop_location:
        return "missing"
    vehicle["stop_name"] = stop_location["stop_name"]
    if vehicle.get("lat") is None or vehicle.get("lng") is None:
        vehicle["lat"] = stop_location["lat"]
        vehicle["lng"] = stop_location["lng"]
        vehicle["position_source"] = "stop_id"
        return "stop_id"
    return None


def place_vehicle_markers(
    vehicles: list[dict],
    *,
    trip_stop_context: dict,
    stop_locations: dict,
    arrival_lookup: dict,
    now: int,
    vehicle_route_ids: set[str],
) -> tuple[list[dict], dict[str, int]]:
    """Attach map coordinates and drop vehicles that still have none."""
    tallies = {"polyline_estimate": 0, "stop_id": 0, "missing": 0}
    for vehicle in vehicles:
        trip_stops = trip_stop_context.get(vehicle.get("trip_id") or "")
        _attach_terminal_stop(vehicle, trip_stops)
        placed = _attach_map_position(
            vehicle, trip_stops, stop_locations, arrival_lookup, now
        )
        if placed in tallies:
            tallies[placed] += 1
        vehicle["route_name"] = f"{vehicle.get('route_id', '')} train"
    placed_vehicles = [
        vehicle
        for vehicle in vehicles
        if vehicle.get("lat") is not None
        and vehicle.get("lng") is not None
        and str(vehicle.get("route_id") or "").upper() in vehicle_route_ids
    ]
    return placed_vehicles, {
        "stop_coordinate_fallbacks": tallies["stop_id"],
        "segment_estimates": tallies["polyline_estimate"],
        "missing_stop_coordinates": tallies["missing"],
        "final_markers_after_stop_fallback": len(placed_vehicles),
    }
