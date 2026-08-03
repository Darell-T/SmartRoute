"""Focused tests for semantic agent progress transport."""

from __future__ import annotations

import asyncio
import time
import types
import unittest

from app.services.agent import events as agent_events
from app.services.agent import policy as agent_policy
from app.services.agent.tool_round import execute_tool_round
from app.services.agent.tools import ToolSpec
from app.services.agent.tools._types import ToolContext, ToolResult


def _parsed_intent() -> types.SimpleNamespace:
    return types.SimpleNamespace(
        avoid_crowds=False,
        requested_route_ids=[],
        required_evidence=types.SimpleNamespace(required_tools=lambda: ()),
    )


def _block() -> types.SimpleNamespace:
    return types.SimpleNamespace(
        id="trip-1",
        name="plan_trip",
        input={"origin": "user", "destination": "Coney Island"},
    )


class _Ledger:
    def __init__(self, executor):
        self.successful = {}
        self._executor = executor

    def key(self, name, tool_input):
        return f"{name}:{tool_input.get('destination', '')}"

    async def execute(self, name, tool_input, ctx, *, deadline_monotonic):
        return await self._executor(tool_input, ctx)


def _registry(executor):
    return {
        "plan_trip": ToolSpec(
            schema={"name": "plan_trip"},
            executor=executor,
            label_fn=lambda _: "Finding routes to Coney Island",
            timeout_s=5,
        )
    }


async def _collect_round(executor, *, ctx=None):
    ctx = ctx or ToolContext(session={})
    items = []
    async for item in execute_tool_round(
        [_block()],
        ctx,
        {},
        [],
        set(),
        agent_policy.policy_for_mode("auto"),
        _parsed_intent(),
        {},
        time.monotonic() + 5,
        _Ledger(executor),
        tool_registry=_registry(executor),
    ):
        items.append(item)
    return items, ctx


class AgentProgressTransportTests(unittest.IsolatedAsyncioTestCase):
    async def test_progress_is_streamed_between_tool_start_and_tool_end(self):
        async def executor(_tool_input, ctx):
            await ctx.emit_progress("finding_routes", "active")
            await asyncio.sleep(0)
            await ctx.emit_progress("finding_routes", "complete")
            return ToolResult(ok=True, data={})

        items, ctx = await _collect_round(executor)
        event_types = [getattr(item, "type", None) for item in items]
        self.assertEqual(
            event_types,
            ["tool_start", "progress", "progress", "tool_end", None],
        )
        progress = [item for item in items if isinstance(item, agent_events.ProgressEvent)]
        self.assertEqual(
            [(event.stage, event.status) for event in progress],
            [("finding_routes", "active"), ("finding_routes", "complete")],
        )
        self.assertIsNone(ctx.progress_sink)

    async def test_progress_publisher_preserves_two_leg_cycles_and_drops_only_noise(self):
        async def executor(_tool_input, ctx):
            for _leg in range(2):
                for stage in (
                    "finding_routes",
                    "checking_live_conditions",
                    "comparing_options",
                ):
                    await ctx.emit_progress(stage, "complete")
                    await ctx.emit_progress(stage, "active")
                    await ctx.emit_progress(stage, "active")
                    await ctx.emit_progress(stage, "complete")
                    await ctx.emit_progress(stage, "complete")
            return ToolResult(ok=True, data={})

        items, _ = await _collect_round(executor)
        progress = [item for item in items if isinstance(item, agent_events.ProgressEvent)]
        self.assertEqual(
            [(event.stage, event.status) for event in progress],
            [
                (stage, status)
                for _leg in range(2)
                for stage in (
                    "finding_routes",
                    "checking_live_conditions",
                    "comparing_options",
                )
                for status in ("active", "complete")
            ],
        )
        self.assertEqual((progress[6].stage, progress[6].status), ("finding_routes", "active"))
        self.assertEqual((progress[-1].stage, progress[-1].status), ("comparing_options", "complete"))

    async def test_cancelled_tool_restores_context_sink(self):
        async def executor(_tool_input, ctx):
            await ctx.emit_progress("finding_routes", "active")
            raise asyncio.CancelledError

        ctx = ToolContext(session={})
        with self.assertRaises(asyncio.CancelledError):
            await _collect_round(executor, ctx=ctx)
        self.assertIsNone(ctx.progress_sink)


class ProgressEventTests(unittest.TestCase):
    def test_serializes_the_typed_progress_contract(self):
        for stage in ("finding_routes", "checking_live_conditions", "comparing_options"):
            for status in ("active", "complete"):
                event = agent_events.ProgressEvent(stage=stage, status=status)
                self.assertEqual(event.to_data(), {"stage": stage, "status": status})
                self.assertIn(f"event: progress\ndata: {{\"stage\":\"{stage}\"", agent_events.sse_format(event))
