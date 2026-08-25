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
from app.services.agent.tools.places import discover_places
from app.services.agent.tools.route import prepare_route_options
from app.services.agent.tools.route import prepare_route_branches
from app.services.agent.tools.location_resolution import ResolvedPlace
from app.services.trips import scoring
from tests.conversation.conversation_matrix_harness import route_cards, run_turn

from tests.agent_route_decision_test_support import (
    AgentRouteDecisionTestMixin,
    _prepared_leg,
    _present_round,
    _route,
    _route_goal_round,
)


class AgentRouteStageAReliabilityTests(
    AgentRouteDecisionTestMixin, unittest.IsolatedAsyncioTestCase
):
    async def test_route_preparation_rejects_missing_session_identity(self) -> None:
        result = await prepare_route_options.execute(
            {"destination": "Madison Square Garden"},
            ToolContext(session={}, origin={"lat": 40.7, "lng": -74.0}),
        )

        self.assertFalse(result.ok)
        self.assertEqual(result.error, "session is required for route preparation")

    async def test_stage_a_excludes_absurd_current_location_route_before_model_choice(
        self,
    ) -> None:
        session_id, session = session_module.new_session()
        origin = {"lat": 40.672, "lng": -73.98}
        ctx = ToolContext(
            session=session,
            session_id=session_id,
            turn_id="nearby-absurd-route",
            now_et="2026-08-20T12:00:00-04:00",
            origin=origin,
            rider_message="Compare nearby Kyuramen branches with less walking.",
        )
        branches = [
            {
                "name": "Kyuramen Forest Hills",
                "address": "71-50 Austin St, Queens, NY",
                "latitude": 40.718,
                "longitude": -73.845,
                "place_id": "provider-forest-hills",
            },
            {
                "name": "Kyuramen Park Slope",
                "address": "219 5th Ave, Brooklyn, NY",
                "latitude": 40.6725,
                "longitude": -73.9785,
                "place_id": "provider-park-slope",
            },
        ]
        with patch.object(
            discover_places.search_local_places,
            "_provider_search",
            new=AsyncMock(return_value=ToolResult(ok=True, data={"places": branches})),
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
        self.assertTrue(discovery.ok, discovery.error)
        nearby_places = discovery.data["places"]
        self.assertEqual(
            {place["name"] for place in nearby_places},
            {"Kyuramen Forest Hills", "Kyuramen Park Slope"},
        )
        forest_id = next(
            place["place_id"]
            for place in nearby_places
            if place["name"] == "Kyuramen Forest Hills"
        )
        park_id = next(
            place["place_id"]
            for place in nearby_places
            if place["name"] == "Kyuramen Park Slope"
        )
        forest = _prepared_leg(
            destination="Kyuramen Forest Hills",
            destination_place=ResolvedPlace(
                "Kyuramen Forest Hills",
                40.718,
                -73.845,
                "discovery",
                place_id=forest_id,
            ),
            routes=[_route(route_ids=("Q",), walking_seconds=60, total_seconds=7200)],
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
            routes=[_route(route_ids=("Q",), walking_seconds=120, total_seconds=2100)],
        )
        rounds = [
            _route_goal_round(
                destination=None,
                destination_place_ids=[forest_id, park_id],
                routing_preference="LESS_WALKING",
                walking_tolerance_minutes=5,
            ),
            _present_round(
                "cd_forest_model",
                lead_in="I chose this because it involves less walking.",
                reason_code="less_walking",
            ),
            _present_round(
                "cd_park_stage_a",
                lead_in=(
                    "I chose this nearby option because it keeps the overall travel "
                    "burden reasonable while keeping walking within your limit."
                ),
                reason_code="reasonable_local_option",
            ),
        ]
        trace = self.loop.TurnTrace()
        original_finalized_score = scoring.finalized_route_score

        def favorable_finalized_score(**kwargs):
            row = original_finalized_score(**kwargs)
            itinerary = kwargs.get("itinerary") or {}
            if itinerary.get("total_duration_seconds") == 7200:
                row["score"] = -10_000
            return row

        with (
            patch(
                "app.services.agent.candidate_store.new_candidate_id",
                return_value="cd_park_stage_a",
            ),
            patch.object(
                scoring,
                "finalized_route_score",
                side_effect=favorable_finalized_score,
            ),
        ):
            events, _trace = await run_turn(
                self.loop,
                session=session,
                session_id=session_id,
                turn_id="nearby-absurd-route",
                message="Compare nearby Kyuramen branches with less walking.",
                rounds=rounds,
                prepare_legs=[forest, park],
                trace=trace,
                origin=origin,
            )

        cards = route_cards(events)
        self.assertEqual(len(cards), 1, trace.capability_attempts)
        card = cards[0]
        self.assertEqual(card.destination["label"], "Kyuramen Park Slope")
        self.assertEqual(
            card.selection_decision["selection_source"],
            "model",
        )
        self.assertEqual(
            card.selection_decision["reason_code"], "reasonable_local_option"
        )
        self.assertNotIn("selected_candidate_id", card.selection_decision)
        self.assertNotIn("selected_candidate_index", card.selection_decision)
        self.assertEqual(card.itinerary["selection_decision"], card.selection_decision)
        state = trip_state_module.get_trip_state(session)
        self.assertEqual(state["selected_candidate_id"], "cd_park_stage_a")
        rider_text = " ".join(
            event.text for event in events if event.type == "token"
        ).casefold()
        self.assertIn("overall travel burden", rider_text)
        present_calls = [
            tool_input
            for name, tool_input in trace.tool_calls
            if name == "present_route"
        ]
        self.assertEqual(present_calls[0]["candidate_id"], "cd_forest_model")
        self.assertEqual(present_calls[-1]["candidate_id"], "cd_park_stage_a")
        self.assertEqual(trace.terminal_resolution["selection_source"], "model")

    async def test_structured_brooklyn_discovery_replaces_prior_scope_for_branches(
        self,
    ) -> None:
        session_id, session = session_module.new_session()
        ctx = ToolContext(
            session=session,
            session_id=session_id,
            turn_id="brooklyn-scope",
            now_et="2026-08-20T12:00:00-04:00",
            origin={"lat": 40.672, "lng": -73.98},
            rider_message="Compare the Brooklyn locations.",
        )
        prior_set = discovery_store.store_discovery_set(
            session_id=session_id,
            session=session,
            query="Kyuramen",
            search_scope={"kind": "nyc", "values": []},
            places=[
                {
                    "name": "Old Manhattan branch",
                    "address": "1 Example St, Manhattan, NY",
                    "latitude": 40.75,
                    "longitude": -73.99,
                    "provider_place_id": "provider-old-manhattan",
                },
                {
                    "name": "Old Queens branch",
                    "address": "2 Example St, Queens, NY",
                    "latitude": 40.74,
                    "longitude": -73.87,
                    "provider_place_id": "provider-old-queens",
                },
            ],
        )
        trip_state_module.bind_discovery_set(session, prior_set)
        brooklyn_places = [
            {
                "name": "Brooklyn Prospect branch",
                "address": "10 Bergen St, Brooklyn, NY",
                "location": {"latitude": 40.68, "longitude": -73.97},
                "place_id": "provider-brooklyn-prospect",
            },
            {
                "name": "Brooklyn Park branch",
                "address": "20 5th Ave, Brooklyn, NY",
                "location": {"latitude": 40.67, "longitude": -73.98},
                "place_id": "provider-brooklyn-park",
            },
        ]
        with patch.object(
            discover_places.search_local_places,
            "_provider_search",
            new=AsyncMock(
                return_value=ToolResult(ok=True, data={"places": brooklyn_places})
            ),
        ):
            discovery = await discover_places.execute(
                {
                    "operation": "search",
                    "query": "Kyuramen",
                    "scope": {"kind": "boroughs", "values": ["Brooklyn"]},
                    "open_now": None,
                    "max_results": 2,
                    "candidate_names": [],
                },
                ctx,
            )
        self.assertTrue(discovery.ok, discovery.error)
        new_set = discovery.data["discovery_set_id"]
        self.assertNotEqual(new_set, prior_set)
        self.assertEqual(
            trip_state_module.get_trip_state(session)["active_discovery_set_id"],
            new_set,
        )
        new_record = discovery_store.load_discovery_set(new_set, session_id=session_id)
        prior_record = discovery_store.load_discovery_set(
            prior_set, session_id=session_id
        )
        self.assertIsNotNone(new_record)
        self.assertIsNotNone(prior_record)
        new_places = list(new_record["places"])
        old_id = prior_record["places"][0]["place_id"]
        new_ids = [place["place_id"] for place in new_places]
        prepared = [
            _prepared_leg(
                destination=place["name"],
                destination_place=ResolvedPlace(
                    place["name"],
                    place["latitude"],
                    place["longitude"],
                    "discovery",
                    place_id=place["place_id"],
                ),
                routes=[
                    _route(route_ids=("Q",), walking_seconds=120, total_seconds=2100)
                ],
            )
            for place in new_places
        ]
        with patch.object(
            prepare_route_branches,
            "prepare_single_leg",
            new=AsyncMock(side_effect=prepared),
        ):
            result = await prepare_route_options.execute(
                {
                    "origin": "user",
                    "destination": "an old Manhattan branch",
                    "destination_place_ids": [new_ids[0], new_ids[1]],
                    "destination_source": "current_turn",
                },
                ctx,
            )
        self.assertTrue(result.ok, result.error)
        self.assertEqual(set(result.data["destination_place_ids"]), set(new_ids))
        self.assertNotIn(old_id, result.data["destination_place_ids"])
