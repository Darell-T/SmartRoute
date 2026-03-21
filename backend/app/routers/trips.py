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
import time
from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
import os
from app.services.route_calculator import nearest_stops, possible_routes, get_schedule, combine_data
from app.services.ai_advisor import stream_recommendation
from app.services.incident_monitor import get_incidents
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
        )
        print(f"[trip] schedule+incidents: {time.monotonic()-t1:.2f}s")

        user_schedule = route_data[0]
        incident_reports = route_data[1]

        combined_data = combine_data(route_options, user_schedule, closest_stops)

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
        return {"text": text, "audio": audio_bytes}

    except HTTPException:
        raise
    except Exception as exc:
        import traceback
        print(f"[trip] UNHANDLED ERROR:\n{traceback.format_exc()}")
        raise HTTPException(status_code=500, detail=str(exc))