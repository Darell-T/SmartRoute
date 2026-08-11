"""Layer-1 tests for the P0 agent tools (plan_trip, transit_snapshot).

Real anthropic/fastapi imports work in this environment, and
JARVIS_MOCK_ADVISOR=1 makes the judge deterministic without a network call,
so these tests patch only the specific I/O boundaries each tool touches
(Google Routes, MTA feeds, geocoding, GTFS enrichment) rather than faking
the whole `anthropic` module -- that's reserved for test_agent_loop.py,
which needs to script the orchestrator model itself.
"""

import json
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

from app.services.agent.tools import plan_trip, transit_snapshot
from tests._fake_http_tools import make_tool_ctx


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _leg(route_id: str, board_in_minutes: int, ride_minutes: int) -> dict:
    now = datetime.now(timezone.utc)
    depart = now + timedelta(minutes=board_in_minutes)
    arrive = depart + timedelta(minutes=ride_minutes)
    return {
        "duration": f"{(board_in_minutes + ride_minutes) * 60}s",
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
                    "transitLine": {"nameShort": route_id, "color": "#000000", "vehicle": {"type": "SUBWAY"}},
                    "stopCount": 10,
                },
            }
        ],
    }


def _google_response(*legs: dict) -> dict:
    return {"routes": [{"legs": [leg]} for leg in legs]}


NYC_COORDS = (40.7128, -74.0060)


def _advisor_response(
    prose: str,
    *,
    candidate_count: int,
    selected_index: int = 0,
) -> str:
    rows = []
    for index in range(candidate_count):
        if index == selected_index:
            rows.append(
                {
                    "index": index,
                    "is_recommended": True,
                    "recommendation_reason": "Best current trade-off.",
                }
            )
        else:
            rows.append(
                {
                    "index": index,
                    "is_recommended": False,
                    "rejection_reason": "Less suitable for this trip.",
                }
            )
    analysis = {"selected_route_index": selected_index, "candidate_analysis": rows}
    return (
        f"{prose} [ROUTE:{selected_index}]"
        f"[CANDIDATE_ANALYSIS]{json.dumps(analysis)}[/CANDIDATE_ANALYSIS]"
    )


class PlanTripToolTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        env_patch = patch.dict("os.environ", {"SMARTROUTE_ENV": "test", "JARVIS_MOCK_ADVISOR": "1"})
        env_patch.start()
        self.addCleanup(env_patch.stop)

        self._geocode = patch.object(
            plan_trip.geo, "geocode_address_with_reason", return_value=(NYC_COORDS, None)
        ).start()
        self._enrich = patch.object(plan_trip.enrichment, "_enrich_route", new=AsyncMock(return_value={})).start()
        self._alerts = patch.object(plan_trip, "fetch_service_alerts", new=AsyncMock(return_value=b"")).start()
        self._stalled_trains = patch.object(plan_trip, "get_stalled_trains", new=AsyncMock(return_value=[])).start()
        self._stalled_buses = patch.object(plan_trip, "get_stalled_buses", new=AsyncMock(return_value=[])).start()
        self._get_route = patch.object(
            plan_trip.directions_service,
            "get_transit_route",
            new=AsyncMock(return_value=_google_response(_leg("Q", 5, 20), _leg("B", 3, 28))),
        ).start()
        self._scan_incidents = patch.object(
            plan_trip.trip_incidents,
            "scan_route_incidents",
            new=AsyncMock(
                return_value={
                    "incidents": [],
                    "scan_metadata": {
                        "status": "complete",
                        "scanned_at": "2026-08-01T12:00:00Z",
                        "cache_hit": False,
                    },
                }
            ),
        ).start()
        self.addCleanup(patch.stopall)

    def _ctx(self, origin=None, gtfs=None):
        return make_tool_ctx(origin, gtfs=gtfs)

    async def test_destination_required(self):
        result = await plan_trip.execute({"origin": "user", "destination": ""}, self._ctx())
        self.assertFalse(result.ok)
        self.assertIn("destination", result.error)

    async def test_exclude_all_modes_is_rejected_without_network_calls(self):
        result = await plan_trip.execute(
            {"origin": "user", "destination": "Costco", "exclude_modes": ["BUS", "SUBWAY"]},
            self._ctx(origin={"lat": 40.7, "lng": -73.9}),
        )
        self.assertFalse(result.ok)
        self.assertIn("no transit modes left", result.error)
        self._get_route.assert_not_awaited()
        self._geocode.assert_not_called()

    async def test_exclude_bus_maps_to_allowed_travel_modes_subway_only(self):
        await plan_trip.execute(
            {"origin": "user", "destination": "Costco", "exclude_modes": ["BUS"]},
            self._ctx(origin={"lat": 40.7, "lng": -73.9}),
        )
        _args, kwargs = self._get_route.call_args
        self.assertEqual(kwargs["allowed_travel_modes"], ["SUBWAY"])

    async def test_departure_time_is_passed_through_to_directions(self):
        await plan_trip.execute(
            {"origin": "user", "destination": "MSG", "departure_time": "2026-07-16T22:00:00-04:00"},
            self._ctx(origin={"lat": 40.7, "lng": -73.9}),
        )
        _args, kwargs = self._get_route.call_args
        self.assertEqual(kwargs["departure_time"], "2026-07-16T22:00:00-04:00")

    async def test_omitted_origin_resolves_to_rider_gps_without_reverse_geocoding(self):
        # Audit proof: rider_location is exposed to the route-preparation
        # tool/state layer via ToolContext.origin, so origin "user" resolves
        # to GPS coordinates deterministically -- no model-copied lat/lng
        # prose and no reverse geocoding of the current location.
        result = await plan_trip.execute(
            {"origin": "user", "destination": "Barclays Center"},
            self._ctx(origin={"lat": 40.7, "lng": -73.9}),
        )
        self.assertTrue(result.ok)
        self._geocode.assert_not_called()
        _args, _kwargs = self._get_route.call_args
        self.assertEqual(_args[0], (40.7, -73.9))
        recommended = next(event for event in result.events if event.role == "recommended")
        self.assertEqual(recommended.origin["label"], "Your location")

    async def test_explicit_origin_is_never_overridden_by_rider_location(self):
        await plan_trip.execute(
            {"origin": "350 5th Ave", "destination": "Costco"},
            self._ctx(origin={"lat": 40.7, "lng": -73.9}),
        )
        self._geocode.assert_any_call("350 5th Ave")
        _args, _kwargs = self._get_route.call_args
        self.assertEqual(_args[0], NYC_COORDS)

    async def test_origin_user_without_gps_asks_for_location(self):
        result = await plan_trip.execute({"origin": "user", "destination": "Costco"}, self._ctx(origin=None))
        self.assertFalse(result.ok)
        self.assertIn("location", result.error.lower())
        self._get_route.assert_not_awaited()

    async def test_origin_address_is_geocoded(self):
        await plan_trip.execute(
            {"origin": "350 5th Ave", "destination": "Costco"},
            self._ctx(origin=None),
        )
        self._geocode.assert_any_call("350 5th Ave")

    async def test_destination_geocode_failure_is_reported(self):
        # origin="user" resolves from ctx.origin without a geocode call, so
        # the only geocode() call is for the destination.
        self._geocode.side_effect = [(None, "Address not found in NYC.")]
        result = await plan_trip.execute(
            {"origin": "user", "destination": "nowhere"}, self._ctx(origin={"lat": 40.7, "lng": -73.9})
        )
        self.assertFalse(result.ok)
        self.assertEqual(result.error, "Address not found in NYC.")

    async def test_successful_call_returns_digest_route_cards_and_session_cards(self):
        result = await plan_trip.execute(
            {"origin": "user", "destination": "Costco"}, self._ctx(origin={"lat": 40.7, "lng": -73.9})
        )
        self.assertTrue(result.ok)
        candidates = result.data["candidates"]
        self.assertEqual(len(candidates), 2)
        for candidate in candidates:
            self.assertTrue(candidate["card_id"].startswith("rc_"))
            self.assertIn("lines", candidate)
            self.assertIn("eta_minutes", candidate)
            self.assertIn("transfers", candidate)
            self.assertIn("reason", candidate)
            # No route geometry leaks into the model-facing digest.
            self.assertNotIn("polyline", candidate)
            self.assertNotIn("route", candidate)

        self.assertEqual(len(result.events), 2)
        card_ids = {card["card_id"] for card in result.session_route_cards}
        self.assertEqual(card_ids, {c["card_id"] for c in candidates})
        roles = {event.role for event in result.events}
        self.assertEqual(roles, {"recommended", "alternative"})

        recommended = next(event for event in result.events if event.role == "recommended")
        self.assertEqual(recommended.turn_id, "t1")
        self.assertTrue(any(step.get("type") == "SUBWAY" for step in recommended.route))

    async def test_successful_plan_trip_reports_monotonic_live_progress_stages(self):
        stages = []

        async def collect(stage, status):
            stages.append((stage, status))

        ctx = self._ctx(origin={"lat": 40.7, "lng": -73.9})
        ctx.progress_sink = collect
        result = await plan_trip.execute(
            {"origin": "user", "destination": "Costco"}, ctx
        )

        self.assertTrue(result.ok)
        self.assertEqual(
            stages,
            [
                ("finding_routes", "active"),
                ("finding_routes", "complete"),
                ("checking_live_conditions", "active"),
                ("checking_live_conditions", "complete"),
                ("comparing_options", "active"),
                ("comparing_options", "complete"),
            ],
        )

    async def test_chained_plan_trip_reports_progress_for_each_sequential_leg(self):
        stages = []

        async def collect(stage, status):
            stages.append((stage, status))

        ctx = self._ctx(origin={"lat": 40.7, "lng": -73.9})
        ctx.progress_sink = collect
        result = await plan_trip.execute(
            {
                "origin": "user",
                "destination": "Costco",
                "waypoints": ["Union Square"],
            },
            ctx,
        )

        self.assertTrue(result.ok)
        expected_cycle = [
            ("finding_routes", "active"),
            ("finding_routes", "complete"),
            ("checking_live_conditions", "active"),
            ("checking_live_conditions", "complete"),
            ("comparing_options", "active"),
            ("comparing_options", "complete"),
        ]
        self.assertEqual(stages, [("finding_routes", "active")] + expected_cycle * 2)
        self.assertEqual(stages[7], ("finding_routes", "active"))
        self.assertEqual(stages[-1], ("comparing_options", "complete"))

    async def test_no_routes_found_is_reported(self):
        self._get_route.return_value = {"routes": []}
        result = await plan_trip.execute(
            {"origin": "user", "destination": "Costco"}, self._ctx(origin={"lat": 40.7, "lng": -73.9})
        )
        self.assertFalse(result.ok)
        self.assertIn("no transit route", result.error)

    async def test_google_routes_error_is_reported_without_traceback(self):
        self._get_route.side_effect = plan_trip.directions_service.GoogleRoutesError("timeout", "boom")
        result = await plan_trip.execute(
            {"origin": "user", "destination": "Costco"}, self._ctx(origin={"lat": 40.7, "lng": -73.9})
        )
        self.assertFalse(result.ok)
        self.assertIn("timeout", result.error)
        self.assertNotIn("Traceback", result.error)

    async def test_route_planning_always_scans_the_normalized_incident_contract(self):
        result = await plan_trip.execute(
            {"origin": "user", "destination": "Costco"},
            self._ctx(origin={"lat": 40.7, "lng": -73.9}),
        )

        self._scan_incidents.assert_awaited_once()
        self.assertEqual(result.data["incident_evidence"], {
            "status": "complete",
            "scanned_at": "2026-08-01T12:00:00Z",
            "cache_hit": False,
        })

    async def test_incident_evidence_projects_bounded_index_metadata_without_records(self):
        self._scan_incidents.return_value = {
            "incidents": [
                {
                    "incident_id": "inc_secret",
                    "location": "Atlantic Avenue",
                    "severity": "high",
                    "description": "Should never appear in evidence.",
                    "advisor_eligible": True,
                }
            ],
            "scan_metadata": {
                "status": "partial",
                "scanned_at": "2026-08-01T12:00:00Z",
                "cache_hit": True,
                "sources": {
                    "attempted": ["incident_index"],
                    "completed": ["incident_index"],
                },
                "lookup_status": "complete",
                "coverage_status": "partial",
                "lookup_kind": "index",
                "requested_coverage_ids": ["lower-manhattan"],
                "warning_count": 1,
                "lookup_latency_ms": 4.2,
            },
        }
        result = await plan_trip.execute(
            {"origin": "user", "destination": "Costco"},
            self._ctx(origin={"lat": 40.7, "lng": -73.9}),
        )

        self.assertTrue(result.ok)
        evidence = result.data["incident_evidence"]
        self.assertEqual(evidence["status"], "partial")
        self.assertEqual(evidence["scanned_at"], "2026-08-01T12:00:00Z")
        self.assertIs(evidence["cache_hit"], True)
        self.assertEqual(evidence["sources"]["completed"], ["incident_index"])
        self.assertEqual(evidence["lookup_status"], "complete")
        self.assertEqual(evidence["coverage_status"], "partial")
        self.assertEqual(evidence["lookup_kind"], "index")
        self.assertEqual(evidence["requested_coverage_ids"], ["lower-manhattan"])
        self.assertEqual(evidence["warning_count"], 1)
        self.assertEqual(evidence["lookup_latency_ms"], 4.2)
        self.assertNotIn("incidents", evidence)
        self.assertNotIn("inc_secret", json.dumps(evidence))
        self.assertNotIn("Atlantic Avenue", json.dumps(evidence))

    async def test_partial_scan_with_incidents_does_not_reach_the_advisor(self):
        incident = {
            "location": "Atlantic Avenue",
            "nearby_station": "Atlantic Av-Barclays Center",
            "severity": "medium",
            "description": "Station access is restricted.",
            "advisor_eligible": True,
        }
        self._scan_incidents.return_value = {
            "incidents": [incident],
            "scan_metadata": {
                "status": "partial",
                "scanned_at": "2026-08-01T12:00:00Z",
                "cache_hit": False,
            },
        }
        captured = {}

        async def capture_advisor(payload, *, model, explanation_style):
            captured["payload"] = payload
            captured["model"] = model
            captured["explanation_style"] = explanation_style
            yield _advisor_response(
                "Take the Q.", candidate_count=len(payload["routes"])
            )

        ctx = self._ctx(origin={"lat": 40.7, "lng": -73.9})
        with (
            patch.object(
                plan_trip.ai_advisor,
                "collect_recommendation",
                new=AsyncMock(side_effect=AssertionError("REST advisor must not run")),
            ),
            patch.object(plan_trip.ai_advisor, "stream_agent_recommendation", new=capture_advisor),
        ):
            result = await plan_trip.execute(
                {"origin": "user", "destination": "Costco"},
                ctx,
            )

        self.assertTrue(result.ok)
        self.assertEqual(captured["payload"]["incidents"], [])
        self.assertEqual(captured["payload"]["evidence"]["advisor"]["status"], "unavailable")
        self.assertIn("incident coverage is incomplete", result.data["passenger_explanation"])
        self.assertNotIn("[ROUTE:", result.data["passenger_explanation"])
        self.assertNotIn("[CANDIDATE_ANALYSIS]", result.data["passenger_explanation"])
        self.assertEqual(result.data["incident_evidence"]["status"], "partial")
        self.assertEqual(captured["model"], plan_trip.agent_policy.policy_for_mode("auto").model)
        model_call = ctx.telemetry["model_calls"][0]
        self.assertEqual(model_call["role"], "route_selection")
        self.assertEqual(model_call["provider"], "anthropic")
        self.assertEqual(model_call["model"], plan_trip.agent_policy.safe_model_label(captured["model"]))
        self.assertEqual(model_call["outcome"], "complete")

    async def test_partial_empty_scan_is_not_presented_as_an_all_clear(self):
        self._scan_incidents.return_value = {
            "incidents": [],
            "scan_metadata": {
                "status": "partial",
                "scanned_at": "2026-08-01T12:00:00Z",
                "cache_hit": False,
            },
        }
        captured = {}

        async def capture_advisor(payload, *, model, explanation_style):
            captured["payload"] = payload
            yield _advisor_response(
                "No active incidents are affecting the Q.",
                candidate_count=len(payload["routes"]),
            )

        result = None
        with patch.object(plan_trip.ai_advisor, "stream_agent_recommendation", new=capture_advisor):
            result = await plan_trip.execute(
                {"origin": "user", "destination": "Costco"},
                self._ctx(origin={"lat": 40.7, "lng": -73.9}),
            )

        self.assertTrue(result.ok)
        self.assertEqual(captured["payload"]["incidents"], [])
        self.assertEqual(captured["payload"]["evidence"]["advisor"]["status"], "unavailable")
        self.assertIn("incident coverage is incomplete", result.data["passenger_explanation"])
        self.assertNotIn("no active incidents", result.data["passenger_explanation"].casefold())
        self.assertNotIn("all clear", result.data["passenger_explanation"].casefold())

    async def test_plan_trip_uses_the_turn_selected_agent_model_and_style(self):
        captured = {}

        async def capture_advisor(payload, *, model, explanation_style):
            captured["payload"] = payload
            captured["model"] = model
            captured["explanation_style"] = explanation_style
            yield _advisor_response(
                "Take the Q.", candidate_count=len(payload["routes"])
            )

        with patch.object(plan_trip.ai_advisor, "stream_agent_recommendation", new=capture_advisor):
            result = await plan_trip.execute(
                {"origin": "user", "destination": "Costco"},
                self._ctx(origin={"lat": 40.7, "lng": -73.9}),
            )

        self.assertTrue(result.ok)
        self.assertEqual(captured["payload"]["planning_mode"], "intelligence")
        self.assertIn("route_candidate_labels", captured["payload"])
        self.assertEqual(
            [row["index"] for row in captured["payload"]["scored_candidates"]],
            [0, 1],
        )
        self.assertEqual(captured["model"], plan_trip.agent_policy.policy_for_mode("auto").model)
        self.assertEqual(captured["explanation_style"], "comparative")

    async def test_valid_agent_selection_is_preserved_when_event_scoring_prefers_another_route(self):
        async def event_evidence(*_args, **_kwargs):
            return (
                "available",
                [
                    {
                        "event_id": "event-1",
                        "title": "Crowded venue",
                        "venue": "Test venue",
                        "route_index": 0,
                        "risk_score": 18.0,
                        "scoring_authorized": True,
                    }
                ],
                [],
                {"grok_status": "complete"},
            )

        captured = {}

        async def choose_first(payload, *, model, explanation_style):
            captured["payload"] = payload
            yield _advisor_response(
                "The Q is still the better fit for your request.",
                candidate_count=len(payload["routes"]),
                selected_index=0,
            )

        with (
            patch.object(plan_trip.crowd_evidence, "collect", side_effect=event_evidence),
            patch.object(plan_trip.ai_advisor, "stream_agent_recommendation", new=choose_first),
        ):
            result = await plan_trip.execute(
                {"origin": "user", "destination": "Costco", "avoid_crowds": True},
                self._ctx(origin={"lat": 40.7, "lng": -73.9}),
            )

        self.assertTrue(result.ok)
        scores = {row["index"]: row["score"] for row in captured["payload"]["scored_candidates"]}
        self.assertGreater(scores[0], scores[1])
        self.assertEqual(result.data["selected_route_index"], 0)
        self.assertEqual(
            result.data["selection_decision"]["selection_reason"], "advisor_tiebreak"
        )

    async def test_invalid_agent_controls_use_deterministic_score_fallback_with_event_evidence(self):
        async def event_evidence(*_args, **_kwargs):
            return (
                "available",
                [
                    {
                        "event_id": "event-1",
                        "title": "Crowded venue",
                        "venue": "Test venue",
                        "route_index": 0,
                        "risk_score": 18.0,
                        "scoring_authorized": True,
                    }
                ],
                [],
                {"grok_status": "complete"},
            )

        invalid_responses = {
            "missing_marker": "Take the Q.",
            "out_of_range": _advisor_response(
                "Take the Q.", candidate_count=2, selected_index=2
            ),
            "malformed_analysis": "Take the Q. [ROUTE:0][CANDIDATE_ANALYSIS]{oops}[/CANDIDATE_ANALYSIS]",
        }
        for name, response in invalid_responses.items():
            ctx = self._ctx(origin={"lat": 40.7, "lng": -73.9})

            async def stream_invalid(_payload, *, model, explanation_style, _response=response):
                yield _response

            with self.subTest(name=name), patch.object(
                plan_trip.crowd_evidence, "collect", side_effect=event_evidence
            ), patch.object(
                plan_trip.ai_advisor,
                "stream_agent_recommendation",
                new=stream_invalid,
            ):
                result = await plan_trip.execute(
                    {"origin": "user", "destination": "Costco", "avoid_crowds": True},
                    ctx,
                )

            self.assertTrue(result.ok)
            self.assertEqual(result.data["selected_route_index"], 1)
            self.assertEqual(ctx.telemetry["plan_trip"]["advisor_status"], "invalid")
            self.assertIs(ctx.telemetry["plan_trip"]["advisor_fallback"], True)

    async def test_quick_mode_uses_haiku_for_each_chained_leg(self):
        calls = []

        async def capture_advisor(payload, *, model, explanation_style):
            calls.append((model, explanation_style, payload["planning_mode"]))
            yield _advisor_response(
                "Take the Q.", candidate_count=len(payload["routes"])
            )

        policy = plan_trip.agent_policy.policy_for_mode("quick")
        ctx = self._ctx(origin={"lat": 40.7, "lng": -73.9})
        ctx.agent_mode = policy.mode
        ctx.agent_model = policy.model
        ctx.agent_explanation_style = policy.explanation_style
        with patch.object(plan_trip.ai_advisor, "stream_agent_recommendation", new=capture_advisor):
            result = await plan_trip.execute(
                {
                    "origin": "user",
                    "destination": "Central Park",
                    "waypoints": ["Union Square"],
                },
                ctx,
            )

        self.assertTrue(result.ok)
        self.assertEqual(calls, [(policy.model, "concise", "intelligence")] * 2)
        self.assertNotEqual(policy.model, plan_trip.agent_policy.policy_for_mode("auto").model)
        self.assertIn("Take the Q.", result.data["passenger_explanation"])


class TransitSnapshotToolTests(unittest.IsolatedAsyncioTestCase):
    def _ctx(self, origin=None, gtfs="fake-gtfs"):
        return make_tool_ctx(origin, gtfs=gtfs)

    async def test_near_user_without_gps_asks_for_location(self):
        result = await transit_snapshot.execute({"near": "user"}, self._ctx(origin=None))
        self.assertFalse(result.ok)
        self.assertIn("location", result.error.lower())

    async def test_near_resolves_and_builds_snapshot(self):
        snapshot = {
            "nearest_stop": {"stop_name": "Church Av", "distance_m": 50},
            "arrivals": [{"route_id": "Q", "station_name": "Church Av", "arrival_time": 123}],
            "alerts": [{"header": "Delays on Q", "route_ids": ["Q"]}],
            "signals": {"network_status": "healthy"},
        }
        with patch.object(
            transit_snapshot, "_build_live_snapshot", new=AsyncMock(return_value=snapshot)
        ) as build_snapshot:
            result = await transit_snapshot.execute(
                {"near": "user"}, self._ctx(origin={"lat": 40.7, "lng": -73.9})
            )
            build_snapshot.assert_awaited_once_with("fake-gtfs", 40.7, -73.9)
        self.assertTrue(result.ok)
        self.assertEqual(result.data["network_status"], "healthy")
        self.assertEqual(len(result.data["arrivals"]), 1)

    async def test_near_without_gtfs_ready_is_reported(self):
        result = await transit_snapshot.execute(
            {"near": "user"}, self._ctx(origin={"lat": 40.7, "lng": -73.9}, gtfs=None)
        )
        self.assertFalse(result.ok)
        self.assertIn("not ready", result.error)

    async def test_lines_filter_alerts_without_location(self):
        alerts = [
            {"header": "Q delayed", "route_ids": ["Q"]},
            {"header": "B suspended", "route_ids": ["B"]},
        ]
        with patch.object(transit_snapshot.mta_feed, "fetch_service_alerts", new=AsyncMock(return_value=b"x")), \
             patch.object(transit_snapshot.mta_feed, "parse_service_alerts", return_value=alerts), \
             patch.object(
                 transit_snapshot.mta_feed,
                 "filter_alerts_for_routes",
                 side_effect=lambda parsed, route_ids: [a for a in parsed if set(a["route_ids"]) & route_ids],
             ):
            result = await transit_snapshot.execute({"lines": ["Q"]}, self._ctx(origin=None))
        self.assertTrue(result.ok)
        self.assertEqual(len(result.data["alerts"]), 1)
        self.assertIn("Q", result.data["alerts"][0]["header"])

    async def test_alert_headline_is_capped_via_safe_text(self):
        long_header = "X" * 500
        with patch.object(transit_snapshot.mta_feed, "fetch_service_alerts", new=AsyncMock(return_value=b"x")), \
             patch.object(
                 transit_snapshot.mta_feed, "parse_service_alerts", return_value=[{"header": long_header, "route_ids": []}]
             ):
            result = await transit_snapshot.execute({}, self._ctx(origin=None))
        self.assertLessEqual(len(result.data["alerts"][0]["header"]), 200)


if __name__ == "__main__":
    unittest.main()
