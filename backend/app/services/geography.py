import re
from math import atan2, cos, radians, sin, sqrt

import httpx

NYC_GEOSEARCH_URL = "https://geosearch.planninglabs.nyc/v2/search"

NYC_BOUNDS = {
    "min_lat": 40.4774,
    "max_lat": 40.9176,
    "min_lon": -74.2591,
    "max_lon": -73.7004,
}


def geocode_address_with_reason(address: str) -> tuple[tuple[float, float] | None, str | None]:
    if not address or not address.strip():
        return None, "Address is empty."

    coord_pattern = re.compile(r'^-?\d+\.?\d*,\s*-?\d+\.?\d*$')
    if coord_pattern.match(address.strip()):
        lat, lng = address.strip().split(",")
        lat, lng = float(lat.strip()), float(lng.strip())
        in_nyc = is_in_nyc(lat, lng)
        print(f"[geo] provider=input outcome=coordinates in_service_area={int(in_nyc)}")
        if not in_nyc:
            return None, "Coordinates are outside NYC bounds."
        return (lat, lng), None

    print("[geo] provider=nyc_geosearch outcome=request_started")
    try:
        with httpx.Client(timeout=5) as client:  # noqa: TID251
            resp = client.get(
                NYC_GEOSEARCH_URL,
                params={"text": address.strip(), "size": 1},
            )
            resp.raise_for_status()
            features = resp.json().get("features", [])
    except httpx.HTTPError as err:
        print(f"[geo] provider=nyc_geosearch outcome=error error_type={type(err).__name__}")
        return None, "Geocoding service is temporarily unavailable."
    if not features:
        print("[geo] provider=nyc_geosearch outcome=no_result")
        return None, "Address not found in NYC."

    lng, lat = features[0]["geometry"]["coordinates"]  # GeoJSON is [lng, lat]
    in_nyc = is_in_nyc(lat, lng)
    print(f"[geo] provider=nyc_geosearch outcome=result in_service_area={int(in_nyc)}")
    if not in_nyc:
        return None, "Address is outside NYC bounds."
    return (lat, lng), None


def is_in_nyc(lat: float, lon: float) -> bool:
    return (
        NYC_BOUNDS["min_lat"] <= lat <= NYC_BOUNDS["max_lat"]
        and NYC_BOUNDS["min_lon"] <= lon <= NYC_BOUNDS["max_lon"]
    )


def distance_meters(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    earth_radius_m = 6371008.8
    d_lat = radians(lat2 - lat1)
    d_lon = radians(lon2 - lon1)
    origin_lat = radians(lat1)
    target_lat = radians(lat2)
    a = (
        sin(d_lat / 2) ** 2
        + cos(origin_lat) * cos(target_lat) * sin(d_lon / 2) ** 2
    )
    return earth_radius_m * 2 * atan2(sqrt(a), sqrt(1 - a))


def find_nearest_stops(
    lat: float,
    lon: float,
    gtfs,
    limit: int = 5,
    radius_m: float | None = None,
) -> list:
    distances = []
    for stop in gtfs.get_all_parent_stops():
        stop_lat = float(stop["stop_lat"])
        stop_lon = float(stop["stop_lon"])
        dist = distance_meters(lat, lon, stop_lat, stop_lon)
        if radius_m is not None and dist > radius_m:
            continue
        distances.append({"stop_id": stop["stop_id"], "stop_name": stop["stop_name"], "stop_lat": stop_lat, "stop_lon": stop_lon, "distance_m": round(dist, 1)})

    distances.sort(key=lambda x: x["distance_m"])
    return distances[:limit]
