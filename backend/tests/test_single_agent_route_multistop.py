"""Tests for the unflagged prepare_route_options / present_route path."""

from __future__ import annotations
import copy
import unittest
from unittest.mock import AsyncMock, patch
from app.services.agent.tools.route import (
    prepare_route_options,
)
from app.services.agent.tools.location_resolution import ResolvedPlace
from app.services.trips.preparation.prepare import AggregatePreparation
from app.services.trips.route_incidents.scan import incident_scan_is_complete

from tests.single_agent_route_test_support import (
    _ctx,
    _prepared_leg,
)


class SingleAgentRouteMultiStopTests(unittest.IsolatedAsyncioTestCase):
    async def test_waypoints_use_one_bounded_aggregate_without_nested_selection(self):
        first = _prepared_leg()
        second = _prepared_leg()
        first.event_impacts = [{"route_index": 0, "title": "Game crowd"}]
        second.origin_place = first.destination_place
        second.destination_place = ResolvedPlace("Final", 40.67, -73.97, "fallback")
        ctx = _ctx()
        with patch(
            "app.services.agent.tools.route.prepare_route_options.prepare_single_leg",
            new=AsyncMock(side_effect=[first, second]),
        ) as prepare_mock:
            aggregate = await prepare_route_options._prepare_multi_stop(
                {
                    "origin": "user",
                    "destination": "Final",
                    "waypoints": ["Barclays Center"],
                },
                ctx,
                {},
                ["Barclays Center"],
            )
        self.assertIsInstance(aggregate, AggregatePreparation)
        self.assertEqual(prepare_mock.await_count, 2)
        self.assertEqual(len(aggregate.aggregate_segments), 1)
        self.assertEqual(len(aggregate.aggregate_segments[0]), 2)
        self.assertEqual(len(aggregate.parsed_routes), 1)
        self.assertEqual(aggregate.event_impacts[0]["route_index"], 0)
        self.assertEqual(aggregate.event_impacts[0]["segment_index"], 0)
        self.assertEqual(aggregate.scored[0]["transfers"], 1)

    async def test_multi_stop_downstream_departure_follows_each_upstream_candidate(
        self,
    ):
        first = _prepared_leg()
        first.parsed_routes = [
            [
                {
                    "type": "SUBWAY",
                    "route_id": "Q",
                    "departure_time_iso": "2026-08-06T12:00:00-04:00",
                    "arrival_time_iso": "2026-08-06T12:10:00-04:00",
                }
            ],
            [
                {
                    "type": "SUBWAY",
                    "route_id": "A",
                    "departure_time_iso": "2026-08-06T12:00:00-04:00",
                    "arrival_time_iso": "2026-08-06T12:20:00-04:00",
                }
            ],
        ]
        first.scored = [
            {"index": 0, "score": 10, "total_minutes": 10, "transfers": 0},
            {"index": 1, "score": 20, "total_minutes": 20, "transfers": 0},
        ]
        downstream_inputs: list[dict] = []

        async def prepare(input_data, _ctx, _timings, **_kwargs):
            downstream_inputs.append(dict(input_data))
            prepared = copy.deepcopy(_prepared_leg())
            departure = input_data.get("departure_time")
            prepared.parsed_routes = [
                [
                    {
                        "type": "BUS",
                        "route_id": "B35",
                        "departure_time_iso": departure,
                        "arrival_time_iso": "2026-08-06T13:00:00-04:00",
                    }
                ]
            ]
            prepared.scored = [
                {"index": 0, "score": 5, "total_minutes": 25, "transfers": 0}
            ]
            prepared.origin_place = first.destination_place
            prepared.destination_place = ResolvedPlace(
                "Final", 40.67, -73.97, "fallback"
            )
            return prepared

        ctx = _ctx()

        async def provider(input_data, _ctx, _timings, **_kwargs):
            return await prepare(input_data, _ctx, _timings, **_kwargs)

        prepare_calls = 0

        async def side_effect(input_data, _ctx, _timings, **_kwargs):
            nonlocal prepare_calls
            prepare_calls += 1
            return (
                first
                if prepare_calls == 1
                else await provider(input_data, _ctx, _timings, **_kwargs)
            )

        with patch(
            "app.services.agent.tools.route.prepare_route_options.prepare_single_leg",
            new=AsyncMock(side_effect=side_effect),
        ) as prepare_mock:
            aggregate = await prepare_route_options._prepare_multi_stop(
                {
                    "origin": "user",
                    "destination": "Final",
                    "waypoints": ["Barclays Center"],
                },
                ctx,
                {},
                ["Barclays Center"],
            )
        self.assertEqual(prepare_mock.await_count, 3)
        self.assertEqual(
            [item["departure_time"] for item in downstream_inputs],
            [
                "2026-08-06T12:35:00-04:00",
                "2026-08-06T12:45:00-04:00",
            ],
        )
        self.assertEqual(
            [
                chain[1]["steps"][0]["departure_time_iso"]
                for chain in [
                    aggregate.aggregate_segments[0],
                    aggregate.aggregate_segments[1],
                ]
            ],
            [
                "2026-08-06T12:35:00-04:00",
                "2026-08-06T12:45:00-04:00",
            ],
        )
        self.assertEqual([row["transfers"] for row in aggregate.scored], [1, 1])

    async def test_candidate_digest_scopes_incidents_and_alerts_to_selected_route(self):
        prepared = _prepared_leg()
        prepared.parsed_routes = [
            [{"type": "SUBWAY", "route_id": "Q"}],
            [{"type": "SUBWAY", "route_id": "A"}],
        ]
        prepared.scored = [
            {"index": 0, "score": 1, "total_minutes": 10, "transfers": 0},
            {"index": 1, "score": 2, "total_minutes": 11, "transfers": 0},
        ]
        prepared.relevant_alerts = [
            {"header": "Q only", "route_ids": ["Q"]},
            {"header": "A only", "route_ids": ["A"]},
            {
                "header": "candidate one only",
                "route_ids": ["Q"],
                "affected_candidate_route_ids": ["candidate-1"],
            },
        ]
        prepared.incidents = [
            {
                "description": "Q incident",
                "affected_candidate_route_ids": ["candidate-0"],
            },
            {
                "description": "A incident",
                "affected_candidate_route_ids": ["candidate-1"],
            },
        ]
        ctx = _ctx()
        with patch(
            "app.services.agent.tools.route.prepare_route_options.prepare_single_leg",
            new=AsyncMock(return_value=prepared),
        ):
            result = await prepare_route_options.execute(
                {
                    "destination": "Barclays Center",
                    "destination_source": "current_turn",
                },
                ctx,
            )
        digests = result.data["candidates"]
        first_conditions = digests[0]["comparison"]["service_conditions"]
        second_conditions = digests[1]["comparison"]["service_conditions"]
        first_alerts = first_conditions["official_alerts"]
        second_alerts = second_conditions["official_alerts"]
        first_headers = [alert["header"] for alert in first_alerts]
        second_headers = [alert["header"] for alert in second_alerts]
        self.assertIn("Q only", first_headers)
        self.assertNotIn("A only", first_headers)
        self.assertNotIn("candidate one only", first_headers)
        self.assertIn("A only", second_headers)
        self.assertTrue(all(isinstance(alert, dict) for alert in first_alerts))
        self.assertTrue(
            all(alert["material_disruption"] is True for alert in first_alerts)
        )
        self.assertTrue(all(isinstance(alert, dict) for alert in second_alerts))
        self.assertTrue(
            all(alert["material_disruption"] is True for alert in second_alerts)
        )
        self.assertIn("A incident", second_conditions["confirmed_incidents"])
        self.assertNotIn("Q incident", second_conditions["confirmed_incidents"])

    async def test_second_leg_evidence_is_remapped_into_aggregate_candidate(self):
        first = _prepared_leg()
        second = copy.deepcopy(_prepared_leg())
        second.origin_place = first.destination_place
        second.destination_place = ResolvedPlace("Final", 40.67, -73.97, "fallback")
        second.event_impacts = [
            {"route_index": 0, "title": "Second stop crowd", "risk_score": 4}
        ]
        second.incidents = [
            {
                "description": "Second stop incident",
                "affected_candidate_route_ids": ["candidate-0"],
            }
        ]
        ctx = _ctx()
        with patch(
            "app.services.agent.tools.route.prepare_route_options.prepare_single_leg",
            new=AsyncMock(side_effect=[first, second]),
        ):
            aggregate = await prepare_route_options._prepare_multi_stop(
                {
                    "origin": "user",
                    "destination": "Final",
                    "waypoints": ["Barclays Center"],
                },
                ctx,
                {},
                ["Barclays Center"],
            )
        evidence = aggregate.candidate_evidence[0]
        self.assertEqual(evidence["event_impacts"][0]["segment_index"], 1)
        self.assertEqual(evidence["event_impacts"][0]["route_index"], 0)
        self.assertEqual(
            evidence["incidents"][0]["affected_candidate_route_ids"],
            ["candidate-0"],
        )

    async def test_multi_stop_incident_metadata_preserves_complete_and_partial_contracts(
        self,
    ):
        first = _prepared_leg()
        second = copy.deepcopy(_prepared_leg())
        second.origin_place = first.destination_place
        second.incident_scan_metadata = {
            "status": "complete",
            "sources": {"completed": ["gtfs"]},
        }
        ctx = _ctx()
        with patch(
            "app.services.agent.tools.route.prepare_route_options.prepare_single_leg",
            new=AsyncMock(side_effect=[first, second]),
        ):
            complete = await prepare_route_options._prepare_multi_stop(
                {
                    "origin": "user",
                    "destination": "Final",
                    "waypoints": ["Barclays Center"],
                },
                ctx,
                {},
                ["Barclays Center"],
            )
        self.assertEqual(complete.incident_scan_metadata["status"], "complete")
        self.assertTrue(incident_scan_is_complete(complete.incident_scan_metadata))

        second.incident_scan_metadata = {
            "status": "partial",
            "sources": {"completed": []},
        }
        with patch(
            "app.services.agent.tools.route.prepare_route_options.prepare_single_leg",
            new=AsyncMock(side_effect=[first, second]),
        ):
            mixed = await prepare_route_options._prepare_multi_stop(
                {
                    "origin": "user",
                    "destination": "Final",
                    "waypoints": ["Barclays Center"],
                },
                ctx,
                {},
                ["Barclays Center"],
            )
        self.assertEqual(mixed.incident_scan_metadata["status"], "partial")
        self.assertFalse(incident_scan_is_complete(mixed.incident_scan_metadata))
