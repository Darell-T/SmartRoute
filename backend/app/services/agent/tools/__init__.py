"""Tool registry for the conversational transit agent (plan_trip,
transit_snapshot from P0; event_lookup, poi_search, venue_crowd_window from
P1; accessibility_status, lookup_facts from P2).

`TOOL_REGISTRY` maps a tool name to its schema, async executor, SSE
`tool_start` label function, and per-tool timeout. `TOOLS` is the plain list
of json schemas for the Anthropic `tools=` request parameter.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import os
from pathlib import Path
from typing import Awaitable, Callable

from app.services.agent.tools import (
    accessibility_status,
    event_lookup,
    lookup_arrivals,
    lookup_facts,
    plan_trip,
    poi_search,
    transit_snapshot,
    venue_crowd_window,
)
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


def _event_lookup_label(tool_input: dict) -> str:
    query = str(tool_input.get("query") or "that event").strip()
    return f"Checking {query} schedule…"


def _lookup_arrivals_label(tool_input: dict) -> str:
    route = str(tool_input.get("route_id") or "your line").strip().upper()
    stop = str(tool_input.get("stop_query") or "").strip()
    suffix = f" at {stop}" if stop else ""
    return f"Checking {route} arrivals{suffix}..."


def _poi_search_label(tool_input: dict) -> str:
    query = str(tool_input.get("query") or "places").strip()
    return f"Finding {query} nearby…"


def _venue_crowd_window_label(tool_input: dict) -> str:
    return "Estimating post-event crowds…"


def _accessibility_status_label(tool_input: dict) -> str:
    station = str(tool_input.get("station") or "that station").strip()
    return f"Checking elevators at {station}…"


def _lookup_facts_label(tool_input: dict) -> str:
    topic = str(tool_input.get("topic") or "that").strip()
    return f"Looking up {topic}…"


# ---- Fixture replay (eval harness hook -- plan doc section 7 Layer 2) ----
#
# AGENT_TOOL_FIXTURES=<dir>: every tool call is intercepted here and replayed
# from {dir}/{tool_name}/{canonical_hash_of_input}.json instead of running
# the real executor, so eval runs never touch a network and fail loudly (not
# silently) on a missing fixture. AGENT_TOOL_FIXTURES_RECORD=1: run the real
# executor AND write its result to that path before returning, to (re)record
# fixtures against live API keys. Wrapping happens once here, at registry
# build time, so plan_trip/transit_snapshot get the hook without either
# module knowing fixtures exist.


def _canonical_hash(tool_input: dict) -> str:
    canonical = json.dumps(tool_input, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


def _fixture_path(fixtures_dir: str, tool_name: str, tool_input: dict) -> Path:
    return Path(fixtures_dir) / tool_name / f"{_canonical_hash(tool_input)}.json"


def _load_fixture(path: Path) -> ToolResult:
    try:
        payload = json.loads(path.read_text())
    except (OSError, ValueError):
        return ToolResult(ok=False, error="no fixture for this input")
    return ToolResult(
        ok=bool(payload.get("ok")),
        data=payload.get("data"),
        summary=payload.get("summary") or "",
        error=payload.get("error"),
    )


def _write_fixture(path: Path, result: ToolResult) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"ok": result.ok, "data": result.data, "summary": result.summary, "error": result.error}
    path.write_text(json.dumps(payload, indent=2, sort_keys=True, default=str))


def _with_fixture_replay(tool_name: str, executor: ToolExecutor) -> ToolExecutor:
    async def wrapped(tool_input: dict, ctx: ToolContext) -> ToolResult:
        fixtures_dir = os.getenv("AGENT_TOOL_FIXTURES")
        if not fixtures_dir:
            return await executor(tool_input, ctx)
        path = _fixture_path(fixtures_dir, tool_name, tool_input)
        if os.getenv("AGENT_TOOL_FIXTURES_RECORD", "").strip() == "1":
            result = await executor(tool_input, ctx)
            _write_fixture(path, result)
            return result
        return _load_fixture(path)

    return wrapped


def _spec(schema: dict, executor: ToolExecutor, label_fn: Callable[[dict], str], timeout_s: float) -> ToolSpec:
    return ToolSpec(
        schema=schema, executor=_with_fixture_replay(schema["name"], executor), label_fn=label_fn, timeout_s=timeout_s
    )


TOOL_REGISTRY: dict[str, ToolSpec] = {
    "plan_trip": _spec(plan_trip.PLAN_TRIP_SCHEMA, plan_trip.execute, _plan_trip_label, 30.0),
    "transit_snapshot": _spec(
        transit_snapshot.TRANSIT_SNAPSHOT_SCHEMA, transit_snapshot.execute, _transit_snapshot_label, 8.0
    ),
    "event_lookup": _spec(event_lookup.EVENT_LOOKUP_SCHEMA, event_lookup.execute, _event_lookup_label, 8.0),
    "lookup_arrivals": _spec(
        lookup_arrivals.LOOKUP_ARRIVALS_SCHEMA,
        lookup_arrivals.execute,
        _lookup_arrivals_label,
        12.0,
    ),
    "poi_search": _spec(poi_search.POI_SEARCH_SCHEMA, poi_search.execute, _poi_search_label, 8.0),
    "venue_crowd_window": _spec(
        venue_crowd_window.VENUE_CROWD_WINDOW_SCHEMA, venue_crowd_window.execute, _venue_crowd_window_label, 2.0
    ),
    "accessibility_status": _spec(
        accessibility_status.ACCESSIBILITY_STATUS_SCHEMA,
        accessibility_status.execute,
        _accessibility_status_label,
        8.0,
    ),
    "lookup_facts": _spec(lookup_facts.LOOKUP_FACTS_SCHEMA, lookup_facts.execute, _lookup_facts_label, 2.0),
}

TOOLS: list[dict] = [spec.schema for spec in TOOL_REGISTRY.values()]

__all__ = ["TOOL_REGISTRY", "TOOLS", "ToolSpec", "ToolContext", "ToolResult"]
