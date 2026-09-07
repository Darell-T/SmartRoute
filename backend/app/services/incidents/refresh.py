"""Thirty-minute background incident refresh orchestration (cron only).

Never runs on rider requests and never selects routes. Acquires a distributed
lock, collects one official MTA snapshot, scouts the ten canonical batches
under a bounded concurrency cap, upserts incidents and per-batch coverage,
stores bounded payload-free metrics, and always releases the lock.
"""

from __future__ import annotations

import asyncio
import json
import secrets
import time
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

from app.services import cache
from app.services.incidents import index as incident_index
from app.services.incidents.batches import INCIDENT_BATCHES, IncidentBatch
from app.services.incidents.official import (
    SOURCE_ALERTS,
    SOURCE_GTFS_RT,
    STATUS_CURRENT,
    STATUS_PARTIAL,
    STATUS_UNAVAILABLE,
    OfficialIncidentSnapshot,
    collect_official_incidents,
)
from app.services.incidents.scout import ScoutBatchResult, scout_incident_batch

JOB_LOCK_KEY = "incident:job:lock"
JOB_LOCK_TTL_S = 25 * 60
JOB_METRICS_KEY = "incident:job:last_metrics"
SCOUT_CONCURRENCY = 2
SCOUT_BATCH_TIMEOUT_S = 60.0


def acquire_job_lock(*, ttl_seconds: int = JOB_LOCK_TTL_S) -> str | None:
    """Atomically acquire the refresh lock; None when another run holds it."""
    token = secrets.token_hex(16)
    if not cache.cache_add(JOB_LOCK_KEY, token, int(ttl_seconds)):
        return None
    return token


def release_job_lock(token: str | None = None) -> bool:
    """Release only when this run still owns the lock; never clears a successor."""
    if not token:
        return False
    return cache.cache_delete_if_value(JOB_LOCK_KEY, token)


def _epoch_to_iso(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, tz=UTC).isoformat().replace("+00:00", "Z")


def _duration_ms(started: float, monotonic: Callable[[], float] | None) -> float:
    now = monotonic() if monotonic is not None else time.monotonic()
    return (now - started) * 1000


async def _collect_official_once(
    collector: Callable[[], Awaitable[OfficialIncidentSnapshot]],
    attempted_at: str,
) -> OfficialIncidentSnapshot:
    try:
        snapshot = await collector()
    except Exception as exc:  # noqa: BLE001 official fetch faults report unavailable
        print(f"[incident-job] official collection failed: {type(exc).__name__}")
        snapshot = None
    if not isinstance(snapshot, OfficialIncidentSnapshot):
        return OfficialIncidentSnapshot(
            incidents=(),
            source_status={SOURCE_ALERTS: STATUS_UNAVAILABLE, SOURCE_GTFS_RT: STATUS_UNAVAILABLE},
            attempted_at=attempted_at,
        )
    return snapshot


def _unavailable_result(batch_id: str, attempted_at: str) -> ScoutBatchResult:
    return ScoutBatchResult(
        batch_id=batch_id,
        incidents=(),
        attempted_at=attempted_at,
        x_status=STATUS_UNAVAILABLE,
        web_status="not_triggered",
        model_calls=0,
    )


async def _scout_one(
    batch: IncidentBatch,
    runner: Callable[[IncidentBatch], Awaitable[ScoutBatchResult]],
    semaphore: asyncio.Semaphore,
    attempted_at: str,
) -> tuple[ScoutBatchResult, bool]:
    async with semaphore:
        try:
            result = await asyncio.wait_for(runner(batch), timeout=SCOUT_BATCH_TIMEOUT_S)
        except TimeoutError:
            return _unavailable_result(batch.batch_id, attempted_at), True
        except Exception as exc:  # noqa: BLE001 scout batch faults report unavailable
            print(f"[incident-job] batch scout failed: {type(exc).__name__}")
            return _unavailable_result(batch.batch_id, attempted_at), False
    if not isinstance(result, ScoutBatchResult):
        return _unavailable_result(batch.batch_id, attempted_at), False
    if result.batch_id != batch.batch_id:
        return _unavailable_result(batch.batch_id, attempted_at), False
    return result, False


async def _scout_all(
    runner: Callable[[IncidentBatch], Awaitable[ScoutBatchResult]],
    attempted_at: str,
) -> list[tuple[ScoutBatchResult, bool]]:
    semaphore = asyncio.Semaphore(SCOUT_CONCURRENCY)
    tasks = [
        asyncio.create_task(_scout_one(batch, runner, semaphore, attempted_at))
        for batch in INCIDENT_BATCHES
    ]
    return list(await asyncio.gather(*tasks))


def _coverage_status(
    *, official_status: dict[str, str], x_status: str, web_status: str
) -> str:
    family = tuple(official_status.get(key) for key in (SOURCE_ALERTS, SOURCE_GTFS_RT))
    official_current = len(family) == 2 and all(status == STATUS_CURRENT for status in family)
    official_usable = any(status in (STATUS_CURRENT, STATUS_PARTIAL) for status in family)
    if official_current and x_status == "complete" and web_status in ("complete", "not_triggered"):
        return "current"
    if not official_usable and x_status == STATUS_UNAVAILABLE:
        return "unavailable"
    return "partial"


def _coverage_record(
    *,
    batch_id: str,
    attempted_at: str,
    official_attempted_at: str | None,
    x_status: str,
    web_status: str,
    x_attempted_at: str,
    incidents_found: int,
    coverage_status: str,
) -> dict[str, Any]:
    return {
        "coverage_id": batch_id,
        "last_attempted_at": attempted_at,
        "last_successful_x_scan_at": x_attempted_at if x_status == "complete" else None,
        "last_official_refresh_at": official_attempted_at,
        "x_status": x_status,
        "web_status": web_status,
        "coverage_status": coverage_status,
        "incidents_found": incidents_found,
    }


def _new_metrics(started_at: str) -> dict[str, Any]:
    return {
        "started_at": started_at,
        "status": "skipped",
        "batches": [],
        "coverage": {"current": 0, "partial": 0, "unavailable": 0},
        "official_sources": {},
        "incidents_upserted": 0,
        "official_incidents": 0,
        "unique_incident_ids": 0,
        "model_calls": 0,
        "scout_timeouts": 0,
        "duration_ms": 0.0,
    }


async def run_background_incident_refresh(
    *,
    collect_official: Callable[[], Awaitable[OfficialIncidentSnapshot]] | None = None,
    scout_batch: Callable[[IncidentBatch], Awaitable[ScoutBatchResult]] | None = None,
    clock: Callable[[], float] | None = None,
    monotonic: Callable[[], float] | None = None,
) -> dict[str, Any]:
    """Run one background refresh cycle; defaults resolve to the real boundaries."""
    started = monotonic() if monotonic is not None else time.monotonic()
    attempted_at = _epoch_to_iso(clock() if clock is not None else time.time())
    metrics = _new_metrics(attempted_at)
    token = acquire_job_lock()
    if token is None:
        metrics["status"] = "lock_held"
        _store_metrics(metrics)
        return metrics
    try:
        collector = collect_official if collect_official is not None else collect_official_incidents
        runner = scout_batch if scout_batch is not None else scout_incident_batch
        snapshot = await _collect_official_once(collector, attempted_at)
        outcomes = await _scout_all(runner, attempted_at)
        metrics["official_sources"] = dict(snapshot.source_status)
        metrics["batches"] = [result.batch_id for result, _timed in outcomes]
        metrics["model_calls"] = sum(result.model_calls for result, _timed in outcomes)
        metrics["scout_timeouts"] = sum(1 for _result, timed_out in outcomes if timed_out)
        official_count = len(snapshot.incidents)
        metrics["official_incidents"] = official_count
        official_usable = any(
            status in (STATUS_CURRENT, STATUS_PARTIAL)
            for status in snapshot.source_status.values()
        )
        upserted, unique_ids, coverage = _index_refresh_cycle(
            snapshot, outcomes, attempted_at, official_usable
        )
        metrics["coverage"] = coverage
        metrics["incidents_upserted"] = upserted
        metrics["unique_incident_ids"] = len(unique_ids)
        metrics["status"] = (
            "complete" if coverage["current"] == len(INCIDENT_BATCHES) else "partial"
        )
        metrics["duration_ms"] = _duration_ms(started, monotonic)
        _store_metrics(metrics)
    except asyncio.CancelledError:
        metrics["status"] = "failed"
        _store_metrics(metrics)
        raise
    except Exception as exc:  # noqa: BLE001 job faults report failed without killing the process
        print(f"[incident-job] refresh failed: {type(exc).__name__}")
        metrics["status"] = "failed"
        metrics["error"] = type(exc).__name__
        metrics["duration_ms"] = _duration_ms(started, monotonic)
        _store_metrics(metrics)
        return metrics
    else:
        return metrics
    finally:
        release_job_lock(token)


def _index_refresh_cycle(
    snapshot: OfficialIncidentSnapshot,
    outcomes: list,
    attempted_at: str,
    official_usable: bool,
) -> tuple[int, set[str], dict[str, int]]:
    unique_ids: set[str] = set()
    upserted = 0
    coverage = {"current": 0, "partial": 0, "unavailable": 0}
    for incident in snapshot.incidents:
        unique_ids.add(incident_index.upsert_incident(incident))
        upserted += 1
    for result, _timed in outcomes:
        for incident in result.incidents:
            unique_ids.add(incident_index.upsert_incident(incident))
            upserted += 1
        status = _coverage_status(
            official_status=snapshot.source_status,
            x_status=result.x_status,
            web_status=result.web_status,
        )
        coverage[status] += 1
        incident_index.set_coverage(
            _coverage_record(
                batch_id=result.batch_id,
                attempted_at=attempted_at,
                official_attempted_at=snapshot.attempted_at if official_usable else None,
                x_status=result.x_status,
                web_status=result.web_status,
                x_attempted_at=result.attempted_at,
                incidents_found=len(result.incidents),
                coverage_status=status,
            )
        )
    return upserted, unique_ids, coverage


def _store_metrics(metrics: dict[str, Any]) -> None:
    cache.cache_set(
        JOB_METRICS_KEY,
        json.dumps(metrics, separators=(",", ":"), default=str),
        24 * 3600,
    )


def last_job_metrics() -> dict[str, Any] | None:
    raw = cache.cache_get(JOB_METRICS_KEY)
    if raw is None:
        return None
    try:
        blob = raw.decode("utf-8") if isinstance(raw, (bytes, bytearray)) else raw
        data = json.loads(blob)
    except (ValueError, TypeError, UnicodeDecodeError):
        return None
    return data if isinstance(data, dict) else None
