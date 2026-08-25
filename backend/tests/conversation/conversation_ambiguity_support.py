"""Batch E3 support: ambiguity/contradiction/temporal audit invariants.

Non-test module (no ``Test*``/``test_*`` names at module level): pytest never
collects it. Drives the *real* agent loop (``loop.run_agent_turn``) with
production intent/tool filtering, the real ``TOOL_REGISTRY`` executors, real
stores, ledger, and SSE events; only deterministic Anthropic rounds and the
documented provider/data seams are scripted. Time validation is additionally
probed through the *real* ``prepare_route_options`` executor with the *real*
``prepare_single_leg`` validation body and scripted provider seams, so
"rejected invalid times cannot create candidate/card state" is proven at the
canonical validation seam.
"""

from __future__ import annotations

import dataclasses
import secrets
import unittest
from contextlib import contextmanager
from unittest.mock import AsyncMock, patch

from app.services.agent import candidate_store
from app.services.agent import discovery_store
from app.services.agent import trip_state as trip_state_module
from app.services.agent.tools.route import prepare_route_options
from app.services.agent.tools.location_resolution import ResolvedPlace
from app.services.agent.tools._types import ToolContext
from tests.conversation.conversation_ambiguity_fixtures import (
    AVOID_STAIRS,
    DEST_REQUIRED_MARKER,
    FORBIDDEN_ROUTE_SURFACE,
    NOW_ET,
    PROVIDER_ROUTE,
    ROUTE_TOOL_PROFILE,
    SCAN_PAYLOAD,
    inaccessible_leg,
)
from tests.conversation.conversation_matrix_harness import (
    _turn_round,
    clear_caches,
    new_session,
    policy_model,
    route_cards,
    run_turn,
    seed_accepted_active_trip,
    text_round,
)
from tests.conversation.conversation_matrix_harness import make_leg


@dataclasses.dataclass(frozen=True)
class ExecutorProbe:
    """One direct real-executor probe result plus immutable store evidence."""

    result: object
    stored_set_ids: tuple[str, ...]
    state: dict


class _E3Base(unittest.IsolatedAsyncioTestCase):
    """Shared invariants for the Batch E3 conversation audits."""

    loop = None  # set in setUpClass by subclasses

    def setUp(self):
        clear_caches()

    # ------------------------------------------------------------------
    # Session/turn plumbing
    # ------------------------------------------------------------------

    def _new_session(self, mode: str) -> tuple[str, dict]:
        session_id = f"sess-e3-{mode}-{secrets.token_hex(4)}"
        _sid, session = new_session()
        return session_id, session

    def _seed_accepted(self, mode: str):
        session_id, session = self._new_session(mode)
        seed = seed_accepted_active_trip(session, session_id)
        return session, session_id, seed

    async def _scripted_turn(
        self, *, session, session_id, message, rounds, mode, turn_id,
        prepare_leg=None, fixed_candidate_id=None, mocks=None,
    ):
        trace = self.loop.TurnTrace()
        mocks = {} if mocks is None else mocks
        events, trace = await run_turn(
            self.loop, session=session, session_id=session_id, message=message,
            rounds=rounds, mode=mode, trace=trace, mocks=mocks, turn_id=turn_id,
            prepare_leg=prepare_leg, fixed_candidate_id=fixed_candidate_id,
        )
        return events, trace, mocks

    async def _no_tool_turn(self, *, session, session_id, message, mode, turn_id):
        return await self._scripted_turn(
            session=session, session_id=session_id, message=message,
            rounds=[text_round("I need more context for that.")],
            mode=mode, turn_id=turn_id,
        )

    async def _failed_prepare_turn(
        self, *, session, session_id, message, mode, turn_id,
        prepare_input=None, prepare_leg=None,
    ):
        rounds = [
            _turn_round("prepare_route_options", "tu-prep", prepare_input or {}),
            text_round("I need more information for that."),
        ]
        return await self._scripted_turn(
            session=session, session_id=session_id, message=message,
            rounds=rounds, mode=mode, turn_id=turn_id, prepare_leg=prepare_leg,
        )

    async def _no_context_noop_turn(
        self, *, session, session_id, message, mode, turn_id, scenario_id,
        pristine=True, offered=None,
    ):
        """One text-only turn with no route surface; state provably unchanged."""

        before = self._snapshot(session)
        events, trace, mocks = await self._no_tool_turn(
            session=session, session_id=session_id, message=message,
            mode=mode, turn_id=turn_id)
        if offered is not None:
            self.assertEqual(self._offered(), offered,
                             f"{scenario_id} offered={sorted(self._offered())}")
        self.assertEqual(self._names(trace), [], f"{scenario_id} nothing executes")
        self._assert_no_route_surface(scenario_id, trace, events, mocks)
        if pristine:
            self._assert_pristine_route_state(
                scenario_id, trip_state_module.get_trip_state(session))
        self._assert_snapshot_unchanged(scenario_id, before, self._snapshot(session))
        self._assert_policy(mode, trace, scenario_id)
        return events, trace, mocks

    async def _origin_only_scenario(self, *, mode, message, scenario_id):
        """Origin-only route turn: bounded destination-missing failure."""

        session_id, session = self._new_session(mode)
        events, trace, mocks = await self._failed_prepare_turn(
            session=session, session_id=session_id, message=message, mode=mode,
            turn_id="t1", prepare_input={"origin": "Home"},
            prepare_leg=make_leg(destination="Work"))
        self.assertEqual(self._offered(), ROUTE_TOOL_PROFILE,
                         f"{scenario_id} route profile offered")
        self._assert_failed_prepare(
            scenario_id=scenario_id, events=events, trace=trace, mocks=mocks,
            marker=DEST_REQUIRED_MARKER, provider_not_reached=True)
        state = trip_state_module.get_trip_state(session)
        self.assertEqual(state["destination"], None,
                         f"{scenario_id} no destination")
        self.assertEqual(session["pending_trip"]["status"], "failed",
                         f"{scenario_id} pending trip records the bounded failure")
        self._assert_policy(mode, trace, scenario_id)

    async def _hard_accessibility_scenario(self, *, mode, scenario_id):
        """Hard accessibility against an incompatible fixture: no winner."""

        session, session_id, seed = self._seed_accepted(mode)
        events, trace, mocks = await self._scripted_turn(
            session=session, session_id=session_id, message=AVOID_STAIRS,
            rounds=[
                _turn_round("prepare_route_options", "tu-access",
                            {"destination": seed.destination}),
                text_round("I could not find a route that meets your constraints."),
            ],
            mode=mode, turn_id="t1",
            prepare_leg=inaccessible_leg(seed.destination))
        self._assert_no_card(events, scenario_id)
        audit = self._assert_audit(
            scenario_id=scenario_id, session_id=session_id, mocks=mocks,
            expected_status="no_hard_constraint_match",
            violations=("accessibility_unknown_or_unavailable",))
        self.assertTrue(audit["tool_input"]["accessibility_required"],
                        f"{scenario_id} hard accessibility enforced")
        self.assertTrue(audit["tool_input"]["avoid_stairs"],
                        f"{scenario_id} stair avoidance enforced")
        self._assert_accepted_preserved(scenario_id=scenario_id, session=session,
                                        seed=seed)

    # ------------------------------------------------------------------
    # Evidence helpers
    # ------------------------------------------------------------------

    def _offered(self) -> frozenset:
        return frozenset(
            schema["name"]
            for schema in self.loop.client.messages.calls[0]["tools"]
        )

    def _tool_ends(self, events: list) -> dict:
        return {
            event.tool: (event.ok, event.summary)
            for event in events
            if event.type == "tool_end"
        }

    def _names(self, trace) -> list:
        return [name for name, _tool_input in trace.tool_calls]

    def _assert_meta_done(self, events: list, scenario_id: str) -> None:
        self.assertEqual(events[0].type, "meta", f"{scenario_id} meta first")
        self.assertEqual(events[-1].type, "done", f"{scenario_id} done last")

    def _assert_no_card(self, events: list, scenario_id: str) -> None:
        self.assertEqual(route_cards(events), [], f"{scenario_id} no route card")
        self._assert_meta_done(events, scenario_id)

    def _assert_no_candidate_sets(self, mocks: dict, scenario_id: str) -> None:
        self.assertEqual(
            mocks["stored_candidate_set_ids"], [],
            f"{scenario_id} no candidate set stored",
        )

    def _assert_provider_not_reached(self, mocks: dict, scenario_id: str) -> None:
        prepare = mocks["prepare_single_leg"]
        self.assertTrue(
            prepare is None or prepare.await_count == 0,
            f"{scenario_id} provider route seam must not be reached",
        )

    def _assert_policy(self, mode: str, trace, scenario_id: str) -> None:
        expected_mode, expected_model = policy_model(self.loop, mode)
        self.assertEqual((trace.initial_mode, trace.final_mode),
                         (expected_mode, expected_mode), f"{scenario_id} mode")
        self.assertEqual(self.loop.client.messages.calls[0]["model"],
                         expected_model, f"{scenario_id} model")

    def _snapshot(self, session: dict) -> dict:
        state = trip_state_module.get_trip_state(session)
        state.pop("updated_at", None)  # volatile wall-clock, not semantic
        return {
            "state": dict(state),
            "active_trip_card": (
                (session.get("active_trip") or {}).get("card_id")
                if session.get("active_trip") else None
            ),
            "route_card_ids": [
                card.get("card_id") for card in session.get("route_cards") or []
            ],
        }

    def _assert_snapshot_unchanged(
        self, scenario_id: str, before: dict, after: dict
    ) -> None:
        self.assertEqual(before["state"], after["state"], f"{scenario_id} trip state")
        self.assertEqual(before["active_trip_card"], after["active_trip_card"],
                         f"{scenario_id} active trip")
        self.assertEqual(before["route_card_ids"], after["route_card_ids"],
                         f"{scenario_id} route cards")

    def _assert_pristine_route_state(self, scenario_id: str, state: dict) -> None:
        self.assertEqual((state["origin"], state["destination"], state["waypoints"]),
                         (None, None, []), f"{scenario_id} route facts untouched")
        self.assertEqual(
            (state["active_candidate_set_id"], state["selected_candidate_id"],
             state["temporary_candidate_set_id"],
             state["temporary_selected_candidate_id"]),
            (None, None, None, None),
            f"{scenario_id} candidate/scenario fields untouched",
        )

    def _assert_no_route_surface(self, scenario_id: str, trace, events, mocks) -> None:
        names = self._names(trace)
        for forbidden in FORBIDDEN_ROUTE_SURFACE:
            self.assertNotIn(forbidden, names,
                             f"{scenario_id} forbidden tool: {forbidden}")
        self._assert_no_card(events, scenario_id)
        self._assert_no_candidate_sets(mocks, scenario_id)
        self._assert_provider_not_reached(mocks, scenario_id)

    def _assert_failed_prepare(
        self, *, scenario_id, events, trace, mocks, marker,
        provider_not_reached=False,
    ):
        self.assertEqual(self._names(trace), ["prepare_route_options"],
                         f"{scenario_id} tool sequence")
        ends = self._tool_ends(events)
        ok, summary = ends["prepare_route_options"]
        self.assertFalse(ok, f"{scenario_id} prepare must fail safely")
        self.assertEqual(
            summary,
            "Route options could not be prepared",
            f"{scenario_id} rider-safe bounded failure",
        )
        self.assertNotIn(marker, summary or "", f"{scenario_id} hides diagnostics")
        self._assert_no_card(events, scenario_id)
        self._assert_no_candidate_sets(mocks, scenario_id)
        if provider_not_reached:
            self._assert_provider_not_reached(mocks, scenario_id)

    def _assert_audit(
        self, *, scenario_id, session_id, mocks, expected_status, violations=(),
    ):
        """Load the one stored audit set; assert status and hard violations."""

        self.assertEqual(len(mocks["stored_candidate_set_ids"]), 1,
                         f"{scenario_id} one audit set")
        audit = candidate_store.load_candidate_set(
            mocks["stored_candidate_set_ids"][0], session_id=session_id)
        self.assertIsNotNone(audit, f"{scenario_id} audit record")
        self.assertEqual(audit["route_status"], expected_status,
                         f"{scenario_id} audit status")
        for violation in violations:
            self.assertIn(
                violation,
                audit["candidates"][0]["digest"]["hard_constraint_violations"],
                f"{scenario_id} violation",
            )
        return audit

    def _assert_accepted_preserved(
        self, *, scenario_id, session, seed
    ) -> None:
        state = trip_state_module.get_trip_state(session)
        self.assertEqual(state["active_candidate_set_id"], seed.candidate_set_id,
                         f"{scenario_id} accepted set preserved")
        self.assertEqual(state["selected_candidate_id"], seed.candidate_id,
                         f"{scenario_id} accepted selection preserved")
        self.assertEqual(session["active_trip"]["card_id"], seed.card_id,
                         f"{scenario_id} accepted card preserved")

    # ------------------------------------------------------------------
    # Expired discovery-set clock (deterministic, no sleep)
    # ------------------------------------------------------------------

    @contextmanager
    def _expired_clock(self, record: dict):
        with patch("app.services.agent.discovery_store.time.time",
                   return_value=float(record["expires_at"]) + 60.0):
            yield

    def _bind_discovery_set(self, session_id: str, session: dict) -> tuple[str, dict]:
        set_id = discovery_store.store_discovery_set(
            session_id=session_id,
            places=[
                {"name": "A Pizza", "latitude": 40.71, "longitude": -73.98},
                {"name": "B Pizza", "latitude": 40.72, "longitude": -73.97},
            ],
            query="pizza",
        )
        trip_state_module.bind_discovery_set(session, set_id)
        return set_id, discovery_store.load_discovery_set(set_id, session_id=session_id)

    # ------------------------------------------------------------------
    # Direct real-executor time-validation probe
    # ------------------------------------------------------------------

    def _probe_ctx(self, session: dict, session_id: str) -> ToolContext:
        return ToolContext(
            session=session, session_id=session_id, turn_id="t-e3-probe",
            now_et=NOW_ET, origin={"lat": 40.75, "lng": -73.99},
        )

    @staticmethod
    async def _resolve_probe_place(value, _ctx, *, missing_location_message):
        if str(value or "").strip().lower() in {"", "user"}:
            return ResolvedPlace("Your location", 40.75, -73.99, "user"), None
        return ResolvedPlace("Work", 40.6826, -73.9754, "fallback"), None

    async def _executor_probe(
        self, tool_input: dict, *, derive_error: BaseException | None = None,
    ) -> ExecutorProbe:
        """Run the real executor with real validation; only seams scripted."""

        session_id, session = new_session()
        stored: list[str] = []
        original_store = candidate_store.store_candidate_set

        def _recording_store(*args, **kwargs):
            set_id = original_store(*args, **kwargs)
            stored.append(set_id)
            return set_id

        derive = AsyncMock(return_value=None)
        if derive_error is not None:
            derive = AsyncMock(side_effect=derive_error)
        patch_targets = [
            ("app.services.agent.tools.route.preparation_adapter.resolve_named_place",
             AsyncMock(side_effect=self._resolve_probe_place)),
            ("app.services.agent.tools.route.preparation_adapter.route_with_recovery",
             AsyncMock(return_value=PROVIDER_ROUTE)),
            ("app.services.agent.tools.route.preparation_adapter."
             "derive_arrive_by_departure", derive),
            ("app.services.mta.realtime.fetch_service_alerts",
             AsyncMock(return_value=[])),
            ("app.services.mta.realtime.get_stalled_trains",
             AsyncMock(return_value=[])),
            ("app.services.mta.realtime.get_stalled_buses",
             AsyncMock(return_value=[])),
            ("app.services.mta.realtime.parse_service_alerts", lambda raw: []),
            ("app.services.mta.realtime.filter_alerts_for_routes",
             lambda alerts, route_ids: []),
            ("app.services.trips.route_incidents.scan.scan_route_incidents",
             AsyncMock(return_value=SCAN_PAYLOAD)),
            ("app.services.agent.candidate_store.store_candidate_set",
             _recording_store),
        ]
        patchers = [patch(target, new=value) for target, value in patch_targets]
        for patcher in patchers:
            patcher.start()
        try:
            result = await prepare_route_options.execute(
                tool_input, self._probe_ctx(session, session_id))
        finally:
            for patcher in patchers:
                patcher.stop()
        return ExecutorProbe(
            result=result, stored_set_ids=tuple(stored),
            state=dict(trip_state_module.get_trip_state(session)),
        )

    def _assert_probe_rejected(
        self, probe: ExecutorProbe, marker: str, scenario_id: str
    ) -> None:
        self.assertFalse(probe.result.ok, f"{scenario_id} must be rejected")
        self.assertIn(marker, probe.result.error or "",
                      f"{scenario_id} error={probe.result.error!r}")
        self.assertEqual(probe.stored_set_ids, (),
                         f"{scenario_id} no candidate set stored")
        self.assertEqual(probe.state["destination"], None,
                         f"{scenario_id} no destination")
        self.assertEqual(
            (probe.state["active_candidate_set_id"],
             probe.state["selected_candidate_id"]),
            (None, None),
            f"{scenario_id} no candidate/selection bound",
        )

    def _assert_probe_presentable(
        self, probe: ExecutorProbe, scenario_id: str
    ) -> None:
        self.assertTrue(probe.result.ok, f"{scenario_id} prepare must succeed")
        self.assertTrue(probe.result.data.get("presentation_allowed") is True,
                        f"{scenario_id} presentation allowed")
        self.assertEqual(probe.result.data.get("route_status"), "good",
                         f"{scenario_id} status")
        self.assertEqual(len(probe.stored_set_ids), 1,
                         f"{scenario_id} one stored set")

    # ------------------------------------------------------------------
    # E3-C: one canonical scripted-ISO route turn (prepare + present)
    # ------------------------------------------------------------------

    async def _iso_departure_route_turn(
        self, *, mode, scenario_id, message, departure_iso,
        fixed_candidate_id,
    ):
        session_id, session = self._new_session(mode)
        prepare_input = {"destination": "Work"}
        if departure_iso is not None:
            prepare_input["departure_time"] = departure_iso
        rounds = [
            _turn_round("prepare_route_options", "tu-prep", prepare_input),
            _turn_round("present_route", "tu-pres",
                        {"candidate_id": fixed_candidate_id}),
            text_round("Here is the route."),
        ]
        events, trace, mocks = await self._scripted_turn(
            session=session, session_id=session_id, message=message,
            rounds=rounds, mode=mode, turn_id="t1",
            prepare_leg=make_leg(destination="Work"),
            fixed_candidate_id=fixed_candidate_id,
        )
        self.assertEqual(self._names(trace),
                         ["prepare_route_options", "present_route"],
                         f"{scenario_id} canonical chain")
        self.assertNotIn("web_search", self._offered(),
                         f"{scenario_id} route planning never gets web search")
        cards = route_cards(events)
        self.assertEqual(len(cards), 1, f"{scenario_id} one recommended card")
        self.assertEqual(cards[0].role, "recommended", f"{scenario_id}")
        state = trip_state_module.get_trip_state(session)
        self.assertEqual(state["selected_candidate_id"], fixed_candidate_id,
                         f"{scenario_id} candidate committed")
        if departure_iso is None:
            self.assertEqual(state["planning_mode"], "leave_now", f"{scenario_id}")
            self.assertEqual(state["requested_departure"], None, f"{scenario_id}")
        else:
            self.assertEqual(state["planning_mode"], "depart_at", f"{scenario_id}")
            self.assertEqual(state["requested_departure"], departure_iso,
                             f"{scenario_id} canonical departure persisted")
            self.assertEqual(cards[0].depart_iso, departure_iso,
                             f"{scenario_id} card departure is the server ISO")
            self.assertEqual((session.get("slots") or {}).get("time_anchor"),
                             departure_iso, f"{scenario_id} time anchor")
        self._assert_policy(mode, trace, scenario_id)
        return events, trace, mocks


__all__ = ("ExecutorProbe", "_E3Base")
