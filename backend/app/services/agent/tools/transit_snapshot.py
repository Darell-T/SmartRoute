"""transit_snapshot tool: current NYC transit conditions, no route planning.

Two modes: near a point (nearest stops + live arrivals + alerts, via the
same snapshot builder the live-feed map uses) or by line (service alerts
filtered to specific route ids, no location needed). Everything returned to
the model goes through text._safe_text caps -- alert/POI/social text is
untrusted per the system prompt's injection-defense clause.
"""

from __future__ import annotations

import asyncio

from app.routers.live_feed import _build_live_snapshot
from app.services import mta_feed
from app.services.agent.tools._types import ToolContext, ToolResult
from app.services.trips import text
from app.utils import geo

ARRIVAL_LIMIT = 8
ALERT_LIMIT = 5

TRANSIT_SNAPSHOT_SCHEMA = {
    "name": "transit_snapshot",
    "description": (
        "Check current NYC transit conditions: nearby stops/arrivals/alerts "
        "near a point, or service alerts for specific lines. Use this before "
        "promising a departure time, or when the rider asks about delays, "
        "crowding, or conditions 'right now'."
    ),
    "strict": True,
    "input_schema": {
        "type": "object",
        "properties": {
            "near": {
                "type": "string",
                "description": (
                    "'user' for the rider's GPS location, an NYC address, or "
                    "'lat,lng'. Returns nearby stops, live arrivals, and alerts."
                ),
            },
            "lines": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Line/route ids to check for alerts, e.g. [\"Q\",\"B\"]. Used when 'near' is omitted.",
            },
        },
        "required": [],
        "additionalProperties": False,
    },
}


async def _resolve_near(near_raw: str, ctx: ToolContext) -> tuple[tuple[float, float] | None, str | None]:
    value = (near_raw or "").strip()
    if value.lower() == "user":
        origin = ctx.origin or {}
        lat, lng = origin.get("lat"), origin.get("lng")
        if lat is not None and lng is not None:
            return (float(lat), float(lng)), None
        return None, "I need your location to check nearby conditions -- share GPS or give me a station name."
    return await asyncio.to_thread(geo.geocode_address_with_reason, value)


def _safe_alert(alert: dict) -> dict:
    return {
        "header": text._safe_text(alert.get("header"), 200),
        "route_ids": [str(r) for r in (alert.get("route_ids") or [])][:6],
    }


def _safe_arrival(arrival: dict) -> dict:
    return {
        "route_id": text._safe_text(arrival.get("route_id"), 12),
        "station_name": text._safe_text(arrival.get("station_name") or arrival.get("parent_stop_name"), 80),
        "arrival_time": arrival.get("arrival_time"),
    }


def _safe_stop(stop: dict | None) -> dict | None:
    if not stop:
        return None
    return {
        "stop_name": text._safe_text(stop.get("stop_name"), 80),
        "distance_m": stop.get("distance_m"),
    }


async def execute(tool_input: dict, ctx: ToolContext) -> ToolResult:
    near_raw = str(tool_input.get("near") or "").strip()
    lines = [str(line).strip().upper() for line in (tool_input.get("lines") or []) if str(line).strip()]

    if near_raw:
        coords, error = await _resolve_near(near_raw, ctx)
        if coords is None:
            return ToolResult(ok=False, error=error or "could not resolve that location")
        if ctx.gtfs is None:
            return ToolResult(ok=False, error="live transit data is not ready yet")
        snapshot = await _build_live_snapshot(ctx.gtfs, coords[0], coords[1])
        arrivals = snapshot.get("arrivals") or []
        alerts = snapshot.get("alerts") or []
        data = {
            "nearest_stop": _safe_stop(snapshot.get("nearest_stop")),
            "arrivals": [_safe_arrival(a) for a in arrivals[:ARRIVAL_LIMIT]],
            "alerts": [_safe_alert(a) for a in alerts[:ALERT_LIMIT]],
            "network_status": (snapshot.get("signals") or {}).get("network_status"),
        }
        summary = f"{len(arrivals)} arrival(s), {len(alerts)} alert(s) near {text._safe_text(near_raw, 60)}"
        return ToolResult(ok=True, data=data, summary=summary)

    raw_alerts = await mta_feed.fetch_service_alerts()
    parsed = mta_feed.parse_service_alerts(raw_alerts) if raw_alerts else []
    filtered = mta_feed.filter_alerts_for_routes(parsed, set(lines)) if lines else parsed
    data = {"alerts": [_safe_alert(a) for a in filtered[:ALERT_LIMIT]]}
    summary = f"{len(filtered)} alert(s)" + (f" for {'/'.join(lines)}" if lines else "")
    return ToolResult(ok=True, data=data, summary=summary)
