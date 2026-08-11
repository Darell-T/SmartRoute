from __future__ import annotations

import unittest

from app.services.agent.tools._types import ToolResult
from app.services.trips import event_crowd, scoring


def _route(
    *,
    stop_name: str = "34 St-Penn Station",
    latitude: float = 40.7505,
    longitude: float = -73.9934,
    expected_at: str = "2026-07-25T23:15:00+00:00",
    total_minutes: int = 30,
) -> list[dict]:
    return [
        {
            "type": "SUBWAY",
            "route_id": "A",
            "departure_stop": stop_name,
            "arrival_stop": "Jay St-MetroTech",
            "departure_coords": {"latitude": latitude, "longitude": longitude},
            "arrival_coords": {"latitude": 40.6923, "longitude": -73.9873},
            "departure_time_iso": expected_at,
            "arrival_time_iso": "2026-07-25T23:40:00+00:00",
            "route_total_minutes": total_minutes,
            "stop_count": 6,
        }
    ]


def _event(
    *,
    event_id: str = "evt-msg",
    latitude: float = 40.7505,
    longitude: float = -73.9934,
    start: str = "2026-07-26T00:00:00Z",
    end: str = "2026-07-26T03:00:00Z",
) -> dict:
    return {
        "event_id": event_id,
        "name": "Concert at the Garden",
        "venue_name": "Madison Square Garden",
        "venue_latitude": latitude,
        "venue_longitude": longitude,
        "start_iso": start,
        "estimated_end_iso": end,
        "start_time_status": "confirmed",
    }


class EventCrowdAssociationTests(unittest.TestCase):
    def test_relevant_ingress_event_is_associated_with_candidate(self):
        impacts = event_crowd.associate_events(
            [_route()],
            [_event()],
            fallback_time=event_crowd._parse_time("2026-07-25T23:15:00Z"),
        )
        self.assertEqual(len(impacts), 1)
        self.assertEqual(impacts[0]["route_index"], 0)
        self.assertEqual(impacts[0]["exposure_window"], "ingress")
        self.assertEqual(impacts[0]["impact_scope"], "station_crowding")
        self.assertEqual(impacts[0]["lines"], ["A"])
        self.assertEqual(impacts[0]["category"], "other")
        self.assertEqual(impacts[0]["freshness_status"], "current")
        self.assertEqual(impacts[0]["source_ref"], "structured:evt-msg")

    def test_distant_event_does_not_affect_route(self):
        impacts = event_crowd.associate_events(
            [_route()],
            [_event(latitude=40.8296, longitude=-73.9262)],
            fallback_time=event_crowd._parse_time("2026-07-25T23:15:00Z"),
        )
        self.assertEqual(impacts, [])

    def test_event_outside_travel_window_does_not_affect_route(self):
        impacts = event_crowd.associate_events(
            [_route()],
            [_event(start="2026-07-27T00:00:00Z", end="2026-07-27T03:00:00Z")],
            fallback_time=event_crowd._parse_time("2026-07-25T23:15:00Z"),
        )
        self.assertEqual(impacts, [])

    def test_ingress_and_egress_are_distinct(self):
        ingress = event_crowd.associate_events(
            [_route(expected_at="2026-07-25T23:15:00Z")],
            [_event()],
            fallback_time=event_crowd._parse_time("2026-07-25T23:15:00Z"),
        )
        egress = event_crowd.associate_events(
            [_route(expected_at="2026-07-26T02:45:00Z")],
            [_event()],
            fallback_time=event_crowd._parse_time("2026-07-26T02:45:00Z"),
        )
        self.assertEqual(ingress[0]["exposure_window"], "ingress")
        self.assertEqual(egress[0]["exposure_window"], "egress")

    def test_duplicate_provider_events_merge_per_candidate(self):
        impacts = event_crowd.associate_events(
            [_route()],
            [_event(), _event()],
            fallback_time=event_crowd._parse_time("2026-07-25T23:15:00Z"),
        )
        self.assertEqual(len(impacts), 1)

    def test_event_penalty_reaches_deterministic_route_score(self):
        routes = [_route(total_minutes=30), _route(stop_name="Canal St", latitude=40.718, longitude=-74.0, total_minutes=34)]
        impacts = event_crowd.associate_events(
            routes,
            [_event()],
            fallback_time=event_crowd._parse_time("2026-07-25T23:15:00Z"),
        )
        scored = scoring._score_routes(routes, [], ticketmaster_event_impacts=impacts)
        score_by_index = scoring._score_by_index(scored)
        self.assertGreater(score_by_index[0]["event_crowd_penalty"], 0)
        self.assertEqual(score_by_index[1]["event_crowd_penalty"], 0)
        self.assertGreater(score_by_index[0]["score"], score_by_index[1]["score"])

    def test_official_x_penalty_is_reduced_and_capped(self):
        event = {
            **_event(),
            "source_class": "official_x",
            "verification_tier": "official",
            "scoring_authorized": True,
            "confidence": 0.75,
        }

        impacts = event_crowd.associate_events(
            [_route()],
            [event],
            fallback_time=event_crowd._parse_time("2026-07-25T23:15:00Z"),
        )

        self.assertEqual(impacts[0]["risk_score"], 5.0)
        self.assertEqual(impacts[0]["source_class"], "official_x")

    def test_independent_x_evidence_cannot_change_score(self):
        event = {
            **_event(),
            "source_class": "independent_x",
            "verification_tier": "corroborative",
            "scoring_authorized": False,
            "confidence": 0.4,
        }

        impacts = event_crowd.associate_events(
            [_route()],
            [event],
            fallback_time=event_crowd._parse_time("2026-07-25T23:15:00Z"),
        )

        self.assertEqual(impacts[0]["risk_score"], 0)
        self.assertEqual(event_crowd.route_event_penalty(0, impacts), 0)


class EventCrowdCollectionTests(unittest.IsolatedAsyncioTestCase):
    async def test_provider_failure_and_no_relevant_events_are_distinct(self):
        async def unavailable(_tool_input, _ctx):
            return ToolResult(ok=False, error="event lookup timed out")

        async def empty(_tool_input, _ctx):
            return ToolResult(ok=True, data={"events": []}, summary="no events")

        class Ctx:
            now_et = "2026-07-25T19:15:00-04:00"

        failed = await event_crowd.collect_route_event_evidence(
            [_route()],
            Ctx(),
            lookup=unavailable,
        )
        clear = await event_crowd.collect_route_event_evidence(
            [_route()],
            Ctx(),
            lookup=empty,
        )
        self.assertEqual(failed[0], "provider_unavailable")
        self.assertEqual(clear[0], "no_relevant_events")

    async def test_collection_deduplicates_events_from_concurrent_hub_queries(self):
        async def duplicate(_tool_input, _ctx):
            return ToolResult(ok=True, data={"events": [_event()]}, summary="one event")

        class Ctx:
            now_et = "2026-07-25T19:15:00-04:00"

        status, impacts, failures = await event_crowd.collect_route_event_evidence(
            [_route()],
            Ctx(),
            lookup=duplicate,
        )
        self.assertEqual(status, "available")
        self.assertEqual(len(impacts), 1)
        self.assertEqual(failures, [])


class RouteScoreFormulaRegressionTests(unittest.TestCase):
    """Single-leg score components are unchanged after helper extraction."""

    def test_route_score_matches_shared_component_formula(self):
        route = _route(total_minutes=30)
        alerts = [{"header": "A disruption", "route_ids": ["A"]}]
        impacts = [
            {"event_id": "ev-1", "title": "Game", "route_index": 0, "risk_score": 5.0}
        ]
        scored = scoring._route_score(
            route,
            alerts,
            route_index=0,
            ticketmaster_event_impacts=impacts,
            routing_preference="LESS_WALKING",
            preferred_modes=["BUS"],
        )
        self.assertEqual(scored["total_minutes"], 30)
        self.assertEqual(scored["transfers"], 0)
        self.assertEqual(scored["alert_count"], 1)
        self.assertEqual(scored["event_crowd_penalty"], 5.0)
        self.assertEqual(scored["walking_penalty"], 0)
        self.assertEqual(scored["preferred_mode_penalty"], 4)
        self.assertEqual(
            scored["score"],
            scoring._component_score_total(
                total_minutes=scored["total_minutes"],
                transfers=scored["transfers"],
                alert_count=scored["alert_count"],
                event_crowd_penalty=scored["event_crowd_penalty"],
                walking_penalty=scored["walking_penalty"],
                preferred_mode_penalty=scored["preferred_mode_penalty"],
            ),
        )
        self.assertEqual(scored["score"], 30 + 0 + 8 + 5.0 + 0 + 4)

    def test_transfer_and_street_walk_components_unchanged(self):
        route = [
            {"type": "SUBWAY", "route_id": "A", "departure_stop": "a", "arrival_stop": "b"},
            {"type": "WALK", "duration_seconds": 120},
            {"type": "SUBWAY", "route_id": "B", "departure_stop": "b", "arrival_stop": "c"},
        ]
        scored = scoring._route_score(route, [], routing_preference="LESS_WALKING")
        self.assertEqual(scored["transfers"], 1)
        self.assertEqual(scored["street_walking_seconds"], 120)
        self.assertEqual(scored["walking_penalty"], 4)
        self.assertEqual(scored["event_crowd_penalty"], 0)
        self.assertEqual(
            scored["score"],
            scoring._component_score_total(
                total_minutes=scored["total_minutes"],
                transfers=scored["transfers"],
                alert_count=scored["alert_count"],
                event_crowd_penalty=scored["event_crowd_penalty"],
                walking_penalty=scored["walking_penalty"],
                preferred_mode_penalty=scored["preferred_mode_penalty"],
            ),
        )


if __name__ == "__main__":
    unittest.main()
