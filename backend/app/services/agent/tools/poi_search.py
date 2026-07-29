"""poi_search tool: finds a point of interest (store, restaurant, business)
in NYC via Google Places API (New) `places:searchText`, for the multi-stop
procedure ("pizza first") -- resolves a rider-named stop into a concrete
place before plan_trip routes a leg to it.

Results are hard-filtered to NYC_BOUNDS (same defense as geo.py's geocoder)
before they ever reach the model, since Places (New) has no built-in region
restriction on textQuery search the way the legacy API did.
"""

from __future__ import annotations

import os
import re
import time

from app.services.agent.tools._http import fetch_json
from app.services.agent.tools._location import resolve_named_point
from app.services.agent.tools._types import ToolContext, ToolResult
from app.services.trips import text
from app.utils import geo

PLACES_SEARCH_URL = "https://places.googleapis.com/v1/places:searchText"
PLACES_FIELD_MASK = "places.displayName,places.formattedAddress,places.location,places.currentOpeningHours.openNow"
POI_SEARCH_TIMEOUT_S = float(os.getenv("POI_SEARCH_TIMEOUT_S", "6.0"))

_POI_RADIUS_M = 3000.0
# No `near` given: bias the whole search toward NYC rather than resolving a
# single point, so "pizza" without a location still returns NYC results.
_NYC_CENTER_LAT = 40.7484
_NYC_CENTER_LNG = -73.9857
_NYC_WIDE_RADIUS_M = 15000.0

_DEFAULT_MAX_RESULTS = 3
_MIN_MAX_RESULTS = 1
_MAX_MAX_RESULTS = 5

_COORD_RE = re.compile(r"^-?\d+\.?\d*,\s*-?\d+\.?\d*$")

POI_SEARCH_SCHEMA = {
    "name": "poi_search",
    "description": (
        "Search for a point of interest (store, restaurant, business) in "
        "NYC by name or category, returning nearby matches with address and "
        "open-now status. Use this to resolve an intermediate stop (e.g. "
        "'pizza first') before planning a leg there with plan_trip."
    ),
    "strict": True,
    "input_schema": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "What to search for, e.g. 'pizza' or 'Costco'.",
            },
            "near": {
                "type": "string",
                "description": (
                    "'user' for the rider's current GPS location, an NYC "
                    "address, or 'lat,lng'. Omit to search across all of NYC."
                ),
            },
            "max_results": {
                "type": "integer",
                "description": "Maximum results to return (default 3).",
            },
        },
        "required": ["query"],
        "additionalProperties": False,
    },
}


def _resolve_api_key() -> str | None:
    key = os.getenv("GOOGLE_PLACES_API_KEY")
    if key:
        return key
    # Fall back to the shared Routes key on the same billing account -- it
    # must have the Places API (New) enabled for this to work; do not farm a
    # separate free-tier key (see plan doc's user decisions).
    return os.getenv("GOOGLE_ROUTES_API_KEY") or None


async def _resolve_bias(near_raw: str, ctx: ToolContext) -> tuple[tuple[float, float, float] | None, str | None]:
    value = (near_raw or "").strip()
    if not value:
        return (_NYC_CENTER_LAT, _NYC_CENTER_LNG, _NYC_WIDE_RADIUS_M), None
    # Raw coordinates skip geo.py's NYC-bounds check deliberately -- a
    # rider-supplied 'near' point only biases the search radius, it is never
    # itself validated as an NYC destination the way plan_trip's origin/
    # destination are.
    if _COORD_RE.match(value):
        lat_str, lng_str = value.split(",")
        return (float(lat_str.strip()), float(lng_str.strip()), _POI_RADIUS_M), None
    coords, error = await resolve_named_point(
        value, ctx, missing_location_message="I need your location to search nearby -- share GPS or give me a place name instead."
    )
    if coords is None:
        return None, error or "could not resolve that location"
    return (coords[0], coords[1], _POI_RADIUS_M), None


def _clamp_max_results(raw_value) -> int:
    try:
        value = int(raw_value) if raw_value is not None else _DEFAULT_MAX_RESULTS
    except (TypeError, ValueError):
        value = _DEFAULT_MAX_RESULTS
    return max(_MIN_MAX_RESULTS, min(_MAX_MAX_RESULTS, value))


async def execute(tool_input: dict, ctx: ToolContext) -> ToolResult:
    timings = {
        "place_resolution_ms": 0.0,
        "place_normalization_ms": 0.0,
    }
    query = str(tool_input.get("query") or "").strip()
    if not query:
        return ToolResult(ok=False, error="query is required", timings=timings)

    api_key = _resolve_api_key()
    if not api_key:
        return ToolResult(
            ok=False,
            error="place search is not configured",
            timings=timings,
        )

    resolution_started = time.monotonic()
    bias, error = await _resolve_bias(str(tool_input.get("near") or ""), ctx)
    timings["place_resolution_ms"] = (
        time.monotonic() - resolution_started
    ) * 1000
    if bias is None:
        return ToolResult(
            ok=False,
            error=error or "could not resolve that location",
            timings=timings,
        )
    lat, lng, radius_m = bias
    max_results = _clamp_max_results(tool_input.get("max_results"))

    body = {
        "textQuery": query,
        "locationBias": {"circle": {"center": {"latitude": lat, "longitude": lng}, "radius": radius_m}},
        "maxResultCount": max_results,
    }
    headers = {
        "Content-Type": "application/json",
        "X-Goog-Api-Key": api_key,
        "X-Goog-FieldMask": PLACES_FIELD_MASK,
    }

    payload, error = await fetch_json(
        "POST",
        PLACES_SEARCH_URL,
        timeout_s=POI_SEARCH_TIMEOUT_S,
        log_tag="agent-poi_search",
        what="place search",
        json_body=body,
        headers=headers,
    )
    if error:
        return ToolResult(ok=False, error=error, timings=timings)

    normalization_started = time.monotonic()
    try:
        results = []
        for place in (payload or {}).get("places") or []:
            location = place.get("location") or {}
            place_lat, place_lng = location.get("latitude"), location.get("longitude")
            if place_lat is None or place_lng is None or not geo._is_in_nyc(place_lat, place_lng):
                continue
            opening_hours = place.get("currentOpeningHours") or {}
            results.append(
                {
                    "name": text._safe_text((place.get("displayName") or {}).get("text"), 80),
                    "address": text._safe_text(place.get("formattedAddress"), 120),
                    "lat": place_lat,
                    "lng": place_lng,
                    "open_now": opening_hours.get("openNow") if "openNow" in opening_hours else None,
                }
            )
    except (KeyError, TypeError, AttributeError) as exc:
        print(f"[agent-poi_search] malformed Places response: {exc!r}")
        timings["place_normalization_ms"] = (
            time.monotonic() - normalization_started
        ) * 1000
        return ToolResult(
            ok=False,
            error="place search returned an unexpected response",
            timings=timings,
        )

    results = results[:max_results]
    timings["place_normalization_ms"] = (
        time.monotonic() - normalization_started
    ) * 1000
    summary = f"found {len(results)} place(s) for '{query}'" if results else f"no places found for '{query}'"
    return ToolResult(
        ok=True,
        data={"results": results},
        summary=summary,
        timings=timings,
    )
