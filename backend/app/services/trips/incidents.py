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
from typing import Any, Iterable, Mapping

from app.services.incident_monitor import get_incidents
from app.services.trips import text
from app.services.trips.incident_association import verified_match_association
from app.services.trips.incident_context import CandidateStopContext, extract_candidate_stop_context
from app.services.trips.incident_merge import merge_incident_evidence


TRIP_INCIDENT_SCAN_TIMEOUT_S = float(os.getenv("TRIP_INCIDENT_SCAN_TIMEOUT_S", "25.0"))
_ALLOWED_SEVERITIES = {"low", "medium", "high", "critical"}
_ALLOWED_SCAN_STATUSES = {"complete", "partial", "failed", "disabled"}
_ALLOWED_SNAPSHOT_STATUSES = {"fresh", "stale", "unavailable", "disabled"}
_ALLOWED_SCAN_SOURCES = {"x_search", "web_search", "cached_511ny"}
_MAX_MERGE_SOURCES = 16
_MAX_MERGE_COUNT = 10_000
_MAX_SCAN_ERRORS = 8


def _safe_merge_sources(source_counts: Mapping[str, int]) -> dict[str, int]:
    """Keep derived merge diagnostics bounded and free of arbitrary objects."""

    result: dict[str, int] = {}
    for source, count in source_counts.items():
        name = text._safe_text(source, 60)
        if not name or isinstance(count, bool) or not isinstance(count, int):
            continue
        # The source field usually contains a public handle or provider name,
        # but it originates in merged external evidence. Never use it as a
        # diagnostic key when it resembles a URL or credential fragment.
        if re.search(
            r"(?i)https?://|\b(api[_-]?key|apikey|token|secret|password|authorization|bearer)\b|\bsk-[A-Za-z0-9_-]+",
            name,
        ):
            continue
        result[name] = max(0, min(_MAX_MERGE_COUNT, count))
        if len(result) >= _MAX_MERGE_SOURCES:
            break
    return result


def _safe_source_names(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    names: list[str] = []
    for item in value:
        if isinstance(item, str) and item in _ALLOWED_SCAN_SOURCES and item not in names:
            names.append(item)
    return names


def _safe_error_category(value: object) -> str:
    """Retain availability semantics without copying provider/model error text."""

    text_value = str(value or "").casefold()
    if "timeout" in text_value or "timed out" in text_value:
        return "timeout"
    if "malformed" in text_value or "invalid json" in text_value:
        return "malformed_response"
    if "disabled" in text_value or "not configured" in text_value:
        return "disabled"
    if "unavailable" in text_value or "failed" in text_value or "error" in text_value:
        return "source_error"
    if "limit" in text_value:
        return "limit_reached"
    return "source_error"


def _safe_scan_sources(value: object) -> dict[str, list[str]]:
    if not isinstance(value, Mapping):
        return {}
    result: dict[str, list[str]] = {}
    for key in ("attempted", "completed"):
        names = _safe_source_names(value.get(key))
        if names:
            result[key] = names
    errors = value.get("errors")
    if isinstance(errors, list):
        result["errors"] = [_safe_error_category(item) for item in errors[:_MAX_SCAN_ERRORS]]
    return result


def _safe_counter(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return max(0, min(_MAX_MERGE_COUNT, value))


def _normalize_scan_metadata(
    raw: object,
    *,
    before_count: int,
    after_count: int,
    source_counts: Mapping[str, int],
) -> dict[str, Any]:
    """Defend the route-advisor boundary from malformed scan metadata.

    Incident rows already have their own conservative normalizer.  This keeps
    the surrounding availability signal equally strict: unknown status text is
    never allowed to look like a successful scan.  Valid monitor metadata is
    preserved, with a bounded derived merge summary added for diagnostics.
    """

    if not isinstance(raw, Mapping):
        return {"status": "failed", "snapshot_status": "unavailable"}
    status = raw.get("status")
    snapshot_status = raw.get("snapshot_status")
    metadata: dict[str, Any] = {}
    normalized_status = status if status in _ALLOWED_SCAN_STATUSES else "failed"
    normalized_snapshot_status = (
        snapshot_status if snapshot_status in _ALLOWED_SNAPSHOT_STATUSES else "unavailable"
    )
    # A complete scan is an all-clear only with a fresh cached official
    # snapshot.  Do not let missing, stale, or unavailable snapshot metadata
    # inherit an optimistic provider status.  A disabled source remains a
    # truthful disabled contract rather than being reported as all-clear.
    if normalized_status == "complete" and normalized_snapshot_status != "fresh":
        normalized_status = {
            "stale": "partial",
            "disabled": "disabled",
        }.get(normalized_snapshot_status, "failed")
    metadata["status"] = normalized_status
    metadata["snapshot_status"] = normalized_snapshot_status
    sources = _safe_scan_sources(raw.get("sources"))
    if sources:
        metadata["sources"] = sources
    for key in ("tool_rounds", "local_tool_calls", "total_tool_calls"):
        counter = _safe_counter(raw.get(key))
        if counter is not None:
            metadata[key] = counter
    metadata["merge"] = {
        "before_count": max(0, min(_MAX_MERGE_COUNT, before_count)),
        "after_count": max(0, min(_MAX_MERGE_COUNT, after_count)),
        "sources": _safe_merge_sources(source_counts),
    }
    return metadata


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
    except Exception:
        # Exceptions may contain provider URLs or credentials. The detailed
        # error belongs to the lower-level server log, never this trip log.
        print("[trip] incident scan failed; continuing without evidence")
        return {"incidents": [], "scan_metadata": {"status": "failed", "snapshot_status": "unavailable"}}

    result_is_mapping = isinstance(result, Mapping)
    incidents = result.get("incidents", []) if result_is_mapping else []
    incidents_are_valid = isinstance(incidents, list)
    if not incidents_are_valid:
        incidents = []
    metadata = result.get("scan_metadata", {}) if result_is_mapping else {}
    if not result_is_mapping or not incidents_are_valid:
        # A malformed scanner response must not be allowed to claim a complete
        # empty scan merely because it supplied optimistic metadata.
        metadata = {"status": "failed", "snapshot_status": "unavailable"}
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
    metadata = _normalize_scan_metadata(
        metadata,
        before_count=len(incidents),
        after_count=len(merged_incidents),
        source_counts=source_counts,
    )
    if metadata.get("status") != "complete":
        # Empty incidents are not an all-clear when evidence collection was
        # disabled, stale, partial, or failed; preserve that signal in logs.
        print(
            "[trip] incident scan "
            f"status={metadata.get('status', 'failed')} "
            f"snapshot={metadata.get('snapshot_status', 'unavailable')}"
        )
    return {
        "incidents": [_normalize_advisor_incident(incident) for incident in merged_incidents if isinstance(incident, dict)],
        "scan_metadata": metadata,
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
