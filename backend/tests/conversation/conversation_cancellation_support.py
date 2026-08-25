"""Batch J2 audit support: cancellation/recovery/presentation-race invariants.

Non-test module (no ``Test*``/``test_*`` names at module level): pytest never
collects it. Drives the *real* agent loop (``loop.run_agent_turn``), the real
``TOOL_REGISTRY`` executors for ``prepare_route_options`` / ``present_route``,
the real candidate/discovery/trip/session stores, the real tool ledger, and
real SSE events. Only deterministic Anthropic rounds and the documented
genuine provider/data seams are scripted (``prepare_single_leg`` provider
route/evidence seam, ``_enrich_route`` legacy enrichment guard, ``lookup_arrivals``
live MTA arrivals, ``new_candidate_id`` opaque id generation, the
Google-Routes/MTA provider seams of ``tests.conversation.conversation_cancellation_fixtures``,
plus a recording wrapper around the real candidate store).

Cancellation/disconnect is driven with synchronization events only -- the
provider seam parks on an ``asyncio.Event`` and the test cancels the stream
consumer or flips the transport disconnect flag after the seam signals
``started``. Every test-side wait is bounded by the deadline primitives in
``tests.conversation.conversation_cancellation_fixtures`` (no sleeps, no timers, no network).
"""

from __future__ import annotations

import asyncio
import copy
import secrets
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

from app.routers import agent_chat
from app.services import admission
from app.services.agent import candidate_store
from app.services.agent import trip_state as trip_state_module
from app.services.agent.tools.route import prepare_route_options  # noqa: F401
from app.services.agent.tools._types import ToolResult
from tests.conversation.conversation_cancellation_fixtures import (
    ACCEPTED_DESTINATION,
    CANDIDATE_V1,
    LEAK_CHECK_TIMEOUT_S,
    NOW_ET,
    ROUTE_MESSAGE,
    ROUTE_NAVIGATION_TOOL_PROFILE,
    collect_stream_with_deadline,
    drain_cancelled_turn,
    empty_mta_seam,
    fast_routes_seam,
    wait_for_seam_start,
)
from tests.conversation.conversation_matrix_harness import (
    _turn_round,
    clear_caches,
    make_leg,
    new_session,
    route_cards,
    run_turn,
)

DEFAULT_ORIGIN = {"lat": 40.75, "lng": -73.99}
LEASE = admission.AdmissionLease("v1.test-principal-j2", "chat", "lease-j2-1")

ROUTE_STATE_KEYS = (
    "origin",
    "destination",
    "waypoints",
    "planning_mode",
    "requested_departure",
    "requested_arrival",
    "active_candidate_set_id",
    "selected_candidate_id",
    "temporary_candidate_set_id",
    "temporary_selected_candidate_id",
    "temporary_base_candidate_set_id",
    "active_discovery_set_id",
    "selected_place_id",
)


class CancellationBase(unittest.IsolatedAsyncioTestCase):
    """Shared invariants for the Batch J2 cancellation/recovery audits."""

    loop = None  # set in setUpClass by subclasses

    def setUp(self):
        clear_caches()

    def _new_session(self) -> tuple[str, dict]:
        session_id = f"sess-j2-{secrets.token_hex(4)}"
        _sid, session = new_session()
        return session_id, session

    def _route_state(self, session: dict) -> dict:
        state = trip_state_module.get_trip_state(session)
        return {key: state[key] for key in ROUTE_STATE_KEYS}

    def _snapshot_session(self, session: dict) -> dict:
        """Immutable server-state projection (never a live dict reference)."""

        return {
            "trip_state": copy.deepcopy(self._route_state(session)),
            "active_trip": copy.deepcopy(session.get("active_trip")),
            "route_cards": copy.deepcopy(session.get("route_cards") or []),
        }

    def _snapshot_record(self, set_id: str, session_id: str) -> dict | None:
        record = candidate_store.load_candidate_set(set_id, session_id=session_id)
        return copy.deepcopy(record) if record is not None else None

    async def _assert_no_owned_pending_tasks(self, baseline: set[asyncio.Task]) -> None:
        # The turn drain above already awaited the cancelled task (its finally
        # drained request-owned children), so the comparison is deterministic;
        # the explicit deadline keeps this audit bounded by construction.
        async with asyncio.timeout(LEAK_CHECK_TIMEOUT_S):
            owned = [
                task
                for task in asyncio.all_tasks()
                if task is not asyncio.current_task() and task not in baseline
            ]
            assert owned == [], f"leaked pending tasks: {owned}"

    def _offered_profile(self) -> frozenset:
        return frozenset(
            schema["name"] for schema in self.loop.client.messages.calls[0]["tools"]
        )

    def _turn_patchers(self, mocks: dict | None) -> list:
        enrich = AsyncMock(return_value=None)
        arrivals = AsyncMock(
            return_value=ToolResult(ok=False, error="fixture: no live arrivals")
        )
        patchers = [
            patch("app.services.trips.enrichment._enrich_route", new=enrich),
            patch("app.services.agent.tools.transit.lookup_arrivals.execute", new=arrivals),
        ]
        if mocks is not None:
            mocks["enrich_route"] = enrich
            mocks["lookup_arrivals"] = arrivals
            mocks["stored_candidate_set_ids"] = []
            original_store = candidate_store.store_candidate_set

            def _recording_store(*args, **kwargs):
                set_id = original_store(*args, **kwargs)
                mocks["stored_candidate_set_ids"].append(set_id)
                return set_id

            patchers.append(patch(
                "app.services.agent.candidate_store.store_candidate_set",
                new=_recording_store,
            ))
        return patchers

    def _route_seam_patchers(self, seam) -> list:
        return [
            patch(
                "app.services.agent.tools.route.preparation_adapter._route_with_recovery",
                new=seam,
            )
        ]

    def _alerts_seam_patchers(self, seam) -> list:
        return [
            # The alerts-blocked scenario stubs the Google-Routes provider
            # seam with canned routes so the real ``prepare_single_leg``
            # reaches the live-MTA gather (the blocking seam) without a
            # network provider; the real executor and stores still run.
            patch(
                "app.services.agent.tools.route.preparation_adapter._route_with_recovery",
                new=fast_routes_seam(),
            ),
            patch("app.services.mta.realtime.fetch_service_alerts", new=seam),
            patch("app.services.mta.realtime.get_stalled_trains",
                  new=empty_mta_seam()),
            patch("app.services.mta.realtime.get_stalled_buses",
                  new=empty_mta_seam()),
        ]

    async def _cancelled_turn(
        self,
        *,
        session: dict,
        session_id: str,
        message: str,
        rounds: list,
        seam_started: asyncio.Event,
        seam_cleaned: asyncio.Event,
        seam_patchers: list,
        mocks: dict | None = None,
        mode: str = "auto",
        turn_id: str = "t1",
        scenario_id: str = "J2-CANCEL",
    ) -> tuple[list, object, dict]:
        """Run one real loop turn and cancel the consumer mid-preparation.
        The turn task parks at the blocked genuine provider seam; the test
        cancels it once ``seam_started`` fires. Returns (events, trace, mocks).
        """

        loop = self.loop
        loop.client.messages._rounds = list(rounds)
        loop.client.messages.calls = []
        trace = loop.TurnTrace()
        patchers = self._turn_patchers(mocks) + list(seam_patchers)
        events: list = []
        for patcher in patchers:
            patcher.start()
        try:

            async def _consume():
                async for event in loop.run_agent_turn(
                    session=session,
                    session_id=session_id,
                    turn_id=turn_id,
                    message=message,
                    now_et=NOW_ET,
                    gtfs=None,
                    origin=DEFAULT_ORIGIN,
                    response_presentation=mode,
                    trace=trace,
                ):
                    events.append(event)

            task = asyncio.create_task(_consume())
            await wait_for_seam_start(
                seam_started,
                scenario_id=scenario_id,
                cancellation_point="caller cancellation at prepare",
                fail=self.fail,
            )
            await drain_cancelled_turn(
                task, scenario_id=scenario_id, fail=self.fail
            )
        finally:
            for patcher in patchers:
                patcher.stop()
        return events, trace, mocks

    async def _disconnected_stream(
        self,
        *,
        session: dict,
        session_id: str,
        message: str,
        rounds: list,
        seam_started: asyncio.Event,
        seam_cleaned: asyncio.Event,
        seam_patchers: list,
        mocks: dict | None = None,
        order: list | None = None,
        mode: str = "auto",
        turn_id: str = "t1",
        heartbeat_s: float = 0.01,
        scenario_id: str = "J2-CANCEL",
    ) -> tuple[list, object, Mock, AsyncMock]:
        """Drive the real ``agent_chat._sse_stream`` disconnect path.
        The transport reports disconnected once the provider seam signals
        ``started``; the stream then cancels the in-flight ``__anext__``,
        drains the generator (turn finalization), saves the session, and
        releases the admission lease -- the production ordering contract.
        """

        loop = self.loop
        loop.client.messages._rounds = list(rounds)
        loop.client.messages.calls = []
        request = SimpleNamespace(
            is_disconnected=AsyncMock(side_effect=lambda: seam_started.is_set())
        )
        save_mock = Mock(side_effect=lambda _sid, _sess, **_kwargs: (
            order.append("save") if order is not None else None
        ))
        release_mock = AsyncMock(side_effect=lambda _lease: (
            order.append("release") if order is not None else None
        ))
        patchers = self._turn_patchers(mocks) + list(seam_patchers)
        if order is not None:
            turn_stream = agent_chat.agent_loop.turn_stream
            original_finalize = turn_stream.finalize_turn

            def _finalize(*args, **kwargs):
                order.append("finalize")
                return original_finalize(*args, **kwargs)

            patchers.append(patch.object(turn_stream, "finalize_turn", new=_finalize))
        patchers.extend(
            [
                patch.object(agent_chat, "HEARTBEAT_INTERVAL_S", heartbeat_s),
                patch.object(agent_chat.session_module, "save_session", save_mock),
                patch.object(agent_chat.admission, "release", release_mock),
            ]
        )
        args = dict(
            request=request, session_id=session_id, session=session,
            turn_id=turn_id, message=message, now_et=NOW_ET, gtfs=None,
            origin=DEFAULT_ORIGIN, selected_card_id=None,
            response_presentation=mode, trace=loop.TurnTrace(), lease=LEASE,
        )
        async def _collect() -> list:
            chunks: list = []
            async for chunk in agent_chat._sse_stream(**args):
                chunks.append(chunk)
            return chunks

        for patcher in patchers:
            patcher.start()
        try:
            chunks = await collect_stream_with_deadline(
                _collect, scenario_id=scenario_id, fail=self.fail
            )
        finally:
            for patcher in patchers:
                patcher.stop()
        return chunks, request, save_mock, release_mock

    def _assert_cancelled_no_commit(
        self,
        *,
        scenario_id: str,
        events: list,
        mocks: dict,
        session: dict,
        session_before: dict,
        seam_cleaned: asyncio.Event,
        destination: str | None = None,
    ) -> None:
        """Cancellation leaves no candidate/card/destination/selection commit."""

        self.assertTrue(
            seam_cleaned.is_set(), f"{scenario_id} provider seam cleaned up"
        )
        self.assertEqual(
            [event for event in events if event.type == "route_card"],
            [], f"{scenario_id} no route card streamed after cancel",
        )
        self.assertNotIn("done", [event.type for event in events],
                         f"{scenario_id} no terminal done after cancel")
        self.assertEqual(
            mocks["stored_candidate_set_ids"],
            [], f"{scenario_id} no candidate set stored; "
            f"actual={mocks['stored_candidate_set_ids']}",
        )
        self.assertEqual(
            self._snapshot_session(session),
            session_before, f"{scenario_id} cancelled turn mutates no trip/card state",
        )
        state = trip_state_module.get_trip_state(session)
        if destination is not None:
            self.assertIsNone(
                state["destination"],
                f"{scenario_id} destination never partially commits",
            )

    async def _natural_route_turn(
        self,
        *,
        session: dict,
        session_id: str,
        destination: str = ACCEPTED_DESTINATION,
        candidate_id: str = CANDIDATE_V1,
        message: str = ROUTE_MESSAGE,
        mode: str = "auto",
        turn_id: str = "t1",
        record_mark_presented: bool = False,
    ) -> tuple[list, object, dict, str]:
        """One real prepare -> present turn that commits a route exactly once."""
        rounds = [
            _turn_round(
                "declare_goals",
                f"tu-goals-{turn_id}",
                {
                    "goals": [
                        {
                            "goal_key": "route",
                            "kind": "route",
                            "depends_on": [],
                        }
                    ]
                },
            ),
            _turn_round(
                "prepare_route_options",
                f"tu-prep-{turn_id}",
                {"goal_key": "route", "destination": destination},
            ),
            _turn_round(
                "present_route",
                f"tu-pres-{turn_id}",
                {"goal_key": "route", "candidate_id": candidate_id},
            ),
        ]
        trace = self.loop.TurnTrace()
        mocks: dict = {}
        presented_calls: list = []
        mark_patchers: list = []
        if record_mark_presented:
            original_mark = candidate_store.mark_presented

            def _mark(*args, **kwargs):
                result = original_mark(*args, **kwargs)
                presented_calls.append((args[0], args[1], result))
                return result

            mark_patchers.append(
                patch("app.services.agent.candidate_store.mark_presented", new=_mark)
            )
        for patcher in mark_patchers:
            patcher.start()
        try:
            events, trace = await run_turn(
                self.loop,
                session=session,
                session_id=session_id,
                message=message,
                rounds=rounds,
                mode=mode,
                trace=trace,
                mocks=mocks,
                turn_id=turn_id,
                prepare_leg=make_leg(destination=destination),
                fixed_candidate_id=candidate_id,
            )
        finally:
            for patcher in mark_patchers:
                patcher.stop()
        self._assert_natural_success(
            scenario_id=f"natural-{turn_id}",
            events=events,
            trace=trace,
            mocks=mocks,
            session=session,
            session_id=session_id,
            mode=mode,
            destination=destination,
            candidate_id=candidate_id,
        )
        set_id = trip_state_module.get_trip_state(session)["active_candidate_set_id"]
        return events, trace, mocks, set_id

    def _assert_natural_success(
        self,
        *,
        scenario_id: str,
        events: list,
        trace,
        mocks: dict,
        session: dict,
        session_id: str,
        mode: str,
        destination: str,
        candidate_id: str,
    ) -> None:
        """Exact one-time commit contract for a successful natural routing turn."""

        self.assertEqual(
            [name for name, _input in trace.tool_calls],
            ["declare_goals", "prepare_route_options", "present_route"],
            f"{scenario_id} canonical chain",
        )
        self.assertEqual(
            self._offered_profile(),
            ROUTE_NAVIGATION_TOOL_PROFILE, f"{scenario_id} offered profile",
        )
        cards = route_cards(events)
        self.assertEqual(
            (len(cards), cards[0].role if cards else None),
            (1, "recommended"), f"{scenario_id} exactly one recommended card",
        )
        state = trip_state_module.get_trip_state(session)
        set_id = state["active_candidate_set_id"]
        self.assertTrue(
            bool(set_id) and set_id.startswith("cs_"),
            f"{scenario_id} real server candidate set",
        )
        self.assertEqual(
            (state["destination"], state["selected_candidate_id"]),
            (destination, candidate_id),
            f"{scenario_id} committed destination and selection",
        )
        self.assertEqual(
            mocks["stored_candidate_set_ids"],
            [set_id], f"{scenario_id} exactly one candidate set stored",
        )
        record = candidate_store.load_candidate_set(set_id, session_id=session_id)
        self.assertIsNotNone(record, f"{scenario_id} stored candidate record")
        self.assertTrue(record["presented"], f"{scenario_id} presented once")
        self.assertEqual(
            record["selected_candidate_id"],
            candidate_id, f"{scenario_id} selection recorded in store",
        )
        self.assertEqual(
            mocks["prepare_single_leg"].await_count,
            1, f"{scenario_id} one provider prepare call",
        )
        # Prepared candidates are immutable at presentation time. Keep the
        # legacy seam patched as a guard, but prove request-time enrichment is
        # not reintroduced on the prepare -> present path.
        self.assertEqual(
            mocks["enrich_route"].await_count,
            0, f"{scenario_id} no request-time enrichment",
        )
        self.assertEqual(events[0].type, "meta", f"{scenario_id} meta first")
        self.assertEqual(events[-1].type, "done", f"{scenario_id} done last")
        self.assertEqual(events[-1].stop_reason, "end_turn", f"{scenario_id} end turn")


__all__ = (
    "CancellationBase",
    "DEFAULT_ORIGIN",
    "LEASE",
    "ROUTE_STATE_KEYS",
)
