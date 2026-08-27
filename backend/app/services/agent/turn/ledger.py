"""Per-turn tool execution accounting for the conversational agent.

The ledger owns deduplication and call caps only.  The loop supplies the
executor so provider behavior and the loop's compatibility patch points stay
at the orchestration boundary.
"""

from __future__ import annotations

import asyncio
import contextlib
import dataclasses
import json
import logging
import time
from collections.abc import AsyncIterator, Awaitable, Callable

from app import observability
from app.services.agent import events as agent_events
from app.services.agent.tools import ToolContext, ToolResult

_LOGGER = logging.getLogger(__name__)


ToolRunner = Callable[..., Awaitable[ToolResult]]


async def run_one_tool(
    name: str,
    tool_input: dict,
    ctx: ToolContext,
    *,
    tool_registry: dict,
    deadline_monotonic: float | None = None,
) -> ToolResult:
    """Apply one bounded deadline and normalize provider failures."""

    spec = tool_registry.get(name)
    if spec is None:
        return ToolResult(ok=False, error=f"unknown tool '{name}'")
    timeout_s = spec.timeout_s
    deadline_limited = False
    if deadline_monotonic is not None:
        remaining_s = deadline_monotonic - time.monotonic()
        deadline_limited = remaining_s <= timeout_s
        timeout_s = min(timeout_s, remaining_s)
    if timeout_s <= 0:
        return ToolResult(ok=False, error="turn deadline reached")
    tool_span = observability.start_tool(ctx, name)
    with observability.activate(tool_span):
        try:
            result = await asyncio.wait_for(
                spec.executor(tool_input, ctx), timeout=timeout_s
            )
            observability.finish_tool(tool_span, ok=result.ok)
            return result
        except TimeoutError as exc:
            if deadline_limited or (
                deadline_monotonic is not None
                and time.monotonic() >= deadline_monotonic
            ):
                result = ToolResult(ok=False, error="turn deadline reached")
            else:
                result = ToolResult(ok=False, error="timed out")
            observability.finish_tool(tool_span, ok=False, error=exc)
            return result
        except Exception as exc:
            _LOGGER.warning(
                "agent tool failed tool=%s type=%s",
                name,
                type(exc).__name__,
            )
            observability.finish_tool(tool_span, ok=False, error=exc)
            return ToolResult(ok=False, error="tool failed")


@dataclasses.dataclass
class ToolProgressRelay:
    """Relay the small public progress vocabulary while tools run in parallel."""

    queue: asyncio.Queue[agent_events.ProgressEvent] = dataclasses.field(
        default_factory=asyncio.Queue
    )
    active_stages: set[str] = dataclasses.field(default_factory=set)
    last_progress: tuple[str, str] | None = None

    async def publish(self, stage: str, status: str) -> None:
        if stage not in {
            "finding_routes",
            "checking_live_conditions",
            "comparing_options",
        } or status not in {"active", "complete"}:
            return
        progress = (stage, status)
        if progress == self.last_progress:
            return
        if status == "complete" and stage not in self.active_stages:
            return
        if status == "active":
            self.active_stages.add(stage)
        else:
            self.active_stages.remove(stage)
        self.last_progress = progress
        await self.queue.put(agent_events.ProgressEvent(stage=stage, status=status))

    async def stream_until(self, round_task: asyncio.Future) -> AsyncIterator:
        while not round_task.done():
            progress_task = asyncio.create_task(self.queue.get())
            try:
                done, _ = await asyncio.wait(
                    {round_task, progress_task},
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if progress_task in done:
                    yield progress_task.result()
                    continue
            finally:
                if not progress_task.done():
                    progress_task.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await progress_task
        while not self.queue.empty():
            yield self.queue.get_nowait()


@dataclasses.dataclass
class TurnToolLedger:
    """Provider-work ledger scoped to one rider turn only."""

    run_tool: ToolRunner
    max_executions: int
    max_executions_per_name: int
    reusable_results: dict[str, ToolResult] = dataclasses.field(default_factory=dict)
    total_executions: int = 0
    executions_by_name: dict[str, int] = dataclasses.field(default_factory=dict)

    @staticmethod
    def key(name: str, tool_input: dict) -> str:
        canonical = json.dumps(tool_input, sort_keys=True, separators=(",", ":"), default=str)
        return f"{name}:{canonical}"

    async def execute(
        self, name: str, tool_input: dict, ctx: ToolContext, *, deadline_monotonic: float
    ) -> ToolResult:
        key = self.key(name, tool_input)
        cached = self.reusable_results.get(key)
        if cached is not None:
            return cached
        if self.total_executions >= self.max_executions:
            return ToolResult(ok=False, error="tool execution limit reached for this turn")
        if self.executions_by_name.get(name, 0) >= self.max_executions_per_name:
            return ToolResult(ok=False, error=f"{name} execution limit reached for this turn")

        self.total_executions += 1
        self.executions_by_name[name] = self.executions_by_name.get(name, 0) + 1
        result = await self.run_tool(
            name,
            tool_input,
            ctx,
            deadline_monotonic=deadline_monotonic,
        )
        if result.reusable_within_turn:
            self.reusable_results[key] = result
        return result
