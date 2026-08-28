"""Bounded background Grok X scouting with conditional Web corroboration.

Scouts the ten coarse IncidentBatch regions outside rider requests; never
selects routes and must never run from a rider request. Orchestrates exactly
one X-only model call and one conditional Web-only call through the optional
transport; evidence normalization stays in scout_normalization.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from app.services.incidents.batches import IncidentBatch
from app.services.incidents.scout_normalization import (
    build_incident_inputs,
    is_valid_web_payload,
    is_valid_x_payload,
    normalize_web_corroborations,
    normalize_x_claims,
)
from app.services.incidents.scout_provider import (
    ScoutSearchResult,
    has_client,
    sanitized_claims,
)
from app.services.incidents.scout_provider import (
    _run_web_search as transport_web_search,
)
from app.services.incidents.scout_provider import (
    _run_x_search as transport_x_search,
)
from app.services.trips.crowds.search_normalization import parse_json


@dataclass(frozen=True, slots=True)
class ScoutBatchResult:
    """Truthful outcome of one background batch scout; never an all-clear."""

    batch_id: str
    incidents: tuple[dict[str, Any], ...]
    attempted_at: str
    x_status: str
    web_status: str
    model_calls: int


def _normalize_clock(clock: Callable[[], datetime] | None) -> datetime:
    raw = clock() if clock is not None else datetime.now(UTC)
    if raw.tzinfo is None or raw.utcoffset() is None:
        raise ValueError("incident scout clock must return an offset-aware datetime")
    return raw.astimezone(UTC)


def _consume(
    result: ScoutSearchResult | None,
    *,
    now: datetime,
    claims_by_ref: Mapping[str, Any] | None = None,
) -> tuple[str, list[dict[str, Any]]]:
    if result is None:
        return "unavailable", []
    if not isinstance(result, ScoutSearchResult):
        return "partial", []
    payload = parse_json(result.response_text)
    if not result.tool_completed or payload is None:
        return "partial", []
    if claims_by_ref is None:
        if not is_valid_x_payload(payload):
            return "partial", []
        return "complete", normalize_x_claims(payload, citations=result.citations, now=now)
    if not is_valid_web_payload(payload):
        return "partial", []
    return "complete", normalize_web_corroborations(
        payload, claims_by_ref=claims_by_ref, citations=result.citations, now=now
    )


def _log_boundary_failure(phase: str, exc: BaseException) -> None:
    print(f"[incident-scout] {phase} runner failed: {type(exc).__name__}")


async def scout_incident_batch(
    batch: IncidentBatch,
    *,
    run_x_search: Callable[..., Awaitable[ScoutSearchResult]] | None = None,
    run_web_search: Callable[..., Awaitable[ScoutSearchResult]] | None = None,
    clock: Callable[[], datetime] | None = None,
) -> ScoutBatchResult:
    """Scout one coarse batch: exactly one X call, then one optional Web call."""
    now = _normalize_clock(clock)
    x_runner = run_x_search if run_x_search is not None else transport_x_search
    web_runner = run_web_search if run_web_search is not None else transport_web_search
    model_calls = 0
    x_result: ScoutSearchResult | None = None
    if run_x_search is not None or has_client():
        model_calls += 1
        try:
            x_result = await x_runner(batch, now=now)
        except Exception as exc:  # noqa: BLE001 scout transport faults stay unavailable
            _log_boundary_failure("x", exc)
    x_status, claims = _consume(x_result, now=now)
    web_status = "not_triggered"
    corroborations: list[dict[str, Any]] = []
    if x_status == "complete" and claims:
        if run_web_search is not None or has_client():
            model_calls += 1
            web_result: ScoutSearchResult | None = None
            try:
                web_result = await web_runner(sanitized_claims(claims), now=now)
            except Exception as exc:  # noqa: BLE001 scout transport faults stay unavailable
                _log_boundary_failure("web", exc)
            web_status, corroborations = _consume(
                web_result,
                now=now,
                claims_by_ref={claim["claim_ref"]: claim for claim in claims},
            )
        else:
            web_status = "unavailable"
    return ScoutBatchResult(
        batch_id=batch.batch_id,
        incidents=build_incident_inputs(
            claims, corroborations, batch_id=batch.batch_id, now=now
        ),
        attempted_at=now.isoformat().replace("+00:00", "Z"),
        x_status=x_status,
        web_status=web_status,
        model_calls=model_calls,
    )
