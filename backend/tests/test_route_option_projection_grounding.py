"""Grounding and evidence projection tests for route options."""

from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, patch

from app.services.agent.tools.route import prepare_route_options
from app.services.trips.preparation.constraints import candidate_digest
from app.services.trips.preparation import evidence as route_option_evidence
from app.services.agent.tools.route import prepare_route_persistence
from app.services.agent.tools.route.prepare_route_options import as_aggregate
from tests.conversation.conversation_matrix_harness import make_leg
from tests.route_option_assembly_test_support import evidence_legs, route_context


class RouteOptionProjectionGroundingTests(unittest.IsolatedAsyncioTestCase):
    def test_public_candidate_digest_is_unordered_and_score_free(self):
        leg = make_leg(route_ids=("Q",))
        base_score = dict(leg.scored[0])
        digests = []
        for candidate_id, rank, score in (("cd_first", 2, 100), ("cd_second", 1, 10)):
            digests.append(
                candidate_digest(
                    route=leg.parsed_routes[0],
                    candidate_id=candidate_id,
                    score={**base_score, "index": 0, "rank": rank, "score": score},
                    alerts=[],
                    incidents=[],
                    event_impacts=[],
                    prepared_arrival_by=None,
                    hard_constraints={
                        "satisfied": True,
                        "accessibility_status": "unknown",
                    },
                    itinerary={
                        "total_duration_seconds": 20 * 60,
                        "total_wait_seconds": 2 * 60,
                        "transfer_count": 0,
                    },
                )
            )

        public = [
            prepare_route_persistence.public_candidate_digest(item) for item in digests
        ]

        self.assertEqual(
            [item["candidate_id"] for item in public], ["cd_first", "cd_second"]
        )
        for item in public:
            self.assertNotIn("score", item)
            self.assertNotIn("rank", item)
            self.assertNotIn("score_summary", item)
            self.assertNotIn("winner", item)
            self.assertNotIn("supported_reason_codes", item)
            self.assertEqual(item["comparison"]["mode"], "unordered_factor_comparison")
            self.assertNotIn("duration_minutes", item)
            self.assertNotIn("transit_lines", item)
            self.assertNotIn("official_service_impacts", item)
            self.assertIn("duration_minutes", item["comparison"]["timing"])
            self.assertIn("transit_lines", item["comparison"]["service_chain"])
            self.assertIn("official_alerts", item["comparison"]["service_conditions"])

    def test_public_event_context_is_not_projected_as_observed_crowding(self):
        public = prepare_route_persistence.public_candidate_digest(
            {
                "candidate_id": "cd_context_only",
                "event_or_crowd_impacts": [
                    {
                        "event_name": "Nearby show",
                        "venue_name": "Venue",
                        "exposure_window": "overlap",
                        "crowd_level": "moderate",
                        "confidence": 0.4,
                        "risk_score": 0,
                        "scoring_authorized": False,
                    }
                ],
            }
        )

        impact = public["comparison"]["service_conditions"]["event_or_crowd"][0]
        self.assertTrue(impact["potential_event_risk"])
        self.assertNotIn("crowd_level", impact)
        self.assertNotIn("potential_event_risk_level", impact)

    def test_candidate_digest_keeps_vehicle_signal_unconfirmed_and_sanitized(self):
        leg = make_leg(route_ids=("B35",))
        leg.parsed_routes[0][1]["route_total_seconds"] = 46 * 60
        digest = candidate_digest(
            route=leg.parsed_routes[0],
            candidate_id="candidate-0",
            score=leg.scored[0],
            alerts=[],
            incidents=[],
            event_impacts=[],
            prepared_arrival_by=None,
            hard_constraints={"satisfied": True, "accessibility_status": "unknown"},
            unconfirmed_material_claims=[
                {
                    "mode": "bus",
                    "route_id": "B35",
                    "stop_id": "opaque-provider-stop",
                    "location": {"latitude": 40.7, "longitude": -73.9},
                },
                {"mode": "bus", "route_id": "B35", "status": "layover"},
            ],
            evidence_coverage={"vehicles": "current", "private": "ignored"},
        )

        self.assertEqual(
            digest["unconfirmed_material_claims"],
            [
                {
                    "mode": "bus",
                    "route": "B35",
                    "location": "the route",
                    "status": "possible_delay_unconfirmed",
                }
            ],
        )
        self.assertEqual(digest["duration_minutes"], 46)
        self.assertEqual(digest["score_summary"]["estimated_duration"], 46)
        self.assertEqual(digest["evidence_coverage"], {"vehicles": "current"})
        serialized = str(digest)
        self.assertNotIn("opaque-provider-stop", serialized)
        self.assertNotIn("stuck in traffic", serialized.casefold())

    def test_vehicle_signal_does_not_cross_authoritative_direction(self):
        leg = make_leg(route_ids=("Q",))
        route = leg.parsed_routes[0]
        route[1]["direction"] = "Uptown"
        route.append({"type": "SUBWAY", "route_id": "A", "direction": "Downtown"})

        claims = route_option_evidence.vehicle_claims_for_route(
            route,
            trains=[
                {"route_id": "Q", "direction": "downtown"},
                {"route_id": "Q", "direction": "uptown"},
            ],
            buses=[],
        )

        self.assertEqual(len(claims), 1)
        self.assertEqual(claims[0]["route"], "Q")

    def test_single_leg_adapter_preserves_vehicle_and_coverage_evidence(self):
        leg = make_leg(route_ids=("B35",))
        leg.stalled_buses = [
            {
                "route_id": "B35",
                "location": {"latitude": 40.7, "longitude": -73.9},
            }
        ]

        aggregate = as_aggregate(leg)

        evidence = aggregate.candidate_evidence[0]
        self.assertEqual(evidence["unconfirmed_material_claims"][0]["route"], "B35")
        self.assertEqual(
            evidence["unconfirmed_material_claims"][0]["status"],
            "possible_delay_unconfirmed",
        )
        self.assertIn("vehicles", evidence["evidence_coverage"])

    async def test_aggregate_scopes_vehicle_signals_to_route_evidence(self):
        first, second = evidence_legs()
        first.stalled = [{"route_id": "Q", "stop_id": "Q01N", "stalled_minutes": 6}]
        first.stalled_buses = [
            {"route_id": "Q", "location": {"latitude": 40.7, "longitude": -73.9}}
        ]
        second.stalled = [{"route_id": "R", "stop_id": "R01S", "stalled_minutes": 9}]
        ctx = route_context()
        with patch(
            "app.services.agent.tools.route.prepare_route_options.prepare_single_leg",
            new=AsyncMock(side_effect=[first, second]),
        ):
            aggregate = await prepare_route_options._prepare_multi_stop(
                {
                    "origin": "user",
                    "destination": "Barclays Center",
                    "waypoints": ["B Pizza"],
                },
                ctx,
                {},
                ["B Pizza"],
            )

        claims = aggregate.candidate_evidence[0]["unconfirmed_material_claims"]
        self.assertEqual({claim["mode"] for claim in claims}, {"train", "bus"})
        self.assertEqual({claim["route"] for claim in claims}, {"Q"})
        self.assertTrue(
            all(claim["status"] == "possible_delay_unconfirmed" for claim in claims)
        )
        self.assertNotIn("Q01N", str(claims))
        self.assertEqual(
            aggregate.candidate_evidence[0]["evidence_coverage"]["incidents"], "current"
        )


__all__ = ()
