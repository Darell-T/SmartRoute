"""On-demand route incident scanning for route-advisor context.

Trip planning uses Grok/X search to check every station the rider could pass
through across all Google route candidates. The result is sent to the route
advisor before it chooses a route. This module intentionally does not run a
detached background scan; if the scan times out or fails, trip planning simply
continues with an empty incident list.
"""

import asyncio
import os
import re
from typing import Any

from app.services.incident_monitor import get_incidents
from app.services.trips import text


TRIP_INCIDENT_SCAN_TIMEOUT_S = float(os.getenv("TRIP_INCIDENT_SCAN_TIMEOUT_S", "25.0"))
_ALLOWED_SEVERITIES = {"low", "medium", "high", "critical"}


async def _scan_route_incidents(stops: list[str]) -> list[dict]:
    if not stops:
        return []

    try:
        result = await asyncio.wait_for(
            get_incidents(stops),
            timeout=TRIP_INCIDENT_SCAN_TIMEOUT_S,
        )
    except asyncio.TimeoutError:
        print(f"[trip] incident scan timed out ({TRIP_INCIDENT_SCAN_TIMEOUT_S:.0f}s)")
        return []
    except Exception as exc:
        print(f"[trip] incident scan failed: {exc!r}")
        return []

    incidents = result.get("incidents", []) if isinstance(result, dict) else []
    if not isinstance(incidents, list):
        return []
    return [_normalize_advisor_incident(incident) for incident in incidents if isinstance(incident, dict)]


def _station_key(value: object) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").strip().lower())


def _normalize_advisor_incident(incident: dict) -> dict:
    severity = str(incident.get("severity") or "medium").strip().lower()
    if severity not in _ALLOWED_SEVERITIES:
        severity = "medium"

    return {
        "location": text._safe_text(incident.get("location"), 100),
        "nearby_station": text._safe_text(
            incident.get("nearby_station")
            or incident.get("station")
            or incident.get("stop_name"),
            80,
        ),
        "severity": severity,
        "description": text._safe_text(incident.get("description"), 220),
        "source": text._safe_text(incident.get("source"), 60),
    }


def _scan_station_names(gtfs: Any, routes: list[list[dict]]) -> list[str]:
    """Return every station across all candidate routes.

    Board, alight, and intermediate stops are included. Intermediate stops
    resolve from the static pattern index when available; otherwise the scan
    falls back to endpoints so trip planning never touches the remote GTFS DB
    on this path.
    """
    seen: set[str] = set()
    names: list[str] = []

    def add(value: object) -> None:
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
