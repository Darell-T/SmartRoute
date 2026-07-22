"""Process-local 511NY event snapshots.

This module deliberately has no FastAPI imports and starts no background work
on import.  The application lifecycle owns ``NY511Poller.start``/``stop`` so a
deployment can designate one process to poll rather than accidentally adding a
poller to every worker.  Route-time consumers use ``SnapshotStore.get_snapshot``
only; they never need the upstream API key or make a live request.
"""

from __future__ import annotations

import asyncio
import json
import math
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal
from urllib.parse import urlsplit

import httpx
from pydantic import BaseModel, Field


DEFAULT_API_URL = "https://511ny.org/api/v2/get/event"
DEFAULT_POLL_INTERVAL_SECONDS = 300.0
DEFAULT_TIMEOUT_SECONDS = 10.0
DEFAULT_STALE_AFTER_SECONDS = 900.0
DEFAULT_MAX_STALE_SECONDS = 3600.0
# This envelope covers the five boroughs and nearby bridge/tunnel approaches.
NYC_BOUNDS = (40.45, 40.95, -74.30, -73.65)  # min_lat, max_lat, min_lon, max_lon
DEFAULT_NYC_BUFFER_DEGREES = 0.05
MAX_ATTEMPTS = 3
NYC_COUNTIES = {"bronx", "kings", "new york", "queens", "richmond"}
# County text is not reliable enough to be the only inclusion criterion, but
# known adjacent/non-NYC counties must not leak in through the broad bridge and
# tunnel envelope.  Missing or unrecognized county text falls back to the box.
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
    # A deliberately small, non-sensitive set of source metadata aids server
    # diagnostics without retaining or sending the full provider record onward.
    source_metadata: dict[str, str] = Field(default_factory=dict)


class IncidentSnapshot(BaseModel):
    incidents: list[Normalized511Incident]
    fetched_at: datetime | None = None
    last_successful_fetch_at: datetime | None = None
    source_record_count: int = 0
    nyc_record_count: int = 0
    invalid_record_count: int = 0
    # Fixture snapshots follow the exact live normalization path, but callers
    # and diagnostics still need to know that they did not come from 511NY.
    source_origin: Literal["live", "fixture"] | None = None
    status: Literal["fresh", "stale", "unavailable"]
    last_error: str | None = None


def _finite_env_float(name: str, default: float) -> float:
    value = float(os.getenv(name, default))
    if not math.isfinite(value):
        raise ValueError(f"{name} must be finite")
    return value


@dataclass(frozen=True)
class NY511Settings:
    api_key: str | None
    enabled: bool
    api_url: str = DEFAULT_API_URL
    poll_interval_seconds: float = DEFAULT_POLL_INTERVAL_SECONDS
    request_timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS
    stale_after_seconds: float = DEFAULT_STALE_AFTER_SECONDS
    max_stale_seconds: float = DEFAULT_MAX_STALE_SECONDS
    nyc_buffer_degrees: float = DEFAULT_NYC_BUFFER_DEGREES
    diagnostic: str | None = None
    fixture_path: str | None = None

    @classmethod
    def from_env(cls) -> "NY511Settings":
        """Read optional server settings without making missing config fatal."""
        key = (os.getenv("NY511_API_KEY") or "").strip() or None
        raw_enabled = (os.getenv("NY511_ENABLED") or "true").strip().lower()
        enabled = raw_enabled not in {"0", "false", "no", "off"}
        fixture_path = (os.getenv("NY511_FIXTURE_PATH") or "").strip() or None
        # This project previously had no runtime-environment setting.  Fixture
        # loading therefore requires an explicit non-production declaration;
        # an unset environment is not enough to accidentally replace live
        # provider data in a deployment.
        runtime_environment = (
            os.getenv("SMARTROUTE_ENV")
            or os.getenv("APP_ENV")
            or os.getenv("ENVIRONMENT")
            or ""
        ).strip().casefold()
        api_url = (os.getenv("NY511_API_BASE_URL") or DEFAULT_API_URL).strip()
        parsed_url = urlsplit(api_url)
        if (
            parsed_url.scheme != "https"
            or parsed_url.hostname not in {"511ny.org", "www.511ny.org"}
            or parsed_url.query
            or parsed_url.fragment
            or parsed_url.username
            or parsed_url.password
        ):
            return cls(api_key=key, enabled=False, diagnostic="invalid API base URL")

        try:
            poll_interval = max(
                60.0,
                _finite_env_float(
                    "NY511_POLL_INTERVAL_SECONDS", DEFAULT_POLL_INTERVAL_SECONDS
                ),
            )
            timeout = max(
                1.0,
                _finite_env_float(
                    "NY511_REQUEST_TIMEOUT_SECONDS", DEFAULT_TIMEOUT_SECONDS
                ),
            )
            stale_after = max(
                1.0,
                _finite_env_float(
                    "NY511_STALE_AFTER_SECONDS", DEFAULT_STALE_AFTER_SECONDS
                ),
            )
            max_stale = max(
                stale_after,
                _finite_env_float(
                    "NY511_MAX_STALE_SECONDS", DEFAULT_MAX_STALE_SECONDS
                ),
            )
            buffer = max(
                0.0,
                min(
                    0.25,
                    _finite_env_float(
                        "NY511_NYC_BUFFER_DEGREES", DEFAULT_NYC_BUFFER_DEGREES
                    ),
                ),
            )
        except ValueError:
            return cls(api_key=key, enabled=False, diagnostic="invalid 511NY numeric configuration")

        if not enabled:
            return cls(key, False, api_url, poll_interval, timeout, stale_after, max_stale, buffer, "source disabled")
        if fixture_path:
            if runtime_environment not in {"development", "dev", "test", "testing"}:
                return cls(
                    key,
                    False,
                    api_url,
                    poll_interval,
                    timeout,
                    stale_after,
                    max_stale,
                    buffer,
                    "511NY fixture mode requires a development or test environment",
                )
            # A fixture is intentionally a development/test substitute for the
            # upstream source.  It needs no key and is loaded by the poller
            # into the normal process-local SnapshotStore.
            return cls(
                key,
                True,
                api_url,
                poll_interval,
                timeout,
                stale_after,
                max_stale,
                buffer,
                "using development 511NY fixture",
                fixture_path,
            )
        if not key:
            return cls(key, False, api_url, poll_interval, timeout, stale_after, max_stale, buffer, "API key not configured")
        return cls(key, True, api_url, poll_interval, timeout, stale_after, max_stale, buffer)


class NY511FetchError(Exception):
    def __init__(self, message: str, *, retryable: bool, retry_after_seconds: float | None = None) -> None:
        super().__init__(message)
        self.retryable = retryable
        self.retry_after_seconds = retry_after_seconds


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
            parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
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


def _normalize_event(
    record: Any,
    *,
    nyc_buffer_degrees: float,
) -> tuple[Normalized511Incident | None, bool]:
    """Return normalized record plus whether provider data was malformed."""
    if not isinstance(record, dict):
        return None, True
    # V2 documents ``ID`` as the unique event identifier.  ``SourceId`` is
    # retained only as a compatibility fallback for provider variants.
    source_id = _text(record.get("ID")) or _text(record.get("SourceId"))
    latitude = _coordinate(record.get("Latitude"), latitude=True)
    longitude = _coordinate(record.get("Longitude"), latitude=False)
    if not source_id or latitude is None or longitude is None:
        return None, True
    if not _in_nyc_envelope(latitude, longitude, nyc_buffer_degrees):
        return None, False
    county_key = _county_key(record)
    if county_key in KNOWN_NON_NYC_COUNTIES:
        return None, False

    secondary_latitude = _coordinate(record.get("LatitudeSecondary"), latitude=True)
    secondary_longitude = _coordinate(record.get("LongitudeSecondary"), latitude=False)
    if secondary_latitude is None or secondary_longitude is None:
        secondary_latitude = secondary_longitude = None
    severity_raw = _text(record.get("Severity"))
    encoded_polyline = _text(record.get("EncodedPolyline")) or _text(record.get("MapEncodedPolyline"))
    geometry = {"encoded_polyline": encoded_polyline} if encoded_polyline else None
    metadata = {
        name: value
        for name, value in {
            "organization": _text(record.get("Organization")),
            "county": _text(record.get("County")) or _text(record.get("CountyName")),
            "state": _text(record.get("State")),
        }.items()
        if value is not None
    }
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
        is_full_closure=record.get("IsFullClosure") if isinstance(record.get("IsFullClosure"), bool) else None,
        geometry=geometry,
        reported_at=_timestamp(record.get("Reported")),
        updated_at=_timestamp(record.get("LastUpdated")),
        starts_at=_timestamp(record.get("StartDate")),
        expected_end_at=_timestamp(record.get("PlannedEndDate")),
        source_metadata=metadata,
    ), False


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


class NY511Client:
    def __init__(self, settings: NY511Settings, *, max_attempts: int = MAX_ATTEMPTS) -> None:
        self._settings = settings
        # Production retains the bounded retry policy.  Opt-in certification
        # can request exactly one upstream attempt without duplicating this
        # client or weakening normal refresh behavior.
        self._max_attempts = max(1, min(MAX_ATTEMPTS, int(max_attempts)))

    async def fetch_events(self) -> list[Any]:
        if not self._settings.enabled or not self._settings.api_key:
            raise NY511FetchError("511NY source is disabled", retryable=False)
        last_error: NY511FetchError | None = None
        for attempt in range(self._max_attempts):
            try:
                async with httpx.AsyncClient(timeout=self._settings.request_timeout_seconds) as client:
                    response = await client.get(
                        self._settings.api_url,
                        params={"key": self._settings.api_key, "format": "json"},
                    )
                if response.status_code == 200:
                    try:
                        payload = response.json()
                    except ValueError as exc:
                        raise NY511FetchError("511NY returned malformed JSON", retryable=False) from exc
                    if not isinstance(payload, list):
                        raise NY511FetchError("511NY returned an unexpected event schema", retryable=False)
                    return payload
                retryable = response.status_code == 429 or response.status_code >= 500
                retry_after = None
                if response.status_code == 429:
                    try:
                        retry_after = float(response.headers.get("Retry-After", ""))
                    except (AttributeError, TypeError, ValueError):
                        retry_after = None
                last_error = NY511FetchError(
                    f"511NY request failed with HTTP {response.status_code}",
                    retryable=retryable,
                    retry_after_seconds=retry_after,
                )
            except httpx.TimeoutException:
                last_error = NY511FetchError("511NY request timed out", retryable=True)
            except httpx.RequestError:
                last_error = NY511FetchError("511NY connection failed", retryable=True)

            if last_error is None or not last_error.retryable or attempt == self._max_attempts - 1:
                break
            # A 429 receives the provider delay when supplied, never shorter
            # than the 6 seconds implied by its 10-calls-per-minute budget.
            # Other transient failures use a small bounded exponential backoff.
            delay = 0.5 * (2**attempt)
            if last_error.retry_after_seconds is not None:
                delay = max(6.0, min(60.0, last_error.retry_after_seconds))
            elif "HTTP 429" in str(last_error):
                delay = 6.0
            await asyncio.sleep(delay)
        assert last_error is not None
        raise last_error


class SnapshotStore:
    """Async-safe latest-successful snapshot storage for one application process."""

    def __init__(self, settings: NY511Settings) -> None:
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
            raise NY511FetchError(
                "511NY response contained no usable event records", retryable=False
            )
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


class NY511Poller:
    """Single-flight scheduled refresher for a process-local ``SnapshotStore``."""

    def __init__(self, settings: NY511Settings | None = None, *, client: NY511Client | None = None, store: SnapshotStore | None = None) -> None:
        self.settings = settings or NY511Settings.from_env()
        self.store = store or SnapshotStore(self.settings)
        self.client = client or NY511Client(self.settings)
        self._refresh_lock = asyncio.Lock()
        self._stop_event = asyncio.Event()
        self._task: asyncio.Task[None] | None = None

    async def _fixture_records(self) -> list[Any]:
        """Read one raw provider-shaped development fixture without network I/O."""

        fixture_path = self.settings.fixture_path
        if not fixture_path:
            raise NY511FetchError("511NY fixture path is not configured", retryable=False)

        def _read() -> list[Any]:
            try:
                with open(fixture_path, "r", encoding="utf-8") as handle:
                    payload = json.load(handle)
            except (OSError, ValueError, TypeError) as exc:
                raise NY511FetchError("511NY fixture is malformed or unreadable", retryable=False) from exc
            if not isinstance(payload, list):
                raise NY511FetchError("511NY fixture has an unexpected event schema", retryable=False)
            return payload

        return await asyncio.to_thread(_read)

    async def refresh(self) -> bool:
        """Fetch once, returning false when another refresh is already running."""
        if self._refresh_lock.locked():
            return False
        async with self._refresh_lock:
            if not self.settings.enabled:
                await self.store.record_failure(self.settings.diagnostic or "511NY source is disabled")
                return False
            try:
                if self.settings.fixture_path:
                    records = await self._fixture_records()
                    await self.store.record_success(records, source_origin="fixture")
                else:
                    records = await self.client.fetch_events()
                    await self.store.record_success(records, source_origin="live")
                return True
            except NY511FetchError as exc:
                await self.store.record_failure(str(exc))
            except Exception:
                # Do not surface unexpected response/client implementation
                # details (which could include a request URL) in diagnostics.
                await self.store.record_failure("511NY refresh failed")
            return False

    def start(self) -> asyncio.Task[None] | None:
        """Schedule immediate and periodic polling once; safe to call repeatedly."""
        if not self.settings.enabled:
            return None
        if self._task is None or self._task.done():
            self._stop_event.clear()
            self._task = asyncio.create_task(self._run(), name="ny511-poller")
        return self._task

    async def stop(self) -> None:
        self._stop_event.set()
        if self._task is None:
            return
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        self._task = None

    async def _run(self) -> None:
        while not self._stop_event.is_set():
            await self.refresh()
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=self.settings.poll_interval_seconds)
            except asyncio.TimeoutError:
                pass
