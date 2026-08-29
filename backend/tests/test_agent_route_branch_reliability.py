"""Regression gates for route preference, crowd, and nearby-place decisions.

These tests exercise the public agent capability/turn seams with deterministic
provider fixtures.  The model is scripted only to make the otherwise
non-deterministic decision reproducible; route/place facts still pass through
the real server-owned executors and stores.
"""
from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, patch

from app.services.agent import discovery_store
from app.services.agent import session as session_module
from app.services.agent import trip_state as trip_state_module
from app.services.agent.tools import ToolContext, ToolResult
from app.services.agent.tools.location_resolution import ResolvedPlace
from app.services.agent.tools.places import discover_places
from app.services.agent.tools.route import prepare_route_branches, prepare_route_options

from tests.agent_route_decision_test_support import (
    AgentRouteDecisionTestMixin,
    _prepared_leg,
    _present_round,
    _route,
    _route_goal_round,
    provider_search_result,
)
from tests.conversation.conversation_matrix_harness import route_cards, run_turn


class AgentRouteBranchReliabilityTests(AgentRouteDecisionTestMixin, unittest.IsolatedAsyncioTestCase):
    async def test_plural_destination_reference_requires_a_comparison_set(self) -> None:
        session_id, session = session_module.new_session()
        ctx = ToolContext(
            session=session,
            session_id=session_id,
            turn_id="single-plural-reference",
            now_et="2026-08-20T12:00:00-04:00",
            origin={"lat": 40.672, "lng": -73.98},
            rider_message="Take this branch.",
        )
        result = await prepare_route_options.execute(
            {
                "origin": "user",
                "destination": None,
                "destination_place_id": None,
                "destination_place_ids": ["place-only"],
                "destination_source": "current_turn",
            },
            ctx,
        )
        assert not result.ok
        assert "comparison-only" in (result.error or "")

    async def test_nearby_branch_pool_leaves_route_choice_to_sonnet(self) -> None:
        session_id, session = session_module.new_session()
        origin = {"lat": 40.672, "lng": -73.98}
        ctx = ToolContext(
            session=session,
            session_id=session_id,
            turn_id="nearby-branch-route",
            now_et="2026-08-20T12:00:00-04:00",
            origin=origin,
            rider_message="Find a Kyuramen branch with less walking.",
        )
        branches = [
            {
                "name": "Kyuramen Forest Hills",
                "address": "71-50 Austin St, Queens, NY",
                "location": {"latitude": 40.718, "longitude": -73.845},
                "place_id": "provider-forest-hills",
                "rating": 4.8,
                "review_count": 2_000,
                "open_now": True,
            },
            {
                "name": "Kyuramen Prospect Heights",
                "address": "550 Vanderbilt Ave, Brooklyn, NY",
                "location": {"latitude": 40.679, "longitude": -73.969},
                "place_id": "provider-prospect-heights",
                "rating": 4.6,
                "review_count": 900,
                "open_now": True,
            },
            {
                "name": "Kyuramen Park Slope",
                "address": "219 5th Ave, Brooklyn, NY",
                "location": {"latitude": 40.6725, "longitude": -73.9785},
                "place_id": "provider-park-slope",
                "rating": 4.5,
                "review_count": 500,
                "open_now": True,
            },
        ]
        with patch.object(
            discover_places.search_local_places,
            "_provider_search",
            new=AsyncMock(return_value=provider_search_result(*branches)),
        ):
            discovery = await discover_places.execute(
                {
                    "operation": "search",
                    "query": "Kyuramen",
                    "scope": {"kind": "current_location", "values": []},
                    "open_now": None,
                    "max_results": 2,
                    "candidate_names": [],
                },
                ctx,
            )
        assert discovery.ok, discovery.error
        nearby_places = discovery.data["places"]
        nearby_names = {place["name"] for place in nearby_places}
        assert nearby_names == {"Kyuramen Prospect Heights", "Kyuramen Park Slope"}
        assert "Kyuramen Forest Hills" not in nearby_names
        park_slope_id = next(
            place["place_id"]
            for place in nearby_places
            if place["name"] == "Kyuramen Park Slope"
        )
        prospect_heights_id = next(
            place["place_id"]
            for place in nearby_places
            if place["name"] == "Kyuramen Prospect Heights"
        )
        park_slope = _prepared_leg(
            destination="Kyuramen Park Slope",
            destination_place=ResolvedPlace(
                "Kyuramen Park Slope",
                40.6725,
                -73.9785,
                "discovery",
                place_id=park_slope_id,
            ),
            routes=[
                _route(route_ids=("Q",), walking_seconds=120, total_seconds=2100),
            ],
        )
        prospect_heights = _prepared_leg(
            destination="Kyuramen Prospect Heights",
            destination_place=ResolvedPlace(
                "Kyuramen Prospect Heights",
                40.679,
                -73.969,
                "discovery",
                place_id=prospect_heights_id,
            ),
            routes=[
                _route(route_ids=("Q",), walking_seconds=600, total_seconds=1800),
            ],
        )
        rounds = [
            _route_goal_round(
                destination=None,
                destination_place_ids=[prospect_heights_id, park_slope_id],
                routing_preference="LESS_WALKING",
            ),
            _present_round(
                "cd_park_model",
                lead_in="I prioritized this option because it involves less walking.",
                reason_code="less_walking",
            ),
        ]
        trace = self.loop.TurnTrace()
        with patch(
            "app.services.agent.candidate_store.new_candidate_id",
            side_effect=["cd_park_scorer", "cd_park_model"],
        ):
            events, _trace = await run_turn(
                self.loop,
                session=session,
                session_id=session_id,
                turn_id="nearby-branch-route",
                message="Take the Kyuramen branch with less walking.",
                rounds=rounds,
                prepare_legs=[prospect_heights, park_slope],
                trace=trace,
                origin=origin,
            )

        assert len(route_cards(events)) == 1, trace.capability_attempts
        card = route_cards(events)[0]
        assert "selected_candidate_id" not in card.selection_decision
        assert "selected_candidate_index" not in card.selection_decision
        assert "base_score" not in card.selection_decision
        assert "final_score" not in card.selection_decision
        assert "penalties" not in card.selection_decision
        assert "evidence_ids" not in card.selection_decision
        assert card.itinerary["selection_decision"] == card.selection_decision
        assert card.selection_decision["selection_source"] == "model"
        assert card.selection_decision["reason_code"] == "less_walking"
        assert card.destination["label"] == "Kyuramen Park Slope"
        assert card.itinerary["total_duration_seconds"] == 2100
        assert card.itinerary["total_street_walking_seconds"] == 120
        prepare_calls = [
            tool_input
            for name, tool_input in trace.tool_calls
            if name == "prepare_route_options"
        ]
        assert prepare_calls[0]["destination_place_ids"] == [prospect_heights_id, park_slope_id]
        token = next(event for event in events if event.type == "token")
        assert "less walking" in token.text.casefold()
        state = trip_state_module.get_trip_state(session)
        assert state["selected_candidate_id"] == "cd_park_model"
        assert state["destination"] == "Kyuramen Park Slope"
        assert state["selected_place_id"] == park_slope_id
        assert trace.terminal_resolution["selection_source"] == "model"

    async def test_branch_route_budget_is_global_and_keeps_each_viable_branch(
        self,
    ) -> None:
        """Compare several routes per branch without multiplying the candidate budget."""

        session_id, session = session_module.new_session()
        origin = {"lat": 40.672, "lng": -73.98}
        ctx = ToolContext(
            session=session,
            session_id=session_id,
            turn_id="nearby-branch-budget",
            now_et="2026-08-20T12:00:00-04:00",
            origin=origin,
            rider_message="Compare nearby Kyuramen branches with less walking.",
        )
        discovery_set_id = discovery_store.store_discovery_set(
            session_id=session_id,
            session=session,
            query="Kyuramen",
            search_scope={"kind": "current_location", "values": []},
            places=[
                {
                    "place_id": "place-forest-hills",
                    "name": "Kyuramen Forest Hills",
                    "address": "71-50 Austin St, Queens, NY",
                    "latitude": 40.718,
                    "longitude": -73.845,
                },
                {
                    "place_id": "place-prospect-heights",
                    "name": "Kyuramen Prospect Heights",
                    "address": "550 Vanderbilt Ave, Brooklyn, NY",
                    "latitude": 40.679,
                    "longitude": -73.969,
                },
                {
                    "place_id": "place-park-slope",
                    "name": "Kyuramen Park Slope",
                    "address": "219 5th Ave, Brooklyn, NY",
                    "latitude": 40.6725,
                    "longitude": -73.9785,
                },
            ],
        )
        trip_state_module.bind_discovery_set(session, discovery_set_id)
        stored_places = discovery_store.load_discovery_set(
            discovery_set_id, session_id=session_id
        )["places"]
        forest_id = stored_places[0]["place_id"]
        prospect_id = stored_places[1]["place_id"]
        park_id = stored_places[2]["place_id"]

        prospect_routes = [
            _route(route_ids=("Q",), walking_seconds=120, total_seconds=1800),
            _route(route_ids=("Q",), walking_seconds=180, total_seconds=1860),
            _route(route_ids=("Q",), walking_seconds=240, total_seconds=1920),
        ]
        park_routes = [
            _route(route_ids=("Q",), walking_seconds=120, total_seconds=2100),
            _route(route_ids=("Q",), walking_seconds=180, total_seconds=2160),
            _route(route_ids=("Q",), walking_seconds=240, total_seconds=2220),
        ]
        # The provider route reports no WALK step, so a raw route-only check
        # sees zero street walking. Canonicalization restores the long access
        # and egress walks from endpoint geometry, making this route violate
        # the active five-minute walking tolerance.
        park_routes[0] = [
            {
                "type": "SUBWAY",
                "route_id": "Q",
                "departure_stop": "Q origin",
                "arrival_stop": "Q destination",
                "duration_seconds": 1200,
                "departure_time_iso": "2026-08-20T12:00:00-04:00",
                "arrival_time_iso": "2026-08-20T12:20:00-04:00",
                "start_point": {"latitude": 40.705, "longitude": -73.95},
                "end_point": {"latitude": 40.705, "longitude": -73.95},
                "route_total_seconds": 1200,
            }
        ]
        prospect = _prepared_leg(
            destination="Kyuramen Prospect Heights",
            destination_place=ResolvedPlace(
                "Kyuramen Prospect Heights",
                40.679,
                -73.969,
                "discovery",
                place_id=prospect_id,
            ),
            routes=prospect_routes,
        )
        park = _prepared_leg(
            destination="Kyuramen Park Slope",
            destination_place=ResolvedPlace(
                "Kyuramen Park Slope",
                40.6725,
                -73.9785,
                "discovery",
                place_id=park_id,
            ),
            routes=park_routes,
        )
        prospect.timings = {"route_provider_ms": 10.0}
        park.timings = {"route_provider_ms": 20.0}
        route_input = {
            "origin": "user",
            "destination": None,
            "destination_place_id": None,
            "destination_place_ids": [prospect_id, park_id],
            "destination_source": "current_turn",
            "routing_preference": "LESS_WALKING",
            "max_candidates": 5,
            "walking_tolerance_minutes": 5,
            "waypoints": None,
            "avoid_crowds": False,
            "goal_key": "route",
        }
        with (
            patch.object(
                prepare_route_branches,
                "prepare_single_leg",
                new=AsyncMock(
                    side_effect=[
                        ToolResult(
                            ok=False,
                            error="no transit route found between those points",
                        ),
                        prospect,
                        park,
                    ]
                ),
            ),
            patch.object(
                prepare_route_branches,
                "build_preparation_dependencies",
                wraps=prepare_route_branches.build_preparation_dependencies,
            ) as build_dependencies,
            patch(
                "app.services.agent.candidate_store.new_candidate_id",
                side_effect=[
                    "cd_prospect_1",
                    "cd_prospect_2",
                    "cd_prospect_3",
                    "cd_park_1",
                    "cd_park_2",
                ],
            ),
        ):
            result = await prepare_route_options.execute(route_input, ctx)

        assert result.ok, result.error
        assert result.data["candidate_count"] == 5
        assert len(result.data["candidates"]) == 5
        assert all(candidate["comparison"]["timing"]["walking_minutes"] <= 5 for candidate in result.data["candidates"])
        branch_ids = [
            candidate["destination_place_id"]
            for candidate in result.data["candidates"]
        ]
        assert branch_ids.count(prospect_id) >= 1
        assert branch_ids.count(park_id) >= 1
        branch_coverage = result.data["branch_coverage"]
        assert any(row.get("place_id") == forest_id for row in branch_coverage), branch_coverage
        assert next(row for row in branch_coverage if row["place_id"] == forest_id)["status"] == "unavailable", branch_coverage
        assert "selected_candidate_id" not in result.data
        assert build_dependencies.call_count == 1
        assert result.timings["route_provider_ms"] == 30.0

    async def test_failed_branch_is_visible_as_coverage_gap(self) -> None:
        session_id, session = session_module.new_session()
        discovery_set_id = discovery_store.store_discovery_set(
            session_id=session_id,
            session=session,
            query="Kyuramen",
            search_scope={"kind": "current_location", "values": []},
            places=[
                {
                    "name": "Kyuramen Prospect Heights",
                    "address": "550 Vanderbilt Ave, Brooklyn, NY",
                    "latitude": 40.679,
                    "longitude": -73.969,
                },
                {
                    "name": "Kyuramen Park Slope",
                    "address": "219 5th Ave, Brooklyn, NY",
                    "latitude": 40.6725,
                    "longitude": -73.9785,
                },
            ],
        )
        trip_state_module.bind_discovery_set(session, discovery_set_id)
        stored_places = discovery_store.load_discovery_set(
            discovery_set_id, session_id=session_id
        )["places"]
        prospect_id = stored_places[0]["place_id"]
        park_id = stored_places[1]["place_id"]
        prepared = _prepared_leg(
            destination="Kyuramen Park Slope",
            destination_place=ResolvedPlace(
                "Kyuramen Park Slope",
                40.6725,
                -73.9785,
                "discovery",
                place_id=park_id,
            ),
            routes=[
                _route(route_ids=("Q",), walking_seconds=120, total_seconds=2100)
            ],
        )
        rounds = [
            _route_goal_round(
                destination=None,
                destination_place_ids=[prospect_id, park_id],
                routing_preference="LESS_WALKING",
            ),
            _present_round(
                "cd_park_only",
                lead_in="I chose this because it involves less walking.",
                reason_code="less_walking",
            ),
            _present_round(
                "cd_park_only",
                lead_in="I chose this because it involves less walking.",
                reason_code="less_walking",
            ),
        ]
        trace = self.loop.TurnTrace()
        with patch(
            "app.services.agent.candidate_store.new_candidate_id",
            return_value="cd_park_only",
        ):
            events, _trace = await run_turn(
                self.loop,
                session=session,
                session_id=session_id,
                turn_id="partial-branch",
                message="Compare both Kyuramen branches with less walking.",
                rounds=rounds,
                prepare_legs=[
                    ToolResult(ok=False, error="no transit route found between those points"),
                    prepared,
                ],
                trace=trace,
                origin={"lat": 40.672, "lng": -73.98},
            )

        cards = route_cards(events)
        assert len(cards) == 1, trace.capability_attempts
        decision = cards[0].selection_decision
        assert "selected_candidate_id" not in decision
        assert "selected_candidate_index" not in decision
        assert cards[0].itinerary["selection_decision"] == decision
        assert decision["selection_source"] == "deterministic_fallback"
        assert decision["reason_code"] == "coverage_gap"
        assert cards[0].destination["label"] == "Kyuramen Park Slope"
        rider_text = " ".join(
            event.text for event in events if event.type == "token"
        ).casefold()
        assert "could not be checked" in rider_text
        assert trace.terminal_resolution["selection_source"] == "deterministic_fallback"
