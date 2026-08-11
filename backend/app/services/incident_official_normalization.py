"""Pure normalization and deterministic identity for official incident sources.

Shared by the official-source collector: bounded canonical incident-index
inputs for MTA service alerts and stalled GTFS-RT positions, plus the
deterministic stalled-identity helpers. No fetching, parsing, or source-status
orchestration lives here; the collector owns those boundaries and the
attempted-at time.
"""

from __future__ import annotations

import hashlib
import math
from datetime import datetime, timezone
from typing import Any, Callable

from app.services.incident_normalization import (
    bounded_ids,
    bounded_int,
    bounded_text,
    incident_id_for,
    sanitize_source_records,
)
from app.services.mta.config import ALERTS_URL, ALL_SUBWAY_ROUTES, route_to_feed

SOURCE_ALERTS = "mta_alerts"
SOURCE_GTFS_RT = "mta_gtfs_rt"
# Potential stalled signals get a short explicit expiry because stale
# telemetry alone is not proof of a stalled train.
STALLED_EXPIRY_S = 600
_MAX_OFFICIAL_INCIDENTS = 64
_ROUTE_LIST_BOUND = 24
_STOP_LIST_BOUND = 24
_ALERT_ID_BOUND = 120
_NUMBERED_SUFFIX = "numbered"
StalledDetector = Callable[[list[dict[str, Any]], set[str], float], list[dict[str, Any]]]


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
    record: dict[str, str] = {"source": source, "source_id": source_id, "observed_at": observed_at}
    if url:
        record["source_url"] = url
    return sanitize_source_records([record])


def _finite_epoch(value: object) -> float | None:
    """Float for a finite positive epoch; bool, absent, non-positive, NaN/inf,
    and overflow values yield None so they can never become timing facts."""
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
        return datetime.fromtimestamp(epoch, tz=timezone.utc).isoformat()
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
