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


def _test_registry() -> dict[str, ToolSpec]:
    return {
        "ok_tool": ToolSpec(schema={"name": "ok_tool"}, executor=_fake_ok_tool, label_fn=lambda i: "Doing the thing…", timeout_s=5.0),
        "fail_tool": ToolSpec(schema={"name": "fail_tool"}, executor=_fake_fail_tool, label_fn=lambda i: "Doing the failing thing…", timeout_s=5.0),
        "slow_tool": ToolSpec(schema={"name": "slow_tool"}, executor=_fake_slow_tool, label_fn=lambda i: "Doing the slow thing…", timeout_s=0.05),
        "plan_trip": ToolSpec(schema={"name": "plan_trip"}, executor=_fake_plan_trip_tool, label_fn=lambda i: "Finding routes…", timeout_s=5.0),
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

    async def test_done_last_even_after_upstream_model_error(self):
        events_out, _ = await self._run([{"raise": True}])
        self.assertEqual(events_out[0].type, "meta")
        self.assertEqual(events_out[-1].type, "done")
        self.assertEqual(events_out[-1].stop_reason, "error")
        errors = [e for e in events_out if e.type == "error"]
        self.assertEqual(len(errors), 1)
        self.assertEqual(errors[0].code, "upstream_error")

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

    async def test_trace_records_tool_calls_and_final_text(self):
        trace = self.loop.TurnTrace()
        rounds = [
            {"tool_use": [{"id": "tu_1", "name": "ok_tool", "input": {"x": 1}}], "stop_reason": "tool_use"},
            {"text": ["final answer"], "stop_reason": "end_turn"},
        ]
        await self._run(rounds, tool_registry=_test_registry(), trace=trace)
        self.assertEqual(trace.tool_calls, [("ok_tool", {"x": 1})])
        self.assertEqual(trace.final_text, "final answer")


class RoundCapTests(_AgentLoopHelpers, unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        cls.loop = _load_agent_loop({"AGENT_MAX_ROUNDS": "2", "AGENT_TURN_DEADLINE_S": "60"})

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
