# mta_feed.py - MTA GTFS-RT Feed Fetcher
#
# This file will contain:
# - Functions to fetch and parse MTA GTFS-RT protobuf feeds:
#   - Trip Updates: Real-time arrival/departure predictions per stop
#   - Service Alerts: Planned work, delays, suspensions
#   - Vehicle Positions: Live train/bus locations
# - Use gtfs-realtime-bindings package to parse protobuf responses
# - Cache parsed feed data in Redis with 30-60 second TTL
# - Handle feed fetch errors gracefully with fallback to cached data
# - Periodic background task to poll feeds and update cache
# - Feed URLs:
#   - Subway: https://api-endpoint.mta.info/Dataservice/mtagtfsfeeds/nyct%2Fgtfs
#   - Bus: https://bustime.mta.info/api/siri/vehicle-monitoring.json

from google.transit import gtfs_realtime_pb2
from datetime import datetime
from zoneinfo import ZoneInfo
import httpx
import asyncio
import json

NYC_TZ = ZoneInfo("America/New_York")

BASE_URL = "https://api-endpoint.mta.info/Dataservice/mtagtfsfeeds/nyct%2Fgtfs"

route_to_feed = {
    "A": "ace", "C": "ace", "E": "ace",
    "B": "bdfm", "D": "bdfm", "F": "bdfm", "M": "bdfm",
    "G": "g",
    "J": "jz", "Z": "jz",
    "N": "nqrw", "Q": "nqrw", "R": "nqrw", "W": "nqrw",
    "L": "l",
    "1": "", "2": "", "3": "", "4": "", "5": "", "6": "", "7": "",
    "SI": "si",
}


async def fetch_feeds(routes: list) -> list:
    from app.utils.cache import cache_get, cache_set

    unique_suffixes = set()
    for route in routes:
        if route in route_to_feed:
            unique_suffixes.add(route_to_feed[route])

    if not unique_suffixes:
        print("Error: No valid train routes provided.")
        return []

    urls = []
    for suffix in unique_suffixes:
        url = f"{BASE_URL}-{suffix}" if suffix else BASE_URL
        urls.append(url)

    results = []
    urls_to_fetch = []

    for url in urls:
        cached = cache_get(url)
        if cached:
            results.append((url, cached))
        else:
            urls_to_fetch.append(url)

    if urls_to_fetch:
        async with httpx.AsyncClient() as client:
            tasks = [client.get(url) for url in urls_to_fetch]
            responses = await asyncio.gather(*tasks)
        for url, response in zip(urls_to_fetch, responses):
            cache_set(url, response.content, 30)
            results.append((url, response.content))

    return [content for _, content in results]
    


def parse_bytes(rawBytes: bytes) -> list:
    user_feed = gtfs_realtime_pb2.FeedMessage()
    user_feed.ParseFromString(rawBytes)

    trip_updates  = []


    for entity in user_feed.entity:
        if entity.HasField("trip_update"):
            trip = entity.trip_update
            
            trip_id = trip.trip.trip_id
            route_id = trip.trip.route_id

            for stop in trip.stop_time_update:
                trip_updates.append({"route_id": route_id,
                "trip_id": trip_id,
                "stop_id": stop.stop_id,
                "arrival_time": stop.arrival.time if stop.arrival.time else None,
                "delay": stop.arrival.delay})
            
    
    return trip_updates

ALERTS_URL = "https://api-endpoint.mta.info/Dataservice/mtagtfsfeeds/camsys%2Fsubway-alerts"


async def fetch_service_alerts() -> bytes:
    from app.utils.cache import cache_get, cache_set

    cached = cache_get(ALERTS_URL)
    if cached:
        return cached

    async with httpx.AsyncClient() as client:
        response = await client.get(ALERTS_URL)
    cache_set(ALERTS_URL, response.content, 60)
    return response.content


def parse_service_alerts(rawBytes: bytes) -> list:
    feed = gtfs_realtime_pb2.FeedMessage()
    feed.ParseFromString(rawBytes)

    now = datetime.now(tz=NYC_TZ).timestamp()
    alerts = []

    for entity in feed.entity:
        if not entity.HasField("alert"):
            continue

        alert = entity.alert

        # Extract active_period (take first if present)
        start = None
        end = None
        if alert.active_period:
            period = alert.active_period[0]
            start = period.start if period.start else None
            end = period.end if period.end else None

        # Filter to currently active alerts
        if start and now < start:
            continue
        if end and end > 0 and now > end:
            continue

        # Extract english text from header_text
        header = ""
        if alert.header_text and alert.header_text.translation:
            for t in alert.header_text.translation:
                if t.language == "en" or not header:
                    header = t.text
                    if t.language == "en":
                        break

        # Extract english text from description_text
        description = ""
        if alert.description_text and alert.description_text.translation:
            for t in alert.description_text.translation:
                if t.language == "en" or not description:
                    description = t.text
                    if t.language == "en":
                        break

        # Collect route_ids and stop_ids from informed_entity
        route_ids = set()
        stop_ids = set()
        for ie in alert.informed_entity:
            if ie.route_id:
                route_ids.add(ie.route_id)
            if ie.stop_id:
                stop_ids.add(ie.stop_id)

        alerts.append({
            "alert_id": entity.id,
            "header": header,
            "description": description,
            "route_ids": list(route_ids),
            "stop_ids": list(stop_ids),
            "start": start,
            "end": end,
        })

    return alerts


def filter_alerts_for_routes(alerts: list, route_ids: set) -> list:
    return [a for a in alerts if set(a["route_ids"]) & route_ids]


def parse_vehicle_positions(rawBytes: bytes) -> list:
    locations = gtfs_realtime_pb2.FeedMessage()
    locations.ParseFromString(rawBytes)

    vehicle_positions = []

    for entity in locations.entity:
        if entity.HasField("vehicle"):
            vehicle = entity.vehicle


            trip_id = vehicle.trip.trip_id
            route_id = vehicle.trip.route_id
            coordinates = (vehicle.position.latitude, vehicle.position.longitude)
            stop_id = vehicle.stop_id
            status = str(vehicle.current_status)
            timestamp = vehicle.timestamp


            vehicle_positions.append({
                "trip_id": trip_id,
                "route_id": route_id,
                "coordinates": coordinates,
                "stop_id": stop_id,
                "status": status,
                "timestamp": timestamp
            })
    
    return vehicle_positions

