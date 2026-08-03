"""Safe, stage-specific telemetry for production trip turns."""

from __future__ import annotations

import asyncio
import json
import unittest
from unittest.mock import AsyncMock, patch

from app.services.agent import policy as agent_policy
from app.services.agent import turn_stream, turn_telemetry
from app.services.agent.tools import plan_trip
from tests._fake_http_tools import make_tool_ctx
from tests.test_plan_trip_itinerary import _google_response, _leg


class TripPipelineTelemetryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        patch.dict(
            "os.environ",
            {"SMARTROUTE_ENV": "test", "JARVIS_MOCK_ADVISOR": "1"},
        ).start()
        patch.object(
            plan_trip.geo,
            "geocode_address_with_reason",
            return_value=((40.7128, -74.0060), None),
        ).start()
        patch.object(
            plan_trip.enrichment,
            "_enrich_route",
            new=AsyncMock(return_value={}),
        ).start()
        patch.object(
            plan_trip,
            "fetch_service_alerts",
            new=AsyncMock(return_value=b""),
        ).start()
        patch.object(
            plan_trip,
            "get_stalled_trains",
            new=AsyncMock(return_value=[]),
        ).start()
        patch.object(
            plan_trip,
            "get_stalled_buses",
            new=AsyncMock(return_value=[]),
        ).start()
        patch.object(
            plan_trip.directions_service,
            "get_transit_route",
            new=AsyncMock(return_value=_google_response(_leg("Q", 3, 20))),
        ).start()
        self.addCleanup(patch.stopall)

    @staticmethod
    def _ctx():
        return make_tool_ctx(origin={"lat": 40.7, "lng": -73.9})

    async def test_successful_cached_scan_records_all_pipeline_stages(self):
        patch.object(
            plan_trip.trip_incidents,
            "scan_route_incidents",
            new=AsyncMock(
                return_value={
                    "incidents": [],
                    "scan_metadata": {"status": "complete", "cache_hit": True},
                }
            ),
        ).start()
        ctx = self._ctx()

        result = await plan_trip.execute(
            {"origin": "user", "destination": "Central Park"},
            ctx,
        )

        self.assertTrue(result.ok)
        pipeline = ctx.telemetry["plan_trip"]
        self.assertEqual(pipeline["outcome"], "success")
        self.assertEqual(pipeline["incident_status"], "complete")
        self.assertIs(pipeline["incident_cache_hit"], True)
        self.assertEqual(pipeline["advisor_status"], "complete")
        self.assertIs(pipeline["advisor_fallback"], False)
        for field in (
            "google_routes_ms",
            "mta_evidence_ms",
            "incident_ms",
            "advisor_ms",
            "scoring_ms",
            "enrichment_ms",
            "plan_trip_ms",
        ):
            self.assertGreaterEqual(pipeline[field], 0)

    async def test_incident_timeout_is_distinct_from_provider_failure(self):
        async def slow_scan(*_args, **_kwargs):
            await asyncio.sleep(1)

        patch.object(
            plan_trip.trip_incidents,
            "scan_route_incidents",
            side_effect=slow_scan,
        ).start()
        ctx = self._ctx()

        with patch.object(plan_trip, "AGENT_GROK_BUDGET_S", 0.001):
            result = await plan_trip.execute(
                {"origin": "user", "destination": "Central Park"},
                ctx,
            )

        self.assertTrue(result.ok)
        self.assertEqual(ctx.telemetry["plan_trip"]["incident_status"], "timeout")
        self.assertIs(ctx.telemetry["plan_trip"]["incident_cache_hit"], False)

    async def test_incident_duration_is_not_inflated_by_slower_event_evidence(self):
        async def slow_events(*_args, **_kwargs):
            await asyncio.sleep(0.06)
            return "available", [], [], {"grok_status": "complete"}

        patch.object(
            plan_trip.crowd_evidence,
            "collect",
            side_effect=slow_events,
        ).start()
        patch.object(
            plan_trip.trip_incidents,
            "scan_route_incidents",
            new=AsyncMock(
                return_value={
                    "incidents": [],
                    "scan_metadata": {"status": "complete", "cache_hit": False},
                }
            ),
        ).start()
        ctx = self._ctx()

        result = await plan_trip.execute(
            {
                "origin": "user",
                "destination": "Central Park",
                "avoid_crowds": True,
            },
            ctx,
        )

        self.assertTrue(result.ok)
        self.assertGreater(result.timings["ticketmaster_ms"], 40)
        self.assertLess(
            result.timings["incident_ms"],
            result.timings["ticketmaster_ms"] / 2,
        )

    async def test_advisor_failure_records_fallback_without_failing_route(self):
        patch.object(
            plan_trip.trip_incidents,
            "scan_route_incidents",
            new=AsyncMock(
                return_value={
                    "incidents": [],
                    "scan_metadata": {"status": "complete", "cache_hit": False},
                }
            ),
        ).start()
        patch.object(
            plan_trip.ai_advisor,
            "collect_agent_recommendation",
            new=AsyncMock(side_effect=RuntimeError("provider unavailable")),
        ).start()
        ctx = self._ctx()

        result = await plan_trip.execute(
            {"origin": "user", "destination": "Central Park"},
            ctx,
        )

        self.assertTrue(result.ok)
        self.assertEqual(ctx.telemetry["plan_trip"]["advisor_status"], "failed")
        self.assertIs(ctx.telemetry["plan_trip"]["advisor_fallback"], True)

    async def test_advisor_timeout_records_timeout_fallback(self):
        async def slow_advisor(_payload, **_kwargs):
            await asyncio.sleep(1)

        patch.object(
            plan_trip.trip_incidents,
            "scan_route_incidents",
            new=AsyncMock(
                return_value={
                    "incidents": [],
                    "scan_metadata": {"status": "complete", "cache_hit": False},
                }
            ),
        ).start()
        patch.object(
            plan_trip.ai_advisor,
            "collect_agent_recommendation",
            side_effect=slow_advisor,
        ).start()
        ctx = self._ctx()

        with patch.object(plan_trip, "TRIP_ADVISOR_TIMEOUT_S", 0.001):
            result = await plan_trip.execute(
                {"origin": "user", "destination": "Central Park"},
                ctx,
            )

        self.assertTrue(result.ok)
        self.assertEqual(ctx.telemetry["plan_trip"]["advisor_status"], "timeout")
        self.assertIs(ctx.telemetry["plan_trip"]["advisor_fallback"], True)

    async def test_chained_trip_uses_conservative_status_aggregation(self):
        patch.object(
            plan_trip.trip_incidents,
            "scan_route_incidents",
            new=AsyncMock(
                side_effect=[
                    {
                        "incidents": [],
                        "scan_metadata": {"status": "complete", "cache_hit": True},
                    },
                    asyncio.TimeoutError(),
                ]
            ),
        ).start()
        patch.object(
            plan_trip.ai_advisor,
            "collect_agent_recommendation",
            new=AsyncMock(side_effect=["[ROUTE:0]", RuntimeError("advisor down")]),
        ).start()
        ctx = self._ctx()

        result = await plan_trip.execute(
            {
                "origin": "user",
                "destination": "Central Park",
                "waypoints": ["Union Square"],
            },
            ctx,
        )

        self.assertTrue(result.ok)
        pipeline = ctx.telemetry["plan_trip"]
        self.assertEqual(pipeline["leg_count"], 2)
        self.assertEqual(pipeline["incident_status"], "timeout")
        self.assertIs(pipeline["incident_cache_hit"], False)
        self.assertEqual(pipeline["advisor_status"], "failed")
        self.assertIs(pipeline["advisor_fallback"], True)

    async def test_chained_cache_hit_is_unknown_when_any_leg_is_unclassified(self):
        patch.object(
            plan_trip.trip_incidents,
            "scan_route_incidents",
            new=AsyncMock(
                side_effect=[
                    {
                        "incidents": [],
                        "scan_metadata": {"status": "complete", "cache_hit": True},
                    },
                    {
                        "incidents": [],
                        "scan_metadata": {"status": "complete"},
                    },
                ]
            ),
        ).start()
        ctx = self._ctx()

        result = await plan_trip.execute(
            {
                "origin": "user",
                "destination": "Central Park",
                "waypoints": ["Union Square"],
            },
            ctx,
        )

        self.assertTrue(result.ok)
        pipeline = ctx.telemetry["plan_trip"]
        self.assertEqual(pipeline["leg_count"], 2)
        self.assertIsNone(pipeline["incident_cache_hit"])

    def test_structured_record_is_allowlisted_and_contains_no_sensitive_data(self):
        telemetry = {
            "plan_trip": {
                "outcome": "success",
                "incident_status": "complete",
                "incident_cache_hit": False,
                "advisor_status": "complete",
                "advisor_fallback": False,
                "google_routes_ms": 12.4,
                "mta_evidence_ms": 8.2,
                "incident_ms": 30.1,
                "advisor_ms": 22.0,
                "scoring_ms": 2.0,
                "enrichment_ms": 4.0,
                "plan_trip_ms": 81.0,
                "prompt": "secret rider text",
                "origin": {"lat": 40.7, "lng": -73.9},
                "api_key": "provider-secret",
            }
        }

        with patch("builtins.print") as printed:
            record = turn_stream._emit_trip_pipeline_timing(
                turn_id="t7",
                stage_ms={"model_ms": 42.0, "total_ms": 90.0},
                telemetry=telemetry,
                first_route_card_ms=79.0,
            )

        self.assertIsNotNone(record)
        encoded = json.dumps(record)
        for forbidden in ("secret rider text", "40.7", "-73.9", "provider-secret"):
            self.assertNotIn(forbidden, encoded)
        self.assertEqual(record["turn_id"], "t7")
        self.assertEqual(record["leg_count"], 0)
        self.assertEqual(record["outer_model_ms"], 42)
        self.assertEqual(record["first_route_card_ms"], 79)
        printed.assert_called_once_with(
            f"[trip-pipeline] {json.dumps(record, sort_keys=True, separators=(',', ':'))}",
            flush=True,
        )

    def test_model_call_telemetry_uses_ordered_records_and_final_role_totals(self):
        for mode in ("auto", "quick"):
            with self.subTest(mode=mode):
                policy = agent_policy.policy_for_mode(mode)
                telemetry = {
                    "mode": mode,
                    "plan_trip": {
                        "outcome": "success",
                        "incident_status": "complete",
                        "advisor_status": "complete",
                    },
                }
                turn_telemetry.record_model_call(
                    telemetry,
                    role="conversation",
                    provider="anthropic",
                    model=policy.model,
                    duration_ms=12,
                    outcome="complete",
                )
                turn_telemetry.record_model_call(
                    telemetry,
                    role="route_selection",
                    provider="anthropic",
                    model=policy.model,
                    duration_ms=8,
                    outcome="complete",
                )
                with patch("builtins.print"):
                    record = turn_stream._emit_trip_pipeline_timing(
                        turn_id="t-models",
                        stage_ms={"model_ms": 12, "total_ms": 20},
                        telemetry=telemetry,
                        first_route_card_ms=18,
                    )

                self.assertEqual([call["call_index"] for call in record["model_calls"]], [1, 2])
                self.assertNotIn("call_count", record["model_calls"][0])
                self.assertEqual(record["model_call_total"], 2)
                self.assertEqual(record["outer_model_call_total"], 1)
                self.assertEqual(record["route_selection_call_total"], 1)
                self.assertEqual(
                    [call["model"] for call in record["model_calls"]],
                    [policy.model, policy.model],
                )


if __name__ == "__main__":
    unittest.main()
