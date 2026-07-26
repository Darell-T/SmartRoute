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
import os
import secrets
import sys
import unittest
from unittest.mock import patch

from app.services.agent import session as session_module
from app.services.agent.tools import ToolResult, ToolSpec
from app.services.agent import events as agent_events
from app.utils import cache
from tests._fake_anthropic import reload_agent_loop_module


def _load_agent_loop(env: dict | None = None):
    # See _fake_anthropic.reload_agent_loop_module's docstring for why this
    # is a manual sys.modules swap rather than patch.dict(sys.modules, ...).
    return reload_agent_loop_module(env=env)


def _reload_budget(env: dict):
    with patch.dict(os.environ, env, clear=False):
        return importlib.reload(sys.modules["app.services.agent.budget"])


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


def _test_registry() -> dict[str, ToolSpec]:
    return {
        "ok_tool": ToolSpec(schema={"name": "ok_tool"}, executor=_fake_ok_tool, label_fn=lambda i: "Doing the thing…", timeout_s=5.0),
        "fail_tool": ToolSpec(schema={"name": "fail_tool"}, executor=_fake_fail_tool, label_fn=lambda i: "Doing the failing thing…", timeout_s=5.0),
        "slow_tool": ToolSpec(schema={"name": "slow_tool"}, executor=_fake_slow_tool, label_fn=lambda i: "Doing the slow thing…", timeout_s=0.05),
        "plan_trip": ToolSpec(schema={"name": "plan_trip"}, executor=_fake_plan_trip_tool, label_fn=lambda i: "Finding routes…", timeout_s=5.0),
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
    }


class _AgentLoopHelpers:
    """Mixin: subclasses set `cls.loop` in setUpClass."""

    async def _run(
        self,
        rounds,
        *,
        message="hi",
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

        patcher = patch.object(self.loop, "TOOL_REGISTRY", tool_registry) if tool_registry is not None else None
        if patcher is not None:
            patcher.start()
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
            if patcher is not None:
                patcher.stop()
        return events_out, session


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

    async def test_quick_crowd_request_escalates_before_planning(self):
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
        self.assertEqual(trace.final_mode, "auto")
        self.assertEqual(trace.tool_calls[0][1]["crowd_search_mode"], "auto")

    async def test_explicit_route_request_is_injected_into_plan_trip(self):
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
                {"plan_trip", "accessibility_status"},
            ),
            ("When is the next Q train?", {"lookup_arrivals"}),
            ("Find a good pizza place", {"poi_search", "plan_trip"}),
            ("Are there events at Barclays Center tonight?", {"event_lookup"}),
        )
        for message, expected_tools in cases:
            with self.subTest(message=message):
                parsed_intent = self.loop.intelligence.parse_intent(message)
                schemas = self.loop._tools_for_intent(parsed_intent)
                total = sum(
                    optional_parameter_count(schema["input_schema"])
                    for schema in schemas
                )
                self.assertLessEqual(total, 24)
                self.assertTrue(
                    expected_tools.issubset({schema["name"] for schema in schemas})
                )

    async def test_route_planning_uses_a_minimal_tool_profile(self):
        parsed_intent = self.loop.intelligence.parse_intent(
            "Plan a Q route to Coney Island with less walking"
        )

        schemas = self.loop._tools_for_intent(parsed_intent)

        self.assertEqual(
            {schema["name"] for schema in schemas},
            {"plan_trip", "accessibility_status"},
        )

    async def test_quick_escalates_once_and_reuses_tool_result_context(self):
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
        self.assertEqual(trace.final_mode, "auto")
        self.assertEqual(
            trace.escalation_reason, "ambiguous_station_or_destination"
        )
        self.assertEqual(len(trace.tool_calls), 1)
        self.assertEqual(
            self.loop.client.messages.calls[1]["model"],
            self.loop.agent_policy.policy_for_mode("auto").model,
        )

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

    async def test_destination_discovery_is_grounded_before_model_in_both_modes(self):
        for mode, expected_limit in (("auto", 3), ("quick", 2)):
            with self.subTest(mode=mode):
                trace = self.loop.TurnTrace()
                await self._run(
                    [{"text": ["Try Di Fara Pizza."], "stop_reason": "end_turn"}],
                    message="What is one of the best pizza places in Brooklyn?",
                    response_presentation=mode,
                    tool_registry=_test_registry(),
                    trace=trace,
                )
                self.assertEqual(trace.tool_calls[0][0], "poi_search")
                self.assertEqual(trace.tool_calls[0][1]["max_results"], expected_limit)
                model_context = self.loop.client.messages.calls[0]["messages"][-1]["content"]
                self.assertIn('<required_evidence source="poi_search">', model_context)
                self.assertIn("Di Fara Pizza", model_context)

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

    async def test_deadline_exceeded_before_first_round_goes_straight_to_wrapup(self):
        rounds = [{"text": ["summary of what I know"], "stop_reason": "end_turn"}]
        events_out, _session = await self._run(rounds, tool_registry=_test_registry())

        self.assertEqual(len(self.loop.client.messages.calls), 1)
        kwargs = self.loop.client.messages.calls[0]
        self.assertEqual(kwargs["tool_choice"], {"type": "none"})

        done = events_out[-1]
        self.assertEqual(done.stop_reason, "deadline")


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
        cls.loop = _load_agent_loop({"AGENT_MOCK_MODE": "1"})

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


class RateLimitBudgetTests(_AgentLoopHelpers, unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        cls.loop = _load_agent_loop()
        cls.budget = _reload_budget({"AGENT_TURNS_PER_SESSION_PER_MIN": "1"})

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


class DailySpendBudgetTests(_AgentLoopHelpers, unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        cls.loop = _load_agent_loop()
        cls.budget = _reload_budget({"AGENT_DAILY_SPEND_LIMIT_USD": "0.000001"})

    def setUp(self):
        cache._mem.clear()

    async def test_daily_spend_over_limit_blocks_the_next_turn(self):
        self.budget.record_usage_cost(1000, 1000)  # trivially exceeds the tiny limit
        events_out, _session = await self._run([])
        error = next(e for e in events_out if e.type == "error")
        self.assertEqual(error.code, "budget_exceeded")
        self.assertFalse(error.retryable)
        self.assertEqual(len(self.loop.client.messages.calls), 0)


class ConcurrencyBudgetTests(_AgentLoopHelpers, unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        cls.loop = _load_agent_loop()
        cls.budget = _reload_budget({"AGENT_MAX_CONCURRENT_STREAMS": "1"})

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


if __name__ == "__main__":
    unittest.main()
