"""OneBusAway stops-for-route: ordered stop lists for bus trip steps.

Google Routes gives bus steps a true street polyline but only a stop COUNT.
This module fetches the route's ordered stop groups from MTA BusTime's
OneBusAway API and slices the stretch between the rider's board and exit
stops so the map can draw intermediate stop markers.

Everything here is fail-open: any network/parse problem yields None/empty
and the trip response renders with board/exit markers only. Payloads are
cached for six hours (stop sequences are quasi-static), which also keeps
repeat trips and multi-candidate enrichment off the OBA rate limit.

Stop matching is by COORDINATES, not names: Google says "Madison Av/E 43 St"
where OBA says "MADISON AV/E 43 ST" -- names do not align, positions do.
"""

import json
import os
from urllib.parse import quote

import httpx

from app.utils.cache import cache_get, cache_set
from app.utils.geo import distance_meters

# NOTE: unlike stops-for-location, the route id is part of the PATH
# (/stops-for-route/{id}.json), not a query parameter.
BUS_STOPS_FOR_ROUTE_URL = "https://bustime.mta.info/api/where/stops-for-route/{route_id}.json"
_CACHE_TTL_SECONDS = 6 * 3600
# MTA splits bus routes across two OBA agencies.
_AGENCY_PREFIXES = ("MTA NYCT_", "MTABC_")


def _bus_api_key() -> str | None:
    # Deliberately not imported from mta_feed: keeps this module free of the
    # GTFS-realtime import chain (and importable under the trips test
    # harness, which fakes mta_feed).
    key = os.getenv("MTA_BUS_API_KEY")
    return key.strip() if key and key.strip() else None


def normalize_google_bus_route_id(route_id: str) -> list[str]:
    """Candidate OBA short ids for a Google bus route id, in try order.
    Google publishes Select Bus Service as "M15-SBS"; OBA uses "M15+"."""
    cleaned = str(route_id or "").strip().upper()
    if not cleaned:
        return []
    candidates = [cleaned]
    if cleaned.endswith("-SBS"):
        candidates.append(cleaned[: -len("-SBS")] + "+")
    return candidates


def _as_list(value):
    return value if isinstance(value, list) else []


def parse_stops_for_route(payload: dict) -> dict:
    """Normalize a stops-for-route payload into
    {stops_by_id: {id: {name, lat, lon}}, ordered_groups: [[stop_id, ...]]}.

    Tolerates both vanilla OneBusAway (data.entry.stopGroupings +
    data.references.stops) and MTA's flat variant (data.stopGroupings +
    data.stops) -- same defensive posture as _stops_for_location_list."""
    empty = {"stops_by_id": {}, "ordered_groups": []}
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, dict):
        return empty

    references = data.get("references") if isinstance(data.get("references"), dict) else {}
    raw_stops = (
        data.get("stops")
        if isinstance(data.get("stops"), list)
        else references.get("stops")
    )
    stops_by_id = {}
    for stop in _as_list(raw_stops):
        if not isinstance(stop, dict):
            continue
        stop_id = str(stop.get("id") or "")
        lat = stop.get("lat")
        lon = stop.get("lon")
        if not stop_id or lat is None or lon is None:
            continue
        stops_by_id[stop_id] = {
            "name": str(stop.get("name") or "Bus stop"),
            "lat": float(lat),
            "lon": float(lon),
        }

    entry = data.get("entry") if isinstance(data.get("entry"), dict) else {}
    raw_groupings = (
        data.get("stopGroupings")
        if isinstance(data.get("stopGroupings"), list)
        else entry.get("stopGroupings")
    )
    ordered_groups = []
    for grouping in _as_list(raw_groupings):
        if not isinstance(grouping, dict):
            continue
        for group in _as_list(grouping.get("stopGroups")):
            if not isinstance(group, dict):
                continue
            stop_ids = [str(s) for s in _as_list(group.get("stopIds")) if s]
            if stop_ids:
                ordered_groups.append(stop_ids)

    if not stops_by_id:
        return empty
    return {"stops_by_id": stops_by_id, "ordered_groups": ordered_groups}


def _nearest_index(coords: dict, stop_ids: list, stops_by_id: dict):
    """(index, distance_m) of the stop in the group nearest to coords."""
    lat = coords.get("latitude")
    lng = coords.get("longitude")
    if lat is None or lng is None:
        return None
    best = None
    for index, stop_id in enumerate(stop_ids):
        stop = stops_by_id.get(stop_id)
        if not stop:
            continue
        d = distance_meters(float(lat), float(lng), stop["lat"], stop["lon"])
        if best is None or d < best[1]:
            best = (index, d)
    return best


def slice_route_stops(parsed: dict, board_coords: dict, exit_coords: dict, max_snap_m: float = 250) -> list:
    """Ordered [{name, lat, lng}] from board stop to exit stop (inclusive).

    Considers every direction group; requires board index < exit index
    (rules out the reverse-direction group, whose nearest stops appear in
    the wrong order); picks the group with the smallest combined snap
    distance. Empty list when no group qualifies within max_snap_m."""
    stops_by_id = parsed.get("stops_by_id") or {}
    best_slice = None
    best_score = None
    for stop_ids in parsed.get("ordered_groups") or []:
        board = _nearest_index(board_coords, stop_ids, stops_by_id)
        exit_ = _nearest_index(exit_coords, stop_ids, stops_by_id)
        if not board or not exit_:
            continue
        if board[1] > max_snap_m or exit_[1] > max_snap_m:
            continue
        if board[0] >= exit_[0]:
            continue
        score = board[1] + exit_[1]
        if best_score is None or score < best_score:
            best_score = score
            best_slice = stop_ids[board[0] : exit_[0] + 1]

    if not best_slice:
        return []
    return [
        {
            "name": stops_by_id[stop_id]["name"],
            "lat": stops_by_id[stop_id]["lat"],
            "lng": stops_by_id[stop_id]["lon"],
        }
        for stop_id in best_slice
        if stop_id in stops_by_id
    ]


async def fetch_bus_route_stop_groups(route_id: str) -> dict | None:
    """Fetch + parse stops-for-route for a Google bus route id. Tries each
    agency prefix and SBS normalization until one returns stops. Cached;
    None on any failure (callers degrade to board/exit-only markers)."""
    key = _bus_api_key()
    if not key:
        return None

    cache_key = f"oba:stops-for-route:{str(route_id or '').strip().upper()}"
    cached = cache_get(cache_key)
    if cached:
        try:
            parsed = parse_stops_for_route(json.loads(cached))
            if parsed["stops_by_id"]:
                return parsed
        except (ValueError, TypeError):
            pass

    for short_id in normalize_google_bus_route_id(route_id):
        for prefix in _AGENCY_PREFIXES:
            try:
                async with httpx.AsyncClient(timeout=8.0) as client:
                    response = await client.get(
                        BUS_STOPS_FOR_ROUTE_URL.format(
                            route_id=quote(f"{prefix}{short_id}", safe=""),
                        ),
                        params={
                            "key": key,
                            "includePolylines": "false",
                            "version": 2,
                        },
                    )
            except Exception:
                continue
            if response.status_code != 200:
                continue
            try:
                payload = response.json()
            except ValueError:
                continue
            parsed = parse_stops_for_route(payload)
            if parsed["stops_by_id"]:
                cache_set(cache_key, json.dumps(payload).encode("utf-8"), _CACHE_TTL_SECONDS)
                return parsed

    return None
