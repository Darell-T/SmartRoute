"""Candidate-scoped incident scan orchestration for trip planning."""

from __future__ import annotations

import asyncio
import os
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping

from app.services.incident_monitor import get_incidents
from app.services.trips.incident_context import CandidateStopContext, extract_candidate_stop_context
from app.services.trips.incident_scan_cache import (
    cache_scan_contract,
    incident_cache_key,
    load_cached_scan,
    normalize_advisor_incident,
)


TRIP_INCIDENT_SCAN_TIMEOUT_S = float(os.getenv("TRIP_INCIDENT_SCAN_TIMEOUT_S", "10.0"))
_inflight_scans: dict[str, asyncio.Task[dict[str, Any]]] = {}
_inflight_lock = asyncio.Lock()

# Compatibility for internal callers/tests that use the prior private adapter.
_normalize_advisor_incident = normalize_advisor_incident


def _failure_metadata(reason: str) -> dict[str, Any]:
    return {
        "status": "failed",
        "sources": {"attempted": ["x_search", "web_search"], "completed": [], "errors": [reason]},
    }


def _normalized_contract(raw: object) -> dict[str, Any]:
    if not isinstance(raw, Mapping):
        return {"incidents": [], "warnings": [], "scan_metadata": _failure_metadata("invalid scanner contract")}
    metadata = raw.get("scan_metadata")
    incidents = raw.get("incidents")
    if not isinstance(metadata, Mapping) or not isinstance(incidents, list):
        return {"incidents": [], "warnings": [], "scan_metadata": _failure_metadata("invalid scanner contract")}
    status = metadata.get("status")
    if status not in {"complete", "partial", "failed", "disabled"}:
        status = "failed"
    normalized = [normalize_advisor_incident(item) for item in incidents if isinstance(item, Mapping)]
    # Evidence may inform the advisor only after both configured search sources
    # completed and independent origins corroborate a route-impacting claim.
    advisor_incidents = [
        incident for incident in normalized
        if status == "complete" and incident.get("advisor_eligible") is True
    ]
    warnings = [incident for incident in normalized if incident not in advisor_incidents]
    sources = metadata.get("sources") if isinstance(metadata.get("sources"), Mapping) else {}
    normalized_metadata: dict[str, Any] = {
        "status": status,
        "sources": {
            "attempted": list(sources.get("attempted") or ["x_search", "web_search"]),
            "completed": list(sources.get("completed") or []),
        },
        "warning_count": len(warnings),
    }
    errors = sources.get("errors")
    if isinstance(errors, list) and errors:
        normalized_metadata["sources"]["errors"] = [str(error)[:120] for error in errors[:3]]
    rounds = metadata.get("tool_rounds")
    if isinstance(rounds, int) and not isinstance(rounds, bool):
        normalized_metadata["tool_rounds"] = max(0, min(rounds, 2))
    return {"incidents": advisor_incidents, "warnings": warnings, "scan_metadata": normalized_metadata}


async def _scan_route_incidents_with_metadata(route_context: Iterable[object]) -> dict[str, Any]:
    context = list(route_context or [])
    if not context:
        return {
            "incidents": [],
            "warnings": [],
            "scan_metadata": {
                "status": "complete",
                "sources": {"attempted": [], "completed": []},
                "warning_count": 0,
            },
        }
    try:
        raw = await asyncio.wait_for(get_incidents(context), timeout=TRIP_INCIDENT_SCAN_TIMEOUT_S)
    except asyncio.CancelledError:
        raise
    except asyncio.TimeoutError:
        print(f"[trip] incident scan timed out ({TRIP_INCIDENT_SCAN_TIMEOUT_S:.0f}s)")
        return {"incidents": [], "warnings": [], "scan_metadata": _failure_metadata("timeout")}
    except Exception:
        print("[trip] incident scan failed; continuing without evidence")
        return {"incidents": [], "warnings": [], "scan_metadata": _failure_metadata("provider failure")}
    return _normalized_contract(raw)


async def _scan_and_cache(key: str, context: list[object]) -> dict[str, Any]:
    try:
        result = await _scan_route_incidents_with_metadata(context)
        metadata = result.get("scan_metadata")
        if isinstance(metadata, Mapping):
            metadata = dict(metadata)
            metadata["scanned_at"] = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
            metadata["cache_hit"] = False
            result = {**result, "scan_metadata": metadata}
        cache_scan_contract(key, result)
        return result
    finally:
        async with _inflight_lock:
            if _inflight_scans.get(key) is asyncio.current_task():
                _inflight_scans.pop(key, None)


async def scan_route_incidents(
    route_context: Iterable[object],
    *,
    travel_at: datetime | str | None = None,
) -> dict[str, Any]:
    """Share one scan per corridor/time bucket and cache only normalized data."""
    context = list(route_context or [])
    key = incident_cache_key(context, travel_at)
    if not key:
        return await _scan_route_incidents_with_metadata(context)
    cached = load_cached_scan(key)
    if cached is not None:
        return cached
    async with _inflight_lock:
        task = _inflight_scans.get(key)
        if task is None:
            task = asyncio.create_task(_scan_and_cache(key, context), name="route-incident-scan")
            _inflight_scans[key] = task
    # A rider cancellation or per-request timeout must not cancel a shared scan
    # that can still populate the bounded cache for the next rider.
    return await asyncio.shield(task)


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
