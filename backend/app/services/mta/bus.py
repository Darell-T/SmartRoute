from __future__ import annotations

import asyncio
import json
import logging
import math
import os
from datetime import datetime
from urllib.parse import quote

import httpx

from app.services.cache import cache_get, cache_set
from app.services.geography import distance_meters
from app.services.mta import bus_runtime
from app.services.mta.config import (
    BUS_STOP_MONITORING_URL,
    BUS_STOPS_FOR_LOCATION_URL,
    BUS_URL,
    NYC_TZ,
)

_LOGGER = logging.getLogger(__name__)
NEARBY_STOPS_CACHE_TTL_S = 120
STOP_MONITORING_CACHE_TTL_S = 15


async def fetch_bus_positions(route_id) -> dict:
    client = await bus_runtime.bus_client()
    response = await client.get(
        BUS_URL,
        params={
            "key": os.getenv("MTA_BUS_API_KEY"),
            "version": 2,
            "LineRef": route_id,
        },
    )
    return response.json()


def _bus_api_key() -> str | None:
    key = os.getenv("MTA_BUS_API_KEY")
    return key.strip() if key and key.strip() else None


def _strip_mta_bus_prefix(value: str | None) -> str:
    text = str(value or "").strip()
    for prefix in ("MTA NYCT_", "MTABC_", "MTA_"):
        if text.startswith(prefix):
            return text[len(prefix):]
    return text


def _parse_siri_time(value: str | None) -> int | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=NYC_TZ)
        return int(parsed.timestamp())
    except ValueError:
        return None


def _as_list(value) -> list:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


_TEXT_KEYS = ("text", "value", "Name", "name")


def _first_text_from_values(values) -> str | None:
    for value in values:
        text = _first_text(value)
        if text:
            return text
    return None


def _first_text(value) -> str | None:
    if value is None:
        return None
    if isinstance(value, list):
        return _first_text_from_values(value)
    if isinstance(value, dict):
        return _first_text_from_values(value.get(key) for key in _TEXT_KEYS)
    text = str(value).strip()
    return text or None


def _route_ids_for_bus_stop(stop: dict) -> list[str]:
    raw_route_ids = stop.get("routeIds") or stop.get("routes") or []
    routes = []
    for route in _as_list(raw_route_ids):
        if isinstance(route, dict):
            candidate = route.get("shortName") or route.get("id")
        else:
            candidate = route
        route_id = _strip_mta_bus_prefix(_first_text(candidate))
        if route_id and route_id not in routes:
            routes.append(route_id)
    return routes


def _stops_for_location_list(payload: dict) -> list:
    """MTA BusTime's OneBusAway flavor returns ``data.stops``; vanilla
    OneBusAway returns ``data.list``.
    """
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, dict):
        return []
    stops = data.get("stops")
    if isinstance(stops, list):
        return stops
    fallback = data.get("list")
    return fallback if isinstance(fallback, list) else []


def _bus_stop_record(stop: dict, distance_m: float) -> dict:
    return {
        "stop_id": str(stop.get("id") or ""),
        "stop_name": str(stop.get("name") or "Bus stop"),
        "stop_lat": float(stop["lat"]),
        "stop_lon": float(stop["lon"]),
        "distance_m": distance_m,
        "route_ids": _route_ids_for_bus_stop(stop),
        "stop_compass": str(stop.get("direction") or ""),
    }


def _location_cache_key(lat: float, lng: float, radius_m: float) -> str:
    return f"{lat:.3f}:{lng:.3f}:{round(radius_m):d}"


async def _refresh_nearby_bus_stops(
    lat: float,
    lng: float,
    radius_m: float,
    limit: int,
    key: str,
    cache_key: str,
) -> tuple[list[dict], dict]:
    lat_span = max(0.002, radius_m / 111_320 * 2)
    lon_scale = max(0.2, abs(math.cos(math.radians(lat))))
    lon_span = max(0.002, radius_m / (111_320 * lon_scale) * 2)
    try:
        client = await bus_runtime.bus_client()
        response = await client.get(
            BUS_STOPS_FOR_LOCATION_URL,
            params={
                "key": key,
                "lat": lat,
                "lon": lng,
                "latSpan": lat_span,
                "lonSpan": lon_span,
            },
        )
        if response.status_code != 200:
            return [], {
                "bus_arrivals_supported": False,
                "reason": f"stops_for_location_http_{response.status_code}",
            }
        payload = response.json()
    except Exception as exc:  # noqa: BLE001 nearby-stop faults degrade arrivals
        return [], {"bus_arrivals_supported": False, "reason": type(exc).__name__}

    stops = []
    for stop in _stops_for_location_list(payload):
        stop_lat = stop.get("lat")
        stop_lon = stop.get("lon")
        if stop_lat is None or stop_lon is None:
            continue
        distance_m = distance_meters(lat, lng, float(stop_lat), float(stop_lon))
        if distance_m > radius_m:
            continue
        stops.append(_bus_stop_record(stop, distance_m))

    stops.sort(key=lambda stop: stop["distance_m"])
    bus_runtime.set_cached(
        bus_runtime.nearby_stops_cache,
        cache_key,
        [dict(stop) for stop in stops],
        NEARBY_STOPS_CACHE_TTL_S,
    )
    return stops[:limit], {
        "bus_arrivals_supported": True,
        "nearby_bus_stop_count": len(stops),
        "nearby_stops_cache": "ready",
    }


async def fetch_nearby_bus_stops(
    lat: float,
    lng: float,
    radius_m: float = 804.672,
    limit: int = 12,
) -> tuple[list[dict], dict]:
    key = _bus_api_key()
    if not key:
        return [], {"bus_arrivals_supported": False, "reason": "missing_mta_bus_api_key"}

    cache_key = _location_cache_key(lat, lng, radius_m)
    cached = bus_runtime.get_cached(bus_runtime.nearby_stops_cache, cache_key)
    if isinstance(cached, list):
        return [dict(stop) for stop in cached[:limit]], {
            "bus_arrivals_supported": True,
            "nearby_bus_stop_count": len(cached),
            "nearby_stops_cache": "cached",
        }

    return await bus_runtime.share_inflight(
        f"nearby-stops:{cache_key}",
        lambda: _refresh_nearby_bus_stops(lat, lng, radius_m, limit, key, cache_key),
    )


async def fetch_bus_stop_monitoring(stop_id: str, visits: int = 4) -> dict:
    key = _bus_api_key()
    if not key:
        return {}

    monitoring_ref = _strip_mta_bus_prefix(stop_id)
    cache_key = f"{monitoring_ref}:{visits}"
    cached = bus_runtime.get_cached(bus_runtime.stop_monitoring_cache, cache_key)
    if isinstance(cached, dict):
        return cached

    async def refresh() -> dict:
        client = await bus_runtime.bus_client()
        response = await client.get(
            BUS_STOP_MONITORING_URL,
            params={
                "key": key,
                "version": 2,
                "MonitoringRef": monitoring_ref,
                "MaximumStopVisits": visits,
            },
        )
        if response.status_code != 200:
            return {}
        payload = response.json()
        if isinstance(payload, dict):
            bus_runtime.set_cached(
                bus_runtime.stop_monitoring_cache,
                cache_key,
                payload,
                STOP_MONITORING_CACHE_TTL_S,
            )
            return payload
        return {}

    return await bus_runtime.share_inflight(f"stop-monitoring:{cache_key}", refresh)


_CALL_ARRIVAL_KEYS = (
    "ExpectedArrivalTime",
    "ExpectedDepartureTime",
    "AimedArrivalTime",
    "AimedDepartureTime",
)


def _arrival_from_stop_visit(visit: dict, stop: dict) -> dict | None:
    journey = visit.get("MonitoredVehicleJourney", {})
    call = journey.get("MonitoredCall", {})
    arrival_time = None
    for key in _CALL_ARRIVAL_KEYS:
        arrival_time = _parse_siri_time(_first_text(call.get(key)))
        if arrival_time:
            break
    if not arrival_time:
        return None
    route_id = _strip_mta_bus_prefix(
        _first_text(journey.get("PublishedLineName"))
        or _first_text(journey.get("LineRef"))
    )
    if not route_id:
        return None
    return {
        "route_id": route_id,
        "trip_id": journey.get("FramedVehicleJourneyRef", {}).get("DatedVehicleJourneyRef"),
        "stop_id": _strip_mta_bus_prefix(
            _first_text(call.get("StopPointRef")) or stop.get("stop_id")
        ),
        "arrival_time": arrival_time,
        "delay": None,
        "direction": _first_text(journey.get("DirectionRef")),
        "terminal_stop_name": _first_text(journey.get("DestinationName")),
        "parent_stop_id": stop.get("stop_id"),
        "parent_stop_name": stop.get("stop_name"),
        "station_name": stop.get("stop_name"),
        "distance_m": stop.get("distance_m"),
        "stop_compass": str(stop.get("stop_compass") or ""),
        "stop_lat": stop.get("stop_lat"),
        "stop_lon": stop.get("stop_lon"),
        "mode": "bus",
    }


def parse_bus_stop_monitoring(payload: dict, stop: dict) -> list[dict]:
    service_delivery = payload.get("Siri", {}).get("ServiceDelivery", {})
    visits = []
    for delivery in _as_list(service_delivery.get("StopMonitoringDelivery")):
        if not isinstance(delivery, dict):
            continue
        visits.extend(_as_list(delivery.get("MonitoredStopVisit")))

    arrivals = []
    for visit in visits:
        if not isinstance(visit, dict):
            continue
        arrival = _arrival_from_stop_visit(visit, stop)
        if arrival:
            arrivals.append(arrival)
    return arrivals


async def get_stalled_buses(route_ids: set) -> list:
    if not route_ids:
        return []

    stalled_buses = []
    tasks = [fetch_bus_positions(line) for line in route_ids]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    for result in results:
        if isinstance(result, Exception):
            print(f"[mta_feed] bus feed fetch error: {result}")
            continue
        stalled_buses.extend(parse_stalled_bus_positions(result))

    return stalled_buses

def _vehicle_activities_from_payload(payload: object) -> list:
    if not isinstance(payload, dict):
        return []
    service_delivery = payload.get("Siri", {}).get("ServiceDelivery", {})
    if not isinstance(service_delivery, dict):
        return []
    deliveries = service_delivery.get("VehicleMonitoringDelivery", [{}])
    if not isinstance(deliveries, list) or not deliveries or not isinstance(deliveries[0], dict):
        return []
    vehicles = deliveries[0].get("VehicleActivity", [])
    if not isinstance(vehicles, list):
        return []
    return vehicles


def _stalled_bus_from_activity(vehicle) -> dict | None:
    if not isinstance(vehicle, dict):
        return None
    vehicle_position = vehicle.get("MonitoredVehicleJourney", {})
    if not isinstance(vehicle_position, dict):
        return None
    progress_rate = vehicle_position.get("ProgressRate")
    progress_status = vehicle_position.get("ProgressStatus", [])
    if progress_rate != "noProgress" or "layover" in progress_status:
        return None
    line_ref = vehicle_position.get("LineRef", "")
    location = vehicle_position.get("VehicleLocation")
    recorded_at_time = vehicle.get("RecordedAtTime") or vehicle_position.get("RecordedAtTime")
    if not line_ref or location is None:
        return None
    return {
        "route_id": line_ref.replace("MTA NYCT_", ""),
        "location": location,
        "time_recorded": recorded_at_time,
    }


def parse_stalled_bus_positions(payload: object) -> list[dict]:
    stalled_buses = []
    for vehicle in _vehicle_activities_from_payload(payload):
        stalled = _stalled_bus_from_activity(vehicle)
        if stalled:
            stalled_buses.append(stalled)
    return stalled_buses


BUS_STOPS_FOR_ROUTE_URL = "https://bustime.mta.info/api/where/stops-for-route/{route_id}.json"
_CACHE_TTL_SECONDS = 6 * 3600
# BusTime splits the same Google route across NYCT and MTABC OBA prefixes.
_AGENCY_PREFIXES = ("MTA NYCT_", "MTABC_")


def _route_as_list(value):
    return value if isinstance(value, list) else []


def normalize_google_bus_route_id(route_id: str) -> list[str]:
    cleaned = str(route_id or "").strip().upper()
    if not cleaned:
        return []
    candidates = [cleaned]
    if cleaned.endswith("-SBS"):
        # Google M15-SBS is BusTime/OBA M15+.
        candidates.append(cleaned[: -len("-SBS")] + "+")
    return candidates


def _stops_by_id_from_payload(data: dict) -> dict:
    references = data.get("references") if isinstance(data.get("references"), dict) else {}
    raw_stops = (
        data.get("stops")
        if isinstance(data.get("stops"), list)
        else references.get("stops")
    )
    stops_by_id = {}
    for stop in _route_as_list(raw_stops):
        if not isinstance(stop, dict):
            continue
        stop_id = str(stop.get("id") or "")
        lat = stop.get("lat")
        lon = stop.get("lon")
        if not stop_id or lat is None or lon is None:
            continue
        stops_by_id[stop_id] = {
            "name": str(stop.get("name") or "Bus stop"),
            "lat": float(lat),
            "lon": float(lon),
        }
    return stops_by_id


def _stop_id_lists_from_grouping(grouping: dict) -> list[list[str]]:
    ordered = []
    for group in _route_as_list(grouping.get("stopGroups")):
        if not isinstance(group, dict):
            continue
        stop_ids = [str(s) for s in _route_as_list(group.get("stopIds")) if s]
        if stop_ids:
            ordered.append(stop_ids)
    return ordered


def parse_stops_for_route(payload: dict) -> dict:
    """Tolerates both vanilla OneBusAway (data.entry.stopGroupings +
    data.references.stops) and MTA's flat variant (data.stopGroupings +
    data.stops)."""
    empty = {"stops_by_id": {}, "ordered_groups": []}
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, dict):
        return empty
    stops_by_id = _stops_by_id_from_payload(data)
    if not stops_by_id:
        return empty
    entry = data.get("entry") if isinstance(data.get("entry"), dict) else {}
    raw_groupings = (
        data.get("stopGroupings")
        if isinstance(data.get("stopGroupings"), list)
        else entry.get("stopGroupings")
    )
    ordered_groups = []
    for grouping in _route_as_list(raw_groupings):
        if not isinstance(grouping, dict):
            continue
        ordered_groups.extend(_stop_id_lists_from_grouping(grouping))
    return {"stops_by_id": stops_by_id, "ordered_groups": ordered_groups}


def _nearest_index(coords: dict, stop_ids: list, stops_by_id: dict):
    lat = coords.get("latitude")
    lng = coords.get("longitude")
    if lat is None or lng is None:
        return None
    best = None
    for index, stop_id in enumerate(stop_ids):
        stop = stops_by_id.get(stop_id)
        if not stop:
            continue
        d = distance_meters(float(lat), float(lng), stop["lat"], stop["lon"])
        if best is None or d < best[1]:
            best = (index, d)
    return best


def _scored_forward_slice(
    stop_ids: list,
    board_coords: dict,
    exit_coords: dict,
    stops_by_id: dict,
    max_snap_m: float,
):
    board = _nearest_index(board_coords, stop_ids, stops_by_id)
    if not board:
        return None
    exit_ = _nearest_index(exit_coords, stop_ids, stops_by_id)
    if not exit_:
        return None
    if board[1] > max_snap_m:
        return None
    if exit_[1] > max_snap_m:
        return None
    # Direction groups are one-way; board must precede exit on that sequence.
    if board[0] >= exit_[0]:
        return None
    return (board[1] + exit_[1], stop_ids[board[0] : exit_[0] + 1])


def slice_route_stops(parsed: dict, board_coords: dict, exit_coords: dict, max_snap_m: float = 250) -> list:
    stops_by_id = parsed.get("stops_by_id") or {}
    scored_slices = []
    for stop_ids in parsed.get("ordered_groups") or []:
        scored = _scored_forward_slice(
            stop_ids, board_coords, exit_coords, stops_by_id, max_snap_m
        )
        if scored is not None:
            scored_slices.append(scored)
    if not scored_slices:
        return []
    best_slice = min(scored_slices, key=lambda item: item[0])[1]
    return [
        {
            "name": stops_by_id[stop_id]["name"],
            "lat": stops_by_id[stop_id]["lat"],
            "lng": stops_by_id[stop_id]["lon"],
        }
        for stop_id in best_slice
        if stop_id in stops_by_id
    ]


async def _stops_payload_for_prefix(prefix: str, short_id: str, key: str):
    client = await bus_runtime.bus_client()
    try:
        response = await client.get(
            BUS_STOPS_FOR_ROUTE_URL.format(
                route_id=quote(f"{prefix}{short_id}", safe=""),
            ),
            params={
                "key": key,
                "includePolylines": "false",
                "version": 2,
            },
            timeout=8.0,
        )
    except httpx.RequestError as exc:
        _LOGGER.debug(
            "Bus stops-for-route request failed prefix=%s reason=%s",
            prefix,
            type(exc).__name__,
        )
        return None
    if response.status_code != 200:
        return None
    try:
        return response.json()
    except ValueError:
        return None


def _parsed_cached_route_stops(cached) -> dict | None:
    try:
        parsed = parse_stops_for_route(json.loads(cached))
    except (ValueError, TypeError):
        return None
    return parsed if parsed["stops_by_id"] else None


async def _first_parsed_stops_for_route(route_id: str, key: str):
    for short_id in normalize_google_bus_route_id(route_id):
        for prefix in _AGENCY_PREFIXES:
            payload = await _stops_payload_for_prefix(prefix, short_id, key)
            if not payload:
                continue
            parsed = parse_stops_for_route(payload)
            if parsed["stops_by_id"]:
                return parsed, payload
    return None, None


async def fetch_bus_route_stop_groups(route_id: str) -> dict | None:
    """None leaves callers on board and exit markers only."""
    key = _bus_api_key()
    if not key:
        return None

    cache_key = f"oba:stops-for-route:{str(route_id or '').strip().upper()}"
    cached = cache_get(cache_key)
    if cached:
        parsed = _parsed_cached_route_stops(cached)
        if parsed is not None:
            return parsed

    parsed, payload = await _first_parsed_stops_for_route(route_id, key)
    if parsed is None:
        return None
    cache_set(cache_key, json.dumps(payload).encode("utf-8"), _CACHE_TTL_SECONDS)
    return parsed
