"""Per-turn tool execution accounting for the conversational agent.

The ledger owns deduplication and call caps only.  The loop supplies the
executor so provider behavior and the loop's compatibility patch points stay
at the orchestration boundary.
"""

from __future__ import annotations

import dataclasses
import json
from collections.abc import Awaitable, Callable

from app.services.agent.tools import ToolContext, ToolResult


ToolRunner = Callable[..., Awaitable[ToolResult]]


@dataclasses.dataclass
class TurnToolLedger:
    """Provider-work ledger scoped to one rider turn only."""

    run_tool: ToolRunner
    max_executions: int
    max_executions_per_name: int
    successful: dict[str, ToolResult] = dataclasses.field(default_factory=dict)
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
        cached = self.successful.get(key)
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
        if result.ok:
            self.successful[key] = result
        return result
