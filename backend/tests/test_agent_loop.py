"""Layer-1 tests for the conversational agent's turn loop
(app/services/agent/loop.py) -- SSE ordering, streaming, round-cap/deadline
wrap-up, parallel tool execution, and budget gating.

Follows the sys.modules fake-anthropic pattern from test_ai_advisor_mock.py,
scripted via tests/_fake_anthropic.py's FakeStream. Per the lesson learned
migrating directions.py's tests: each TestCase class reloads the module(s)
it needs exactly once in setUpClass, never per-test, to avoid churning
importlib.reload/sys.modules patching enough to trip the zoneinfo bug seen
under heavy reload cycling in this sandbox.
"""

from __future__ import annotations

import importlib
import asyncio
from contextlib import contextmanager
import os
import random
import secrets
import sys
import types
import time
import unittest
from unittest.mock import patch

from app.services.agent import session as session_module
from app.services.agent import trip_state as trip_state_module
from app.services.agent.tools import ToolContext, ToolResult, ToolSpec
from app.services.agent import events as agent_events
from app.utils import cache
from tests._fake_anthropic import reload_agent_loop_module


def _load_agent_loop(env: dict | None = None):
    # See _fake_anthropic.reload_agent_loop_module's docstring for why this
    # is a manual sys.modules swap rather than patch.dict(sys.modules, ...).
    return reload_agent_loop_module(env=env)


@contextmanager
def _reloaded_budget(env: dict[str, str]):
    """Reload budget constants for one test class, then restore ambient state.

    ``importlib.reload`` mutates the existing module object, which is also the
    object imported by ``loop``. The environment patch is therefore not enough:
    after it exits, reload once more against the restored environment so later
    classes and runner selections see their original budget configuration.
    """
    budget_module = importlib.import_module("app.services.agent.budget")
    try:
        with patch.dict(os.environ, env, clear=False):
            yield importlib.reload(budget_module)
    finally:
        importlib.reload(budget_module)


async def _fake_ok_tool(tool_input, ctx):
    return ToolResult(ok=True, data={"echo": tool_input}, summary="did the thing")


async def _fake_fail_tool(tool_input, ctx):
    return ToolResult(ok=False, error="boom")


async def _fake_slow_tool(tool_input, ctx):
    import asyncio

    await asyncio.sleep(10)
    return ToolResult(ok=True, data={})


async def _fake_plan_trip_tool(tool_input, ctx):
    event = agent_events.RouteCardEvent(
        card_id="rc_test0001",
        turn_id=ctx.turn_id,
        role="recommended",
        origin={"label": "A", "lat": 40.7, "lng": -73.9},
        destination={"label": "B", "lat": 40.8, "lng": -74.0},
        summary={"eta_minutes": 20, "transfers": 0, "lines": ["Q"], "reason": "fastest"},
        route=[{"type": "SUBWAY"}],
        alerts=[],
    )
    return ToolResult(
        ok=True,
        data={"candidates": [{"card_id": "rc_test0001"}]},
        summary="found 1 route",
        events=[event],
        session_route_cards=[{"card_id": "rc_test0001", "role": "recommended", "lines": ["Q"], "eta_minutes": 20}],
    )


async def _fake_prepare_route_options_tool(tool_input, ctx):
    return ToolResult(
        ok=True,
        data={
            "candidate_set_id": "cs_test_only",
            "route_status": "good",
            "presentation_allowed": True,
            "candidates": [
                {
                    "candidate_id": "cd_test_only",
                    "duration_minutes": 20,
                    "transfers": 0,
                    "transit_lines": ["Q"],
                }
            ],
        },
        summary="prepared one route option",
    )


async def _fake_present_route_tool(tool_input, ctx):
    event = agent_events.RouteCardEvent(
        card_id="rc_conversational",
        turn_id=ctx.turn_id,
        role="recommended",
        origin={"label": "A", "lat": 40.7, "lng": -73.9},
        destination={"label": "B", "lat": 40.8, "lng": -74.0},
        summary={"eta_minutes": 20, "transfers": 0, "lines": ["Q"], "reason": "fits"},
        route=[{"type": "SUBWAY", "route_id": "Q"}],
        alerts=[],
    )
    return ToolResult(
        ok=True,
        data={"passenger_explanation": "Take the Q; it is the best fit."},
        summary="presented one route",
        events=[event],
        session_route_cards=[
            {"card_id": "rc_conversational", "role": "recommended", "lines": ["Q"], "eta_minutes": 20}
        ],
    )


async def _fake_ambiguous_plan_trip_tool(tool_input, ctx):
    return ToolResult(
        ok=True,
        data={
            "source_status": "stop_not_resolved",
            "ambiguity": [{"name": "34 St"}, {"name": "34 St-Hudson Yards"}],
        },
        summary="destination is ambiguous",
    )


async def _fake_arrivals_tool(tool_input, ctx):
    payload = {
        "route_id": tool_input["route_id"],
        "stop": {
            "id": "D28",
            "name": tool_input.get("stop_query") or "Newkirk Plaza",
            "latitude": 40.6351,
            "longitude": -73.9628,
        },
        "directions": [
            {
                "id": "downtown",
                "label": "Downtown / Brooklyn-bound",
                "arrivals": [
                    {
                        "expected_at": "2026-07-25T14:04:00Z",
                        "minutes": 4,
                        "realtime": True,
                    }
                ],
            }
        ],
        "updated_at": "2026-07-25T14:00:00Z",
        "source_status": "live",
    }
    return ToolResult(
        ok=True,
        data=payload,
        summary="live arrivals",
        events=[agent_events.ArrivalCardEvent.from_lookup(ctx.turn_id, payload)],
    )


async def _fake_arrival_clarification_tool(tool_input, ctx):
    payload = {
        "route_id": tool_input["route_id"],
        "stop": {"id": "", "name": "Transit stop"},
        "directions": [],
        "updated_at": "2026-07-25T14:00:00Z",
        "source_status": "stop_not_resolved",
        "ambiguity": [
            {"stop_id": "A01", "stop_name": "34 St-Penn Station"},
            {"stop_id": "A32", "stop_name": "34 St-Hudson Yards"},
        ],
    }
    return ToolResult(
        ok=True,
        data=payload,
        summary="station clarification required",
        events=[agent_events.ArrivalCardEvent.from_lookup(ctx.turn_id, payload)],
    )


async def _fake_poi_tool(tool_input, ctx):
    return ToolResult(
        ok=True,
        data={
            "results": [
                {
                    "name": "Di Fara Pizza",
                    "address": "1424 Avenue J, Brooklyn, NY",
                    "lat": 40.625,
                    "lng": -73.961,
                }
            ]
        },
        summary="found one grounded place",
    )


async def _fake_search_local_places_tool(tool_input, ctx):
    return ToolResult(
        ok=True,
        data={
            "discovery_set_id": "ds_test_only",
            "places": [
                {
                    "place_id": "pl_di_fara",
                    "ordinal": 1,
                    "name": "Di Fara Pizza",
                    "neighborhood": "Brooklyn",
                    "category": "pizza",
                    "open_status": "open",
                    "baseline_score": 0.9,
                    "address": "1424 Av J",
                }
            ],
        },
        summary="found one grounded place",
    )


def _test_registry() -> dict[str, ToolSpec]:
    return {
        "ok_tool": ToolSpec(schema={"name": "ok_tool"}, executor=_fake_ok_tool, label_fn=lambda i: "Doing the thing…", timeout_s=5.0),
        "fail_tool": ToolSpec(schema={"name": "fail_tool"}, executor=_fake_fail_tool, label_fn=lambda i: "Doing the failing thing…", timeout_s=5.0),
        "slow_tool": ToolSpec(schema={"name": "slow_tool"}, executor=_fake_slow_tool, label_fn=lambda i: "Doing the slow thing…", timeout_s=0.05),
        "plan_trip": ToolSpec(schema={"name": "plan_trip"}, executor=_fake_plan_trip_tool, label_fn=lambda i: "Finding routes…", timeout_s=5.0),
        "prepare_route_options": ToolSpec(
            schema={"name": "prepare_route_options"},
            executor=_fake_prepare_route_options_tool,
            label_fn=lambda i: "Preparing routes…",
            timeout_s=5.0,
        ),
        "present_route": ToolSpec(
            schema={"name": "present_route"},
            executor=_fake_present_route_tool,
            label_fn=lambda i: "Presenting the route…",
            timeout_s=5.0,
        ),
        "lookup_arrivals": ToolSpec(
            schema={"name": "lookup_arrivals"},
            executor=_fake_arrivals_tool,
            label_fn=lambda i: f"Checking {i.get('route_id')} arrivals",
            timeout_s=5.0,
        ),
        "poi_search": ToolSpec(
            schema={"name": "poi_search"},
            executor=_fake_poi_tool,
            label_fn=lambda i: "Finding places",
            timeout_s=5.0,
        ),
        "search_local_places": ToolSpec(
            schema={"name": "search_local_places"},
            executor=_fake_search_local_places_tool,
            label_fn=lambda i: "Finding places",
            timeout_s=5.0,
        ),
        "get_place_details": ToolSpec(
            schema={"name": "get_place_details"},
            executor=_fake_search_local_places_tool,
            label_fn=lambda i: "Checking place details",
            timeout_s=5.0,
        ),
    }


def _offered_schemas_for_registry(registry: dict) -> list[dict]:
    """Explicit offered surface for one fake-registry mechanics run.

    Loop-mechanics tests inject fake ``ToolSpec`` executors through
    ``_AgentLoopHelpers._run(tool_registry=...)``. Offering the injected
    registry's own schemas on that run makes scripted fake tools genuinely
    offered instead of bypassing the per-turn allowlist boundary.
    """

    return [spec.schema for spec in registry.values()]


class _AgentLoopHelpers:
    """Mixin: subclasses set `cls.loop` in setUpClass."""

    async def _run(
        self,
        rounds,
        *,
        message="transit status",
        session=None,
        session_id=None,
        origin=None,
        trace=None,
        tool_registry=None,
        selected_card_id=None,
        response_presentation="auto",
    ):
        self.loop.client.messages._rounds = list(rounds)
        self.loop.client.messages.calls = []
        if session is None:
            _discard_id, session = session_module.new_session()
        if session_id is None:
            # Unique per call so per-session rate limiting never leaks
            # between unrelated tests.
            session_id = secrets.token_hex(8)

        patcher = (
            patch.object(self.loop, "TOOL_REGISTRY", tool_registry)
            if tool_registry is not None
            else None
        )
        intent_patcher = (
            patch.object(
                self.loop,
                "_tools_for_intent",
                lambda *_args, **_kwargs: _offered_schemas_for_registry(
                    tool_registry
                ),
            )
            if tool_registry is not None
            else None
        )
        active_patchers = [
            active for active in (patcher, intent_patcher) if active is not None
        ]
        for active in active_patchers:
            active.start()
        events_out = []
        try:
            async for event in self.loop.run_agent_turn(
                session=session,
                session_id=session_id,
                turn_id="t1",
                message=message,
                now_et="2026-07-15T21:00:00-04:00",
                gtfs=None,
                origin=origin,
                selected_card_id=selected_card_id,
                response_presentation=response_presentation,
                trace=trace,
            ):
                events_out.append(event)
        finally:
            for active in active_patchers:
                active.stop()
        return events_out, session


class _BudgetConfiguredAgentLoopTests(_AgentLoopHelpers, unittest.IsolatedAsyncioTestCase):
    BUDGET_ENV: dict[str, str]

    @classmethod
    def setUpClass(cls):
        cls.loop = _load_agent_loop()
        cls._budget_scope = _reloaded_budget(cls.BUDGET_ENV)
        cls.budget = cls._budget_scope.__enter__()
        cls.addClassCleanup(cls._budget_scope.__exit__, None, None, None)


class LoopMechanicsTests(_AgentLoopHelpers, unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        cls.loop = _load_agent_loop()

    def setUp(self):
        cache._mem.clear()

    async def test_meta_first_and_done_last_on_a_clean_turn(self):
        events_out, _session = await self._run([{"text": ["Hello rider"], "stop_reason": "end_turn"}])
        self.assertEqual(events_out[0].type, "meta")
        self.assertEqual(events_out[-1].type, "done")
        self.assertEqual(events_out[-1].stop_reason, "end_turn")

    async def test_token_events_stream_text_deltas_in_order(self):
        events_out, _ = await self._run([{"text": ["Hel", "lo "], "stop_reason": "end_turn"}])
        tokens = [event.text for event in events_out if event.type == "token"]
        self.assertEqual(tokens, ["Hel", "lo "])

    async def test_final_text_persisted_to_session_history(self):
        _events, session = await self._run([{"text": ["ok, taking the Q"], "stop_reason": "end_turn"}])
        assistant_turns = [h for h in session["history"] if h["role"] == "assistant"]
        self.assertEqual(assistant_turns[-1]["text"], "ok, taking the Q")

    async def test_internal_card_ids_and_markdown_do_not_reach_rider_prose(self):
        events_out, session = await self._run(
            [
                {
                    "text": [
                        "**Recommended: Card ",
                        "rc_b87e6f1a — Q/D trains, 1 transfer, ~31 min**",
                    ],
                    "stop_reason": "end_turn",
                }
            ]
        )

        prose = "".join(event.text for event in events_out if event.type == "token")
        self.assertEqual(
            prose,
            "Recommended: Q/D trains, 1 transfer, about 31 min",
        )
        self.assertNotIn("rc_b87e6f1a", session["history"][-1]["text"])
        self.assertNotIn("**", session["history"][-1]["text"])

    def test_opaque_candidate_ids_do_not_reach_rider_prose(self):
        sanitized = self.loop._sanitize_rider_text(
            "Selected cd_test_only from cs_test_only."
        )
        self.assertNotIn("cd_test_only", sanitized)
        self.assertNotIn("cs_test_only", sanitized)

    async def test_done_last_even_after_upstream_model_error(self):
        events_out, _ = await self._run([{"raise": True}])
        self.assertEqual(events_out[0].type, "meta")
        self.assertEqual(events_out[-1].type, "done")
        self.assertEqual(events_out[-1].stop_reason, "error")
        errors = [e for e in events_out if e.type == "error"]
        self.assertEqual(len(errors), 1)
        self.assertEqual(errors[0].code, "upstream_error")

    async def test_bad_request_is_attempted_once_and_emits_typed_error(self):
        class BadRequest(Exception):
            status_code = 400
            request_id = "req_bad_request"
            body = {
                "type": "error",
                "error": {
                    "type": "invalid_request_error",
                    "message": "temperature is not supported",
                },
            }

        events_out, _ = await self._run(
            [
                {"exception": BadRequest()},
                {"text": ["must not run"], "stop_reason": "end_turn"},
            ]
        )
        self.assertEqual(len(self.loop.client.messages.calls), 1)
        errors = [event for event in events_out if event.type == "error"]
        self.assertEqual(errors[0].code, "invalid_request")
        self.assertFalse(errors[0].retryable)
        self.assertEqual(events_out[-1].stop_reason, "error")

    async def test_transient_server_error_retries_within_application_bound(self):
        class ServerError(Exception):
            status_code = 503
            request_id = "req_server_error"
            body = {
                "type": "error",
                "error": {
                    "type": "api_error",
                    "message": "service temporarily unavailable",
                },
            }

        events_out, _ = await self._run(
            [
                {"exception": ServerError()},
                {"text": ["recovered"], "stop_reason": "end_turn"},
            ]
        )
        self.assertEqual(len(self.loop.client.messages.calls), 2)
        self.assertFalse(any(event.type == "error" for event in events_out))
        self.assertEqual(events_out[-1].stop_reason, "end_turn")

    async def test_system_block_carries_ephemeral_cache_control(self):
        await self._run([{"text": ["hi"], "stop_reason": "end_turn"}])
        kwargs = self.loop.client.messages.calls[0]
        self.assertEqual(kwargs["system"][-1]["cache_control"], {"type": "ephemeral"})

    async def test_context_block_appended_to_latest_user_message(self):
        await self._run([{"text": ["hi"], "stop_reason": "end_turn"}], origin={"lat": 40.7, "lng": -73.9})
        kwargs = self.loop.client.messages.calls[0]
        last_user_content = kwargs["messages"][-1]["content"]
        self.assertIn("<context>", last_user_content)
        self.assertIn("rider_location: 40.7000,-73.9000", last_user_content)

    async def test_quick_presentation_uses_the_shared_pipeline_with_smaller_budgets(self):
        rounds = [
            {"tool_use": [{"id": "tu_1", "name": "plan_trip", "input": {"destination": "Costco"}}], "stop_reason": "tool_use"},
            {"text": ["Take the Q."], "stop_reason": "end_turn"},
        ]
        trace = self.loop.TurnTrace()

        await self._run(
            rounds,
            message="Take me to Costco",
            response_presentation="quick",
            tool_registry=_test_registry(),
            trace=trace,
        )

        first_call = self.loop.client.messages.calls[0]
        self.assertIn(
            "response_presentation: quick",
            first_call["messages"][-1]["content"],
        )
        self.assertEqual(trace.tool_calls[0][1]["destination"], "Costco")
        self.assertEqual(trace.tool_calls[0][1]["max_candidates"], 2)
        self.assertFalse(trace.tool_calls[0][1]["avoid_crowds"])
        self.assertFalse(trace.tool_calls[0][1]["include_first_leg_arrivals"])
        self.assertEqual(trace.initial_mode, "quick")
        self.assertEqual(trace.final_mode, "quick")
        self.assertIsNone(trace.escalation_reason)
        self.assertEqual(trace.model_call_count, 1)
        self.assertEqual(len(self.loop.client.messages.calls), 1)

    async def test_auto_crowd_avoidance_enables_bounded_crowd_research(self):
        trace = self.loop.TurnTrace()
        rounds = [
            {
                "tool_use": [
                    {
                        "id": "tu_1",
                        "name": "plan_trip",
                        "input": {"destination": "Columbus Circle"},
                    }
                ],
                "stop_reason": "tool_use",
            },
            {"text": ["Take the A."], "stop_reason": "end_turn"},
        ]

        await self._run(
            rounds,
            message=(
                "I want to head to Columbus Circle later and avoid crowds "
                "on both the street and subway"
            ),
            tool_registry=_test_registry(),
            trace=trace,
        )

        plan_input = trace.tool_calls[0][1]
        self.assertTrue(plan_input["avoid_crowds"])
        self.assertEqual(plan_input["crowd_search_mode"], "auto")
        self.assertNotIn("include_incident_scan", plan_input)

    async def test_quick_crowd_request_keeps_the_quick_model_for_planning(self):
        trace = self.loop.TurnTrace()
        rounds = [
            {
                "tool_use": [
                    {
                        "id": "tu_1",
                        "name": "plan_trip",
                        "input": {"destination": "Columbus Circle"},
                    }
                ],
                "stop_reason": "tool_use",
            },
            {"text": ["Take the Q."], "stop_reason": "end_turn"},
        ]

        await self._run(
            rounds,
            message="Plan a trip to Columbus Circle and avoid crowds",
            response_presentation="quick",
            tool_registry=_test_registry(),
            trace=trace,
        )

        self.assertEqual(trace.escalation_reason, "explicit_crowd_evidence")
        self.assertEqual(trace.final_mode, "quick")
        self.assertEqual(trace.tool_calls[0][1]["crowd_search_mode"], "auto")
        self.assertEqual(
            self.loop.client.messages.calls[0]["model"],
            self.loop.agent_policy.policy_for_mode("quick").model,
        )
        self.assertEqual(len(self.loop.client.messages.calls), 1)

    async def test_explicit_route_request_is_injected_into_route_preparation(self):
        trace = self.loop.TurnTrace()
        rounds = [
            {
                "tool_use": [
                    {
                        "id": "tu_1",
                        "name": "plan_trip",
                        "input": {"destination": "Coney Island"},
                    }
                ],
                "stop_reason": "tool_use",
            },
            {"text": ["Take the Q."], "stop_reason": "end_turn"},
        ]

        await self._run(
            rounds,
            message="Plan a Q route to Coney Island",
            tool_registry=_test_registry(),
            trace=trace,
        )

        self.assertEqual(trace.tool_calls[0][1]["required_route_ids"], ["Q"])

    async def test_negative_route_request_injects_exclusions_not_required(self):
        trace = self.loop.TurnTrace()
        _discard_id, session = session_module.new_session()
        session["active_trip"] = {"card_id": "rc_active", "role": "recommended"}
        trip_state_module.update_trip_state(
            session,
            origin="Home",
            destination="Work",
            active_candidate_set_id="cs_active",
            selected_candidate_id="cd_active",
        )
        rounds = [
            {
                "tool_use": [
                    {
                        "id": "tu_1",
                        "name": "prepare_route_options",
                        # The model omits what_if; the server must still
                        # recognize the what-if turn from the rider message
                        # and keep preparation isolated from active state.
                        "input": {"destination": "Coney Island"},
                    }
                ],
                "stop_reason": "tool_use",
            },
            {"text": ["OK."], "stop_reason": "end_turn"},
        ]

        _events, session = await self._run(
            rounds,
            message="What if I avoid the Q?",
            session=session,
            session_id="sess-what-if-avoid-q",
            tool_registry=_test_registry(),
            trace=trace,
        )

        tool_call = trace.tool_calls[0][1]
        self.assertTrue(tool_call["what_if"])
        self.assertEqual(tool_call["excluded_route_ids"], ["Q"])
        self.assertNotIn("required_route_ids", tool_call)
        # A what-if exclusion is temporary and never becomes an active slot.
        self.assertNotIn(
            "excluded_route_ids",
            ((session.get("slots") or {}).get("constraints") or {}),
        )
        # Server-enforced what-if isolation keeps active candidate/trip state.
        state = trip_state_module.get_trip_state(session)
        self.assertEqual(state["active_candidate_set_id"], "cs_active")
        self.assertEqual(state["selected_candidate_id"], "cd_active")
        self.assertEqual(session["active_trip"]["card_id"], "rc_active")

    async def test_active_route_exclusion_persists_across_followups(self):
        trace = self.loop.TurnTrace()
        rounds = [
            {
                "tool_use": [
                    {
                        "id": "tu_1",
                        "name": "prepare_route_options",
                        "input": {"destination": "Coney Island"},
                    }
                ],
                "stop_reason": "tool_use",
            },
            {"text": ["OK."], "stop_reason": "end_turn"},
        ]

        _events, session = await self._run(
            rounds,
            message="Avoid the Q",
            tool_registry=_test_registry(),
            trace=trace,
        )

        self.assertEqual(trace.tool_calls[0][1]["excluded_route_ids"], ["Q"])
        self.assertEqual(
            session["slots"]["constraints"]["excluded_route_ids"],
            ["Q"],
        )

    async def test_intent_tool_profiles_stay_within_provider_schema_limit(self):
        def optional_parameter_count(schema):
            if not schema:
                return 0
            properties = schema.get("properties") or {}
            required = set(schema.get("required") or [])
            return (
                sum(name not in required for name in properties)
                + sum(
                    optional_parameter_count(value)
                    for value in properties.values()
                    if isinstance(value, dict)
                )
                + optional_parameter_count(schema.get("items") or {})
            )

        cases = (
            (
                "Plan a trip to Coney Island with less walking",
                {
                    "get_place_details",
                    "prepare_route_options",
                    "present_route",
                    "accessibility_status",
                },
            ),
            (
                "route me there please",
                {
                    "get_place_details",
                    "prepare_route_options",
                    "present_route",
                    "accessibility_status",
                },
            ),
            ("When is the next Q train?", {"lookup_arrivals"}),
            (
                "Find a good pizza place",
                {
                    "search_local_places",
                    "prepare_route_options",
                    "present_route",
                    "accessibility_status",
                    "web_search",
                },
            ),
            (
                "lets get some L'Industrie now",
                {
                    "search_local_places",
                    "prepare_route_options",
                    "present_route",
                    "accessibility_status",
                    "web_search",
                },
            ),
            (
                "Are there events at Barclays Center tonight?",
                {
                    "check_area_conditions",
                },
            ),
            (
                "How much is the subway fare?",
                {"lookup_facts"},
            ),
            (
                "How much is the fare, is the Q delayed, is the elevator "
                "accessible, what events are at Barclays, and what are the "
                "latest news reports?",
                {
                    "transit_snapshot",
                    "event_lookup",
                    "venue_crowd_window",
                    "accessibility_status",
                    "lookup_facts",
                    "web_search",
                },
            ),
            ("Hello", set()),
        )
        for message, expected_tools in cases:
            with self.subTest(message=message):
                parsed_intent = self.loop.intelligence.parse_intent(message)
                schemas = self.loop._tools_for_intent(
                    parsed_intent, message=message
                )
                total = sum(
                    optional_parameter_count(schema.get("input_schema"))
                    for schema in schemas
                )
                # Anthropic rejects a request before its first token when the
                # offered custom schemas exceed 24 optional parameters.
                # Measured counts: route_planning 23
                # (prepare_route_options 17, get_place_details 4,
                # present_route 1, accessibility_status 1);
                # destination_discovery 22 (route execution 19 plus
                # search_local_places 3 and web_search 0). Later discovery
                # references receive get_place_details on their own surface.
                # transit-question facets are now smaller than the route
                # profile and are asserted exactly below.
                self.assertLessEqual(total, 24)
                self.assertEqual(
                    expected_tools,
                    {schema["name"] for schema in schemas},
                )

        simple_intent = types.SimpleNamespace(intent="simple_general")
        followup_surfaces = [
            self.loop._tools_for_intent(
                simple_intent,
                scenario_action=action,
            )
            for action in (
                self.loop.scenario_followup.ScenarioAction.ACCEPT,
                self.loop.scenario_followup.ScenarioAction.REJECT,
            )
        ]
        for action in self.loop.discovery_followup.DiscoveryFollowupAction:
            with patch.object(
                self.loop.discovery_followup,
                "detect_followup_action",
                return_value=action,
            ):
                followup_surfaces.append(
                    self.loop._tools_for_intent(
                        simple_intent,
                        session={},
                        message="test",
                    )
                )
        for schemas in followup_surfaces:
            total = sum(
                optional_parameter_count(schema.get("input_schema"))
                for schema in schemas
            )
            self.assertLessEqual(
                total,
                24,
                [schema.get("name") for schema in schemas],
            )

    async def test_route_planning_uses_a_minimal_tool_profile(self):
        parsed_intent = self.loop.intelligence.parse_intent(
            "Plan a Q route to Coney Island with less walking"
        )

        schemas = self.loop._tools_for_intent(parsed_intent)

        self.assertEqual(
            {schema["name"] for schema in schemas},
            {
                "get_place_details",
                "prepare_route_options",
                "present_route",
                "accessibility_status",
            },
        )

    async def test_venue_crowd_window_is_transit_question_only(self):
        transit_question = self.loop.intelligence.parse_intent(
            "Is there a concert at Barclays tonight?"
        )
        transit_names = {
            schema["name"]
            for schema in self.loop._tools_for_intent(
                transit_question,
                message="Is there a concert at Barclays tonight?",
            )
        }
        self.assertIn("venue_crowd_window", transit_names)
        self.assertIn("event_lookup", transit_names)
        for parsed in (
            self.loop.intelligence.ParsedIntent(
                intent="route_planning", avoid_crowds=False
            ),
            self.loop.intelligence.ParsedIntent(
                intent="destination_discovery", avoid_crowds=False
            ),
        ):
            with self.subTest(intent=parsed.intent):
                names = {
                    schema["name"] for schema in self.loop._tools_for_intent(parsed)
                }
                self.assertNotIn("venue_crowd_window", names)
                self.assertNotIn("plan_trip", names)
                self.assertNotIn("poi_search", names)

    async def test_conversational_route_prepares_compares_and_presents_once(self):
        trace = self.loop.TurnTrace()
        events_out, session = await self._run(
            [
                {
                    "tool_use": [
                        {
                            "id": "prepare-1",
                            "name": "prepare_route_options",
                            "input": {"destination": "Coney Island"},
                        }
                    ],
                    "stop_reason": "tool_use",
                },
                {
                    "tool_use": [
                        {
                            "id": "present-1",
                            "name": "present_route",
                            "input": {"candidate_id": "cd_test_only"},
                        }
                    ],
                    "stop_reason": "tool_use",
                },
                {"text": ["This must not be requested."], "stop_reason": "end_turn"},
            ],
            message="Plan a route to Coney Island",
            tool_registry=_test_registry(),
            trace=trace,
        )

        self.assertEqual(
            [name for name, _tool_input in trace.tool_calls],
            ["prepare_route_options", "present_route"],
        )
        self.assertEqual(len(self.loop.client.messages.calls), 2)
        route_cards = [event for event in events_out if event.type == "route_card"]
        self.assertEqual([event.role for event in route_cards], ["recommended"])
        self.assertEqual(len(session["route_cards"]), 1)
        # The harness explicitly offers the injected fake-registry schemas;
        # the real route-planning surface (which never offers the legacy
        # REST plan_trip) is asserted on the real _tools_for_intent path.
        self.assertEqual(
            {schema["name"] for schema in self.loop.client.messages.calls[0]["tools"]},
            set(_test_registry()),
        )

    async def test_route_rounds_keep_the_selected_outer_model(self):
        rounds = [
            {
                "tool_use": [
                    {
                        "id": "prepare-1",
                        "name": "prepare_route_options",
                        "input": {"destination": "Coney Island"},
                    }
                ],
                "stop_reason": "tool_use",
            },
            {
                "tool_use": [
                    {
                        "id": "present-1",
                        "name": "present_route",
                        "input": {"candidate_id": "cd_test_only"},
                    }
                ],
                "stop_reason": "tool_use",
            },
        ]
        for mode in ("auto", "quick"):
            with self.subTest(mode=mode):
                trace = self.loop.TurnTrace()
                await self._run(
                    rounds,
                    message="Plan a route to Coney Island",
                    response_presentation=mode,
                    tool_registry=_test_registry(),
                    trace=trace,
                )
                expected_model = self.loop.agent_policy.policy_for_mode(mode).model
                self.assertEqual(
                    [call["model"] for call in self.loop.client.messages.calls],
                    [expected_model, expected_model],
                )
                self.assertEqual(
                    [name for name, _tool_input in trace.tool_calls],
                    ["prepare_route_options", "present_route"],
                )

    async def test_what_if_preview_stays_temporary_until_a_later_acceptance(self):
        _discard_id, session = session_module.new_session()
        session["active_trip"] = {"card_id": "rc_active", "role": "recommended"}
        trip_state_module.update_trip_state(
            session,
            origin="Home",
            destination="Work",
            active_candidate_set_id="cs_active",
            selected_candidate_id="cd_active",
        )
        candidate_set_id = "cs_what_if"
        candidate_id = "cd_what_if"

        async def prepare_what_if(tool_input, ctx):
            self.assertTrue(tool_input.get("what_if"))
            trip_state_module.bind_temporary_candidate_set(
                ctx.session,
                candidate_set_id,
                base_candidate_set_id="cs_active",
            )
            return ToolResult(
                ok=True,
                data={
                    "candidate_set_id": candidate_set_id,
                    "route_status": "good",
                    "presentation_allowed": True,
                    "candidates": [{"candidate_id": candidate_id}],
                },
                summary="prepared temporary route",
            )

        async def present_what_if(tool_input, ctx):
            self.assertEqual(tool_input.get("candidate_id"), candidate_id)
            commit = tool_input.get("commit_scenario") is True
            if commit:
                trip_state_module.commit_scenario(
                    ctx.session,
                    candidate_set_id=candidate_set_id,
                    candidate_id=candidate_id,
                    tool_input={"origin": "Home", "destination": "Airport"},
                )
            else:
                trip_state_module.bind_temporary_selected_candidate(
                    ctx.session,
                    candidate_id,
                )
            event = agent_events.RouteCardEvent(
                card_id="rc_what_if",
                turn_id=ctx.turn_id,
                role="recommended",
                origin={"label": "Home", "lat": 40.7, "lng": -73.9},
                destination={"label": "Airport", "lat": 40.64, "lng": -73.78},
                summary={
                    "eta_minutes": 45,
                    "transfers": 1,
                    "lines": ["A"],
                    "reason": "fits",
                },
                route=[{"type": "SUBWAY", "route_id": "A"}],
                alerts=[],
            )
            cards = (
                [{"card_id": "rc_what_if", "role": "recommended"}]
                if commit
                else []
            )
            return ToolResult(
                ok=True,
                data={"passenger_explanation": "Take the A to the airport."},
                summary="presented temporary route",
                events=[event],
                session_route_cards=cards,
            )

        registry = _test_registry()
        registry["prepare_route_options"] = ToolSpec(
            schema={"name": "prepare_route_options"},
            executor=prepare_what_if,
            label_fn=lambda _input: "Preparing routes",
            timeout_s=5.0,
        )
        registry["present_route"] = ToolSpec(
            schema={"name": "present_route"},
            executor=present_what_if,
            label_fn=lambda _input: "Presenting route",
            timeout_s=5.0,
        )

        await self._run(
            [
                {
                    "tool_use": [
                        {
                            "id": "prepare-what-if",
                            "name": "prepare_route_options",
                            "input": {
                                "origin": "Home",
                                "destination": "Airport",
                                "what_if": True,
                            },
                        }
                    ],
                    "stop_reason": "tool_use",
                },
                {
                    "tool_use": [
                        {
                            "id": "present-preview",
                            "name": "present_route",
                            "input": {"candidate_id": candidate_id},
                        }
                    ],
                    "stop_reason": "tool_use",
                },
            ],
            message="What if I went to the airport instead?",
            session=session,
            session_id="sess-what-if",
            tool_registry=registry,
        )
        preview_state = trip_state_module.get_trip_state(session)
        self.assertEqual(preview_state["active_candidate_set_id"], "cs_active")
        self.assertEqual(preview_state["selected_candidate_id"], "cd_active")
        self.assertEqual(preview_state["temporary_candidate_set_id"], candidate_set_id)
        self.assertEqual(preview_state["temporary_selected_candidate_id"], candidate_id)
        self.assertEqual(session["active_trip"]["card_id"], "rc_active")

        await self._run(
            [{"text": ["Keep my original trip."], "stop_reason": "end_turn"}],
            message="Never mind",
            session=session,
            session_id="sess-what-if",
            tool_registry=registry,
        )
        unchanged = trip_state_module.get_trip_state(session)
        self.assertEqual(unchanged["active_candidate_set_id"], "cs_active")
        self.assertEqual(unchanged["selected_candidate_id"], "cd_active")

        await self._run(
            [
                {
                    "tool_use": [
                        {
                            "id": "present-commit",
                            "name": "present_route",
                            "input": {
                                "candidate_id": candidate_id,
                                "commit_scenario": True,
                            },
                        }
                    ],
                    "stop_reason": "tool_use",
                }
            ],
            message="Use the airport route instead",
            session=session,
            session_id="sess-what-if",
            tool_registry=registry,
        )
        committed = trip_state_module.get_trip_state(session)
        self.assertEqual(committed["active_candidate_set_id"], candidate_set_id)
        self.assertEqual(committed["selected_candidate_id"], candidate_id)
        self.assertIsNone(committed["temporary_candidate_set_id"])
        self.assertEqual(session["active_trip"]["card_id"], "rc_what_if")

    async def test_quick_keeps_haiku_when_tool_output_requires_clarification(self):
        rounds = [
            {
                "tool_use": [
                    {
                        "id": "tu_1",
                        "name": "plan_trip",
                        "input": {"destination": "34th Street"},
                    }
                ],
                "stop_reason": "tool_use",
            },
            {"text": ["Which 34th Street stop do you mean?"], "stop_reason": "end_turn"},
        ]
        registry = _test_registry()
        registry["plan_trip"] = ToolSpec(
            schema={"name": "plan_trip"},
            executor=_fake_ambiguous_plan_trip_tool,
            label_fn=lambda _input: "Finding routesâ€¦",
            timeout_s=5.0,
        )
        trace = self.loop.TurnTrace()

        await self._run(
            rounds,
            message="Take me to 34th Street",
            response_presentation="quick",
            tool_registry=registry,
            trace=trace,
        )

        self.assertEqual(trace.initial_mode, "quick")
        self.assertEqual(trace.final_mode, "quick")
        self.assertEqual(
            trace.escalation_reason, "ambiguous_station_or_destination"
        )
        self.assertEqual(len(trace.tool_calls), 1)
        self.assertEqual(
            self.loop.client.messages.calls[1]["model"],
            self.loop.agent_policy.policy_for_mode("quick").model,
        )

    async def test_quick_edge_conditions_never_substitute_sonnet(self):
        cases = (
            (
                "required_tool_failure",
                ToolResult(ok=False, error="provider unavailable"),
            ),
            (
                "mandatory_constraints_unsatisfied",
                ToolResult(ok=False, error="no transit route found"),
            ),
            (
                "conflicting_mandatory_evidence",
                ToolResult(ok=True, data={"conflicting_mandatory_evidence": True}),
            ),
            (
                "effectively_tied_final_scores",
                ToolResult(ok=True, data={"quick_escalation_reason": "effectively_tied_final_scores"}),
            ),
        )
        quick_model = self.loop.agent_policy.policy_for_mode("quick").model
        auto_model = self.loop.agent_policy.policy_for_mode("auto").model

        for expected_reason, result in cases:
            async def edge_plan_trip(_tool_input, _ctx, *, response=result):
                return response

            registry = _test_registry()
            registry["plan_trip"] = ToolSpec(
                schema={"name": "plan_trip"},
                executor=edge_plan_trip,
                label_fn=lambda _input: "Finding routes…",
                timeout_s=5.0,
            )
            trace = self.loop.TurnTrace()
            with self.subTest(reason=expected_reason):
                await self._run(
                    [
                        {"tool_use": [{"id": "tu_1", "name": "plan_trip", "input": {"destination": "Costco"}}], "stop_reason": "tool_use"},
                        {"text": ["I need one more detail."], "stop_reason": "end_turn"},
                    ],
                    message="Plan a trip to Costco",
                    response_presentation="quick",
                    tool_registry=registry,
                    trace=trace,
                )
                models = [call["model"] for call in self.loop.client.messages.calls]
                self.assertEqual(trace.escalation_reason, expected_reason)
                self.assertEqual(trace.final_mode, "quick")
                self.assertEqual(models, [quick_model, quick_model])
                self.assertNotIn(auto_model, models)

    async def test_parallel_tools_return_single_tool_result_message(self):
        rounds = [
            {
                "tool_use": [
                    {"id": "tu_1", "name": "ok_tool", "input": {"a": 1}},
                    {"id": "tu_2", "name": "fail_tool", "input": {"b": 2}},
                ],
                "stop_reason": "tool_use",
            },
            {"text": ["done"], "stop_reason": "end_turn"},
        ]
        events_out, _session = await self._run(rounds, tool_registry=_test_registry())

        tool_starts = [e for e in events_out if e.type == "tool_start"]
        self.assertEqual({e.tool for e in tool_starts}, {"ok_tool", "fail_tool"})
        tool_ends = {e.tool: e for e in events_out if e.type == "tool_end"}
        self.assertTrue(tool_ends["ok_tool"].ok)
        self.assertFalse(tool_ends["fail_tool"].ok)
        self.assertEqual(tool_ends["fail_tool"].summary, "boom")

        second_call_kwargs = self.loop.client.messages.calls[1]
        last_message = second_call_kwargs["messages"][-1]
        self.assertEqual(last_message["role"], "user")
        self.assertEqual(len(last_message["content"]), 2)
        ids = {block["tool_use_id"] for block in last_message["content"]}
        self.assertEqual(ids, {"tu_1", "tu_2"})
        error_blocks = [b for b in last_message["content"] if b.get("is_error")]
        self.assertEqual(len(error_blocks), 1)
        self.assertEqual(error_blocks[0]["tool_use_id"], "tu_2")

    async def test_tool_timeout_produces_is_error_tool_end(self):
        rounds = [
            {"tool_use": [{"id": "tu_1", "name": "slow_tool", "input": {}}], "stop_reason": "tool_use"},
            {"text": ["ok"], "stop_reason": "end_turn"},
        ]
        events_out, _ = await self._run(rounds, tool_registry=_test_registry())
        tool_end = next(e for e in events_out if e.type == "tool_end")
        self.assertFalse(tool_end.ok)
        self.assertEqual(tool_end.summary, "timed out")

    async def test_route_card_events_emitted_and_stored_in_session(self):
        rounds = [
            {"tool_use": [{"id": "tu_1", "name": "plan_trip", "input": {"destination": "Costco"}}], "stop_reason": "tool_use"},
            {"text": ["Here you go"], "stop_reason": "end_turn"},
        ]
        events_out, session = await self._run(rounds, tool_registry=_test_registry())
        route_cards = [e for e in events_out if e.type == "route_card"]
        self.assertEqual(len(route_cards), 1)
        self.assertEqual(route_cards[0].card_id, "rc_test0001")
        self.assertEqual(route_cards[0].turn_id, "t1")
        self.assertEqual(session["route_cards"][0]["card_id"], "rc_test0001")

    async def test_model_acknowledgement_precedes_terminal_plan_trip_without_another_model_round(self):
        async def terminal_plan_trip(tool_input, ctx):
            result = await _fake_plan_trip_tool(tool_input, ctx)
            result.data["passenger_explanation"] = "Take the Q to B in about 20 minutes with no transfers."
            return result

        registry = _test_registry()
        registry["plan_trip"] = ToolSpec(
            schema={"name": "plan_trip"},
            executor=terminal_plan_trip,
            label_fn=lambda _input: "Finding routes…",
            timeout_s=5.0,
        )
        rounds = [
            {
                "text": ["I'll compare your options for Costco."],
                "tool_use": [{"id": "tu_1", "name": "plan_trip", "input": {"destination": "Costco"}}],
                "stop_reason": "tool_use",
            },
            {"text": ["This must not be used"], "stop_reason": "end_turn"},
        ]

        events_out, session = await self._run(rounds, tool_registry=registry)

        event_types = [event.type for event in events_out]
        token_events = [event for event in events_out if event.type == "token"]
        self.assertLess(event_types.index("meta"), event_types.index("token"))
        self.assertLess(event_types.index("token"), event_types.index("tool_start"))
        self.assertLess(event_types.index("tool_start"), event_types.index("tool_end"))
        self.assertLess(event_types.index("tool_end"), event_types.index("route_card"))
        route_card_index = event_types.index("route_card")
        self.assertLess(route_card_index, event_types.index("token", route_card_index + 1))
        self.assertEqual(event_types[-1], "done")
        rider_text = "".join(event.text for event in events_out if event.type == "token")
        self.assertEqual(
            [event.text for event in token_events],
            [
                "I'll compare your options for Costco.",
                "\n\nTake the Q to B in about 20 minutes with no transfers.",
            ],
        )
        self.assertEqual(
            rider_text,
            "I'll compare your options for Costco.\n\nTake the Q to B in about 20 minutes with no transfers.",
        )
        self.assertNotIn("[ROUTE:", rider_text)
        self.assertNotIn("CANDIDATE_ANALYSIS", rider_text)
        self.assertEqual(session["history"][-1]["text"], rider_text)
        self.assertEqual(len(self.loop.client.messages.calls), 1)

    async def test_terminal_plan_trip_injects_input_grounded_acknowledgement_without_model_prose(self):
        async def terminal_plan_trip(tool_input, ctx):
            result = await _fake_plan_trip_tool(tool_input, ctx)
            result.data["passenger_explanation"] = "Take the Q to B in about 20 minutes with no transfers."
            return result

        registry = _test_registry()
        registry["plan_trip"] = ToolSpec(
            schema={"name": "plan_trip"},
            executor=terminal_plan_trip,
            label_fn=lambda _input: "Finding routesâ€¦",
            timeout_s=5.0,
        )
        rounds = [
            {
                "tool_use": [
                    {
                        "id": "tu_1",
                        "name": "plan_trip",
                        "input": {
                            "destination": "Costco",
                            "exclude_modes": ["BUS"],
                            "routing_preference": "LESS_WALKING",
                        },
                    }
                ],
                "stop_reason": "tool_use",
            },
            {"text": ["This must not be used"], "stop_reason": "end_turn"},
        ]

        events_out, _session = await self._run(rounds, tool_registry=registry)

        token_events = [event for event in events_out if event.type == "token"]
        acknowledgement = token_events[0].text
        event_types = [event.type for event in events_out]
        self.assertLess(event_types.index("token"), event_types.index("tool_start"))
        self.assertEqual(
            acknowledgement,
            "I'll plan your trip to Costco (without buses; with less walking).",
        )
        for unsupported_claim in ("route", "arrival", "incident", "service"):
            self.assertNotIn(unsupported_claim, acknowledgement.casefold())
        self.assertEqual(len(self.loop.client.messages.calls), 1)

    async def test_telemetry_emits_before_done_when_client_closes_at_terminal_event(self):
        async def telemetry_plan_trip(tool_input, ctx):
            ctx.telemetry["plan_trip"] = {
                "outcome": "success",
                "leg_count": 1,
                "incident_status": "complete",
                "incident_cache_hit": False,
                "advisor_status": "complete",
            }
            return await _fake_plan_trip_tool(tool_input, ctx)

        registry = _test_registry()
        registry["plan_trip"] = ToolSpec(
            schema={"name": "plan_trip"},
            executor=telemetry_plan_trip,
            label_fn=lambda _input: "Finding routes…",
            timeout_s=5.0,
        )
        self.loop.client.messages._rounds = [
            {
                "tool_use": [
                    {
                        "id": "tu_telemetry",
                        "name": "plan_trip",
                        "input": {"destination": "Costco"},
                    }
                ],
                "stop_reason": "tool_use",
            },
            {"text": ["Here you go"], "stop_reason": "end_turn"},
        ]
        self.loop.client.messages.calls = []
        _discard_id, session = session_module.new_session()
        timeline = []

        def record_print(*args, **_kwargs):
            if args and str(args[0]).startswith("[trip-pipeline]"):
                timeline.append("telemetry")

        with (
            patch.object(self.loop, "TOOL_REGISTRY", registry),
            patch.object(
                self.loop,
                "_tools_for_intent",
                lambda *_args, **_kwargs: _offered_schemas_for_registry(
                    registry
                ),
            ),
            patch("builtins.print", side_effect=record_print) as printed,
        ):
            stream = self.loop.run_agent_turn(
                session=session,
                session_id=secrets.token_hex(8),
                turn_id="t1",
                message="Get me to Costco",
                now_et="2026-07-15T21:00:00-04:00",
            )
            while True:
                event = await anext(stream)
                timeline.append(event.type)
                if event.type == "done":
                    break
            await stream.aclose()

        telemetry_prints = [
            call
            for call in printed.call_args_list
            if call.args and str(call.args[0]).startswith("[trip-pipeline]")
        ]
        self.assertEqual(len(telemetry_prints), 1)
        self.assertIs(telemetry_prints[0].kwargs.get("flush"), True)
        self.assertLess(timeline.index("telemetry"), timeline.index("done"))
        self.assertIn('"mode":"auto"', str(telemetry_prints[0].args[0]))
        self.assertIn("I'd take the Q", session["history"][-1]["text"])
        self.assertEqual(len(self.loop.client.messages.calls), 1)

    async def test_no_bus_language_is_enforced_at_the_plan_trip_boundary(self):
        rounds = [
            {"tool_use": [{"id": "tu_1", "name": "plan_trip", "input": {"destination": "Costco"}}], "stop_reason": "tool_use"},
            {"text": ["Take the Q."], "stop_reason": "end_turn"},
        ]
        trace = self.loop.TurnTrace()

        _events, session = await self._run(
            rounds,
            message="Heading to Costco, no bus",
            tool_registry=_test_registry(),
            trace=trace,
        )

        self.assertEqual(trace.tool_calls[0][1]["exclude_modes"], ["BUS"])
        self.assertEqual(session["slots"]["constraints"]["exclude_modes"], ["BUS"])

    async def test_rider_can_explicitly_allow_bus_again(self):
        _discard_id, session = session_module.new_session()
        session["slots"] = {"constraints": {"exclude_modes": ["BUS"]}}
        rounds = [
            {"tool_use": [{"id": "tu_1", "name": "plan_trip", "input": {"destination": "Costco"}}], "stop_reason": "tool_use"},
            {"text": ["Take the B35."], "stop_reason": "end_turn"},
        ]
        trace = self.loop.TurnTrace()

        await self._run(
            rounds,
            message="Bus is okay now",
            session=session,
            tool_registry=_test_registry(),
            trace=trace,
        )

        self.assertNotIn("exclude_modes", trace.tool_calls[0][1])

    async def test_route_card_turn_gets_grounded_text_when_model_returns_no_prose(self):
        rounds = [
            {"tool_use": [{"id": "tu_1", "name": "plan_trip", "input": {"destination": "Costco"}}], "stop_reason": "tool_use"},
            {"text": [], "stop_reason": "end_turn"},
        ]

        events_out, session = await self._run(rounds, tool_registry=_test_registry())

        prose = "".join(event.text for event in events_out if event.type == "token")
        self.assertIn("I'd take the Q", prose)
        self.assertIn("20 minutes", prose)
        self.assertIn("no transfers", prose)
        self.assertIn("fastest option", prose)
        self.assertEqual(session["history"][-1]["role"], "assistant")
        self.assertEqual(session["history"][-1]["text"], prose)

    async def test_trace_records_tool_calls_and_final_text(self):
        trace = self.loop.TurnTrace()
        rounds = [
            {"tool_use": [{"id": "tu_1", "name": "ok_tool", "input": {"x": 1}}], "stop_reason": "tool_use"},
            {"text": ["final answer"], "stop_reason": "end_turn"},
        ]
        await self._run(rounds, tool_registry=_test_registry(), trace=trace)
        self.assertEqual(trace.tool_calls, [("ok_tool", {"x": 1})])
        self.assertEqual(trace.final_text, "final answer")
        self.assertEqual(trace.model_call_count, 2)

    async def test_simple_arithmetic_skips_model_and_tools(self):
        events_out, session = await self._run([], message="What is 5 + 5?")
        self.assertEqual(len(self.loop.client.messages.calls), 0)
        self.assertEqual(
            "".join(event.text for event in events_out if event.type == "token"),
            "10.",
        )
        self.assertEqual(session["history"][-1]["text"], "10.")

    async def test_resolved_arrival_uses_active_trip_and_skips_every_model_call(self):
        _session_id, session = session_module.new_session()
        session["active_trip"] = {
            "first_boarding": {
                "route_id": "q",
                "stop_id": "D28",
                "stop_name": "Church Av",
                "direction_id": 1,
                "direction_label": "Coney Island-Stillwell Av",
                "destination_stop_id": "D43",
            }
        }
        trace = self.loop.TurnTrace()
        events_out, _session = await self._run(
            [],
            message="When does the next q arrive?",
            session=session,
            response_presentation="quick",
            tool_registry=_test_registry(),
            trace=trace,
        )
        self.assertEqual(trace.tool_calls[0][0], "lookup_arrivals")
        self.assertEqual(trace.tool_calls[0][1]["limit"], 2)
        self.assertFalse(any(name == "plan_trip" for name, _ in trace.tool_calls))
        arrival_event = next(event for event in events_out if event.type == "arrival_card")
        self.assertEqual(arrival_event.resolution_status, "resolved")
        self.assertEqual(len(self.loop.client.messages.calls), 0)
        self.assertEqual(
            "".join(event.text for event in events_out if event.type == "token"),
            "The next downtown Q train at Newkirk Plaza is in 4 minutes.",
        )
        self.assertEqual([event.type for event in events_out].count("done"), 1)
        self.assertEqual(events_out[-1].stop_reason, "end_turn")
        self.assertEqual(events_out[-1].terminal_state, "completed")
        self.assertEqual(trace.model_call_count, 0)
        self.assertEqual(trace.tool_call_count, 1)
        self.assertEqual(trace.retry_count, 0)
        self.assertGreaterEqual(trace.stage_ms["arrival_lookup_ms"], 0)

    async def test_implicit_arrival_followup_uses_active_first_boarding(self):
        _session_id, session = session_module.new_session()
        session["active_trip"] = {
            "first_boarding": {
                "route_id": "2",
                "stop_id": "247",
                "stop_name": "Church Av",
                "direction_id": 2,
                "direction_label": "Manhattan",
                "destination_stop_id": "120",
            }
        }
        trace = self.loop.TurnTrace()

        events_out, _session = await self._run(
            [],
            message="when is the next arrival",
            session=session,
            tool_registry=_test_registry(),
            trace=trace,
        )

        self.assertEqual(trace.tool_calls[0][0], "lookup_arrivals")
        self.assertEqual(trace.tool_calls[0][1]["route_id"], "2")
        self.assertEqual(len(self.loop.client.messages.calls), 0)
        self.assertEqual([event.type for event in events_out].count("done"), 1)
        self.assertFalse(any(event.type == "error" for event in events_out))

    async def test_implicit_arrival_without_active_trip_clarifies_without_model(self):
        events_out, _session = await self._run(
            [],
            message="when is the next arrival",
            tool_registry=_test_registry(),
        )

        self.assertEqual(len(self.loop.client.messages.calls), 0)
        self.assertEqual(
            "".join(event.text for event in events_out if event.type == "token"),
            "Which train or bus route do you want arrivals for?",
        )
        self.assertEqual(events_out[-1].terminal_state, "clarification_required")
        self.assertFalse(any(event.type == "error" for event in events_out))

    async def test_arrival_clarification_is_terminal_and_never_becomes_a_generic_error(self):
        registry = _test_registry()
        registry["lookup_arrivals"] = ToolSpec(
            schema={"name": "lookup_arrivals"},
            executor=_fake_arrival_clarification_tool,
            label_fn=lambda i: f"Checking {i.get('route_id')} arrivals",
            timeout_s=5.0,
        )

        events_out, _session = await self._run(
            [],
            message="When does the next Q arrive at 34 St?",
            tool_registry=registry,
        )

        self.assertEqual(len(self.loop.client.messages.calls), 0)
        arrival_event = next(event for event in events_out if event.type == "arrival_card")
        self.assertEqual(arrival_event.resolution_status, "ambiguous")
        self.assertFalse(any(event.type == "error" for event in events_out))
        self.assertEqual([event.type for event in events_out].count("done"), 1)
        self.assertEqual(events_out[-1].stop_reason, "clarification_required")
        self.assertEqual(events_out[-1].terminal_state, "clarification_required")

    async def test_station_only_arrival_clarification_can_resume_lookup(self):
        clarification_registry = _test_registry()
        clarification_registry["lookup_arrivals"] = ToolSpec(
            schema={"name": "lookup_arrivals"},
            executor=_fake_arrival_clarification_tool,
            label_fn=lambda i: f"Checking {i.get('route_id')} arrivals",
            timeout_s=5.0,
        )
        first_events, session = await self._run(
            [],
            message="When does the next Q arrive at 34 St?",
            tool_registry=clarification_registry,
        )
        self.assertEqual(
            first_events[-1].terminal_state,
            "clarification_required",
        )

        trace = self.loop.TurnTrace()
        await self._run(
            [
                {
                    "tool_use": [
                        {
                            "id": "tu_arrival_followup",
                            "name": "lookup_arrivals",
                            "input": {
                                "route_id": "Q",
                                "stop_query": "34 St-Herald Sq",
                            },
                        }
                    ],
                    "stop_reason": "tool_use",
                },
                {
                    "text": ["The next downtown Q is in 4 minutes."],
                    "stop_reason": "end_turn",
                },
            ],
            message="34 St-Herald Sq",
            session=session,
            tool_registry=_test_registry(),
            trace=trace,
        )

        first_model_call = self.loop.client.messages.calls[0]
        self.assertIn(
            "lookup_arrivals",
            {schema["name"] for schema in first_model_call["tools"]},
        )
        self.assertEqual(trace.tool_calls[0][0], "lookup_arrivals")

    def test_stale_arrival_copy_preserves_the_prediction_and_its_freshness(self):
        text, stop_reason = self.loop._arrival_response(
            {
                "route_id": "Q",
                "stop": {"name": "Church Av"},
                "source_status": "stale",
                "directions": [
                    {
                        "id": "downtown",
                        "arrivals": [{"minutes": 4}],
                    }
                ],
            }
        )

        self.assertEqual(stop_reason, "end_turn")
        self.assertEqual(
            text,
            "The latest available downtown Q prediction at Church Av is 4 minutes, "
            "but live data is stale.",
        )

    def test_arrival_copy_skips_a_due_prediction(self):
        text, stop_reason = self.loop._arrival_response(
            {
                "route_id": "Q",
                "stop": {"name": "Church Av"},
                "source_status": "live",
                "directions": [
                    {
                        "id": "downtown",
                        "arrivals": [{"minutes": 0}, {"minutes": 8}, {"minutes": 14}],
                    }
                ],
            }
        )

        self.assertEqual(stop_reason, "end_turn")
        self.assertEqual(
            text,
            "The next downtown Q train at Church Av is in 8 minutes.",
        )

    async def test_destination_discovery_is_model_directed_and_grounded_in_both_modes(self):
        for mode, expected_limit in (("auto", 3), ("quick", 2)):
            with self.subTest(mode=mode):
                # Other test classes reload the module with narrow deadline
                # fixtures; use a fresh fake client for each presentation.
                self.loop = _load_agent_loop()
                trace = self.loop.TurnTrace()
                with patch.object(self.loop, "AGENT_TURN_DEADLINE_S", 60), patch.object(
                    self.loop.budget, "AGENT_DAILY_SPEND_LIMIT_USD", 5
                ):
                    events_out, _session = await self._run(
                        [
                            {
                                "tool_use": [
                                    {
                                        "id": "poi-1",
                                        "name": "search_local_places",
                                        "input": {
                                            "query": "pizza Brooklyn",
                                            "max_results": expected_limit,
                                        },
                                    }
                                ],
                                "stop_reason": "tool_use",
                            },
                            {
                                "text": ["One strong grounded option is Di Fara Pizza."],
                                "stop_reason": "end_turn",
                            },
                        ],
                        message="What is one of the best pizza places in Brooklyn?",
                        response_presentation=mode,
                        tool_registry=_test_registry(),
                        trace=trace,
                    )
                self.assertTrue(
                    trace.tool_calls,
                    f"events={[event.type for event in events_out]} "
                    f"errors={[getattr(event, 'code', '') for event in events_out if event.type == 'error']} "
                    f"model_calls={len(self.loop.client.messages.calls)}",
                )
                self.assertEqual(trace.tool_calls[0][0], "search_local_places")
                self.assertEqual(
                    trace.tool_calls[0][1]["max_results"],
                    expected_limit,
                )
                # The harness explicitly offers the injected fake-registry
                # schemas, so the real discovery surface (including the
                # native web_search appended by the intent policy) is
                # asserted on the real _tools_for_intent path.
                self.assertIn(
                    "search_local_places",
                    {
                        schema["name"]
                        for schema in self.loop.client.messages.calls[0]["tools"]
                    },
                )
                parsed_intent = self.loop.intelligence.parse_intent(
                    "What is one of the best pizza places in Brooklyn?"
                )
                schemas = self.loop._tools_for_intent(
                    parsed_intent,
                    self.loop.agent_policy.policy_for_mode(mode),
                )
                names = {schema["name"] for schema in schemas}
                self.assertEqual(
                    names,
                    {
                        "search_local_places",
                        "prepare_route_options",
                        "present_route",
                        "accessibility_status",
                        "web_search",
                    },
                )
                web_schema = next(
                    schema for schema in schemas if schema["name"] == "web_search"
                )
                self.assertEqual(
                    web_schema["max_uses"],
                    2 if mode == "quick" else 3,
                )
                self.assertEqual(
                    web_schema["user_location"]["city"],
                    "New York City",
                )
                self.assertIn(
                    "Di Fara Pizza",
                    self.loop.client.messages.calls[1]["messages"][-1]["content"][0][
                        "content"
                    ],
                )

    async def test_failed_trip_resume_offer_is_added_once_without_auto_retry(self):
        _session_id, session = session_module.new_session()
        session_module.mark_pending_trip_failed(
            session,
            {"destination": "JFK"},
            "routing timed out",
        )
        first, session = await self._run([], message="5 + 5", session=session)
        second, _session = await self._run([], message="2 + 2", session=session)
        first_text = "".join(event.text for event in first if event.type == "token")
        second_text = "".join(event.text for event in second if event.type == "token")
        self.assertIn("retry the trip to JFK", first_text)
        self.assertEqual(second_text, "4.")
        self.assertEqual(len(self.loop.client.messages.calls), 0)


class RoundCapTests(_AgentLoopHelpers, unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        cls.loop = _load_agent_loop({"AGENT_AUTO_MAX_ROUNDS": "2", "AGENT_TURN_DEADLINE_S": "60"})

    def setUp(self):
        cache._mem.clear()

    async def test_round_cap_triggers_wrapup_with_tool_choice_none(self):
        # Every real round asks for another tool call -- the model never
        # naturally stops, so the cap must kick in after 2 rounds.
        rounds = [
            {"tool_use": [{"id": "tu_1", "name": "ok_tool", "input": {}}], "stop_reason": "tool_use"},
            {"tool_use": [{"id": "tu_2", "name": "ok_tool", "input": {}}], "stop_reason": "tool_use"},
            {"text": ["here is what I know so far"], "stop_reason": "end_turn"},
        ]
        with patch.dict(os.environ, {"AGENT_AUTO_MAX_ROUNDS": "2"}):
            events_out, _session = await self._run(rounds, tool_registry=_test_registry())

        self.assertEqual(len(self.loop.client.messages.calls), 3)
        wrapup_kwargs = self.loop.client.messages.calls[-1]
        self.assertEqual(wrapup_kwargs["tool_choice"], {"type": "none"})
        self.assertEqual(wrapup_kwargs["max_tokens"], 300)
        self.assertNotIn("tools", wrapup_kwargs)

        done = events_out[-1]
        self.assertEqual(done.type, "done")
        self.assertEqual(done.stop_reason, "max_rounds")


class DeadlineTests(_AgentLoopHelpers, unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        # A deadline in the past trips on the very first check, before any
        # real round -- deterministic without needing to fake wall-clock time.
        cls.loop = _load_agent_loop({"AGENT_MAX_ROUNDS": "50", "AGENT_TURN_DEADLINE_S": "-1"})

    def setUp(self):
        cache._mem.clear()

    async def test_deadline_exceeded_before_first_round_never_starts_wrapup(self):
        events_out, _session = await self._run([], tool_registry=_test_registry())

        self.assertEqual(len(self.loop.client.messages.calls), 0)
        done = events_out[-1]
        self.assertEqual(done.stop_reason, "deadline")

    async def test_near_deadline_tool_is_cancelled_and_returns_deadline_terminal(self):
        async def slow_arrivals(_tool_input, _ctx):
            await asyncio.sleep(0.05)
            return ToolResult(ok=True, data={"source_status": "available"})

        registry = _test_registry()
        registry["lookup_arrivals"] = ToolSpec(
            schema={"name": "lookup_arrivals"},
            executor=slow_arrivals,
            label_fn=lambda _input: "Checking arrivals",
            timeout_s=5.0,
        )
        with patch.object(self.loop, "AGENT_TURN_DEADLINE_S", 0.01):
            events_out, _session = await self._run(
                [],
                message="When is the next Q train?",
                tool_registry=registry,
            )
        self.assertEqual([event.type for event in events_out].count("done"), 1)
        self.assertEqual(events_out[-1].stop_reason, "deadline")
        self.assertEqual(
            [event.code for event in events_out if event.type == "error"], ["deadline"]
        )
        self.assertTrue(any(event.type == "tool_start" for event in events_out))
        self.assertTrue(any(event.type == "tool_end" and not event.ok for event in events_out))

    async def test_grounded_route_card_completes_without_a_followup_model_round(self):
        calls = 0

        async def scripted_stream(**stream_kwargs):
            nonlocal calls
            calls += 1
            if calls == 1:
                message = types.SimpleNamespace(
                    content=[types.SimpleNamespace(type="tool_use", id="plan-1", name="plan_trip", input={})],
                    stop_reason="tool_use",
                    usage=types.SimpleNamespace(input_tokens=1, output_tokens=1),
                )
                yield self.loop.model_stream.ModelCallCompleted(message, None, 1)
                return
            remaining = max(0.0, stream_kwargs["deadline_monotonic"] - time.monotonic())
            await asyncio.sleep(remaining + 0.001)
            yield self.loop.model_stream.ModelCallCompleted(
                None,
                agent_events.ErrorEvent(code="deadline", message="timed out", retryable=True),
                1,
            )

        started = time.monotonic()
        with patch.object(self.loop, "AGENT_TURN_DEADLINE_S", 0.2), patch.object(
            self.loop.model_stream, "stream_model_call", scripted_stream
        ):
            events_out, session = await self._run([], message="Plan a trip", tool_registry=_test_registry())

        self.assertLess(time.monotonic() - started, 0.5)
        self.assertEqual(calls, 1)
        self.assertEqual(len([event for event in events_out if event.type == "done"]), 1)
        self.assertEqual(events_out[-1].stop_reason, "end_turn")
        self.assertTrue(any(event.type == "route_card" and event.card_id == "rc_test0001" for event in events_out))
        self.assertTrue(any("takes about 20 min" in event.text for event in events_out if event.type == "token"))
        self.assertTrue(any(card["card_id"] == "rc_test0001" for card in session["route_cards"]))


class AgentDisabledBudgetTests(_AgentLoopHelpers, unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        cls.loop = _load_agent_loop()

    def setUp(self):
        cache._mem.clear()

    async def test_agent_enabled_false_short_circuits_before_any_model_call(self):
        with patch.dict(os.environ, {"AGENT_ENABLED": "0"}):
            events_out, _session = await self._run([])
        self.assertEqual(events_out[0].type, "meta")
        error = next(e for e in events_out if e.type == "error")
        self.assertEqual(error.code, "budget_exceeded")
        self.assertEqual(events_out[-1].type, "done")
        self.assertEqual(len(self.loop.client.messages.calls), 0)


class MockAgentModeTests(_AgentLoopHelpers, unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        cls.loop = _load_agent_loop({"SMARTROUTE_ENV": "test", "AGENT_MOCK_MODE": "1"})

    def setUp(self):
        cache._mem.clear()

    async def test_mock_mode_streams_preview_events_without_a_model_call(self):
        with patch.dict(os.environ, {"AGENT_MOCK_STEP_DELAY_MS": "0"}):
            events_out, session = await self._run([], message="Heading to Costco with a cart")

        event_types = [event.type for event in events_out]
        self.assertEqual(event_types[0], "meta")
        self.assertEqual(event_types[-1], "done")
        self.assertIn("tool_start", event_types)
        self.assertIn("tool_end", event_types)
        self.assertIn("token", event_types)
        self.assertIn("route_card", event_types)
        self.assertEqual(len(self.loop.client.messages.calls), 0)
        self.assertIn("preview", "".join(event.text for event in events_out if event.type == "token").casefold())
        self.assertEqual(session["route_cards"][-1]["card_id"], "mock-t1")

    async def test_quick_mock_copy_is_shorter_without_changing_route_facts(self):
        automatic = self.loop._mock_trip_copy("Heading to Costco", "auto")
        quick = self.loop._mock_trip_copy("Heading to Costco", "quick")

        self.assertLess(len(quick[0]), len(automatic[0]))
        self.assertEqual(quick[1:], automatic[1:])


class RateLimitBudgetTests(_BudgetConfiguredAgentLoopTests):
    BUDGET_ENV = {"AGENT_TURNS_PER_SESSION_PER_MIN": "1"}

    def setUp(self):
        cache._mem.clear()

    async def test_second_turn_in_the_same_minute_is_rate_limited(self):
        session_id = "rate-limit-fixed-session"
        first, _session = await self._run([{"text": ["ok"], "stop_reason": "end_turn"}], session_id=session_id)
        self.assertEqual(first[-1].stop_reason, "end_turn")

        second, _session2 = await self._run([], session_id=session_id)
        error = next(e for e in second if e.type == "error")
        self.assertEqual(error.code, "rate_limited")
        self.assertEqual(len(self.loop.client.messages.calls), 0)


class DailySpendBudgetTests(_BudgetConfiguredAgentLoopTests):
    BUDGET_ENV = {"AGENT_DAILY_SPEND_LIMIT_USD": "0.000001"}

    def setUp(self):
        cache._mem.clear()

    async def test_daily_spend_over_limit_blocks_the_next_turn(self):
        self.budget.record_usage_cost(1000, 1000)  # trivially exceeds the tiny limit
        events_out, _session = await self._run([])
        error = next(e for e in events_out if e.type == "error")
        self.assertEqual(error.code, "budget_exceeded")
        self.assertFalse(error.retryable)
        self.assertEqual(len(self.loop.client.messages.calls), 0)


class ConcurrencyBudgetTests(_BudgetConfiguredAgentLoopTests):
    BUDGET_ENV = {"AGENT_MAX_CONCURRENT_STREAMS": "1"}

    def setUp(self):
        cache._mem.clear()

    async def test_concurrency_semaphore_rejects_when_the_single_slot_is_taken(self):
        sem = self.budget.concurrency_semaphore()
        await sem.acquire()
        try:
            events_out, _session = await self._run([])
        finally:
            sem.release()
        error = next(e for e in events_out if e.type == "error")
        self.assertEqual(error.code, "rate_limited")
        self.assertEqual(len(self.loop.client.messages.calls), 0)


class DeterministicAndDeduplicationTests(_AgentLoopHelpers, unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        cls.loop = _load_agent_loop()

    def setUp(self):
        cache._mem.clear()

    async def test_clear_simple_and_off_topic_turns_use_no_model_or_tools(self):
        cases = {
            "hello": "Hi — I can plan NYC subway and bus trips, check arrivals, and explain service changes.",
            "thanks": "You’re welcome.",
            "help": "Tell me where you’re starting and going, or ask about a train or bus arrival.",
            "2 + 2": "4.",
            "tell me a joke": "SmartRoute is for NYC transit help. I can plan a subway or bus trip, compare routes, or check arrivals.",
        }
        for message, expected in cases.items():
            with self.subTest(message=message):
                trace = self.loop.TurnTrace()
                events_out, _session = await self._run([], message=message, trace=trace)
                self.assertEqual("".join(e.text for e in events_out if e.type == "token"), expected)
                self.assertEqual(trace.model_call_count, 0)
                self.assertEqual(trace.tool_call_count, 0)
                self.assertEqual(len(self.loop.client.messages.calls), 0)

    async def test_ambiguous_transit_question_remains_model_backed(self):
        events_out, _session = await self._run(
            [{"text": ["Which line are you asking about?"], "stop_reason": "end_turn"}],
            message="Is the subway running?",
        )
        self.assertTrue(any(event.type == "token" for event in events_out))
        self.assertEqual(len(self.loop.client.messages.calls), 1)

    async def test_greeting_thanks_help_and_off_topic_paraphrases_stay_deterministic(self):
        for message in ("good morning", "thank you so much", "what can you do", "write me a poem"):
            with self.subTest(message=message):
                trace = self.loop.TurnTrace()
                events_out, _session = await self._run([], message=message, trace=trace)
                self.assertTrue(any(event.type == "token" for event in events_out))
                self.assertEqual(trace.model_call_count, 0)
                self.assertEqual(trace.tool_call_count, 0)

    async def test_turn_ledger_reuses_successes_and_retries_failures(self):
        calls = {"success": 0, "failure": 0}

        async def succeeds(tool_input, _ctx):
            calls["success"] += 1
            value = tool_input["value"]
            return ToolResult(
                ok=True,
                data={"value": value},
                summary=f"ok-{value}",
                events=[agent_events.TokenEvent(text=f"effect-{value}")],
                session_route_cards=[{"card_id": f"card-{value}", "role": "recommended"}],
                timings={"render_ms": 5},
            )

        async def fails_then_succeeds(tool_input, _ctx):
            calls["failure"] += 1
            return ToolResult(ok=calls["failure"] == 2, data={"retry": True}, error="retry")

        registry = {
            "success": ToolSpec(schema={"name": "success"}, executor=succeeds, label_fn=lambda _i: "Working", timeout_s=5),
            "failure": ToolSpec(schema={"name": "failure"}, executor=fails_then_succeeds, label_fn=lambda _i: "Working", timeout_s=5),
        }
        rounds = [
            {"tool_use": [
                {"id": "a", "name": "success", "input": {"value": 1, "other": 2}},
                {"id": "b", "name": "success", "input": {"other": 2, "value": 1}},
            ], "stop_reason": "tool_use"},
            {"tool_use": [
                {"id": "c", "name": "success", "input": {"value": 1, "other": 2}},
                {"id": "d", "name": "success", "input": {"value": 2, "other": 2}},
                {"id": "e", "name": "failure", "input": {"value": 1}},
            ], "stop_reason": "tool_use"},
            {"tool_use": [{"id": "f", "name": "failure", "input": {"value": 1}}], "stop_reason": "tool_use"},
            {"text": ["done"], "stop_reason": "end_turn"},
        ]
        trace = self.loop.TurnTrace()
        events_out, session = await self._run(rounds, tool_registry=registry, trace=trace)

        self.assertEqual(calls, {"success": 2, "failure": 2})
        self.assertEqual([event.text for event in events_out if event.type == "token"], ["effect-1", "effect-2", "done"])
        self.assertEqual([entry["text"] for entry in session["history"] if entry["role"] == "tool"], ["ok-1", "ok-2"])
        self.assertEqual([card["card_id"] for card in session["route_cards"]], ["card-1", "card-2"])
        self.assertEqual(trace.stage_ms["render_ms"], 10)
        self.assertEqual(len(trace.tool_calls), 6)
        self.assertEqual(trace.model_tool_use_count, 6)
        self.assertEqual(trace.provider_tool_execution_count, 4)
        self.assertEqual(len([event for event in events_out if event.type == "tool_end"]), 6)

    async def test_turn_ledger_caps_block_provider_work_at_the_boundaries(self):
        calls = 0

        async def fake_run(*_args, **_kwargs):
            nonlocal calls
            calls += 1
            return ToolResult(ok=True, data={})

        ledger = self.loop.TurnToolLedger()
        with patch.object(self.loop, "MAX_TOOL_EXECUTIONS_PER_TURN", 2), patch.object(
            self.loop, "MAX_TOOL_EXECUTIONS_PER_NAME", 1
        ), patch.object(self.loop, "_run_one_tool", fake_run):
            self.assertTrue((await ledger.execute("one", {"value": 1}, ToolContext(), deadline_monotonic=999999)).ok)
            self.assertFalse((await ledger.execute("one", {"value": 2}, ToolContext(), deadline_monotonic=999999)).ok)
            self.assertTrue((await ledger.execute("two", {"value": 1}, ToolContext(), deadline_monotonic=999999)).ok)
            self.assertFalse((await ledger.execute("three", {"value": 1}, ToolContext(), deadline_monotonic=999999)).ok)

        self.assertEqual(calls, 2)


class AgentBudgetIsolationTests(unittest.TestCase):
    def test_budget_classes_are_order_independent_across_repeated_runs(self):
        budget_module = importlib.import_module("app.services.agent.budget")
        original_limits = (
            budget_module.AGENT_TURNS_PER_SESSION_PER_MIN,
            budget_module.AGENT_DAILY_SPEND_LIMIT_USD,
            budget_module.AGENT_MAX_CONCURRENT_STREAMS,
        )
        selected_classes = [
            (RateLimitBudgetTests, "test_second_turn_in_the_same_minute_is_rate_limited"),
            (DailySpendBudgetTests, "test_daily_spend_over_limit_blocks_the_next_turn"),
            (ConcurrencyBudgetTests, "test_concurrency_semaphore_rejects_when_the_single_slot_is_taken"),
            (LoopMechanicsTests, "test_destination_discovery_is_model_directed_and_grounded_in_both_modes"),
        ]

        orders = []
        for seed in (17, 41):
            ordered_classes = list(selected_classes)
            random.Random(seed).shuffle(ordered_classes)
            orders.append([test_case.__name__ for test_case, _method in ordered_classes])
            suite = unittest.TestSuite(
                test_case(method) for test_case, method in ordered_classes
            )
            result = unittest.TestResult()
            suite.run(result)
            self.assertTrue(
                result.wasSuccessful(),
                f"seed={seed} failures={result.failures} errors={result.errors}",
            )
            self.assertEqual(
                (
                    budget_module.AGENT_TURNS_PER_SESSION_PER_MIN,
                    budget_module.AGENT_DAILY_SPEND_LIMIT_USD,
                    budget_module.AGENT_MAX_CONCURRENT_STREAMS,
                ),
                original_limits,
            )

        self.assertNotEqual(orders[0], orders[1])


if __name__ == "__main__":
    unittest.main()
