"""event_lookup tool: looks up NYC-area events (games, concerts) via
Ticketmaster Discovery v2, so the model never states an event's time from
memory. Pairs with venue_crowd_window: this tool supplies the estimated end
time that venue_crowd_window turns into a subway surge window.

Ticketmaster gives real start times but not end times, so end times here are
always a heuristic derived from venues.estimate_event_duration() -- never
presented to the rider as an official schedule.
"""

from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from app.services.agent import venues
from app.services.agent.tools._http import fetch_json
from app.services.agent.tools._types import ToolContext, ToolResult
from app.services.trips import text
from app.utils import cache

TICKETMASTER_EVENTS_URL = "https://app.ticketmaster.com/discovery/v2/events.json"
TICKETMASTER_DMA_ID = "345"  # New York DMA
EVENT_LOOKUP_TIMEOUT_S = float(os.getenv("EVENT_LOOKUP_TIMEOUT_S", "6.0"))
EVENT_LOOKUP_CACHE_TTL_S = 600
EVENT_LOOKUP_CACHE_PREFIX = "agent:events:"
EVENT_LOOKUP_SIZE = 5
EVENT_LOOKUP_DEFAULT_TIME = "19:00:00"  # used only when Ticketmaster gives a local date with no local time

_ET = ZoneInfo("America/New_York")

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
                "description": "Event, team, or artist name to search, e.g. 'Knicks' or 'FIFA'.",
            },
            "date": {
                "type": "string",
                "description": "YYYY-MM-DD to narrow the search to a single day (America/New_York).",
            },
            "venue": {
                "type": "string",
                "description": "Venue name to narrow the search, e.g. 'Madison Square Garden'.",
            },
        },
        "required": ["query"],
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


def _cache_key(query: str, date: str | None, venue: str | None) -> str:
    normalized = {
        "q": query.strip().lower(),
        "date": (date or "").strip(),
        "venue": (venue or "").strip().lower(),
    }
    canonical = json.dumps(normalized, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]
    return f"{EVENT_LOOKUP_CACHE_PREFIX}{digest}"


def _read_cache(key: str) -> dict | None:
    raw = cache.cache_get(key)
    if raw is None:
        return None
    try:
        blob = raw.decode("utf-8") if isinstance(raw, (bytes, bytearray)) else raw
        return json.loads(blob)
    except (ValueError, TypeError, UnicodeDecodeError):
        return None


def _event_start_iso(event: dict) -> str | None:
    dates = event.get("dates") or {}
    start = dates.get("start") or {}
    start_iso = start.get("dateTime")
    if start_iso:
        return start_iso
    local_date = start.get("localDate")
    if not local_date:
        return None
    local_time = start.get("localTime") or EVENT_LOOKUP_DEFAULT_TIME
    try:
        naive = datetime.strptime(f"{local_date} {local_time}", "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None
    start_et = naive.replace(tzinfo=_ET)
    return start_et.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _classification_strings(event: dict) -> tuple[str, str, str]:
    classifications = event.get("classifications") or []
    if not classifications:
        return "", "", ""
    first = classifications[0] or {}
    segment = (first.get("segment") or {}).get("name") or ""
    genre = (first.get("genre") or {}).get("name") or ""
    sub_genre = (first.get("subGenre") or {}).get("name") or ""
    return segment, genre, sub_genre


def _parse_event(event: dict) -> dict:
    name = text._safe_text(event.get("name"), 120)
    event_venues = ((event.get("_embedded") or {}).get("venues")) or []
    venue_name_raw = event_venues[0].get("name") if event_venues else None
    venue_name = text._safe_text(venue_name_raw, 80) if venue_name_raw else None
    venue_key = venues.normalize_venue_name(venue_name_raw)

    start_iso = _event_start_iso(event)
    estimated_end_iso = None
    end_estimate_basis = None
    if start_iso:
        try:
            start_dt = datetime.strptime(start_iso, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        except ValueError:
            start_dt = None
        if start_dt is not None:
            duration, basis = venues.estimate_event_duration(*_classification_strings(event))
            estimated_end_iso = (start_dt + duration).strftime("%Y-%m-%dT%H:%M:%SZ")
            end_estimate_basis = basis

    return {
        "name": name,
        "venue_name": venue_name,
        "venue_key": venue_key,
        "start_iso": start_iso,
        "estimated_end_iso": estimated_end_iso,
        "end_estimate_basis": end_estimate_basis,
    }


async def execute(tool_input: dict, ctx: ToolContext) -> ToolResult:
    query = str(tool_input.get("query") or "").strip()
    if not query:
        return ToolResult(ok=False, error="query is required")

    api_key = os.getenv("TICKETMASTER_API_KEY")
    if not api_key:
        return ToolResult(ok=False, error="event lookup is not configured")

    date = tool_input.get("date")
    venue = tool_input.get("venue")

    cache_key = _cache_key(query, date, venue)
    cached = _read_cache(cache_key)
    if cached is not None:
        return ToolResult(ok=True, data=cached.get("data"), summary=cached.get("summary") or "")

    params = {
        "apikey": api_key,
        "keyword": f"{query} {venue}".strip() if venue else query,
        "dmaId": TICKETMASTER_DMA_ID,
        "size": str(EVENT_LOOKUP_SIZE),
        "sort": "date,asc",
    }
    if date:
        bounds = _et_day_bounds_utc(str(date))
        if bounds is None:
            return ToolResult(ok=False, error="date must be YYYY-MM-DD")
        params["startDateTime"], params["endDateTime"] = bounds

    payload, error = await fetch_json(
        "GET",
        TICKETMASTER_EVENTS_URL,
        timeout_s=EVENT_LOOKUP_TIMEOUT_S,
        log_tag="agent-event_lookup",
        what="event lookup",
        params=params,
    )
    if error:
        return ToolResult(ok=False, error=error)

    try:
        raw_events = ((payload or {}).get("_embedded") or {}).get("events") or []
        parsed_events = [_parse_event(event) for event in raw_events[:EVENT_LOOKUP_SIZE]]
    except (KeyError, TypeError, AttributeError) as exc:
        print(f"[agent-event_lookup] malformed Ticketmaster response: {exc!r}")
        return ToolResult(ok=False, error="event lookup returned an unexpected response")

    data: dict = {"events": parsed_events}
    if any(event.get("estimated_end_iso") for event in parsed_events):
        data["note"] = "end times are estimates based on typical event length, not an official schedule"

    summary = (
        f"found {len(parsed_events)} event(s) for '{query}'"
        if parsed_events
        else f"no events found for '{query}'"
    )

    cache.cache_set(cache_key, json.dumps({"data": data, "summary": summary}, default=str), EVENT_LOOKUP_CACHE_TTL_S)
    return ToolResult(ok=True, data=data, summary=summary)
