"""Bounded current-condition evidence for one rider-named NYC area.

This is intentionally not a second route planner. It resolves one specific
area, scopes incident evidence to nearby actual GTFS stops, and reads the
shared background incident index (never a request-time provider scan). The
existing Grok X/web crowd-search boundary still supplies nearby crowd-driving
events. The two kinds of evidence stay separate so one cannot imply the other.
"""

from __future__ import annotations

import asyncio
import hashlib
import re
from datetime import datetime, timezone
from typing import Any, Mapping
from zoneinfo import ZoneInfo

from app.services.agent.tools.location_resolution import resolve_named_place
from app.services.agent.tools._types import ToolContext, ToolResult
from app.services.trips import text
from app.services.trips.crowds import search as crowd_search
from app.services.trips.route_incidents import scan as trip_incidents
from app.services.trips.crowds.hotspots import HotspotHit
from app.services.trips.route_incidents.context import CandidateStopAssociation, CandidateStopContext
from app.services import geography as geo
from app.services.geography import find_nearest_stops

_NYC_TZ = ZoneInfo("America/New_York")
_MAX_NEARBY_STOPS = 5
_AREA_STOP_RADIUS_M = 2_000
_MAX_EVENTS = 5
_MAX_INCIDENTS = 5
_TRUTHFUL_INCIDENT_STATUSES = frozenset(
    {"complete", "partial", "stale", "unavailable", "unscanned", "failed"}
)
_LOOKUP_METADATA_KEYS = ("lookup_status", "coverage_status", "lookup_kind")
_BROAD_AREA_INPUTS = frozenset(
    {
        "nyc", "new york", "new york city", "the city", "city", "citywide",
        "all nyc", "all of nyc", "all new york city", "all of new york city",
        "manhattan", "manhattan ny", "all manhattan", "all of manhattan",
        "borough of manhattan", "the borough of manhattan",
        "brooklyn", "brooklyn ny", "all brooklyn", "all of brooklyn",
        "borough of brooklyn", "the borough of brooklyn",
        "queens", "queens ny", "all queens", "all of queens",
        "borough of queens", "the borough of queens",
        "bronx", "the bronx", "bronx ny", "all bronx", "all of bronx",
        "all the bronx", "all of the bronx", "borough of bronx", "the borough of bronx",
        "staten island", "staten island ny", "all staten island", "all of staten island",
        "borough of staten island", "the borough of staten island",
    }
)
_BROAD_AREA_MESSAGE = (
    "Please name a specific NYC station, neighborhood, or landmark; "
    "SmartRoute does not scan the whole city."
)
_OUTSIDE_NYC_KNOWN_PLACES = frozenset({"newark liberty international airport"})
_OUTSIDE_AREA_MESSAGE = "SmartRoute can check area conditions only within New York City."

AREA_CONDITIONS_SCHEMA = {
    "name": "check_area_conditions",
    "description": (
        "Check current reported incident and crowd-driving event evidence near one "
        "specific NYC station, neighborhood, or landmark. Returns incidents and "
        "events separately; it does not assess safety or plan a route. Use "
        "prepare_route_options for any directions request, then present_route."
    ),
    "strict": True,
    "input_schema": {
        "type": "object",
        "properties": {
            "area": {
                "type": "string",
                "description": "A specific NYC station, neighborhood, or landmark; never a borough or all of NYC.",
            },
            "at": {
                "type": "string",
                "description": "Optional timezone-aware RFC3339 time to scope nearby event evidence; omit for now.",
            },
        },
        "required": ["area"],
        "additionalProperties": False,
    },
}


def _area_key(value: str) -> str:
    return " ".join(re.sub(r"[^a-z0-9]+", " ", value.casefold()).split())


def _parse_at(value: object, ctx: ToolContext) -> tuple[datetime | None, str | None]:
    raw = str(value or "").strip() or str(ctx.now_et or "").strip()
    if raw:
        try:
            parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            if value:
                return None, "at must be an RFC3339 timestamp with a timezone offset"
        else:
            if parsed.tzinfo is None:
                return None, "at must be an RFC3339 timestamp with a timezone offset"
            return parsed, None
    return datetime.now(_NYC_TZ), None


def _coordinate_key(latitude: float, longitude: float) -> str:
    material = f"{latitude:.4f}:{longitude:.4f}"
    return "area-" + hashlib.sha256(material.encode("utf-8")).hexdigest()[:12]


def _is_nyc_area(name: str, latitude: float, longitude: float) -> bool:
    bounds = geo.NYC_BOUNDS
    in_bounds = (
        bounds["min_lat"] <= latitude <= bounds["max_lat"]
        and bounds["min_lon"] <= longitude <= bounds["max_lon"]
    )
    # EWR is a known destination for trip planning, but it is not an NYC area
    # for this local-conditions tool. The coarse NYC bounding box includes it.
    return in_bounds and _area_key(name) not in _OUTSIDE_NYC_KNOWN_PLACES


def _nearby_stop_context(
    *, latitude: float, longitude: float, gtfs: Any, area_key: str
) -> list[CandidateStopContext]:
    if gtfs is None:
        return []
    try:
        stops = find_nearest_stops(
            latitude,
            longitude,
            gtfs,
            limit=_MAX_NEARBY_STOPS,
            radius_m=_AREA_STOP_RADIUS_M,
        )
    except Exception:
        return []

    contexts: list[CandidateStopContext] = []
    for stop in stops:
        if not isinstance(stop, Mapping):
            continue
        name = text._safe_text(stop.get("stop_name"), 80)
        try:
            stop_latitude = float(stop.get("stop_lat"))
            stop_longitude = float(stop.get("stop_lon"))
        except (TypeError, ValueError):
            continue
        if not name or not (-90 <= stop_latitude <= 90 and -180 <= stop_longitude <= 180):
            continue
        contexts.append(
            CandidateStopContext(
                stop_id=text._safe_text(stop.get("stop_id"), 80) or None,
                stop_name=name,
                latitude=stop_latitude,
                longitude=stop_longitude,
                associations=[CandidateStopAssociation(candidate_route_id=area_key, mode="area")],
            )
        )
    return contexts


def _safe_events(value: object) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for row in value if isinstance(value, list) else []:
        if not isinstance(row, Mapping):
            continue
        events.append(
            {
                "name": text._safe_text(row.get("name"), 140),
                "category": text._safe_text(row.get("category"), 24),
                "venue_name": text._safe_text(row.get("venue_name"), 100),
                "start_iso": row.get("start_iso") if isinstance(row.get("start_iso"), str) else None,
                "estimated_end_iso": row.get("estimated_end_iso") if isinstance(row.get("estimated_end_iso"), str) else None,
                "source_class": text._safe_text(row.get("source_class"), 32),
                "verification_tier": text._safe_text(row.get("verification_tier"), 32),
            }
        )
        if len(events) >= _MAX_EVENTS:
            break
    return events


def _safe_incidents(value: object) -> list[dict[str, Any]]:
    incidents: list[dict[str, Any]] = []
    for row in value if isinstance(value, list) else []:
        if not isinstance(row, Mapping):
            continue
        severity = str(row.get("severity") or "medium").casefold()
        display: dict[str, Any] = {
            "location": text._safe_text(row.get("location") or row.get("location_name"), 100),
            "nearby_station": text._safe_text(row.get("nearby_station"), 80),
            "severity": severity if severity in {"low", "medium", "high", "critical"} else "medium",
            "description": text._safe_text(row.get("description"), 220),
            "source": text._safe_text(row.get("source"), 60),
        }
        state = str(row.get("state") or "").casefold()
        if state in {"unconfirmed", "confirmed", "rejected", "refreshing", "stale", "resolved"}:
            display["state"] = state
        if isinstance(row.get("corroborated"), bool):
            display["corroborated"] = row["corroborated"]
        incidents.append(display)
        if len(incidents) >= _MAX_INCIDENTS:
            break
    return incidents


def _display_incidents(value: object) -> list[dict[str, Any]]:
    """Deduplicate index incidents and nearby warnings for rider display."""
    merged: dict[str, dict[str, Any]] = {}
    for row in value if isinstance(value, list) else []:
        if not isinstance(row, Mapping):
            continue
        incident_id = text._safe_text(row.get("incident_id"), 120)
        key = incident_id or text._safe_text(
            row.get("location") or row.get("location_name"), 100
        )
        if key and key not in merged:
            merged[key] = dict(row)
    return _safe_incidents(list(merged.values()))


def _safe_sources(value: object) -> dict[str, list[str]] | None:
    if not isinstance(value, Mapping):
        return None
    result: dict[str, list[str]] = {}
    for key in ("completed", "errors"):
        values = value.get(key)
        if isinstance(values, list):
            result[key] = [
                text._safe_text(item, 80) for item in values[:6] if text._safe_text(item, 80)
            ]
    return result or None


def _incident_evidence(value: object) -> dict[str, Any]:
    """Project a bounded, payload-free summary of an index lookup."""
    metadata = value.get("scan_metadata") if isinstance(value, Mapping) else None
    metadata = metadata if isinstance(metadata, Mapping) else {}
    status = str(metadata.get("status") or "failed")
    evidence: dict[str, Any] = {
        "status": status if status in _TRUTHFUL_INCIDENT_STATUSES else "failed",
    }
    if isinstance(metadata.get("scanned_at"), str):
        evidence["scanned_at"] = metadata["scanned_at"]
    if isinstance(metadata.get("cache_hit"), bool):
        evidence["cache_hit"] = metadata["cache_hit"]
    for key in _LOOKUP_METADATA_KEYS:
        lookup_value = metadata.get(key)
        if isinstance(lookup_value, str) and lookup_value:
            evidence[key] = lookup_value
    if isinstance(metadata.get("warning_count"), int):
        evidence["warning_count"] = metadata["warning_count"]
    requested = metadata.get("requested_coverage_ids")
    if isinstance(requested, list):
        evidence["requested_coverage_ids"] = [
            text._safe_text(item, 120)
            for item in requested[:16]
            if text._safe_text(item, 120)
        ]
    sources = _safe_sources(metadata.get("sources"))
    if sources is not None:
        evidence["sources"] = sources
    return evidence


def _event_evidence(value: object, *, travel_at: datetime) -> dict[str, Any]:
    result = value if isinstance(value, Mapping) else {}
    status = str(result.get("status") or "unavailable")
    evidence: dict[str, Any] = {
        "status": status if status in {"complete", "partial", "unavailable"} else "unavailable",
        "travel_at": travel_at.isoformat(),
        "checked_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    }
    if isinstance(result.get("cache_hit"), bool):
        evidence["cache_hit"] = result["cache_hit"]
    completed = result.get("completed_sources")
    if isinstance(completed, list):
        evidence["completed_sources"] = [
            text._safe_text(item, 40) for item in completed[:4] if text._safe_text(item, 40)
        ]
    return evidence


async def execute(tool_input: dict, ctx: ToolContext) -> ToolResult:
    area_raw = str(tool_input.get("area") or "").strip()
    if not area_raw:
        return ToolResult(ok=False, error="area is required")
    if _area_key(area_raw) in _BROAD_AREA_INPUTS:
        return ToolResult(ok=False, error=_BROAD_AREA_MESSAGE)

    travel_at, time_error = _parse_at(tool_input.get("at"), ctx)
    if time_error is not None:
        return ToolResult(ok=False, error=time_error)
    assert travel_at is not None
    place, resolution_error = await resolve_named_place(
        area_raw,
        ctx,
        missing_location_message="Name a specific NYC station, neighborhood, or landmark to check conditions.",
    )
    if place is None:
        return ToolResult(ok=False, error=resolution_error or "could not resolve that NYC area")

    area_name = text._safe_text(place.name, 100) or text._safe_text(area_raw, 100)
    if not _is_nyc_area(area_name, place.latitude, place.longitude):
        return ToolResult(ok=False, error=_OUTSIDE_AREA_MESSAGE)
    area_key = _coordinate_key(place.latitude, place.longitude)
    stop_context = _nearby_stop_context(
        latitude=place.latitude, longitude=place.longitude, gtfs=ctx.gtfs, area_key=area_key
    )
    hotspot = HotspotHit(
        route_index=0,
        hotspot_key=area_key,
        hotspot_name=area_name,
        station_name=(stop_context[0].stop_name if stop_context else None) or area_name,
        latitude=place.latitude,
        longitude=place.longitude,
        expected_at=travel_at,
        route_id="",
    )
    incident_task = (
        asyncio.create_task(trip_incidents.scan_route_incidents(stop_context, travel_at=travel_at))
        if stop_context
        else None
    )
    event_task = asyncio.create_task(
        crowd_search.search_hotspots([hotspot], travel_at=travel_at, allow_live_search=True)
    )
    if incident_task is None:
        incident_result: object = {
            "incidents": [],
            "warnings": [],
            "scan_metadata": {
                "status": "unscanned",
                "lookup_status": "unscanned",
                "coverage_status": "unscanned",
                "lookup_kind": "index",
                "requested_coverage_ids": [],
                "warning_count": 0,
                "cache_hit": False,
                "sources": {"attempted": ["incident_index"], "completed": []},
            },
        }
        try:
            event_result: object = await event_task
        except Exception as error:
            event_result = error
    else:
        incident_result, event_result = await asyncio.gather(
            incident_task,
            event_task,
            return_exceptions=True,
        )
    if isinstance(incident_result, BaseException):
        incident_result = {"incidents": [], "scan_metadata": {"status": "failed"}}
    if isinstance(event_result, BaseException):
        event_result = {"status": "unavailable", "events": [], "completed_sources": []}

    incident_rows = incident_result.get("incidents") if isinstance(incident_result, Mapping) else None
    warning_rows = incident_result.get("warnings") if isinstance(incident_result, Mapping) else None

    return ToolResult(
        ok=True,
        data={
            "area": area_name,
            "incidents": _display_incidents(
                [
                    *(incident_rows if isinstance(incident_rows, list) else []),
                    *(warning_rows if isinstance(warning_rows, list) else []),
                ]
            ),
            "events": _safe_events(event_result.get("events") if isinstance(event_result, Mapping) else []),
            "incident_evidence": _incident_evidence(incident_result),
            "event_evidence": _event_evidence(event_result, travel_at=travel_at),
        },
        summary=(
            f"checked reported conditions near {area_name}"
            if stop_context
            else f"checked event evidence near {area_name}; nearby transit stop context is unavailable"
        ),
    )
