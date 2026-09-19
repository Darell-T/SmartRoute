"""Read historical 511NY fixtures for offline route comparison tests."""

from __future__ import annotations

import asyncio
import math
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

NYC_BOUNDS = (40.45, 40.95, -74.30, -73.65)
DEFAULT_NYC_BUFFER_DEGREES = 0.05
NYC_COUNTIES = {"bronx", "kings", "new york", "queens", "richmond"}
KNOWN_NON_NYC_COUNTIES = {
    "albany", "allegany", "bronx", "broome", "cattaraugus", "cayuga",
    "chautauqua", "chemung", "chenango", "clinton", "columbia",
    "cortland", "delaware", "dutchess", "erie", "essex", "franklin",
    "fulton", "genesee", "greene", "hamilton", "herkimer", "jefferson",
    "lewis", "livingston", "madison", "monroe", "montgomery", "nassau",
    "niagara", "oneida", "onondaga", "ontario", "orange", "orleans",
    "oswego", "otsego", "putnam", "rensselaer", "rockland", "saratoga",
    "schenectady", "schoharie", "schuyler", "seneca", "st lawrence",
    "steuben", "suffolk", "sullivan", "tioga", "tompkins", "ulster",
    "warren", "washington", "wayne", "westchester", "wyoming", "yates",
}
KNOWN_NON_NYC_COUNTIES -= NYC_COUNTIES


class Normalized511Incident(BaseModel):
    source_id: str
    source: Literal["511ny"] = "511ny"
    event_type: str | None = None
    event_subtype: str | None = None
    description: str | None = None
    comment: str | None = None
    severity_raw: str | None = None
    severity_normalized: Literal["unknown", "low", "moderate", "high", "critical"]
    latitude: float
    longitude: float
    secondary_latitude: float | None = None
    secondary_longitude: float | None = None
    roadway_name: str | None = None
    direction_of_travel: str | None = None
    lanes_affected: str | None = None
    is_full_closure: bool | None = None
    geometry: dict[str, str] | None = None
    reported_at: datetime | None = None
    updated_at: datetime | None = None
    starts_at: datetime | None = None
    expected_end_at: datetime | None = None
    source_metadata: dict[str, str] = Field(default_factory=dict)


class IncidentSnapshot(BaseModel):
    incidents: list[Normalized511Incident]
    fetched_at: datetime | None = None
    last_successful_fetch_at: datetime | None = None
    source_record_count: int = 0
    nyc_record_count: int = 0
    invalid_record_count: int = 0
    source_origin: Literal["live", "fixture"] | None = None
    status: Literal["fresh", "stale", "unavailable"]
    last_error: str | None = None


@dataclass(frozen=True)
class SnapshotSettings:
    stale_after_seconds: float = 900.0
    max_stale_seconds: float = 3600.0
    nyc_buffer_degrees: float = DEFAULT_NYC_BUFFER_DEGREES
    diagnostic: str | None = None


def _text(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    value = " ".join(value.split()).strip()
    return value or None


def _coordinate(value: Any, *, latitude: bool) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    limit = 90.0 if latitude else 180.0
    if not math.isfinite(result) or result == 0 or not -limit <= result <= limit:
        return None
    return result


def _timestamp(value: Any) -> datetime | None:
    if value is None or value == "":
        return None
    try:
        if isinstance(value, (int, float)) or (isinstance(value, str) and value.strip().isdigit()):
            return datetime.fromtimestamp(float(value), tz=UTC)
        if isinstance(value, str):
            parsed = datetime.fromisoformat(value.strip())
            return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)
    except (OverflowError, OSError, ValueError):
        return None
    return None


def _severity(value: str | None) -> Literal["unknown", "low", "moderate", "high", "critical"]:
    label = (value or "").casefold()
    if label in {"critical", "highest"}:
        return "critical"
    if label in {"high", "severe", "major"}:
        return "high"
    if label in {"moderate", "medium"}:
        return "moderate"
    if label in {"low", "minor"}:
        return "low"
    return "unknown"


def _in_nyc_envelope(latitude: float, longitude: float, buffer: float) -> bool:
    min_lat, max_lat, min_lon, max_lon = NYC_BOUNDS
    return min_lat - buffer <= latitude <= max_lat + buffer and min_lon - buffer <= longitude <= max_lon + buffer


def _county_key(record: dict[str, Any]) -> str | None:
    county = _text(record.get("County")) or _text(record.get("CountyName"))
    if county is None:
        return None
    key = county.casefold().replace(" county", "").strip()
    return key or None


def _event_identity(record: dict[str, Any]) -> tuple[str, float, float] | None:
    # V2 documents ``ID`` as the unique event identifier.  ``SourceId`` is
    # retained only as a compatibility fallback for provider variants.
    source_id = _text(record.get("ID")) or _text(record.get("SourceId"))
    latitude = _coordinate(record.get("Latitude"), latitude=True)
    longitude = _coordinate(record.get("Longitude"), latitude=False)
    if not source_id or latitude is None or longitude is None:
        return None
    return source_id, latitude, longitude


def _optional_secondary_coords(
    record: dict[str, Any],
) -> tuple[float | None, float | None]:
    latitude = _coordinate(record.get("LatitudeSecondary"), latitude=True)
    longitude = _coordinate(record.get("LongitudeSecondary"), latitude=False)
    if latitude is None or longitude is None:
        return None, None
    return latitude, longitude


def _event_source_metadata(record: dict[str, Any]) -> dict[str, str]:
    return {
        name: value
        for name, value in {
            "organization": _text(record.get("Organization")),
            "county": _text(record.get("County")) or _text(record.get("CountyName")),
            "state": _text(record.get("State")),
        }.items()
        if value is not None
    }


def _normalized_511_incident(
    record: dict[str, Any], source_id: str, latitude: float, longitude: float
) -> Normalized511Incident:
    secondary_latitude, secondary_longitude = _optional_secondary_coords(record)
    severity_raw = _text(record.get("Severity"))
    encoded_polyline = _text(record.get("EncodedPolyline")) or _text(
        record.get("MapEncodedPolyline")
    )
    return Normalized511Incident(
        source_id=source_id,
        event_type=_text(record.get("EventType")),
        event_subtype=_text(record.get("EventSubType")),
        description=_text(record.get("Description")),
        comment=_text(record.get("Comment")),
        severity_raw=severity_raw,
        severity_normalized=_severity(severity_raw),
        latitude=latitude,
        longitude=longitude,
        secondary_latitude=secondary_latitude,
        secondary_longitude=secondary_longitude,
        roadway_name=_text(record.get("RoadwayName")),
        direction_of_travel=_text(record.get("DirectionOfTravel")),
        lanes_affected=_text(record.get("LanesAffected")),
        is_full_closure=(
            record.get("IsFullClosure")
            if isinstance(record.get("IsFullClosure"), bool)
            else None
        ),
        geometry={"encoded_polyline": encoded_polyline} if encoded_polyline else None,
        reported_at=_timestamp(record.get("Reported")),
        updated_at=_timestamp(record.get("LastUpdated")),
        starts_at=_timestamp(record.get("StartDate")),
        expected_end_at=_timestamp(record.get("PlannedEndDate")),
        source_metadata=_event_source_metadata(record),
    )


def _normalize_event(
    record: Any,
    *,
    nyc_buffer_degrees: float,
) -> tuple[Normalized511Incident | None, bool]:
    if not isinstance(record, dict):
        return None, True
    identity = _event_identity(record)
    if identity is None:
        return None, True
    source_id, latitude, longitude = identity
    if (
        not _in_nyc_envelope(latitude, longitude, nyc_buffer_degrees)
        or _county_key(record) in KNOWN_NON_NYC_COUNTIES
    ):
        return None, False
    return _normalized_511_incident(record, source_id, latitude, longitude), False


def normalize_event(record: Any, *, nyc_buffer_degrees: float = DEFAULT_NYC_BUFFER_DEGREES) -> Normalized511Incident | None:
    """Convert one official v2 event record, returning ``None`` when unusable."""
    incident, _invalid = _normalize_event(record, nyc_buffer_degrees=nyc_buffer_degrees)
    return incident


def normalize_events(records: list[Any], *, nyc_buffer_degrees: float = DEFAULT_NYC_BUFFER_DEGREES) -> list[Normalized511Incident]:
    """Filter, normalize, and de-duplicate by official event identifier."""
    incidents, _invalid_count = _normalize_events(records, nyc_buffer_degrees=nyc_buffer_degrees)
    return incidents


def _normalize_events(
    records: list[Any], *, nyc_buffer_degrees: float = DEFAULT_NYC_BUFFER_DEGREES
) -> tuple[list[Normalized511Incident], int]:
    incidents: dict[str, Normalized511Incident] = {}
    invalid_record_count = 0
    for record in records:
        incident, invalid = _normalize_event(record, nyc_buffer_degrees=nyc_buffer_degrees)
        invalid_record_count += int(invalid)
        if incident is not None:
            incidents[incident.source_id] = incident
    return list(incidents.values()), invalid_record_count


class SnapshotStore:
    """Keep fixture snapshots and reproduce their age at replay time."""

    def __init__(self, settings: SnapshotSettings) -> None:
        self._settings = settings
        self._lock = asyncio.Lock()
        self._snapshot: IncidentSnapshot | None = None
        self._last_error: str | None = None

    async def record_success(
        self,
        records: list[Any],
        *,
        fetched_at: datetime | None = None,
        source_origin: Literal["live", "fixture"] = "live",
    ) -> IncidentSnapshot:
        fetched_at = fetched_at or datetime.now(UTC)
        incidents, invalid_record_count = _normalize_events(
            records, nyc_buffer_degrees=self._settings.nyc_buffer_degrees
        )
        if records and invalid_record_count == len(records):
            raise ValueError("511NY fixture contained no usable event records")
        snapshot = IncidentSnapshot(
            incidents=incidents,
            fetched_at=fetched_at,
            last_successful_fetch_at=fetched_at,
            source_record_count=len(records),
            nyc_record_count=len(incidents),
            invalid_record_count=invalid_record_count,
            source_origin=source_origin,
            status="fresh",
        )
        async with self._lock:
            self._snapshot = snapshot
            self._last_error = None
        return snapshot.model_copy(deep=True)

    async def record_failure(self, error: str) -> None:
        async with self._lock:
            self._last_error = error

    async def get_snapshot(self, *, now: datetime | None = None) -> IncidentSnapshot:
        now = now or datetime.now(UTC)
        async with self._lock:
            snapshot = self._snapshot.model_copy(deep=True) if self._snapshot else None
            error = self._last_error
        if snapshot is None or snapshot.last_successful_fetch_at is None:
            return IncidentSnapshot(
                incidents=[],
                status="unavailable",
                last_error=error or self._settings.diagnostic,
            )
        age = max(0.0, (now - snapshot.last_successful_fetch_at).total_seconds())
        snapshot.last_error = error
        if age > self._settings.max_stale_seconds:
            snapshot.incidents = []
            snapshot.status = "unavailable"
        elif age > self._settings.stale_after_seconds:
            snapshot.status = "stale"
        else:
            snapshot.status = "fresh"
        return snapshot


