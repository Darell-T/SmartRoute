from __future__ import annotations

import asyncio
import math
import os
from datetime import datetime

from app.services.mta.config import (
    BUS_STOP_MONITORING_URL,
    BUS_STOPS_FOR_LOCATION_URL,
    BUS_URL,
    NYC_TZ,
)
from app.services.mta import bus_runtime

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
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
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

        from app.utils.geo import distance_meters

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
