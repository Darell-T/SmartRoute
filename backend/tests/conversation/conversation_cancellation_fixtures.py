"""Batch J2 fixtures: cancellation/recovery/presentation-race constants.

Non-test module (no ``Test*``/``test_*`` names at module level): pytest never
collects it. Reuses the Batch E2 identity constants and markers from
``tests.conversation.conversation_candidate_reference_fixtures`` instead of duplicating
them, and owns only the Batch J2-specific transcript messages, deterministic
opaque ids, the controllable *genuine provider seam* factories, and the
shared bounded-wait synchronization primitives (deadline constants plus
``wait_for_seam_start`` / ``drain_cancelled_turn``) that keep every test-side
wait explicit and deterministic.

The seam factories script the narrow provider boundary inside the real
``prepare_route_options`` executor:

- ``route_seam`` blocks at
  ``app.services.trips.preparation.dependencies._route_with_recovery``
  (the Google-Routes provider recovery seam used by the real
  ``prepare_single_leg``), so a cancel/disconnect lands inside real canonical
  route preparation before any candidate store write.
- ``alerts_seam`` blocks at ``mta.realtime.fetch_service_alerts`` (the live MTA
  service-alert fetch), i.e. after the request-owned event/incident evidence
  tasks exist, so cancellation also exercises the real
  ``_drain_owned_evidence_tasks`` path at loop level.

Both are plain async callables patched at their module attribute; the real
registry, executors, candidate/discovery/trip stores, ledger, and SSE events
all run untouched. Synchronization is event/future based only (no sleeps).
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

from tests.conversation.conversation_candidate_reference_fixtures import (
    ALREADY_PRESENTED_MARKER,
    CANDIDATE_SET_UNKNOWN_MARKER,
    CANDIDATE_UNKNOWN_MARKER,
    LEAK_MARKERS,
    UNOFFERED_TOOL_MARKER,
)
from tests.conversation.conversation_reference_safety_fixtures import (
    ROUTE_NAVIGATION_TOOL_PROFILE,
)

# Batch J2 transcript messages. Destinations are deliberately real known
# places so the *real* ``prepare_single_leg`` resolves them without geocoding
# when a test lets the real preparation path run to the provider seam.
ROUTE_MESSAGE = "Plan a route to Barclays Center"
WORK_MESSAGE = "Plan a route to Work"
CHANGE_ROUTE_MESSAGE = "Change the route"
WHAT_IF_CANCEL_MESSAGE = "What if I went to Barclays Center instead?"
STALE_PROBE_MESSAGE = "Show me the first option."
ACCEPTED_DESTINATION = "Barclays Center"
WORK_DESTINATION = "Work"

# Deterministic opaque candidate ids issued through the real store id seam
# (``candidate_store.new_candidate_id``), one per session/turn.
CANDIDATE_V1 = "cd_j2_v1"
CANDIDATE_V2 = "cd_j2_v2"
CANDIDATE_V3 = "cd_j2_v3"

# Fixed timestamps used across the turn transcript (harness convention).
NOW_ET = "2026-08-06T12:00:00-04:00"

# Test-side deadlines: every seam start, cancelled-task drain, disconnect
# stream completion, and no-leak assertion is bounded so a production
# regression or an unreachable seam fails the audit as a scenario-named
# assertion error, never as a hanging suite.
SEAM_START_TIMEOUT_S = 5.0
CANCELLED_DRAIN_TIMEOUT_S = 5.0
STREAM_COMPLETION_TIMEOUT_S = 5.0
LEAK_CHECK_TIMEOUT_S = 1.0


async def wait_for_seam_start(
    event: asyncio.Event,
    *,
    scenario_id: str,
    cancellation_point: str,
    fail: Callable[[str], None],
) -> None:
    """Wait for a provider seam with an explicit deadline; a timeout fails
    with the exact scenario and cancellation point instead of hanging."""

    try:
        await asyncio.wait_for(event.wait(), SEAM_START_TIMEOUT_S)
    except TimeoutError:
        fail(
            f"{scenario_id} {cancellation_point}: provider seam never "
            f"started within {SEAM_START_TIMEOUT_S}s"
        )


async def drain_cancelled_turn(
    task: asyncio.Task,
    *,
    scenario_id: str,
    fail: Callable[[str], None],
) -> None:
    """Drain a cancelled turn task within a bounded deadline.

    The caller owns a guaranteed bounded cancellation: the turn parks at a
    blocked provider seam and the test cancels it. ``asyncio.wait`` observes
    the task with its own deadline, so even a cancellation-swallowing turn
    fails with the scenario instead of hanging the suite.
    """

    task.cancel()
    done, _pending = await asyncio.wait(
        {task}, timeout=CANCELLED_DRAIN_TIMEOUT_S
    )
    if task not in done:
        task.cancel()
        fail(
            f"{scenario_id} cancelled turn task did not drain within "
            f"{CANCELLED_DRAIN_TIMEOUT_S}s"
        )
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        raise exc
    fail(f"{scenario_id} cancelled turn task completed instead of draining")


async def collect_stream_with_deadline(
    collect: Callable[[], Awaitable[list]],
    *,
    scenario_id: str,
    fail: Callable[[str], None],
) -> list:
    """Collect a stream within a bounded deadline; a stalled stream fails
    with the scenario instead of hanging the suite."""

    task = asyncio.ensure_future(collect())
    done, _pending = await asyncio.wait(
        {task}, timeout=STREAM_COMPLETION_TIMEOUT_S
    )
    if task not in done:
        task.cancel()
        fail(
            f"{scenario_id} disconnect stream never completed within "
            f"{STREAM_COMPLETION_TIMEOUT_S}s"
        )
    return task.result()


async def route_seam(
    *,
    started: asyncio.Event,
    cleaned: asyncio.Event,
    release: asyncio.Event | None = None,
    routes: list | None = None,
) -> Callable[..., Awaitable[list]]:
    """Blocking Google-Routes provider seam for the real prepare path.

    Signals ``started``, then parks the caller on ``release`` (or forever when
    ``release`` is None) and signals ``cleaned`` from ``finally`` when the
    caller is cancelled. Returns ``routes`` only when released.
    """

    async def _blocking(*_args: Any, **_kwargs: Any) -> list:
        started.set()
        try:
            if release is not None:
                await release.wait()
            else:
                await asyncio.Event().wait()
        finally:
            cleaned.set()
        return routes or []

    return _blocking


async def alerts_seam(
    *,
    started: asyncio.Event,
    cleaned: asyncio.Event,
    release: asyncio.Event | None = None,
) -> Callable[..., Awaitable[list]]:
    """Blocking live-MTA alerts seam for the real prepare path.

    Blocks inside the real ``asyncio.gather`` of ``prepare_single_leg`` after
    the request-owned event/incident evidence tasks exist, so cancellation
    drains those tasks through the production ``finally``.
    """

    async def _blocking(*_args: Any, **_kwargs: Any) -> list:
        started.set()
        try:
            if release is not None:
                await release.wait()
            else:
                await asyncio.Event().wait()
        finally:
            cleaned.set()
        return []

    return _blocking


def canned_subway_route(destination: str = "Barclays Center") -> list[dict]:
    """Deterministic provider route payload (shape of ``parse_response``).

    The alerts-blocked scenario routes the real ``prepare_single_leg``
    through this payload so it reaches the live-MTA gather without a network
    provider. The SUBWAY step carries no coordinates, so no crowd hotspot
    (and therefore no live event search) is triggered.
    """

    return [
        [
            {
                "type": "WALK",
                "duration_seconds": 180,
                "departure_time_iso": "2026-08-06T12:00:00-04:00",
                "arrival_time_iso": "2026-08-06T12:03:00-04:00",
            },
            {
                "type": "SUBWAY",
                "route_id": "Q",
                "duration_seconds": 1200,
                "departure_stop": "Canal St",
                "arrival_stop": destination,
                "departure_time_iso": "2026-08-06T12:05:00-04:00",
                "arrival_time_iso": "2026-08-06T12:25:00-04:00",
            },
        ]
    ]


def fast_routes_seam(routes: list | None = None) -> Callable[..., Awaitable[list]]:
    """Non-blocking Google-Routes provider seam returning canned routes.

    Lets the real prepare path pass routing and park at the blocking alerts
    seam instead of depending on the Google Routes API (which is not
    configured in this offline audit environment).
    """

    canned = routes if routes is not None else canned_subway_route()

    async def _fast(*_args: Any, **_kwargs: Any) -> list:
        return canned

    return _fast


def empty_mta_seam() -> Callable[..., Awaitable[list]]:
    """Deterministic no-op live-MTA seams for the other MTA gather legs."""

    async def _empty(*_args: Any, **_kwargs: Any) -> list:
        return []

    return _empty


__all__ = (
    "ACCEPTED_DESTINATION",
    "ALREADY_PRESENTED_MARKER",
    "CANCELLED_DRAIN_TIMEOUT_S",
    "CANDIDATE_SET_UNKNOWN_MARKER",
    "CANDIDATE_UNKNOWN_MARKER",
    "CANDIDATE_V1",
    "CANDIDATE_V2",
    "CANDIDATE_V3",
    "CHANGE_ROUTE_MESSAGE",
    "LEAK_CHECK_TIMEOUT_S",
    "LEAK_MARKERS",
    "NOW_ET",
    "ROUTE_MESSAGE",
    "ROUTE_NAVIGATION_TOOL_PROFILE",
    "SEAM_START_TIMEOUT_S",
    "STALE_PROBE_MESSAGE",
    "STREAM_COMPLETION_TIMEOUT_S",
    "UNOFFERED_TOOL_MARKER",
    "WHAT_IF_CANCEL_MESSAGE",
    "WORK_DESTINATION",
    "WORK_MESSAGE",
    "alerts_seam",
    "canned_subway_route",
    "collect_stream_with_deadline",
    "drain_cancelled_turn",
    "empty_mta_seam",
    "fast_routes_seam",
    "route_seam",
    "wait_for_seam_start",
)
