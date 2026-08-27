"""Batch E2 audit support: candidate/reference identity safety through the
real canonical conversation loop.

Non-test module (no ``Test*``/``test_*`` names at module level): pytest never
collects it. Drives the real agent loop (``loop.run_agent_turn``) with the
production state-scoped tool surface, real registry/executors, real
candidate/trip/session/scenario stores, real prompt context, real ledger,
and real SSE events. Only deterministic Anthropic rounds and the documented
provider/data seams of ``tests.conversation.conversation_matrix_harness`` are scripted;
set expiry goes through the real store's TTL boundary by advancing the
candidate-store clock deterministically (no sleep, no network).

Every loop test asserts the OFFERED tool profile before crediting any
scripted tool state, so a scripted unoffered tool can never create a false
pass. Real ``cd_*``/``cs_*`` ids are always read back from the real store;
invented and provider/raw-shaped ids appear only in the explicit
malicious-input cases. Immutable before/after projections (trip state,
active trip, route cards, stored candidate-set record) prove rejected
attempts mutate nothing.
"""

from __future__ import annotations

import copy
import dataclasses
import secrets
import unittest
from contextlib import contextmanager
from unittest.mock import patch

from app.services.agent import candidate_store
from app.services.agent import trip_state as trip_state_module

from tests.conversation.conversation_candidate_reference_fixtures import (
    CANDIDATE_SET_UNKNOWN_MARKER,
    CANDIDATE_UNKNOWN_MARKER,
    CANDIDATE_V1,
    CANDIDATE_V2,
    CHANGE_ROUTE_MESSAGE,
    LEAK_MARKERS,
    REPLAN_MESSAGE,
    ROUTE_MESSAGE,
    ROUTE_NAVIGATION_TOOL_PROFILE,
)
from tests.conversation.conversation_matrix_harness import (
    _turn_round,
    clear_caches,
    make_leg,
    new_session,
    policy_model,
    route_cards,
    run_turn,
    text_round,
)


@dataclasses.dataclass(frozen=True)
class TurnSnapshot:
    """One loop turn's evidence, captured before the next turn resets mocks."""

    events: list
    trace: object
    mocks: dict
    state: dict
    offered: frozenset
    context: str
    result_blob: str
    models: tuple


class _CandidateReferenceBase(unittest.IsolatedAsyncioTestCase):
    """Shared invariants for the Batch E2 candidate/reference safety cases."""

    loop = None  # set in setUpClass by subclasses

    def setUp(self):
        clear_caches()

    def _new_session(self, mode: str) -> tuple[str, dict]:
        session_id = f"sess-e2-{mode}-{secrets.token_hex(4)}"
        _sid, session = new_session()
        return session_id, session

    def _tool_ctx(self, session: dict, session_id: str):
        from app.services.agent.tools._types import ToolContext

        return ToolContext(
            session=session,
            session_id=session_id,
            turn_id="t-e2",
            now_et="2026-08-08T12:00:00-04:00",
            origin={"lat": 40.75, "lng": -73.99},
        )

    @contextmanager
    def _expired_candidate_clock(self, record: dict):
        """Advance the real candidate-store clock past ``expires_at``."""

        with patch(
            "app.services.agent.candidate_store.time.time",
            return_value=float(record["expires_at"]) + 60.0,
        ):
            yield

    async def _natural_route_turn(
        self,
        *,
        mode: str,
        scenario_id: str,
        session: dict,
        session_id: str,
        destination: str,
        candidate_id: str = CANDIDATE_V1,
        turn_id: str = "t1",
    ) -> tuple[str, dict]:
        """One real natural prepare -> present turn that commits a route.

        Asserts the full canonical acceptance contract and returns the
        committed (candidate_set_id, store record).
        """

        rounds = [
            _turn_round(
                "prepare_route_options",
                f"tu-prep-{turn_id}",
                {"destination": destination},
            ),
            _turn_round(
                "present_route",
                f"tu-pres-{turn_id}",
                {"candidate_id": candidate_id},
            ),
        ]
        ev = await self._scripted_turn(
            mode=mode,
            session=session,
            session_id=session_id,
            message=ROUTE_MESSAGE,
            rounds=rounds,
            turn_id=turn_id,
            prepare_leg=make_leg(destination=destination),
            fixed_candidate_id=candidate_id,
        )
        names = [name for name, _input in ev.trace.tool_calls]
        assert ev.offered == ROUTE_NAVIGATION_TOOL_PROFILE, f"{scenario_id} natural routing offers the canonical route profile; " f"actual={sorted(ev.offered)}; executed={names}"
        assert names == ["prepare_route_options", "present_route"], f"{scenario_id} natural routing sequence"
        assert len(route_cards(ev.events)) == 1, f"{scenario_id} natural routing emits exactly one card"
        set_id = ev.state["active_candidate_set_id"]
        assert set_id
        assert set_id.startswith("cs_"), f"{scenario_id} natural routing binds a real candidate set"
        assert ev.state["selected_candidate_id"] == candidate_id, f"{scenario_id} natural routing commits the selection"
        assert len(ev.mocks["stored_candidate_set_ids"]) == 1, f"{scenario_id} natural routing stores one candidate set"
        assert ev.mocks["stored_candidate_set_ids"][0] == set_id, f"{scenario_id} stored set is the bound set"
        record = candidate_store.load_candidate_set(set_id, session_id=session_id)
        assert record is not None, f"{scenario_id} stored candidate record"
        assert record["presented"], f"{scenario_id} accepted set is presented"
        assert record["selected_candidate_id"] == candidate_id, f"{scenario_id} accepted set records the selection"
        assert len(session.get("route_cards") or []) == 1, f"{scenario_id} one persisted route card"
        assert session.get("active_trip") is not None, f"{scenario_id} accepted active trip bound"
        self._assert_policy(scenario_id, mode, ev)
        self._assert_meta_done(scenario_id, ev)
        return set_id, record

    async def _scripted_turn(
        self,
        *,
        mode: str,
        session: dict,
        session_id: str,
        message: str,
        rounds: list,
        turn_id: str,
        prepare_leg=None,
        fixed_candidate_id: str | None = None,
    ) -> TurnSnapshot:
        """Run one real loop turn and snapshot the offered/executed contract."""

        trace = self.loop.TurnTrace()
        mocks: dict = {}
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
            prepare_leg=prepare_leg,
            fixed_candidate_id=fixed_candidate_id,
        )
        state = dict(trip_state_module.get_trip_state(session))
        offered = frozenset(
            schema["name"] for schema in self.loop.client.messages.calls[0]["tools"]
        )
        context = str(self.loop.client.messages.calls[0]["messages"][-1]["content"])
        result_blob = ""
        if len(self.loop.client.messages.calls) >= 2:
            result_blob = str(
                self.loop.client.messages.calls[1]["messages"][-1]["content"]
            )
        models = tuple(call["model"] for call in self.loop.client.messages.calls)
        return TurnSnapshot(
            events=events,
            trace=trace,
            mocks=mocks,
            state=state,
            offered=offered,
            context=context,
            result_blob=result_blob,
            models=models,
        )

    def _tool_ends(self, ev: TurnSnapshot) -> dict:
        return {
            event.tool: (event.ok, event.summary)
            for event in ev.events
            if event.type == "tool_end"
        }

    def _snapshot_session(self, session: dict) -> dict:
        """Immutable server-state projection (never a live dict reference)."""

        return {
            "trip_state": copy.deepcopy(trip_state_module.get_trip_state(session)),
            "active_trip": copy.deepcopy(session.get("active_trip")),
            "route_cards": copy.deepcopy(session.get("route_cards") or []),
        }

    def _snapshot_record(self, set_id: str, session_id: str) -> dict | None:
        record = candidate_store.load_candidate_set(set_id, session_id=session_id)
        return copy.deepcopy(record) if record is not None else None

    def _assert_meta_done(self, scenario_id: str, ev: TurnSnapshot) -> None:
        assert ev.events[0].type == "meta", f"{scenario_id} meta first"
        assert ev.events[-1].type == "done", f"{scenario_id} done last"

    def _assert_policy(self, scenario_id: str, mode: str, ev: TurnSnapshot) -> None:
        expected_mode, expected_model = policy_model(self.loop, mode)
        assert (ev.trace.initial_mode, ev.trace.final_mode) == (expected_mode, expected_mode), f"{scenario_id} policy mode"
        assert list(ev.models) == [expected_model] * len(ev.models), f"{scenario_id} policy models; actual={list(ev.models)}"

    def _assert_no_text_leak(self, scenario_id: str, ev: TurnSnapshot) -> None:
        lowered = ev.trace.final_text.casefold()
        for marker in LEAK_MARKERS:
            assert marker not in lowered, f"{scenario_id} rider text leaked {marker}"

    def _assert_rejected_present(
        self,
        *,
        scenario_id: str,
        ev: TurnSnapshot,
        marker: str,
        session_before: dict,
        record_before: dict | None,
        set_id: str,
        session_id: str,
        session: dict,
        forbidden_provider: bool = True,
    ) -> None:
        """Shared bounded-rejection invariants for a failed present_route."""

        names = [name for name, _input in ev.trace.tool_calls]
        end_map = self._tool_ends(ev)
        present_end = next(
            (event for event in ev.events
             if event.type == "tool_end" and event.tool == "present_route"),
            None,
        )
        assert present_end is not None, (
            f"{scenario_id} present_route must fail bounded; "
            f"executed={names}; tool_ends={end_map}"
        )
        assert present_end.ok is False, (
            f"{scenario_id} present_route must fail bounded; "
            f"executed={names}; tool_ends={end_map}"
        )
        assert (present_end.summary if present_end else None) == "The prepared route could not be shown", f"{scenario_id} rider-safe bounded identity failure"
        assert marker not in (present_end.summary or "" if present_end else ""), f"{scenario_id} hides internal identity diagnostics"
        assert route_cards(ev.events) == [], f"{scenario_id} no route card on a rejected present"
        assert len(ev.mocks["stored_candidate_set_ids"]) == 0, f"{scenario_id} no candidate set stored on a rejected " f"present; actual={ev.mocks['stored_candidate_set_ids']}"
        assert self._snapshot_session(session) == session_before, f"{scenario_id} rejected present mutates no session state"
        assert self._snapshot_record(set_id, session_id) == record_before, f"{scenario_id} rejected present mutates no store record"
        prepare = ev.mocks["prepare_single_leg"]
        if forbidden_provider:
            assert prepare is None or prepare.await_count == 0, f"{scenario_id} provider route seam must not be reached; " f"actual_await_count={prepare.await_count if prepare is not None else None}"
        self._assert_meta_done(scenario_id, ev)
        self._assert_no_text_leak(scenario_id, ev)

    def _assert_no_candidate_authoring(
        self,
        *,
        scenario_id: str,
        ev: TurnSnapshot,
        expected_execution_count: int = 0,
    ) -> None:
        """Candidate/reference-like rider text never authors a candidate."""

        names = [name for name, _input in ev.trace.tool_calls]
        assert route_cards(ev.events) == [], f"{scenario_id} no route card"
        assert ev.mocks["stored_candidate_set_ids"] == [], f"{scenario_id} no candidate set stored; " f"actual={ev.mocks['stored_candidate_set_ids']}"
        assert ev.trace.provider_tool_execution_count == expected_execution_count, f"{scenario_id} bounded tool execution count; executed={names}"
        for key in (
            "origin", "destination", "waypoints",
            "active_candidate_set_id", "selected_candidate_id",
            "temporary_candidate_set_id", "temporary_selected_candidate_id",
            "temporary_base_candidate_set_id",
        ):
            assert ev.state[key] == (None if key != "waypoints" else []), f"{scenario_id} trip-state [{key}] untouched"
        self._assert_meta_done(scenario_id, ev)
        self._assert_no_text_leak(scenario_id, ev)

    async def _rejected_present_turn(
        self,
        *,
        mode: str,
        scenario_id: str,
        session: dict,
        session_id: str,
        message: str,
        candidate_id: str,
        marker: str,
        set_id: str,
        turn_id: str,
    ) -> TurnSnapshot:
        """Scripted ``present_route(candidate_id)`` fails bounded, no mutation.

        Asserts the exact offered route profile before crediting the scripted
        tool, that only the real present executor ran, the bounded rejection
        marker, zero cards, zero store writes, and identical before/after
        session and store-record projections.
        """

        rounds = [
            _turn_round(
                "present_route",
                f"tu-{turn_id}",
                {"candidate_id": candidate_id},
            ),
            text_round("I can only present a server-issued prepared candidate."),
        ]
        session_before = self._snapshot_session(session)
        record_before = self._snapshot_record(set_id, session_id)
        ev = await self._scripted_turn(
            mode=mode, session=session, session_id=session_id,
            message=message, rounds=rounds, turn_id=turn_id)
        assert ev.offered == ROUTE_NAVIGATION_TOOL_PROFILE, f"{scenario_id} route profile; actual={sorted(ev.offered)}"
        assert [name for name, _input in ev.trace.tool_calls] == ["present_route"], f"{scenario_id} only the real present executor runs"
        self._assert_rejected_present(
            scenario_id=scenario_id, ev=ev, marker=marker,
            session_before=session_before, record_before=record_before,
            set_id=set_id, session_id=session_id, session=session)
        return ev

    async def case3_expired_recovery(self, mode: str) -> None:
        """E2-CASE3: expiry fails safely; recovery re-issues a new candidate."""

        s = f"E2C3-{mode}"
        session_id, session = self._new_session(mode)
        set_id, record = await self._natural_route_turn(
            mode=mode, scenario_id=f"{s}-t1", session=session,
            session_id=session_id, destination="Work",
            candidate_id=CANDIDATE_V1, turn_id="t1")
        session_before = self._snapshot_session(session)
        record_before = self._snapshot_record(set_id, session_id)
        rounds = [
            _turn_round("present_route", "tu-exp",
                        {"candidate_id": CANDIDATE_V1}),
            text_round("That option is no longer available."),
        ]
        with self._expired_candidate_clock(record):
            ev = await self._scripted_turn(
                mode=mode, session=session, session_id=session_id,
                message=CHANGE_ROUTE_MESSAGE, rounds=rounds, turn_id="t2")
            assert [name for name, _input in ev.trace.tool_calls] == ["present_route"], f"{s} expired follow-up runs the real executor"
            assert candidate_store.load_candidate_set(set_id, session_id=session_id) is None, f"{s} expired set loads nothing under the store clock"
            probe = candidate_store.get_candidate(
                set_id, CANDIDATE_V1, session_id=session_id)
            assert probe[0] is None, f"{s} expired identity never reactivates"
            assert probe[2] is not None, f"{s} expired identity never reactivates"
            assert "expired" in probe[2], f"{s} probe names expiry"
        # Store-record snapshots run under the real clock; the loop rejection
        # evidence itself is clock-independent.
        self._assert_rejected_present(
            scenario_id=f"{s}-expired", ev=ev,
            marker=CANDIDATE_SET_UNKNOWN_MARKER,
            session_before=session_before, record_before=record_before,
            set_id=set_id, session_id=session_id, session=session)
        with self._expired_candidate_clock(record):
            rounds = [
                _turn_round("prepare_route_options", "tu-recover",
                            {"destination": "Coney Island"}),
                _turn_round("present_route", "tu-recover-present",
                            {"candidate_id": CANDIDATE_V2}),
            ]
            ev = await self._scripted_turn(
                mode=mode, session=session, session_id=session_id,
                message=REPLAN_MESSAGE, rounds=rounds, turn_id="t3",
                prepare_leg=make_leg(destination="Coney Island"),
                fixed_candidate_id=CANDIDATE_V2)
            assert [name for name, _input in ev.trace.tool_calls] == ["prepare_route_options", "present_route"], f"{s} recovery runs the canonical chain"
            assert len(route_cards(ev.events)) == 1, f"{s} recovery emits exactly one card"
            new_set_id = ev.state["active_candidate_set_id"]
            assert new_set_id
            assert new_set_id.startswith("cs_"), f"{s} recovery issues a new server set"
            assert new_set_id != set_id, f"{s} recovery set is fresh"
            assert ev.state["selected_candidate_id"] == CANDIDATE_V2, f"{s} recovery commits the new candidate"
            new_record = candidate_store.load_candidate_set(
                new_set_id, session_id=session_id)
            assert new_record["presented"], f"{s} recovery set presented"
            assert new_record["selected_candidate_id"] == CANDIDATE_V2, f"{s} recovery set records selection"
            rounds = [
                _turn_round("present_route", "tu-old-again",
                            {"candidate_id": CANDIDATE_V1}),
                text_round("The old option stays unavailable."),
            ]
            # Immutable projections captured BEFORE the t4 probe turn; the
            # after-turn comparison then proves the probe mutated nothing.
            t4_session_before = self._snapshot_session(session)
            t4_record_before = self._snapshot_record(new_set_id, session_id)
            ev = await self._scripted_turn(
                mode=mode, session=session, session_id=session_id,
                message=CHANGE_ROUTE_MESSAGE, rounds=rounds, turn_id="t4")
            self._assert_rejected_present(
                scenario_id=f"{s}-never-reactivates", ev=ev,
                marker=CANDIDATE_UNKNOWN_MARKER,
                session_before=t4_session_before,
                record_before=t4_record_before,
                set_id=new_set_id, session_id=session_id, session=session)
            assert trip_state_module.get_trip_state(session)["selected_candidate_id"] == CANDIDATE_V2, f"{s} recovery selection survives the old probe"


__all__ = ("TurnSnapshot", "_CandidateReferenceBase")
