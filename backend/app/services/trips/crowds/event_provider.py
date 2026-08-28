"""Ticketmaster event facts and neutral venue/event normalization.

This module owns provider I/O, bounded caching, Ticketmaster payload parsing,
and the static venue facts used by trip crowd evidence. Agent capabilities
adapt its result into ``ToolResult``; trip services can call it directly.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import os
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from app.services import cache
from app.services.geography import distance_meters
from app.services.trips import text

TICKETMASTER_EVENTS_URL = "https://app.ticketmaster.com/discovery/v2/events.json"
TICKETMASTER_NYC_LATLONG = "40.7128,-74.0060"
EVENT_LOOKUP_DEFAULT_RADIUS_MILES = 25
EVENT_LOOKUP_MAX_RADIUS_MILES = 30
EVENT_LOOKUP_DEFAULT_TIMEOUT_S = 6.0
EVENT_LOOKUP_MAX_TIMEOUT_S = 15.0
EVENT_LOOKUP_CACHE_TTL_S = 600
# Keep the deployed namespace so existing cached event evidence remains usable.
EVENT_LOOKUP_CACHE_PREFIX = "agent:events:"
EVENT_LOOKUP_PAGE_SIZE = 10
EVENT_LOOKUP_MAX_PAGES = 2
EVENT_LOOKUP_MAX_RESULTS = 5

_ET = ZoneInfo("America/New_York")
_inflight_locks: dict[tuple[int, str], asyncio.Lock] = {}


@dataclass(frozen=True)
class EventLookupResult:
    """Provider-neutral result consumed by trips and agent adapters."""

    ok: bool
    data: dict | None = None
    summary: str = ""
    error: str | None = None


# Static facts are intentionally conservative and are not live crowd sensors.
_DURATION_RULES: list[tuple[tuple[str, ...], timedelta, str]] = [
    (("nba",), timedelta(hours=2, minutes=30), "NBA game"),
    (("nhl",), timedelta(hours=2, minutes=30), "NHL game"),
    (("mlb", "baseball"), timedelta(hours=3), "MLB game"),
    (("nfl",), timedelta(hours=3, minutes=15), "NFL game"),
    (("soccer", "fifa", "football (soccer)"), timedelta(hours=2, minutes=15), "Soccer match"),
    (("music", "concert"), timedelta(hours=3), "Concert"),
]
_DEFAULT_DURATION = timedelta(hours=3)
_VENUE_CROWD_TABLE: dict[str, dict] = {
    "msg": {
        "stations": ["34 St-Penn Station"],
        "lines": ["1", "2", "3", "A", "C", "E"],
        "alternates": "walk ~5 min to Herald Sq (B/D/F/M/N/Q/R/W) or 28 St",
        "note": "",
    },
    "barclays": {
        "stations": ["Atlantic Av-Barclays Ctr"],
        "lines": ["2", "3", "4", "5", "B", "D", "N", "Q", "R", "W"],
        "alternates": "walk a few minutes to Nevins St (2/3/4/5) or Dean St (B/Q) for lighter platforms",
        "note": "",
    },
    "yankee_stadium": {
        "stations": ["161 St-Yankee Stadium"],
        "lines": ["4", "B", "D"],
        "alternates": "consider Mount Eden Av (4) one stop away, or Metro-North's Yankees-E 153 St station",
        "note": "",
    },
    "citi_field": {
        "stations": ["Mets-Willets Point"],
        "lines": ["7"],
        "alternates": "LIRR runs supplemental service from Mets-Willets Point right after games",
        "note": "",
    },
    "penn_station": {
        "stations": ["34 St-Penn Station"],
        "lines": ["1", "2", "3", "A", "C", "E"],
        "alternates": "walk ~5 min to Herald Sq (B/D/F/M/N/Q/R/W) if the Penn Station platforms are jammed",
        "note": (
            "NJ Transit and LIRR concourses also get very crowded here after "
            "MetLife Stadium events (Giants, Jets, FIFA matches)."
        ),
    },
    "port_authority": {
        "stations": ["42 St-Port Authority Bus Terminal"],
        "lines": ["A", "C", "E", "N", "Q", "R", "W", "S", "1", "2", "3", "7"],
        "alternates": "consider walking a block to 5 Av/53 St (E/M) or Times Sq-42 St for lighter platforms",
        "note": "",
    },
}
VENUE_CROWD_TABLE = _VENUE_CROWD_TABLE
VENUE_ALIASES: dict[str, str] = {
    "madison square garden": "msg",
    "the garden": "msg",
    "msg": "msg",
    "barclays center": "barclays",
    "the barclays center": "barclays",
    "barclays": "barclays",
    "yankee stadium": "yankee_stadium",
    "citi field": "citi_field",
    "penn station": "penn_station",
    "pennsylvania station": "penn_station",
    "moynihan train hall": "penn_station",
    "port authority bus terminal": "port_authority",
    "port authority": "port_authority",
}


def _format_duration(duration: timedelta) -> str:
    total_minutes = int(duration.total_seconds() // 60)
    hours, minutes = divmod(total_minutes, 60)
    return f"{hours}h{minutes}m" if minutes else f"{hours}h"


def estimate_event_duration(*classification_strings: str | None) -> tuple[timedelta, str]:
    haystack = " ".join(value for value in classification_strings if value).lower()
    for keywords, duration, label in _DURATION_RULES:
        if any(keyword in haystack for keyword in keywords):
            return duration, f"{label} ≈ {_format_duration(duration)}"
    return _DEFAULT_DURATION, f"Event ≈ {_format_duration(_DEFAULT_DURATION)}"


def normalize_venue_name(name: str | None) -> str | None:
    if not name:
        return None
    key = " ".join(str(name).strip().lower().split())
    if not key:
        return None
    if key in VENUE_ALIASES:
        return VENUE_ALIASES[key]
    for alias, venue_key in VENUE_ALIASES.items():
        if alias in key:
            return venue_key
    return None


async def fetch_json(
    method: str,
    url: str,
    *,
    timeout_s: float,
    log_tag: str,
    what: str,
    params: dict | None = None,
    json_body: dict | None = None,
    headers: dict | None = None,
) -> tuple[dict | list | None, str | None]:
    # Lazy: agent.tools.__init__ imports venue tables from this module.
    from app.services.agent.tools.provider_http import fetch_json as shared_fetch_json

    return await shared_fetch_json(
        method,
        url,
        timeout_s=timeout_s,
        log_tag=log_tag,
        what=what,
        params=params,
        json_body=json_body,
        headers=headers,
    )


def _et_day_bounds_utc(date_str: str) -> tuple[str, str] | None:
    try:
        day = datetime.strptime(date_str, "%Y-%m-%d")
    except ValueError:
        return None
    start_et = day.replace(tzinfo=_ET)
    end_et = start_et + timedelta(days=1) - timedelta(seconds=1)
    fmt = "%Y-%m-%dT%H:%M:%SZ"
    return (
        start_et.astimezone(UTC).strftime(fmt),
        end_et.astimezone(UTC).strftime(fmt),
    )


def _cache_key(
    query: str,
    date: str | None,
    venue: str | None,
    radius_miles: float,
    latitude: float,
    longitude: float,
) -> str:
    canonical = json.dumps(
        {
            "q": query.strip().lower(),
            "date": (date or "").strip(),
            "venue": (venue or "").strip().lower(),
            "radius_miles": radius_miles,
            "latitude": round(latitude, 4),
            "longitude": round(longitude, 4),
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]
    return f"{EVENT_LOOKUP_CACHE_PREFIX}{digest}"


def _read_cache(key: str) -> dict | None:
    try:
        raw = cache.cache_get(key)
    except Exception as exc:  # noqa: BLE001 cache faults miss this lookup
        print(f"[event-provider] cache read failed: {type(exc).__name__}")
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
    except Exception as exc:  # noqa: BLE001 cache faults skip storing this lookup
        print(f"[event-provider] cache write failed: {type(exc).__name__}")


def _positive_float_env(name: str, default: float, maximum: float) -> float:
    try:
        value = float(os.getenv(name, str(default)))
    except ValueError:
        return default
    if not math.isfinite(value):
        return default
    return min(max(value, 0.1), maximum)


def _lookup_lock(cache_key: str) -> asyncio.Lock:
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
    start = _mapping(_event_dates(event).get("start"))
    if any(
        start.get(flag) is True
        for flag in ("dateTBA", "dateTBD", "timeTBA", "noSpecificTime")
    ):
        return None
    start_iso = start.get("dateTime")
    if isinstance(start_iso, str):
        try:
            parsed = datetime.fromisoformat(start_iso)
        except ValueError:
            return None
        if parsed.tzinfo is None:
            return None
        return parsed.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    local_date = start.get("localDate")
    local_time = start.get("localTime")
    if not isinstance(local_date, str) or not isinstance(local_time, str):
        return None
    try:
        naive = datetime.strptime(f"{local_date} {local_time}", "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None
    timezone_name = _event_dates(event).get("timezone") or event.get("timezone")
    try:
        event_timezone = ZoneInfo(timezone_name) if isinstance(timezone_name, str) else _ET
    except (ValueError, KeyError):
        event_timezone = _ET
    return naive.replace(tzinfo=event_timezone).astimezone(UTC).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )


def _start_time_status(event: dict) -> str:
    start = _mapping(_event_dates(event).get("start"))
    for field, status in (
        ("dateTBA", "date_tba"),
        ("dateTBD", "date_tbd"),
        ("timeTBA", "time_tba"),
        ("noSpecificTime", "no_specific_time"),
    ):
        if start.get(field) is True:
            return status
    if isinstance(start.get("dateTime"), str) or isinstance(start.get("localTime"), str):
        return "confirmed"
    if isinstance(start.get("localDate"), str):
        return "date_only"
    return "unknown"


def _venue_coordinates(venue: dict | None) -> tuple[float | None, float | None]:
    location = venue.get("location") if isinstance(venue, dict) else None
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
    return distance_meters(
        latitude,
        longitude,
        center_latitude,
        center_longitude,
    ) <= radius_miles * 1609.344


def _classification_strings(event: dict) -> tuple[str, str, str]:
    classifications = event.get("classifications") or []
    if not isinstance(classifications, list) or not classifications:
        return "", "", ""
    first = _mapping(classifications[0])
    return (
        _mapping(first.get("segment")).get("name") or "",
        _mapping(first.get("genre")).get("name") or "",
        _mapping(first.get("subGenre")).get("name") or "",
    )


def _parse_event(event: dict) -> dict:
    name = text._safe_text(event.get("name"), 120)
    event_venues = _mapping(event.get("_embedded")).get("venues") or []
    if not isinstance(event_venues, list):
        event_venues = []
    first_venue = (
        event_venues[0]
        if event_venues and isinstance(event_venues[0], dict)
        else None
    )
    venue_name_raw = first_venue.get("name") if first_venue else None
    venue_name = text._safe_text(venue_name_raw, 80) if venue_name_raw else None
    venue_key = normalize_venue_name(venue_name_raw)
    latitude, longitude = _venue_coordinates(first_venue)
    venue_context = VENUE_CROWD_TABLE.get(venue_key or "") or {}
    start_iso = _event_start_iso(event)
    dates = _event_dates(event)
    status = text._safe_text(_mapping(dates.get("status")).get("code"), 32).lower() or "unknown"
    estimated_end_iso = None
    end_estimate_basis = None
    if start_iso and status not in {"canceled", "cancelled", "postponed", "rescheduled"}:
        try:
            start_dt = datetime.strptime(start_iso, "%Y-%m-%dT%H:%M:%SZ").replace(
                tzinfo=UTC
            )
        except ValueError:
            start_dt = None
        if start_dt is not None:
            duration, basis = estimate_event_duration(*_classification_strings(event))
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
        str(event.get(key) or "").strip().lower()
        for key in ("name", "venue_name", "start_iso", "local_date")
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
    if not isinstance(raw_events, list) or any(
        not isinstance(event, dict) for event in raw_events
    ):
        return None, 0
    page = payload.get("page") or {}
    total_pages = page.get("totalPages", 0) if isinstance(page, dict) else 0
    return raw_events, total_pages if isinstance(total_pages, int) else 0


FetchJSON = Callable[..., Awaitable[tuple[dict | list | None, str | None]]]


def _ticketmaster_params(
    query: str,
    date: str | None,
    venue: str | None,
    api_key: str,
    radius_miles: float,
    latitude: float,
    longitude: float,
) -> dict | EventLookupResult:
    params = {
        "apikey": api_key,
        "latlong": (
            TICKETMASTER_NYC_LATLONG
            if (latitude, longitude) == (40.7128, -74.0060)
            else f"{latitude:.6f},{longitude:.6f}"
        ),
        "radius": str(max(1, math.ceil(radius_miles))),
        "unit": "miles",
        "includeTBA": "no",
        "includeTBD": "no",
        "size": str(EVENT_LOOKUP_PAGE_SIZE),
        "page": "0",
        "sort": "date,asc",
    }
    keyword = f"{query} {venue}".strip() if venue else query
    if keyword:
        params["keyword"] = keyword
    if not date:
        return params
    bounds = _et_day_bounds_utc(date)
    if bounds is None:
        return EventLookupResult(ok=False, error="date must be YYYY-MM-DD")
    params["startDateTime"], params["endDateTime"] = bounds
    return params


def _event_in_search(
    parsed: dict,
    seen: set[str],
    latitude: float,
    longitude: float,
    radius_miles: float,
) -> bool:
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
        return False
    if parsed["status"] in {"canceled", "cancelled"}:
        return False
    return _event_identity(parsed) not in seen


def _append_page_events(
    raw_events: list[dict],
    parsed_events: list[dict],
    seen: set[str],
    latitude: float,
    longitude: float,
    radius_miles: float,
) -> None:
    for raw_event in raw_events:
        parsed = _parse_event(raw_event)
        if not _event_in_search(parsed, seen, latitude, longitude, radius_miles):
            continue
        seen.add(_event_identity(parsed))
        parsed_events.append(parsed)
        if len(parsed_events) >= EVENT_LOOKUP_MAX_RESULTS:
            return


async def _lookup_uncached(
    query: str,
    date: str | None,
    venue: str | None,
    api_key: str,
    radius_miles: float,
    latitude: float = 40.7128,
    longitude: float = -74.0060,
    *,
    fetch_json_impl: FetchJSON | None = None,
    max_pages: int | None = None,
) -> EventLookupResult:
    params = _ticketmaster_params(
        query, date, venue, api_key, radius_miles, latitude, longitude
    )
    if isinstance(params, EventLookupResult):
        return params
    fetch = fetch_json_impl or fetch_json
    timeout_s = _positive_float_env(
        "EVENT_LOOKUP_TIMEOUT_S",
        EVENT_LOOKUP_DEFAULT_TIMEOUT_S,
        EVENT_LOOKUP_MAX_TIMEOUT_S,
    )
    parsed_events: list[dict] = []
    seen: set[str] = set()
    partial = False
    page_limit = max(
        1,
        int(max_pages if max_pages is not None else EVENT_LOOKUP_MAX_PAGES),
    )
    for page_number in range(page_limit):
        params["page"] = str(page_number)
        payload, error = await fetch(
            "GET",
            TICKETMASTER_EVENTS_URL,
            timeout_s=timeout_s,
            log_tag="event-provider",
            what="event lookup",
            params=params.copy(),
        )
        if error:
            if parsed_events:
                partial = True
                break
            return EventLookupResult(ok=False, error=error)
        raw_events, total_pages = _events_from_payload(payload)
        if raw_events is None:
            return EventLookupResult(
                ok=False,
                error="event lookup returned an unexpected response",
            )
        _append_page_events(
            raw_events, parsed_events, seen, latitude, longitude, radius_miles
        )
        pages_to_fetch = min(max(total_pages, 1), page_limit)
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
    return EventLookupResult(ok=True, data=data, summary=summary)


def _search_point(tool_input: dict) -> tuple[float, float, float] | EventLookupResult:
    configured_radius = _positive_float_env(
        "TICKETMASTER_SEARCH_RADIUS_MILES",
        EVENT_LOOKUP_DEFAULT_RADIUS_MILES,
        EVENT_LOOKUP_MAX_RADIUS_MILES,
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
        return EventLookupResult(ok=False, error="event search location is invalid")
    if not (-90 <= latitude <= 90 and -180 <= longitude <= 180):
        return EventLookupResult(ok=False, error="event search location is invalid")
    if not _is_within_search_radius(
        latitude,
        longitude,
        40.7128,
        -74.0060,
        EVENT_LOOKUP_MAX_RADIUS_MILES,
    ):
        return EventLookupResult(
            ok=False,
            error="event search location is outside the NYC service area",
        )
    return latitude, longitude, radius_miles


async def lookup_events(
    tool_input: dict,
    _context: object | None = None,
    *,
    fetch_json_impl: FetchJSON | None = None,
    lookup_uncached_impl: Callable[..., Awaitable[EventLookupResult]] | None = None,
) -> EventLookupResult:
    """Look up bounded NYC-area events."""

    del _context
    query = str(tool_input.get("query") or "").strip()
    if os.getenv("TICKETMASTER_ENABLED", "true").strip().lower() in {
        "0",
        "false",
        "no",
        "off",
    }:
        return EventLookupResult(ok=False, error="event lookup is disabled")
    api_key = os.getenv("TICKETMASTER_API_KEY", "").strip()
    if not api_key:
        return EventLookupResult(ok=False, error="event lookup is not configured")

    date = tool_input.get("date")
    venue = tool_input.get("venue")
    point = _search_point(tool_input)
    if isinstance(point, EventLookupResult):
        return point
    latitude, longitude, radius_miles = point
    cache_key = _cache_key(query, date, venue, radius_miles, latitude, longitude)

    def cached_result() -> EventLookupResult | None:
        cached = _read_cache(cache_key)
        if cached is None:
            return None
        return EventLookupResult(
            ok=True,
            data=cached.get("data"),
            summary=cached.get("summary") or "",
        )

    cached = cached_result()
    if cached is not None:
        return cached
    lock = _lookup_lock(cache_key)
    lock_key = (id(asyncio.get_running_loop()), cache_key)
    try:
        async with lock:
            cached = cached_result()
            if cached is not None:
                return cached
            lookup = lookup_uncached_impl or _lookup_uncached
            result = await lookup(
                query,
                str(date) if date is not None else None,
                str(venue) if venue is not None else None,
                api_key,
                radius_miles,
                latitude,
                longitude,
                fetch_json_impl=fetch_json_impl,
            )
            if result.ok:
                _write_cache(
                    cache_key,
                    {
                        "data": getattr(result, "data", None),
                        "summary": getattr(result, "summary", ""),
                    },
                )
            return result
    finally:
        if not lock.locked() and _inflight_locks.get(lock_key) is lock:
            _inflight_locks.pop(lock_key, None)
