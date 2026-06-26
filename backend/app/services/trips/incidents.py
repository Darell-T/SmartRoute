"""Off-hot-path incident scanning + route incident markers.

The background scan (`_launch_incident_scan` / `_refresh_incidents_bg`) is
single-flight and fire-and-forget: a trip never awaits Grok, it serves the most
recent cached result from `_LAST_INCIDENTS` and kicks off a refresh for next
time. The marker builders turn cached incidents into map markers anchored on the
chosen route's stops. Depends on ``scoring`` (`_step_route_id`) and ``text``.
"""

import asyncio
import os
import re

from app.services.incident_monitor import get_incidents
from app.services.trips import scoring, text

# Incident scan (Grok + X-search) is far slower than any trip budget, so it runs
# OFF the hot path: a single-flight background task with its own generous
# timeout, caching its last result for trips to read instantly.
TRIP_INCIDENT_SCAN_TIMEOUT_S = float(os.getenv("TRIP_INCIDENT_SCAN_TIMEOUT_S", "25.0"))

_LAST_INCIDENTS: list[dict] = []
_INCIDENT_SCAN_INFLIGHT = False
_INCIDENT_BG_TASKS: set = set()


async def _refresh_incidents_bg(stops: list[str]):
    """Best-effort background incident scan. Never blocks a trip response; caches
    its result in _LAST_INCIDENTS for subsequent trips to serve."""
    global _INCIDENT_SCAN_INFLIGHT, _LAST_INCIDENTS
    try:
        result = await asyncio.wait_for(
            get_incidents(stops), timeout=TRIP_INCIDENT_SCAN_TIMEOUT_S
        )
        incidents = result.get("incidents", []) if isinstance(result, dict) else []
        _LAST_INCIDENTS = incidents if isinstance(incidents, list) else []
        print(f"[trip] background incident scan: {len(_LAST_INCIDENTS)} incident(s)")
    except asyncio.TimeoutError:
        print(f"[trip] background incident scan timed out ({TRIP_INCIDENT_SCAN_TIMEOUT_S:.0f}s)")
    except Exception as exc:
        print(f"[trip] background incident scan failed: {exc!r}")
    finally:
        _INCIDENT_SCAN_INFLIGHT = False


def _launch_incident_scan(stops: list[str]) -> bool:
    """Fire-and-forget, single-flight incident refresh. Returns True when a scan
    is in progress, so the trip response can flag incidents as pending."""
    global _INCIDENT_SCAN_INFLIGHT
    if not stops:
        return False
    if _INCIDENT_SCAN_INFLIGHT:
        return True
    _INCIDENT_SCAN_INFLIGHT = True
    try:
        task = asyncio.create_task(_refresh_incidents_bg(stops))
    except Exception:
        _INCIDENT_SCAN_INFLIGHT = False
        return False
    # Keep a strong reference so the task is not garbage-collected mid-flight.
    _INCIDENT_BG_TASKS.add(task)
    task.add_done_callback(_INCIDENT_BG_TASKS.discard)
    return True


def _station_key(value: object) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").strip().lower())

def _coords_from_stop(value: object) -> tuple[float, float] | None:
    if not isinstance(value, dict):
        return None
    lat = value.get("lat", value.get("latitude"))
    lng = value.get("lng", value.get("longitude"))
    if not isinstance(lat, (int, float)) or not isinstance(lng, (int, float)):
        return None
    return float(lat), float(lng)

def _route_stop_index(route: list[dict]) -> dict[str, dict]:
    stops: dict[str, dict] = {}

    def add_stop(name: object, coords: tuple[float, float] | None, route_id: str) -> None:
        key = _station_key(name)
        if not key:
            return
        row = stops.setdefault(
            key,
            {
                "name": text._safe_text(name, 80),
                "lat": None,
                "lng": None,
                "route_ids": set(),
            },
        )
        if coords and row["lat"] is None and row["lng"] is None:
            row["lat"], row["lng"] = coords
        if route_id:
            row["route_ids"].add(route_id)

    for step in route or []:
        route_id = scoring._step_route_id(step)
        if step.get("type") not in ("SUBWAY", "BUS"):
            continue
        add_stop(step.get("departure_stop"), _coords_from_stop(step.get("departure_coords")), route_id)
        add_stop(step.get("arrival_stop"), _coords_from_stop(step.get("arrival_coords")), route_id)
        for stop in step.get("intermediate_stop_locations") or []:
            if not isinstance(stop, dict):
                continue
            add_stop(stop.get("name"), _coords_from_stop(stop), route_id)

    return stops

def _build_route_incident_markers(incidents: list[dict], chosen_route: list[dict]) -> list[dict]:
    stop_index = _route_stop_index(chosen_route)
    markers: list[dict] = []
    allowed_severities = {"low", "medium", "high", "critical"}

    for incident in incidents or []:
        if not isinstance(incident, dict):
            continue
        station_name = (
            incident.get("nearby_station")
            or incident.get("station")
            or incident.get("stop_name")
        )
        stop = stop_index.get(_station_key(station_name))
        if not stop or stop.get("lat") is None or stop.get("lng") is None:
            continue

        severity = str(incident.get("severity") or "medium").strip().lower()
        if severity not in allowed_severities:
            severity = "medium"
        detail_parts = [
            text._safe_text(incident.get("location"), 80),
            text._safe_text(incident.get("source"), 40),
        ]
        detail = " · ".join(part for part in detail_parts if part)
        description = text._safe_text(incident.get("description"), 180)

        markers.append(
            {
                "id": f"route-incident-{len(markers)}",
                "type": "incident",
                "lat": stop["lat"],
                "lng": stop["lng"],
                "title": description or "Incident reported near this route.",
                "detail": detail,
                "severity": severity,
                "source": text._safe_text(incident.get("source"), 40),
                "station": stop["name"],
                "routeIds": sorted(stop["route_ids"]),
                "active": severity in ("high", "critical"),
            }
        )

    return markers

def _scan_station_names(gtfs, routes) -> list[str]:
    """Every station the rider could encounter across all candidate routes --
    board, alight, AND intermediate stops -- deduped by normalized name. This is
    the full set ATLAS scans for incidents, so it watches the whole journey, not
    just the endpoints. Intermediate stops resolve straight from the in-memory
    static pattern index (always instant); if that index is absent we fall back
    to endpoints only rather than ever touching the remote DB on this path."""
    seen: set[str] = set()
    names: list[str] = []

    def add(value):
        key = _station_key(value)
        if key and key not in seen:
            seen.add(key)
            names.append(text._safe_text(value, 80))

    index = getattr(gtfs, "_pattern_index", None) if gtfs else None
    for route in routes or []:
        for step in route:
            if step.get("type") not in ("SUBWAY", "BUS"):
                continue
            add(step.get("departure_stop"))
            add(step.get("arrival_stop"))
            if step.get("type") == "SUBWAY" and index and step.get("route_id"):
                try:
                    rows, _meta = index.get_intermediate_stops_with_coords(
                        step["route_id"],
                        step.get("departure_stop"),
                        step.get("arrival_stop"),
                        step.get("departure_coords"),
                        step.get("arrival_coords"),
                    )
                except Exception:
                    rows = []
                for row in rows or []:
                    add(row.get("name"))
    return names
