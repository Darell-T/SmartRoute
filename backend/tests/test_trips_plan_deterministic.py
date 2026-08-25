"""Deterministic Live Map route selection for ``POST /api/trip``.

Proves the non-conversational endpoint consumes the same canonical
infrastructure as the agent path (semantic transfer normalization,
deterministic scoring, hard constraints, indexed incident evidence, canonical
itinerary projection) and makes ZERO model/advisor selection calls. It also
freezes the response contract consumed by the frontend's
``normalizeTripCandidates``.
"""

import unittest
from unittest.mock import AsyncMock, patch

from tests.test_trips_enrichment import (
    _load_trips_module,
    _payload,
    _request_with_gtfs,
)


def _subway_step(route_id, minutes, depart="14 St", arrive="23 St"):
    return {
        "type": "SUBWAY",
        "route_id": route_id,
        "departure_stop": depart,
        "arrival_stop": arrive,
        "route_total_minutes": minutes,
    }


def _transfer_route(minutes=30):
    """Subway -> walk -> subway with explicit stop ids so semantic transfer
    normalization classifies the walk as a same-station transfer. R14 is a
    canonical PARENT station id (never a platform id), so equal parent ids are
    same_station -- and like same_platform it stays an in-station transfer."""
    return [
        {
            "type": "SUBWAY",
            "route_id": "Q",
            "departure_stop": "Times Sq-42 St",
            "arrival_stop": "14 St-Union Sq",
            "departure_stop_id": "R16",
            "arrival_stop_id": "R14",
            "route_total_minutes": minutes,
        },
        {
            "type": "WALK",
            "duration_seconds": 300,
            "start_point": {"latitude": 40.7353, "longitude": -73.9901},
            "end_point": {"latitude": 40.7353, "longitude": -73.9900},
        },
        {
            "type": "SUBWAY",
            "route_id": "4",
            "departure_stop": "14 St-Union Sq",
            "arrival_stop": "Brooklyn Bridge",
            "departure_stop_id": "R14",
            "arrival_stop_id": "R13",
            "route_total_minutes": minutes,
        },
    ]


async def _bus_fetch(route_id):
    return {"canned": []}


class TripPlanDeterministicTests(unittest.IsolatedAsyncioTestCase):
    async def test_selection_orders_by_minutes_then_transfers_then_index(self):
        # (a) fewest minutes wins.
        trips = _load_trips_module(
            _bus_fetch,
            routes=[_transfer_route(30), [_subway_step("A", 25)]],
        )
        result = await trips.plan_trip(_request_with_gtfs(), _payload(trips))
        self.assertEqual(result["selected_route_index"], 1)
        self.assertTrue(result["route_candidates"][1]["is_recommended"])
        self.assertEqual(result["route_candidates"][1]["total_minutes"], 25)

        # (b) minutes tie -> fewer transfers wins.
        trips = _load_trips_module(
            _bus_fetch,
            routes=[_transfer_route(20), [_subway_step("A", 20)]],
        )
        result = await trips.plan_trip(_request_with_gtfs(), _payload(trips))
        self.assertEqual(result["selected_route_index"], 1)
        self.assertEqual(result["route_candidates"][1]["score_breakdown"]["transfers"], 0)
        self.assertEqual(result["route_candidates"][0]["score_breakdown"]["transfers"], 1)

        # (c) minutes + transfers tie -> lowest index (stable tie-break).
        trips = _load_trips_module(
            _bus_fetch,
            routes=[_transfer_route(20), _transfer_route(20)],
        )
        result = await trips.plan_trip(_request_with_gtfs(), _payload(trips))
        self.assertEqual(result["selected_route_index"], 0)
        self.assertTrue(result["route_candidates"][0]["is_recommended"])

    async def test_plan_trip_makes_zero_model_selection_calls(self):
        trips = _load_trips_module(
            _bus_fetch,
            routes=[_transfer_route(30), [_subway_step("A", 25)]],
        )
        self.assertFalse(hasattr(trips, "ai_advisor"), "trips.py must not import ai_advisor")
        self.assertFalse(
            hasattr(trips, "production_shadow"),
            "trips.py must not import production_shadow",
        )
        self.assertFalse(
            hasattr(
                trips.direct_plan.candidates,
                "_parse_candidate_analysis",
            ),
            "evaluation-only control parsing must stay outside production trips",
        )
        strip_controls = patch.object(
            trips.direct_plan.candidates,
            "_strip_model_control_blocks",
            new=lambda *args, **kwargs: (_ for _ in ()).throw(
                AssertionError("[ROUTE:N] stripping must never run")
            ),
        )
        with patch(
            "evaluation.route_intelligence.advisor.stream_recommendation",
            new=AsyncMock(side_effect=AssertionError("advisor must never be called")),
        ) as advisor, patch(
            "evaluation.route_intelligence.trip_shadow.run_trip_shadow",
            new=AsyncMock(side_effect=AssertionError("shadow baseline must never be called")),
        ) as shadow, strip_controls:
            result = await trips.plan_trip(_request_with_gtfs(), _payload(trips))
        advisor.assert_not_awaited()
        shadow.assert_not_awaited()
        self.assertIsInstance(result, dict)
        self.assertIn("route_candidates", result)

    async def test_recommendation_is_deterministic_without_control_tags(self):
        trips = _load_trips_module(
            _bus_fetch,
            routes=[_transfer_route(30), [_subway_step("A", 25)]],
        )
        result = await trips.plan_trip(_request_with_gtfs(), _payload(trips))
        chosen = result["route_candidates"][result["selected_route_index"]]
        self.assertIsInstance(result["recommendation"], str)
        self.assertTrue(result["recommendation"])
        self.assertNotIn("[ROUTE:", result["recommendation"])
        self.assertNotIn("[CANDIDATE_ANALYSIS]", result["recommendation"])
        self.assertEqual(result["recommendation"], chosen["recommendation_reason"])

        # Same input -> same explanation (no model, no randomness).
        again = await trips.plan_trip(_request_with_gtfs(), _payload(trips))
        self.assertEqual(again["recommendation"], result["recommendation"])
        self.assertEqual(again["selected_route_index"], result["selected_route_index"])

    async def test_semantic_transfer_normalization_reaches_scoring_and_itinerary(self):
        trips = _load_trips_module(_bus_fetch, routes=[_transfer_route(30)])
        result = await trips.plan_trip(_request_with_gtfs(), _payload(trips))
        chosen = result["route_candidates"][0]
        self.assertTrue(chosen["is_recommended"])

        walk = chosen["steps"][1]
        self.assertEqual(walk["type"], "WALK")
        self.assertEqual(walk["transfer_kind"], "same_station")
        self.assertEqual(walk["semantic_transfer_group_id"], "transfer_0")
        self.assertEqual(walk["transfer_semantics"]["kind"], "same_station")
        self.assertEqual(walk["transfer_semantics"]["from_parent_station"], "R14")
        self.assertEqual(walk["transfer_semantics"]["to_parent_station"], "R14")
        self.assertEqual(walk["transfer_semantics"]["total_seconds"], 300)
        self.assertEqual(walk["transfer_semantics"]["street_walking_seconds"], 0)
        self.assertEqual(walk["transfer_semantics"]["in_station_transfer_seconds"], 300)

        # Scoring consumed the semantic transfer: one transfer, no street walk.
        self.assertEqual(chosen["score_breakdown"]["transfers"], 1)
        itinerary = chosen["itinerary"]
        self.assertEqual(itinerary["transfer_count"], 1)
        self.assertEqual(itinerary["total_street_walking_seconds"], 0)
        self.assertEqual(itinerary["total_in_station_transfer_seconds"], 300)

    async def test_response_contract_matches_normalize_trip_candidates(self):
        trips = _load_trips_module(
            _bus_fetch,
            routes=[_transfer_route(30), [_subway_step("A", 25)]],
        )
        result = await trips.plan_trip(_request_with_gtfs(), _payload(trips))
        self.assertEqual(
            set(result),
            {
                "recommendation",
                "route",
                "selected_route_index",
                "route_candidates",
                "alerts",
                "selection_decision",
            },
        )
        self.assertIsInstance(result["selected_route_index"], int)
        self.assertIsInstance(result["route"], list)
        self.assertIsInstance(result["alerts"], list)
        self.assertGreaterEqual(len(result["route_candidates"]), 2)
        for candidate in result["route_candidates"]:
            self.assertTrue(candidate["id"])
            self.assertIsInstance(candidate["index"], int)
            self.assertIsInstance(candidate["steps"], list)
            self.assertTrue(candidate["itinerary"]["itinerary_id"])
            self.assertIsInstance(candidate["total_minutes"], int)
            self.assertIsInstance(candidate["score_breakdown"]["transfers"], int)
        self.assertEqual(
            result["route_candidates"][result["selected_route_index"]]["index"],
            result["selected_route_index"],
        )
        self.assertIs(
            result["route"],
            result["route_candidates"][result["selected_route_index"]]["steps"],
        )

        # The canonical selection_decision describes the same server-owned
        # candidate as the top-level route / index / recommendation.
        decision = result["selection_decision"]
        chosen_index = result["selected_route_index"]
        chosen_candidate = result["route_candidates"][chosen_index]
        self.assertEqual(decision["selected_candidate_index"], chosen_index)
        self.assertEqual(decision["selected_candidate_id"], f"candidate-{chosen_index}")
        self.assertEqual(decision["selected_candidate_id"], chosen_candidate["id"])
        self.assertEqual(decision["final_score"], float(chosen_candidate["selection_score"]))
        self.assertEqual(
            decision["base_score"],
            float(chosen_candidate["score_breakdown"]["duration_minutes"]),
        )
        self.assertEqual(result["recommendation"], chosen_candidate["recommendation_reason"])
        self.assertIs(result["route"], chosen_candidate["steps"])
        self.assertEqual(
            chosen_candidate["itinerary"]["itinerary_id"],
            chosen_candidate["id"],
        )
        # The single canonical selection_decision rides on the top level and
        # the chosen itinerary as the exact same record.
        self.assertIs(decision, chosen_candidate["itinerary"]["selection_decision"])
        # Alternates never claim they were selected.
        for index, candidate in enumerate(result["route_candidates"]):
            if index == chosen_index:
                continue
            self.assertIsNone(candidate["itinerary"].get("selection_decision"))
            self.assertEqual(candidate["structured_recommendation_reasons"], [])

    async def test_selection_decision_is_attached_only_to_chosen_itinerary(self):
        trips = _load_trips_module(
            _bus_fetch,
            routes=[_transfer_route(30), [_subway_step("A", 25)]],
        )
        result = await trips.plan_trip(_request_with_gtfs(), _payload(trips))
        chosen_index = result["selected_route_index"]
        chosen_candidate = result["route_candidates"][chosen_index]
        chosen_decision = chosen_candidate["itinerary"]["selection_decision"]
        self.assertEqual(chosen_decision["selected_candidate_index"], chosen_index)
        self.assertEqual(chosen_decision["selected_candidate_id"], chosen_candidate["id"])
        self.assertIs(result["selection_decision"], chosen_decision)
        for index, candidate in enumerate(result["route_candidates"]):
            if index == chosen_index:
                continue
            self.assertIsNone(candidate["itinerary"].get("selection_decision"))

    async def test_empty_routes_return_404_without_fabricated_winner(self):
        trips = _load_trips_module(_bus_fetch, routes=[])
        with self.assertRaises(trips.HTTPException) as error:
            await trips.plan_trip(_request_with_gtfs(), _payload(trips))
        self.assertEqual(error.exception.status_code, 404)
        self.assertEqual(error.exception.detail, "No route found")

    async def test_no_valid_candidate_returns_404_without_fabricated_winner(self):
        trips = _load_trips_module(
            _bus_fetch,
            routes=[_transfer_route(20), [_subway_step("A", 20)]],
        )
        with patch.object(
            trips.direct_plan,
            "route_constraints",
            return_value={"satisfied": False, "violations": ["walking_tolerance"]},
        ):
            with self.assertRaises(trips.HTTPException) as error:
                await trips.plan_trip(_request_with_gtfs(), _payload(trips))
        self.assertEqual(error.exception.status_code, 404)
        self.assertEqual(error.exception.detail, "No route found")

    async def test_direct_planning_invokes_shared_prepare_single_leg(self):
        """The direct path must demonstrably call the shared preparation."""
        trips = _load_trips_module(
            _bus_fetch,
            routes=[_transfer_route(30), [_subway_step("A", 25)]],
        )
        real_prepare = trips.direct_plan.prepare_single_leg
        with patch.object(
            trips.direct_plan,
            "prepare_single_leg",
            new=AsyncMock(wraps=real_prepare),
        ) as prepare:
            result = await trips.plan_trip(_request_with_gtfs(), _payload(trips))
        prepare.assert_awaited_once()
        tool_input = prepare.await_args.args[0]
        self.assertEqual(tool_input["origin"], "user")
        self.assertEqual(tool_input["destination"], "Test Dest")
        self.assertEqual(tool_input["routing_preference"], "FEWER_TRANSFERS")
        self.assertIsNotNone(prepare.await_args.kwargs.get("resolved_origin"))
        self.assertIsNotNone(prepare.await_args.kwargs.get("resolved_destination"))
        self.assertEqual(result["selected_route_index"], 1)

    async def test_top_scored_valid_candidate_gets_lowest_final_score(self):
        trips = _load_trips_module(
            _bus_fetch,
            routes=[_transfer_route(30), [_subway_step("A", 25)]],
        )
        result = await trips.plan_trip(_request_with_gtfs(), _payload(trips))
        self.assertEqual(result["selected_route_index"], 1)
        self.assertEqual(
            result["selection_decision"]["selection_reason"],
            "lowest_final_score",
        )

    async def test_invalid_best_score_skips_to_next_valid_with_hard_constraint(self):
        trips = _load_trips_module(
            _bus_fetch,
            routes=[_transfer_route(30), [_subway_step("A", 25)]],
        )
        calls = {"count": 0}

        def _fake_constraints(route, tool_input):
            calls["count"] += 1
            if calls["count"] == 1:
                return {"satisfied": False, "violations": ["walking_tolerance"]}
            return {"satisfied": True, "violations": []}

        with patch.object(
            trips.direct_plan,
            "route_constraints",
            side_effect=_fake_constraints,
        ):
            result = await trips.plan_trip(_request_with_gtfs(), _payload(trips))
        # Best scored (25 min, index 1) violates a hard constraint; the next
        # valid candidate (index 0) wins with the hard_constraint reason.
        self.assertEqual(result["selected_route_index"], 0)
        self.assertTrue(result["route_candidates"][0]["is_recommended"])
        self.assertEqual(result["selection_decision"]["selection_reason"], "hard_constraint")
        self.assertEqual(
            result["selection_decision"]["selected_candidate_index"],
            result["selected_route_index"],
        )

    async def test_single_candidate_uses_neutral_deterministic_recommendation(self):
        trips = _load_trips_module(_bus_fetch, routes=[_transfer_route(30)])
        result = await trips.plan_trip(_request_with_gtfs(), _payload(trips))
        chosen = result["route_candidates"][0]
        self.assertTrue(result["recommendation"])
        self.assertIn("Recommended as the best valid route", result["recommendation"])
        self.assertEqual(result["recommendation"], chosen["recommendation_reason"])
        self.assertNotIn("[ROUTE:", result["recommendation"])

    async def test_incomplete_incident_coverage_appends_truthful_disclosure(self):
        trips = _load_trips_module(_bus_fetch, routes=[_transfer_route(30)])
        with patch.object(
            trips.direct_plan,
            "incident_scan_is_complete",
            return_value=False,
        ):
            result = await trips.plan_trip(_request_with_gtfs(), _payload(trips))
        self.assertIn(
            "Current incident coverage is incomplete, so allow extra time.",
            result["recommendation"],
        )
        self.assertEqual(
            result["recommendation"],
            result["route_candidates"][0]["recommendation_reason"],
        )


if __name__ == "__main__":
    unittest.main()
