"""Google Places provider and normalization boundary for place discovery."""

from __future__ import annotations

import logging
import math
import os
import re
import time

from app.services import geography as geo
from app.services.agent import discovery_store
from app.services.agent.discovery_store import normalize_price_level
from app.services.agent.tools._types import ToolContext, ToolResult
from app.services.agent.tools.location_resolution import resolve_named_point
from app.services.agent.tools.places import geography as conversational_geography
from app.services.agent.tools.provider_http import fetch_json
from app.services.trips import text

_LOGGER = logging.getLogger(__name__)

PLACES_SEARCH_URL = "https://places.googleapis.com/v1/places:searchText"
PLACES_FIELD_MASK = (
    "places.id,places.displayName,places.formattedAddress,places.location,"
    "places.addressComponents,places.currentOpeningHours.openNow,"
    "places.priceLevel,places.rating,places.userRatingCount,nextPageToken"
)
POI_SEARCH_TIMEOUT_S = float(os.getenv("POI_SEARCH_TIMEOUT_S", "6.0"))

_POI_RADIUS_M = 3000.0
_NYC_CENTER_LAT = 40.7484
_NYC_CENTER_LNG = -73.9857
_NYC_WIDE_RADIUS_M = 15000.0
_DEFAULT_PROVIDER_RESULTS = 3
_MIN_PROVIDER_RESULTS = 1
_MAX_PROVIDER_RESULTS = 8
_COORD_RE = re.compile(r"^-?\d+\.?\d*,\s*-?\d+\.?\d*$")


_OPEN_BONUS = {"open": 0.15, "unknown": 0.05, "closed": 0.0}


def _finite(value: object, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default


def _finite_or_none(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _clamped(value: float, maximum: float) -> float:
    return max(0.0, min(maximum, value))


def baseline_ranking(place: dict) -> dict[str, object]:
    """Deterministic advisory ranking metadata; never reorders provider results."""

    rating = _clamped(_finite(place.get("rating")), 5.0) / 5.0
    review = _clamped(_finite(place.get("review_count")), 5000.0) / 5000.0
    open_status = str(place.get("open_status") or "unknown")
    open_bonus = _OPEN_BONUS.get(open_status, 0.05)
    score = round(0.50 * rating + 0.25 * review + 0.25 * open_bonus, 4)
    return {
        "baseline_score": score,
        "ranking_factors": {
            "rating": round(rating, 4),
            "review_volume": round(review, 4),
            "open_bonus": open_bonus,
            "price_level": place.get("price_level"),
        },
    }


def _resolve_api_key() -> str | None:
    return (
        os.getenv("GOOGLE_PLACES_API_KEY") or os.getenv("GOOGLE_ROUTES_API_KEY") or None
    )


async def _resolve_bias(
    near_raw: str,
    ctx: ToolContext,
) -> tuple[tuple[float, float, float] | None, str | None]:
    value = (near_raw or "").strip()
    if not value:
        return (_NYC_CENTER_LAT, _NYC_CENTER_LNG, _NYC_WIDE_RADIUS_M), None
    if _COORD_RE.match(value):
        lat_str, lng_str = value.split(",")
        return (float(lat_str.strip()), float(lng_str.strip()), _POI_RADIUS_M), None
    coords, error = await resolve_named_point(
        value,
        ctx,
        missing_location_message=(
            "I need your location to search nearby; share GPS or give me a place name."
        ),
    )
    if coords is None:
        return None, error or "could not resolve that location"
    return (coords[0], coords[1], _POI_RADIUS_M), None


def _clamp_provider_results(raw_value: object) -> int:
    try:
        value = int(raw_value) if raw_value is not None else _DEFAULT_PROVIDER_RESULTS
    except (TypeError, ValueError):
        value = _DEFAULT_PROVIDER_RESULTS
    return max(_MIN_PROVIDER_RESULTS, min(_MAX_PROVIDER_RESULTS, value))


async def execute(tool_input: dict, ctx: ToolContext) -> ToolResult:
    """Run one bounded Google Places query and normalize the provider response."""

    timings = {"place_resolution_ms": 0.0, "place_normalization_ms": 0.0}
    query = str(tool_input.get("query") or "").strip()
    if not query:
        return ToolResult(ok=False, error="query is required", timings=timings)
    api_key = _resolve_api_key()
    if not api_key:
        return ToolResult(
            ok=False, error="place search is not configured", timings=timings
        )

    resolution_started = time.monotonic()
    bias, error = await _resolve_bias(str(tool_input.get("near") or ""), ctx)
    timings["place_resolution_ms"] = (time.monotonic() - resolution_started) * 1000
    if bias is None:
        return ToolResult(
            ok=False,
            error=error or "could not resolve that location",
            timings=timings,
        )
    lat, lng, radius_m = bias
    max_results = _clamp_provider_results(tool_input.get("max_results"))
    request_body = {
        "textQuery": query,
        "locationBias": {
            "circle": {
                "center": {"latitude": lat, "longitude": lng},
                "radius": radius_m,
            }
        },
        "pageSize": max_results,
    }
    page_token = tool_input.get("page_token")
    if isinstance(page_token, str) and page_token.strip():
        request_body["pageToken"] = page_token.strip()[:4096]
    payload, error = await fetch_json(
        "POST",
        PLACES_SEARCH_URL,
        timeout_s=POI_SEARCH_TIMEOUT_S,
        log_tag="agent-place-search",
        what="place search",
        json_body=request_body,
        headers={
            "Content-Type": "application/json",
            "X-Goog-Api-Key": api_key,
            "X-Goog-FieldMask": PLACES_FIELD_MASK,
        },
    )
    if error:
        return ToolResult(ok=False, error=error, timings=timings)

    normalization_started = time.monotonic()
    try:
        results = []
        for place in (payload or {}).get("places") or []:
            location = place.get("location") or {}
            place_lat = location.get("latitude")
            place_lng = location.get("longitude")
            if (
                place_lat is None
                or place_lng is None
                or not geo._is_in_nyc(place_lat, place_lng)
            ):
                continue
            opening_hours = place.get("currentOpeningHours") or {}
            results.append(
                {
                    "name": text._safe_text(
                        (place.get("displayName") or {}).get("text"), 80
                    ),
                    "address": text._safe_text(place.get("formattedAddress"), 120),
                    "place_id": str(place.get("id") or "").strip() or None,
                    "lat": place_lat,
                    "lng": place_lng,
                    "open_now": (
                        opening_hours.get("openNow")
                        if "openNow" in opening_hours
                        else None
                    ),
                    "price_level": normalize_price_level(place.get("priceLevel")),
                    "rating": place.get("rating"),
                    "review_count": place.get("userRatingCount"),
                    "address_components": place.get("addressComponents") or [],
                }
            )
    except (KeyError, TypeError, AttributeError) as exc:
        _LOGGER.warning(
            "malformed Places response type=%s",
            type(exc).__name__,
        )
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
    next_page_token = (payload or {}).get("nextPageToken")
    if not isinstance(next_page_token, str) or not next_page_token.strip():
        next_page_token = None
    return ToolResult(
        ok=True,
        data={
            "results": results,
            "next_page_token": (
                next_page_token.strip()[:4096] if next_page_token else None
            ),
        },
        timings=timings,
    )


async def _provider_search(tool_input: dict, ctx: ToolContext) -> ToolResult:
    """Contain provider exceptions at the structured-search boundary."""

    try:
        result = await execute(tool_input, ctx)
    except Exception:
        _LOGGER.warning("place discovery provider failed")
        return ToolResult(ok=False, error="place search is temporarily unavailable")
    if isinstance(result, ToolResult):
        return result
    return ToolResult(ok=False, error="place search returned no usable result")


def _place_identity(place: dict) -> tuple[str, str]:
    provider_id = str(place.get("provider_place_id") or "").strip().casefold()
    if provider_id:
        return "provider", provider_id
    name = discovery_store._normalized_name(place.get("name"))
    address = " ".join(str(place.get("address") or "").casefold().split())
    if name or address:
        return "name_address", f"{name}|{address}"
    return "coordinates", f"{place.get('latitude')}|{place.get('longitude')}"


def _coverage(
    areas: list[str] | list[dict[str, str | None]],
    results: list[ToolResult],
) -> dict[str, object]:
    labels = [
        (str(area.get("label") or "NYC").strip() or "NYC")
        if isinstance(area, dict)
        else (str(area or "NYC").strip() or "NYC")
        for area in areas
    ]
    unavailable = [
        area for area, result in zip(labels, results, strict=False) if not result.ok
    ]
    return {
        "status": "partial" if unavailable else "complete",
        "searched_areas": list(dict.fromkeys(labels)),
        "unavailable_areas": list(dict.fromkeys(unavailable)),
    }


def _target_accepts_place(
    place: dict,
    target: dict[str, str | None],
    scope: dict,
) -> bool:
    """Keep a provider result in the geography that requested it."""

    if scope["kind"] not in {"boroughs", "nyc"}:
        return True
    address = place.get("address") or place.get("formatted_address")
    borough = conversational_geography.resolve_place_borough(
        address_components=place.get("address_components")
        or place.get("addressComponents"),
        formatted_address=address,
        neighborhood=place.get("neighborhood") or place.get("borough"),
    )
    return borough == conversational_geography.canonical_borough(target.get("label"))


def _search_targets(scope: dict) -> list[dict[str, str | None]]:
    kind = scope["kind"]
    if kind == "current_location":
        return [{"near": "user", "label": ""}]
    if kind == "named_area":
        area = scope["values"][0]
        return [{"near": area, "label": area}]
    if kind == "boroughs":
        return [{"near": borough, "label": borough} for borough in scope["values"]]
    if kind == "nyc":
        return [
            {"near": borough, "label": borough}
            for borough in conversational_geography.CANONICAL_BOROUGHS
        ]
    return [{"near": None, "label": ""}]


def _normalize_discovery_place(
    place: dict, query: str, search_area: str
) -> dict | None:
    if not isinstance(place, dict):
        return None
    address = place.get("address") or place.get("formatted_address") or ""
    components = place.get("address_components") or place.get("addressComponents")
    borough = conversational_geography.resolve_place_borough(
        address_components=components,
        formatted_address=address,
        neighborhood=place.get("neighborhood") or place.get("borough"),
    )
    open_now = place.get("open_now")
    location = place.get("location")
    latitude = (
        location.get("latitude")
        if isinstance(location, dict)
        else place.get("lat") or place.get("latitude")
    )
    longitude = (
        location.get("longitude")
        if isinstance(location, dict)
        else place.get("lng") or place.get("longitude")
    )
    latitude = _finite_or_none(latitude)
    longitude = _finite_or_none(longitude)
    return {
        "name": place.get("name") or place.get("display_name"),
        "address": address,
        "neighborhood": place.get("neighborhood") or "",
        "borough": borough,
        "category": place.get("category") or query,
        "open_status": "open"
        if open_now is True
        else ("closed" if open_now is False else "unknown"),
        "price_level": place.get("price_level"),
        "rating": place.get("rating"),
        "review_count": place.get("review_count") or place.get("user_rating_count"),
        "latitude": latitude,
        "longitude": longitude,
        "provider_place_id": place.get("place_id") or place.get("id"),
        "transit_context": place.get("transit_context") or {},
        "search_area": search_area,
        "address_components": components or [],
    }


def _authorized_for_scope(place: dict, scope: dict) -> bool:
    kind = scope["kind"]
    if kind == "boroughs":
        return place.get("borough") in scope["values"]
    if kind != "nyc":
        return True
    return (
        conversational_geography.is_nyc_locality(
            place.get("address_components"), place.get("address")
        )
        or bool(place.get("borough"))
        or _has_nyc_coordinates(place)
    )


def _has_nyc_coordinates(place: dict) -> bool:
    try:
        latitude = float(place.get("latitude"))
        longitude = float(place.get("longitude"))
    except (TypeError, ValueError):
        return False
    return geo._is_in_nyc(latitude, longitude)


def _model_place(place: dict) -> dict:
    model = {
        "place_id": place.get("place_id"),
        "ordinal": place.get("ordinal"),
        "name": place.get("name"),
        "address": place.get("address"),
        "borough": place.get("borough"),
        "neighborhood": place.get("neighborhood"),
        "open_status": place.get("open_status"),
        "price_level": place.get("price_level"),
        "rating": place.get("rating"),
        "review_count": place.get("review_count"),
        "baseline_order": place.get("ordinal"),
    }
    for field in ("latitude", "longitude", "rider_distance_meters"):
        value = _finite_or_none(place.get(field))
        if value is not None:
            model[field] = value
    return model


def _provider_places(result: ToolResult) -> list[dict]:
    data = result.data if isinstance(result.data, dict) else {}
    return [
        place
        for place in (data.get("places") or data.get("results") or [])
        if isinstance(place, dict)
    ]


def _merged_timings(results: list[ToolResult]) -> dict[str, float]:
    timings: dict[str, float] = {}
    for result in results:
        for name, duration in result.timings.items():
            timings[name] = timings.get(name, 0.0) + max(0.0, float(duration))
    return timings
