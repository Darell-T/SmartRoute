"""Regression tests for accepted-trip direction and service evidence binding."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.services.agent.tools.transit import evidence as transit_evidence
from app.services.agent.tools.transit import check_transit, present_transit
from app.services.agent.tools._types import ToolContext, ToolResult
from app.services.agent.tools.route.route_projection import first_boarding_context
from app.services.agent.turn.contract import GoalKind, OutcomeGoal, TurnContract
from app.services.agent.turn.evidence import TurnEvidence
from app.services.trips.itinerary import build_canonical_itinerary
from app.services import cache
from app.services.mta.static_gtfs.stop_patterns import StopPatternIndex
from tests.agent_evidence_binding_test_support import transit_input


class AgentTransitDirectionReliabilityTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        cache._mem.clear()

    async def test_model_echoed_accepted_b_headsign_uses_canonical_direction(self):
        """An echoed accepted-trip headsign is not current-turn rider input."""

        index = StopPatternIndex.load()
        step = {
            "type": "SUBWAY",
            "route_id": "B",
            "direction": "Bedford Park Blvd",
            "departure_stop": "Church Av",
            "arrival_stop": "7 Av",
            "departure_coords": {
                "latitude": index.stops["D28"]["lat"],
                "longitude": index.stops["D28"]["lon"],
            },
            "arrival_coords": {
                "latitude": index.stops["D25"]["lat"],
                "longitude": index.stops["D25"]["lon"],
            },
        }
        boarding = first_boarding_context(
            SimpleNamespace(_pattern_index=index), step, 0
        )
        self.assertEqual(boarding["stop_order"]["origin_stop_id"], "D28")
        self.assertEqual(boarding["stop_order"]["destination_stop_id"], "D25")
        from app.services.trips.itinerary import build_canonical_itinerary

        itinerary = build_canonical_itinerary(
            [step], origin="Church Av", destination="7 Av"
        )
        context = ToolContext(
            session_id="b-follow-up-session",
            rider_message="How is the B running for the trip you just gave me?",
            session={
                "active_trip": {
                    "first_boarding": boarding,
                    "canonical_itinerary": itinerary,
                }
            },
            gtfs=SimpleNamespace(_pattern_index=index),
        )
        status_result = ToolResult(
            ok=True,
            data={
                "source": "mta_service_alerts",
                "freshness": "live",
                "status": "active_alerts",
                "alerts": [
                    {
                        "alert_id": "b-alert",
                        "header": "B service change",
                        "route_ids": ["B"],
                        "direction": "uptown",
                    }
                ],
                "gtfs_rt_coverage": "current",
                "incident_coverage": "current",
            },
        )

        tool_input = transit_input(
            operation="service_status",
            route_ids=["B"],
            station=None,
        )
        tool_input["direction"] = "Bedford Park Blvd"
        with patch.object(
            check_transit,
            "collect_service_status",
            new=AsyncMock(return_value=status_result),
        ) as collect_status:
            result = await check_transit.execute(
                tool_input,
                context,
            )

        self.assertTrue(result.ok)
        self.assertNotEqual(
            (result.data or {}).get("status"),
            "clarification_required",
            "the accepted itinerary already resolves the B direction",
        )
        collect_status.assert_awaited_once()
        fields = collect_status.await_args.args[1]
        self.assertEqual(fields["direction"], "uptown")
        evidence = (result.data or {}).get("evidence") or {}
        self.assertEqual(
            evidence.get("direction_scope", {}).get("resolved"), "uptown"
        )
        self.assertEqual(
            evidence.get("confirmed_matching_alerts", [])[0].get("header"),
            "B service change",
        )

    async def test_explicit_headsign_precedes_accepted_trip_direction(self):
        """An explicit destination/headsign remains the rider's first choice."""

        index = StopPatternIndex.load()
        step = {
            "type": "SUBWAY",
            "route_id": "B",
            "direction": "Bedford Park Blvd",
            "departure_stop": "Church Av",
            "arrival_stop": "7 Av",
            "departure_coords": {
                "latitude": index.stops["D28"]["lat"],
                "longitude": index.stops["D28"]["lon"],
            },
            "arrival_coords": {
                "latitude": index.stops["D25"]["lat"],
                "longitude": index.stops["D25"]["lon"],
            },
        }
        gtfs = SimpleNamespace(_pattern_index=index)
        context = ToolContext(
            session_id="b-headsign-follow-up-session",
            session={
                "active_trip": {
                    "first_boarding": first_boarding_context(gtfs, step, 0),
                    "canonical_itinerary": build_canonical_itinerary(
                        [step], origin="Church Av", destination="7 Av"
                    ),
                }
            },
            gtfs=gtfs,
        )
        status_result = ToolResult(
            ok=True,
            data={
                "source": "mta_service_alerts",
                "freshness": "live",
                "status": "active_alerts",
                "alerts": [
                    {
                        "alert_id": "b-headsign-alert",
                        "header": "B service change",
                        "route_ids": ["B"],
                    }
                ],
                "gtfs_rt_coverage": "current",
                "incident_coverage": "current",
            },
        )
        tool_input = transit_input(
            operation="service_status",
            route_ids=["B"],
            station=None,
        )
        tool_input.update(
            {
                "stop_source": "accepted_trip",
                "station_source": "accepted_trip",
                "direction": "Bedford Park Blvd",
            }
        )
        context.rider_message = "Check the B toward Bedford Park Blvd."

        with patch.object(
            check_transit,
            "collect_service_status",
            new=AsyncMock(return_value=status_result),
        ) as collect_status:
            result = await check_transit.execute(tool_input, context)

        self.assertTrue(result.ok)
        self.assertEqual(
            collect_status.await_args.args[1]["direction"], "bedford park blvd"
        )
        evidence = (result.data or {}).get("evidence") or {}
        self.assertEqual(
            evidence.get("confirmed_matching_alerts", [])[0].get("header"),
            "B service change",
        )

    async def test_accepted_seven_headsign_does_not_become_uptown_or_downtown(self):
        index = StopPatternIndex.load()
        step = {
            "type": "SUBWAY",
            "route_id": "7",
            "direction": "Flushing-Main St",
            "departure_stop": "Times Sq-42 St",
            "arrival_stop": "Flushing-Main St",
            "departure_coords": {
                "latitude": index.stops["725"]["lat"],
                "longitude": index.stops["725"]["lon"],
            },
            "arrival_coords": {
                "latitude": index.stops["701"]["lat"],
                "longitude": index.stops["701"]["lon"],
            },
        }
        boarding = first_boarding_context(
            SimpleNamespace(_pattern_index=index), step, 0
        )
        self.assertNotIn("semantic_direction", boarding)
        context = ToolContext(
            session_id="seven-headsign-session",
            session={"active_trip": {"first_boarding": boarding}},
            gtfs=SimpleNamespace(_pattern_index=index),
        )
        status_result = ToolResult(
            ok=True,
            data={
                "source": "mta_service_alerts",
                "freshness": "live",
                "status": "no_active_alerts",
                "alerts": [],
                "gtfs_rt_coverage": "current",
                "incident_coverage": "current",
            },
        )

        with patch.object(
            check_transit,
            "collect_service_status",
            new=AsyncMock(return_value=status_result),
        ) as collect_status:
            result = await check_transit.execute(
                transit_input(
                    operation="service_status",
                    route_ids=["7"],
                    station=None,
                ),
                context,
            )

        self.assertTrue(result.ok)
        collect_status.assert_awaited_once()
        self.assertEqual(
            collect_status.await_args.args[1]["direction"], "flushing-main st"
        )

    def test_static_northbound_pattern_derivation_is_not_b_specific(self):
        index = StopPatternIndex.load()
        step = {
            "type": "SUBWAY",
            "route_id": "Q",
            "direction": "96 St",
            "departure_stop": "Church Av",
            "arrival_stop": "96 St",
            "departure_coords": {
                "latitude": index.stops["D28"]["lat"],
                "longitude": index.stops["D28"]["lon"],
            },
            "arrival_coords": {
                "latitude": index.stops["Q05"]["lat"],
                "longitude": index.stops["Q05"]["lon"],
            },
        }
        boarding = first_boarding_context(
            SimpleNamespace(_pattern_index=index), step, 0
        )
        context = SimpleNamespace(
            session={"active_trip": {"first_boarding": boarding}},
            gtfs=SimpleNamespace(_pattern_index=index),
        )
        from app.services.agent.tools.transit.direction import accepted_trip_direction

        self.assertEqual(accepted_trip_direction(context, ["Q"]), "uptown")

    def test_accepted_l_headsign_remains_route_scoped_label(self):
        index = StopPatternIndex.load()
        step = {
            "type": "SUBWAY",
            "route_id": "L",
            "direction": "Canarsie-Rockaway Pkwy",
            "departure_stop": "1 Av",
            "arrival_stop": "Canarsie-Rockaway Pkwy",
            "departure_coords": {
                "latitude": index.stops["L06"]["lat"],
                "longitude": index.stops["L06"]["lon"],
            },
            "arrival_coords": {
                "latitude": index.stops["L29"]["lat"],
                "longitude": index.stops["L29"]["lon"],
            },
        }
        boarding = first_boarding_context(
            SimpleNamespace(_pattern_index=index), step, 0
        )
        self.assertNotIn("semantic_direction", boarding)
        context = SimpleNamespace(
            session={"active_trip": {"first_boarding": boarding}},
            gtfs=SimpleNamespace(_pattern_index=index),
        )
        from app.services.agent.tools.transit.direction import accepted_trip_direction

        direction = accepted_trip_direction(context, ["L"])
        self.assertEqual(direction, "canarsie-rockaway pkwy")
        self.assertNotIn(direction, {"uptown", "downtown"})

    async def test_raw_numeric_subway_direction_id_does_not_imply_semantics(self):
        itinerary = {
            "legs": [
                {
                    "mode": "SUBWAY",
                    "service_id": "Q",
                    "board": "Newkirk Plaza",
                    "alight": "Prospect Park",
                    "direction_id": 0,
                }
            ]
        }
        status_result = ToolResult(
            ok=True,
            data={
                "source": "mta_service_alerts",
                "freshness": "live",
                "status": "no_active_alerts",
                "alerts": [],
            },
        )
        with patch.object(
            check_transit,
            "collect_service_status",
            new=AsyncMock(return_value=status_result),
        ) as collect_status:
            result = await check_transit.execute(
                transit_input(
                    operation="service_status",
                    route_ids=["Q"],
                    station=None,
                ),
                ToolContext(
                    session_id="numeric-direction-session",
                    session={"active_trip": {"canonical_itinerary": itinerary}},
                ),
            )

        self.assertTrue(result.ok)
        self.assertNotEqual(
            (result.data or {}).get("status"), "clarification_required"
        )
        self.assertIsNone(collect_status.await_args.args[1]["direction"])

    async def test_partial_incident_coverage_preserves_verified_route_facts(self):
        """Known alert/vehicle facts stay usable when incident coverage is partial."""

        evidence_set_id, evidence = transit_evidence.build_evidence_set(
            session_id="partial-grounding-session",
            operation="service_status",
            route_ids=["B"],
            direction="uptown",
            result={
                "source": "mta_service_alerts",
                "freshness": "live",
                "status": "active_alerts",
                "alerts": [
                    {
                        "alert_id": "b-alert",
                        "header": "B service change",
                        "route_ids": ["B"],
                    }
                ],
                "gtfs_rt_coverage": "current",
                "gtfs_rt_observed_at": "2026-08-20T12:00:00Z",
                "incident_coverage": "partial",
                "incidents": [],
                "unconfirmed_signals": [
                    {
                        "route_id": "B",
                        "mode": "subway",
                        "kind": "possible_stalled_train",
                        "reason": "vehicle evidence available",
                    }
                ],
            },
        )
        self.assertEqual(evidence["source_coverage"]["alerts"], "current")
        self.assertEqual(evidence["source_coverage"]["gtfs_rt"], "current")
        self.assertEqual(evidence["source_coverage"]["incidents"], "partial")

        turn_evidence = TurnEvidence()
        turn_evidence.bind_contract(
            TurnContract((OutcomeGoal("status", GoalKind.SERVICE_STATUS),))
        )
        turn_evidence.record_goal_handle("status", evidence_set_id)
        result = await present_transit.execute(
            {
                "evidence_set_id": evidence_set_id,
                "goal_key": "status",
                "lead_in": "",
                "follow_up": "",
            },
            ToolContext(
                session_id="partial-grounding-session",
                turn_evidence=turn_evidence,
                telemetry={},
            ),
        )

        self.assertTrue(result.ok)
        passenger_text = str((result.data or {}).get("passenger_text") or "")
        self.assertIn("B service change", passenger_text)
        self.assertIn("possible stalled train", passenger_text.casefold())
        self.assertIn("check part", passenger_text.casefold())
        self.assertNotIn("can't confirm", passenger_text.casefold())

    def test_route_bound_findings_survive_current_and_partial_coverage(self):
        for incident_coverage in ("current", "partial"):
            with self.subTest(incident_coverage=incident_coverage):
                _evidence_set_id, evidence = transit_evidence.build_evidence_set(
                    session_id=f"route-scope-{incident_coverage}",
                    operation="service_status",
                    route_ids=["B"],
                    direction="uptown",
                    result={
                        "source": "mta_service_alerts",
                        "freshness": "live",
                        "status": "active_alerts",
                        "alerts": [
                            {
                                "alert_id": "b-alert",
                                "header": "B service change",
                                "route_ids": ["B"],
                            }
                        ],
                        "gtfs_rt_coverage": "current",
                        "incident_coverage": incident_coverage,
                        "unconfirmed_signals": [
                            {
                                "route_id": "B",
                                "mode": "subway",
                                "kind": "possible_stalled_train",
                            }
                        ],
                    },
                )

                self.assertEqual(
                    evidence["confirmed_matching_alerts"][0]["header"],
                    "B service change",
                )
                self.assertEqual(
                    evidence["unconfirmed_signals"][0]["route_id"], "B"
                )



if __name__ == "__main__":
    unittest.main()
