"""Optional Damn Lines queue evidence for exact Google Places venues."""

from __future__ import annotations

import asyncio
import json
import math
import os
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from email.utils import parsedate_to_datetime
from typing import NamedTuple
from zoneinfo import ZoneInfo

from app.services import cache
from app.services.agent.tools.provider_http import fetch_json

_NYC = ZoneInfo("America/New_York")
_CURRENT_MAX_AGE = timedelta(minutes=5)
_HISTORY_REFRESH_AGE = timedelta(days=7)
_HISTORY_MAX_AGE = timedelta(days=30)
_CURRENT_CACHE_KEY, _HISTORY_CACHE_KEY = "agent:damn-lines:current:v1", "agent:damn-lines:history:v1"
_COOLDOWN_CACHE_KEY = "agent:damn-lines:cooldown:v1"


class SupportedVenue(NamedTuple):
    google_place_id: str
    slug: str
    name: str
    source_url: str


class QueueSource(NamedTuple):
    title: str
    url: str


class QueueObservation(NamedTuple):
    google_place_id: str
    people_count: int | None
    wait_minutes: float | None
    captured_at: datetime


class CurrentQueueResult(NamedTuple):
    observations: dict[str, QueueObservation]
    provider_available: bool


class HistoricalQueuePattern(NamedTuple):
    google_place_id: str
    weekday: int
    hour: int
    people_mean: float | None
    wait_minutes_mean: float | None
    sample_count: int
    comparable_dates: int
    date_from: date
    date_to: date


_VENUE_ROWS = (
    ("ChIJ92OsaJVZwokRsC54kf-J-3g", "lindustrie_wv", "L'industrie Pizzeria", "lindustrie-pizzeria"),
    ("ChIJD5S2xrdZwokR-Co72baJxZ4", "salts_cure_wv", "Breakfast by Salt's Cure", "breakfast-by-salts-cure"),
    ("ChIJuW43oZNZwokRdE5tLzpuykE", "johns_on_bleecker", "John's of Bleecker Street", "johns-of-bleecker-street"),
    ("ChIJ330HVABZwokRvqws3mNYmsU", "salt_hanks", "Salt Hank's", "salt-hanks"),
    ("ChIJDyMGAgBZwokRggrgBWarG0o", "banh_anh_em", "Bánh Anh Em", "banh-anh-em"),
    ("ChIJjUkOxEVZwokR4rb65xB6ziE", "lucindas", "Lucinda's", "lucindas"),
    ("ChIJz7d4K2VZwokRGOW9yyiNsck", "the_halal_guys", "The Halal Guys", "the-halal-guys"),
    ("ChIJ1-mW-LhbwokR7Ryv5ha-gzc", "golden_diner", "Golden Diner", "golden-diner"),
    ("ChIJpUwuIMNZwokRBlex2HAooM0", "caffe_panna", "Caffè Panna", "caffe-panna"),
)
_SUPPORTED_VENUES = {
    place_id: SupportedVenue(
        place_id, slug, name, f"https://damnlines.com/camera/{source_slug}"
    )
    for place_id, slug, name, source_slug in _VENUE_ROWS
}
_PLACE_ID_BY_SLUG = {venue.slug: place_id for place_id, venue in _SUPPORTED_VENUES.items()}

_current_refresh_task: asyncio.Task[bool] | None = None
_history_refresh_task: asyncio.Task[bool] | None = None
_warmup_tasks: set[asyncio.Task[None]] = set()
_history_loaded = False
_history_last_success: datetime | None = None
_history_index: dict[tuple[str, int, int], HistoricalQueuePattern] = {}


def get_supported_venue(google_place_id: str) -> SupportedVenue | None:
    return _SUPPORTED_VENUES.get(str(google_place_id or "").strip())


def source_for_places(google_place_ids: list[str]) -> tuple[QueueSource, ...]:
    venues = (get_supported_venue(place_id) for place_id in dict.fromkeys(google_place_ids))
    return tuple(QueueSource(f"Damn Lines: {venue.name}", venue.source_url) for venue in venues if venue)


def _now_utc(now: datetime | None) -> datetime:
    if now is None:
        return datetime.now(UTC)
    if now.tzinfo is None:
        raise ValueError
    return now.astimezone(UTC)


def _timestamp(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip())
    except ValueError:
        return None
    return parsed.astimezone(UTC) if parsed.tzinfo is not None else None


def _non_negative_number(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) and number >= 0 else None


def _people_count(value: object) -> int | None:
    number = _non_negative_number(value)
    return int(number) if number is not None and number.is_integer() else None


def _fresh(captured_at: datetime, now: datetime) -> bool:
    return timedelta(0) <= now - captured_at <= _CURRENT_MAX_AGE


def _observation(place_id: str, status: object, now: datetime) -> QueueObservation | None:
    if place_id not in _SUPPORTED_VENUES or not isinstance(status, dict):
        return None
    captured_at = _timestamp(status.get("captured_at"))
    if captured_at is None or not _fresh(captured_at, now):
        return None
    count = status.get("current_count") if "current_count" in status else status.get("people_count")
    people_count = _people_count(count)
    wait_minutes = _non_negative_number(status.get("wait_minutes"))
    if people_count is None and wait_minutes is None:
        return None
    return QueueObservation(place_id, people_count, wait_minutes, captured_at)


def _normalize_current_record(record: object, now: datetime) -> QueueObservation | None:
    if not isinstance(record, dict):
        return None
    place_id = _PLACE_ID_BY_SLUG.get(str(record.get("slug") or ""))
    return _observation(place_id or "", record.get("status"), now)


def _encode_current(observations: dict[str, QueueObservation]) -> str:
    items = [item._asdict() for item in observations.values()]
    return json.dumps({"observations": items}, separators=(",", ":"), default=str)


def _read_current(now: datetime) -> dict[str, QueueObservation] | None:
    raw = cache.cache_get(_CURRENT_CACHE_KEY, fail_open=True)
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8", errors="replace")
    try:
        payload = json.loads(raw) if isinstance(raw, str) else None
    except (TypeError, ValueError):
        return None
    if not isinstance(payload, dict) or not isinstance(payload.get("observations"), list):
        return None
    observations: dict[str, QueueObservation] = {}
    for record in payload["observations"]:
        observation = _observation(
            str(record.get("google_place_id") or ""), record, now
        ) if isinstance(record, dict) else None
        if observation is not None:
            observations[observation.google_place_id] = observation
    return observations


def _cooldown_active() -> bool:
    return cache.cache_get(_COOLDOWN_CACHE_KEY, fail_open=True) is not None


def _retry_after_seconds(value: str | None, now: datetime) -> int:
    try:
        return max(1, math.ceil(float(value or "")))
    except ValueError:
        try:
            retry_at = parsedate_to_datetime(value or "").astimezone(UTC)
        except (TypeError, ValueError, OverflowError):
            return 60
        return max(1, math.ceil((retry_at - now).total_seconds()))


async def _request_json(
    path: str, *, api_key: str, now: datetime, params: dict[str, object] | None = None
) -> dict | None:
    if _cooldown_active():
        return None
    def handle_response(status: int, headers: object) -> None:
        if status != 429 or not hasattr(headers, "get"):
            return
        delay = _retry_after_seconds(headers.get("Retry-After"), now)
        cache.cache_set(_COOLDOWN_CACHE_KEY, "1", delay, fail_open=True)

    payload, error = await fetch_json(
        "GET",
        f"https://api.damnlines.com/v1{path}",
        timeout_s=_timeout_seconds(),
        log_tag="agent-damn-lines",
        what="queue information",
        params=params,
        headers={"Authorization": f"Bearer {api_key}"},
        on_response=handle_response,
    )
    return payload if error is None and isinstance(payload, dict) else None


def _timeout_seconds() -> float:
    try:
        value = float(os.getenv("DAMNLINES_TIMEOUT_S", "4.0"))
    except ValueError:
        return 4.0
    return value if math.isfinite(value) and value > 0 else 4.0


async def _fetch_current(now: datetime) -> bool:
    api_key = (os.getenv("DAMNLINES_API_KEY") or "").strip()
    if not api_key or _cooldown_active():
        return False
    payload = await _request_json("/locations", api_key=api_key, now=now)
    records = payload.get("data") if payload is not None else None
    if not isinstance(records, list):
        return False
    observations: dict[str, QueueObservation] = {}
    for record in records:
        observation = _normalize_current_record(record, now)
        if observation is not None:
            observations[observation.google_place_id] = observation
    cache.cache_set(
        _CURRENT_CACHE_KEY,
        _encode_current(observations),
        600,
        fail_open=True,
    )
    return True


async def _refresh_current(now: datetime) -> bool:
    global _current_refresh_task
    if _current_refresh_task is None or _current_refresh_task.done():
        _current_refresh_task = asyncio.create_task(_fetch_current(now))
    task = _current_refresh_task
    try:
        return await asyncio.shield(task)
    finally:
        if task.done() and _current_refresh_task is task:
            _current_refresh_task = None


async def get_current_observations(
    google_place_ids: list[str], *, now: datetime | None = None
) -> CurrentQueueResult:
    requested = list(dict.fromkeys(
        place_id for place_id in google_place_ids if place_id in _SUPPORTED_VENUES
    ))
    if not requested:
        return CurrentQueueResult({}, False)
    checked_at = _now_utc(now)
    cached = _read_current(checked_at)
    observations = cached or {}
    available = cached is not None
    if any(place_id not in observations for place_id in requested):
        available = await _refresh_current(checked_at) or available
        observations = _read_current(checked_at) or observations
    relevant = {place_id: observations[place_id] for place_id in requested if place_id in observations}
    return CurrentQueueResult(relevant, available)


@dataclass
class _HistoryAccumulator:
    sample_count: int = 0
    people_weight: float = 0
    people_samples: int = 0
    wait_weight: float = 0
    wait_samples: int = 0
    dates: set[date] = field(default_factory=set)


def _aggregate_history(
    rows_by_place: dict[str, list[object]],
) -> dict[tuple[str, int, int], HistoricalQueuePattern]:
    buckets: dict[tuple[str, int, int], _HistoryAccumulator] = {}
    for place_id, rows in rows_by_place.items():
        for row in rows:
            if not isinstance(row, dict):
                continue
            bucket_start = _timestamp(row.get("bucket_start"))
            samples = _people_count(row.get("count_samples"))
            if bucket_start is None or samples is None or samples <= 0:
                continue
            people = _non_negative_number(row.get("people_mean"))
            wait = _non_negative_number(row.get("wait_minutes_mean"))
            if people is None and wait is None:
                continue
            local = bucket_start.astimezone(_NYC)
            key = (place_id, local.weekday(), local.hour)
            bucket = buckets.setdefault(key, _HistoryAccumulator())
            bucket.sample_count += samples
            bucket.dates.add(local.date())
            if people is not None:
                bucket.people_weight += people * samples
                bucket.people_samples += samples
            if wait is not None:
                bucket.wait_weight += wait * samples
                bucket.wait_samples += samples
    result: dict[tuple[str, int, int], HistoricalQueuePattern] = {}
    for key, bucket in buckets.items():
        dates = sorted(bucket.dates)
        result[key] = HistoricalQueuePattern(
            google_place_id=key[0],
            weekday=key[1],
            hour=key[2],
            people_mean=(bucket.people_weight / bucket.people_samples if bucket.people_samples else None),
            wait_minutes_mean=(bucket.wait_weight / bucket.wait_samples if bucket.wait_samples else None),
            sample_count=bucket.sample_count,
            comparable_dates=len(dates),
            date_from=dates[0],
            date_to=dates[-1],
        )
    return result


def _encode_history(index: dict[tuple[str, int, int], HistoricalQueuePattern], last_success: datetime) -> str:
    return json.dumps(
        {
            "last_success": last_success.isoformat(),
            "patterns": [[*item[:7], item.date_from.isoformat(), item.date_to.isoformat()] for item in index.values()],
        },
        separators=(",", ":"),
        default=str,
    )


def _install_history(raw: object) -> bool:
    global _history_index, _history_last_success, _history_loaded
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8", errors="replace")
    try:
        payload = json.loads(raw) if isinstance(raw, str) else None
        last_success = _timestamp(payload.get("last_success")) if isinstance(payload, dict) else None
        records = payload.get("patterns") if isinstance(payload, dict) else None
    except (TypeError, ValueError):
        return False
    if last_success is None or not isinstance(records, list):
        return False
    index: dict[tuple[str, int, int], HistoricalQueuePattern] = {}
    for record in records:
        if not isinstance(record, list) or len(record) != 9:
            continue
        try:
            pattern = HistoricalQueuePattern(
                str(record[0]),
                record[1],
                record[2],
                _non_negative_number(record[3]),
                _non_negative_number(record[4]),
                record[5],
                record[6],
                date.fromisoformat(str(record[7])),
                date.fromisoformat(str(record[8])),
            )
        except (TypeError, ValueError):
            continue
        if (
            pattern.google_place_id in _SUPPORTED_VENUES
            and isinstance(pattern.weekday, int)
            and isinstance(pattern.hour, int)
            and isinstance(pattern.sample_count, int)
            and isinstance(pattern.comparable_dates, int)
            and 0 <= pattern.weekday <= 6
            and 0 <= pattern.hour <= 23
            and pattern.sample_count > 0
            and pattern.comparable_dates > 0
            and (pattern.people_mean is not None or pattern.wait_minutes_mean is not None)
        ):
            index[(pattern.google_place_id, pattern.weekday, pattern.hour)] = pattern
    _history_index = index
    _history_last_success = last_success
    _history_loaded = True
    return True


def _load_history_cache() -> None:
    global _history_loaded
    if not _history_loaded:
        _history_loaded = True
        _install_history(cache.cache_get(_HISTORY_CACHE_KEY, fail_open=True))


async def _fetch_history_rows(now: datetime, api_key: str) -> dict[str, list[object]] | None:
    since = now - _HISTORY_MAX_AGE
    rows_by_place: dict[str, list[object]] = {}
    for place_id, venue in _SUPPORTED_VENUES.items():
        rows: list[object] = []
        cursor: str | None = None
        seen_cursors: set[str] = set()
        while True:
            params: dict[str, object] = {
                "location": venue.slug,
                "since": since.isoformat(),
                "until": now.isoformat(),
                "interval": "1h",
                "limit": 1000,
            }
            if cursor is not None:
                params["cursor"] = cursor
            payload = await _request_json(
                "/lines", api_key=api_key, now=now, params=params
            )
            page = payload.get("data_aggregated") if payload is not None else None
            pagination = payload.get("pagination") if payload is not None else None
            if not isinstance(page, list) or not isinstance(pagination, dict):
                return None
            rows.extend(page)
            if not pagination.get("has_more"):
                break
            next_cursor = pagination.get("next_cursor")
            if (
                not isinstance(next_cursor, str)
                or not next_cursor
                or next_cursor in seen_cursors
            ):
                return None
            seen_cursors.add(next_cursor)
            cursor = next_cursor
        rows_by_place[place_id] = rows
    return rows_by_place


async def _perform_history_refresh(now: datetime, force: bool) -> bool:
    _load_history_cache()
    if not force and _history_last_success is not None and _history_last_success <= now and now - _history_last_success < _HISTORY_REFRESH_AGE:
        return False
    api_key = (os.getenv("DAMNLINES_API_KEY") or "").strip()
    if not api_key or _cooldown_active():
        return False
    rows = await _fetch_history_rows(now, api_key)
    if rows is None:
        return False
    index = _aggregate_history(rows)
    encoded = _encode_history(index, now)
    cache.cache_set(_HISTORY_CACHE_KEY, encoded, 31 * 24 * 60 * 60, fail_open=True)
    _install_history(encoded)
    return True


async def refresh_history(*, now: datetime | None = None, force: bool = False) -> bool:
    global _history_refresh_task
    checked_at = _now_utc(now)
    if _history_refresh_task is None or _history_refresh_task.done():
        _history_refresh_task = asyncio.create_task(
            _perform_history_refresh(checked_at, force)
        )
    task = _history_refresh_task
    try:
        return await asyncio.shield(task)
    finally:
        if task.done() and _history_refresh_task is task:
            _history_refresh_task = None


async def warm_history(*, now: datetime | None = None) -> None:
    checked_at = _now_utc(now)
    _load_history_cache()
    if _history_last_success is None or checked_at - _history_last_success >= _HISTORY_REFRESH_AGE:
        await refresh_history(now=checked_at)


def schedule_history_warmup(*, now: datetime | None = None) -> None:
    """Start a nonblocking refresh when no current-enough snapshot exists."""

    if not (os.getenv("DAMNLINES_API_KEY") or "").strip():
        return
    _load_history_cache()
    checked_at = _now_utc(now)
    if (
        _history_last_success is not None
        and checked_at - _history_last_success < _HISTORY_REFRESH_AGE
    ):
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    task = loop.create_task(warm_history(now=checked_at))
    _warmup_tasks.add(task)
    task.add_done_callback(_warmup_tasks.discard)


def get_historical_pattern(
    google_place_id: str,
    when: datetime,
    *,
    now: datetime | None = None,
) -> HistoricalQueuePattern | None:
    if google_place_id not in _SUPPORTED_VENUES:
        return None
    _load_history_cache()
    checked_at = _now_utc(now)
    if (
        _history_last_success is None
        or _history_last_success > checked_at
        or checked_at - _history_last_success > _HISTORY_MAX_AGE
    ):
        return None
    if when.tzinfo is None:
        raise ValueError
    local = when.astimezone(_NYC)
    return _history_index.get((google_place_id, local.weekday(), local.hour))
