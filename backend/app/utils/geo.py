from geopy.distance import geodesic
import re
import requests

NYC_GEOSEARCH_URL = "https://geosearch.planninglabs.nyc/v2/search"

# NYC bounding box to reject addresses outside the area, NYC ADDRESSES ONLY
NYC_BOUNDS = {
    "min_lat": 40.4774,
    "max_lat": 40.9176,
    "min_lon": -74.2591,
    "max_lon": -73.7004,
}


def geocode_address(address: str) -> tuple | None:
    coords, _reason = geocode_address_with_reason(address)
    return coords


def geocode_address_with_reason(address: str) -> tuple[tuple[float, float] | None, str | None]:
    if not address or not address.strip():
        return None, "Address is empty."

    # Check if input is already coordinates
    coord_pattern = re.compile(r'^-?\d+\.?\d*,\s*-?\d+\.?\d*$')
    if coord_pattern.match(address.strip()):
        lat, lng = address.strip().split(",")
        lat, lng = float(lat.strip()), float(lng.strip())
        in_nyc = _is_in_nyc(lat, lng)
        print(f"[geo] coordinate input detected: lat={lat}, lng={lng}, in_nyc={in_nyc}")
        if not in_nyc:
            return None, "Coordinates are outside NYC bounds."
        return (lat, lng), None

    # Use NYC Planning GeoSearch API — free, no key, NYC-specific
    print(f"[geo] GeoSearch query: {address!r}")
    try:
        resp = requests.get(
            NYC_GEOSEARCH_URL,
            params={"text": address.strip(), "size": 1},
            timeout=5,
        )
        resp.raise_for_status()
        features = resp.json().get("features", [])
        if not features:
            print(f"[geo] GeoSearch returned no results for {address!r}")
            return None, "Address not found in NYC."

        lng, lat = features[0]["geometry"]["coordinates"]  # GeoJSON is [lng, lat]
        in_nyc = _is_in_nyc(lat, lng)
        print(f"[geo] GeoSearch result: lat={lat}, lng={lng}, in_nyc={in_nyc}")
        if not in_nyc:
            return None, "Address is outside NYC bounds."
        return (lat, lng), None
    except requests.RequestException as err:
        print(f"[geo] GeoSearch error: {err}")
        return None, f"Geocoding service error: {err}"


def _is_in_nyc(lat: float, lon: float) -> bool:
    return (
        NYC_BOUNDS["min_lat"] <= lat <= NYC_BOUNDS["max_lat"]
        and NYC_BOUNDS["min_lon"] <= lon <= NYC_BOUNDS["max_lon"]
    )


def distance_meters(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    return geodesic((lat1, lon1), (lat2, lon2)).meters


def find_nearest_stops(lat: float, lon: float, gtfs, limit: int = 5) -> list:
    distances = []
    for stop in gtfs.get_all_parent_stops():
        stop_lat = float(stop["stop_lat"])
        stop_lon = float(stop["stop_lon"])
        dist = distance_meters(lat, lon, stop_lat, stop_lon)
        distances.append({"stop_id": stop["stop_id"], "stop_name": stop["stop_name"], "stop_lat": stop_lat, "stop_lon": stop_lon, "distance_m": round(dist, 1)})

    distances.sort(key=lambda x: x["distance_m"])
    return distances[:limit]


def walking_time_minutes(meters: float, speed_mps: float = 1.4) -> float:
    return round(meters / speed_mps / 60, 1)


if __name__ == "__main__":
    from app.utils.gtfs_static import GTFSStaticData

    result = geocode_address("350 5th Ave, New York")
    print(f"Geocoded: {result}")

    if result:
        gtfs = GTFSStaticData()
        lat, lon = result
        nearest = find_nearest_stops(lat, lon, gtfs)
        print(f"\nNearest stations:")
        for stop in nearest:
            walk = walking_time_minutes(stop["distance_m"])
            print(f"  {stop['stop_name']} ({stop['stop_id']}) - {stop['distance_m']}m, ~{walk} min walk")