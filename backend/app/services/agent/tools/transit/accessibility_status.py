"""accessibility_status tool: wraps the MTA's public elevator/escalator
outage feed so the model can ground "does this station have a working
elevator" answers instead of guessing -- this is the real answer to the
"heading to Costco, I've got a cart" demo query's accessibility half.

MTA moves data-service endpoints occasionally, so the feed URL is an
env-overridable module constant (`MTA_ENE_URL`), the same pattern
`app/services/mta/config.py` uses for its GTFS-RT feed hosts. Fail-open: any
fetch/parse problem returns `ok=False` with a short rider-facing reason,
never a traceback -- an outage feed being briefly unavailable should not
crash a trip-planning turn.

The full feed (every currently reported outage, before per-station
filtering) is cached via `utils/cache.py` for 120s under key
`agent:ene:feed`; each call re-filters the cached list for the requested
station instead of re-fetching.
"""

from __future__ import annotations

import json
import os

from app.services.agent.tools.provider_http import fetch_json
from app.services.agent.tools._types import ToolContext, ToolResult
from app.services.trips import text
from app.services import cache

# The MTA's current elevator/escalator outage feed -- same api-endpoint.mta.info
# data-service host as the GTFS-RT feeds in app/services/mta/config.py, no API
# key required. Overridable because MTA has moved these endpoints before.
MTA_ENE_URL = os.getenv(
    "MTA_ENE_URL",
    "https://api-endpoint.mta.info/Dataservice/mtagtfsfeeds/nyct%2Fnyct_ene.json",
)
ACCESSIBILITY_STATUS_TIMEOUT_S = float(os.getenv("ACCESSIBILITY_STATUS_TIMEOUT_S", "6.0"))
ENE_CACHE_TTL_S = 120
ENE_CACHE_KEY = "agent:ene:feed"

# The response shape isn't formally documented and MTA has changed the
# wrapping key before -- try the plausible ones, then fall back to the
# first list-valued entry in the payload, rather than pinning one key name.
_POSSIBLE_LIST_KEYS = ("outages", "eeoutages", "nyct_ene", "nyct_ene_equipments", "equipments", "results", "data")

_STATION_TOKEN_MAP = {
    "st": "street",
    "ave": "avenue",
    "av": "avenue",
    "sq": "square",
    "ctr": "center",
    "pkwy": "parkway",
    "blvd": "boulevard",
}

ACCESSIBILITY_STATUS_SCHEMA = {
    "name": "accessibility_status",
    "description": (
        "Check current MTA-reported elevator/escalator outages at a subway "
        "station. Call this before recommending a route with transfers to a "
        "rider traveling with a cart, stroller, or wheelchair."
    ),
    "strict": True,
    "input_schema": {
        "type": "object",
        "properties": {
            "station": {
                "type": "string",
                "description": "Station name to check, e.g. '34 St-Penn Station'.",
            },
        },
        "required": ["station"],
        "additionalProperties": False,
    },
}


def _normalize_station(value: object) -> str:
    """Loose local station-name normalizer.

    It casefolds, strips separators, and expands a few common abbreviations
    without coupling accessibility checks to incident-collection internals.
    """
    raw = " ".join(str(value or "").split()).strip().casefold()
    if not raw:
        return ""
    translation = str.maketrans({"&": " ", ",": " ", "-": " ", "/": " ", ".": " "})
    tokens = raw.translate(translation).split()
    normalized_tokens = [_STATION_TOKEN_MAP.get(token, token) for token in tokens]
    return " ".join(normalized_tokens)


def _station_matches(record_station_norm: str, query_norm: str) -> bool:
    if not record_station_norm or not query_norm:
        return False
    return query_norm in record_station_norm or record_station_norm in query_norm


def _extract_outage_records(payload) -> list[dict]:
    if isinstance(payload, list):
        return [r for r in payload if isinstance(r, dict)]
    if isinstance(payload, dict):
        for key in _POSSIBLE_LIST_KEYS:
            value = payload.get(key)
            if isinstance(value, list):
                return [r for r in value if isinstance(r, dict)]
        for value in payload.values():
            if isinstance(value, list):
                return [r for r in value if isinstance(r, dict)]
    return []


def _read_cached_feed() -> list[dict] | None:
    raw = cache.cache_get(ENE_CACHE_KEY)
    if raw is None:
        return None
    try:
        blob = raw.decode("utf-8") if isinstance(raw, (bytes, bytearray)) else raw
        parsed = json.loads(blob)
    except (ValueError, TypeError, UnicodeDecodeError):
        return None
    return parsed if isinstance(parsed, list) else None


async def _fetch_feed() -> list[dict] | None:
    """Returns the full list of raw outage records, from cache when fresh.
    `None` means the feed could not be fetched/parsed -- callers treat that
    as a fail-open `ok=False`, never a crash."""
    cached = _read_cached_feed()
    if cached is not None:
        return cached

    payload, error = await fetch_json(
        "GET",
        MTA_ENE_URL,
        timeout_s=ACCESSIBILITY_STATUS_TIMEOUT_S,
        log_tag="agent-accessibility_status",
        what="MTA ENE feed",
    )
    if error:
        return None

    records = _extract_outage_records(payload)
    cache.cache_set(ENE_CACHE_KEY, json.dumps(records, default=str), ENE_CACHE_TTL_S)
    return records


def _equipment_type(raw: dict) -> str:
    return str(raw.get("equipmenttype") or "").strip().upper()


async def execute(tool_input: dict, ctx: ToolContext) -> ToolResult:
    station_raw = str(tool_input.get("station") or "").strip()
    if not station_raw:
        return ToolResult(ok=False, error="station is required")

    borough_raw = str(tool_input.get("borough") or "").strip()
    query_norm = _normalize_station(station_raw)
    borough_norm = _normalize_station(borough_raw) if borough_raw else ""

    records = await _fetch_feed()
    if records is None:
        return ToolResult(ok=False, error="elevator status is temporarily unavailable")

    matched_raw = [
        raw
        for raw in records
        if _station_matches(_normalize_station(raw.get("station")), query_norm)
        and (not borough_norm or borough_norm in _normalize_station(raw.get("borough")))
    ]
    if not matched_raw:
        return ToolResult(
            ok=False,
            error=f"no accessibility record matched {station_raw}",
            outcome="unavailable",
        )
    # Only elevator outages carry detail into the digest; escalators are a
    # bare count, so there is no need to fully parse those records.
    elevator_outages = [
        {
            "equipment": text._safe_text(raw.get("equipment") or raw.get("equipmentno"), 20),
            "serving": text._safe_text(raw.get("serving"), 120),
            "estimated_return": text._safe_text(raw.get("estimatedreturntoservice"), 40),
        }
        for raw in matched_raw
        if _equipment_type(raw) == "EL"
    ]
    escalator_count = sum(1 for raw in matched_raw if _equipment_type(raw) == "ES")

    station_matched = text._safe_text(station_raw, 80)
    data = {
        "station_matched": station_matched,
        "elevator_outages": elevator_outages,
        "escalator_outages_count": escalator_count,
        "checked_at_note": "reflects current MTA-reported elevator/escalator outages, not real-time equipment status",
    }

    if elevator_outages:
        summary = f"{len(elevator_outages)} elevator outage(s) reported at {station_matched}"
    else:
        summary = f"no elevator outages reported at {station_matched}"
    if escalator_count:
        summary += f"; {escalator_count} escalator outage(s) also reported"

    return ToolResult(ok=True, data=data, summary=summary)
