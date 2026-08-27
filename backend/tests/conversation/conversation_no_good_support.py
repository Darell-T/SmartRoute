"""Shared Batch A support for canonical no-good-options conversation tests.

Non-test module (no ``Test*``/``test_*`` names at module level): pytest never
collects it. Holds the shared ``_NoGoodOptionsBase`` invariants and constants
used by ``test_conversation_no_good_aggregate`` (A-NG-01..05) and
``test_conversation_no_good_nonfatal_followup`` (A-NG-06..09).
"""

from __future__ import annotations

import secrets
import unittest

from app.services.agent import candidate_store
from app.services.agent import trip_state as trip_state_module

from tests.conversation.conversation_matrix_harness import (
    _turn_round,
    clear_caches,
    new_session,
    policy_model,
    route_cards,
    run_turn,
    seed_accepted_active_trip,
)

FORBIDDEN_TOOL_NAMES = (
    "present_route", "plan_trip", "web_search", "search_local_places",
    "get_place_details", "event_lookup", "transit_snapshot",
    "lookup_arrivals", "lookup_facts", "accessibility_status",
    "venue_crowd_window", "check_area_conditions",
)

NO_GOOD_MODEL_TEXT = "I could not find a route that meets your constraints."
VIABLE_CANDIDATE_ID = "cd_viable_with_warning"


def _route_declaration_round(tool_id: str = "tu_goals") -> dict:
    return _turn_round(
        "declare_goals",
        tool_id,
        {
            "goals": [
                {
                    "goal_key": "route",
                    "kind": "route",
                    "depends_on": [],
                }
            ]
        },
    )


def _route_unavailable_round(tool_id: str = "tu_no_good") -> dict:
    return _turn_round(
        "complete_turn",
        tool_id,
        {
            "goal_keys": ["route"],
            "outcome": "unavailable",
            "message": NO_GOOD_MODEL_TEXT,
        },
    )


class _NoGoodOptionsBase(unittest.IsolatedAsyncioTestCase):
    """Shared invariants for non-presentable canonical prepare outcomes."""

    loop = None  # set in setUpClass by subclasses

    def setUp(self):
        clear_caches()

    async def _run_no_good_turn(
        self,
        *,
        session,
        session_id,
        message,
        rounds,
        mode,
        prepare_leg,
        fixed_candidate_id=None,
    ):
        trace = self.loop.TurnTrace()
        mocks = {}
        events, trace = await run_turn(
            self.loop,
            session=session,
            session_id=session_id,
            message=message,
            rounds=rounds,
            mode=mode,
            prepare_leg=prepare_leg,
            fixed_candidate_id=fixed_candidate_id,
            trace=trace,
            mocks=mocks,
        )
        return events, trace, mocks

    async def _run_presentable_scenario(
        self,
        *,
        mode,
        message,
        prepare_leg,
        expected_status,
    ):
        """A viable candidate stays presentable when evidence is adverse or incomplete."""

        session_id = f"sess-{mode}-{secrets.token_hex(4)}"
        _sid, session = new_session()
        seed = seed_accepted_active_trip(session, session_id)
        events, trace, mocks = await self._run_no_good_turn(
            session=session,
            session_id=session_id,
            message=message,
            rounds=[
                _route_declaration_round(),
                _turn_round(
                    "prepare_route_options",
                    "tu_prepare_viable",
                    {
                        "goal_key": "route",
                        "destination": seed.destination,
                    },
                ),
                _turn_round(
                    "present_route",
                    "tu_present_viable",
                    {
                        "goal_key": "route",
                        "candidate_id": VIABLE_CANDIDATE_ID,
                    },
                ),
            ],
            mode=mode,
            prepare_leg=prepare_leg,
            fixed_candidate_id=VIABLE_CANDIDATE_ID,
        )

        assert [name for name, _tool_input in trace.tool_calls] == ["declare_goals", "prepare_route_options", "present_route"]
        cards = route_cards(events)
        assert len(cards) == 1
        assert cards[0].role == "recommended"
        assert trace.model_call_count == 3
        assert mocks["prepare_single_leg"].await_count == 1

        state = trip_state_module.get_trip_state(session)
        assert state["active_candidate_set_id"] != seed.candidate_set_id
        assert state["selected_candidate_id"] == VIABLE_CANDIDATE_ID
        record = candidate_store.load_candidate_set(
            state["active_candidate_set_id"],
            session_id=session_id,
        )
        assert record is not None
        assert record["route_status"] == expected_status
        assert record["presented"]
        assert record["selected_candidate_id"] == VIABLE_CANDIDATE_ID
        return session, session_id, events, trace, record

    async def _run_scenario(
        self,
        *,
        mode,
        message,
        prepare_leg,
        expected_status,
        expected_prepare_input=None,
        tool_input_extra=None,
    ):
        session_id = f"sess-{mode}-{secrets.token_hex(4)}"
        _sid, session = new_session()
        seed = seed_accepted_active_trip(session, session_id)
        events, trace, mocks = await self._run_no_good_turn(
            session=session,
            session_id=session_id,
            message=message,
            rounds=self._prepare_rounds(
                seed.destination,
                extra_input=tool_input_extra,
            ),
            mode=mode,
            prepare_leg=prepare_leg,
        )
        state, audit = self._assert_no_good_invariants(
            events=events,
            trace=trace,
            session=session,
            session_id=session_id,
            seed=seed,
            mode=mode,
            expected_status=expected_status,
            expected_prepare_input=expected_prepare_input,
            stored_set_ids=mocks["stored_candidate_set_ids"],
        )
        assert mocks["prepare_single_leg"].await_count == 1
        return session, session_id, seed, events, trace, audit, state

    def _assert_no_good_invariants(
        self,
        *,
        events,
        trace,
        session,
        session_id,
        seed,
        mode,
        expected_status,
        expected_prepare_input=None,
        stored_set_ids=None,
    ):
        """Assert the shared non-presentable replan contract: the accepted
        selection (card, active/selected candidate, committed record) stays
        bound; the new set is stored only as a separate audit set."""

        names = [name for name, _tool_input in trace.tool_calls]
        assert names == ["declare_goals", "prepare_route_options", "complete_turn"]
        for forbidden in FORBIDDEN_TOOL_NAMES:
            assert forbidden not in names, f"forbidden tool used: {forbidden}"
        if expected_prepare_input is not None:
            prepare_input = next(
                tool_input
                for name, tool_input in trace.tool_calls
                if name == "prepare_route_options"
            )
            for key, value in expected_prepare_input.items():
                assert prepare_input.get(key) == value

        assert events[0].type == "meta"
        assert events[-1].type == "done"
        assert events[-1].stop_reason == "end_turn"
        assert route_cards(events) == [], "no route card for a no-good prepare"
        # Truthful bounded model response: a real second model round completed
        # through the terminal capability, without inventing a winner or
        # leaking internal ids.
        assert trace.model_call_count == 3
        assert "could not find" in trace.final_text
        lowered = trace.final_text.casefold()
        for marker in ("recommended", "i'd take", "best option", "cd_", "cs_"):
            assert marker not in lowered, f"winner/internal id leaked: {marker}"

        expected_mode, expected_model = policy_model(self.loop, mode)
        assert trace.initial_mode == expected_mode
        assert trace.final_mode == expected_mode
        assert self.loop.client.messages.calls[0]["model"] == expected_model

        # P1: the accepted selection stays one bound unit -- active route
        # facts and active/selected candidate identity unchanged.
        state = trip_state_module.get_trip_state(session)
        assert state["active_candidate_set_id"] == seed.candidate_set_id
        assert state["selected_candidate_id"] == seed.candidate_id
        assert state["origin"] == seed.origin
        assert state["destination"] == seed.destination
        assert state["waypoints"] == []
        assert state["planning_mode"] == seed.planning_mode
        assert state["requested_departure"] == seed.requested_departure
        assert state["requested_arrival"] == seed.requested_arrival
        assert session["active_trip"]["card_id"] == seed.card_id
        assert [card["card_id"] for card in session["route_cards"]] == [seed.card_id]
        record = candidate_store.load_candidate_set(
            seed.candidate_set_id,
            session_id=session_id,
        )
        assert record is not None
        assert record["presented"] is True
        assert record["selected_candidate_id"] == seed.candidate_id
        assert record["route_status"] == "good"

        # The audit set is observed through the recording store wrapper (the
        # real store ran); never located via trip_state.active_candidate_set_id.
        assert len(stored_set_ids) == 1
        audit_set_id = stored_set_ids[0]
        assert audit_set_id != seed.candidate_set_id
        assert audit_set_id != state["active_candidate_set_id"], "audit set must not replace the active server-owned selection"
        audit = candidate_store.load_candidate_set(
            audit_set_id,
            session_id=session_id,
        )
        assert audit is not None
        assert audit["route_status"] == expected_status
        assert not audit["presented"]
        assert audit["selected_candidate_id"] is None
        return state, audit

    def _prepare_rounds(
        self,
        destination: str,
        tool_id: str = "tu_1",
        *,
        extra_input: dict | None = None,
    ) -> list[dict]:
        tool_input = {
            "goal_key": "route",
            "destination": destination,
            # Arrival differs from the accepted leave_now trip so the
            # snapshot proves accepted planning fields survive.
            "arrival_by": "2026-08-06T13:00:00-04:00",
        }
        if extra_input:
            tool_input.update(extra_input)
        return [
            _route_declaration_round(),
            _turn_round(
                "prepare_route_options",
                tool_id,
                tool_input,
            ),
            _route_unavailable_round(),
        ]
