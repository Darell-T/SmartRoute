"""Bounded official incident-source snapshots for background workers.

Collects exactly two MTA source families - the single subway service-alert
feed (forced fresh) and the deduplicated GTFS-RT subway feed groups needed
for ALL_SUBWAY_ROUTES (forced fresh) - into bounded canonical incident-index
inputs with explicit per-source status. Never selects routes and must not be
called from a rider request. This module owns the production provider calls
through defaults resolved from the MTA fetch/parse boundaries; tests inject
callables and recorded provider-shaped values, so tests make no provider
calls. Pure normalization and deterministic identity live in
incident_official_normalization; this module re-exports their public constants
alongside the snapshot contract.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable

from app.services.incident_normalization import bounded_text
from app.services.incident_official_normalization import (
    SOURCE_ALERTS,
    SOURCE_GTFS_RT,
    STALLED_EXPIRY_S,
    StalledDetector,
    _NUMBERED_SUFFIX,
    dedupe_incidents,
    expected_feed_groups,
    normalize_alert,
    normalize_stalled,
)
from app.services.mta.alerts import (
    fetch_service_alerts as _fetch_service_alerts,
    parse_service_alerts as _parse_service_alerts,
)
from app.services.mta.config import ALL_SUBWAY_ROUTES
from app.services.mta.feeds import fetch_feeds_with_metadata as _fetch_feeds_with_metadata
from app.services.mta.subway import (
    detect_stalled_trains as _detect_stalled_trains,
    parse_vehicle_positions as _parse_vehicle_positions,
)

STATUS_CURRENT = "current"
STATUS_PARTIAL = "partial"
STATUS_UNAVAILABLE = "unavailable"


@dataclass(frozen=True, slots=True)
class OfficialIncidentSnapshot:
    """Bounded immutable snapshot; an empty incident tuple never implies
    all-clear - trust ``source_status`` instead."""

    incidents: tuple[dict[str, Any], ...]
    source_status: dict[str, str]
    attempted_at: str


async def collect_official_incidents(
    *,
    fetch_alerts: Callable[[], Awaitable[bytes]] | None = None,
    fetch_feed_groups: Callable[[], Awaitable[list[dict[str, Any]]]] | None = None,
    parse_alerts: Callable[[bytes], list[dict[str, Any]]] | None = None,
    parse_positions: Callable[[bytes], list[dict[str, Any]]] | None = None,
    detect_stalled: StalledDetector | None = None,
    clock: Callable[[], float] | None = None,
) -> OfficialIncidentSnapshot:
    """Collect one bounded official incident snapshot (background only).
    Production defaults are resolved here so tests can inject every callable.
    Injected contracts: ``fetch_alerts()`` -> bytes; ``fetch_feed_groups()``
    -> feed dicts with ``suffix``/``content``; ``parse_alerts(bytes)`` ->
    alert dicts; ``parse_positions(bytes)`` -> position dicts;
    ``detect_stalled(positions, route_ids, now_timestamp)`` -> stalled dicts;
    ``clock()`` -> epoch seconds. Sources are independent."""
    now = clock() if clock is not None else time.time()
    attempted_at = datetime.fromtimestamp(now, tz=timezone.utc).isoformat()
    alert_bytes = await _fetch_source(
        fetch_alerts,
        lambda: _fetch_service_alerts(force_refresh=True),
        "MTA service-alert",
    )
    alert_incidents, alert_status = _collect_alert_incidents(
        alert_bytes, parse_alerts, attempted_at
    )
    gtfs_incidents, gtfs_status = await _collect_gtfs_incidents(
        fetch_feed_groups, parse_positions, detect_stalled, now=now, attempted_at=attempted_at
    )
    return OfficialIncidentSnapshot(
        incidents=dedupe_incidents(alert_incidents + gtfs_incidents),
        source_status={SOURCE_ALERTS: alert_status, SOURCE_GTFS_RT: gtfs_status},
        attempted_at=attempted_at,
    )


async def _fetch_source(
    fetch: Callable[[], Awaitable[Any]] | None,
    default: Callable[[], Awaitable[Any]],
    label: str,
) -> Any:
    """Run one provider fetch, translating failure into None at the boundary."""
    try:
        if fetch is None:
            return await default()
        return await fetch()
    except Exception as exc:
        print(f"[incident-official] {label} fetch failed: {type(exc).__name__}")
        return None


def _collect_alert_incidents(
    raw_bytes: bytes,
    parse_alerts: Callable[[bytes], list[dict[str, Any]]] | None,
    attempted_at: str,
) -> tuple[list[dict[str, Any]], str]:
    """Alerts are current only for a non-empty, successfully parsed payload."""
    if not isinstance(raw_bytes, (bytes, bytearray)) or not raw_bytes:
        return [], STATUS_UNAVAILABLE
    parser = parse_alerts if parse_alerts is not None else _parse_service_alerts
    try:
        raw_alerts = parser(bytes(raw_bytes))
    except Exception:
        return [], STATUS_UNAVAILABLE
    if not isinstance(raw_alerts, list):
        return [], STATUS_UNAVAILABLE
    incidents: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for alert in raw_alerts:
        if not isinstance(alert, dict):
            continue
        incident = normalize_alert(alert, attempted_at)
        if incident["source_id"] and incident["source_id"] not in seen_ids:
            seen_ids.add(incident["source_id"])
            incidents.append(incident)
    return incidents, STATUS_CURRENT


async def _collect_gtfs_incidents(
    fetch_feed_groups: Callable[[], Awaitable[list[dict[str, Any]]]] | None,
    parse_positions: Callable[[bytes], list[dict[str, Any]]] | None,
    detect_stalled: StalledDetector | None,
    *,
    now: float,
    attempted_at: str,
) -> tuple[list[dict[str, Any]], str]:
    groups = await _fetch_source(
        fetch_feed_groups,
        lambda: _fetch_feeds_with_metadata(ALL_SUBWAY_ROUTES, force_refresh=True),
        "GTFS-RT",
    )
    if not isinstance(groups, list):
        return [], STATUS_UNAVAILABLE
    expected = expected_feed_groups()
    if not expected:
        return [], STATUS_UNAVAILABLE
    parser = parse_positions if parse_positions is not None else _parse_vehicle_positions
    positions: list[dict[str, Any]] = []
    usable: set[str] = set()
    for group in groups:
        if not isinstance(group, dict):
            continue
        suffix = bounded_text(group.get("suffix"), 40) or _NUMBERED_SUFFIX
        content = group.get("content")
        if not isinstance(content, (bytes, bytearray)) or not content:
            # Empty or non-bytes feed groups are not usable evidence.
            continue
        try:
            parsed = parser(bytes(content))
        except Exception:
            # One malformed group never discards other usable groups.
            continue
        if not isinstance(parsed, list):
            # A parser result must be a list; anything else is unusable.
            continue
        positions.extend(parsed)
        usable.add(suffix)
    usable &= expected
    if not usable:
        return [], STATUS_UNAVAILABLE
    status = STATUS_CURRENT if usable == expected else STATUS_PARTIAL
    detector = detect_stalled if detect_stalled is not None else _detect_stalled_trains
    try:
        stalled_records = detector(positions, set(ALL_SUBWAY_ROUTES), now_timestamp=now)
    except Exception:
        # Without stalled detection, GTFS coverage cannot be claimed; alerts
        # are collected separately and stay intact.
        return [], STATUS_UNAVAILABLE
    if not isinstance(stalled_records, list):
        # A non-list detector result is an invalid detector contract; treat it
        # like a detector failure and claim no GTFS coverage. Alerts stay intact.
        return [], STATUS_UNAVAILABLE
    return normalize_stalled(stalled_records, positions, attempted_at, now=now), status
