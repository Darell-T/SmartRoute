# trips.py - Trip Query Endpoint
#
# POST /api/trip
#
# This file will contain:
# - Trip planning endpoint that accepts origin, destination, and arrive_by time
# - Workflow:
#   1. Validate request payload
#   2. Call route_calculator service to compute best routes
#   3. Apply real-time delay adjustments from MTA feed data
#   4. Call ai_advisor service to generate plain-English explanation
#   5. Compute confidence score based on current conditions and historical data
#   6. Return TripResponse with recommendation, route legs, and alternatives
# - Store trip in database
import base64
import asyncio
import json
import time
from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
import os
from app.services.route_calculator import nearest_stops, possible_routes, get_schedule, combine_data
from app.services.ai_advisor import stream_recommendation
from app.services.incident_monitor import get_incidents
from app.services.mta_feed import fetch_service_alerts, parse_service_alerts, filter_alerts_for_routes
from app.services.voice import generate_speech

router = APIRouter()

class TripRequest(BaseModel):
    origin: str
    destination: str

@router.post("/api/trip")
async def plan_trip(request: TripRequest):
    t0 = time.monotonic()
    print(f"[trip] origin={request.origin!r}  destination={request.destination!r}")
    try:
        closest_stops = await nearest_stops(request.origin, request.destination)
        print(f"[trip] geocode+stops: {time.monotonic()-t0:.2f}s")
        if isinstance(closest_stops, str):
            raise HTTPException(status_code=400, detail=closest_stops)

        route_options = possible_routes(closest_stops)

        station_names = []
        for stop in closest_stops["origin_stops"]:
            station_names.append(stop["stop_name"])
        for stop in closest_stops["dest_stops"]:
            station_names.append(stop["stop_name"])

        async def safe_incidents():
            if not os.getenv("XAI_API_KEY"):
                return "[]"
            try:
                return await asyncio.wait_for(get_incidents(station_names), timeout=2.0)
            except Exception:
                return "[]"

        t1 = time.monotonic()
        route_data = await asyncio.gather(
            get_schedule(route_options),
            safe_incidents(),
            fetch_service_alerts(),
        )
        print(f"[trip] schedule+incidents+alerts: {time.monotonic()-t1:.2f}s")

        user_schedule = route_data[0]
        incident_reports = route_data[1]
        raw_alerts = route_data[2]

        # Parse and filter service alerts to user's relevant routes
        service_alerts = parse_service_alerts(raw_alerts)
        user_route_ids = set()
        for option in route_options:
            user_route_ids.update(option["routes"])
        relevant_alerts = filter_alerts_for_routes(service_alerts, user_route_ids)

        combined_data = combine_data(route_options, user_schedule, closest_stops)

        # Inject service alerts so JARVIS can see them
        combined_dict = json.loads(combined_data)
        combined_dict["service_alerts"] = relevant_alerts
        combined_data = json.dumps(combined_dict)

        # Extract structured route data for the frontend
        parsed = json.loads(combined_data)
        route_options_list = parsed.get("possible_routes", [])
        schedule_entries = parsed.get("schedule_for_user_stops_only", [])

        best = route_options_list[0] if route_options_list else None
        frontend_route = {}

        if best:
            origin_stop_id = best["origin_stop"]
            dest_stop_id = best["dest_stop"]
            train_lines = best["routes"]

            origin_station = next(
                (s for s in closest_stops["origin_stops"] if s["stop_id"] == origin_stop_id), None
            )
            dest_station = next(
                (s for s in closest_stops["dest_stops"] if s["stop_id"] == dest_stop_id), None
            )

            # Find next departure from schedule
            now_ts = time.time()
            next_deps = sorted(
                [e for e in schedule_entries
                 if e["stop_id"].rstrip("NS") == origin_stop_id
                 and e["route_id"] in train_lines
                 and e["arrival_time"] is not None
                 and e["arrival_time"] > now_ts],
                key=lambda e: e["arrival_time"]
            )
            next_dep = next_deps[0] if next_deps else None

            if origin_station and dest_station:
                frontend_route["trainLine"] = next_dep["route_id"] if next_dep else train_lines[0]
                frontend_route["originStation"] = {
                    "name": origin_station["stop_name"],
                    "lat": origin_station["stop_lat"],
                    "lng": origin_station["stop_lon"],
                }
                frontend_route["destStation"] = {
                    "name": dest_station["stop_name"],
                    "lat": dest_station["stop_lat"],
                    "lng": dest_station["stop_lon"],
                }
                frontend_route["departureTimestamp"] = next_dep["arrival_time"] if next_dep else None

                if next_dep:
                    # Infer direction from stop_id suffix (N=Uptown, S=Downtown)
                    stop_suffix = next_dep["stop_id"][-1] if next_dep["stop_id"] else ""
                    if stop_suffix == "N":
                        frontend_route["direction"] = "Uptown"
                    elif stop_suffix == "S":
                        frontend_route["direction"] = "Downtown"
                    else:
                        frontend_route["direction"] = ""

                # Ride duration: match trip_id at both origin and dest stops
                frontend_route["rideDurationMinutes"] = None
                if next_dep:
                    origin_time = next_dep["arrival_time"]
                    dest_entries = [e for e in schedule_entries
                                   if e["trip_id"] == next_dep["trip_id"]
                                   and e["stop_id"].rstrip("NS") == dest_stop_id
                                   and e["arrival_time"] is not None]
                    if dest_entries:
                        frontend_route["rideDurationMinutes"] = round((dest_entries[0]["arrival_time"] - origin_time) / 60)

        t2 = time.monotonic()
        text_parts: list[str] = []
        async for chunk in stream_recommendation(combined_data, incident_reports):
            text_parts.append(chunk)
        text = "".join(text_parts)
        print(f"[trip] claude: {time.monotonic()-t2:.2f}s")

        t3 = time.monotonic()
        audio = await asyncio.to_thread(generate_speech, text)
        print(f"[trip] elevenlabs: {time.monotonic()-t3:.2f}s")

        audio_bytes = base64.b64encode(audio).decode("utf-8")
        print(f"[trip] total: {time.monotonic()-t0:.2f}s")
        # Build compact alert summaries for the frontend
        frontend_alerts = []
        for alert in relevant_alerts:
            frontend_alerts.append({
                "header": alert["header"],
                "routeIds": alert["route_ids"],
            })

        return {
            "text": text,
            "audio": audio_bytes,
            "originCoords": closest_stops.get("origin_coords"),
            "destCoords": closest_stops.get("dest_coords"),
            "serviceAlerts": frontend_alerts,
            **frontend_route,
        }

    except HTTPException:
        raise
    except Exception as exc:
        import traceback
        print(f"[trip] UNHANDLED ERROR:\n{traceback.format_exc()}")
        raise HTTPException(status_code=500, detail=str(exc))