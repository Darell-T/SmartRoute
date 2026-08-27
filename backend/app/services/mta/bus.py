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


def _first_text(value) -> str | None:
    if value is None:
        return None
    if isinstance(value, list):
        for item in value:
            text = _first_text(item)
            if text:
                return text
        return None
    if isinstance(value, dict):
        for key in ("text", "value", "Name", "name"):
            text = _first_text(value.get(key))
            if text:
                return text
        return None
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
    """Extract the stop array from a stops-for-location response.

    MTA BusTime's OneBusAway flavor returns ``data.stops``; vanilla
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
    # Nearby stops do not materially differ within roughly one city block.
    return f"{lat:.3f}:{lng:.3f}:{round(radius_m):d}"


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

    async def refresh() -> tuple[list[dict], dict]:
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
        except Exception as exc:
            return [], {"bus_arrivals_supported": False, "reason": type(exc).__name__}

        from app.services.geography import distance_meters

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

    return await bus_runtime.share_inflight(f"nearby-stops:{cache_key}", refresh)


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
        journey = visit.get("MonitoredVehicleJourney", {})
        call = journey.get("MonitoredCall", {})
        arrival_time = (
            _parse_siri_time(_first_text(call.get("ExpectedArrivalTime")))
            or _parse_siri_time(_first_text(call.get("ExpectedDepartureTime")))
            or _parse_siri_time(_first_text(call.get("AimedArrivalTime")))
            or _parse_siri_time(_first_text(call.get("AimedDepartureTime")))
        )
        if not arrival_time:
            continue
        route_id = _strip_mta_bus_prefix(
            _first_text(journey.get("PublishedLineName"))
            or _first_text(journey.get("LineRef"))
        )
        if not route_id:
            continue
        arrivals.append({
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
        })
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

def parse_stalled_bus_positions(payload: object) -> list[dict]:
    """Extract stalled buses from one provider-shaped SIRI payload."""
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

    stalled_buses = []
    for vehicle in vehicles:
        if not isinstance(vehicle, dict):
            continue
        vehicle_position = vehicle.get("MonitoredVehicleJourney", {})
        if not isinstance(vehicle_position, dict):
            continue
        progress_rate = vehicle_position.get("ProgressRate")
        progress_status = vehicle_position.get("ProgressStatus", [])
        if progress_rate != "noProgress" or "layover" in progress_status:
            continue
        line_ref = vehicle_position.get("LineRef", "")
        location = vehicle_position.get("VehicleLocation")
        recorded_at_time = vehicle.get("RecordedAtTime") or vehicle_position.get("RecordedAtTime")
        if not line_ref or location is None:
            continue
        stalled_buses.append({
            "route_id": line_ref.replace("MTA NYCT_", ""),
            "location": location,
            "time_recorded": recorded_at_time,
        })
    return stalled_buses


BUS_STOPS_FOR_ROUTE_URL = "https://bustime.mta.info/api/where/stops-for-route/{route_id}.json"
_CACHE_TTL_SECONDS = 6 * 3600
# MTA splits bus routes across two OBA agencies.
_AGENCY_PREFIXES = ("MTA NYCT_", "MTABC_")


def _route_as_list(value):
    """Keep stops-for-route parsing strict about provider list fields."""
    return value if isinstance(value, list) else []


def normalize_google_bus_route_id(route_id: str) -> list[str]:
    """Candidate OBA short ids for a Google bus route id, in try order.
    Google publishes Select Bus Service as "M15-SBS"; OBA uses "M15+"."""
    cleaned = str(route_id or "").strip().upper()
    if not cleaned:
        return []
    candidates = [cleaned]
    if cleaned.endswith("-SBS"):
        candidates.append(cleaned[: -len("-SBS")] + "+")
    return candidates


def parse_stops_for_route(payload: dict) -> dict:
    """Normalize a stops-for-route payload into
    {stops_by_id: {id: {name, lat, lon}}, ordered_groups: [[stop_id, ...]]}.

    Tolerates both vanilla OneBusAway (data.entry.stopGroupings +
    data.references.stops) and MTA's flat variant (data.stopGroupings +
    data.stops) -- same defensive posture as _stops_for_location_list."""
    empty = {"stops_by_id": {}, "ordered_groups": []}
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, dict):
        return empty

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
        for group in _route_as_list(grouping.get("stopGroups")):
            if not isinstance(group, dict):
                continue
            stop_ids = [str(s) for s in _route_as_list(group.get("stopIds")) if s]
            if stop_ids:
                ordered_groups.append(stop_ids)

    if not stops_by_id:
        return empty
    return {"stops_by_id": stops_by_id, "ordered_groups": ordered_groups}


def _nearest_index(coords: dict, stop_ids: list, stops_by_id: dict):
    """(index, distance_m) of the stop in the group nearest to coords."""
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


def slice_route_stops(parsed: dict, board_coords: dict, exit_coords: dict, max_snap_m: float = 250) -> list:
    """Ordered [{name, lat, lng}] from board stop to exit stop (inclusive).

    Considers every direction group; requires board index < exit index
    (rules out the reverse-direction group, whose nearest stops appear in
    the wrong order); picks the group with the smallest combined snap
    distance. Empty list when no group qualifies within max_snap_m."""
    stops_by_id = parsed.get("stops_by_id") or {}
    best_slice = None
    best_score = None
    for stop_ids in parsed.get("ordered_groups") or []:
        board = _nearest_index(board_coords, stop_ids, stops_by_id)
        exit_ = _nearest_index(exit_coords, stop_ids, stops_by_id)
        if not board or not exit_:
            continue
        if board[1] > max_snap_m or exit_[1] > max_snap_m:
            continue
        if board[0] >= exit_[0]:
            continue
        score = board[1] + exit_[1]
        if best_score is None or score < best_score:
            best_score = score
            best_slice = stop_ids[board[0] : exit_[0] + 1]

    if not best_slice:
        return []
    return [
        {
            "name": stops_by_id[stop_id]["name"],
            "lat": stops_by_id[stop_id]["lat"],
            "lng": stops_by_id[stop_id]["lon"],
        }
        for stop_id in best_slice
        if stop_id in stops_by_id
    ]


async def fetch_bus_route_stop_groups(route_id: str) -> dict | None:
    """Fetch + parse stops-for-route for a Google bus route id. Tries each
    agency prefix and SBS normalization until one returns stops. Cached;
    None on any failure (callers degrade to board/exit-only markers)."""
    key = _bus_api_key()
    if not key:
        return None

    cache_key = f"oba:stops-for-route:{str(route_id or '').strip().upper()}"
    cached = cache_get(cache_key)
    if cached:
        try:
            parsed = parse_stops_for_route(json.loads(cached))
            if parsed["stops_by_id"]:
                return parsed
        except (ValueError, TypeError):
            pass

    for short_id in normalize_google_bus_route_id(route_id):
        for prefix in _AGENCY_PREFIXES:
            try:
                async with httpx.AsyncClient(timeout=8.0) as client:
                    response = await client.get(
                        BUS_STOPS_FOR_ROUTE_URL.format(
                            route_id=quote(f"{prefix}{short_id}", safe=""),
                        ),
                        params={
                            "key": key,
                            "includePolylines": "false",
                            "version": 2,
                        },
                    )
            except httpx.RequestError as exc:
                _LOGGER.debug(
                    "Bus stops-for-route request failed prefix=%s reason=%s",
                    prefix,
                    type(exc).__name__,
                )
                continue
            if response.status_code != 200:
                continue
            try:
                payload = response.json()
            except ValueError:
                continue
            parsed = parse_stops_for_route(payload)
            if parsed["stops_by_id"]:
                cache_set(cache_key, json.dumps(payload).encode("utf-8"), _CACHE_TTL_SECONDS)
                return parsed

    return None
