"""Regression tests for canonical itinerary/evidence binding at agent seams."""

from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, patch

from app.services import cache
from app.services.agent.tools._types import ToolContext, ToolResult
from app.services.agent.tools.transit import check_transit, present_transit
from app.services.agent.tools.transit import evidence as transit_evidence
from app.services.agent.turn.contract import GoalKind, OutcomeGoal, TurnContract
from app.services.agent.turn.evidence import TurnEvidence

from tests.agent_evidence_binding_test_support import transit_input


def _airtrain_session() -> dict[str, object]:
    """One accepted trip whose AirTrain leg terminates at Lefferts Blvd."""

    itinerary = {
        "legs": [
            {
                "mode": "AIRTRAIN",
                "service_id": "AIRTRAIN",
                "board": "JFK Terminal 4",
                "alight": "Ozone Park-Lefferts Blvd",
            }
        ]
    }
    # Keep both names while the accepted-trip/session adapters converge on
    # the canonical itinerary field; neither contains Howard Beach.
    return {
        "active_trip": {
            "canonical_itinerary": itinerary,
            "itinerary": itinerary,
        }
    }


def _bus_stop_session(route_id: str) -> dict[str, object]:
    """One accepted bus itinerary whose entities are explicitly bus stops."""

    itinerary = {
        "legs": [
            {
                "mode": "BUS",
                "service_id": route_id,
                "board": "Church Av",
                "alight": "Nostrand Av",
                "stops": [
                    {
                        "id": f"{route_id}-church",
                        "name": "Church Av",
                        "entity_type": "BUS_STOP",
                    },
                    {
                        "id": f"{route_id}-nostrand",
                        "name": "Nostrand Av",
                        "entity_type": "BUS_STOP",
                    },
                ],
            }
        ]
    }
    return {
        "active_trip": {
            "canonical_itinerary": itinerary,
            "itinerary": itinerary,
        }
    }


def _mixed_route_session() -> dict[str, object]:
    itinerary = {
        "legs": [
            {
                "mode": "SUBWAY",
                "service_id": "Q",
                "board": "Newkirk Plaza",
                "alight": "Prospect Park",
                "board_entity_type": "SUBWAY_STATION",
                "alight_entity_type": "SUBWAY_STATION",
            },
            {
                "mode": "BUS",
                "service_id": "B35",
                "board": "Church Av",
                "alight": "Nostrand Av",
                "board_entity_type": "BUS_STOP",
                "alight_entity_type": "BUS_STOP",
            },
        ]
    }
    return {
        "active_trip": {
            "lines": ["Q", "B35"],
            "canonical_itinerary": itinerary,
        }
    }


class AgentEvidenceBindingReliabilityTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        cache._mem.clear()

    async def test_accessibility_evidence_must_match_selected_airtrain_itinerary(self):
        """Howard Beach facts cannot ground a Lefferts AirTrain itinerary."""

        provider_result = ToolResult(
            ok=True,
            data={
                "station_matched": "Howard Beach",
                "elevator_outages": [{"equipment": "E-Howard-1"}],
                "escalator_outages_count": 0,
            },
        )
        context = ToolContext(
            session_id="evidence-binding-session",
            session=_airtrain_session(),
        )

        with patch.object(
            check_transit.accessibility_status,
            "execute",
            new=AsyncMock(return_value=provider_result),
        ):
            result = await check_transit.execute(
                transit_input(
                    station="Howard Beach",
                    route_ids=["AIRTRAIN"],
                    station_source="accepted_trip",
                ),
                context,
            )

        assert not result.ok, "a station lookup outside the selected itinerary must be rejected"
        assert "evidence_set_id" not in (result.data or {})
        assert "Howard Beach" not in repr(result.data)

    async def test_accessibility_evidence_binds_selected_airtrain_station(self):
        provider_result = ToolResult(
            ok=True,
            data={
                "station_matched": "Ozone Park-Lefferts Blvd",
                "elevator_outages": [{"equipment": "E-Lefferts-1"}],
                "escalator_outages_count": 0,
            },
        )
        context = ToolContext(
            session_id="lefferts-evidence-session",
            session=_airtrain_session(),
        )

        with patch.object(
            check_transit.accessibility_status,
            "execute",
            new=AsyncMock(return_value=provider_result),
        ):
            result = await check_transit.execute(
                transit_input(
                    station="Ozone Park-Lefferts Blvd",
                    route_ids=["AIRTRAIN"],
                    station_source="accepted_trip",
                ),
                context,
            )

        assert result.ok
        evidence = (result.data or {}).get("evidence") or {}
        binding = evidence.get("accessibility", {}).get("binding", {})
        assert binding.get("station") == "Ozone Park-Lefferts Blvd"
        assert binding.get("entity_type") == "AIRTRAIN_STATION"
        assert binding.get("route_ids") == ["AIRTRAIN"]

        turn_evidence = TurnEvidence()
        turn_evidence.bind_contract(
            TurnContract((OutcomeGoal("accessibility", GoalKind.ACCESSIBILITY),))
        )
        turn_evidence.record_goal_handle(
            "accessibility", result.data["evidence_set_id"]
        )
        presented = await present_transit.execute(
            {
                "evidence_set_id": result.data["evidence_set_id"],
                "goal_key": "accessibility",
                "lead_in": "",
                "follow_up": "",
            },
            ToolContext(
                session_id="lefferts-evidence-session",
                turn_evidence=turn_evidence,
                telemetry={},
            ),
        )
        passenger_text = str((presented.data or {}).get("passenger_text") or "")
        assert "Ozone Park-Lefferts Blvd" in passenger_text
        assert "Howard Beach" not in passenger_text

    async def test_current_turn_station_does_not_use_stale_trip_binding(self):
        provider_result = ToolResult(
            ok=True,
            data={
                "station_matched": "Times Sq-42 St",
                "elevator_outages": [],
                "escalator_outages_count": 0,
            },
        )
        context = ToolContext(
            session_id="new-station-evidence-session",
            session=_airtrain_session(),
            rider_message="Can you check accessibility at Times Sq-42 St?",
        )

        with patch.object(
            check_transit.accessibility_status,
            "execute",
            new=AsyncMock(return_value=provider_result),
        ):
            result = await check_transit.execute(
                transit_input(
                    station="Times Sq-42 St",
                    route_ids=["7"],
                ),
                context,
            )

        assert result.ok
        assert "binding" not in ((result.data or {}).get("evidence") or {}).get("accessibility", {})

    async def test_subway_elevator_evidence_cannot_attach_to_bus_stop_routes(self):
        """B35/B15 are bus routes, not station entities with elevator facts."""

        for route_id in ("B35", "B15"):
            with self.subTest(route_id=route_id):
                provider_result = ToolResult(
                    ok=True,
                    data={
                        "station_matched": route_id,
                        "elevator_outages": [{"equipment": f"E-{route_id}"}],
                        "escalator_outages_count": 0,
                    },
                )
                context = ToolContext(
                    session_id=f"{route_id}-evidence-session",
                    session=_bus_stop_session(route_id),
                )

                with patch.object(
                    check_transit.accessibility_status,
                    "execute",
                    new=AsyncMock(return_value=provider_result),
                ):
                    result = await check_transit.execute(
                        transit_input(
                            station=route_id,
                            route_ids=[route_id],
                            station_source="accepted_trip",
                        ),
                        context,
                    )

                assert not result.ok, f"unsupported bus-stop accessibility must be rejected for {route_id}"
                assert "evidence_set_id" not in (result.data or {})
                assert "elevator_outages" not in repr(result.data)

    async def test_accessibility_route_scope_cannot_cross_mixed_route_legs(self):
        session = _mixed_route_session()
        bound, bind_error = transit_evidence.bind_accessibility_target(
            "Prospect Park",
            session,
            ["Q"],
        )
        assert bind_error is None
        assert bound["route_ids"] == ["Q"]

        context = ToolContext(
            session_id="mixed-route-evidence-session",
            session=session,
        )
        result = await check_transit.execute(
            transit_input(
                station="Prospect Park",
                route_ids=["B35"],
                station_source="accepted_trip",
            ),
            context,
        )

        assert not result.ok
        assert result.outcome == "unavailable"
        assert "accepted itinerary" in str(result.error)

    async def test_bus_stop_accessibility_presentation_uses_bus_stop_language(self):
        evidence_set_id, _evidence = transit_evidence.build_evidence_set(
            session_id="bus-presentation-session",
            operation="accessibility",
            route_ids=["B35"],
            result={
                "station_matched": "Church Av",
                "elevator_outages": [{"equipment": "E-B35"}],
                "binding": {
                    "bound": True,
                    "route_ids": ["B35"],
                    "mode": "bus",
                    "station": "Church Av",
                    "entity_type": "BUS_STOP",
                },
            },
        )
        turn_evidence = TurnEvidence()
        turn_evidence.bind_contract(
            TurnContract((OutcomeGoal("accessibility", GoalKind.ACCESSIBILITY),))
        )
        turn_evidence.record_goal_handle("accessibility", evidence_set_id)
        result = await present_transit.execute(
            {
                "evidence_set_id": evidence_set_id,
                "goal_key": "accessibility",
                "lead_in": "",
                "follow_up": "",
            },
            ToolContext(
                session_id="bus-presentation-session",
                turn_evidence=turn_evidence,
                telemetry={},
            ),
        )

        passenger_text = str((result.data or {}).get("passenger_text") or "")
        assert "station" not in passenger_text.casefold()
        assert "elevator" not in passenger_text.casefold()


if __name__ == "__main__":
    unittest.main()
