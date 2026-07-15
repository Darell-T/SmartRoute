"""Tool registry for the conversational transit agent (P0: plan_trip +
transit_snapshot only -- poi_search/event_lookup/venue_crowd_window land in
P1 per the plan doc).

`TOOL_REGISTRY` maps a tool name to its schema, async executor, SSE
`tool_start` label function, and per-tool timeout. `TOOLS` is the plain list
of json schemas for the Anthropic `tools=` request parameter.
"""

from __future__ import annotations

from typing import Awaitable, Callable

import dataclasses

from app.services.agent.tools import plan_trip, transit_snapshot
from app.services.agent.tools._types import ToolContext, ToolResult

ToolExecutor = Callable[[dict, ToolContext], Awaitable[ToolResult]]


@dataclasses.dataclass
class ToolSpec:
    schema: dict
    executor: ToolExecutor
    label_fn: Callable[[dict], str]
    timeout_s: float


def _plan_trip_label(tool_input: dict) -> str:
    destination = str(tool_input.get("destination") or "your destination").strip()
    excluded = [str(m).strip().lower() for m in (tool_input.get("exclude_modes") or [])]
    suffix = f" (no {', '.join(excluded)})" if excluded else ""
    return f"Finding routes to {destination}{suffix}…"


def _transit_snapshot_label(tool_input: dict) -> str:
    near = str(tool_input.get("near") or "").strip()
    if near:
        return f"Checking conditions near {near}…"
    lines = tool_input.get("lines") or []
    if lines:
        return f"Checking alerts for {'/'.join(str(line) for line in lines)}…"
    return "Checking live transit conditions…"


TOOL_REGISTRY: dict[str, ToolSpec] = {
    "plan_trip": ToolSpec(
        schema=plan_trip.PLAN_TRIP_SCHEMA,
        executor=plan_trip.execute,
        label_fn=_plan_trip_label,
        timeout_s=30.0,
    ),
    "transit_snapshot": ToolSpec(
        schema=transit_snapshot.TRANSIT_SNAPSHOT_SCHEMA,
        executor=transit_snapshot.execute,
        label_fn=_transit_snapshot_label,
        timeout_s=8.0,
    ),
}

TOOLS: list[dict] = [spec.schema for spec in TOOL_REGISTRY.values()]

__all__ = ["TOOL_REGISTRY", "TOOLS", "ToolSpec", "ToolContext", "ToolResult"]
