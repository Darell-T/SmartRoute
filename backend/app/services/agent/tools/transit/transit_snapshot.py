"""transit_snapshot tool: current NYC transit conditions, no route planning.

Two modes: near a point (nearest stops + live arrivals + alerts, via the
same snapshot builder the live-feed map uses) or by line (service alerts
filtered to specific route ids, no location needed). Everything returned to
the model goes through text._safe_text caps -- alert/POI/social text is
untrusted per the system prompt's injection-defense clause.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

from app.services.agent.tools._types import ToolContext, ToolResult
from app.services.agent.tools.location_resolution import resolve_named_point
from app.services.agent.tools.transit import evidence as transit_evidence
from app.services.agent.tools.transit import (
    evidence_binding as transit_evidence_binding,
)
from app.services.incidents import index as incident_index
from app.services.live_feed.snapshot import build_live_snapshot as _build_live_snapshot
from app.services.mta import realtime as mta_realtime
from app.services.mta.alerts import project_service_alert
from app.services.trips import text

ARRIVAL_LIMIT = 8
ALERT_LIMIT = 5

TRANSIT_SNAPSHOT_SCHEMA = {
    "name": "transit_snapshot",
    "description": (
        "Use this to determine whether subway lines, bus routes, stations, or "
        "the rider's nearby transit are currently affected by delays, "
        "suspensions, reroutes, service changes, or other active conditions. "
        "Line mode needs no station. Nearby arrivals are supporting context, "
        "not a substitute for service-status evidence."
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


def _safe_alert(alert: dict) -> dict:
    source = (
        alert
        if alert.get("header") or not alert.get("title")
        else {**alert, "header": alert.get("title")}
    )
    result = project_service_alert(source) or {}
    if "header" in result:
        result["header"] = text._safe_text(result.get("header"), 200)
    direction = text._safe_text(
        alert.get("direction") or alert.get("direction_label"), 80
    )
    if direction:
        result["direction"] = direction
    return {key: value for key, value in result.items() if value not in (None, "", [])}


def _safe_arrival(arrival: dict) -> dict:
    return {
        "route_id": text._safe_text(arrival.get("route_id"), 12),
        "station_name": text._safe_text(arrival.get("station_name") or arrival.get("parent_stop_name"), 80),
        "arrival_time": arrival.get("arrival_time"),
    }


def _safe_stop(stop: dict | None) -> dict | None:
    if not stop:
        return None
    result = {
        "id": text._safe_text(stop.get("stop_id") or stop.get("id"), 40),
        "stop_name": text._safe_text(stop.get("stop_name") or stop.get("name"), 80),
        "distance_m": stop.get("distance_m"),
    }
    for output, source in (("latitude", "stop_lat"), ("longitude", "stop_lon")):
        if source in stop:
            result[output] = stop[source]
    return {key: value for key, value in result.items() if value not in (None, "")}


async def execute(tool_input: dict, ctx: ToolContext) -> ToolResult:
    near_raw = str(tool_input.get("near") or "").strip()
    lines = list(
        dict.fromkeys(
            str(line).strip().upper()
            for line in (tool_input.get("lines") or [])
            if str(line).strip()
        )
    )

    if near_raw:
        coords, error = await resolve_named_point(
            near_raw,
            ctx,
            missing_location_message="I need your location to check nearby conditions -- share GPS or give me a station name.",
        )
        if coords is None:
            return ToolResult(ok=False, error=error or "could not resolve that location")
        if ctx.gtfs is None:
            return ToolResult(ok=False, error="live transit data is not ready yet")
        snapshot = await _build_live_snapshot(ctx.gtfs, coords[0], coords[1])
        arrivals = snapshot.get("arrivals") or []
        alerts = snapshot.get("alerts") or []
        data = {
            "source": "smartroute_live_feed",
            "freshness": "live",
            "observed_at": datetime.now(UTC).isoformat(),
            "nearest_stop": _safe_stop(snapshot.get("nearest_stop")),
            "arrivals": [_safe_arrival(a) for a in arrivals[:ARRIVAL_LIMIT]],
            "alerts": [_safe_alert(a) for a in alerts[:ALERT_LIMIT]],
            "network_status": (snapshot.get("signals") or {}).get("network_status"),
        }
        summary = f"{len(arrivals)} arrival(s), {len(alerts)} alert(s) near {text._safe_text(near_raw, 60)}"
        return ToolResult(ok=True, data=data, summary=summary)

    alert_result = await mta_realtime.fetch_service_alerts(
        force_refresh=True,
        with_metadata=True,
    )
    if isinstance(alert_result, dict):
        raw_alerts = alert_result.get("content") or b""
        freshness = str(alert_result.get("freshness") or "unavailable")
        observed_at = alert_result.get("observed_at")
    else:
        raw_alerts = alert_result
        # A legacy bytes-only provider result has no trustworthy observation
        # metadata. Keep the payload usable, but never imply request-time data.
        freshness = "unknown" if raw_alerts else "unavailable"
        observed_at = None
    if not raw_alerts:
        return ToolResult(
            ok=False,
            data={
                "source": "mta_service_alerts",
                "freshness": freshness,
                "status": "unavailable",
                "requested_routes": lines,
                "affected_routes": [],
                "alerts": [],
                **({"observed_at": observed_at} if observed_at else {}),
            },
            error="current MTA service-alert data is unavailable",
        )
    parsed = mta_realtime.parse_service_alerts(raw_alerts)
    filtered = mta_realtime.filter_alerts_for_routes(parsed, set(lines)) if lines else parsed
    affected_routes = sorted(
        {
            str(route_id).strip().upper()
            for alert in filtered
            for route_id in (alert.get("route_ids") or [])
            if str(route_id).strip()
        }
    )
    data = {
        "source": "mta_service_alerts",
        "freshness": freshness,
        "status": "active_alerts" if filtered else "no_active_alerts",
        "requested_routes": lines,
        "affected_routes": affected_routes,
        "alerts": [_safe_alert(a) for a in filtered[:ALERT_LIMIT]],
        **({"observed_at": observed_at} if observed_at else {}),
    }
    summary = (
        f"{len(filtered)} active service alert(s)"
        + (f" for {'/'.join(lines)}" if lines else "")
    )
    return ToolResult(ok=True, data=data, summary=summary)


_SUBWAY_ROUTES = frozenset(
    str(item).strip().upper() for item in mta_realtime.ALL_SUBWAY_ROUTES
)


def _transit_source_calls(route_ids: list[str]) -> list[tuple[str, Any]]:
    subway_routes = set(route_ids) & _SUBWAY_ROUTES
    bus_routes = set(route_ids) - subway_routes
    calls = []
    if subway_routes:
        calls.append(("gtfs_rt", mta_realtime.get_stalled_trains(subway_routes)))
    if bus_routes:
        calls.append(("bustime", mta_realtime.get_stalled_buses(bus_routes)))
    return calls


def _signal_rows(
    source_name: str,
    source_result: object,
    route_ids: list[str],
) -> tuple[str, list[dict[str, Any]], bool]:
    if isinstance(source_result, Exception) or not isinstance(source_result, (list, tuple)):
        return "unavailable", [], False
    mode = "subway" if source_name == "gtfs_rt" else "bus"
    default_kind = "stalled_train" if mode == "subway" else "stalled_bus"
    rows = [
        {**item, "kind": str(item.get("kind") or default_kind), "mode": mode}
        for item in source_result
        if isinstance(item, dict)
        and str(item.get("route_id") or "").strip().upper() in route_ids
    ]
    coverage = "current" if source_name == "gtfs_rt" else "partial"
    return coverage, rows, True


def _latest_observed(rows: list[dict[str, Any]], mode: str) -> str:
    return max(
        (
            str(
                item.get("time_recorded")
                or item.get("observed_at")
                or item.get("updated_at")
            ).strip()
            for item in rows
            if item.get("mode") == mode
            and (
                item.get("time_recorded")
                or item.get("observed_at")
                or item.get("updated_at")
            )
        ),
        default="",
    )


async def _vehicle_evidence(route_ids: list[str]) -> tuple[dict[str, Any], bool]:
    source_calls = _transit_source_calls(route_ids)
    if not source_calls:
        return {}, False
    source_results = await asyncio.gather(
        *(call for _name, call in source_calls),
        return_exceptions=True,
    )
    data: dict[str, Any] = {}
    rows: list[dict[str, Any]] = []
    available = False
    for (source_name, _call), source_result in zip(
        source_calls, source_results, strict=True
    ):
        coverage, source_rows, source_available = _signal_rows(
            source_name, source_result, route_ids
        )
        data[f"{source_name}_coverage"] = coverage
        rows.extend(source_rows)
        available = available or source_available
    data["unconfirmed_signals"] = [
        transit_evidence.safe_unconfirmed_signal(item) for item in rows
    ]
    for source_name, mode in (("gtfs_rt", "subway"), ("bustime", "bus")):
        observed_at = _latest_observed(rows, mode)
        if observed_at:
            data[f"{source_name}_observed_at"] = observed_at
    return data, available


def _incident_evidence(route_ids: list[str]) -> tuple[dict[str, Any], bool]:
    try:
        incident_result = incident_index.lookup_incidents(route_ids=route_ids)
    except Exception:
        return {"incident_coverage": "unavailable"}, False
    if not isinstance(incident_result, dict):
        return (
            {"incident_coverage": "unavailable"}
            if incident_result is not None
            else {}
        ), False

    incidents = [
        item
        for item in incident_result.get("incidents") or []
        if isinstance(item, dict)
    ][:12]
    data: dict[str, Any] = {
        "incident_coverage": str(
            incident_result.get("coverage_status") or "unscanned"
        ),
        "incidents": incidents,
    }
    observed_at = max(
        (
            str(
                item.get("observed_at")
                or item.get("updated_at")
                or item.get("reported_at")
            ).strip()
            for item in incidents
            if item.get("observed_at")
            or item.get("updated_at")
            or item.get("reported_at")
        ),
        default="",
    )
    if observed_at:
        data["incident_observed_at"] = observed_at
    return data, True


def _status_payload(route_ids: list[str], result: ToolResult) -> dict[str, Any]:
    data = dict(result.data) if isinstance(result.data, dict) else {}
    if not result.ok:
        data.setdefault("source", "mta_service_alerts")
        data.setdefault("freshness", "unavailable")
        data.setdefault("status", "unavailable")
        data.setdefault("alerts", [])
    data.setdefault("requested_routes", route_ids)
    return data


async def collect_service_status(
    route_ids: list[str],
    fields: dict[str, str | None],
    ctx: ToolContext,
) -> ToolResult:
    """Gather alerts plus relevant vehicle and incident signals in parallel."""
    decision_binding = transit_evidence_binding.decision_evidence_for_status(
        route_ids,
        ctx.session,
        ctx.session_id,
    )
    if decision_binding.get("reused"):
        return ToolResult(
            ok=True,
            data=decision_binding.get("data") or {},
            summary="Reused current accepted route evidence",
        )
    payload: dict[str, Any] = {"lines": route_ids} if route_ids else {}
    if fields["area"]:
        payload["near"] = fields["area"]
    result = await execute(payload, ctx)
    if not route_ids:
        return result

    data = _status_payload(route_ids, result)
    vehicle_data, vehicle_available = await _vehicle_evidence(route_ids)
    incident_data, incident_available = _incident_evidence(route_ids)
    data.update(vehicle_data)
    data.update(incident_data)
    if result.ok:
        continuity = transit_evidence_binding.decision_alert_continuity(
            decision_binding,
            data.get("alerts"),
        )
        if continuity is not None:
            data["decision_evidence_continuity"] = continuity
    if result.ok:
        result.data = data
        return result
    if not (vehicle_available or incident_available):
        result.data = data
        return result
    return ToolResult(
        ok=True,
        data=data,
        summary="Checked current transit status with partial coverage",
        timings=dict(result.timings),
    )
