"""plan_trip emits canonical itinerary on each RouteCardEvent.

summary.eta_minutes / summary.transfers must come only from the itinerary
(not scoring total_minutes / transfer recount for card display).
"""

from __future__ import annotations

import asyncio
import json
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

from app.services.agent.tools import plan_trip
from app.utils.stop_patterns import StopPatternIndex
from tests._fake_http_tools import make_tool_ctx


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _leg(route_id: str, board_in_minutes: int, ride_minutes: int, *, duration_minutes: int | None = None) -> dict:
    now = datetime.now(timezone.utc)
    depart = now + timedelta(minutes=board_in_minutes)
    arrive = depart + timedelta(minutes=ride_minutes)
    total = duration_minutes if duration_minutes is not None else board_in_minutes + ride_minutes
    return {
        "duration": f"{total * 60}s",
        "steps": [
            {
                "travelMode": "TRANSIT",
                "polyline": {"encodedPolyline": "poly"},
                "transitDetails": {
                    "stopDetails": {
                        "departureStop": {
                            "name": f"{route_id} Start",
                            "location": {"latLng": {"latitude": 40.75, "longitude": -73.98}},
                        },
                        "arrivalStop": {
                            "name": f"{route_id} End",
                            "location": {"latLng": {"latitude": 40.76, "longitude": -73.99}},
                        },
                        "departureTime": _iso(depart),
                        "arrivalTime": _iso(arrive),
                    },
                    "headsign": "Uptown",
                    "transitLine": {
                        "nameShort": route_id,
                        "color": "#000000",
                        "vehicle": {"type": "SUBWAY"},
                    },
                    "stopCount": 10,
                },
            }
        ],
    }


def _google_response(*legs: dict) -> dict:
    return {"routes": [{"legs": [leg]} for leg in legs]}


class PlanTripItineraryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        env_patch = patch.dict("os.environ", {"SMARTROUTE_ENV": "test", "JARVIS_MOCK_ADVISOR": "1"})
        env_patch.start()
        self.addCleanup(env_patch.stop)

        patch.object(
            plan_trip.geo, "geocode_address_with_reason", return_value=((40.7128, -74.0060), None)
        ).start()
        patch.object(plan_trip.enrichment, "_enrich_route", new=AsyncMock(return_value={})).start()
        patch.object(plan_trip, "fetch_service_alerts", new=AsyncMock(return_value=b"")).start()
        patch.object(plan_trip, "get_stalled_trains", new=AsyncMock(return_value=[])).start()
        patch.object(plan_trip, "get_stalled_buses", new=AsyncMock(return_value=[])).start()
        self._get_route = patch.object(
            plan_trip.directions_service,
            "get_transit_route",
            new=AsyncMock(
                return_value=_google_response(
                    _leg("Q", 5, 20, duration_minutes=25),
                    _leg("B", 3, 28, duration_minutes=31),
                )
            ),
        ).start()
        patch.object(
            plan_trip.trip_incidents,
            "scan_route_incidents",
            new=AsyncMock(
                return_value={
                    "incidents": [],
                    "scan_metadata": {
                        "status": "complete",
                        "snapshot_status": "fresh",
                        "scanned_at": "2026-08-01T12:00:00Z",
                        "cache_hit": False,
                    },
                }
            ),
        ).start()
        self.addCleanup(patch.stopall)

    def _ctx(self, *, gtfs=None):
        return make_tool_ctx(origin={"lat": 40.7, "lng": -73.9}, gtfs=gtfs)

    async def test_route_cards_carry_canonical_itinerary(self):
        result = await plan_trip.execute(
            {"origin": "user", "destination": "Costco"}, self._ctx()
        )
        self.assertTrue(result.ok)
        self.assertEqual(len(result.events), 2)
        self.assertTrue(
            {
                "place_resolution_ms",
                "route_provider_ms",
                "mta_ms",
                "ticketmaster_ms",
                "scoring_ms",
            }.issubset(result.timings)
        )

        for event in result.events:
            self.assertIsNotNone(event.itinerary)
            itinerary = event.itinerary
            self.assertIsInstance(itinerary["total_duration_seconds"], int)
            self.assertGreater(itinerary["total_duration_seconds"], 0)
            self.assertIn("transfer_count", itinerary)
            self.assertIn("legs", itinerary)
            self.assertEqual(itinerary["itinerary_id"], event.card_id)

            # Wire payload includes itinerary dict.
            payload = event.to_data()
            self.assertIn("itinerary", payload)
            self.assertEqual(
                payload["itinerary"]["total_duration_seconds"],
                itinerary["total_duration_seconds"],
            )

            # Summary times come only from itinerary.
            expected_eta = max(1, round(itinerary["total_duration_seconds"] / 60))
            self.assertEqual(event.summary["eta_minutes"], expected_eta)
            self.assertEqual(event.summary["transfers"], itinerary["transfer_count"])

        # Known Google leg durations: 25 min and 31 min → 1500s / 1860s.
        durations = sorted(e.itinerary["total_duration_seconds"] for e in result.events)
        self.assertEqual(durations, [25 * 60, 31 * 60])

        # Digest mirrors itinerary-derived minutes / transfers / walk.
        for candidate, event in zip(result.data["candidates"], result.events):
            self.assertEqual(candidate["eta_minutes"], event.summary["eta_minutes"])
            self.assertEqual(candidate["transfers"], event.summary["transfers"])
            self.assertEqual(
                candidate["walk_minutes"],
                round(event.itinerary["total_walk_seconds"] / 60),
            )

    async def test_explicit_route_constraint_filters_candidates_before_selection(self):
        result = await plan_trip.execute(
            {
                "origin": "user",
                "destination": "Costco",
                "required_route_ids": ["B"],
            },
            self._ctx(),
        )

        self.assertTrue(result.ok)
        self.assertEqual(len(result.events), 1)
        self.assertEqual(result.events[0].summary["lines"], ["B"])
        self.assertEqual(result.data["candidates"][0]["lines"], ["B"])

    async def test_missing_requested_route_fails_instead_of_substituting_another_line(self):
        result = await plan_trip.execute(
            {
                "origin": "user",
                "destination": "Costco",
                "required_route_ids": ["N"],
            },
            self._ctx(),
        )

        self.assertFalse(result.ok)
        self.assertEqual(
            result.error,
            "no route candidate used the requested N service",
        )
        self.assertEqual(result.events, [])

    async def test_recommended_card_persists_canonical_first_boarding_context(self):
        pattern_index = StopPatternIndex(
            {
                "stops": {
                    "Q1": {"name": "Q Start", "lat": 40.75, "lon": -73.98},
                    "Q2": {"name": "Q End", "lat": 40.76, "lon": -73.99},
                },
                "patterns": [
                    {
                        "route_id": "Q",
                        "route_short_name": "Q",
                        "direction_id": 1,
                        "trip_count": 10,
                        "signature": "q-test",
                        "stop_ids": ["Q1", "Q2"],
                    }
                ],
            }
        )
        gtfs = type("Gtfs", (), {"_pattern_index": pattern_index})()

        result = await plan_trip.execute(
            {"origin": "user", "destination": "Costco"},
            self._ctx(gtfs=gtfs),
        )

        active = next(
            card for card in result.session_route_cards if card["role"] == "recommended"
        )
        self.assertEqual(
            active["first_boarding"],
            {
                "route_id": "Q",
                "mode": "subway",
                "stop_id": "Q1",
                "stop_name": "Q Start",
                "coordinates": {"latitude": 40.75, "longitude": -73.98},
                "direction_id": 1,
                "direction_label": "Uptown",
                "destination_stop_id": "Q2",
                "walking_minutes": 0,
            },
        )

    async def test_crowd_evidence_providers_run_concurrently(self):
        active_providers: set[str] = set()
        overlap_observed = False
        overlap_gate = asyncio.Event()

        async def wait_for_other_provider(name: str):
            nonlocal overlap_observed
            active_providers.add(name)
            if len(active_providers) == 2:
                overlap_observed = True
                overlap_gate.set()
            try:
                await asyncio.wait_for(overlap_gate.wait(), timeout=0.05)
            except asyncio.TimeoutError:
                pass
            finally:
                active_providers.remove(name)

        async def collect_events(*_args, **_kwargs):
            await wait_for_other_provider("ticketmaster")
            return "available", [], [], {"grok_status": "complete"}

        async def scan_incidents(_context, **_kwargs):
            await wait_for_other_provider("web")
            return {
                "incidents": [],
                "scan_metadata": {
                    "status": "complete",
                    "snapshot_status": "fresh",
                    "scanned_at": "2026-08-01T12:00:00Z",
                    "cache_hit": False,
                },
            }

        with (
            patch.object(
                plan_trip.crowd_evidence,
                "collect",
                new=collect_events,
            ),
            patch.object(
                plan_trip.trip_incidents,
                "scan_route_incidents",
                new=scan_incidents,
            ),
        ):
            result = await plan_trip.execute(
                {
                    "origin": "user",
                    "destination": "Columbus Circle",
                    "avoid_crowds": True,
                },
                self._ctx(),
            )

        self.assertTrue(result.ok)
        self.assertTrue(overlap_observed)

    async def test_summary_not_from_scoring_total_when_itinerary_differs(self):
        """If scoring would disagree, card still uses itinerary seconds."""
        result = await plan_trip.execute(
            {"origin": "user", "destination": "Costco"}, self._ctx()
        )
        self.assertTrue(result.ok)
        for event in result.events:
            from_itinerary = max(1, round(event.itinerary["total_duration_seconds"] / 60))
            self.assertEqual(event.summary["eta_minutes"], from_itinerary)
            # Must not invent ETA from max minutes_until_arrival path alone.
            self.assertEqual(
                event.summary["eta_minutes"],
                max(1, round(event.itinerary["total_duration_seconds"] / 60)),
            )

    async def test_depart_at_planning_mode_when_departure_time_set(self):
        result = await plan_trip.execute(
            {
                "origin": "user",
                "destination": "MSG",
                "departure_time": "2026-07-16T22:00:00-04:00",
            },
            self._ctx(),
        )
        self.assertTrue(result.ok)
        for event in result.events:
            self.assertEqual(event.itinerary["planning_mode"], "depart_at")
            self.assertEqual(
                event.itinerary["requested_departure"], "2026-07-16T22:00:00-04:00"
            )
            self.assertEqual(event.depart_iso, "2026-07-16T22:00:00-04:00")

    async def test_leave_now_planning_mode_by_default(self):
        result = await plan_trip.execute(
            {"origin": "user", "destination": "Costco"}, self._ctx()
        )
        self.assertTrue(result.ok)
        for event in result.events:
            self.assertEqual(event.itinerary["planning_mode"], "leave_now")
            self.assertIsNone(event.itinerary["requested_departure"])

    async def test_waypoints_emit_one_server_owned_chained_itinerary(self):
        result = await plan_trip.execute(
            {
                "origin": "user",
                "waypoints": ["Joe's Pizza"],
                "destination": "Costco",
            },
            self._ctx(),
        )

        self.assertTrue(result.ok)
        self.assertEqual(self._get_route.await_count, 2)
        self.assertEqual(len(result.events), 1)
        self.assertEqual(len(result.data["candidates"]), 1)

        event = result.events[0]
        itinerary = event.itinerary
        self.assertEqual(event.role, "recommended")
        self.assertEqual(itinerary["waypoints"][0]["display_name"], "Joe's Pizza")
        self.assertEqual(itinerary["waypoints"][0]["dwell_minutes"], 25)
        self.assertEqual(itinerary["waypoints"][0]["dwell_source"], "default")
        self.assertEqual(len(itinerary["segments"]), 2)
        self.assertEqual(itinerary["segments"][0]["destination"]["display_name"], "Joe's Pizza")
        self.assertEqual(itinerary["dwell_events"][0]["duration_seconds"], 25 * 60)
        self.assertEqual([step["segment_index"] for step in event.route], [0, 1])
        self.assertEqual(itinerary["total_dwell_seconds"], 25 * 60)
        # Each provider route is 25 minutes, plus one server-owned dwell.
        self.assertEqual(itinerary["total_duration_seconds"], 75 * 60)
        self.assertEqual(
            event.summary["eta_minutes"],
            round(itinerary["total_duration_seconds"] / 60),
        )

    async def test_waypoint_trip_reports_progress_for_each_sequential_leg(self):
        progress: list[tuple[str, str]] = []

        async def record_progress(stage: str, status: str) -> None:
            progress.append((stage, status))

        ctx = self._ctx()
        ctx.progress_sink = record_progress
        result = await plan_trip.execute(
            {
                "origin": "user",
                "waypoints": ["Joe's Pizza"],
                "destination": "Costco",
            },
            ctx,
        )

        self.assertTrue(result.ok)
        self.assertEqual(self._get_route.await_count, 2)
        self.assertEqual(
            progress,
            [
                ("finding_routes", "active"),
                ("finding_routes", "active"),
                ("finding_routes", "complete"),
                ("checking_live_conditions", "active"),
                ("checking_live_conditions", "complete"),
                ("comparing_options", "active"),
                ("comparing_options", "complete"),
                ("finding_routes", "active"),
                ("finding_routes", "complete"),
                ("checking_live_conditions", "active"),
                ("checking_live_conditions", "complete"),
                ("comparing_options", "active"),
                ("comparing_options", "complete"),
            ],
        )

    async def test_chained_explanation_discloses_incomplete_incidents_once(self):
        with patch.object(
            plan_trip.trip_incidents,
            "scan_route_incidents",
            new=AsyncMock(
                return_value={
                    "incidents": [],
                    "scan_metadata": {"status": "timeout"},
                }
            ),
        ):
            result = await plan_trip.execute(
                {
                    "origin": "user",
                    "waypoints": ["Joe's Pizza"],
                    "destination": "Costco",
                },
                self._ctx(),
            )

        self.assertTrue(result.ok)
        self.assertEqual(
            result.data["passenger_explanation"].casefold().count(
                "incident coverage is incomplete"
            ),
            1,
        )

    async def test_chained_explanation_normalizes_truthful_incomplete_variants(self):
        def advisor_response(prose: str) -> str:
            return (
                f"{prose} [ROUTE:0][CANDIDATE_ANALYSIS]"
                + json.dumps(
                    {
                        "selected_route_index": 0,
                        "candidate_analysis": [
                            {
                                "index": 0,
                                "is_recommended": True,
                                "recommendation_reason": "Direct ride.",
                            },
                            {
                                "index": 1,
                                "is_recommended": False,
                                "rejection_reason": "Slower option.",
                            },
                        ],
                    }
                )
                + "[/CANDIDATE_ANALYSIS]"
            )

        advisor_responses = [
            advisor_response("Take the Q to Joe's Pizza. Incident coverage is incomplete."),
            advisor_response("Take the B to Union Square. Incident information was unavailable."),
            advisor_response("The incident scan timed out."),
        ]
        with (
            patch.object(
                plan_trip.trip_incidents,
                "scan_route_incidents",
                new=AsyncMock(
                    side_effect=[
                        {"incidents": [], "scan_metadata": {"status": "timeout"}},
                        {"incidents": [], "scan_metadata": {"status": "unavailable"}},
                        {"incidents": [], "scan_metadata": {"status": "partial"}},
                    ]
                ),
            ),
            patch.object(
                plan_trip.ai_advisor,
                "collect_agent_recommendation",
                new=AsyncMock(side_effect=advisor_responses),
            ),
        ):
            result = await plan_trip.execute(
                {
                    "origin": "user",
                    "waypoints": ["Joe's Pizza", "Union Square"],
                    "destination": "Costco",
                },
                self._ctx(),
            )

        self.assertTrue(result.ok)
        explanation = result.data["passenger_explanation"]
        self.assertIn("Take the Q to Joe's Pizza.", explanation)
        self.assertIn("Take the B to Union Square.", explanation)
        self.assertEqual(
            explanation.casefold().count("incident coverage is incomplete"),
            1,
        )
        self.assertNotIn("incident information was unavailable", explanation.casefold())
        self.assertNotIn("incident scan timed out", explanation.casefold())
        self.assertNotIn("no active incidents", explanation.casefold())

    async def test_invalid_waypoints_fail_before_location_or_route_providers(self):
        for waypoints in (["A", "B", "C", "D"], ["x" * 161]):
            with self.subTest(waypoints=waypoints):
                self._get_route.reset_mock()
                result = await plan_trip.execute(
                    {"origin": "user", "destination": "Costco", "waypoints": waypoints},
                    self._ctx(),
                )
                self.assertFalse(result.ok)
                self._get_route.assert_not_awaited()

    async def test_waypoint_boundaries_reject_every_invalid_shape_before_providers(self):
        invalid_values = (
            ["A", "B", "C", "D"],
            ["x" * (plan_trip.MAX_WAYPOINT_CHARS + 1)],
            ["A", 2],
            ["A", "   "],
        )
        for waypoints in invalid_values:
            with self.subTest(waypoints=waypoints), patch.object(
                plan_trip.geo, "geocode_address_with_reason", return_value=((40.7, -73.9), None)
            ) as geocode:
                self._get_route.reset_mock()
                result = await plan_trip.execute(
                    {"origin": "user", "destination": "Costco", "waypoints": waypoints}, self._ctx()
                )
                self.assertFalse(result.ok)
                geocode.assert_not_called()
                self._get_route.assert_not_awaited()

    async def test_waypoint_limits_accept_exact_bounds_and_bound_route_provider_legs(self):
        self.assertEqual(plan_trip.MAX_WAYPOINTS, 3)
        self.assertEqual(plan_trip.MAX_WAYPOINT_CHARS, 160)
        self.assertEqual(
            plan_trip._validated_waypoints(["A", "B"]),
            (["A", "B"], None),
        )
        self.assertEqual(
            plan_trip._validated_waypoints(["A", "B", "C"]),
            (["A", "B", "C"], None),
        )
        self._get_route.reset_mock()
        result = await plan_trip.execute(
            {
                "origin": "user",
                "destination": "Costco",
                "waypoints": ["A" * 160, "B", "C"],
            },
            self._ctx(),
        )
        self.assertTrue(result.ok)
        # Three distinct ordered waypoints produce their three legs plus the
        # final destination leg; the provider work is exactly bounded.
        self.assertEqual(self._get_route.await_count, plan_trip.MAX_WAYPOINTS + 1)

    async def test_recommended_card_carries_deterministic_reason_facts(self):
        result = await plan_trip.execute(
            {"origin": "user", "destination": "Costco"}, self._ctx()
        )

        recommended = next(event for event in result.events if event.role == "recommended")
        reasons = recommended.itinerary["structured_recommendation_reasons"]
        self.assertTrue(reasons)
        self.assertEqual(reasons[0]["code"], "fastest")
        self.assertEqual(reasons[0]["difference_seconds"], 6 * 60)
        self.assertIsInstance(result.data["candidates"][0]["structured_recommendation_reasons"], list)

    async def test_arrive_by_derives_a_scheduled_departure_without_becoming_leave_now(self):
        arrival_by = "2026-07-16T22:00:00-04:00"
        result = await plan_trip.execute(
            {
                "origin": "user",
                "destination": "Costco Sunset Park",
                "arrival_by": arrival_by,
            },
            self._ctx(),
        )

        self.assertTrue(result.ok)
        # One internal probe, then the actual route request at derived departure.
        self.assertEqual(self._get_route.await_count, 2)
        recommended = next(event for event in result.events if event.role == "recommended")
        self.assertEqual(recommended.itinerary["planning_mode"], "arrive_by")
        self.assertEqual(recommended.itinerary["requested_arrival"], arrival_by)
        self.assertIsNotNone(recommended.itinerary["requested_departure"])
        self.assertNotEqual(
            recommended.itinerary["requested_departure"],
            arrival_by,
        )

    async def test_coordinate_recovery_keeps_the_named_destination_identity(self):
        first_failure = plan_trip.directions_service.GoogleRoutesError(
            "request_failed",
            "address route failed",
        )
        self._get_route.side_effect = [
            first_failure,
            _google_response(_leg("Q", 5, 20, duration_minutes=25)),
        ]
        result = await plan_trip.execute(
            {"origin": "user", "destination": "Costco Sunset Park"},
            self._ctx(),
        )

        self.assertTrue(result.ok)
        self.assertEqual(self._get_route.await_count, 2)
        event = next(item for item in result.events if item.role == "recommended")
        self.assertEqual(event.destination["label"], "Costco Sunset Park")
        self.assertEqual(event.destination["address"], "Costco Sunset Park")
        self.assertEqual(event.origin["label"], "Your location")
        self.assertNotIn("40.7", event.destination["label"])
        # The recovery request alone carries the provider coordinate input.
        self.assertIsNone(self._get_route.await_args_list[0].args[2])
        self.assertEqual(self._get_route.await_args_list[1].args[2], (40.7128, -74.006))

    async def test_empty_named_destination_response_recovers_with_coordinates(self):
        self._get_route.side_effect = [
            {"routes": []},
            _google_response(_leg("Q", 5, 20, duration_minutes=25)),
        ]

        result = await plan_trip.execute(
            {"origin": "user", "destination": "Coney Island"},
            self._ctx(),
        )

        self.assertTrue(result.ok)
        self.assertEqual(self._get_route.await_count, 2)
        self.assertIsNone(self._get_route.await_args_list[0].args[2])
        self.assertEqual(
            self._get_route.await_args_list[1].args[2],
            (40.7128, -74.006),
        )

    async def test_empty_named_and_coordinate_responses_keep_normalized_failure(self):
        self._get_route.side_effect = [{"routes": []}, {"routes": []}]

        result = await plan_trip.execute(
            {"origin": "user", "destination": "Coney Island"},
            self._ctx(),
        )

        self.assertFalse(result.ok)
        self.assertEqual(result.error, "no transit route found between those points")
        self.assertEqual(self._get_route.await_count, 2)
        self.assertIsNone(self._get_route.await_args_list[0].args[2])
        self.assertEqual(
            self._get_route.await_args_list[1].args[2],
            (40.7128, -74.006),
        )

    async def test_crowd_intent_gives_scored_event_evidence_to_the_selected_agent(self):
        impacts = [
            {
                "event_id": "evt-msg",
                "title": "Concert at the Garden",
                "venue": "Madison Square Garden",
                "route_index": 0,
                "distance_meters": 80,
                "risk_score": 8,
                "confidence": 0.85,
                "exposure_window": "ingress",
                "impact_scope": "station_crowding",
            }
        ]
        captured = {}

        async def choose_b(payload, *, model, explanation_style):
            captured["payload"] = payload
            return (
                "The B avoids the event exposure on the Q. [ROUTE:1]"
                "[CANDIDATE_ANALYSIS]"
                + json.dumps(
                    {
                        "selected_route_index": 1,
                        "candidate_analysis": [
                            {
                                "index": 0,
                                "is_recommended": False,
                                "rejection_reason": "Crowd exposure at the Garden.",
                            },
                            {
                                "index": 1,
                                "is_recommended": True,
                                "recommendation_reason": "Avoids the event crowding.",
                            },
                        ],
                    }
                )
                + "[/CANDIDATE_ANALYSIS]"
            )

        with patch.object(
            plan_trip.crowd_evidence,
            "collect",
            new=AsyncMock(
                return_value=(
                    "available",
                    impacts,
                    [],
                    {"grok_status": "complete"},
                )
            ),
        ) as collect, patch.object(
            plan_trip.ai_advisor,
            "collect_agent_recommendation",
            new=choose_b,
        ):
            result = await plan_trip.execute(
                {
                    "origin": "user",
                    "destination": "Costco",
                    "avoid_crowds": True,
                },
                self._ctx(),
            )

        self.assertTrue(result.ok)
        collect.assert_awaited_once()
        scores = {row["index"]: row["score"] for row in captured["payload"]["scored_candidates"]}
        self.assertGreater(scores[0], scores[1])
        recommended = next(event for event in result.events if event.role == "recommended")
        self.assertEqual(recommended.summary["lines"], ["B"])
        self.assertEqual(result.data["event_evidence"]["status"], "available")
        decision = result.data["selection_decision"]
        self.assertEqual(result.data["selected_route_index"], 1)
        self.assertEqual(decision["selected_candidate_index"], 1)
        self.assertEqual(decision["selected_candidate_id"], recommended.card_id)
        self.assertEqual(
            recommended.itinerary["selection_decision"],
            decision,
        )
        self.assertEqual(recommended.selection_decision, decision)
        self.assertEqual(decision["selection_reason"], "advisor_tiebreak")
        self.assertIn("crowd_evidence_considered", decision["hard_constraints_satisfied"])
        self.assertEqual(decision["evidence_ids"], [])
        active = next(
            card for card in result.session_route_cards if card["role"] == "recommended"
        )
        self.assertEqual(active["card_id"], decision["selected_candidate_id"])
        self.assertEqual(active["selection_decision"], decision)
        first = result.data["candidates"][0]
        self.assertEqual(first["event_impacts"][0]["event_name"], "Concert at the Garden")
        self.assertGreater(first["event_crowd_penalty"], 0)
        self.assertEqual(result.data["evidence"]["events"]["status"], "current")
        self.assertEqual(result.data["evidence"]["events"]["payload"], {"count": 1})
        self.assertNotIn("latitude", str(result.data["evidence"]))

    async def test_ticketmaster_failure_does_not_fail_route_planning(self):
        with patch.object(
            plan_trip.crowd_evidence,
            "collect",
            new=AsyncMock(
                return_value=(
                    "provider_unavailable",
                    [],
                    ["event lookup timed out"],
                    {"grok_status": "unavailable"},
                )
            ),
        ):
            result = await plan_trip.execute(
                {
                    "origin": "user",
                    "destination": "Costco",
                    "avoid_crowds": True,
                },
                self._ctx(),
            )

        self.assertTrue(result.ok)
        self.assertEqual(
            result.data["event_evidence"]["status"],
            "provider_unavailable",
        )
        self.assertEqual(result.data["event_evidence"]["provider_failure_count"], 1)

    async def test_automatic_hotspot_triggers_crowd_collection_in_auto(self):
        hit = plan_trip.crowd_hotspots.HotspotHit(
            route_index=0,
            hotspot_key="midtown_34",
            hotspot_name="MSG and Herald Square",
            station_name="34 St-Herald Sq",
            latitude=40.75,
            longitude=-73.99,
            expected_at=datetime.now(timezone.utc),
            route_id="Q",
        )
        collect = AsyncMock(
            return_value=(
                "no_relevant_events",
                [],
                [],
                {"grok_status": "complete"},
            )
        )
        with (
            patch.object(
                plan_trip.crowd_hotspots,
                "find_hotspot_hits",
                return_value=[hit],
            ),
            patch.object(plan_trip.crowd_evidence, "collect", new=collect),
        ):
            result = await plan_trip.execute(
                {
                    "origin": "user",
                    "destination": "Costco",
                    "crowd_search_mode": "auto",
                },
                self._ctx(),
            )

        self.assertTrue(result.ok)
        collect.assert_awaited_once()
        self.assertFalse(collect.await_args.kwargs["explicit_crowd_request"])
        self.assertTrue(collect.await_args.kwargs["allow_live_search"])

    async def test_incidental_hotspot_in_quick_uses_structured_evidence_only(self):
        hit = plan_trip.crowd_hotspots.HotspotHit(
            route_index=0,
            hotspot_key="midtown_34",
            hotspot_name="MSG and Herald Square",
            station_name="34 St-Herald Sq",
            latitude=40.75,
            longitude=-73.99,
            expected_at=datetime.now(timezone.utc),
            route_id="Q",
        )
        collect = AsyncMock(
            return_value=(
                "no_relevant_events",
                [],
                [],
                {"grok_status": "not_required"},
            )
        )
        with (
            patch.object(
                plan_trip.crowd_hotspots,
                "find_hotspot_hits",
                return_value=[hit],
            ),
            patch.object(plan_trip.crowd_evidence, "collect", new=collect),
        ):
            result = await plan_trip.execute(
                {
                    "origin": "user",
                    "destination": "Costco",
                    "crowd_search_mode": "quick",
                },
                self._ctx(),
            )

        self.assertTrue(result.ok)
        self.assertFalse(collect.await_args.kwargs["allow_live_search"])

    async def test_first_leg_arrival_context_is_preserved_on_recommended_card(self):
        from app.services.agent.tools._types import ToolResult

        arrival_result = ToolResult(
            ok=True,
            data={
                "source_status": "live",
                "catchability": {
                    "walking_minutes": 6,
                    "boarding_buffer_minutes": 2,
                    "arrival_minutes": [3, 11],
                    "catchable_arrival_minutes": 11,
                    "confidence": 0.9,
                },
            },
            summary="live arrivals",
        )
        with patch(
            "app.services.agent.tools.lookup_arrivals.execute",
            new=AsyncMock(return_value=arrival_result),
        ) as lookup:
            result = await plan_trip.execute(
                {
                    "origin": "user",
                    "destination": "Costco",
                    "include_first_leg_arrivals": True,
                },
                self._ctx(),
            )

        self.assertTrue(result.ok)
        lookup.assert_awaited_once()
        recommended = next(event for event in result.events if event.role == "recommended")
        context = recommended.summary["first_leg_arrival"]
        self.assertEqual(context["route_id"], "Q")
        self.assertEqual(context["catchable_arrival_minutes"], 11)


class RouteCardEventItineraryWireTests(unittest.TestCase):
    def test_to_data_omits_itinerary_when_none(self):
        from app.services.agent import events as agent_events

        event = agent_events.RouteCardEvent(
            card_id="rc_x",
            turn_id="t1",
            role="recommended",
            origin={"label": "A"},
            destination={"label": "B"},
            summary={"eta_minutes": 1, "transfers": 0, "lines": [], "reason": None},
            route=[],
            alerts=[],
        )
        data = event.to_data()
        self.assertNotIn("itinerary", data)

    def test_to_data_includes_itinerary_when_present(self):
        from app.services.agent import events as agent_events

        itin = {"itinerary_id": "rc_x", "total_duration_seconds": 120, "transfer_count": 0}
        event = agent_events.RouteCardEvent(
            card_id="rc_x",
            turn_id="t1",
            role="recommended",
            origin={"label": "A"},
            destination={"label": "B"},
            summary={"eta_minutes": 2, "transfers": 0, "lines": [], "reason": None},
            route=[],
            alerts=[],
            itinerary=itin,
        )
        data = event.to_data()
        self.assertEqual(data["itinerary"], itin)


if __name__ == "__main__":
    unittest.main()
