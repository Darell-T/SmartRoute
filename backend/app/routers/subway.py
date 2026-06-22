import time
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse


router = APIRouter()

# NYCT subway routes (incl. shuttles + Staten Island Railway). Buses, ferry,
# and other modes are filtered out so the map only carries subway dots.
SUBWAY_ROUTE_IDS: set[str] = {
    "1", "2", "3", "4", "5", "6", "6X",
    "7", "7X",
    "A", "C", "E",
    "B", "D", "F", "FX", "M",
    "G",
    "J", "Z",
    "L",
    "N", "Q", "R", "W",
    "S", "FS", "GS", "H",
    "SI", "SIR", "SS",
}

# Cache the parent-station + routes pull for an hour. The query touches
# stops/stop_times/trips and we expect this to change at the GTFS refresh
# cadence, not per-request.
_CACHE: dict[str, object] = {"data": None, "ts": 0.0}
_CACHE_TTL = 3600.0


@router.get("/api/subway-stops")
async def subway_stops(request: Request):
    gtfs = getattr(request.app.state, "gtfs", None)
    if gtfs is None:
        return JSONResponse({"error": "GTFS not ready"}, status_code=503)

    now = time.time()
    cached = _CACHE.get("data")
    cached_ts = _CACHE.get("ts") or 0.0
    if cached and now - float(cached_ts) < _CACHE_TTL:
        return JSONResponse(cached)

    rows = gtfs.get_subway_stops_with_routes(SUBWAY_ROUTE_IDS)

    features = []
    for row in rows:
        lat = row.get("stop_lat")
        lon = row.get("stop_lon")
        if lat is None or lon is None:
            continue
        features.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [lon, lat]},
            "properties": {
                "stop_id": row["stop_id"],
                "name": row["stop_name"],
                "route_ids": row["route_ids"],
            },
        })

    payload = {
        "type": "FeatureCollection",
        "features": features,
        "updated_at": int(now),
    }
    _CACHE["data"] = payload
    _CACHE["ts"] = now
    return JSONResponse(payload)
