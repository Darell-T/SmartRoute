"""poi_search tool: finds a point of interest (store, restaurant, business)
in NYC via Google Places API (New) `places:searchText`, for the multi-stop
procedure ("pizza first") -- resolves a rider-named stop into a concrete
place before plan_trip routes a leg to it.

Results are hard-filtered to NYC_BOUNDS (same defense as geo.py's geocoder)
before they ever reach the model, since Places (New) has no built-in region
restriction on textQuery search the way the legacy API did.
"""

from __future__ import annotations

import asyncio
import os
import re

import httpx

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
                "minimum": 1,
                "maximum": 5,
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
    if value.lower() == "user":
        origin = ctx.origin or {}
        lat, lng = origin.get("lat"), origin.get("lng")
        if lat is not None and lng is not None:
            return (float(lat), float(lng), _POI_RADIUS_M), None
        return None, "I need your location to search nearby -- share GPS or give me a place name instead."
    if _COORD_RE.match(value):
        lat_str, lng_str = value.split(",")
        return (float(lat_str.strip()), float(lng_str.strip()), _POI_RADIUS_M), None
    coords, reason = await asyncio.to_thread(geo.geocode_address_with_reason, value)
    if coords is None:
        return None, reason or "could not resolve that location"
    return (coords[0], coords[1], _POI_RADIUS_M), None


def _clamp_max_results(raw_value) -> int:
    try:
        value = int(raw_value) if raw_value is not None else _DEFAULT_MAX_RESULTS
    except (TypeError, ValueError):
        value = _DEFAULT_MAX_RESULTS
    return max(_MIN_MAX_RESULTS, min(_MAX_MAX_RESULTS, value))


async def execute(tool_input: dict, ctx: ToolContext) -> ToolResult:
    query = str(tool_input.get("query") or "").strip()
    if not query:
        return ToolResult(ok=False, error="query is required")

    api_key = _resolve_api_key()
    if not api_key:
        return ToolResult(ok=False, error="place search is not configured")

    bias, error = await _resolve_bias(str(tool_input.get("near") or ""), ctx)
    if bias is None:
        return ToolResult(ok=False, error=error or "could not resolve that location")
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

    try:
        async with httpx.AsyncClient(timeout=POI_SEARCH_TIMEOUT_S) as client:
            response = await client.post(PLACES_SEARCH_URL, json=body, headers=headers)
            response.raise_for_status()
            payload = response.json()
    except httpx.TimeoutException:
        print("[agent-poi_search] Places timed out")
        return ToolResult(ok=False, error="place search timed out")
    except httpx.HTTPStatusError as exc:
        print(f"[agent-poi_search] Places HTTP {exc.response.status_code}")
        return ToolResult(ok=False, error="place search failed")
    except httpx.RequestError as exc:
        print(f"[agent-poi_search] Places request failed: {type(exc).__name__}")
        return ToolResult(ok=False, error="place search failed")
    except (ValueError, TypeError) as exc:
        print(f"[agent-poi_search] Places invalid JSON: {exc!r}")
        return ToolResult(ok=False, error="place search returned an unexpected response")

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
        return ToolResult(ok=False, error="place search returned an unexpected response")

    results = results[:max_results]
    summary = f"found {len(results)} place(s) for '{query}'" if results else f"no places found for '{query}'"
    return ToolResult(ok=True, data={"results": results}, summary=summary)
