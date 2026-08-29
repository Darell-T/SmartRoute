"""Provider-neutral collection and verification of route crowd evidence."""

from __future__ import annotations

import asyncio
import re
from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Any

from app.services.trips.crowds import event as event_crowd
from app.services.trips.crowds import search as crowd_search
from app.services.trips.crowds.hotspots import HotspotHit

_LIVE_SEARCH_DEADLINE_S = 6.0


def _dynamic_hits(routes: list[list[dict]]) -> list[HotspotHit]:
    hits: list[HotspotHit] = []
    for point in event_crowd.search_hubs(routes):
        key = re.sub(r"[^a-z0-9]+", "-", point.name.casefold()).strip("-")
        hits.append(
            HotspotHit(
                route_index=point.route_index,
                hotspot_key=f"route-{point.route_index}-{key}",
                hotspot_name=point.name,
                station_name=point.name,
                latitude=point.latitude,
                longitude=point.longitude,
                expected_at=point.expected_at,
                route_id="",
            )
        )
    return hits


def _route_points(hits: Iterable[HotspotHit]) -> list[event_crowd.RoutePoint]:
    return [
        event_crowd.RoutePoint(
            route_index=hit.route_index,
            name=hit.station_name,
            latitude=hit.latitude,
            longitude=hit.longitude,
            expected_at=hit.expected_at,
            route_id=hit.route_id,
        )
        for hit in hits
    ]


def _parse_impact_identity(impact: dict) -> tuple[int, str, str]:
    venue = re.sub(
        r"[^a-z0-9]+",
        " ",
        str(impact.get("venue") or "").casefold(),
    ).strip()
    title = re.sub(
        r"[^a-z0-9]+",
        " ",
        str(impact.get("title") or "").casefold(),
    ).strip()
    return (
        int(impact.get("route_index", -1)),
        venue or title,
        str(impact.get("window_start_iso") or ""),
    )


def _select_stronger_impact(previous: dict | None, impact: dict) -> dict:
    if previous is None:
        return impact
    if (
        float(impact.get("confidence") or 0),
        float(impact.get("risk_score") or 0),
    ) > (
        float(previous.get("confidence") or 0),
        float(previous.get("risk_score") or 0),
    ):
        return impact
    return previous


def _deduplicate_impacts(impacts: Iterable[dict]) -> list[dict]:
    selected: dict[tuple[int, str, str], dict] = {}
    for impact in impacts:
        key = _parse_impact_identity(impact)
        selected[key] = _select_stronger_impact(selected.get(key), impact)
    return sorted(
        selected.values(),
        key=lambda row: (
            int(row.get("route_index", -1)),
            -float(row.get("risk_score") or 0),
            str(row.get("event_id") or ""),
        ),
    )


def _task_outcome(task: asyncio.Task[Any], done: set[asyncio.Task[Any]]) -> Any:
    if task not in done or task.cancelled():
        return TimeoutError()
    try:
        return task.result()
    except BaseException as exc:  # noqa: BLE001 provider task faults stay unavailable
        return exc


def _select_crowd_hits(
    routes: list[list[dict]],
    hotspot_hits: Iterable[HotspotHit],
    explicit_crowd_request: bool,
) -> list[HotspotHit]:
    hits = list(hotspot_hits)
    if not hits and explicit_crowd_request:
        return _dynamic_hits(routes)
    return hits


def _parse_travel_at(hits: list[HotspotHit], ctx: Any) -> datetime:
    travel_at = next(
        (hit.expected_at for hit in hits if hit.expected_at is not None),
        None,
    )
    if travel_at is not None:
        return travel_at
    try:
        return datetime.fromisoformat(str(ctx.now_et))
    except ValueError:
        return datetime.now(UTC)


async def collect(
    routes: list[list[dict]],
    ctx: Any,
    *,
    hotspot_hits: Iterable[HotspotHit],
    explicit_crowd_request: bool,
    allow_live_search: bool,
) -> tuple[event_crowd.EventEvidenceStatus, list[dict], list[str], dict]:
    hits = _select_crowd_hits(routes, hotspot_hits, explicit_crowd_request)
    if not hits:
        return "not_required", [], [], {"grok_status": "not_required"}

    points = _route_points(hits)
    travel_at = _parse_travel_at(hits, ctx)

    ticketmaster_result, grok_result = await _run_provider_searches(
        routes,
        ctx,
        hits,
        points,
        travel_at,
        allow_live_search,
    )
    ticketmaster_status, ticketmaster_impacts, failures = ticketmaster_result
    grok_events = list(grok_result.get("events") or [])
    grok_impacts = event_crowd.associate_events(
        routes,
        grok_events,
        fallback_time=travel_at,
        additional_route_points=points,
    )
    impacts = _deduplicate_impacts([*ticketmaster_impacts, *grok_impacts])

    grok_status = str(grok_result.get("status") or "unavailable")
    status = _combined_status(
        ticketmaster_status,
        grok_status,
        impacts,
        live_search_required=allow_live_search,
    )

    if allow_live_search and grok_status != "complete":
        failures.append(f"grok_{grok_status}")
    return status, impacts, failures, {
        "grok_status": grok_status,
        "grok_cache_hit": bool(grok_result.get("cache_hit")),
        "completed_sources": list(grok_result.get("completed_sources") or []),
    }


async def _run_provider_searches(
    routes: list[list[dict]],
    ctx: Any,
    hits: list[HotspotHit],
    points: list[event_crowd.RoutePoint],
    travel_at: datetime,
    allow_live_search: bool,
) -> tuple[Any, dict]:
    ticketmaster_task = asyncio.create_task(
        event_crowd.collect_route_event_evidence(
            routes,
            ctx,
            search_points=points,
        )
    )
    grok_task = asyncio.create_task(
        asyncio.wait_for(
            crowd_search.search_hotspots(
                hits,
                travel_at=travel_at,
                allow_live_search=allow_live_search,
            ),
            timeout=_LIVE_SEARCH_DEADLINE_S,
        )
    )
    tasks = {ticketmaster_task, grok_task}
    try:
        done, _pending = await asyncio.wait(tasks, timeout=_LIVE_SEARCH_DEADLINE_S)
    finally:
        # Caller cancellation while waiting must tear down both provider
        # tasks too, not only the normal shared-deadline path.
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    ticketmaster_result = _task_outcome(ticketmaster_task, done)
    grok_outcome = _task_outcome(grok_task, done)
    if isinstance(ticketmaster_result, BaseException):
        ticketmaster_result = (
            "provider_unavailable",
            [],
            [type(ticketmaster_result).__name__],
        )
    if isinstance(grok_outcome, BaseException):
        grok_result = {
            "status": "partial"
            if isinstance(grok_outcome, asyncio.TimeoutError)
            else "unavailable",
            "events": [],
            "completed_sources": [],
        }
    else:
        grok_result = grok_outcome
    return ticketmaster_result, grok_result


def _combined_status(
    ticketmaster_status: str,
    grok_status: str,
    impacts: list[dict],
    *,
    live_search_required: bool,
) -> event_crowd.EventEvidenceStatus:
    ticketmaster_complete = ticketmaster_status in {
        "available",
        "no_relevant_events",
    }
    grok_complete = grok_status == "complete" or not live_search_required
    if ticketmaster_complete and grok_complete:
        return "available" if impacts else "no_relevant_events"
    if ticketmaster_status in {"partial", "available", "no_relevant_events"}:
        return "partial"
    if grok_status in {"complete", "partial"}:
        return "partial"
    return "provider_unavailable"
