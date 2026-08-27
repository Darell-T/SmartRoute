"""Live Sonnet checks for core tool selection and terminal behavior."""

from __future__ import annotations

import unittest
from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

from app.services.agent import session as session_module
from app.services.agent.tools._types import ToolResult
from app.services.agent.tools.places import damn_lines, discover_places
from app.services.agent.tools.transit import check_transit

from tests.anthropic_live_fixtures import (
    _place_results,
    _prepared_crowd_route,
    _prepared_route,
)
from tests.anthropic_live_support import (
    LIVE_ENABLED,
    AnthropicLiveAgentContractMixin,
    _passenger_text,
    _tool_names,
)

LINDUSTRIE_GOOGLE_PLACE_ID = "ChIJ92OsaJVZwokRsC54kf-J-3g"


def _queue_place_results() -> ToolResult:
    return ToolResult(
        ok=True,
        data={
            "results": [
                {
                    "name": "L'industrie Pizzeria",
                    "address": "104 Christopher Street, New York, NY",
                    "lat": 40.7334,
                    "lng": -74.0052,
                    "open_now": True,
                    "rating": 4.7,
                    "review_count": 390,
                    "place_id": LINDUSTRIE_GOOGLE_PLACE_ID,
                    "address_components": [
                        {
                            "longText": "Manhattan",
                            "types": ["sublocality_level_1"],
                        }
                    ],
                },
                {
                    "name": "Neighborhood Pizza",
                    "address": "10 West 4th Street, New York, NY",
                    "lat": 40.7311,
                    "lng": -73.9972,
                    "open_now": True,
                    "rating": 4.6,
                    "review_count": 210,
                    "place_id": "google-unmonitored-pizza",
                    "address_components": [
                        {
                            "longText": "Manhattan",
                            "types": ["sublocality_level_1"],
                        }
                    ],
                },
            ]
        },
        summary="2 verified pizza places",
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
        assert names[0] == "declare_goals"
        assert names[-1] == "complete_turn"
        assert "discover_places" not in names
        assert "check_transit" not in names
        assert "prepare_route_options" not in names
        self._report("natural_greeting", rider, events, trace)

    async def test_real_sonnet_presents_queue_evidence_with_attribution(self) -> None:
        rider = (
            "Show me two good pizza options near me. I would rather not wait "
            "in a long line, so include any current queue information."
        )
        current = damn_lines.CurrentQueueResult(
            observations={
                LINDUSTRIE_GOOGLE_PLACE_ID: damn_lines.QueueObservation(
                    google_place_id=LINDUSTRIE_GOOGLE_PLACE_ID,
                    people_count=12,
                    wait_minutes=9,
                    captured_at=datetime(2026, 8, 18, 16, tzinfo=UTC),
                )
            },
            provider_available=True,
        )
        with (
            patch.object(
                discover_places.search_local_places,
                "execute",
                new=AsyncMock(return_value=_queue_place_results()),
            ),
            patch.object(
                damn_lines,
                "get_current_observations",
                new=AsyncMock(return_value=current),
            ),
        ):
            events, trace = await self._run_turn(
                rider,
                turn_id="live-queue-attribution",
            )

        self._report("queue_attribution", rider, events, trace)
        self._assert_completed(events)
        names = _tool_names(trace)
        assert names[0] == "declare_goals"
        assert "discover_places" in names
        assert "present_places" in names
        assert "prepare_route_options" not in names
        discover_input = next(
            payload
            for name, payload in trace.tool_calls
            if name == "discover_places"
        )
        assert discover_input["queue_context"]["mode"] == "decision"
        source_events = [event for event in events if event.type == "sources"]
        assert len(source_events) == 1
        assert source_events[0].sources == (
            {
                "title": "Damn Lines: L'industrie Pizzeria",
                "url": "https://damnlines.com/camera/lindustrie-pizzeria",
            },
        )
        passenger_text = _passenger_text(events).casefold()
        assert "9 minutes" in passenger_text
        assert "12 people" in passenger_text
        assert "no queue coverage" in passenger_text

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
        assert names[0] == "declare_goals"
        assert "discover_places" in names
        assert "prepare_route_options" in names
        assert "present_route" in names
        assert "present_places" not in names
        assert sum(event.type == "route_card" for event in events) == 1
        self._assert_completed(events)
        failed_attempts = [
            attempt for attempt in trace.capability_attempts if not attempt["ok"]
        ]
        assert failed_attempts == []

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
        assert names[0] == "declare_goals"
        assert "discover_places" in names
        assert "present_places" in names
        assert "prepare_route_options" not in names
        text = _passenger_text(events).casefold()
        assert "reviews" not in text
        assert "verified match" not in text
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
        assert "discover_places" in names
        assert "prepare_route_options" in names
        assert "present_route" in names
        prepare_input = next(
            tool_input
            for name, tool_input in trace.tool_calls
            if name == "prepare_route_options"
        )
        assert prepare_input.get("avoid_crowds")
        route_card = next(event for event in events if event.type == "route_card")
        assert "B" in (route_card.summary.get("lines") or [])
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
        assert names[0] == "declare_goals"
        assert "discover_places" not in names
        assert "prepare_route_options" in names
        assert "present_route" in names
        prepare_input = next(
            tool_input
            for name, tool_input in trace.tool_calls
            if name == "prepare_route_options"
        )
        assert prepare_input.get("avoid_crowds")
        prepared_input, prepared_ctx = prepare_mock.await_args.args[:2]
        assert prepared_input.get("origin") == "user"
        assert prepared_ctx.origin == {"lat": 40.6494, "lng": -73.9631}
        assert sum(event.type == "route_card" for event in events) == 1
        assert "current address" not in _passenger_text(events).casefold()
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
        assert "discover_places" in names
        assert "prepare_route_options" in names
        assert "present_places" in names
        assert "complete_turn" in names
        assert sum(event.type == "route_card" for event in events) == 0
        passenger_text = _passenger_text(events).casefold()
        assert "ramen" in passenger_text
        assert "route" in passenger_text or "subway" in passenger_text
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
        assert "check_transit" in names
        assert "present_transit" in names
        assert "clarification" not in events[-1].terminal_state
        assert "complete_turn" not in names
        check_inputs = [
            tool_input
            for name, tool_input in trace.tool_calls
            if name == "check_transit"
        ]
        assert [tool_input.get("operation") for tool_input in check_inputs] == ["service_status", "arrivals"]
        check_indexes = [i for i, name in enumerate(names) if name == "check_transit"]
        present_indexes = [i for i, name in enumerate(names) if name == "present_transit"]
        assert len(present_indexes) == 2
        assert max(check_indexes) < min(present_indexes)
        present_inputs = [
            tool_input
            for name, tool_input in trace.tool_calls
            if name == "present_transit"
        ]
        assert any(term in str(tool_input.get("lead_in") or "").casefold() for tool_input in present_inputs for term in ("wait", "take", "taking"))
        text = _passenger_text(events).casefold()
        assert "out of date" in text or "fresher" in text
        assert "no active alert" not in text
        card = next(event for event in events if event.type == "arrival_card")
        assert card.route_id == "Q"
        assert card.stop.get("id") == "D28"
        assert card.directions[0]["arrivals"][0]["minutes"] == 4
