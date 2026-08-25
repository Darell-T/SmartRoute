"""Live Sonnet checks for core tool selection and terminal behavior."""

from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, patch

from app.services.agent import session as session_module
from app.services.agent.tools.transit import check_transit
from app.services.agent.tools.places import discover_places
from app.services.agent.tools._types import ToolResult

from tests.anthropic_live_fixtures import (
    _place_results,
    _prepared_crowd_route,
    _prepared_route,
)
from tests.anthropic_live_support import (
    AnthropicLiveAgentContractMixin,
    LIVE_ENABLED,
    _passenger_text,
    _tool_names,
)


@unittest.skipUnless(
    LIVE_ENABLED,
    "set RUN_ANTHROPIC_TOOL_CONTRACT=1 to run live provider checks",
)
class AnthropicLiveAgentContractTests(
    AnthropicLiveAgentContractMixin,
    unittest.IsolatedAsyncioTestCase,
):
    async def test_real_sonnet_answers_a_greeting_naturally(self) -> None:
        rider = "Hi — what can you help me with?"
        events, trace = await self._run_turn(rider, turn_id="live-greeting")

        self._assert_completed(events)
        names = _tool_names(trace)
        self.assertEqual(names[0], "declare_goals")
        self.assertEqual(names[-1], "complete_turn")
        self.assertNotIn("discover_places", names)
        self.assertNotIn("check_transit", names)
        self.assertNotIn("prepare_route_options", names)
        self._report("natural_greeting", rider, events, trace)

    async def test_real_sonnet_completes_compound_discovery_and_route(self) -> None:
        rider = "Find a good ramen spot and route me there by subway."
        with (
            patch.object(
                discover_places.search_local_places,
                "execute",
                new=AsyncMock(return_value=_place_results()),
            ),
            patch(
                "app.services.agent.tools.route.prepare_route_options.prepare_single_leg",
                new=AsyncMock(side_effect=_prepared_route),
            ),
        ):
            events, trace = await self._run_turn(
                rider,
                turn_id="live-compound",
            )

        self._report("compound_discovery_route", rider, events, trace)
        names = _tool_names(trace)
        self.assertEqual(names[0], "declare_goals")
        self.assertIn("discover_places", names)
        self.assertIn("prepare_route_options", names)
        self.assertIn("present_route", names)
        self.assertNotIn("present_places", names)
        self.assertEqual(sum(event.type == "route_card" for event in events), 1)
        self._assert_completed(events)
        failed_attempts = [
            attempt for attempt in trace.capability_attempts if not attempt["ok"]
        ]
        self.assertEqual(failed_attempts, [])

    async def test_real_sonnet_presents_a_concise_place_list(self) -> None:
        rider = "What are three good ramen options in Manhattan?"
        with patch.object(
            discover_places.search_local_places,
            "execute",
            new=AsyncMock(return_value=_place_results()),
        ):
            events, trace = await self._run_turn(
                rider,
                turn_id="live-place-list",
            )

        self._report("concise_place_list", rider, events, trace)
        names = _tool_names(trace)
        self.assertEqual(names[0], "declare_goals")
        self.assertIn("discover_places", names)
        self.assertIn("present_places", names)
        self.assertNotIn("prepare_route_options", names)
        text = _passenger_text(events).casefold()
        self.assertNotIn("reviews", text)
        self.assertNotIn("verified match", text)
        self._assert_completed(events)

    async def test_real_sonnet_uses_crowd_evidence_in_route_choice(self) -> None:
        rider = "Find a good ramen spot and route me there while avoiding crowds."
        with (
            patch.object(
                discover_places.search_local_places,
                "execute",
                new=AsyncMock(return_value=_place_results()),
            ),
            patch(
                "app.services.agent.tools.route.prepare_route_options.prepare_single_leg",
                new=AsyncMock(side_effect=_prepared_crowd_route),
            ),
        ):
            events, trace = await self._run_turn(
                rider,
                turn_id="live-crowd-route",
            )

        self._report("crowd_aware_compound_route", rider, events, trace)
        names = _tool_names(trace)
        self.assertIn("discover_places", names)
        self.assertIn("prepare_route_options", names)
        self.assertIn("present_route", names)
        prepare_input = next(
            tool_input
            for name, tool_input in trace.tool_calls
            if name == "prepare_route_options"
        )
        self.assertTrue(prepare_input.get("avoid_crowds"))
        route_card = next(event for event in events if event.type == "route_card")
        self.assertIn("B", route_card.summary.get("lines") or [])
        self._assert_completed(events)

    async def test_real_sonnet_routes_from_shared_device_location(self) -> None:
        rider = "Get me to Madison Square Garden and avoid crowds."
        prepare_mock = AsyncMock(side_effect=_prepared_crowd_route)
        with patch(
            "app.services.agent.tools.route.prepare_route_options.prepare_single_leg",
            new=prepare_mock,
        ):
            events, trace = await self._run_turn(
                rider,
                turn_id="live-shared-location",
            )

        self._report("shared_location_route", rider, events, trace)
        names = _tool_names(trace)
        self.assertEqual(names[0], "declare_goals")
        self.assertNotIn("discover_places", names)
        self.assertIn("prepare_route_options", names)
        self.assertIn("present_route", names)
        prepare_input = next(
            tool_input
            for name, tool_input in trace.tool_calls
            if name == "prepare_route_options"
        )
        self.assertTrue(prepare_input.get("avoid_crowds"))
        prepared_input, prepared_ctx = prepare_mock.await_args.args[:2]
        self.assertEqual(prepared_input.get("origin"), "user")
        self.assertEqual(prepared_ctx.origin, {"lat": 40.6494, "lng": -73.9631})
        self.assertEqual(sum(event.type == "route_card" for event in events), 1)
        self.assertNotIn("current address", _passenger_text(events).casefold())
        self._assert_completed(events)

    async def test_real_sonnet_preserves_partial_discovery_success(self) -> None:
        rider = "Find a good ramen spot and route me there by subway."
        with (
            patch.object(
                discover_places.search_local_places,
                "execute",
                new=AsyncMock(return_value=_place_results()),
            ),
            patch(
                "app.services.agent.tools.route.prepare_route_options.prepare_single_leg",
                new=AsyncMock(
                    return_value=ToolResult(
                        ok=False,
                        error="no transit route found between those points",
                    )
                ),
            ),
        ):
            events, trace = await self._run_turn(
                rider,
                turn_id="live-partial-success",
            )

        self._report("partial_discovery_success", rider, events, trace)
        names = _tool_names(trace)
        self.assertIn("discover_places", names)
        self.assertIn("prepare_route_options", names)
        self.assertIn("present_places", names)
        self.assertIn("complete_turn", names)
        self.assertEqual(sum(event.type == "route_card" for event in events), 0)
        passenger_text = _passenger_text(events).casefold()
        self.assertIn("ramen", passenger_text)
        self.assertTrue("route" in passenger_text or "subway" in passenger_text)
        self._assert_completed(events)

    async def test_real_sonnet_take_wait_checks_status_and_arrivals(self) -> None:
        session_id, session = session_module.new_session()
        session["active_trip"] = {
            "first_boarding": {
                "route_id": "Q",
                "stop_id": "D28",
                "stop_name": "Church Av",
                "direction": "uptown",
                "direction_label": "uptown",
            }
        }
        status = ToolResult(
            ok=True,
            data={
                "source": "mta_service_alerts",
                "freshness": "stale",
                "status": "active_alerts",
                "alerts": [{
                    "alert_id": "q-live-check",
                    "header": "Uptown Q service may be delayed",
                    "route_ids": ["Q"],
                    "direction": "uptown",
                }],
            },
            summary="Q status is stale",
        )
        arrivals = ToolResult(
            ok=True,
            data={
                "route_id": "Q",
                "source_status": "current",
                "stop": {"id": "D28", "name": "Church Av"},
                "directions": [{
                    "id": "uptown",
                    "label": "Uptown / Manhattan-bound",
                    "arrivals": [{"minutes": 4, "realtime": True}],
                }],
            },
            summary="Uptown Q arrival checked",
        )
        with (
            patch.object(check_transit, "collect_service_status", new=AsyncMock(return_value=status)),
            patch.object(check_transit.lookup_arrivals, "execute", new=AsyncMock(return_value=arrivals)),
        ):
            events, trace = await self._run_session_turn(
                "Is it smart to take the uptown Q now?",
                turn_id="live-plan-take-wait",
                session_id=session_id,
                session=session,
            )

        self._report("take_wait_status_and_arrivals", "Is it smart to take the uptown Q now?", events, trace)
        self._assert_completed(events)
        names = _tool_names(trace)
        self.assertIn("check_transit", names)
        self.assertIn("present_transit", names)
        self.assertNotIn("clarification", events[-1].terminal_state)
        self.assertNotIn("complete_turn", names)
        check_inputs = [
            tool_input
            for name, tool_input in trace.tool_calls
            if name == "check_transit"
        ]
        self.assertEqual(
            [tool_input.get("operation") for tool_input in check_inputs],
            ["service_status", "arrivals"],
        )
        check_indexes = [i for i, name in enumerate(names) if name == "check_transit"]
        present_indexes = [i for i, name in enumerate(names) if name == "present_transit"]
        self.assertEqual(len(present_indexes), 2)
        self.assertLess(max(check_indexes), min(present_indexes))
        present_inputs = [
            tool_input
            for name, tool_input in trace.tool_calls
            if name == "present_transit"
        ]
        self.assertTrue(
            any(
                term in str(tool_input.get("lead_in") or "").casefold()
                for tool_input in present_inputs
                for term in ("wait", "take", "taking")
            )
        )
        text = _passenger_text(events).casefold()
        self.assertTrue("out of date" in text or "fresher" in text)
        self.assertNotIn("no active alert", text)
        card = next(event for event in events if event.type == "arrival_card")
        self.assertEqual(card.route_id, "Q")
        self.assertEqual(card.stop.get("id"), "D28")
        self.assertEqual(card.directions[0]["arrivals"][0]["minutes"], 4)
