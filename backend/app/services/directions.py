import os
import httpx
import json
from datetime import datetime
from zoneinfo import ZoneInfo



ROUTES_URL = "https://routes.googleapis.com/directions/v2:computeRoutes"
key = os.getenv("GOOGLE_ROUTES_API_KEY")

FIELD_MASK = FIELD_MASK = ",".join([
    "routes.legs.steps.transitDetails",
    "routes.legs.steps.travelMode",
    "routes.legs.steps.startLocation",
    "routes.legs.steps.endLocation",
    "routes.legs.duration",
    "routes.legs.distanceMeters",
    "routes.legs.steps.staticDuration",
    "routes.legs.polyline.encodedPolyline",
    "routes.legs.steps.polyline.encodedPolyline",
])

async def get_transit_route(origin: tuple, dest: str) -> dict:
    request_body = {
        "origin": {
            "location": {
                "latLng": {
                    "latitude": origin[0],
                    "longitude": origin[1]
                }
            }
        },
        "destination": {
            "address": dest
        },
        "travelMode": "TRANSIT",
        "computeAlternativeRoutes": True,
        "transitPreferences": {
            "allowedTravelModes": ["SUBWAY", "BUS"],
            "routingPreference": "FEWER_TRANSFERS"
        },
        "languageCode": "en-US"
    }
    headers = {
        "Content-type": "application/json",
        "X-Goog-Api-Key": key,
        "X-Goog-FieldMask": FIELD_MASK
    }

    async with httpx.AsyncClient() as client:
        # implementation goes here
        response = await client.post(
            ROUTES_URL,
            json = request_body,
            headers = headers,
            timeout = 10.0
        )

        return response.json()

def parse_response(response: dict) -> list:
    routes = []


    for route in response["routes"]:
        steps = []
        for step in route["legs"][0]["steps"]:

            if step["travelMode"] == "TRANSIT":
                train_line = step["transitDetails"]["transitLine"]["nameShort"].replace("Line", "").strip()
                line_color = step["transitDetails"]["transitLine"]["color"]
                direction = step["transitDetails"]["headsign"]
                stop_count = step["transitDetails"]["stopCount"]

                departure_stop = step["transitDetails"]["stopDetails"]["departureStop"]["name"]
                arrival_stop = step["transitDetails"]["stopDetails"]["arrivalStop"]["name"]

                departure_coords = step["transitDetails"]["stopDetails"]["departureStop"]["location"]["latLng"]
                arrival_coords = step["transitDetails"]["stopDetails"]["arrivalStop"]["location"]["latLng"]


                depart_time = step["transitDetails"]["stopDetails"]["departureTime"]
                arrival_time = step["transitDetails"]["stopDetails"]["arrivalTime"]



                departure_utc = datetime.fromisoformat(depart_time.replace("Z", "+00:00"))
                departure_est = departure_utc.astimezone(ZoneInfo("America/New_York"))

                arrival_utc = datetime.fromisoformat(arrival_time.replace("Z", "+00:00"))
                arrival_est = arrival_utc.astimezone(ZoneInfo("America/New_York"))
                minutes_until_train_arrives = (departure_est - datetime.now(ZoneInfo("America/New_York"))).total_seconds() / 60
                arrival = (arrival_est - datetime.now(ZoneInfo("America/New_York"))).total_seconds() / 60

                transit_step = {
                    "type": step["transitDetails"]["transitLine"]["vehicle"]["type"],
                "train_line": train_line,
                "line_color": line_color,
                "direction": direction,
                "stop_count": stop_count,
                "departure_stop": departure_stop,
                "arrival_stop": arrival_stop,
                "departure_coords": departure_coords,
                "arrival_coords": arrival_coords,
                "minutes_until_train_arrives": minutes_until_train_arrives,
                "minutes_until_arrival": arrival,
                "polyline": step["polyline"]

                }
                steps.append(transit_step)
            
            if step["travelMode"] == "WALK":
                steps.append({
                    "type": step["travelMode"],
                    "start_point": step["startLocation"]["latLng"],
                    "end_point": step["endLocation"]["latLng"],
                    "polyline": step["polyline"],
                })
        routes.append(steps)
    
    return routes




            


