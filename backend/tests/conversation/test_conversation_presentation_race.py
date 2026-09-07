"""Batch J2: presentation neighbors and duplicate-presentation races.

Deterministic scenarios (no sleeps) through the real loop, real
``present_route`` executor, real tool ledger, real candidate store, and real
SSE events:

- J2-PRESENT-4a: concurrent (same-round parallel) duplicate ``present_route``
  calls against one candidate set deduplicate to exactly one execution, one
  committed selected identity, and one stored/streamed accepted card; the
  losing call is served from the same result (no second provider call).
- J2-PRESENT-4b: a stale re-present of an already-presented candidate in a
  later turn fails bounded at the atomic store reservation ("already
  presented"), emits no card, mutates no session or store record, and never
  re-runs route preparation.
- J2-PRESENT-4c: the backend has no event-replay state. SSE frames are
  transport-only formatting (``events.sse_format``) and are never persisted;
  there is no replay endpoint and candidate records carry no replay/event
  log. This is documented as unsupported rather than invented: the only
  server-side replay guard is the atomic one-time presentation reservation.

``present_route`` legitimately re-reads the stored candidate before the final
one-time reservation gate. Route enrichment is intentionally outside this
request-critical path; assertions distinguish the route-provider prepare seam
(must stay at one call for the whole transcript) from the guarded legacy
enrichment seam (which must remain unused).

The real loop, registry, executors, stores, ledger, and SSE events run
untouched; only deterministic Anthropic rounds and the documented genuine
provider/data seams are scripted. No production, existing tests, or ledger
are modified.
"""

from __future__ import annotations

import asyncio
from unittest.mock import patch

from app.services.agent import candidate_store
from app.services.agent import trip_state as trip_state_module
from app.services.agent.tools._types import ToolContext
from app.services.agent.tools.route import present_route

from tests.conversation.conversation_cancellation_fixtures import (
    ALREADY_PRESENTED_MARKER,
    CANDIDATE_V1,
    CANDIDATE_V2,
    CHANGE_ROUTE_MESSAGE,
    WORK_MESSAGE,
)
from tests.conversation.conversation_cancellation_support import CancellationBase
from tests.conversation.conversation_matrix_harness import (
    _turn_round,
    load_agent_loop,
    make_leg,
    new_session,
    route_cards,
    run_turn,
)


class PresentationRaceTests(CancellationBase):
    """J2-PRESENT-4: presentation neighbors against one committed set."""

    @classmethod
    def setUpClass(cls):
        cls.loop = load_agent_loop()

    async def _replan_turn(
        self,
        *,
        session: dict,
        session_id: str,
        rounds: list,
        turn_id: str,
        fixed_candidate_id: str,
        mocks: dict,
        mark_calls: list,
    ) -> tuple[list, object]:
        """One scripted replan turn with the real present executor."""

        original_mark = candidate_store.mark_presented

        def _mark(*args, **kwargs):
            result = original_mark(*args, **kwargs)
            mark_calls.append((args[0], args[1], result))
            return result

        trace = self.loop.TurnTrace()
        with patch("app.services.agent.candidate_store.mark_presented", new=_mark):
            events, trace = await run_turn(
                self.loop,
                session=session,
                session_id=session_id,
                message=CHANGE_ROUTE_MESSAGE,
                rounds=rounds,
                mode="auto",
                trace=trace,
                mocks=mocks,
                turn_id=turn_id,
                prepare_leg=make_leg(destination="Coney Island"),
                fixed_candidate_id=fixed_candidate_id,
            )
        return events, trace

    async def test_same_round_duplicate_present_deduplicates_to_one(self):
        """J2-PRESENT-4a: one execution, one card, one committed identity."""

        session_id, session = self._new_session()
        _e, _t, _m, _seed_set = await self._natural_route_turn(
            session=session,
            session_id=session_id,
            message=WORK_MESSAGE,
            destination="Work",
            candidate_id=CANDIDATE_V1,
            turn_id="t1",
        )
        baseline = set(asyncio.all_tasks())
        rounds = [
            _turn_round(
                "declare_goals",
                "tu-goals",
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
                "tu-prep",
                {
                    "goal_key": "route",
                    "origin": None,
                    "destination": "Coney Island",
                    "destination_place_id": None,
                    "exclude_modes": None,
                    "allowed_modes": None,
                    "excluded_route_ids": None,
                    "required_route_ids": None,
                    "allowed_route_ids": None,
                    "preferred_modes": None,
                    "routing_preference": None,
                    "departure_time": None,
                    "arrival_by": None,
                    "waypoints": None,
                    "waypoint_dwell_minutes": None,
                    "avoid_crowds": None,
                    "avoid_stairs": None,
                    "accessibility_required": None,
                    "walking_tolerance_minutes": None,
                    "what_if": None,
                    "activity_label": None,
                },
            ),
            {
                "tool_use": [
                    {
                        "id": "tu-p1",
                        "name": "present_route",
                        "input": {
                            "goal_key": "route",
                            "candidate_id": CANDIDATE_V2,
                            "lead_in": "The route options were close, so I chose this one for your trip.",
                            "follow_up": "",
                            "reason_code": "meets_hard_constraints",
                        },
                    },
                    {
                        "id": "tu-p2",
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
        mark_calls: list = []
        mocks: dict = {}
        events, trace = await self._replan_turn(
            session=session,
            session_id=session_id,
            rounds=rounds,
            turn_id="t2",
            fixed_candidate_id=CANDIDATE_V2,
            mocks=mocks,
            mark_calls=mark_calls,
        )
        present_attempts = [
            attempt
            for attempt in trace.capability_attempts
            if attempt["capability"] == "present_route"
        ]
        assert (len(present_attempts), all(attempt["ok"] for attempt in present_attempts)) == (2, True), "J2-PRESENT-4a both duplicate calls resolve ok"
        assert len(route_cards(events)) == 1, "J2-PRESENT-4a exactly one streamed accepted card"
        assert len(mark_calls) == 1, "J2-PRESENT-4a exactly one atomic reservation"
        assert mark_calls[0][2] is None, "J2-PRESENT-4a reservation succeeds"
        assert mocks["enrich_route"].await_count == 0, "J2-PRESENT-4a no request-time enrichment"
        assert mocks["prepare_single_leg"].await_count == 1, "J2-PRESENT-4a one route-provider prepare call"
        new_set_id = trip_state_module.get_trip_state(session)[
            "active_candidate_set_id"
        ]
        new_record = candidate_store.load_candidate_set(
            new_set_id, session_id=session_id
        )
        assert (new_record["presented"], new_record["selected_candidate_id"]) == (True, CANDIDATE_V2), "J2-PRESENT-4a one committed selected identity"
        assert len(session.get("route_cards") or []) == 2, "J2-PRESENT-4a seed card plus one new persisted card"
        assert trip_state_module.get_trip_state(session)["selected_candidate_id"] == CANDIDATE_V2, "J2-PRESENT-4a committed selection is the one identity"
        await self._assert_no_owned_pending_tasks(baseline)

    async def test_stale_represent_of_presented_candidate_fails_bounded(self):
        """J2-PRESENT-4b: client retry after first success is deduplicated."""

        session_id, session = self._new_session()
        _e, _t, _m, set_id = await self._natural_route_turn(
            session=session,
            session_id=session_id,
            message=WORK_MESSAGE,
            destination="Work",
            candidate_id=CANDIDATE_V1,
            turn_id="t1",
        )
        record_before = self._snapshot_record(set_id, session_id)
        session_before = self._snapshot_session(session)
        baseline = set(asyncio.all_tasks())
        mark_calls: list = []
        mocks: dict = {}
        original_mark = candidate_store.mark_presented

        def _mark(*args, **kwargs):
            result = original_mark(*args, **kwargs)
            mark_calls.append((args[0], args[1], result))
            return result

        patchers = self._turn_patchers(mocks)
        for patcher in patchers:
            patcher.start()
        try:
            with patch(
                "app.services.agent.candidate_store.mark_presented",
                new=_mark,
            ):
                result = await present_route.execute(
                    {
                        "goal_key": "route",
                        "candidate_id": CANDIDATE_V1,
                        "lead_in": "The route options were close, so I chose this one for your trip.",
                        "follow_up": "",
                        "reason_code": "meets_hard_constraints",
                    },
                    ToolContext(
                        session=session,
                        session_id=session_id,
                        turn_id="t2",
                        now_et="2026-08-06T12:00:00-04:00",
                    ),
                )
        finally:
            for patcher in patchers:
                patcher.stop()
        assert not result.ok, "J2-PRESENT-4b stale present fails"
        assert ALREADY_PRESENTED_MARKER in (result.error or ""), "J2-PRESENT-4b atomic store rejection remains explicit internally"
        assert result.events == [], "J2-PRESENT-4b no card on stale replay"
        assert "prepare_single_leg" not in mocks, "J2-PRESENT-4b never enters route preparation"
        assert len(mark_calls) == 1, "J2-PRESENT-4b one reservation attempt"
        assert ALREADY_PRESENTED_MARKER in (mark_calls[0][2] or ""), "J2-PRESENT-4b store rejects the duplicate reservation"
        assert mocks["stored_candidate_set_ids"] == [], "J2-PRESENT-4b no new candidate set stored"
        assert self._snapshot_session(session) == session_before, "J2-PRESENT-4b session state unchanged"
        assert self._snapshot_record(set_id, session_id) == record_before, "J2-PRESENT-4b store record unchanged"
        selected = trip_state_module.get_trip_state(session)["selected_candidate_id"]
        assert selected == CANDIDATE_V1, (
            "J2-PRESENT-4b exactly one committed selected identity remains"
        )
        assert len(session.get("route_cards") or []) == 1, "J2-PRESENT-4b exactly one persisted accepted card"
        await self._assert_no_owned_pending_tasks(baseline)

    def test_no_event_replay_state_exists_server_side(self):
        """J2-PRESENT-4c: replay is documented unsupported, not invented."""

        _session_id, session = new_session()
        for key in ("event_replay", "replay_events", "presented_events", "replay"):
            assert key not in session
        from app.routers import agent_chat

        paths = [route.path for route in agent_chat.router.routes]
        assert not any("replay" in path for path in paths), "no replay endpoint exists to consume stale SSE frames"
        # Candidate records carry no replay/event log; only the one-time
        # presentation reservation guards re-presentation.
        session_id = f"sess-j2-noreplay-{id(session)}"
        set_id = candidate_store.store_candidate_set(
            session_id=session_id,
            payload={"candidates": []},
        )
        record = candidate_store.load_candidate_set(set_id, session_id=session_id)
        for key in ("replay", "presented_events", "event_log", "sse_events"):
            assert key not in record


__all__ = ()
