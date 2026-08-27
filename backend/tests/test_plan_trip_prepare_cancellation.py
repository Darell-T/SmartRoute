"""Focused cancellation-safety tests for ``prepare_single_leg``.

Request-owned event/incident evidence tasks must be cancelled and drained
before the caller's ``CancelledError`` propagates, no matter which await the
cancellation lands on after those tasks are created.
"""

from __future__ import annotations

import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from app.services.agent.tools._types import ToolContext
from app.services.agent.tools.location_resolution import ResolvedPlace
from app.services.agent.tools.route.preparation_adapter import prepare_single_leg


def _ctx() -> ToolContext:
    return ToolContext(
        session={},
        session_id="sess-cancel",
        turn_id="t1",
        now_et="2026-08-06T12:00:00-04:00",
        origin={"lat": 40.75, "lng": -73.99},
        agent_mode="auto",
        agent_model="claude-test",
        agent_explanation_style="comparative",
    )


def _route() -> list[dict]:
    return [
        {
            "type": "SUBWAY",
            "route_id": "Q",
            "departure_stop": "Canal St",
            "arrival_stop": "Atlantic Av",
            "departure_time_iso": "2026-08-06T12:05:00-04:00",
            "arrival_time_iso": "2026-08-06T12:25:00-04:00",
        }
    ]


def _blocking_provider(
    *,
    block: bool,
    started: asyncio.Event,
    cleaned_up: asyncio.Event,
    result: object = None,
):
    async def fake(*_args, **_kwargs):
        started.set()
        if not block:
            return result
        try:
            await asyncio.Event().wait()
        finally:
            cleaned_up.set()

    return fake


async def _yield_loop_turn() -> None:
    """Run one event-loop pass without a timer or network.

    Lets a continuation already queued on the loop (e.g. preparation resuming
    after the MTA gather) reach its next blocking await before the test
    cancels, so the cancellation lands deterministically at that await.
    """
    finished = asyncio.Event()
    asyncio.get_running_loop().call_soon(finished.set)
    await finished.wait()


def _dependencies(
    *, block_mta: bool
) -> tuple[SimpleNamespace, dict[str, asyncio.Event]]:
    events = {
        "event_started": asyncio.Event(),
        "event_cleaned_up": asyncio.Event(),
        "incident_started": asyncio.Event(),
        "incident_cleaned_up": asyncio.Event(),
        "mta_started": asyncio.Event(),
    }
    deps = SimpleNamespace(
        resolve_named_place=AsyncMock(
            side_effect=AssertionError("resolve_named_place must not run")
        ),
        derive_arrive_by_departure=AsyncMock(
            side_effect=AssertionError("arrive-by must not run")
        ),
        route_with_recovery=AsyncMock(return_value=[_route()]),
        directions_service=SimpleNamespace(GoogleRoutesError=RuntimeError),
        collect_alerts=_blocking_provider(
            block=block_mta,
            started=events["mta_started"],
            cleaned_up=asyncio.Event(),
            result=[],
        ),
        collect_stalled_trains=AsyncMock(return_value=[]),
        collect_stalled_buses=AsyncMock(return_value=[]),
        parse_service_alerts=lambda _raw: [],
        filter_alerts_for_routes=lambda _alerts, _route_ids: [],
        evidence_envelope=lambda name, payload, **_kwargs: {
            "name": name,
            "payload": payload,
        },
        current_payload=lambda envelope, empty: envelope.get("payload") or empty,
        scoring=SimpleNamespace(_score_routes=lambda _routes, _alerts, **_kwargs: []),
        trip_incidents=SimpleNamespace(
            build_candidate_stop_context=lambda _gtfs, _routes: [],
            scan_route_incidents=_blocking_provider(
                block=True,
                started=events["incident_started"],
                cleaned_up=events["incident_cleaned_up"],
            ),
            incident_lookup_succeeded=lambda _metadata: True,
        ),
        crowd_hotspots=SimpleNamespace(find_hotspot_hits=lambda _gtfs, _routes: []),
        crowd_evidence=SimpleNamespace(
            collect=_blocking_provider(
                block=True,
                started=events["event_started"],
                cleaned_up=events["event_cleaned_up"],
            )
        ),
        candidates=SimpleNamespace(
            _collect_route_and_bus_ids=lambda _routes: (set(), set())
        ),
        route_service_ids=lambda _route: set(),
        context_timeout_seconds=60.0,
        live_evidence_ttl_seconds=60,
        event_evidence_ttl_seconds=60,
    )
    return deps, events


class PlanTripPrepareCancellationTests(unittest.IsolatedAsyncioTestCase):
    async def _start_prepare(
        self,
        deps: SimpleNamespace,
    ) -> asyncio.Task[object]:
        ctx = _ctx()
        origin = ResolvedPlace("Your location", 40.75, -73.99, "user")
        destination = ResolvedPlace("Barclays Center", 40.6826, -73.9754, "fallback")
        return asyncio.create_task(
            prepare_single_leg(
                {
                    "origin": "user",
                    "destination": "Barclays Center",
                    "avoid_crowds": True,
                },
                ctx,
                {},
                dependencies=deps,
                emit_comparing_progress=False,
                resolved_origin=origin,
                resolved_destination=destination,
            )
        )

    async def test_cancellation_during_mta_drains_event_and_incident_tasks(self):
        deps, events = _dependencies(block_mta=True)
        with patch(
            "app.services.agent.tools.route.preparation_adapter.normalize_routes",
            new=lambda routes, _gtfs=None: routes,
        ):
            prepare_task = await self._start_prepare(deps)
            # Both evidence tasks are running and MTA is blocked: preparation
            # is parked inside the MTA gather, so cancellation lands there.
            await events["event_started"].wait()
            await events["incident_started"].wait()
            await events["mta_started"].wait()
            prepare_task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await prepare_task

        assert prepare_task.cancelled()
        assert events["event_cleaned_up"].is_set()
        assert events["incident_cleaned_up"].is_set()

    async def test_cancellation_during_event_await_drains_incident_task(self):
        deps, events = _dependencies(block_mta=False)
        with patch(
            "app.services.agent.tools.route.preparation_adapter.normalize_routes",
            new=lambda routes, _gtfs=None: routes,
        ):
            prepare_task = await self._start_prepare(deps)
            await events["event_started"].wait()
            await events["incident_started"].wait()
            # MTA returned immediately; one loop turn lets preparation reach
            # `await event_task` so the cancellation lands on the event await.
            await _yield_loop_turn()
            prepare_task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await prepare_task

        assert prepare_task.cancelled()
        assert events["event_cleaned_up"].is_set()
        assert events["incident_cleaned_up"].is_set()


if __name__ == "__main__":
    unittest.main()
