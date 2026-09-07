"""Focused tests for semantic agent progress transport."""

from __future__ import annotations

import asyncio
import time
import types
import unittest

import pytest
from app.services.agent import events as agent_events
from app.services.agent.model import policy as agent_policy
from app.services.agent.tools import ToolSpec
from app.services.agent.tools._types import ToolContext, ToolResult
from app.services.agent.turn.tool_round import execute_tool_round


def _block() -> types.SimpleNamespace:
    return types.SimpleNamespace(
        id="trip-1",
        name="plan_trip",
        input={"origin": "user", "destination": "Coney Island"},
    )


def _search_block() -> types.SimpleNamespace:
    return types.SimpleNamespace(
        id="search-1",
        name="discover_places",
        input={
            "operation": "search",
            "query": "coffee",
            "scope": {"kind": "current_location", "values": []},
            "open_now": None,
            "max_results": 5,
            "candidate_names": [],
        },
    )


def _terminal_block(name: str) -> types.SimpleNamespace:
    return types.SimpleNamespace(id=f"{name}-1", name=name, input={})


class _Ledger:
    def __init__(self, executor):
        self.successful = {}
        self.reusable_results = {}
        self._executor = executor

    def key(self, name, tool_input):
        return f"{name}:{tool_input.get('destination', '')}"

    async def execute(self, name, tool_input, ctx, *, deadline_monotonic):
        del name, deadline_monotonic
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
    items = [
        item
        async for item in execute_tool_round(
        [_block()],
        ctx,
        {},
        [],
        set(),
        agent_policy.policy_for_mode("auto"),
        {},
        time.monotonic() + 5,
        _Ledger(executor),
        tool_registry=_registry(executor),
        )
    ]
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
        assert event_types == [
            "tool_start",
            "progress",
            "progress",
            "tool_end",
            None,
        ]
        progress = [item for item in items if isinstance(item, agent_events.ProgressEvent)]
        assert [(event.stage, event.status) for event in progress] == [
            ("finding_routes", "active"),
            ("finding_routes", "complete"),
        ]
        assert ctx.progress_sink is None

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
        assert [(event.stage, event.status) for event in progress] == [
            (stage, status)
            for _leg in range(2)
            for stage in (
                "finding_routes",
                "checking_live_conditions",
                "comparing_options",
            )
            for status in ("active", "complete")
        ]
        assert (progress[6].stage, progress[6].status) == ("finding_routes", "active")
        assert (progress[-1].stage, progress[-1].status) == (
            "comparing_options",
            "complete",
        )

    async def test_cancelled_tool_restores_context_sink(self):
        async def executor(_tool_input, ctx):
            await ctx.emit_progress("finding_routes", "active")
            raise asyncio.CancelledError

        ctx = ToolContext(session={})
        with pytest.raises(asyncio.CancelledError):
            await _collect_round(executor, ctx=ctx)
        assert ctx.progress_sink is None

    async def test_server_owned_refinement_drives_public_discovery(self):
        captured = {}

        async def executor(tool_input, _ctx):
            captured.update(tool_input)
            return ToolResult(ok=True, data={"places": []})

        registry = {
            "discover_places": ToolSpec(
                schema={"name": "discover_places"},
                executor=executor,
                label_fn=lambda value: (
                    f"Searching for {value['query']} across "
                    f"{' and '.join(value['scope'].get('values') or [])}"
                ),
                timeout_s=5,
            )
        }
        ctx = ToolContext(
            session={},
            discovery_refinement={
                "operation": "search",
                "query": "pizza",
                "scope": {
                    "kind": "boroughs",
                    "values": ["Manhattan", "Brooklyn"],
                },
                "open_now": None,
                "max_results": 5,
                "candidate_names": [],
            },
        )
        items = [
            item
            async for item in execute_tool_round(
            [_search_block()],
            ctx,
            {},
            [],
            set(),
            agent_policy.policy_for_mode("auto"),
            {},
            time.monotonic() + 5,
            _Ledger(executor),
            tool_registry=registry,
            allowed_tool_names=frozenset({"discover_places"}),
            )
        ]

        start = next(item for item in items if isinstance(item, agent_events.ToolStartEvent))
        assert start.label == "Searching for pizza across Manhattan and Brooklyn"
        assert captured["query"] == "pizza"
        assert captured["scope"] == {
            "kind": "boroughs",
            "values": ["Manhattan", "Brooklyn"],
        }

    async def test_presenters_and_completion_do_not_emit_activity_events(self):
        async def executor(_tool_input, _ctx):
            return ToolResult(ok=True, data={})

        for name in ("present_places", "present_transit", "present_route", "complete_turn"):
            with self.subTest(name=name):
                registry = {
                    name: ToolSpec(
                        schema={"name": name},
                        executor=executor,
                        label_fn=lambda _value: "Internal handoff",
                        timeout_s=5,
                    )
                }
                items = [
                    item
                    async for item in execute_tool_round(
                    [_terminal_block(name)],
                    ToolContext(session={}),
                    {},
                    [],
                    set(),
                    agent_policy.policy_for_mode("auto"),
                    {},
                    time.monotonic() + 5,
                    _Ledger(executor),
                    tool_registry=registry,
                    allowed_tool_names=frozenset({name}),
                    )
                ]

                assert not any(
                    isinstance(
                        item,
                        (agent_events.ToolStartEvent, agent_events.ToolEndEvent),
                    )
                    for item in items
                )


class ProgressEventTests(unittest.TestCase):
    def test_serializes_the_typed_progress_contract(self):
        for stage in ("finding_routes", "checking_live_conditions", "comparing_options"):
            for status in ("active", "complete"):
                event = agent_events.ProgressEvent(stage=stage, status=status)
                assert event.to_data() == {"stage": stage, "status": status}
                needle = f'event: progress\ndata: {{"stage":"{stage}"'
                assert needle in agent_events.sse_format(event)
