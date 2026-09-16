from __future__ import annotations

import json
import os

from app.services import cache, text
from app.services.agent.tools.base import ToolContext, ToolResult
from app.services.agent.tools.provider_http import fetch_json

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
        return _records_from_list(payload)
    if isinstance(payload, dict):
        return _records_from_mapping(payload)
    return []


def _records_from_list(value: object) -> list[dict]:
    if not isinstance(value, list):
        return []
    return [row for row in value if isinstance(row, dict)]


def _records_from_mapping(payload: dict) -> list[dict]:
    for key in _POSSIBLE_LIST_KEYS:
        value = payload.get(key)
        if isinstance(value, list):
            return _records_from_list(value)
    for value in payload.values():
        if isinstance(value, list):
            return _records_from_list(value)
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
    del ctx
    station_raw = str(tool_input.get("station") or "").strip()
    if not station_raw:
        return ToolResult(ok=False, error="station is required")

    borough_raw = str(tool_input.get("borough") or "").strip()
    query_norm = _normalize_station(station_raw)
    borough_norm = _normalize_station(borough_raw) if borough_raw else ""

    records = await _fetch_feed()
    if records is None:
        return ToolResult(ok=False, error="elevator status is temporarily unavailable")

    matched_raw = _matched_outages(records, query_norm, borough_norm)
    if not matched_raw:
        return ToolResult(
            ok=False,
            error=f"no accessibility record matched {station_raw}",
            outcome="unavailable",
        )
    return _accessibility_result(station_raw, matched_raw)


def _matched_outages(
    records: list[dict], query_norm: str, borough_norm: str
) -> list[dict]:
    return [
        raw
        for raw in records
        if _station_matches(_normalize_station(raw.get("station")), query_norm)
        and (not borough_norm or borough_norm in _normalize_station(raw.get("borough")))
    ]


def _accessibility_result(station_raw: str, matched_raw: list[dict]) -> ToolResult:
    elevator_outages = [
        {
            "equipment": text.safe_text(raw.get("equipment") or raw.get("equipmentno"), 20),
            "serving": text.safe_text(raw.get("serving"), 120),
            "estimated_return": text.safe_text(raw.get("estimatedreturntoservice"), 40),
        }
        for raw in matched_raw
        if _equipment_type(raw) == "EL"
    ]
    escalator_count = sum(1 for raw in matched_raw if _equipment_type(raw) == "ES")
    station_matched = text.safe_text(station_raw, 80)
    data = {
        "station_matched": station_matched,
        "elevator_outages": elevator_outages,
        "escalator_outages_count": escalator_count,
        "checked_at_note": "reflects current MTA-reported elevator/escalator outages, not real-time equipment status",
    }
    summary = (
        f"{len(elevator_outages)} elevator outage(s) reported at {station_matched}"
        if elevator_outages
        else f"no elevator outages reported at {station_matched}"
    )
    if escalator_count:
        summary += f"; {escalator_count} escalator outage(s) also reported"
    return ToolResult(ok=True, data=data, summary=summary)
