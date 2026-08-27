"""End-to-end persistence and presentation tests for assembled routes."""

from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, patch

from app.services.agent import candidate_store, trip_state
from app.services.agent.tools._types import ToolResult
from app.services.agent.tools.route import prepare_route_options, present_route
from app.services.trips import candidates
from app.services.trips.preparation.constraints import (
    candidate_digest,
    route_constraints,
)
from app.services.trips.preparation.prepare import AggregatePreparation

from tests.conversation.conversation_discovery_waypoint_fixtures import (
    waypoint_segment_legs,
)
from tests.conversation.conversation_matrix_harness import make_leg
from tests.route_option_assembly_test_support import (
    SCORE_CONTRACT_KEYS,
    evidence_legs,
    route_context,
)


class RouteOptionAssemblyIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_aggregate_score_row_carries_full_contract_with_nonzero_evidence(
        self,
    ):
        first, second = evidence_legs()
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
        assert isinstance(aggregate, AggregatePreparation)
        row = aggregate.scored[0]
        assert set(row) == SCORE_CONTRACT_KEYS
        # Real evidence is derived, not defaulted: the shared "Q disruption"
        # alert is counted once across legs, and each event once per exposure.
        assert row["alert_count"] == 2
        assert row["alerts"] == ["Q disruption", "A outage"]
        assert row["transit_count"] == 2
        assert row["event_crowd_penalty"] == 11.0
        assert row["transfers"] == 1
        assert row["rank"] == 1
        # The row score is exactly explainable from the row's own canonical
        # components (75 canonical minutes + 1 transfer*4 + 2 alerts*8 +
        # 11.0 event penalty). The old per-leg-sum + transfer-delta row
        # (48.0) contradicted this breakdown.
        assert row["score"] == 75 + 4 + 2 * 8 + 11.0
        assert row["score"] == row["total_minutes"] + row["transfers"] * 4 + row["alert_count"] * 8 + row["event_crowd_penalty"] + row["walking_penalty"] + row["preferred_mode_penalty"]
        assert row["score"] != 48.0
        # Alert/event exposure is real evidence: the public digest reports
        # medium reliability without coupling this test to a private helper.
        digest = candidate_digest(
            route=aggregate.parsed_routes[0],
            candidate_id="candidate-test",
            score=row,
            alerts=[],
            incidents=[],
            event_impacts=aggregate.candidate_evidence[0]["event_impacts"],
            prepared_arrival_by=None,
            hard_constraints=route_constraints(
                aggregate.parsed_routes[0],
                {"origin": "user", "destination": "Barclays Center"},
            ),
            evidence_coverage=aggregate.coverage,
        )
        assert digest["score_summary"]["reliability"] == "medium"
        # The real candidate projection consumes the row without fallbacks.
        built = candidates._build_route_candidates(
            aggregate.parsed_routes,
            0,
            {},
            aggregate.scored,
        )
        assert built[0]["score_breakdown"]["active_alerts"] == 2
        assert built[0]["score_breakdown"]["transit_lines"] == ["Q", "A"]
        assert built[0]["selection_rank"] == 1

    async def test_aggregate_rank_follows_final_score_not_beam_order(self):
        first = make_leg(
            route_ids=("Q", "R", "M"),
            destination="B Pizza",
            alerts=(
                {"header": "Q disruption", "route_ids": ["Q"]},
                {"header": "R delay", "route_ids": ["R"]},
                {"header": "M slow", "route_ids": ["M"]},
            ),
            event_impacts=(
                {
                    "route_index": 0,
                    "event_id": "ev-game",
                    "title": "Game",
                    "risk_score": 10.0,
                },
            ),
        )
        second = make_leg(
            route_ids=("A",),
            destination="Barclays Center",
            alerts=({"header": "A outage", "route_ids": ["A"]},),
        )
        second.origin_place = first.destination_place
        ctx = route_context()
        # The beam expands the second segment once per first-leg partial.
        with patch(
            "app.services.agent.tools.route.prepare_route_options.prepare_single_leg",
            new=AsyncMock(side_effect=[first, second, second, second]),
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
        rows = aggregate.scored
        # Per-leg sums ordered the beam [0, 1, 2] (44, 45, 46); the canonical
        # deduplicated score reorders it, so rank must follow final score.
        assert [row["index"] for row in rows] == [0, 1, 2]
        assert [row["rank"] for row in rows] == [3, 1, 2]
        assert [row["score"] for row in rows] == [105.0, 95.0, 95.0]
        assert [row["event_crowd_penalty"] for row in rows] == [10.0, 0.0, 0.0]
        assert [row["alert_count"] for row in rows] == [2, 2, 2]
        for row in rows:
            assert row["score"] == row["total_minutes"] + row["transfers"] * 4 + row["alert_count"] * 8 + row["event_crowd_penalty"] + row["walking_penalty"] + row["preferred_mode_penalty"]
        # Candidate/evidence indices stay aligned: only candidate 0 is
        # exposed to the event, and each chain keeps its own alert evidence.
        assert [len(item["event_impacts"]) for item in aggregate.candidate_evidence] == [1, 0, 0]
        assert aggregate.candidate_evidence[0]["event_impacts"][0]["event_id"] == "ev-game"
        ranked = sorted(rows, key=lambda row: row["rank"])
        assert [row["index"] for row in ranked] == [1, 2, 0]

    async def test_multi_stop_prepare_store_present_projection_emits_one_card(
        self,
    ):
        place2 = {
            "place_id": "pl_assembly_waypoint",
            "name": "B Pizza",
            "latitude": 40.6870,
            "longitude": -73.9800,
            "address": "2 B Ave",
            "provider_place_id": "ChIJ-assembly",
        }
        ctx = route_context()
        with patch(
            "app.services.agent.tools.route.prepare_route_options.prepare_single_leg",
            new=AsyncMock(side_effect=waypoint_segment_legs(place2)),
        ):
            prepared = await prepare_route_options.execute(
                {
                    "origin": "user",
                    "destination": "Barclays",
                    "destination_source": "current_turn",
                    "waypoints": ["B Pizza"],
                },
                ctx,
            )
        assert prepared.ok, prepared.error
        candidate_id = prepared.data["candidates"][0]["candidate_id"]
        set_id = prepared.data["candidate_set_id"]
        record = candidate_store.load_candidate_set(
            set_id,
            session_id=ctx.session_id,
        )
        assert record is not None
        assert record["candidate_kind"] == "multi_stop"
        assert record["waypoints"] == ["B Pizza"]
        with (
            patch(
                "app.services.trips.enrichment._enrich_route",
                new=AsyncMock(return_value=None),
            ),
            patch(
                "app.services.agent.tools.transit.lookup_arrivals.execute",
                new=AsyncMock(
                    return_value=ToolResult(ok=False, error="fixture: no live arrivals")
                ),
            ),
        ):
            presented = await present_route.execute(
                {
                    "candidate_id": candidate_id,
                    "lead_in": "The route options were close, so I chose this one for your trip.",
                    "follow_up": "",
                    "reason_code": "meets_hard_constraints",
                },
                ctx,
            )
        assert presented.ok, presented.error
        cards = [event for event in presented.events if event.type == "route_card"]
        assert [event.role for event in cards] == ["recommended"]
        itinerary = cards[0].itinerary
        waypoints = itinerary.get("waypoints") or []
        assert len(waypoints) == 1
        assert waypoints[0]["display_name"] == "B Pizza"
        assert (waypoints[0]["dwell_minutes"], waypoints[0]["dwell_source"]) == (25, "default")
        assert itinerary["total_dwell_seconds"] == 1500
        assert itinerary["destination"]["name"] == "Barclays Center"
        state = trip_state.get_trip_state(ctx.session)
        assert state["waypoints"] == ["B Pizza"]
        assert state["selected_candidate_id"] == candidate_id
        assert state["temporary_candidate_set_id"] is None

    async def test_removal_turn_reprepares_destination_only_without_residue(
        self,
    ):
        ctx = route_context()
        with patch(
            "app.services.agent.tools.route.prepare_route_options.prepare_single_leg",
            new=AsyncMock(
                return_value=make_leg(
                    route_ids=("Q",),
                    destination="Barclays Center",
                )
            ),
        ):
            prepared = await prepare_route_options.execute(
                {
                    "origin": "user",
                    "destination": "Barclays",
                    "destination_source": "current_turn",
                    "waypoints": [],
                },
                ctx,
            )
        assert prepared.ok, prepared.error
        candidate_id = prepared.data["candidates"][0]["candidate_id"]
        set_id = prepared.data["candidate_set_id"]
        record = candidate_store.load_candidate_set(
            set_id,
            session_id=ctx.session_id,
        )
        assert record["candidate_kind"] == "single_leg"
        assert record["waypoints"] == []
        assert record["aggregate_segments"] == []
        with patch(
            "app.services.trips.enrichment._enrich_route",
            new=AsyncMock(return_value=None),
        ):
            presented = await present_route.execute(
                {
                    "candidate_id": candidate_id,
                    "lead_in": "The route options were close, so I chose this one for your trip.",
                    "follow_up": "",
                    "reason_code": "meets_hard_constraints",
                },
                ctx,
            )
        assert presented.ok, presented.error
        cards = [event for event in presented.events if event.type == "route_card"]
        assert len(cards) == 1
        itinerary = cards[0].itinerary
        assert itinerary.get("waypoints") == []
        assert itinerary.get("total_dwell_seconds") == 0
        state = trip_state.get_trip_state(ctx.session)
        assert state["waypoints"] == []
        assert state["selected_candidate_id"] == candidate_id


__all__ = ()
