"""Passenger-facing transit presentation is gated by an evidence handle."""

from __future__ import annotations

import unittest

from app.services.agent.tools.transit import evidence as transit_evidence
from app.services.agent.tools.transit.direction import DirectionResolution
from app.services.agent.events import (
    ArrivalCardEvent,
    TokenEvent,
    TransitStatusActionEvent,
)
from app.services.agent.tools.transit import present_transit
from app.services.agent.tools._types import ToolContext
from app.services import cache


class PresentTransitTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        cache._mem.clear()

    async def test_status_presentation_uses_verified_alert_and_returns_outcome(self) -> None:
        set_id, _payload = transit_evidence.build_evidence_set(
            session_id="s1",
            operation="service_status",
            route_ids=["Q"],
            result={
                "source": "mta_service_alerts",
                "freshness": "live",
                "status": "active_alerts",
                "alerts": [{"alert_id": "a1", "header": "Q delays", "route_ids": ["Q"]}],
            },
        )
        result = await present_transit.execute(
            {"evidence_set_id": set_id, "goal_key": "status"},
            ToolContext(session_id="s1", turn_id="t1"),
        )

        self.assertTrue(result.ok)
        self.assertFalse(result.terminal)
        self.assertEqual(result.data["presentation_outcome"]["goal_key"], "status")
        self.assertIn("Q delays", result.data["passenger_text"])
        self.assertIsInstance(result.events[0], TokenEvent)

    async def test_planned_q_local_change_is_presented_without_disruption_claim(self) -> None:
        for requested_direction in ("uptown", "downtown"):
            with self.subTest(direction=requested_direction):
                set_id, _payload = transit_evidence.build_evidence_set(
                    session_id=f"planned-q-{requested_direction}",
                    operation="service_status",
                    route_ids=["Q"],
                    direction=requested_direction,
                    result={
                        "source": "mta_service_alerts",
                        "freshness": "live",
                        "status": "active_alerts",
                        "gtfs_rt_coverage": "current",
                        "incident_coverage": "current",
                        "alerts": [
                            {
                                "source": "mta_service_alerts",
                                "source_id": "lmm:planned_work:33095",
                                "alert_id": "lmm:planned_work:33095",
                                "header": "Q trains run local",
                                "description": (
                                    "In Manhattan, Q runs local in both directions "
                                    "between 57 St-7 Av and Canal St"
                                ),
                                "route_ids": ["Q"],
                                "direction_ids": ["0", "1"],
                                "direction_scope": "both_directions",
                                "planned_status": "planned",
                                "change_type": "express_to_local",
                                "service_operating": True,
                                "material_disruption": False,
                            }
                        ],
                    },
                )

                result = await present_transit.execute(
                    {"evidence_set_id": set_id, "goal_key": "status"},
                    ToolContext(
                        session_id=f"planned-q-{requested_direction}",
                        turn_id="t1",
                    ),
                )

                self.assertTrue(result.ok, result.error)
                passenger_text = result.data["passenger_text"]
                self.assertIn("Official planned service change", passenger_text)
                self.assertIn("runs local", passenger_text)
                self.assertNotIn("does not confirm the requested direction", passenger_text)
                for false_claim in ("delay", "outage", "disruption", "impact"):
                    self.assertNotIn(false_claim, passenger_text.casefold())

    async def test_unplanned_severe_alert_keeps_conservative_official_alert_wording(self) -> None:
        set_id, _payload = transit_evidence.build_evidence_set(
            session_id="unplanned-severe",
            operation="service_status",
            route_ids=["Q"],
            direction="uptown",
            result={
                "source": "mta_service_alerts",
                "freshness": "live",
                "status": "active_alerts",
                "gtfs_rt_coverage": "current",
                "incident_coverage": "current",
                "alerts": [
                    {
                        "source": "mta_service_alerts",
                        "source_id": "lmm:alert:999",
                        "alert_id": "lmm:alert:999",
                        "header": "Q severe delays",
                        "route_ids": ["Q"],
                        "direction": "uptown",
                        "direction_scope": "direction_specific",
                        "planned_status": "unplanned",
                        "change_type": "severe_delay",
                        "service_operating": "unknown",
                        "material_disruption": True,
                    }
                ],
            },
        )

        result = await present_transit.execute(
            {"evidence_set_id": set_id, "goal_key": "status"},
            ToolContext(session_id="unplanned-severe", turn_id="t1"),
        )

        passenger_text = result.data["passenger_text"]
        self.assertIn("Official alert", passenger_text)
        self.assertIn("Q severe delays", passenger_text)
        self.assertNotIn("Official planned service change", passenger_text)

    async def test_changed_alert_evidence_is_explained_once_without_exposing_ids(self) -> None:
        set_id, _payload = transit_evidence.build_evidence_set(
            session_id="changed-alert",
            operation="service_status",
            route_ids=["Q"],
            direction="uptown",
            result={
                "source": "mta_service_alerts",
                "freshness": "live",
                "status": "active_alerts",
                "decision_evidence_continuity": {
                    "comparable": True,
                    "changed": True,
                },
                "alerts": [
                    {
                        "source": "mta_service_alerts",
                        "source_id": "lmm:alert:new",
                        "alert_id": "lmm:alert:new",
                        "header": "Q service change",
                        "route_ids": ["Q"],
                        "direction": "uptown",
                        "direction_scope": "direction_specific",
                        "planned_status": "unplanned",
                        "change_type": "delay",
                        "material_disruption": True,
                    }
                ],
            },
        )

        result = await present_transit.execute(
            {"evidence_set_id": set_id, "goal_key": "status"},
            ToolContext(session_id="changed-alert", turn_id="t1"),
        )

        self.assertTrue(result.ok, result.error)
        passenger_text = result.data["passenger_text"]
        self.assertIn(
            "Official alert evidence has changed since the route was prepared.",
            passenger_text,
        )
        self.assertNotIn("lmm:alert:new", passenger_text)

    async def test_systemwide_planned_q_change_keeps_distinct_wording(self) -> None:
        set_id, _payload = transit_evidence.build_evidence_set(
            session_id="planned-q-systemwide",
            operation="service_status",
            result={
                "source": "mta_service_alerts",
                "freshness": "live",
                "status": "active_alerts",
                "gtfs_rt_coverage": "current",
                "incident_coverage": "current",
                "alerts": [
                    {
                        "source": "mta_service_alerts",
                        "source_id": "lmm:planned_work:33095",
                        "alert_id": "lmm:planned_work:33095",
                        "header": "Q trains run local",
                        "description": "In Manhattan, Q runs local in both directions",
                        "route_ids": ["Q"],
                        "direction_scope": "both_directions",
                        "planned_status": "planned",
                        "change_type": "express_to_local",
                        "service_operating": True,
                        "material_disruption": False,
                    }
                ],
            },
        )

        result = await present_transit.execute(
            {"evidence_set_id": set_id, "goal_key": "status"},
            ToolContext(session_id="planned-q-systemwide", turn_id="t1"),
        )

        passenger_text = result.data["passenger_text"]
        self.assertIn("Official planned service change", passenger_text)
        self.assertIn("runs local", passenger_text)
        for false_claim in ("delay", "outage", "disruption", "impact"):
            self.assertNotIn(false_claim, passenger_text.casefold())

    async def test_transit_output_contains_canonical_facts_without_follow_up(self) -> None:
        set_id, _payload = transit_evidence.build_evidence_set(
            session_id="s1",
            operation="service_status",
            route_ids=["Q"],
            result={
                "source": "mta_service_alerts",
                "freshness": "live",
                "status": "no_active_alerts",
                "alerts": [],
            },
        )

        result = await present_transit.execute(
            {
                "evidence_set_id": set_id,
                "goal_key": "status",
                "follow_up": "Want arrivals for a specific station?",
            },
            ToolContext(session_id="s1", turn_id="t1"),
        )

        self.assertTrue(result.ok)
        self.assertIn("official alert", result.events[0].text)
        self.assertEqual(result.data["follow_up"], "")
        self.assertEqual(len(result.events), 1)
        self.assertEqual(result.events[0].text, result.data["passenger_text"])

    async def test_invalid_transit_framing_is_rejected_before_presentation(self) -> None:
        result = await present_transit.execute(
            {
                "evidence_set_id": "te_unknown",
                "goal_key": "status",
                "follow_up": "Using evidence_set_id te_secret.",
            },
            ToolContext(session_id="s1", turn_id="t1"),
        )

        self.assertFalse(result.ok)
        self.assertTrue(result.internal_diagnostic)
        self.assertIn("follow_up", result.error or "")

    async def test_systemwide_status_lists_affected_services_without_checked_route_dump(self) -> None:
        set_id, _payload = transit_evidence.build_evidence_set(
            session_id="s1",
            operation="service_status",
            result={
                "source": "mta_service_alerts",
                "freshness": "live",
                "status": "active_alerts",
                "alerts": [
                    {"alert_id": "q1", "header": "Q delays", "route_ids": ["Q"]},
                    {"alert_id": "b1", "header": "B rerouted", "route_ids": ["B"]},
                ],
            },
        )

        result = await present_transit.execute(
            {"evidence_set_id": set_id, "goal_key": "status"},
            ToolContext(session_id="s1", turn_id="t1"),
        )

        self.assertTrue(result.ok)
        text = result.data["passenger_text"]
        self.assertIn("Affected services right now:", text)
        self.assertIn("Q: Q delays", text)
        self.assertIn("B: B rerouted", text)
        self.assertNotIn("the requested route", text)
        self.assertTrue(
            any(
                isinstance(event, TransitStatusActionEvent)
                for event in result.events
            )
        )

    async def test_systemwide_status_keeps_possible_stalled_vehicle_visible(self) -> None:
        set_id, _payload = transit_evidence.build_evidence_set(
            session_id="s1",
            operation="service_status",
            result={
                "source": "mta_service_alerts",
                "freshness": "live",
                "status": "active_alerts",
                "alerts": [{"alert_id": "q1", "header": "Q delays", "route_ids": ["Q"]}],
                "unconfirmed_signals": [
                    {
                        "kind": "possible_stalled_train",
                        "route_id": "Q",
                        "mode": "subway",
                    }
                ],
            },
        )

        result = await present_transit.execute(
            {"evidence_set_id": set_id, "goal_key": "status"},
            ToolContext(session_id="s1", turn_id="t1"),
        )

        text = result.data["passenger_text"]
        self.assertIn("Possible stalled train on Q (not confirmed)", text)
        self.assertTrue(
            any(isinstance(event, TransitStatusActionEvent) for event in result.events)
        )

    async def test_route_specific_status_does_not_offer_systemwide_alerts_action(self) -> None:
        set_id, _payload = transit_evidence.build_evidence_set(
            session_id="s1",
            operation="service_status",
            route_ids=["Q"],
            result={
                "source": "mta_service_alerts",
                "freshness": "live",
                "status": "active_alerts",
                "alerts": [
                    {"alert_id": "q1", "header": "Q delays", "route_ids": ["Q"]}
                ],
            },
        )

        result = await present_transit.execute(
            {"evidence_set_id": set_id, "goal_key": "status"},
            ToolContext(session_id="s1", turn_id="t1"),
        )

        self.assertFalse(
            any(
                isinstance(event, TransitStatusActionEvent)
                for event in result.events
            )
        )

    async def test_unresolved_direction_is_not_presented_as_verified(self) -> None:
        set_id, _payload = transit_evidence.build_evidence_set(
            session_id="s1",
            operation="service_status",
            route_ids=["Q"],
            direction="downtown",
            result={"source": "mta_service_alerts", "freshness": "live", "status": "no_active_alerts", "alerts": []},
        )
        result = await present_transit.execute(
            {"evidence_set_id": set_id, "goal_key": "status"},
            ToolContext(session_id="s1", turn_id="t1"),
        )

        self.assertTrue(result.ok)
        self.assertNotIn("downtown Q", result.data["passenger_text"])
        self.assertIn("can't confirm", result.data["passenger_text"])

    async def test_current_official_no_alerts_explains_unscanned_incidents(self) -> None:
        set_id, _payload = transit_evidence.build_evidence_set(
            session_id="s1",
            operation="service_status",
            route_ids=["Q"],
            direction="downtown",
            direction_resolution=DirectionResolution(
                requested="downtown",
                resolved="downtown",
                authoritative=True,
            ),
            result={
                "source": "mta_service_alerts",
                "freshness": "live",
                "status": "no_active_alerts",
                "alerts": [],
                "incident_coverage": "unscanned",
                "gtfs_rt_coverage": "current",
            },
        )

        result = await present_transit.execute(
            {"evidence_set_id": set_id, "goal_key": "status"},
            ToolContext(session_id="s1", turn_id="t1"),
        )

        text = result.data["passenger_text"]
        self.assertIn("matching official alert", text)
        self.assertIn("stalled train", text)
        self.assertIn("couldn't fully check the recent incident reports", text)
        self.assertNotIn("unscanned", text)
        self.assertNotIn("I can't confirm the current status", text)

    async def test_event_schedule_uses_rider_language_and_model_framing(self) -> None:
        set_id, _payload = transit_evidence.build_evidence_set(
            session_id="s1",
            operation="event_schedule",
            result={
                "events": [
                    {
                        "name": "Jonas Brothers: The Burning Up Tour All Over Again",
                        "venue_name": "Madison Square Garden",
                        "start_iso": "2026-08-20T19:30:00-04:00",
                    },
                    {
                        "name": "Madison Square Garden Tour Experience",
                        "venue_name": "Madison Square Garden",
                        "start_iso": "2026-08-20T12:00:00-04:00",
                    },
                ],
            },
        )

        result = await present_transit.execute(
            {
                "evidence_set_id": set_id,
                "goal_key": "events",
                "lead_in": "There are a couple of things happening nearby.",
                "follow_up": "Want me to recheck the route with those crowds in mind?",
            },
            ToolContext(session_id="s1", turn_id="t1"),
        )

        self.assertTrue(result.ok, result.error)
        visible = "".join(
            event.text for event in result.events if isinstance(event, TokenEvent)
        )
        self.assertTrue(visible.startswith("There are a couple of things happening nearby."))
        self.assertIn("Jonas Brothers", visible)
        self.assertIn("Madison Square Garden", visible)
        self.assertIn("Aug 20 at 7:30 PM", visible)
        self.assertNotIn("Current events:", visible)
        self.assertNotIn("Event:", visible)

    async def test_systemwide_current_official_result_precedes_supplemental_caveat(self) -> None:
        set_id, _payload = transit_evidence.build_evidence_set(
            session_id="s1",
            operation="service_status",
            result={
                "source": "mta_service_alerts",
                "freshness": "live",
                "status": "no_active_alerts",
                "alerts": [],
                "incident_coverage": "unscanned",
                "gtfs_rt_coverage": "current",
            },
        )

        result = await present_transit.execute(
            {"evidence_set_id": set_id, "goal_key": "status"},
            ToolContext(session_id="s1", turn_id="t1"),
        )

        text = result.data["passenger_text"]
        self.assertIn("No affected service was identified", text)
        self.assertIn("couldn't fully check the recent incident reports", text)
        self.assertNotIn("unscanned", text)
        self.assertNotIn("I can't confirm current systemwide", text)

    async def test_unavailable_official_alerts_do_not_claim_no_alerts(self) -> None:
        set_id, _payload = transit_evidence.build_evidence_set(
            session_id="s1",
            operation="service_status",
            route_ids=["Q"],
            direction="downtown",
            direction_resolution=DirectionResolution(
                requested="downtown",
                resolved="downtown",
                authoritative=True,
            ),
            result={
                "source": "mta_service_alerts",
                "freshness": "unavailable",
                "status": "unavailable",
                "alerts": [],
                "incident_coverage": "unavailable",
                "gtfs_rt_coverage": "unavailable",
            },
        )

        result = await present_transit.execute(
            {"evidence_set_id": set_id, "goal_key": "status"},
            ToolContext(session_id="s1", turn_id="t1"),
        )

        text = result.data["passenger_text"]
        self.assertIn("can't confirm", text)
        self.assertIn("official MTA alerts weren't available", text)
        self.assertNotIn("coverage is unavailable", text)
        self.assertNotIn("No matching official alert", text)
        self.assertNotIn("..", text)

    async def test_current_status_keeps_verified_direction_in_wording(self) -> None:
        set_id, _payload = transit_evidence.build_evidence_set(
            session_id="s1",
            operation="service_status",
            route_ids=["Q"],
            direction="downtown",
            direction_resolution=DirectionResolution(
                requested="downtown",
                resolved="downtown",
                authoritative=True,
            ),
            result={
                "source": "mta_service_alerts",
                "freshness": "live",
                "status": "no_active_alerts",
                "alerts": [],
                "incident_coverage": "current",
                "gtfs_rt_coverage": "current",
            },
        )

        result = await present_transit.execute(
            {"evidence_set_id": set_id, "goal_key": "status"},
            ToolContext(session_id="s1", turn_id="t1"),
        )

        text = result.data["passenger_text"]
        self.assertIn("downtown Q", text)
        self.assertNotIn("uptown", text)

    async def test_directionless_alert_does_not_gain_directional_scope(self) -> None:
        set_id, _payload = transit_evidence.build_evidence_set(
            session_id="s1",
            operation="service_status",
            route_ids=["Q"],
            direction="downtown",
            direction_resolution=DirectionResolution(
                requested="downtown",
                resolved="downtown",
                authoritative=True,
            ),
            result={
                "source": "mta_service_alerts",
                "freshness": "live",
                "status": "active_alerts",
                "alerts": [{
                    "alert_id": "q1",
                    "header": "Q delays.",
                    "route_ids": ["Q"],
                }],
            },
        )

        result = await present_transit.execute(
            {"evidence_set_id": set_id, "goal_key": "status"},
            ToolContext(session_id="s1", turn_id="t1"),
        )

        text = result.data["passenger_text"]
        self.assertNotIn("..", text)
        self.assertIn("Q delays", text)
        self.assertIn("does not confirm the requested direction", text)

    async def test_status_presentation_names_confirmed_incident_evidence(self) -> None:
        set_id, _payload = transit_evidence.build_evidence_set(
            session_id="s1",
            operation="service_status",
            route_ids=["Q"],
            direction="downtown",
            direction_resolution=DirectionResolution(
                requested="downtown",
                resolved="downtown",
                authoritative=True,
            ),
            result={
                "source": "mta_service_alerts",
                "freshness": "live",
                "status": "no_active_alerts",
                "alerts": [],
                "incidents": [
                    {
                        "incident_id": "inc-1",
                        "state": "confirmed",
                        "location_name": "Near Newkirk Plaza",
                        "affected_route_ids": ["Q"],
                        "direction": "downtown",
                    }
                ],
                "incident_coverage": "current",
                "gtfs_rt_coverage": "current",
            },
        )

        result = await present_transit.execute(
            {"evidence_set_id": set_id, "goal_key": "status"},
            ToolContext(session_id="s1", turn_id="t1"),
        )

        self.assertIn("Confirmed incident", result.data["passenger_text"])
        self.assertIn("Near Newkirk Plaza", result.data["passenger_text"])

    async def test_status_presentation_labels_possible_stalled_vehicle_as_unconfirmed(self) -> None:
        set_id, _payload = transit_evidence.build_evidence_set(
            session_id="s1",
            operation="service_status",
            route_ids=["Q"],
            direction="downtown",
            direction_resolution=DirectionResolution(
                requested="downtown",
                resolved="downtown",
                authoritative=True,
            ),
            result={
                "source": "mta_service_alerts",
                "freshness": "live",
                "status": "no_active_alerts",
                "alerts": [],
                "unconfirmed_signals": [
                    {
                        "kind": "possible_stalled_train",
                        "route_id": "Q",
                        "mode": "subway",
                        "direction": "downtown",
                    }
                ],
                "incident_coverage": "current",
                "gtfs_rt_coverage": "current",
            },
        )

        result = await present_transit.execute(
            {"evidence_set_id": set_id, "goal_key": "status"},
            ToolContext(session_id="s1", turn_id="t1"),
        )

        text = result.data["passenger_text"]
        self.assertIn("possible stalled train", text)
        self.assertIn("isn't confirmed", text)

    async def test_complete_status_check_does_not_reduce_result_to_alerts_only(self) -> None:
        set_id, _payload = transit_evidence.build_evidence_set(
            session_id="s1",
            operation="service_status",
            route_ids=["Q"],
            direction="downtown",
            direction_resolution=DirectionResolution(
                requested="downtown",
                resolved="downtown",
                authoritative=True,
            ),
            result={
                "source": "mta_service_alerts",
                "freshness": "live",
                "status": "no_active_alerts",
                "alerts": [],
                "incident_coverage": "current",
                "gtfs_rt_coverage": "current",
            },
        )

        result = await present_transit.execute(
            {"evidence_set_id": set_id, "goal_key": "status"},
            ToolContext(session_id="s1", turn_id="t1"),
        )

        text = result.data["passenger_text"]
        self.assertIn("official alert", text)
        self.assertIn("confirmed incident", text)
        self.assertIn("stalled train", text)

    async def test_arrival_presentation_emits_canonical_arrival_event(self) -> None:
        set_id, _payload = transit_evidence.build_evidence_set(
            session_id="s1",
            operation="arrivals",
            route_ids=["Q"],
            result={
                "route_id": "Q",
                "stop": {"id": "D28", "name": "Newkirk Plaza"},
                "source_status": "live",
                "directions": [{"id": "downtown", "label": "Downtown / Brooklyn-bound", "arrivals": [{"expected_at": "2026-08-15T12:05:00+00:00", "minutes": 5, "realtime": True}]}],
            },
        )
        result = await present_transit.execute(
            {"evidence_set_id": set_id, "goal_key": "arrivals"},
            ToolContext(session_id="s1", turn_id="t1"),
        )

        self.assertTrue(result.ok)
        self.assertTrue(any(isinstance(event, ArrivalCardEvent) for event in result.events))
        self.assertEqual(result.data["presentation_outcome"]["operation"], "arrivals")

    async def test_no_arrivals_stays_prose_only_instead_of_emitting_stub_card(self) -> None:
        set_id, _payload = transit_evidence.build_evidence_set(
            session_id="s1",
            operation="arrivals",
            route_ids=["Q"],
            result={
                "route_id": "Q",
                "stop": {"id": "D28", "name": "Newkirk Plaza"},
                "source_status": "no_predictions",
                "directions": [],
            },
        )

        result = await present_transit.execute(
            {"evidence_set_id": set_id, "goal_key": "arrivals"},
            ToolContext(session_id="s1", turn_id="t1"),
        )

        self.assertTrue(result.ok)
        self.assertIn("No upcoming arrivals", result.data["passenger_text"])
        self.assertFalse(any(isinstance(event, ArrivalCardEvent) for event in result.events))
        self.assertTrue(any(isinstance(event, TokenEvent) for event in result.events))

    async def test_stale_arrivals_without_predictions_stay_prose_only(self) -> None:
        set_id, _payload = transit_evidence.build_evidence_set(
            session_id="s1",
            operation="arrivals",
            route_ids=["Q"],
            result={
                "route_id": "Q",
                "stop": {"id": "D28", "name": "Newkirk Plaza"},
                "source_status": "stale",
                "directions": [],
            },
        )

        result = await present_transit.execute(
            {"evidence_set_id": set_id, "goal_key": "arrivals"},
            ToolContext(session_id="s1", turn_id="t1"),
        )

        self.assertTrue(result.ok)
        self.assertIn("out of date", result.data["passenger_text"])
        self.assertFalse(any(isinstance(event, ArrivalCardEvent) for event in result.events))
        self.assertTrue(any(isinstance(event, TokenEvent) for event in result.events))

    async def test_downtown_presentation_cannot_describe_uptown_as_catchable(self) -> None:
        set_id, _payload = transit_evidence.build_evidence_set(
            session_id="s1",
            operation="arrivals",
            route_ids=["Q"],
            direction="downtown",
            result={
                "route_id": "Q",
                "stop": {"id": "D28", "name": "Newkirk Plaza"},
                "source_status": "live",
                "directions": [
                    {
                        "id": "uptown",
                        "label": "Uptown / Manhattan-bound",
                        "arrivals": [{"minutes": 3}],
                    },
                    {
                        "id": "downtown",
                        "label": "Downtown / Brooklyn-bound",
                        "arrivals": [{"minutes": 9}],
                    },
                ],
                "catchability": {
                    "walking_minutes": 2,
                    "boarding_buffer_minutes": 2,
                    "arrival_minutes": [3, 9],
                    "catchable_arrival_minutes": 3,
                    "confidence": 0.9,
                },
            },
        )

        result = await present_transit.execute(
            {"evidence_set_id": set_id, "goal_key": "arrivals"},
            ToolContext(session_id="s1", turn_id="t1"),
        )

        event = next(event for event in result.events if isinstance(event, ArrivalCardEvent))
        self.assertEqual([group["id"] for group in event.directions], ["downtown"])
        self.assertEqual(event.catchability["catchable_arrival_minutes"], 9)
        self.assertNotIn("uptown", repr(event.to_data()))


if __name__ == "__main__":
    unittest.main()
