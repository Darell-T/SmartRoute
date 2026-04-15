from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app.utils.geo import find_nearest_stops
from app.services import mta_feed
import asyncio


router = APIRouter()

class LiveFeedRequest(BaseModel):
    lat: float
    lng: float

@router.post("/api/live-feed")
async def live_feed(request: Request, payload: LiveFeedRequest):
    gtfs = getattr(request.app.state, "gtfs", None)
    if gtfs is None:
        return JSONResponse({"Error":"GTFS not ready"}, status_code=503)
    
    # 1. find_nearest_stops
    nearest_stops = find_nearest_stops(payload.lat, payload.lng, gtfs, 5)
    # 2. get_unique_routes_for_stops 
    unique_routes = gtfs.get_unique_routes_for_stops(nearest_stops)
    # 3. fetch_feeds + parse_bytes for arrivals
    route_ids = [r for routes in unique_routes.values() for r in routes]
    feeds = await mta_feed.fetch_feeds(route_ids)

    parse_tasks = [asyncio.to_thread(mta_feed.parse_bytes, feed) for feed in feeds]
    parsed_lists = await asyncio.gather(*parse_tasks, return_exceptions = True)

    trip_updates = []
    for parsed in parsed_lists:
        if isinstance(parsed, Exception):
            print(f"[live_feed] parse_bytes failed: {parsed}")
            continue
        trip_updates.extend(parsed)

    

    

    # 4. fetch_service_alerts
    raw_alerts = await mta_feed.fetch_service_alerts()
    parsed_alerts = mta_feed.parse_service_alerts(raw_alerts)
    filtered_alerts = mta_feed.filter_alerts_for_routes(parsed_alerts, set(route_ids))


    # 5. return { stops, arrivals, alerts }
    return JSONResponse(
        {
            "stops": nearest_stops,
            "arrivals": trip_updates,
            "alerts": filtered_alerts,
        }
    )
