"""Candidate-scoped incident evidence from the deterministic incident index.

Normal rider routing performs exactly one immediate batched index lookup,
bounded and off the async event loop: no provider scan, X/web search, retry,
or background queue runs on the request path. The background incident job owns
provider boundaries and writes the index; coverage truth comes only from
explicit coverage records. Pure context matching and record projection live in
incident_index_adapter.
"""

from __future__ import annotations

import time
from datetime import datetime
from typing import Any, Iterable, Mapping

from app.services.incidents import index as incident_index
from app.services.trips.route_incidents.context import CandidateStopContext, extract_candidate_stop_context
from app.services.trips.route_incidents.index_adapter import extract_lookup_context, project_records

COMPLETE_INCIDENT_SCAN_STATUS = "complete"

# Single canonical rider disclosure for incomplete incident coverage. The
# agent projection and the direct Live Map path both import this exact
# sentence so the two projection surfaces can never drift apart.
INCOMPLETE_INCIDENT_DISCLOSURE = (
    "Current incident coverage is incomplete, so allow extra time."
)
_UNSAFE_INCIDENT_CLEAR_MARKERS = (
    "no incidents",
    "no reported incidents",
    "no active incidents",
    "all clear",
    "no disruption",
    "none blocking",
    "none changing",
)

_LEGACY_STATUS = {
    "current": "complete",
    "partial": "partial",
    "stale": "stale",
    "unavailable": "unavailable",
    "unscanned": "unscanned",
}


def incident_scan_is_complete(metadata: Mapping[str, object] | None) -> bool:
    """Coverage is fully current only when the legacy status is complete."""
    return isinstance(metadata, Mapping) and metadata.get("status") == COMPLETE_INCIDENT_SCAN_STATUS


def contains_unsafe_incident_clear(value: str) -> bool:
    """Identify unsupported all-clear claims when incident coverage is incomplete."""
    normalized = value.casefold()
    return any(marker in normalized for marker in _UNSAFE_INCIDENT_CLEAR_MARKERS)


def incident_lookup_succeeded(metadata: Mapping[str, object] | None) -> bool:
    """Indexed evidence is safe to consume once the lookup itself succeeded.
    Independent of overall coverage freshness: a specific unexpired confirmed
    indexed incident stays usable while an unrelated coverage batch is stale.
    """
    return isinstance(metadata, Mapping) and metadata.get("lookup_status") == COMPLETE_INCIDENT_SCAN_STATUS


def _base_metadata(coverage_ids: list[str], warning_count: int, *, completed: bool) -> dict[str, Any]:
    """Truthful, payload-free lookup sources: the index was the only source."""
    return {
        "lookup_kind": "index",
        "requested_coverage_ids": coverage_ids,
        "warning_count": warning_count,
        "cache_hit": False,
        "sources": {
            "attempted": ["incident_index"],
            "completed": ["incident_index"] if completed else [],
        },
    }


async def scan_route_incidents(
    route_context: Iterable[object],
    *,
    travel_at: datetime | str | None = None,
) -> dict[str, Any]:
    """One immediate incident-index lookup; never scans providers."""
    del travel_at  # indexed coverage is not travel-time dependent
    rows, stop_ids, route_ids, coverage_ids = extract_lookup_context(route_context or [])
    started = time.monotonic()
    try:
        result = await incident_index.lookup_incidents_async(
            stop_ids=stop_ids,
            route_ids=route_ids,
            coverage_ids=coverage_ids,
        )
    except Exception as exc:
        print(f"[trip] incident index lookup failed: {type(exc).__name__}")
        return {
            "incidents": [],
            "warnings": [],
            "scan_metadata": {
                **_base_metadata(coverage_ids, 0, completed=False),
                "status": "unavailable",
                "lookup_status": "failed",
                "coverage_status": "unavailable",
            },
        }
    latency_ms = (time.monotonic() - started) * 1000
    coverage_status = str(result.get("coverage_status") or "unscanned")
    incidents, warnings = project_records(result.get("incidents") or [], rows)
    return {
        "incidents": incidents,
        "warnings": warnings,
        "scan_metadata": {
            **_base_metadata(coverage_ids, len(warnings), completed=True),
            "status": _LEGACY_STATUS.get(coverage_status, "unscanned"),
            "lookup_status": COMPLETE_INCIDENT_SCAN_STATUS,
            "coverage_status": coverage_status,
            "lookup_latency_ms": round(latency_ms, 3),
        },
    }


def build_candidate_stop_context(gtfs: Any, routes: list[list[dict]]) -> list[CandidateStopContext]:
    """Cover every static intermediate transit stop without request-time DB work."""
    index = getattr(gtfs, "_pattern_index", None) if gtfs else None
    context_routes: list[list[dict]] = []
    for route in routes or []:
        copied_route: list[dict] = []
        for original in route or []:
            step = dict(original)
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
                if rows:
                    step["intermediate_stop_locations"] = [
                        dict(row) for row in rows if isinstance(row, dict)
                    ]
            copied_route.append(step)
        context_routes.append(copied_route)
    return extract_candidate_stop_context(context_routes)
