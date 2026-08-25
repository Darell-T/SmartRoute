"""Focused tests for the bounded direct-area conditions contract."""

from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, patch

from app.services.agent.tools.transit import check_area_conditions
from app.services.agent.tools.location_resolution import ResolvedPlace
from app.services.agent.tools._types import ToolContext


class _NearbyGtfs:
    def get_all_parent_stops(self):
        return [
            {
                "stop_id": "D24",
                "stop_name": "Atlantic Av-Barclays Ctr",
                "stop_lat": 40.6844,
                "stop_lon": -73.9775,
            },
            {
                "stop_id": "R31",
                "stop_name": "Union St",
                "stop_lat": 40.6773,
                "stop_lon": -73.9831,
            },
            {
                "stop_id": "R30",
                "stop_name": "DeKalb Av",
                "stop_lat": 40.6906,
                "stop_lon": -73.9818,
            },
        ]


class _NoNearbyGtfs:
    def get_all_parent_stops(self):
        return [
            {
                "stop_id": "far",
                "stop_name": "Far Station",
                "stop_lat": 40.84,
                "stop_lon": -73.94,
            }
        ]


def _ctx(gtfs=None) -> ToolContext:
    return ToolContext(gtfs=_NearbyGtfs() if gtfs is None else gtfs, now_et="2026-08-01T14:00:00-04:00")


class CheckAreaConditionsTests(unittest.IsolatedAsyncioTestCase):
    async def test_citywide_and_borough_inputs_return_a_deterministic_clarification_without_provider_calls(self):
        for area in (
            "New York City",
            "Brooklyn",
            "Brooklyn, NY",
            "all of Brooklyn",
            "the borough of Queens",
        ):
            with self.subTest(area=area):
                resolver = AsyncMock()
                with patch.object(check_area_conditions, "resolve_named_place", new=resolver):
                    result = await check_area_conditions.execute({"area": area}, _ctx())

                self.assertFalse(result.ok)
                self.assertEqual(result.error, check_area_conditions._BROAD_AREA_MESSAGE)
                resolver.assert_not_awaited()
        self.assertNotIn(
            check_area_conditions._area_key("Downtown Brooklyn"),
            check_area_conditions._BROAD_AREA_INPUTS,
        )

    async def test_outside_areas_are_rejected_after_resolution_without_provider_calls(self):
        scan = AsyncMock()
        search = AsyncMock()
        with (
            patch.object(check_area_conditions.trip_incidents, "scan_route_incidents", new=scan),
            patch.object(check_area_conditions.crowd_search, "search_hotspots", new=search),
        ):
            ewr = await check_area_conditions.execute({"area": "EWR"}, _ctx())
        self.assertFalse(ewr.ok)
        self.assertEqual(ewr.error, check_area_conditions._OUTSIDE_AREA_MESSAGE)
        scan.assert_not_awaited()
        search.assert_not_awaited()

        outside = ResolvedPlace("Boston", 42.3601, -71.0589, "geocoder")
        with patch.object(check_area_conditions, "resolve_named_place", new=AsyncMock(return_value=(outside, None))):
            result = await check_area_conditions.execute({"area": "Boston"}, _ctx())
        self.assertFalse(result.ok)
        self.assertEqual(result.error, check_area_conditions._OUTSIDE_AREA_MESSAGE)

    async def test_specific_area_uses_actual_nearby_stops_and_keeps_current_evidence_separate(self):
        place = ResolvedPlace(
            name="Barclays Center",
            latitude=40.6826,
            longitude=-73.9754,
            source="fallback",
        )
        scan = AsyncMock(
            return_value={
                "incidents": [
                    {
                        "location": "Atlantic Avenue",
                        "nearby_station": "Atlantic Av-Barclays Ctr",
                        "severity": "high",
                        "description": "Emergency response affecting an entrance.",
                        "source": "official_web",
                        "candidate_route_ids": ["must-not-leak"],
                    }
                ],
                "scan_metadata": {
                    "status": "complete",
                    "snapshot_status": "fresh",
                    "scanned_at": "2026-08-01T18:01:00Z",
                    "cache_hit": False,
                    "sources": {"completed": ["web_search", "x_search"]},
                },
            }
        )
        search = AsyncMock(
            return_value={
                "status": "complete",
                "cache_hit": False,
                "completed_sources": ["web_search", "x_search"],
                "events": [
                    {
                        "name": "Neighborhood parade",
                        "category": "parade",
                        "venue_name": "Atlantic Avenue",
                        "start_iso": "2026-08-01T19:00:00-04:00",
                        "estimated_end_iso": None,
                        "source_class": "official_web",
                        "verification_tier": "official",
                        "source_ref": "https://example.test/untrusted",
                    }
                ],
            }
        )
        with (
            patch.object(check_area_conditions, "resolve_named_place", new=AsyncMock(return_value=(place, None))),
            patch.object(check_area_conditions.trip_incidents, "scan_route_incidents", new=scan),
            patch.object(check_area_conditions.crowd_search, "search_hotspots", new=search),
        ):
            result = await check_area_conditions.execute(
                {"area": "Barclays Center", "at": "2026-08-01T19:00:00-04:00"},
                _ctx(),
            )

        self.assertTrue(result.ok)
        scan.assert_awaited_once()
        stop_context = scan.await_args.args[0]
        self.assertEqual(
            [stop.stop_name for stop in stop_context],
            ["Atlantic Av-Barclays Ctr", "Union St", "DeKalb Av"],
        )
        self.assertTrue(all(stop.stop_name != "Barclays Center" for stop in stop_context))
        hotspot = search.await_args.args[0][0]
        self.assertEqual(hotspot.station_name, "Atlantic Av-Barclays Ctr")
        self.assertEqual(hotspot.expected_at.isoformat(), "2026-08-01T19:00:00-04:00")
        self.assertTrue(search.await_args.kwargs["allow_live_search"])
        self.assertEqual(result.data["incident_evidence"]["status"], "complete")
        self.assertEqual(result.data["event_evidence"]["status"], "complete")
        self.assertEqual(result.data["incidents"][0]["severity"], "high")
        self.assertEqual(result.data["events"][0]["category"], "parade")
        self.assertNotIn("candidate_route_ids", result.data["incidents"][0])
        self.assertNotIn("source_ref", result.data["events"][0])

    async def test_event_unavailability_is_not_converted_into_an_incident_all_clear(self):
        place = ResolvedPlace("Barclays Center", 40.6826, -73.9754, "fallback")
        scan = AsyncMock(
            return_value={
                "incidents": [],
                "scan_metadata": {"status": "complete", "snapshot_status": "fresh"},
            }
        )
        search = AsyncMock(return_value={"status": "unavailable", "events": [], "completed_sources": []})
        with (
            patch.object(check_area_conditions, "resolve_named_place", new=AsyncMock(return_value=(place, None))),
            patch.object(check_area_conditions.trip_incidents, "scan_route_incidents", new=scan),
            patch.object(check_area_conditions.crowd_search, "search_hotspots", new=search),
        ):
            result = await check_area_conditions.execute({"area": "Barclays Center"}, _ctx())

        self.assertTrue(result.ok)
        self.assertEqual(result.data["incident_evidence"]["status"], "complete")
        self.assertEqual(result.data["event_evidence"]["status"], "unavailable")
        self.assertNotIn("all_clear", result.data["incident_evidence"])

    async def test_no_nearby_transit_stop_leaves_incidents_unavailable_but_still_checks_events(self):
        place = ResolvedPlace("Barclays Center", 40.6826, -73.9754, "fallback")
        scan = AsyncMock()
        search = AsyncMock(
            return_value={
                "status": "complete",
                "events": [],
                "completed_sources": ["web_search", "x_search"],
            }
        )
        with (
            patch.object(check_area_conditions, "resolve_named_place", new=AsyncMock(return_value=(place, None))),
            patch.object(check_area_conditions.trip_incidents, "scan_route_incidents", new=scan),
            patch.object(check_area_conditions.crowd_search, "search_hotspots", new=search),
        ):
            result = await check_area_conditions.execute(
                {"area": "Barclays Center"},
                _ctx(gtfs=_NoNearbyGtfs()),
            )

        scan.assert_not_awaited()
        search.assert_awaited_once()
        self.assertEqual(result.data["incident_evidence"]["status"], "unscanned")
        self.assertEqual(result.data["incident_evidence"]["lookup_status"], "unscanned")
        self.assertEqual(result.data["incident_evidence"]["coverage_status"], "unscanned")
        self.assertEqual(result.data["incident_evidence"]["lookup_kind"], "index")
        self.assertNotIn("all_clear", result.data["incident_evidence"])
        self.assertEqual(result.data["event_evidence"]["status"], "complete")

    async def test_no_nearby_transit_stop_converts_an_event_provider_error_to_unavailable_evidence(self):
        place = ResolvedPlace("Barclays Center", 40.6826, -73.9754, "fallback")
        scan = AsyncMock()
        search = AsyncMock(side_effect=RuntimeError("event provider failed"))
        with (
            patch.object(check_area_conditions, "resolve_named_place", new=AsyncMock(return_value=(place, None))),
            patch.object(check_area_conditions.trip_incidents, "scan_route_incidents", new=scan),
            patch.object(check_area_conditions.crowd_search, "search_hotspots", new=search),
        ):
            result = await check_area_conditions.execute(
                {"area": "Barclays Center"},
                _ctx(gtfs=_NoNearbyGtfs()),
            )

        self.assertTrue(result.ok)
        scan.assert_not_awaited()
        search.assert_awaited_once()
        self.assertEqual(result.data["incident_evidence"]["status"], "unscanned")
        self.assertEqual(result.data["event_evidence"]["status"], "unavailable")

    async def test_truthful_stale_unavailable_and_unscanned_statuses_are_preserved(self):
        place = ResolvedPlace("Barclays Center", 40.6826, -73.9754, "fallback")
        for status in ("stale", "unavailable", "unscanned", "complete", "partial", "failed"):
            with self.subTest(status=status):
                scan = AsyncMock(
                    return_value={
                        "incidents": [],
                        "warnings": [],
                        "scan_metadata": {
                            "status": status,
                            "lookup_status": "complete",
                            "coverage_status": status,
                            "lookup_kind": "index",
                            "requested_coverage_ids": ["lower-manhattan"],
                            "warning_count": 0,
                            "cache_hit": False,
                            "sources": {
                                "attempted": ["incident_index"],
                                "completed": ["incident_index"],
                            },
                        },
                    }
                )
                search = AsyncMock(
                    return_value={"status": "unavailable", "events": [], "completed_sources": []}
                )
                with (
                    patch.object(
                        check_area_conditions,
                        "resolve_named_place",
                        new=AsyncMock(return_value=(place, None)),
                    ),
                    patch.object(
                        check_area_conditions.trip_incidents,
                        "scan_route_incidents",
                        new=scan,
                    ),
                    patch.object(check_area_conditions.crowd_search, "search_hotspots", new=search),
                ):
                    result = await check_area_conditions.execute(
                        {"area": "Barclays Center"}, _ctx()
                    )

                self.assertTrue(result.ok)
                evidence = result.data["incident_evidence"]
                self.assertEqual(evidence["status"], status)
                self.assertEqual(evidence["lookup_status"], "complete")
                self.assertEqual(evidence["coverage_status"], status)
                self.assertEqual(evidence["lookup_kind"], "index")
                self.assertEqual(evidence["requested_coverage_ids"], ["lower-manhattan"])
                self.assertEqual(evidence["warning_count"], 0)
                self.assertEqual(evidence["sources"]["completed"], ["incident_index"])
                self.assertNotIn("all_clear", evidence)

    async def test_confirmed_and_unconfirmed_warnings_remain_distinguishable(self):
        place = ResolvedPlace("Barclays Center", 40.6826, -73.9754, "fallback")
        confirmed = {
            "incident_id": "inc_confirmed",
            "state": "confirmed",
            "corroborated": True,
            "location": "Atlantic Avenue",
            "severity": "high",
            "description": "Emergency response affecting an entrance.",
            "source": "mta_alerts + x_search",
        }
        x_only = {
            "incident_id": "inc_x_only",
            "state": "unconfirmed",
            "corroborated": False,
            "location": "Flatbush Avenue",
            "severity": "medium",
            "description": "Unconfirmed social report.",
            "source": "x_search",
        }
        scan = AsyncMock(
            return_value={
                "incidents": [],
                "warnings": [confirmed, x_only],
                "scan_metadata": {
                    "status": "partial",
                    "lookup_status": "complete",
                    "coverage_status": "partial",
                    "lookup_kind": "index",
                    "requested_coverage_ids": ["downtown-northwest-brooklyn"],
                    "warning_count": 2,
                    "cache_hit": False,
                    "sources": {
                        "attempted": ["incident_index"],
                        "completed": ["incident_index"],
                    },
                },
            }
        )
        search = AsyncMock(
            return_value={"status": "unavailable", "events": [], "completed_sources": []}
        )
        with (
            patch.object(
                check_area_conditions,
                "resolve_named_place",
                new=AsyncMock(return_value=(place, None)),
            ),
            patch.object(check_area_conditions.trip_incidents, "scan_route_incidents", new=scan),
            patch.object(check_area_conditions.crowd_search, "search_hotspots", new=search),
        ):
            result = await check_area_conditions.execute({"area": "Barclays Center"}, _ctx())

        self.assertTrue(result.ok)
        by_location = {row["location"]: row for row in result.data["incidents"]}
        self.assertEqual(set(by_location), {"Atlantic Avenue", "Flatbush Avenue"})
        self.assertEqual(by_location["Atlantic Avenue"]["state"], "confirmed")
        self.assertTrue(by_location["Atlantic Avenue"]["corroborated"])
        self.assertEqual(by_location["Flatbush Avenue"]["state"], "unconfirmed")
        self.assertFalse(by_location["Flatbush Avenue"]["corroborated"])
        self.assertEqual(result.data["incident_evidence"]["warning_count"], 2)
        self.assertNotIn("all_clear", result.data["incident_evidence"])

    async def test_incidents_and_warnings_combine_and_dedupe_by_identity(self):
        place = ResolvedPlace("Barclays Center", 40.6826, -73.9754, "fallback")
        incident = {
            "incident_id": "inc_dup",
            "state": "confirmed",
            "corroborated": True,
            "location": "Atlantic Avenue",
            "severity": "high",
            "description": "Emergency response.",
            "source": "mta_alerts",
        }
        scan = AsyncMock(
            return_value={
                "incidents": [incident],
                "warnings": [dict(incident)],
                "scan_metadata": {
                    "status": "complete",
                    "lookup_status": "complete",
                    "coverage_status": "current",
                    "lookup_kind": "index",
                    "requested_coverage_ids": [],
                    "warning_count": 1,
                    "cache_hit": False,
                    "sources": {
                        "attempted": ["incident_index"],
                        "completed": ["incident_index"],
                    },
                },
            }
        )
        search = AsyncMock(
            return_value={"status": "unavailable", "events": [], "completed_sources": []}
        )
        with (
            patch.object(
                check_area_conditions,
                "resolve_named_place",
                new=AsyncMock(return_value=(place, None)),
            ),
            patch.object(check_area_conditions.trip_incidents, "scan_route_incidents", new=scan),
            patch.object(check_area_conditions.crowd_search, "search_hotspots", new=search),
        ):
            result = await check_area_conditions.execute({"area": "Barclays Center"}, _ctx())

        self.assertTrue(result.ok)
        self.assertEqual(len(result.data["incidents"]), 1)
        self.assertEqual(result.data["incidents"][0]["location"], "Atlantic Avenue")


if __name__ == "__main__":
    unittest.main()
