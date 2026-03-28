import base64
import asyncio
import re
import time
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from app.services.directions import get_transit_route, parse_response
from app.services.ai_advisor import stream_recommendation
from app.services.incident_monitor import get_incidents
from app.services.mta_feed import fetch_service_alerts, get_stalled_buses, parse_service_alerts, filter_alerts_for_routes, get_stalled_trains
from app.services.voice import generate_speech

router = APIRouter()

class TripRequest(BaseModel):
    origin_lat: float
    origin_lng: float
    destination: str

@router.post("/api/trip")
async def plan_trip(request: Request, payload: TripRequest):
    t0 = time.monotonic()
    try:
        gtfs = getattr(request.app.state, "gtfs", None)
        if gtfs is None:
            detail = getattr(request.app.state, "gtfs_error", None) or "GTFS data is still loading"
            raise HTTPException(status_code=503, detail=detail)

        # 1. Get routes from Google
        response = await get_transit_route((payload.origin_lat, payload.origin_lng), payload.destination)

        # 2. Parse the response into structured routes
        parsed_response = parse_response(response)

        # 3. Extract route IDs and station names from all routes
        route_ids = set()
        all_stops = []
        bus_route_ids = set()
        for route in parsed_response:
            for step in route:
                if step["type"] in ("SUBWAY", "BUS"):
                    route_ids.add(step["route_id"])
                    intermediate = gtfs.get_intermediate_stops(
                        step["route_id"], step["departure_stop"], step["arrival_stop"]
                    )
                    step["intermediate_stops"] = intermediate
                    all_stops.extend(intermediate)

                if step["type"] == "BUS":
                    bus_route_ids.add(step["route_id"])


        # 4. Fetch alerts, incidents, and stalled trains in parallel
        raw_alerts, incidents, stalled, stalled_buses = await asyncio.gather(
            fetch_service_alerts(),
            get_incidents(all_stops),
            get_stalled_trains(route_ids),
            get_stalled_buses(bus_route_ids)
        )

        # 5. Filter alerts for relevant routes
        parsed_alerts = parse_service_alerts(raw_alerts) if raw_alerts else []
        relevant_alerts = filter_alerts_for_routes(parsed_alerts, route_ids)

        # 6. Build payload for JARVIS (routes + alerts + incidents + stalled trains + Stalled buses)
        jarvis_payload = {
            "routes": parsed_response,
            "service_alerts": relevant_alerts,
            "incidents": incidents if incidents else [],
            "stalled_trains": stalled if stalled else [],
            "stalled_buses": stalled_buses if stalled_buses else [],
        }

        # 7. Stream to Claude
        raw_recommendation = ""
        async for chunk in stream_recommendation(jarvis_payload):
            raw_recommendation += chunk

        # 8. Parse chosen route index from JARVIS tag, then strip it
        chosen_index = 0
        route_tag_match = re.search(r"\[ROUTE:(\d+)\]", raw_recommendation)
        if route_tag_match:
            chosen_index = int(route_tag_match.group(1))
            if chosen_index >= len(parsed_response):
                chosen_index = 0
        recommendation = re.sub(r"\s*\[ROUTE:\d+\]\s*", "", raw_recommendation).strip()
        print(f"[trip] JARVIS chose route index {chosen_index}")

        # 9. Generate speech (with tag stripped, abbreviations expanded for TTS)
        tts_text = recommendation
        tts_text = tts_text.replace(" St ", " Street ")
        tts_text = tts_text.replace(" St.", " Street")
        tts_text = tts_text.replace(" Sq ", " Square ")
        tts_text = tts_text.replace(" Hwy ", " Highway ")
        tts_text = tts_text.replace(" Av ", " Avenue ")
        tts_text = tts_text.replace(" Av.", " Avenue")
        tts_text = tts_text.replace(" Blvd ", " Boulevard ")
        tts_text = tts_text.replace(" Pkwy ", " Parkway ")
        tts_text = tts_text.replace(" Ctr", " Center")
        tts_text = tts_text.replace(" Rd ", " Road ")
        try:
            audio_bytes = await asyncio.to_thread(generate_speech, tts_text)
            audio_b64 = base64.b64encode(audio_bytes).decode("utf-8")
        except Exception as exc:
            print(f"[trip] TTS unavailable, returning text-only response: {exc}")
            audio_b64 = ""

        # 10. Return response — only the chosen route goes to the frontend
        chosen_route = parsed_response[chosen_index] if parsed_response else []
        elapsed = time.monotonic() - t0
        print(f"[trip] completed in {elapsed:.1f}s")
        return {
            "recommendation": recommendation,
            "audio": audio_b64,
            "route": chosen_route,
            "alerts": relevant_alerts,
        }

    except HTTPException:
        raise
    except Exception as exc:
        import traceback
        print(f"[trip] UNHANDLED ERROR:\n{traceback.format_exc()}")
        raise HTTPException(status_code=500, detail=str(exc))
