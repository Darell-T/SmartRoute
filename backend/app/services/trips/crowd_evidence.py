"""Provider-neutral collection and verification of route crowd evidence."""

from __future__ import annotations

import asyncio
import re
from datetime import datetime, timezone
from typing import Any, Iterable

from app.services.trips import crowd_search, event_crowd
from app.services.trips.crowd_hotspots import HotspotHit

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


def _deduplicate_impacts(impacts: Iterable[dict]) -> list[dict]:
    selected: dict[tuple[int, str, str], dict] = {}
    for impact in impacts:
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
        key = (
            int(impact.get("route_index", -1)),
            venue or title,
            str(impact.get("window_start_iso") or ""),
        )
        previous = selected.get(key)
        if previous is None or (
            float(impact.get("confidence") or 0),
            float(impact.get("risk_score") or 0),
        ) > (
            float(previous.get("confidence") or 0),
            float(previous.get("risk_score") or 0),
        ):
            selected[key] = impact
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
        return asyncio.TimeoutError()
    try:
        return task.result()
    except BaseException as exc:
        return exc


async def collect(
    routes: list[list[dict]],
    ctx: Any,
    *,
    hotspot_hits: Iterable[HotspotHit],
    explicit_crowd_request: bool,
    allow_live_search: bool,
) -> tuple[event_crowd.EventEvidenceStatus, list[dict], list[str], dict]:
    hits = list(hotspot_hits)
    if not hits and explicit_crowd_request:
        hits = _dynamic_hits(routes)
    if not hits:
        return "not_required", [], [], {"grok_status": "not_required"}

    points = _route_points(hits)
    travel_at = next(
        (hit.expected_at for hit in hits if hit.expected_at is not None),
        None,
    )
    if travel_at is None:
        try:
            travel_at = datetime.fromisoformat(str(ctx.now_et).replace("Z", "+00:00"))
        except ValueError:
            travel_at = datetime.now(timezone.utc)

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
    done, pending = await asyncio.wait(
        {ticketmaster_task, grok_task},
        timeout=_LIVE_SEARCH_DEADLINE_S,
    )
    for task in pending:
        task.cancel()
    if pending:
        await asyncio.gather(*pending, return_exceptions=True)

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
            "status": "partial" if isinstance(grok_outcome, asyncio.TimeoutError) else "unavailable",
            "events": [],
            "completed_sources": [],
        }
    else:
        grok_result = grok_outcome
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
    ticketmaster_complete = ticketmaster_status != "provider_unavailable"
    grok_required = allow_live_search
    grok_complete = grok_status == "complete" or not grok_required
    if ticketmaster_complete and grok_complete:
        status: event_crowd.EventEvidenceStatus = (
            "available" if impacts else "no_relevant_events"
        )
    elif ticketmaster_complete or grok_status in {"complete", "partial"}:
        status = "partial"
    else:
        status = "provider_unavailable"

    if grok_required and grok_status not in {"complete"}:
        failures.append(f"grok_{grok_status}")
    return status, impacts, failures, {
        "grok_status": grok_status,
        "grok_cache_hit": bool(grok_result.get("cache_hit")),
        "completed_sources": list(grok_result.get("completed_sources") or []),
    }
