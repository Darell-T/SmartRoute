"""event_lookup tool: looks up NYC-area events (games, concerts) via
Ticketmaster Discovery v2, so the model never states an event's time from
memory. Pairs with venue_crowd_window: this tool supplies the estimated end
time that venue_crowd_window turns into a subway surge window.

Ticketmaster gives real start times but not end times, so end times here are
always a heuristic derived from venues.estimate_event_duration() -- never
presented to the rider as an official schedule.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import os
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from app.services.agent import venues
from app.services.agent.tools._http import fetch_json
from app.services.agent.tools._types import ToolContext, ToolResult
from app.services.trips import text
from app.utils import cache
from app.utils.geo import distance_meters

TICKETMASTER_EVENTS_URL = "https://app.ticketmaster.com/discovery/v2/events.json"
# Discovery v2 accepts latitude,longitude.  Use an explicit, deliberately
# bounded radius instead of the broader NY DMA, which also returns events far
# outside the area a NYC transit rider can reasonably use.
TICKETMASTER_NYC_LATLONG = "40.7128,-74.0060"
EVENT_LOOKUP_DEFAULT_RADIUS_MILES = 25
EVENT_LOOKUP_MAX_RADIUS_MILES = 30
EVENT_LOOKUP_DEFAULT_TIMEOUT_S = 6.0
EVENT_LOOKUP_MAX_TIMEOUT_S = 15.0
EVENT_LOOKUP_CACHE_TTL_S = 600
EVENT_LOOKUP_CACHE_PREFIX = "agent:events:"
EVENT_LOOKUP_PAGE_SIZE = 10
EVENT_LOOKUP_MAX_PAGES = 2
EVENT_LOOKUP_MAX_RESULTS = 5

_ET = ZoneInfo("America/New_York")
_inflight_locks: dict[tuple[int, str], asyncio.Lock] = {}

EVENT_LOOKUP_SCHEMA = {
    "name": "event_lookup",
    "description": (
        "Look up an NYC-area event (sports game, concert) by name via "
        "Ticketmaster to ground its start time and estimate its end time. "
        "Call this before stating any event time, or before venue_crowd_window."
    ),
    "strict": True,
    "input_schema": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": (
                    "Optional event, team, or artist name. Omit for a bounded "
                    "all-events search near a route hub."
                ),
            },
            "date": {
                "type": "string",
                "description": "YYYY-MM-DD to narrow the search to a single day (America/New_York).",
            },
            "venue": {
                "type": "string",
                "description": "Venue name to narrow the search, e.g. 'Madison Square Garden'.",
            },
            "latitude": {
                "type": "number",
                "description": "Optional NYC route-hub latitude for a local event search.",
            },
            "longitude": {
                "type": "number",
                "description": "Optional NYC route-hub longitude for a local event search.",
            },
            "radius_miles": {
                "type": "number",
                "description": "Optional bounded search radius. Internal route searches use a small radius.",
            },
        },
        "required": [],
        "additionalProperties": False,
    },
}


def _et_day_bounds_utc(date_str: str) -> tuple[str, str] | None:
    """YYYY-MM-DD (interpreted as an America/New_York calendar day) -> UTC
    `(startDateTime, endDateTime)` strings Ticketmaster accepts."""
    try:
        day = datetime.strptime(date_str, "%Y-%m-%d")
    except ValueError:
        return None
    start_et = day.replace(tzinfo=_ET)
    end_et = start_et + timedelta(days=1) - timedelta(seconds=1)
    fmt = "%Y-%m-%dT%H:%M:%SZ"
    return start_et.astimezone(timezone.utc).strftime(fmt), end_et.astimezone(timezone.utc).strftime(fmt)


def _cache_key(
    query: str,
    date: str | None,
    venue: str | None,
    radius_miles: float,
    latitude: float,
    longitude: float,
) -> str:
    normalized = {
        "q": query.strip().lower(),
        "date": (date or "").strip(),
        "venue": (venue or "").strip().lower(),
        "radius_miles": radius_miles,
        "latitude": round(latitude, 4),
        "longitude": round(longitude, 4),
    }
    canonical = json.dumps(normalized, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]
    return f"{EVENT_LOOKUP_CACHE_PREFIX}{digest}"


def _read_cache(key: str) -> dict | None:
    try:
        raw = cache.cache_get(key)
    except Exception as exc:  # Cache outages must not disable event lookup.
        print(f"[agent-event_lookup] cache read failed: {type(exc).__name__}")
        return None
    if raw is None:
        return None
    try:
        blob = raw.decode("utf-8") if isinstance(raw, (bytes, bytearray)) else raw
        return json.loads(blob)
    except (ValueError, TypeError, UnicodeDecodeError):
        return None


def _write_cache(key: str, value: dict) -> None:
    try:
        cache.cache_set(key, json.dumps(value, default=str), EVENT_LOOKUP_CACHE_TTL_S)
    except Exception as exc:  # Cache outages must not turn a successful lookup into a failure.
        print(f"[agent-event_lookup] cache write failed: {type(exc).__name__}")


def _positive_float_env(name: str, default: float, maximum: float) -> float:
    """Read a bounded operational setting without exposing environment data."""
    try:
        value = float(os.getenv(name, str(default)))
    except ValueError:
        return default
    if not math.isfinite(value):
        return default
    return min(max(value, 0.1), maximum)


def _lookup_lock(cache_key: str) -> asyncio.Lock:
    # Locks are per event loop because this module is exercised by multiple
    # IsolatedAsyncioTestCase loops and can be used by multiple server loops.
    key = (id(asyncio.get_running_loop()), cache_key)
    lock = _inflight_locks.get(key)
    if lock is None:
        lock = asyncio.Lock()
        _inflight_locks[key] = lock
    return lock


def _mapping(value: object) -> dict:
    return value if isinstance(value, dict) else {}


def _event_dates(event: dict) -> dict:
    return _mapping(event.get("dates"))


def _event_start_iso(event: dict) -> str | None:
    dates = _event_dates(event)
    start = _mapping(dates.get("start"))
    if any(start.get(flag) is True for flag in ("dateTBA", "dateTBD", "timeTBA", "noSpecificTime")):
        return None
    start_iso = start.get("dateTime")
    if isinstance(start_iso, str):
        try:
            parsed = datetime.fromisoformat(start_iso.replace("Z", "+00:00"))
        except ValueError:
            return None
        if parsed.tzinfo is None:
            return None
        return parsed.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    local_date = start.get("localDate")
    local_time = start.get("localTime")
    # A date without an official start time is useful context, but never a
    # basis for a crowd-window calculation.  In particular, do not invent 7pm.
    if not isinstance(local_date, str) or not isinstance(local_time, str):
        return None
    try:
        naive = datetime.strptime(f"{local_date} {local_time}", "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None
    timezone_name = dates.get("timezone") or event.get("timezone")
    try:
        event_timezone = ZoneInfo(timezone_name) if isinstance(timezone_name, str) else _ET
    except (ValueError, KeyError):
        event_timezone = _ET
    return naive.replace(tzinfo=event_timezone).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _start_time_status(event: dict) -> str:
    start = _mapping(_event_dates(event).get("start"))
    if start.get("dateTBA") is True:
        return "date_tba"
    if start.get("dateTBD") is True:
        return "date_tbd"
    if start.get("timeTBA") is True:
        return "time_tba"
    if start.get("noSpecificTime") is True:
        return "no_specific_time"
    if isinstance(start.get("dateTime"), str) or isinstance(start.get("localTime"), str):
        return "confirmed"
    if isinstance(start.get("localDate"), str):
        return "date_only"
    return "unknown"


def _venue_coordinates(venue: dict | None) -> tuple[float | None, float | None]:
    if not isinstance(venue, dict):
        return None, None
    location = venue.get("location") or {}
    if not isinstance(location, dict):
        return None, None
    try:
        latitude = float(location.get("latitude"))
        longitude = float(location.get("longitude"))
    except (TypeError, ValueError):
        return None, None
    if not (-90 <= latitude <= 90 and -180 <= longitude <= 180):
        return None, None
    return latitude, longitude


def _is_within_search_radius(
    latitude: float,
    longitude: float,
    center_latitude: float,
    center_longitude: float,
    radius_miles: float,
) -> bool:
    """Defend against an upstream geo-filter miss using the actual search hub."""

    return (
        distance_meters(
            latitude,
            longitude,
            center_latitude,
            center_longitude,
        )
        <= radius_miles * 1609.344
    )


def _classification_strings(event: dict) -> tuple[str, str, str]:
    classifications = event.get("classifications") or []
    if not isinstance(classifications, list) or not classifications:
        return "", "", ""
    first = _mapping(classifications[0])
    segment = _mapping(first.get("segment")).get("name") or ""
    genre = _mapping(first.get("genre")).get("name") or ""
    sub_genre = _mapping(first.get("subGenre")).get("name") or ""
    return segment, genre, sub_genre


def _parse_event(event: dict) -> dict:
    name = text._safe_text(event.get("name"), 120)
    event_venues = _mapping(event.get("_embedded")).get("venues") or []
    if not isinstance(event_venues, list):
        event_venues = []
    first_venue = event_venues[0] if event_venues and isinstance(event_venues[0], dict) else None
    venue_name_raw = first_venue.get("name") if first_venue else None
    venue_name = text._safe_text(venue_name_raw, 80) if venue_name_raw else None
    venue_key = venues.normalize_venue_name(venue_name_raw)
    latitude, longitude = _venue_coordinates(first_venue)
    venue_context = venues.VENUE_CROWD_TABLE.get(venue_key or "") or {}

    start_iso = _event_start_iso(event)
    dates = _event_dates(event)
    status = text._safe_text(_mapping(dates.get("status")).get("code"), 32).lower() or "unknown"
    estimated_end_iso = None
    end_estimate_basis = None
    # Cancelled, postponed, and rescheduled listings may retain an obsolete
    # timestamp.  Surface their status, but never use it to create a rider
    # crowd prediction until Ticketmaster reports a normal event status.
    if start_iso and status not in {"canceled", "cancelled", "postponed", "rescheduled"}:
        try:
            start_dt = datetime.strptime(start_iso, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        except ValueError:
            start_dt = None
        if start_dt is not None:
            duration, basis = venues.estimate_event_duration(*_classification_strings(event))
            estimated_end_iso = (start_dt + duration).strftime("%Y-%m-%dT%H:%M:%SZ")
            end_estimate_basis = basis

    return {
        "event_id": text._safe_text(event.get("id"), 80) or None,
        "name": name,
        "venue_name": venue_name,
        "venue_key": venue_key,
        "venue_latitude": latitude,
        "venue_longitude": longitude,
        "nearby_stations": list(venue_context.get("stations") or []),
        "nearby_lines": list(venue_context.get("lines") or []),
        "status": status,
        "start_time_status": _start_time_status(event),
        "local_date": text._safe_text(_mapping(dates.get("start")).get("localDate"), 10) or None,
        "start_iso": start_iso,
        "estimated_end_iso": estimated_end_iso,
        "end_estimate_basis": end_estimate_basis,
    }


def _event_identity(event: dict) -> str:
    event_id = event.get("event_id")
    if event_id:
        return f"id:{event_id}"
    return "fallback:" + "|".join(
        str(event.get(key) or "").strip().lower() for key in ("name", "venue_name", "start_iso", "local_date")
    )


def _events_from_payload(payload: object) -> tuple[list[dict] | None, int]:
    if not isinstance(payload, dict):
        return None, 0
    embedded = payload.get("_embedded")
    if embedded is None:
        return [], 0
    if not isinstance(embedded, dict):
        return None, 0
    raw_events = embedded.get("events")
    if raw_events is None:
        return [], 0
    if not isinstance(raw_events, list):
        return None, 0
    if any(not isinstance(event, dict) for event in raw_events):
        return None, 0
    page = payload.get("page") or {}
    total_pages = page.get("totalPages", 0) if isinstance(page, dict) else 0
    return raw_events, total_pages if isinstance(total_pages, int) else 0


async def _lookup_uncached(
    query: str,
    date: str | None,
    venue: str | None,
    api_key: str,
    radius_miles: float,
    latitude: float = 40.7128,
    longitude: float = -74.0060,
) -> ToolResult:
    params = {
        "apikey": api_key,
        "latlong": (
            TICKETMASTER_NYC_LATLONG
            if (latitude, longitude) == (40.7128, -74.0060)
            else f"{latitude:.6f},{longitude:.6f}"
        ),
        "radius": f"{radius_miles:g}",
        "unit": "miles",
        # Unscheduled listings cannot support an event-timing crowd window.
        # Keep defensive parsing below because upstream records can still be
        # incomplete or change shape.
        "includeTBA": "no",
        "includeTBD": "no",
        "size": str(EVENT_LOOKUP_PAGE_SIZE),
        "page": "0",
        "sort": "date,asc",
    }
    keyword = f"{query} {venue}".strip() if venue else query
    if keyword:
        params["keyword"] = keyword
    if date:
        bounds = _et_day_bounds_utc(date)
        if bounds is None:
            return ToolResult(ok=False, error="date must be YYYY-MM-DD")
        params["startDateTime"], params["endDateTime"] = bounds

    timeout_s = _positive_float_env("EVENT_LOOKUP_TIMEOUT_S", EVENT_LOOKUP_DEFAULT_TIMEOUT_S, EVENT_LOOKUP_MAX_TIMEOUT_S)
    parsed_events: list[dict] = []
    seen: set[str] = set()
    pages_to_fetch = 1
    partial = False

    for page_number in range(EVENT_LOOKUP_MAX_PAGES):
        params["page"] = str(page_number)
        payload, error = await fetch_json(
            "GET",
            TICKETMASTER_EVENTS_URL,
            timeout_s=timeout_s,
            log_tag="agent-event_lookup",
            what="event lookup",
            params=params.copy(),
        )
        if error:
            if parsed_events:
                partial = True
                break
            return ToolResult(ok=False, error=error)

        raw_events, total_pages = _events_from_payload(payload)
        if raw_events is None:
            return ToolResult(ok=False, error="event lookup returned an unexpected response")
        for raw_event in raw_events:
            parsed = _parse_event(raw_event)
            event_latitude = parsed["venue_latitude"]
            event_longitude = parsed["venue_longitude"]
            if (
                event_latitude is not None
                and event_longitude is not None
                and not _is_within_search_radius(
                    event_latitude,
                    event_longitude,
                    latitude,
                    longitude,
                    radius_miles,
                )
            ):
                continue
            # A cancelled event cannot produce a useful current crowd window.
            if parsed["status"] in {"canceled", "cancelled"}:
                continue
            identity = _event_identity(parsed)
            if identity in seen:
                continue
            seen.add(identity)
            parsed_events.append(parsed)
            if len(parsed_events) >= EVENT_LOOKUP_MAX_RESULTS:
                break

        # Fetch page two only when page one did not yield enough usable,
        # distinct events; this is bounded pagination, not an unbounded crawl.
        pages_to_fetch = min(max(total_pages, 1), EVENT_LOOKUP_MAX_PAGES)
        if len(parsed_events) >= EVENT_LOOKUP_MAX_RESULTS or page_number + 1 >= pages_to_fetch:
            break

    data: dict = {"events": parsed_events}
    if any(event.get("estimated_end_iso") for event in parsed_events):
        data["note"] = "end times are estimates based on typical event length, not an official schedule"
    if partial:
        data["partial"] = True

    subject = f"'{query}'" if query else "the route area"
    summary = (
        f"found {len(parsed_events)} event(s) for {subject}"
        if parsed_events
        else f"no events found for {subject}"
    )
    return ToolResult(ok=True, data=data, summary=summary)


async def execute(tool_input: dict, ctx: ToolContext) -> ToolResult:
    query = str(tool_input.get("query") or "").strip()

    if os.getenv("TICKETMASTER_ENABLED", "true").strip().lower() in {"0", "false", "no", "off"}:
        return ToolResult(ok=False, error="event lookup is disabled")

    api_key = os.getenv("TICKETMASTER_API_KEY", "").strip()
    if not api_key:
        return ToolResult(ok=False, error="event lookup is not configured")

    date = tool_input.get("date")
    venue = tool_input.get("venue")

    configured_radius = _positive_float_env(
        "TICKETMASTER_SEARCH_RADIUS_MILES", EVENT_LOOKUP_DEFAULT_RADIUS_MILES, EVENT_LOOKUP_MAX_RADIUS_MILES
    )
    try:
        requested_radius = float(tool_input.get("radius_miles", configured_radius))
    except (TypeError, ValueError):
        requested_radius = configured_radius
    radius_miles = (
        min(max(requested_radius, 0.1), EVENT_LOOKUP_MAX_RADIUS_MILES)
        if math.isfinite(requested_radius)
        else configured_radius
    )
    try:
        latitude = float(tool_input.get("latitude", 40.7128))
        longitude = float(tool_input.get("longitude", -74.0060))
    except (TypeError, ValueError):
        return ToolResult(ok=False, error="event search location is invalid")
    if not (-90 <= latitude <= 90 and -180 <= longitude <= 180):
        return ToolResult(ok=False, error="event search location is invalid")
    if not _is_within_search_radius(
        latitude,
        longitude,
        40.7128,
        -74.0060,
        EVENT_LOOKUP_MAX_RADIUS_MILES,
    ):
        return ToolResult(ok=False, error="event search location is outside the NYC service area")

    cache_key = _cache_key(query, date, venue, radius_miles, latitude, longitude)
    cached = _read_cache(cache_key)
    if cached is not None:
        return ToolResult(ok=True, data=cached.get("data"), summary=cached.get("summary") or "")

    lock = _lookup_lock(cache_key)
    lock_key = (id(asyncio.get_running_loop()), cache_key)
    try:
        async with lock:
            cached = _read_cache(cache_key)
            if cached is not None:
                return ToolResult(ok=True, data=cached.get("data"), summary=cached.get("summary") or "")
            result = await _lookup_uncached(
                query,
                str(date) if date is not None else None,
                str(venue) if venue is not None else None,
                api_key,
                radius_miles,
                latitude,
                longitude,
            )
            if result.ok:
                _write_cache(cache_key, {"data": result.data, "summary": result.summary})
            return result
    finally:
        # Cache data is committed before release.  Removing an idle lock keeps
        # one-off natural-language queries from growing this process-local map.
        if not lock.locked() and _inflight_locks.get(lock_key) is lock:
            _inflight_locks.pop(lock_key, None)
