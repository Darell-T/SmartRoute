"""Fresh live Sonnet certification for the SmartRoute reliability plan.

Only external place, route, and transit providers are deterministic. The
production Sonnet prompt, goal ledger, capability loop, validation, state,
selection, presenters, and terminal policy all remain live.
"""

from __future__ import annotations

import re
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.services.agent import loop, session as session_module
from app.services.agent.tools.transit import check_transit
from app.services.agent.tools.places import discover_places
from app.services.agent.tools.location_resolution import ResolvedPlace
from app.services.agent.tools._types import ToolContext, ToolResult
from app.services.agent.tools.route.route_projection import first_boarding_context
from app.services.trips.itinerary import build_canonical_itinerary
from app.services.mta.static_gtfs.stop_patterns import StopPatternIndex
from tests.agent_route_decision_test_support import _prepared_leg, _route
from tests.anthropic_live_fixtures import _prepared_route
from tests.anthropic_live_support import (
    AnthropicLiveAgentContractMixin,
    LIVE_ENABLED,
    _passenger_text,
    _safe_trace_diagnostics,
    _tool_names,
)


def _provider_place(
    name: str,
    *,
    address: str,
    latitude: float,
    longitude: float,
    provider_id: str,
) -> dict:
    return {
        "name": name,
        "address": address,
        "lat": latitude,
        "lng": longitude,
        "open_now": True,
        "rating": 4.7,
        "review_count": 800,
        "place_id": provider_id,
        "address_components": [
            {"longText": "Brooklyn", "types": ["sublocality_level_1"]}
        ],
    }


def _kyuramen_branches() -> ToolResult:
    return ToolResult(
        ok=True,
        data={
            "results": [
                _provider_place(
                    "KYURAMEN Forest Hills",
                    address="108-50 Queens Boulevard, Queens, NY",
                    latitude=40.7218,
                    longitude=-73.8438,
                    provider_id="provider-forest-hills",
                ),
                _provider_place(
                    "KYURAMEN Prospect Heights",
                    address="700 Atlantic Avenue, Brooklyn, NY",
                    latitude=40.6837,
                    longitude=-73.9760,
                    provider_id="provider-prospect-heights",
                ),
                _provider_place(
                    "KYURAMEN Park Slope",
                    address="315 Fifth Avenue, Brooklyn, NY",
                    latitude=40.6720,
                    longitude=-73.9833,
                    provider_id="provider-park-slope",
                ),
            ]
        },
        summary="3 verified KYURAMEN locations",
    )


async def _branch_route(*args, **kwargs):
    destination = kwargs.get("resolved_destination")
    if destination is None:
        raise AssertionError("branch comparison must use a verified destination")
    name = destination.name.casefold()
    if "forest hills" in name:
        route = _route(
            route_ids=("B", "F"),
            walking_seconds=60,
            total_seconds=7200,
        )
    elif "prospect heights" in name:
        route = _route(
            route_ids=("B",),
            walking_seconds=600,
            total_seconds=1800,
        )
    else:
        route = _route(
            route_ids=("B",),
            walking_seconds=120,
            total_seconds=2100,
        )
    return _prepared_leg(
        destination=destination.name,
        destination_place=destination,
        routes=[route],
    )


async def _jfk_routes(*args, **kwargs):
    destination = ResolvedPlace(
        "John F. Kennedy International Airport",
        40.6413,
        -73.7781,
        "fixture",
    )
    return _prepared_leg(
        destination=destination.name,
        destination_place=destination,
        routes=[
            _route(
                route_ids=("A", "AirTrain"),
                walking_seconds=300,
                total_seconds=5400,
            ),
            _route(
                route_ids=("B", "F", "E", "AirTrain"),
                walking_seconds=60,
                total_seconds=4200,
            ),
        ],
    )


async def _msg_routes(*args, **kwargs):
    destination = ResolvedPlace(
        "Madison Square Garden",
        40.7505,
        -73.9934,
        "fixture",
    )
    return _prepared_leg(
        destination=destination.name,
        destination_place=destination,
        routes=[
            _route(
                route_ids=("Q",),
                walking_seconds=360,
                total_seconds=2400,
            ),
            _route(
                route_ids=("B", "M"),
                walking_seconds=240,
                total_seconds=2700,
            ),
        ],
        event_evidence_status="partial",
    )


def _b_status() -> ToolResult:
    return ToolResult(
        ok=True,
        data={
            "source": "mta_service_alerts",
            "freshness": "live",
            "status": "active_alerts",
            "alerts": [
                {
                    "alert_id": "b-live-alert",
                    "header": "Uptown B trains are running with delays",
                    "route_ids": ["B"],
                    "direction": "uptown",
                }
            ],
            "gtfs_rt_coverage": "current",
            "incident_coverage": "current",
        },
        summary="Checked B service",
    )


def _present_input(trace, capability: str) -> dict:
    return next(
        tool_input
        for name, tool_input in trace.tool_calls
        if name == capability
    )


class AnthropicPlanLiveFixtureContractTests(unittest.IsolatedAsyncioTestCase):
    def test_safe_live_diagnostics_are_strictly_structural(self) -> None:
        trace = loop.TurnTrace(
            tool_calls=[("present_route", {"candidate_id": "candidate-1"})],
            model_call_count=3,
            terminal_resolution={
                "selection_source": "model",
                "resolution": "completed",
            },
            telemetry={
                "trace_id": "1" * 32,
                "goal_states": {"route": {"evidence_handle": "set-1"}},
                "route_candidate_diagnostics": {
                    "final_structurally_unique_candidate_count": 2
                },
                "route_decision_corrections": {"route:set-1": 1},
                "raw_prompt": "must not be projected",
            },
        )

        self.assertEqual(
            _safe_trace_diagnostics(trace),
            {
                "trace_id": "1" * 32,
                "model_request_count": 3,
                "candidate_ids": ["candidate-1"],
                "candidate_family_count": 2,
                "evidence_handles": ["set-1"],
                "selection_source": "model",
                "correction_count": 1,
                "completion_result": "completed",
            },
        )

    async def test_kyuramen_fixture_intercepts_the_active_provider_boundary(self) -> None:
        provider = AsyncMock(return_value=_kyuramen_branches())
        ctx = ToolContext(
            session={},
            session_id="live-fixture-contract",
            turn_id="live-fixture-contract-turn",
            now_et="2026-08-24T12:00:00-04:00",
            origin={"lat": 40.65, "lng": -73.95},
            agent_mode="auto",
            rider_message="Find me a Kyuramen with the least amount of walking.",
        )

        with patch.object(
            discover_places.search_local_places,
            "_provider_search",
            new=provider,
        ):
            result = await discover_places.execute(
                {
                    "operation": "search",
                    "query": "Kyuramen",
                    "scope": {"kind": "current_location", "values": []},
                    "open_now": True,
                    "max_results": 8,
                    "candidate_names": [],
                    "exclude_presented": False,
                },
                ctx,
            )

        self.assertTrue(result.ok, result.error)
        self.assertGreaterEqual(len(result.data.get("places") or []), 2)
        provider.assert_awaited_once()


@unittest.skipUnless(
    LIVE_ENABLED,
    "set RUN_ANTHROPIC_TOOL_CONTRACT=1 to run live provider checks",
)
class AnthropicPlanReliabilityLiveTests(
    AnthropicLiveAgentContractMixin,
    unittest.IsolatedAsyncioTestCase,
):
    async def test_nearby_multi_branch_choice_stays_model_primary(self) -> None:
        rider = "Find me a Kyuramen with the least amount of walking."
        with (
            patch.object(
                discover_places.search_local_places,
                "_provider_search",
                new=AsyncMock(return_value=_kyuramen_branches()),
            ),
            patch(
                "app.services.agent.tools.route.prepare_route_branches.prepare_single_leg",
                new=AsyncMock(side_effect=_branch_route),
            ),
        ):
            events, trace = await self._run_turn(
                rider,
                turn_id="live-plan-nearby-branches",
            )

        self._report("nearby_multi_branch_model_choice", rider, events, trace)
        self._assert_completed(events)
        names = _tool_names(trace)
        self.assertIn("discover_places", names)
        self.assertIn("prepare_route_options", names)
        self.assertIn("present_route", names)
        prepare_input = _present_input(trace, "prepare_route_options")
        self.assertGreaterEqual(len(prepare_input.get("destination_place_ids") or []), 2)
        card = next(event for event in events if event.type == "route_card")
        self.assertIn("park slope", str(card.destination.get("label") or "").casefold())
        decision = card.selection_decision or {}
        self.assertEqual(decision.get("selection_source"), "model")
        self.assertIn(decision.get("reason_code"), {"less_walking", "reasonable_local_option"})
        self.assertNotIn("forest hills", _passenger_text(events).casefold())

    async def test_jfk_fewer_transfers_controls_the_rationale(self) -> None:
        rider = "Get me to JFK with fewer transfers because I have a large suitcase."
        with patch(
            "app.services.agent.tools.route.prepare_route_options.prepare_single_leg",
            new=AsyncMock(side_effect=_jfk_routes),
        ):
            events, trace = await self._run_turn(rider, turn_id="live-plan-jfk")

        self._report("jfk_fewer_transfers", rider, events, trace)
        self._assert_completed(events)
        prepare_input = _present_input(trace, "prepare_route_options")
        self.assertEqual(prepare_input.get("routing_preference"), "FEWER_TRANSFERS")
        present_input = _present_input(trace, "present_route")
        self.assertEqual(present_input.get("reason_code"), "fewer_transfers")
        card = next(event for event in events if event.type == "route_card")
        decision = card.selection_decision or {}
        self.assertEqual(decision.get("reason_code"), "fewer_transfers")
        self.assertNotIn(
            (card.summary or {}).get("reason"),
            {"fastest", "less_walking"},
        )

    async def test_msg_crowd_gap_stays_visible_and_grounded(self) -> None:
        rider = "Get me to Madison Square Garden and avoid crowds."
        with patch(
            "app.services.agent.tools.route.prepare_route_options.prepare_single_leg",
            new=AsyncMock(side_effect=_msg_routes),
        ):
            events, trace = await self._run_turn(rider, turn_id="live-plan-msg")

        self._report("msg_avoid_crowds_limitation", rider, events, trace)
        self._assert_completed(events)
        prepare_input = _present_input(trace, "prepare_route_options")
        self.assertTrue(prepare_input.get("avoid_crowds"))
        present_input = _present_input(trace, "present_route")
        self.assertNotEqual(
            present_input.get("reason_code"),
            "lower_event_crowd_exposure",
        )
        text = _passenger_text(events).casefold()
        self.assertIn("crowd", text)
        self.assertTrue("could not" in text or "couldn't" in text or "partial" in text)
        card = next(event for event in events if event.type == "route_card")
        self.assertNotEqual(
            (card.selection_decision or {}).get("reason_code"),
            "lower_event_crowd_exposure",
        )

    async def test_accepted_b_trip_supplies_direction_without_asking(self) -> None:
        index = StopPatternIndex.load()
        gtfs = SimpleNamespace(_pattern_index=index)
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
        session_id, session = session_module.new_session()
        session["active_trip"] = {
            "card_id": "live-b-trip",
            "role": "recommended",
            "lines": ["B"],
            "destination": {"label": "7 Av"},
            "first_boarding": first_boarding_context(gtfs, step, 0),
            "canonical_itinerary": build_canonical_itinerary(
                [step],
                origin="Church Av",
                destination="7 Av",
            ),
        }
        rider = "How is the B running for the trip you just gave me?"
        status_mock = AsyncMock(return_value=_b_status())
        with patch.object(
            check_transit,
            "collect_service_status",
            new=status_mock,
        ):
            events, trace = await self._run_session_turn(
                rider,
                turn_id="live-plan-b-direction",
                session_id=session_id,
                session=session,
                gtfs=gtfs,
            )

        self._report("accepted_b_direction", rider, events, trace)
        self._assert_completed(events)
        names = _tool_names(trace)
        self.assertIn("check_transit", names)
        self.assertIn("present_transit", names)
        self.assertNotIn("clarification", events[-1].terminal_state)
        status_mock.assert_awaited_once()
        self.assertEqual(status_mock.await_args.args[1]["direction"], "uptown")
        text = _passenger_text(events).casefold()
        self.assertIn("delay", text)
        self.assertNotIn("which direction", text)

    async def test_ordinary_route_completes_without_an_optional_offer(self) -> None:
        rider = "Route me to Union Square."
        with patch(
            "app.services.agent.tools.route.prepare_route_options.prepare_single_leg",
            new=AsyncMock(side_effect=_prepared_route),
        ):
            events, trace = await self._run_turn(
                rider,
                turn_id="live-plan-no-follow-up",
            )

        self._report("ordinary_route_no_follow_up", rider, events, trace)
        self._assert_completed(events)
        present_inputs = [
            tool_input
            for name, tool_input in trace.tool_calls
            if name == "present_route"
        ]
        self.assertTrue(present_inputs)
        present_input = present_inputs[-1]
        self.assertEqual(present_input.get("follow_up"), "")
        lead_in = str(present_input.get("lead_in") or "").strip()
        self.assertTrue(lead_in)
        passenger_text = _passenger_text(events)
        lowered = passenger_text.casefold()
        self.assertIn(lead_in.casefold(), lowered)
        self.assertNotIn("?", passenger_text)
        for generic in (
            "this option satisfies the required trip constraints",
            "fits the trip",
            "satisfies the constraints",
            "the best option",
            "the practical option",
            "a practical fit",
            "satisfying your trip",
            "without any complications",
            "hard-valid",
            "verified choice",
            "tradeoff advantage",
            "evidence",
            "constraints",
        ):
            self.assertNotIn(generic, lowered)
        card = next(event for event in events if event.type == "route_card")
        decision = card.selection_decision or {}
        reason_code = str(decision.get("reason_code") or "")
        self.assertEqual(reason_code, present_input.get("reason_code"))
        structured_codes = {
            str(reason.get("code") or "")
            for reason in (card.itinerary or {}).get(
                "structured_recommendation_reasons", []
            )
            if isinstance(reason, dict)
        }
        self.assertIn(reason_code, structured_codes)
        reason_terms = {
            "fastest": ("fast", "quick"),
            "less_walking": ("walk",),
            "fewer_transfers": ("transfer",),
            "avoids_active_disruption": ("delay", "disruption", "reliable"),
            "lower_event_crowd_exposure": ("crowd",),
            "meets_hard_constraints": (
                "close",
                "equal",
                "similar",
                "no clear",
                "nothing stood out",
                "no strong edge",
                "covers what",
            ),
            "reasonable_local_option": ("nearby", "travel", "burden"),
            "coverage_gap": ("coverage", "could not", "couldn't"),
            "accessibility": ("accessible", "elevator", "stairs"),
        }
        self.assertIn(reason_code, reason_terms)
        self.assertTrue(
            any(term in lowered for term in reason_terms[reason_code]),
            (reason_code, lead_in),
        )
        route_shape_claim = re.search(
            r"\bdirect\b|straightforward|transfer[- ]free",
            lowered,
        )
        if route_shape_claim:
            itinerary = card.itinerary or {}
            transfer_count = itinerary.get(
                "transfer_count",
                (card.summary or {}).get("transfers"),
            )
            self.assertIsNotNone(transfer_count)
            self.assertEqual(int(transfer_count), 0)
            transit_legs = [
                leg
                for leg in (itinerary.get("legs") or [])
                if str(leg.get("mode") or "").upper() != "WALK"
            ]
            if route_shape_claim.group(0) != "straightforward":
                self.assertLessEqual(len(transit_legs), 1)
        self.assertEqual(sum(event.type == "route_card" for event in events), 1)


if __name__ == "__main__":
    unittest.main()
