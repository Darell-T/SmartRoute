"""Regression gates for route preference, crowd, and nearby-place decisions.

These tests exercise the public agent capability/turn seams with deterministic
provider fixtures.  The model is scripted only to make the otherwise
non-deterministic decision reproducible; route/place facts still pass through
the real server-owned executors and stores.
"""
from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, patch

from app.services.agent import session as session_module
from app.services.agent.tools import ToolContext, ToolResult
from app.services.agent.tools.location_resolution import ResolvedPlace
from app.services.agent.tools.places import discover_places
from app.services.trips.selection_decision import (
    evaluate_candidate_decision,
    evaluate_dominated_selection,
)

from tests.agent_route_decision_test_support import (
    AgentRouteDecisionTestMixin,
    _prepared_leg,
    _present_round,
    _route,
    _route_goal_round,
)
from tests.conversation.conversation_matrix_harness import route_cards, run_turn


def _dominance_candidate(
    candidate_id: str,
    *,
    duration: object,
    walking: object,
    transfers: object,
    preference: str,
    preference_source: str = "current_turn",
    official: list | None = None,
    confirmed: list | None = None,
    unconfirmed: list | None = None,
) -> dict:
    return {
        "candidate_id": candidate_id,
        "digest": {
            "duration_minutes": duration,
            "walking_minutes": walking,
            "transfers": transfers,
            "hard_constraints_satisfied": True,
            "official_service_impacts": list(official or []),
            "confirmed_incident_impacts": list(confirmed or []),
            "unconfirmed_material_claims": list(unconfirmed or []),
            "soft_preferences": {
                "routing_preference": preference,
                "routing_preference_source": preference_source,
            },
        },
    }


class AgentRouteDecisionReliabilityTests(AgentRouteDecisionTestMixin, unittest.IsolatedAsyncioTestCase):
    async def test_fewer_transfers_preference_cannot_be_presented_as_less_walking(
        self,
    ) -> None:
        destination = ResolvedPlace(
            "JFK Airport", 40.6413, -73.7781, "known_place"
        )
        prepared = _prepared_leg(
            destination="JFK",
            destination_place=destination,
            routes=[
                _route(route_ids=("A",), walking_seconds=120, total_seconds=1800),
                _route(
                    route_ids=("A", "AirTrain"),
                    walking_seconds=300,
                    total_seconds=2100,
                ),
            ],
        )
        rounds = [
            _route_goal_round(
                destination="JFK",
                routing_preference="FEWER_TRANSFERS",
            ),
            _present_round(
                "cd_fewer_transfers",
                lead_in="I chose this route because it uses less walking.",
                reason_code="less_walking",
            ),
            _present_round(
                "cd_fewer_transfers",
                lead_in="I chose this route because it uses fewer transfers.",
                reason_code="fewer_transfers",
            ),
        ]
        trace = self.loop.TurnTrace()
        with patch(
            "app.services.agent.candidate_store.new_candidate_id",
            side_effect=["cd_fewer_transfers", "cd_more_walking"],
        ):
            events, _trace = await run_turn(
                self.loop,
                session=session_module.new_session()[1],
                session_id="route-preference-turn",
                turn_id="route-preference",
                message="Get me to JFK with fewer transfers because I have huge luggage.",
                rounds=rounds,
                prepare_leg=prepared,
                trace=trace,
            )

        present_calls = [
            tool_input
            for name, tool_input in trace.tool_calls
            if name == "present_route"
        ]
        prepare_calls = [
            tool_input
            for name, tool_input in trace.tool_calls
            if name == "prepare_route_options"
        ]
        assert len(prepare_calls) == 1
        assert prepare_calls[0]["routing_preference"] == "FEWER_TRANSFERS"
        assert len(present_calls) == 2
        assert present_calls[-1]["reason_code"] == "fewer_transfers"
        assert "less walking" not in present_calls[-1]["lead_in"].casefold()
        cards = route_cards(events)
        assert len(cards) == 1
        card = cards[0]
        assert card.selection_decision["reason_code"] == "fewer_transfers"
        structured_codes = {
            reason["code"]
            for reason in card.itinerary["structured_recommendation_reasons"]
        }
        assert structured_codes == {"fewer_transfers"}
        assert "fastest" not in structured_codes
        assert "less_walking" not in structured_codes
        assert "fewer transfers" in card.summary["reason"].casefold()

    async def test_avoid_crowds_stays_active_without_a_temporally_relevant_event_claim(
        self,
    ) -> None:
        destination = ResolvedPlace(
            "Madison Square Garden", 40.7505, -73.9934, "known_place"
        )
        prepared = _prepared_leg(
            destination="Madison Square Garden",
            destination_place=destination,
            routes=[
                _route(route_ids=("Q",), walking_seconds=120, total_seconds=1800),
                _route(
                    route_ids=("Q", "N"),
                    walking_seconds=180,
                    total_seconds=2100,
                ),
            ],
            event_evidence_status="no_relevant_events",
        )
        rounds = [
            _route_goal_round(
                destination="Madison Square Garden",
                avoid_crowds=True,
            ),
            _present_round(
                "cd_msg_route",
                lead_in="I chose this route because it avoids crowds.",
                reason_code="lower_event_crowd_exposure",
            ),
            _present_round(
                "cd_msg_route",
                lead_in=(
                    "I found a route, but I could not verify event-related crowd "
                    "risk for the arrival window."
                ),
                reason_code="meets_hard_constraints",
            ),
        ]
        trace = self.loop.TurnTrace()
        session = session_module.new_session()[1]
        with patch(
            "app.services.agent.candidate_store.new_candidate_id",
            side_effect=["cd_msg_route", "cd_msg_alt"],
        ):
            events, _trace = await run_turn(
                self.loop,
                session=session,
                session_id="crowd-constraint-turn",
                turn_id="crowd-constraint",
                message="Get me to Madison Square Garden and avoid crowds.",
                rounds=rounds,
                prepare_leg=prepared,
                trace=trace,
            )

        prepare_calls = [
            tool_input
            for name, tool_input in trace.tool_calls
            if name == "prepare_route_options"
        ]
        assert len(prepare_calls) == 1
        assert prepare_calls[0]["avoid_crowds"]
        present_calls = [
            tool_input
            for name, tool_input in trace.tool_calls
            if name == "present_route"
        ]
        assert len(present_calls) == 2
        assert "avoid crowds" not in present_calls[-1]["lead_in"].casefold()
        assert "crowd-free" not in present_calls[-1]["lead_in"].casefold()
        cards = route_cards(events)
        assert len(cards) == 1
        card = cards[0]
        structured_codes = {
            reason["code"]
            for reason in card.itinerary["structured_recommendation_reasons"]
        }
        assert structured_codes == {"meets_hard_constraints"}
        assert "lower_event_crowd_exposure" not in structured_codes
        assert "fewer_transfers" not in structured_codes
        assert "no relevant event crowd evidence" in card.summary["reason"].casefold()
        rider_text = " ".join(
            event.text for event in events if event.type == "token"
        ).casefold()
        assert "no relevant event crowd evidence" in rider_text

    async def test_current_location_keeps_untruncated_provider_candidates(
        self,
    ) -> None:
        session_id, session = session_module.new_session()
        ctx = ToolContext(
            session=session,
            session_id=session_id,
            turn_id="nearby-branch",
            now_et="2026-08-20T12:00:00-04:00",
            origin={"lat": 40.672, "lng": -73.98},
            rider_message="Find the Kyuramen branch with the least walking.",
        )
        far_branch = {
            "name": "Kyuramen Forest Hills",
            "address": "71-50 Austin St, Queens, NY",
            "location": {"latitude": 40.718, "longitude": -73.845},
            "place_id": "provider-forest-hills",
            "rating": 4.6,
            "review_count": 800,
            "open_now": True,
            "address_components": [
                {"longText": "Queens", "types": ["sublocality_level_1"]},
                {"longText": "New York", "types": ["locality"]},
            ],
        }
        nearby_branch = {
            "name": "Kyuramen Park Slope",
            "address": "219 5th Ave, Brooklyn, NY",
            "location": {"latitude": 40.6725, "longitude": -73.9785},
            "place_id": "provider-park-slope",
            "rating": 4.5,
            "review_count": 500,
            "open_now": True,
            "address_components": [
                {"longText": "Brooklyn", "types": ["sublocality_level_1"]},
                {"longText": "New York", "types": ["locality"]},
            ],
        }
        coordinate_missing_branch = {
            "name": "Kyuramen Midtown",
            "address": "123 W 42nd St, Manhattan, NY",
            "place_id": "provider-midtown",
            "rating": 4.4,
            "review_count": 300,
            "open_now": True,
        }

        provider_result = ToolResult(
            ok=True,
            data={"places": [far_branch, coordinate_missing_branch, nearby_branch]},
        )
        with patch.object(
            discover_places.search_local_places,
            "_provider_search",
            new=AsyncMock(return_value=provider_result),
        ):
            result = await discover_places.execute(
                {
                    "operation": "search",
                    "query": "Kyuramen",
                    "scope": {"kind": "current_location", "values": []},
                    "open_now": None,
                    "max_results": 8,
                    "candidate_names": [],
                },
                ctx,
            )

        assert result.ok, result.error
        places = result.data["places"]
        assert [place["name"] for place in places] == ["Kyuramen Forest Hills", "Kyuramen Midtown", "Kyuramen Park Slope"]

    def test_clear_less_walking_dominance_is_challenged_for_current_or_persisted_preference(self):
        for source in ("current_turn", "persisted_rider"):
            with self.subTest(source=source):
                selected = _dominance_candidate(
                    "selected",
                    duration=75,
                    walking=6,
                    transfers=1,
                    preference="LESS_WALKING",
                    preference_source=source,
                )
                alternative = _dominance_candidate(
                    "alternative",
                    duration=25,
                    walking=4,
                    transfers=1,
                    preference="LESS_WALKING",
                    preference_source=source,
                )

                decision = evaluate_dominated_selection(
                    {"candidates": [selected, alternative]}, selected
                )

                assert decision["challenged"]
                assert decision["preference"] == "LESS_WALKING"

    def test_fewer_transfers_dominance_is_challenged(self):
        selected = _dominance_candidate(
            "selected",
            duration=75,
            walking=6,
            transfers=2,
            preference="FEWER_TRANSFERS",
        )
        alternative = _dominance_candidate(
            "alternative",
            duration=25,
            walking=4,
            transfers=1,
            preference="FEWER_TRANSFERS",
        )

        decision = evaluate_dominated_selection(
            {"candidates": [selected, alternative]}, selected
        )

        assert decision["challenged"]
        assert decision["preference"] == "FEWER_TRANSFERS"

    def test_less_walking_tradeoff_remains_model_owned(self):
        selected = _dominance_candidate(
            "selected",
            duration=82,
            walking=7,
            transfers=2,
            preference="LESS_WALKING",
        )
        alternative = _dominance_candidate(
            "alternative",
            duration=75,
            walking=11,
            transfers=1,
            preference="LESS_WALKING",
        )

        decision = evaluate_dominated_selection(
            {"candidates": [selected, alternative]}, selected
        )

        assert not decision["challenged"]

    def test_worse_confirmed_or_official_condition_burden_blocks_challenge(self):
        selected = _dominance_candidate(
            "selected",
            duration=75,
            walking=6,
            transfers=1,
            preference="LESS_WALKING",
        )
        alternative = _dominance_candidate(
            "alternative",
            duration=25,
            walking=4,
            transfers=1,
            preference="LESS_WALKING",
            official=[{"route_id": "Q"}],
            unconfirmed=[{"status": "possible_delay_unconfirmed"}],
        )

        decision = evaluate_dominated_selection(
            {"candidates": [selected, alternative]}, selected
        )

        assert not decision["challenged"]

    def test_equal_condition_counts_with_different_impacts_fail_closed(self):
        cases = (
            (
                "official",
                [{"alert_id": "alert-a"}],
                [{"alert_id": "alert-b"}],
            ),
            (
                "confirmed",
                ["incident-a"],
                ["incident-b"],
            ),
        )
        for name, selected_impacts, alternative_impacts in cases:
            with self.subTest(name=name):
                selected = _dominance_candidate(
                    "selected",
                    duration=75,
                    walking=6,
                    transfers=1,
                    preference="LESS_WALKING",
                    official=selected_impacts if name == "official" else [],
                    confirmed=selected_impacts if name == "confirmed" else [],
                )
                alternative = _dominance_candidate(
                    "alternative",
                    duration=25,
                    walking=4,
                    transfers=1,
                    preference="LESS_WALKING",
                    official=alternative_impacts if name == "official" else [],
                    confirmed=alternative_impacts if name == "confirmed" else [],
                )

                decision = evaluate_dominated_selection(
                    {"candidates": [selected, alternative]}, selected
                )

                assert not decision["challenged"]

    def test_removing_a_selected_condition_can_still_allow_challenge(self):
        selected = _dominance_candidate(
            "selected",
            duration=75,
            walking=6,
            transfers=1,
            preference="LESS_WALKING",
            official=[{"alert_id": "alert-a"}, {"alert_id": "alert-b"}],
            confirmed=["incident-a"],
        )
        alternative = _dominance_candidate(
            "alternative",
            duration=25,
            walking=4,
            transfers=1,
            preference="LESS_WALKING",
            official=[{"alert_id": "alert-a"}],
            confirmed=["incident-a"],
        )

        decision = evaluate_dominated_selection(
            {"candidates": [selected, alternative]}, selected
        )

        assert decision["challenged"]

    def test_non_material_planned_service_change_is_ignored_by_dominance(self):
        planned_local = {
            "source": "mta_service_alerts",
            "source_id": "lmm:planned_work:33095",
            "alert_id": "lmm:planned_work:33095",
            "planned_status": "planned",
            "change_type": "express_to_local",
            "service_operating": True,
            "material_disruption": False,
        }
        selected = _dominance_candidate(
            "selected",
            duration=75,
            walking=6,
            transfers=1,
            preference="LESS_WALKING",
            official=[planned_local],
        )
        alternative = _dominance_candidate(
            "alternative",
            duration=25,
            walking=4,
            transfers=1,
            preference="LESS_WALKING",
        )

        decision = evaluate_dominated_selection(
            {"candidates": [selected, alternative]}, selected
        )
        assert decision["challenged"]

        unknown_alternative = _dominance_candidate(
            "unknown-alternative",
            duration=25,
            walking=4,
            transfers=1,
            preference="LESS_WALKING",
            official=[{"header": "Unknown service notice"}],
        )
        decision = evaluate_dominated_selection(
            {"candidates": [selected, unknown_alternative]}, selected
        )
        assert not decision["challenged"]

    def test_missing_or_nonfinite_comparison_factors_fail_closed(self):
        for key, value in (
            ("duration_minutes", None),
            ("walking_minutes", float("inf")),
            ("transfers", -1),
        ):
            with self.subTest(key=key):
                selected = _dominance_candidate(
                    "selected",
                    duration=75,
                    walking=6,
                    transfers=1,
                    preference="LESS_WALKING",
                )
                selected["digest"][key] = value
                alternative = _dominance_candidate(
                    "alternative",
                    duration=25,
                    walking=4,
                    transfers=1,
                    preference="LESS_WALKING",
                )

                decision = evaluate_dominated_selection(
                    {"candidates": [selected, alternative]}, selected
                )

                assert not decision["challenged"]

        for key in ("official_service_impacts", "confirmed_incident_impacts"):
            with self.subTest(key=key):
                selected = _dominance_candidate(
                    "selected",
                    duration=75,
                    walking=6,
                    transfers=1,
                    preference="LESS_WALKING",
                )
                selected["digest"][key] = None
                alternative = _dominance_candidate(
                    "alternative",
                    duration=25,
                    walking=4,
                    transfers=1,
                    preference="LESS_WALKING",
                )

                decision = evaluate_dominated_selection(
                    {"candidates": [selected, alternative]}, selected
                )

                assert not decision["challenged"]

    def test_unconfirmed_vehicle_signal_cannot_ground_disruption_avoidance(self):
        selected = {
            "candidate_id": "cd_selected",
            "digest": {
                "hard_constraints_satisfied": True,
                "soft_preferences": {"routing_preference": "FEWER_TRANSFERS"},
                "official_service_impacts": [],
                "confirmed_incident_impacts": [],
                "unconfirmed_material_claims": [],
            },
        }
        alternative = {
            "candidate_id": "cd_alternative",
            "digest": {
                "hard_constraints_satisfied": True,
                "soft_preferences": {"routing_preference": "FEWER_TRANSFERS"},
                "official_service_impacts": [],
                "confirmed_incident_impacts": [],
                "unconfirmed_material_claims": [
                    {"status": "possible_delay_unconfirmed"}
                ],
            },
        }

        assert "avoids_active_disruption" not in evaluate_candidate_decision({"candidates": [selected, alternative]}, selected)["supported_reason_codes"]
