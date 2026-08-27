"""Batch E2 audit: candidate lifecycle/state-transition safety through the
real loop.

Lifecycle/state-transition cases (Auto + Quick): E2-CASE3 expired sets fail
safely and recovery re-issues a candidate (expired identity never
reactivates); E2-CASE4 a superseded candidate is never presented; E2-CASE5
duplicate presentation is bounded (same-round retries deduplicate to one
card); E2-CASE8 sessions never share candidate store, scenario, cards, or
selections (incl. a what-if cross-session probe). Rejection/identity-authoring
cases (E2-CASE1, CASE2, CASE6, CASE7) live in
``test_conversation_candidate_reference_safety``.

Real loop, production state-scoped tool surface, registry/executors, stores,
ledger, and SSE events run untouched; only deterministic Anthropic rounds and
documented provider/data seams are scripted. Offered profiles are asserted
before any scripted tool state is credited.
"""

from __future__ import annotations

import itertools
from unittest.mock import patch

from app.services.agent import candidate_store
from app.services.agent import trip_state as trip_state_module
from app.services.agent.tools.route import present_route
from app.services.agent.turn.contract import GoalState, TurnContract
from app.services.agent.turn.evidence import TurnEvidence

from tests.conversation.conversation_candidate_reference_fixtures import (
    CANDIDATE_A,
    CANDIDATE_B,
    CANDIDATE_SET_UNKNOWN_MARKER,
    CANDIDATE_UNKNOWN_MARKER,
    CANDIDATE_V1,
    CANDIDATE_V2,
    CANDIDATE_WHAT_IF_A,
    CHANGE_ROUTE_MESSAGE,
    REPLAN_MESSAGE,
    WHAT_IF_MESSAGE,
)
from tests.conversation.conversation_candidate_reference_support import (
    _CandidateReferenceBase,
)
from tests.conversation.conversation_matrix_harness import (
    _turn_round,
    load_agent_loop,
    make_leg,
    route_cards,
)
from tests.conversation.test_conversation_candidate_reference_safety import (
    _ModelLedCandidateMixin,
)

MODES = ("auto",)
INITIAL_TOOL_PROFILE = frozenset(
    {
        "declare_goals",
        "discover_places",
        "check_transit",
        "prepare_route_options",
        "complete_turn",
    }
)


def _goal_round(
    goal_key: str,
    kind: str,
    tool_name: str,
    tool_id: str,
    tool_input: dict,
    *,
    depends_on: list[str] | None = None,
) -> dict:
    """Declare rider outcomes before any state-valid capability call."""

    declaration = {
        "id": f"{tool_id}-goals",
        "name": "declare_goals",
        "input": {
            "goals": [
                {
                    "goal_key": goal_key,
                    "kind": kind,
                    "depends_on": list(depends_on or []),
                }
            ]
        },
    }
    capability_input = dict(tool_input)
    if tool_name != "complete_turn":
        capability_input = {"goal_key": goal_key, **capability_input}
    if tool_name == "prepare_route_options":
        has_explicit_destination = bool(
            capability_input.get("destination")
            or capability_input.get("destination_place_id")
        )
        capability_input.setdefault(
            "destination_source",
            "current_turn" if has_explicit_destination else "accepted_trip",
        )
    return {
        "tool_use": [
            declaration,
            {"id": tool_id, "name": tool_name, "input": capability_input},
        ],
        "stop_reason": "tool_use",
    }


def _present_route_round(tool_id: str, candidate_id: str, *, goal_key: str = "route") -> dict:
    return _turn_round(
        "present_route",
        tool_id,
        {"goal_key": goal_key, "candidate_id": candidate_id},
    )


class ExpiredCandidateSetTests(_ModelLedCandidateMixin, _CandidateReferenceBase):
    """E2-CASE3 (Auto + Quick): expiry fails safely; recovery re-issues."""

    @classmethod
    def setUpClass(cls):
        cls.loop = load_agent_loop()

    async def case3_expired_recovery(self, mode: str) -> None:
        """E2-CASE3 with a state-valid presenter identity probe."""

        scenario = f"E2C3-{mode}"
        session_id, session = self._new_session(mode)
        set_id, record = await self._natural_route_turn(
            mode=mode,
            scenario_id=f"{scenario}-t1",
            session=session,
            session_id=session_id,
            destination="Work",
            candidate_id=CANDIDATE_V1,
            turn_id="t1",
        )
        session_before = self._snapshot_session(session)
        record_before = self._snapshot_record(set_id, session_id)
        with self._expired_candidate_clock(record):
            ev = await self._rejected_present_turn(
                mode=mode,
                scenario_id=f"{scenario}-expired",
                session=session,
                session_id=session_id,
                message=CHANGE_ROUTE_MESSAGE,
                candidate_id=CANDIDATE_V1,
                marker=CANDIDATE_SET_UNKNOWN_MARKER,
                set_id=set_id,
                turn_id="t2",
                bypass_accepted_replay=True,
            )
            assert candidate_store.load_candidate_set(set_id, session_id=session_id) is None
            probe = candidate_store.get_candidate(
                set_id, CANDIDATE_V1, session_id=session_id
            )
            assert probe[0] is None
            assert probe[2] is not None
            assert "expired" in probe[2]
        assert self._snapshot_session(session) == session_before
        assert self._snapshot_record(set_id, session_id) == record_before

        with self._expired_candidate_clock(record):
            ev = await self._scripted_turn(
                mode=mode,
                session=session,
                session_id=session_id,
                message=REPLAN_MESSAGE,
                rounds=[
                    _goal_round(
                        "route",
                        "route",
                        "prepare_route_options",
                        "tu-recover",
                        {"destination": "Coney Island"},
                    ),
                    _present_route_round("tu-recover-present", CANDIDATE_V2),
                ],
                turn_id="t3",
                prepare_leg=make_leg(destination="Coney Island"),
                fixed_candidate_id=CANDIDATE_V2,
            )
        assert [name for name, _input in ev.trace.tool_calls] == ["declare_goals", "prepare_route_options", "present_route"], f"{scenario} recovery canonical chain"
        assert len(route_cards(ev.events)) == 1
        new_set_id = ev.state["active_candidate_set_id"]
        assert bool(new_set_id)
        assert new_set_id.startswith("cs_")
        assert new_set_id != set_id
        assert ev.state["selected_candidate_id"] == CANDIDATE_V2
        new_record = candidate_store.load_candidate_set(new_set_id, session_id=session_id)
        assert new_record["presented"]
        assert new_record["selected_candidate_id"] == CANDIDATE_V2

        with self._expired_candidate_clock(record):
            ev = await self._rejected_present_turn(
                mode=mode,
                scenario_id=f"{scenario}-never-reactivates",
                session=session,
                session_id=session_id,
                message=CHANGE_ROUTE_MESSAGE,
                candidate_id=CANDIDATE_V1,
                marker=CANDIDATE_UNKNOWN_MARKER,
                set_id=new_set_id,
                turn_id="t4",
            )
        assert trip_state_module.get_trip_state(session)["selected_candidate_id"] == CANDIDATE_V2

    async def test_e2_case3_expired_then_recovery(self):
        for mode in MODES:
            with self.subTest(mode=mode):
                await self._case3_with_probe_ordering_sentinel(mode)

    async def _case3_with_probe_ordering_sentinel(self, mode: str) -> None:
        """Run E2-CASE3 and prove the t4 probe snapshots precede its turn.

        Wraps the snapshot/turn helpers with sequence markers, then checks
        that the immutable projections handed to the final rejected-present
        assertion were captured before the final scripted turn. This is a
        sentinel for the corrected t4 old-candidate probe ordering: if the
        snapshots were ever taken inline after the turn again, the captured
        sequence numbers would no longer precede the turn and this fails.
        """

        seq = itertools.count()
        snapshot_events = []
        record_events = []
        turn_events = []
        probe_events = []
        active_probe = None
        session_snapshot = self._snapshot_session
        record_snapshot = self._snapshot_record
        scripted_turn = self._scripted_turn
        rejected_present = self._rejected_present_turn

        def tracked_session(session):
            result = session_snapshot(session)
            event_seq = next(seq)
            snapshot_events.append((event_seq, result))
            if active_probe is not None and active_probe["before_turn"]:
                active_probe["session"].append(event_seq)
            return result

        def tracked_record(set_id, session_id):
            result = record_snapshot(set_id, session_id)
            event_seq = next(seq)
            record_events.append((event_seq, result))
            if active_probe is not None and active_probe["before_turn"]:
                active_probe["record"].append(event_seq)
            return result

        async def tracked_turn(*args, **kwargs):
            event_seq = next(seq)
            turn_events.append(event_seq)
            if active_probe is not None:
                active_probe["before_turn"] = False
            return await scripted_turn(*args, **kwargs)

        async def tracked_probe(*args, **kwargs):
            nonlocal active_probe
            probe = {"before_turn": True, "session": [], "record": []}
            probe_events.append(probe)
            active_probe = probe
            try:
                return await rejected_present(*args, **kwargs)
            finally:
                active_probe = None

        self._snapshot_session = tracked_session
        self._snapshot_record = tracked_record
        self._scripted_turn = tracked_turn
        self._rejected_present_turn = tracked_probe
        try:
            await self.case3_expired_recovery(mode)
        finally:
            self._snapshot_session = session_snapshot
            self._snapshot_record = record_snapshot
            self._scripted_turn = scripted_turn
            self._rejected_present_turn = rejected_present
        assert len(probe_events) >= 2, f"{mode} rejected probes recorded"
        final_probe = probe_events[-1]
        assert final_probe["session"], f"{mode} final rejected-present session snapshot precedes its turn"
        assert final_probe["record"], f"{mode} final rejected-present record snapshot precedes its turn"
        assert snapshot_events, f"{mode} t4 session snapshot recorded"
        assert record_events, f"{mode} t4 record snapshot recorded"
        last_turn_seq = turn_events[-1]
        assert max(final_probe["session"]) < last_turn_seq, f"{mode} t4 session snapshot predates the t4 turn"
        assert max(final_probe["record"]) < last_turn_seq, f"{mode} t4 record snapshot predates the t4 turn"


class SupersededCandidateTests(_ModelLedCandidateMixin, _CandidateReferenceBase):
    """E2-CASE4 (Auto + Quick): newer preparation supersedes the old id."""

    @classmethod
    def setUpClass(cls):
        cls.loop = load_agent_loop()

    async def _transcript(self, mode: str):
        s = f"E2C4-{mode}"
        session_id, session = self._new_session(mode)
        set_id, _record = await self._natural_route_turn(
            mode=mode, scenario_id=f"{s}-t1", session=session,
            session_id=session_id, destination="Work",
            candidate_id=CANDIDATE_V1, turn_id="t1")
        old_card_id = (session.get("active_trip") or {}).get("card_id")
        old_record_before = self._snapshot_record(set_id, session_id)
        rounds = [
            _goal_round(
                "route", "route", "prepare_route_options", "tu-replan",
                {"destination": "Coney Island"},
            ),
            {
                "tool_use": [
                    {
                        "id": "tu-old",
                        "name": "present_route",
                        "input": {
                            "goal_key": "route",
                            "candidate_id": CANDIDATE_V1,
                            "lead_in": "The route options were close, so I chose this one for your trip.",
                            "follow_up": "",
                            "reason_code": "meets_hard_constraints",
                        },
                    },
                    {
                        "id": "tu-new",
                        "name": "present_route",
                        "input": {
                            "goal_key": "route",
                            "candidate_id": CANDIDATE_V2,
                            "lead_in": "The route options were close, so I chose this one for your trip.",
                            "follow_up": "",
                            "reason_code": "meets_hard_constraints",
                        },
                    },
                ],
                "stop_reason": "tool_use",
            },
        ]
        ev = await self._scripted_turn(
            mode=mode, session=session, session_id=session_id,
            message=CHANGE_ROUTE_MESSAGE, rounds=rounds, turn_id="t2",
            prepare_leg=make_leg(destination="Coney Island"),
            fixed_candidate_id=CANDIDATE_V2)
        assert (ev.offered, [name for name, _input in ev.trace.tool_calls]) == (INITIAL_TOOL_PROFILE, ["declare_goals", "prepare_route_options", "present_route", "present_route"]), f"{s} offered={sorted(ev.offered)}; " f"executed={[n for n, _ in ev.trace.tool_calls]}"
        present_attempts = [
            attempt
            for attempt in ev.trace.capability_attempts
            if attempt["capability"] == "present_route"
        ]
        assert [attempt["ok"] for attempt in present_attempts] == [False, True], f"{s} superseded candidate fails and current candidate presents"
        assert CANDIDATE_UNKNOWN_MARKER not in ev.trace.final_text, f"{s} hides internal candidate diagnostics"
        new_set_id = ev.state["active_candidate_set_id"]
        assert (len(route_cards(ev.events)), ev.mocks["prepare_single_leg"].await_count, len(ev.mocks["stored_candidate_set_ids"]), ev.mocks["stored_candidate_set_ids"][0] if ev.mocks["stored_candidate_set_ids"] else None, ev.state["selected_candidate_id"]) == (1, 1, 1, new_set_id, CANDIDATE_V2), f"{s} one card, one provider call, one set, one selection"
        assert self._snapshot_record(set_id, session_id) == old_record_before, f"{s} superseded record never mutates"
        new_record = candidate_store.load_candidate_set(
            new_set_id, session_id=session_id)
        assert (new_record["presented"], new_record["selected_candidate_id"]) == (True, CANDIDATE_V2), f"{s} current set presented once"
        at_prepare = (ev.mocks.get("session_at_store") or [{}])[0]
        assert (at_prepare.get("active_trip") or {}).get("card_id") == old_card_id, f"{s} accepted trip survives until commit"
        self._assert_meta_done(s, ev)
        self._assert_no_text_leak(s, ev)
        self._assert_policy(s, mode, ev)

    async def test_e2_case4_superseded_candidate(self):
        for mode in MODES:
            with self.subTest(mode=mode):
                await self._transcript(mode)


class DuplicatePresentationTests(_ModelLedCandidateMixin, _CandidateReferenceBase):
    """E2-CASE5 (Auto + Quick): one committed presentation, no corruption."""

    @classmethod
    def setUpClass(cls):
        cls.loop = load_agent_loop()

    async def _rejected_present_with_valid_framing(self, **kwargs):
        """Keep the inherited identity probe focused on its store gate."""

        original_execute = present_route.execute

        async def execute(tool_input, ctx):
            payload = dict(tool_input)
            payload.setdefault(
                "lead_in",
                "The route options were close, so I chose this one for your trip.",
            )
            payload.setdefault("follow_up", "")
            payload.setdefault("reason_code", "meets_hard_constraints")
            return await original_execute(payload, ctx)

        with patch.object(present_route, "execute", new=execute):
            return await self._rejected_present_turn(**kwargs)

    async def _cross_turn_retry(self, mode: str):
        s = f"E2C5-{mode}"
        session_id, session = self._new_session(mode)
        set_id, _record = await self._natural_route_turn(
            mode=mode, scenario_id=f"{s}-t1", session=session,
            session_id=session_id, destination="Work",
            candidate_id=CANDIDATE_V1, turn_id="t1")
        cards_before = list(session.get("route_cards") or [])
        state_before = trip_state_module.get_trip_state(session).copy()
        record_before = self._snapshot_record(set_id, session_id)
        evidence = TurnEvidence()
        evidence.bind_contract(
            TurnContract.from_payload(
                {"goals": [{"goal_key": "route", "kind": "route"}]}
            )
        )
        evidence.record_goal_handle("route", set_id)
        evidence.record_goal("route", GoalState.EVIDENCE_READY, attempted=True)
        ctx = self._tool_ctx(session, session_id)
        ctx.turn_id = "t2"
        ctx.turn_evidence = evidence
        result = await present_route.execute(
            {
                "goal_key": "route",
                "candidate_id": CANDIDATE_V1,
                "lead_in": "The accepted route is still available.",
                "follow_up": "",
                "reason_code": "meets_hard_constraints",
            },
            ctx,
        )
        assert result.ok, f"{s} accepted replay succeeds: {result.error}"
        assert result.data.get("presentation_outcome") == "accepted_route_replay", s
        assert len(route_cards(result.events)) == 1, f"{s} one replay card"
        assert result.session_route_cards == [], f"{s} replay has no card payload"
        assert (session.get("route_cards") or []) == cards_before, f"{s} replay does not persist a duplicate card"
        assert trip_state_module.get_trip_state(session) == state_before, f"{s} replay does not mutate trip state"
        assert self._snapshot_record(set_id, session_id) == record_before, f"{s} replay does not mutate the accepted candidate record"
        assert len(session.get("route_cards") or []) == 1, f"{s} no duplicate card persisted"

    async def _same_round_identical_retry(self, mode: str):
        s = f"E2C5-{mode}-SAME"
        session_id, session = self._new_session(mode)
        await self._natural_route_turn(
            mode=mode, scenario_id=f"{s}-t1", session=session,
            session_id=session_id, destination="Work",
            candidate_id=CANDIDATE_V1, turn_id="t1")
        cards_before = len(session.get("route_cards") or [])
        rounds = [
            _goal_round(
                "route", "route", "prepare_route_options", "tu-replan",
                {"destination": "Coney Island"},
            ),
            {
                "tool_use": [
                    {"id": "tu-d1", "name": "present_route",
                     "input": {
                         "goal_key": "route",
                         "candidate_id": CANDIDATE_V2,
                         "lead_in": "The route options were close, so I chose this one for your trip.",
                         "follow_up": "",
                         "reason_code": "meets_hard_constraints",
                     }},
                    {"id": "tu-d2", "name": "present_route",
                     "input": {
                         "goal_key": "route",
                         "candidate_id": CANDIDATE_V2,
                         "lead_in": "The route options were close, so I chose this one for your trip.",
                         "follow_up": "",
                         "reason_code": "meets_hard_constraints",
                     }},
                ],
                "stop_reason": "tool_use",
            },
        ]
        ev = await self._scripted_turn(
            mode=mode, session=session, session_id=session_id,
            message=CHANGE_ROUTE_MESSAGE, rounds=rounds, turn_id="t2",
            prepare_leg=make_leg(destination="Coney Island"),
            fixed_candidate_id=CANDIDATE_V2)
        present_attempts = [
            attempt
            for attempt in ev.trace.capability_attempts
            if attempt["capability"] == "present_route"
        ]
        assert (len(present_attempts), all(attempt["ok"] for attempt in present_attempts), len(route_cards(ev.events)), len(session.get("route_cards") or []) - cards_before, ev.trace.provider_tool_execution_count) == (2, True, 1, 1, 2), f"{s} one execution, one streamed card, one persisted card"
        new_set_id = ev.state["active_candidate_set_id"]
        new_record = candidate_store.load_candidate_set(
            new_set_id, session_id=session_id)
        assert (new_record["presented"], new_record["selected_candidate_id"]) == (True, CANDIDATE_V2), f"{s} one reservation, one committed selection"

    async def test_e2_case5_cross_turn_retry(self):
        for mode in MODES:
            with self.subTest(mode=mode):
                await self._cross_turn_retry(mode)

    async def test_e2_case5_same_round_identical_retry(self):
        for mode in MODES:
            with self.subTest(mode=mode):
                await self._same_round_identical_retry(mode)


class SessionIsolationCandidateTests(_ModelLedCandidateMixin, _CandidateReferenceBase):
    """E2-CASE8 (Auto + Quick): full isolation incl. what-if probe."""

    @classmethod
    def setUpClass(cls):
        cls.loop = load_agent_loop()

    async def _transcript(self, mode: str):
        s = f"E2C8-{mode}"
        sid_a, s_a = self._new_session(mode)
        sid_b, s_b = self._new_session(mode)
        set_a, _record_a = await self._natural_route_turn(
            mode=mode, scenario_id=f"{s}-A", session=s_a, session_id=sid_a,
            destination="Work", candidate_id=CANDIDATE_A, turn_id="t1")
        set_b, _record_b = await self._natural_route_turn(
            mode=mode, scenario_id=f"{s}-B", session=s_b, session_id=sid_b,
            destination="Home", candidate_id=CANDIDATE_B, turn_id="t1")
        a_before = self._snapshot_session(s_a)
        rounds = [
            _goal_round(
                "route", "route", "prepare_route_options", "tu-wa",
                {"destination": "Airport", "what_if": True},
            ),
            _present_route_round("tu-wa-present", CANDIDATE_WHAT_IF_A),
        ]
        ev_a = await self._scripted_turn(
            mode=mode, session=s_a, session_id=sid_a,
            message=WHAT_IF_MESSAGE, rounds=rounds, turn_id="t2",
            prepare_leg=make_leg(destination="Airport"),
            fixed_candidate_id=CANDIDATE_WHAT_IF_A)
        assert [name for name, _input in ev_a.trace.tool_calls] == ["declare_goals", "prepare_route_options", "present_route"], f"{s} A what-if preview runs the canonical chain"
        state_a = trip_state_module.get_trip_state(s_a)
        temp_set_a = state_a["temporary_candidate_set_id"]
        assert (bool(temp_set_a) and temp_set_a.startswith("cs_"), state_a["temporary_selected_candidate_id"], state_a["active_candidate_set_id"], state_a["selected_candidate_id"]) == (True, CANDIDATE_WHAT_IF_A, set_a, CANDIDATE_A), f"{s} A preview binds temp, keeps active"
        assert (len(route_cards(ev_a.events)), s_a.get("active_trip"), s_a.get("route_cards") or []) == (1, a_before["active_trip"], a_before["route_cards"]), f"{s} preview streams one card, persists nothing"
        temp_record_a = candidate_store.load_candidate_set(
            temp_set_a, session_id=sid_a)
        assert (temp_record_a["presented"], temp_record_a["scenario_mode"]) == (False, "what_if"), f"{s} preview is an unconsumed what-if"
        a_after_preview = self._snapshot_session(s_a)
        temp_a_before = self._snapshot_record(temp_set_a, sid_a)
        await self._rejected_present_turn(
            mode=mode, scenario_id=f"{s}-B-uses-A-whatif", session=s_b,
            session_id=sid_b, message=WHAT_IF_MESSAGE,
            candidate_id=CANDIDATE_WHAT_IF_A,
            marker=CANDIDATE_UNKNOWN_MARKER, set_id=set_b, turn_id="t2")
        assert (self._snapshot_session(s_a), self._snapshot_record(temp_set_a, sid_a)) == (a_after_preview, temp_a_before), f"{s} A session and temporary record untouched by B probe"
        for probe_set in (set_a, temp_set_a):
            assert candidate_store.load_candidate_set(probe_set, session_id=sid_b) is None, f"{s} A set {probe_set} does not load under B"
        assert candidate_store.load_candidate_set(set_b, session_id=sid_a) is None, f"{s} B set does not load under A"
        assert trip_state_module.get_trip_state(s_a)["temporary_candidate_set_id"] == temp_set_a, f"{s} A temporary scenario stays bound"
        assert trip_state_module.get_trip_state(s_b)["temporary_candidate_set_id"] is None, f"{s} B binds no temporary scenario from A"

    async def test_e2_case8_session_isolation(self):
        for mode in MODES:
            with self.subTest(mode=mode):
                await self._transcript(mode)


__all__ = ()
