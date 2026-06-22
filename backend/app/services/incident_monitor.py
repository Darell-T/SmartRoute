import asyncio
import json
import os
from typing import Any

try:
    from xai_sdk import Client
    from xai_sdk.chat import system, user
    from xai_sdk.tools import x_search
except Exception:
    Client = None
    system = None
    user = None
    x_search = None


_ALLOWED_SEVERITIES = {"low", "medium", "high"}
_MODEL_NAME = "grok-4-1-fast-reasoning"
_XAI_API_KEY = os.getenv("XAI_API_KEY")
_STATION_TOKEN_MAP = {
    "av": "avenue",
    "ave": "avenue",
    "blvd": "boulevard",
    "bklyn": "brooklyn",
    "ctr": "center",
    "ct": "center",
    "hwy": "highway",
    "pkwy": "parkway",
    "pl": "place",
    "plz": "plaza",
    "rd": "road",
    "sq": "square",
    "st": "street",
}


client = Client(api_key=_XAI_API_KEY) if Client is not None and _XAI_API_KEY else None

GROK_INCIDENT_PROMPT = """You are an NYC incident scanner. Your job is to check
real-time posts on X (Twitter) for any incidents that could affect subway service.

Stations: {stations}

Rules:
- Only real incidents from the last 60 minutes within about 0.5 miles of any listed station.
- Focus on @NYCTSubway, @NYCrimeNow, @NYScanner, @CitizenAppNYC, rider complaints, and local news.
- If your first search is unclear or incomplete, call the search tool again with a better query.
- Output ONLY this exact JSON. No other text.

{{
  "incidents": [
    {{
      "location": "address or cross-street",
      "nearby_station": "exact station name",
      "severity": "low | medium | high",
      "description": "one sentence plain English summary",
      "source": "@handle or source name"
    }}
  ]
}}

If nothing relevant is found, return {{"incidents": []}}.
Only report real, specific incidents from actual posts. If you are unsure, leave it out."""


def _normalize_text_field(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    return " ".join(value.split()).strip()


def _station_key(value: Any) -> str:
    text = _normalize_text_field(value).casefold()
    if not text:
        return ""

    translation = str.maketrans({
        "&": " ",
        ",": " ",
        "-": " ",
        "/": " ",
    })
    tokens = text.translate(translation).replace(".", " ").split()
    normalized_tokens = [
        _STATION_TOKEN_MAP.get(token, token)
        for token in tokens
        if token
    ]
    return " ".join(normalized_tokens)


def _normalize_station_names(route_stops: list[str]) -> list[str]:
    normalized = []
    seen = set()
    for stop in route_stops or []:
        name = _normalize_text_field(stop)
        if not name:
            continue
        key = _station_key(name)
        if key in seen:
            continue
        seen.add(key)
        normalized.append(name)
    return normalized


def _strip_code_fences(content: str) -> str:
    text = (content or "").strip()
    if text.startswith("```json"):
        text = text[7:]
    elif text.startswith("```"):
        text = text[3:]
    if text.endswith("```"):
        text = text[:-3]
    return text.strip()


def _response_text(response: Any) -> str:
    content = getattr(response, "content", "")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        chunks: list[str] = []
        for item in content:
            if isinstance(item, str):
                chunks.append(item)
            elif isinstance(item, dict):
                text = item.get("text")
                if isinstance(text, str):
                    chunks.append(text)
            else:
                text = getattr(item, "text", None)
                if isinstance(text, str):
                    chunks.append(text)
        return "".join(chunks)
    return str(content or "")


def _parse_json_object(content: str) -> dict[str, Any] | None:
    text = _strip_code_fences(content)
    if not text:
        return None
    try:
        parsed = json.loads(text)
        return parsed if isinstance(parsed, dict) else None
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end == -1 or end <= start:
            return None
        try:
            parsed = json.loads(text[start : end + 1])
            return parsed if isinstance(parsed, dict) else None
        except json.JSONDecodeError:
            return None


def _normalize_incident(item: Any) -> dict[str, str] | None:
    if not isinstance(item, dict):
        return None

    location = _normalize_text_field(item.get("location"))
    nearby_station = _normalize_text_field(item.get("nearby_station"))
    severity = _normalize_text_field(item.get("severity")).lower()
    description = _normalize_text_field(item.get("description"))
    source = _normalize_text_field(item.get("source"))

    if not location or not nearby_station or not description or not source:
        return None
    if severity not in _ALLOWED_SEVERITIES:
        return None

    return {
        "location": location,
        "nearby_station": nearby_station,
        "severity": severity,
        "description": description,
        "source": source,
    }


def _normalize_incident_payload(
    payload: Any,
    allowed_stations: list[str] | None = None,
) -> dict[str, list[dict[str, str]]]:
    if not isinstance(payload, dict):
        return {"incidents": []}

    incidents = payload.get("incidents")
    if not isinstance(incidents, list):
        return {"incidents": []}

    station_map = {
        _station_key(station): station
        for station in (allowed_stations or [])
        if station
    }
    normalized = []
    for incident in incidents:
        normalized_incident = _normalize_incident(incident)
        if normalized_incident is None:
            continue
        if station_map:
            canonical_station = station_map.get(_station_key(normalized_incident["nearby_station"]))
            if not canonical_station:
                continue
            normalized_incident["nearby_station"] = canonical_station
        normalized.append(normalized_incident)

    return {"incidents": normalized}


def _run_incident_agent(
    station_names: str,
    station_list: list[str],
) -> dict[str, list[dict[str, str]]]:
    if not client or not system or not user or not x_search or not _XAI_API_KEY:
        return {"incidents": []}

    chat = client.chat.create(
        model=_MODEL_NAME,
        tools=[x_search()],
        temperature=0.0,
    )
    chat.append(system(GROK_INCIDENT_PROMPT.format(stations=station_names)))
    chat.append(user(f"Find any incidents near these stations right now: {station_names}"))

    for _ in range(3):
        response = chat.sample()
        if getattr(response, "finish_reason", None) == "tool_calls":
            chat.append(response)
            continue

        payload = _parse_json_object(_response_text(response))
        if payload is None:
            return {"incidents": []}
        return _normalize_incident_payload(payload, station_list)

    return {"incidents": []}


async def get_incidents(route_stops: list[str]) -> dict[str, list[dict[str, str]]]:
    station_list = _normalize_station_names(route_stops)
    if not station_list:
        return {"incidents": []}

    station_names = ", ".join(station_list)

    try:
        return await asyncio.to_thread(_run_incident_agent, station_names, station_list)
    except Exception as exc:
        print(f"[incident_monitor] Grok agent failed: {exc}")
        return {"incidents": []}
