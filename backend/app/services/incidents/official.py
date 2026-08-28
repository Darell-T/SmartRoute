"""Official incident normalization and bounded source snapshots.

Pure helpers normalize MTA service alerts and stalled GTFS-RT positions into
bounded incident-index inputs. The background collector owns the two provider
boundaries and reports explicit source status; no request path calls this module.
"""

from __future__ import annotations

import hashlib
import logging
import math
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from app.services.incidents.normalization import (
    bounded_ids,
    bounded_int,
    bounded_text,
    incident_id_for,
    sanitize_source_records,
)
from app.services.mta.alerts import (
    fetch_service_alerts as _fetch_service_alerts,
)
from app.services.mta.alerts import (
    parse_service_alerts as _parse_service_alerts,
)
from app.services.mta.config import ALERTS_URL, ALL_SUBWAY_ROUTES, route_to_feed
from app.services.mta.feeds import (
    fetch_feeds_with_metadata as _fetch_feeds_with_metadata,
)
from app.services.mta.subway import (
    detect_stalled_trains as _detect_stalled_trains,
)
from app.services.mta.subway import (
    parse_vehicle_positions as _parse_vehicle_positions,
)

SOURCE_ALERTS = "mta_alerts"
SOURCE_GTFS_RT = "mta_gtfs_rt"
STALLED_EXPIRY_S = 600
_MAX_OFFICIAL_INCIDENTS = 64
_LOGGER = logging.getLogger(__name__)
_ROUTE_LIST_BOUND = 24
_STOP_LIST_BOUND = 24
_ALERT_ID_BOUND = 120
_NUMBERED_SUFFIX = "numbered"
StalledDetector = Callable[
    [list[dict[str, Any]], set[str], float], list[dict[str, Any]]
]


def expected_feed_groups() -> set[str]:
    """Unique GTFS-RT feed suffixes required for the full subway route set."""
    return {
        route_to_feed[route] or _NUMBERED_SUFFIX
        for route in ALL_SUBWAY_ROUTES
        if route in route_to_feed
    }


def normalize_alert(alert: dict[str, Any], attempted_at: str) -> dict[str, Any]:
    """One bounded alert incident. A valid finite positive provider ``start``
    becomes the observation time; ``last_verified_at`` stays the snapshot
    attempted-at time, and only a valid finite positive ``end`` becomes
    expiry. Absent or invalid values fall back to attempted-at, never a
    fabricated time."""
    alert_id = bounded_text(alert.get("alert_id"), _ALERT_ID_BOUND)
    observed_at = epoch_to_iso(alert.get("start")) or attempted_at
    return canonical_incident(
        source=SOURCE_ALERTS,
        source_id=alert_id,
        state="confirmed",
        advisor_eligible=True,
        description=bounded_text(alert.get("description"), 280)
        or bounded_text(alert.get("header"), 200),
        observed_at=observed_at,
        last_verified_at=attempted_at,
        expires_at=epoch_value(alert.get("end")),
        provenance=provenance(SOURCE_ALERTS, alert_id, observed_at, url=ALERTS_URL),
        routes=alert.get("route_ids"),
        stops=alert.get("stop_ids"),
    )


def normalize_stalled(
    stalled_records: list[dict[str, Any]],
    positions: list[dict[str, Any]],
    attempted_at: str,
    *,
    now: float,
) -> list[dict[str, Any]]:
    """Bounded unconfirmed stalled-train incidents with deterministic ids."""
    pairs_by_key = position_identity_pairs(positions)
    indexes: dict[tuple[str, str, str], int] = {}
    incidents: list[dict[str, Any]] = []
    for record in stalled_records:
        if not isinstance(record, dict):
            continue
        key = stalled_key(record)
        route_id, stop_id = key[0], key[1]
        if not route_id or not stop_id:
            continue
        index = indexes.get(key, 0)
        indexes[key] = index + 1
        source_id = stalled_source_id(key, pairs_by_key.get(key, []), index)
        stalled_minutes = bounded_int(record.get("stalled_minutes"))
        incidents.append(
            canonical_incident(
                source=SOURCE_GTFS_RT,
                source_id=source_id,
                state="unconfirmed",
                advisor_eligible=False,
                description=(
                    f"Stale train position on route {route_id} at {stop_id} "
                    f"({stalled_minutes} min old); possible delay - unconfirmed"
                ),
                observed_at=attempted_at,
                last_verified_at=attempted_at,
                expires_at=now + STALLED_EXPIRY_S,
                provenance=provenance(SOURCE_GTFS_RT, source_id, attempted_at),
                routes=[route_id],
                stops=[stop_id],
            )
        )
    return incidents


def canonical_incident(
    *,
    source: str,
    source_id: str,
    state: str,
    advisor_eligible: bool,
    description: str,
    observed_at: str,
    last_verified_at: str,
    expires_at: float | None,
    provenance: list[dict[str, str]],
    routes: object,
    stops: object,
) -> dict[str, Any]:
    """Shared bounded canonical incident-index input shape for both sources."""
    return {
        "source": source,
        "source_id": source_id,
        "state": state,
        "advisor_eligible": advisor_eligible,
        "impact_scope": "subway_operations",
        "description": description,
        "observed_at": observed_at,
        "last_verified_at": last_verified_at,
        "expires_at": expires_at,
        "source_records": provenance,
        "affected_route_ids": bounded_ids(routes, _ROUTE_LIST_BOUND, upper=True),
        "affected_stop_ids": bounded_ids(stops, _STOP_LIST_BOUND),
    }


def provenance(
    source: str, source_id: str, observed_at: str, *, url: str | None = None
) -> list[dict[str, str]]:
    record: dict[str, str] = {
        "source": source,
        "source_id": source_id,
        "observed_at": observed_at,
    }
    if url:
        record["source_url"] = url
    return sanitize_source_records([record])


def _finite_epoch(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if value <= 0:
        return None
    try:
        epoch = float(value)
    except OverflowError:
        return None
    return epoch if math.isfinite(epoch) else None


def epoch_to_iso(value: object) -> str | None:
    """UTC ISO text for a valid finite positive epoch, else None."""
    epoch = _finite_epoch(value)
    if epoch is None:
        return None
    try:
        return datetime.fromtimestamp(epoch, tz=UTC).isoformat()
    except (OverflowError, ValueError, OSError):
        return None


def epoch_value(value: object) -> float | None:
    """Numeric expiry for a valid finite positive epoch, else None."""
    return _finite_epoch(value)


def stalled_key(record: dict[str, Any]) -> tuple[str, str, str]:
    return (
        str(record.get("route_id") or "").upper(),
        str(record.get("stop_id") or ""),
        str(record.get("status") or ""),
    )


def position_identity_pairs(
    positions: list[dict[str, Any]],
) -> dict[tuple[str, str, str], list[tuple[str, str]]]:
    pairs: dict[tuple[str, str, str], list[tuple[str, str]]] = {}
    for pos in positions:
        if not isinstance(pos, dict):
            continue
        pair = (str(pos.get("trip_id") or ""), str(pos.get("timestamp") or ""))
        pairs.setdefault(stalled_key(pos), []).append(pair)
    return pairs


def stalled_source_id(
    key: tuple[str, str, str], pairs: list[tuple[str, str]], index: int
) -> str:
    """Deterministic identity from available route/stop/trip/timestamp values."""
    trip_id, timestamp = pairs[index] if index < len(pairs) else ("", "")
    material = "|".join((key[0], key[1], key[2], trip_id, timestamp))
    digest = hashlib.sha256(material.encode("utf-8")).hexdigest()[:16]
    return f"stalled-{digest}"


def dedupe_incidents(incidents: list[dict[str, Any]]) -> tuple[dict[str, Any], ...]:
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for incident in incidents:
        incident_id = incident_id_for(incident)
        if incident_id in seen:
            continue
        seen.add(incident_id)
        out.append(incident)
        if len(out) >= _MAX_OFFICIAL_INCIDENTS:
            break
    return tuple(out)


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
    attempted_at = datetime.fromtimestamp(now, tz=UTC).isoformat()
    alert_bytes = await _fetch_source(
        fetch_alerts,
        lambda: _fetch_service_alerts(force_refresh=True),
        "MTA service-alert",
    )
    alert_incidents, alert_status = _collect_alert_incidents(
        alert_bytes, parse_alerts, attempted_at
    )
    gtfs_incidents, gtfs_status = await _collect_gtfs_incidents(
        fetch_feed_groups,
        parse_positions,
        detect_stalled,
        now=now,
        attempted_at=attempted_at,
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
    try:
        if fetch is None:
            return await default()
        return await fetch()
    except Exception as exc:  # noqa: BLE001 official source faults report unavailable
        print(f"[incident-official] {label} fetch failed: {type(exc).__name__}")
        return None


def _collect_alert_incidents(
    raw_bytes: bytes,
    parse_alerts: Callable[[bytes], list[dict[str, Any]]] | None,
    attempted_at: str,
) -> tuple[list[dict[str, Any]], str]:
    if not isinstance(raw_bytes, (bytes, bytearray)) or not raw_bytes:
        return [], STATUS_UNAVAILABLE
    parser = parse_alerts if parse_alerts is not None else _parse_service_alerts
    try:
        raw_alerts = parser(bytes(raw_bytes))
    except Exception:  # noqa: BLE001 malformed alerts stay unavailable
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


def _parsed_feed_group(
    group: object,
    parser: Callable[[bytes], list[dict[str, Any]]],
) -> tuple[str, list[dict[str, Any]]] | None:
    if not isinstance(group, dict):
        return None
    suffix = bounded_text(group.get("suffix"), 40) or _NUMBERED_SUFFIX
    content = group.get("content")
    if not isinstance(content, (bytes, bytearray)) or not content:
        return None
    try:
        parsed = parser(bytes(content))
    except Exception as exc:  # noqa: BLE001 malformed GTFS-RT groups are skipped
        _LOGGER.warning(
            "Skipping malformed GTFS-RT group suffix=%s reason=%s",
            suffix,
            type(exc).__name__,
        )
        return None
    if not isinstance(parsed, list):
        return None
    return suffix, parsed


def _positions_from_feed_groups(
    groups: list,
    parser: Callable[[bytes], list[dict[str, Any]]],
    expected: set[str],
) -> tuple[list[dict[str, Any]], set[str]]:
    positions: list[dict[str, Any]] = []
    usable: set[str] = set()
    for group in groups:
        parsed = _parsed_feed_group(group, parser)
        if parsed is None:
            continue
        suffix, vehicles = parsed
        positions.extend(vehicles)
        usable.add(suffix)
    return positions, usable & expected


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
    parser = (
        parse_positions if parse_positions is not None else _parse_vehicle_positions
    )
    positions, usable = _positions_from_feed_groups(groups, parser, expected)
    if not usable:
        return [], STATUS_UNAVAILABLE
    status = STATUS_CURRENT if usable == expected else STATUS_PARTIAL
    detector = detect_stalled if detect_stalled is not None else _detect_stalled_trains
    try:
        stalled_records = detector(positions, set(ALL_SUBWAY_ROUTES), now_timestamp=now)
    except Exception:  # noqa: BLE001 stalled-detector faults report unavailable
        return [], STATUS_UNAVAILABLE
    if not isinstance(stalled_records, list):
        return [], STATUS_UNAVAILABLE
    return normalize_stalled(stalled_records, positions, attempted_at, now=now), status
