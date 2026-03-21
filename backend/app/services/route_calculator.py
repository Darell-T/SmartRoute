# route_calculator.py - Route Computation Engine
#
# This file will contain:
# - Core route calculation logic:
#   1. Find nearest stops to origin and destination coordinates
#   2. Query static GTFS data for possible routes (direct and with transfers)
#   3. Calculate baseline travel times from stop_times.txt
#   4. Apply real-time delay adjustments from Trip Updates feed
#   5. Add transfer time estimates (walking time between platforms + buffer)
#   6. Rank routes by estimated arrival time and reliability
# - Return multiple route options with:
#   - Route legs (transit, transfer, walk)
#   - Per-leg duration and current delay
#   - Total trip duration
#   - Recommended departure time to arrive by target time
# - Use historical delay patterns for confidence scoring
import json
from app.utils.gtfs_static import GTFSStaticData
from app.utils.geo import geocode_address, find_nearest_stops, walking_time_minutes
from app.services.mta_feed import fetch_feeds, parse_bytes, parse_vehicle_positions
import asyncio
from datetime import datetime
from zoneinfo import ZoneInfo

gtfs = GTFSStaticData()

async def nearest_stops(origin: str, dest: str) -> dict:

    coordinates = await asyncio.gather(
        asyncio.to_thread(geocode_address, origin),
        asyncio.to_thread (geocode_address, dest))

    origin_coords = coordinates[0]
    dest_coords = coordinates[1]

    #use these coordinates to find  the nearest stops
    if origin_coords is  None or dest_coords is None:
        return "Not a valid address or address out of NYC area"
    
    origin_stops = find_nearest_stops(origin_coords[0],origin_coords[1], gtfs.stops_by_id, 5)
    dest_stops = find_nearest_stops(dest_coords[0],dest_coords[1], gtfs.stops_by_id, 5)

    return {
        "origin_stops": origin_stops,
        "dest_stops": dest_stops,
        "origin_coords": {"lat": origin_coords[0], "lng": origin_coords[1]},
        "dest_coords": {"lat": dest_coords[0], "lng": dest_coords[1]},
    }

def possible_routes(stops: dict) -> list:

    origin_routes = {}
    dest_routes = {}
    route_options = [] 

    for origin_stop in stops["origin_stops"]:
        origin_routes[origin_stop["stop_id"]] = gtfs.get_routes_for_stops(origin_stop["stop_id"])
    
    for dest_stop in stops["dest_stops"]:
        dest_routes[dest_stop["stop_id"]] = gtfs.get_routes_for_stops(dest_stop["stop_id"])
    
    #find overlapping stations

    for o_stop, o_routes in origin_routes.items():
        for d_stop, d_routes in dest_routes.items():
            overlap = o_routes & d_routes
            if overlap:
                route_options.append({"origin_stop": o_stop,
                "dest_stop": d_stop,
                "routes": overlap})
    
    return route_options

async def get_schedule(routes: list) -> dict: 

    unique_routes = set()

    for option in routes:
        for train_line in option["routes"]:
            unique_routes.add(train_line)
    
    #get unique route ids
    raw_feeds = await fetch_feeds(list(unique_routes))
    all_updates = []

    for feed in raw_feeds:
        all_updates.extend(parse_bytes(feed))
    
    #return all_updates
    relevant_stops = set()
    for option in routes:
        relevant_stops.add(option["origin_stop"])
        relevant_stops.add(option["dest_stop"])

    user_schedule = []

    for update in all_updates:
        stop = update["stop_id"]
        parent = stop.rstrip("NS")
        if parent in relevant_stops or stop in relevant_stops:
            user_schedule.append(update)
    
    all_positions = []
    for feed in raw_feeds:
        all_positions.extend(parse_vehicle_positions(feed))
    
    stalled_trains = []
    
    for position in all_positions:
        if position["stop_id"] in relevant_stops and datetime.now(tz=ZoneInfo("America/New_York")).timestamp() - position["timestamp"] > 300:
            stalled_trains.append(position)
            

    
    return {"user_schedule": user_schedule,
    "stalled_trains": stalled_trains}


def combine_data(route_options: list, schedule: dict, closest_stops: dict) -> json: 
    combine_data = closest_stops
    combine_data["possible_routes"] = []
    combine_data["schedule_for_user_stops_only"] = []
    combine_data["stalled_trains_at_stops"] = []

    for route in route_options:
        combine_data["possible_routes"].append({
            "origin_stop": route["origin_stop"],
            "dest_stop": route["dest_stop"],
            "routes": list(route["routes"])
        })
    
    # Keep only the next 5 arrivals per stop to limit Claude prompt size
    arrivals_per_stop: dict = {}
    for entry in schedule["user_schedule"]:
        stop_key = entry["stop_id"]
        if arrivals_per_stop.get(stop_key, 0) < 5:
            combine_data["schedule_for_user_stops_only"].append(entry)
            arrivals_per_stop[stop_key] = arrivals_per_stop.get(stop_key, 0) + 1
    
    for stalled_train in schedule["stalled_trains"]:
        combine_data["stalled_trains_at_stops"].append(stalled_train)
    
    return json.dumps(combine_data)


