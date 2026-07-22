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
from typing import Any, Iterable

from app.services.incident_monitor import get_incidents
from app.services.trips import text
from app.services.trips.incident_association import verified_match_association
from app.services.trips.incident_context import CandidateStopContext, extract_candidate_stop_context
from app.services.trips.incident_merge import merge_incident_evidence


TRIP_INCIDENT_SCAN_TIMEOUT_S = float(os.getenv("TRIP_INCIDENT_SCAN_TIMEOUT_S", "25.0"))
_ALLOWED_SEVERITIES = {"low", "medium", "high", "critical"}


async def _scan_route_incidents_with_metadata(
    route_context: Iterable[object], *, snapshot_store: Any | None = None
) -> dict[str, Any]:
    """Return the scanner contract while keeping the advisor-facing list separate."""
    route_context = list(route_context or [])
    if not route_context:
        return {"incidents": [], "scan_metadata": {"status": "complete", "snapshot_status": "disabled"}}

    try:
        result = await asyncio.wait_for(
            get_incidents(route_context, snapshot_store=snapshot_store),
            timeout=TRIP_INCIDENT_SCAN_TIMEOUT_S,
        )
    except asyncio.TimeoutError:
        print(f"[trip] incident scan timed out ({TRIP_INCIDENT_SCAN_TIMEOUT_S:.0f}s)")
        return {"incidents": [], "scan_metadata": {"status": "failed", "snapshot_status": "unavailable"}}
    except Exception as exc:
        print(f"[trip] incident scan failed: {exc!r}")
        return {"incidents": [], "scan_metadata": {"status": "failed", "snapshot_status": "unavailable"}}

    incidents = result.get("incidents", []) if isinstance(result, dict) else []
    if not isinstance(incidents, list):
        incidents = []
    metadata = result.get("scan_metadata", {}) if isinstance(result, dict) else {}
    # The scanner's final response is untrusted model output. Deduplicate only
    # evidence the conservative helper can prove related, then normalize back
    # to the advisor's long-standing five-field contract below.
    merged_incidents = merge_incident_evidence(incidents)
    source_counts: dict[str, int] = {}
    for incident in merged_incidents:
        contributing = incident.get("sources")
        values = contributing if isinstance(contributing, list) else [incident.get("source")]
        for source in {str(value).strip() for value in values if str(value).strip()}:
            source_counts[source] = source_counts.get(source, 0) + 1
    if isinstance(metadata, dict):
        metadata = dict(metadata)
        metadata["merge"] = {
            "before_count": len(incidents),
            "after_count": len(merged_incidents),
            "sources": source_counts,
        }
    if isinstance(metadata, dict) and metadata.get("status") != "complete":
        # Empty incidents are not an all-clear when evidence collection was
        # disabled, stale, partial, or failed; preserve that signal in logs.
        print(
            "[trip] incident scan "
            f"status={metadata.get('status', 'failed')} "
            f"snapshot={metadata.get('snapshot_status', 'unavailable')}"
        )
    return {
        "incidents": [_normalize_advisor_incident(incident) for incident in merged_incidents if isinstance(incident, dict)],
        "scan_metadata": metadata if isinstance(metadata, dict) else {"status": "failed", "snapshot_status": "unavailable"},
    }


async def _scan_route_incidents(route_context: Iterable[object], *, snapshot_store: Any | None = None) -> list[dict]:
    """Backward-compatible advisor helper that deliberately returns bare incidents."""
    return (await _scan_route_incidents_with_metadata(route_context, snapshot_store=snapshot_store))["incidents"]


def _station_key(value: object) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").strip().lower())


def _normalize_advisor_incident(incident: dict) -> dict:
    severity = str(incident.get("severity") or "medium").strip().lower()
    if severity not in _ALLOWED_SEVERITIES:
        severity = "medium"

    contributing = incident.get("sources")
    values = contributing if isinstance(contributing, (list, tuple, set)) else [incident.get("source")]
    source = ", ".join(dict.fromkeys(text._safe_text(value, 60) for value in values if text._safe_text(value, 60)))
    normalized = {
        "location": text._safe_text(incident.get("location"), 100),
        "nearby_station": text._safe_text(
            incident.get("nearby_station")
            or incident.get("station")
            or incident.get("stop_name"),
            80,
        ),
        "severity": severity,
        "description": text._safe_text(incident.get("description"), 220),
        "source": text._safe_text(source, 60),
    }
    normalized.update(verified_match_association(incident))
    return normalized


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


def build_candidate_stop_context(gtfs: Any, routes: list[list[dict]]) -> list[CandidateStopContext]:
    """Build all candidate stop contexts from static subway patterns only.

    Google candidate alternatives are not synchronously enriched.  We copy just
    subway legs and attach the already-loaded pattern-index stops; bus legs keep
    their endpoints, avoiding remote DB and bus-feed work on alternate routes.
    """
    index = getattr(gtfs, "_pattern_index", None) if gtfs else None
    context_routes: list[list[dict]] = []
    for route in routes or []:
        copied_route: list[dict] = []
        for original in route or []:
            step = dict(original)
            if step.get("type") == "SUBWAY" and index and step.get("route_id"):
                try:
                    rows, _meta = index.get_intermediate_stops_with_coords(
                        step["route_id"], step.get("departure_stop"), step.get("arrival_stop"),
                        step.get("departure_coords"), step.get("arrival_coords"),
                    )
                except Exception:
                    rows = []
                if rows:
                    step["intermediate_stop_locations"] = [dict(row) for row in rows if isinstance(row, dict)]
            copied_route.append(step)
        context_routes.append(copied_route)
    return extract_candidate_stop_context(context_routes)
