"""Live Sonnet checks for multi-turn conversational state and constraints."""

from __future__ import annotations

import unittest
from copy import deepcopy
from unittest.mock import AsyncMock, patch

from app.services.agent import candidate_store, loop
from app.services.agent import session as session_module
from app.services.agent import trip_state as trip_state_module
from app.services.agent.tools._types import ToolResult
from app.services.agent.tools.places import discover_places
from app.services.agent.tools.transit import check_transit

from tests.anthropic_live_fixtures import (
    _contextual_place_results,
    _prepared_advisory_route,
    _prepared_route,
    _transit_status,
)
from tests.anthropic_live_support import (
    LIVE_ENABLED,
    AnthropicLiveAgentContractMixin,
    _assert_safe_passenger_output,
    _passenger_text,
    _tool_names,
)


@unittest.skipUnless(
    LIVE_ENABLED,
    "set RUN_ANTHROPIC_TOOL_CONTRACT=1 to run live provider checks",
)
class AnthropicLiveConversationContractTests(
    AnthropicLiveAgentContractMixin,
    unittest.IsolatedAsyncioTestCase,
):
    async def test_real_sonnet_relaxes_q_constraint_and_accepts_advisory(self) -> None:
        session_id, session = session_module.new_session()
        turns = (
            (
                "live-relax-q-1",
                "Route me to Madison Square Garden, but do not use the Q.",
                "B",
            ),
            (
                "live-relax-q-2",
                "Actually, the delayed Q is fine. Use the Q anyway.",
                "Q",
            ),
        )
        records: list[tuple[list, loop.TurnTrace]] = []
        with patch(
            "app.services.agent.tools.route.prepare_route_options.prepare_single_leg",
            new=AsyncMock(side_effect=_prepared_advisory_route),
        ):
            for turn_id, rider, expected_line in turns:
                events, trace = await self._run_session_turn(
                    rider,
                    turn_id=turn_id,
                    session_id=session_id,
                    session=session,
                )
                self._report(turn_id, rider, events, trace)
                self._assert_completed(events)
                names = _tool_names(trace)
                assert "prepare_route_options" in names
                assert "present_route" in names
                assert "complete_turn" not in names
                route_card = next(
                    event for event in events if event.type == "route_card"
                )
                assert expected_line in (route_card.summary.get("lines") or [])
                records.append((events, trace))

        first_prepare = next(
            tool_input
            for name, tool_input in records[0][1].tool_calls
            if name == "prepare_route_options"
        )
        second_prepare = next(
            tool_input
            for name, tool_input in records[1][1].tool_calls
            if name == "prepare_route_options"
        )
        assert "Q" in (first_prepare.get("excluded_route_ids") or [])
        assert "Q" not in (second_prepare.get("excluded_route_ids") or [])
        assert "Q" in (second_prepare.get("required_route_ids") or [])
        follow_up_text = _passenger_text(records[1][0]).casefold()
        assert "delay" in follow_up_text or "coverage" in follow_up_text

    async def test_real_sonnet_keeps_physically_impossible_route_blocked(self) -> None:
        rider = (
            "Route me from Kings Highway to Brighton Beach on the B. "
            "Even if no B route exists between those stations, that's okay."
        )
        with patch(
            "app.services.agent.tools.route.prepare_route_options.prepare_single_leg",
            new=AsyncMock(
                return_value=ToolResult(
                    ok=False,
                    error="no transit route found between those points",
                )
            ),
        ):
            events, trace = await self._run_turn(
                rider,
                turn_id="live-impossible-route",
            )

        self._report("physically_impossible_route", rider, events, trace)
        names = _tool_names(trace)
        assert "prepare_route_options" in names
        assert "complete_turn" in names
        assert "present_route" not in names
        assert sum(event.type == "route_card" for event in events) == 0
        text = _passenger_text(events).casefold().replace("\u2019", "'")
        assert any(phrase in text for phrase in ("can't", "couldn't", "cannot", "wasn't able", "unable", "could not", "no verified route", "not available"))
        self._assert_completed(events)

    async def test_real_sonnet_preserves_direction_and_status_intent(self) -> None:
        rider = "Does the downtown Q have any stalled trains currently?"
        with patch.object(
            check_transit,
            "collect_service_status",
            new=AsyncMock(return_value=_transit_status()),
        ):
            events, trace = await self._run_turn(
                rider,
                turn_id="live-transit",
            )

        names = _tool_names(trace)
        assert names[0] == "declare_goals"
        assert "check_transit" in names
        assert "present_transit" in names
        transit_input = next(
            tool_input
            for name, tool_input in trace.tool_calls
            if name == "check_transit"
        )
        assert transit_input.get("operation") == "service_status"
        assert transit_input.get("route_ids") == ["Q"]
        assert str(transit_input.get("direction") or "").casefold() == "downtown"
        passenger_text = _passenger_text(events).casefold()
        assert "delay" in passenger_text
        assert "stalled train" in passenger_text
        self._assert_completed(events)
        self._report("directional_transit_status", rider, events, trace)

    async def test_real_sonnet_lets_backend_clarify_direction_for_q_advice(self) -> None:
        rider = "Is it smart to take the Q right now?"
        events, trace = await self._run_turn(rider, turn_id="live-q-ambiguity")

        assert events[-1].type == "done"
        assert events[-1].terminal_state == "clarification_required"
        assert not any(event.type == "error" for event in events)
        _assert_safe_passenger_output(events)
        text = _passenger_text(events).casefold()
        assert "direction" in text or ("uptown" in text and "downtown" in text)
        assert "q" in text
        names = _tool_names(trace)
        assert "check_transit" in names
        assert "take the q" not in text
        assert "wait for the q" not in text
        self._report("ambiguous_q_advice", rider, events, trace)

    async def test_real_sonnet_retains_places_across_a_follow_up_search(self) -> None:
        session_id, session = session_module.new_session()
        turns = (
            (
                "live-memory-1",
                "What are some good pizza options in Manhattan?",
                "present_places",
            ),
            ("live-memory-2", "What about Brooklyn?", "present_places"),
            (
                "live-memory-3",
                "Take me to Prince Street Pizza and minimize walking.",
                "present_route",
            ),
        )
        records: list[tuple[str, list, loop.TurnTrace]] = []
        with (
            patch.object(
                discover_places.search_local_places,
                "execute",
                new=AsyncMock(side_effect=_contextual_place_results),
            ),
            patch(
                "app.services.agent.tools.route.prepare_route_options.prepare_single_leg",
                new=AsyncMock(side_effect=_prepared_route),
            ),
        ):
            for turn_id, rider, expected_terminal in turns:
                events, trace = await self._run_session_turn(
                    rider,
                    turn_id=turn_id,
                    session_id=session_id,
                    session=session,
                )
                self._report(turn_id, rider, events, trace)
                self._assert_completed(events)
                assert expected_terminal in _tool_names(trace)
                records.append((rider, events, trace))

        first_text = _passenger_text(records[0][1])
        second_text = _passenger_text(records[1][1])
        third_names = _tool_names(records[2][2])
        assert "Prince Street Pizza" in first_text
        assert "Lo Duca Pizza" in second_text
        assert "discover_places" not in third_names
        assert "prepare_route_options" in third_names
        assert sum(event.type == "route_card" for event in records[2][1]) == 1
        route_card = next(
            event for event in records[2][1] if event.type == "route_card"
        )
        assert "prince street" in str(route_card.destination.get("label") or "").casefold()

    async def test_real_sonnet_lindustrie_followup_uses_qualitative_research(self) -> None:
        session_id, session = session_module.new_session()
        with patch.object(
            discover_places.search_local_places,
            "execute",
            new=AsyncMock(side_effect=_contextual_place_results),
        ):
            first_events, first_trace = await self._run_session_turn(
                "What are some good pizza options in Manhattan?",
                turn_id="live-lindustrie-list",
                session_id=session_id,
                session=session,
            )
            second_events, second_trace = await self._run_session_turn(
                "What's good at L'Industrie?",
                turn_id="live-lindustrie-details",
                session_id=session_id,
                session=session,
            )

        self._report(
            "lindustrie_details_list",
            "What are some good pizza options in Manhattan?",
            first_events,
            first_trace,
        )
        self._report(
            "lindustrie_details_followup",
            "What's good at L'Industrie?",
            second_events,
            second_trace,
        )
        self._assert_completed(first_events)
        self._assert_completed(second_events)
        assert "present_places" in _tool_names(first_trace)
        second_names = _tool_names(second_trace)
        assert "discover_places" in second_names
        assert "present_places" in second_names
        second_present_inputs = [
            payload
            for name, payload in second_trace.tool_calls
            if name == "present_places"
        ]
        assert len(second_present_inputs) >= 2
        assert any(payload.get("presentation_mode") == "details" and payload.get("research_used") is False for payload in second_present_inputs)
        second_present_input = second_present_inputs[-1]
        assert second_present_input.get("presentation_mode") == "details"
        assert True is second_present_input.get("research_used")
        assert str(second_present_input.get("lead_in") or "").strip()
        second_text = _passenger_text(second_events)
        lowered = second_text.casefold()
        assert "l'industrie" in lowered
        assert any(term in lowered for term in ("signature", "popular", "known", "famous", "style", "slice", "menu"))
        assert "104 christopher" not in lowered
        assert "4.7" not in lowered
        assert "1. l'industrie pizzeria" not in lowered

    async def test_real_sonnet_why_not_q_uses_stored_comparison_without_replanning(self) -> None:
        candidates = [
            {
                "candidate_id": "cd_b",
                "digest": {
                    "destination_name": "Madison Square Garden",
                    "transit_lines": ["B"],
                    "duration_minutes": 35,
                    "walking_minutes": 4,
                    "transfers": 0,
                    "official_service_impacts": [],
                    "confirmed_incident_impacts": [],
                    "unconfirmed_material_claims": [],
                    "event_or_crowd_impacts": [],
                },
            },
            {
                "candidate_id": "cd_q",
                "digest": {
                    "destination_name": "Madison Square Garden",
                    "transit_lines": ["Q"],
                    "duration_minutes": 34,
                    "walking_minutes": 12,
                    "transfers": 1,
                    "official_service_impacts": [],
                    "confirmed_incident_impacts": [],
                    "unconfirmed_material_claims": [],
                    "event_or_crowd_impacts": [],
                },
            },
        ]
        session_id, session = session_module.new_session()
        set_id = candidate_store.store_candidate_set(
            session_id=session_id,
            payload={"candidates": candidates},
        )
        trip_state_module.bind_candidate_set(session, set_id)
        trip_state_module.bind_selected_candidate(session, "cd_b")
        active_trip = {
            "card_id": "rc_winner",
            "destination": "Madison Square Garden",
            "canonical_itinerary": {
                "total_duration_seconds": 2100,
                "transfers": 0,
                "walking_seconds": 240,
                "route_lines": ["B"],
            },
            "selection_decision": {
                "reason_code": "fewer_transfers",
                "selection_source": "model",
            },
        }
        session["active_trip"] = deepcopy(active_trip)
        session["route_cards"] = [deepcopy(active_trip)]
        before_record = deepcopy(
            candidate_store.load_candidate_set(set_id, session_id=session_id)
        )
        before_trip = deepcopy(session["active_trip"])
        before_cards = deepcopy(session["route_cards"])

        events, trace = await self._run_session_turn(
            "Why not the Q?",
            turn_id="live-why-q",
            session_id=session_id,
            session=session,
        )

        self._report("why_not_q_comparison", "Why not the Q?", events, trace)
        self._assert_completed(events)
        names = _tool_names(trace)
        assert names == ["declare_goals", "complete_turn"]
        assert "prepare_route_options" not in names
        assert "present_route" not in names
        assert "check_transit" not in names
        text = _passenger_text(events).casefold()
        assert "q" in text
        assert "walking" in text
        assert "b" in text or "accepted" in text
        assert before_record == candidate_store.load_candidate_set(set_id, session_id=session_id)
        assert before_trip == session["active_trip"]
        assert before_cards == session["route_cards"]
