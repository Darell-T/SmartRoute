"""Tool registry for the rider-facing conversational transit agent.

The public vocabulary is stable while each model round receives only the
tools valid for current server-owned turn state. Internal leaf executors stay
registered for dispatch and are never model-offered. The legacy nested-
selector ``plan_trip`` facade is not a public production tool.

`TOOL_REGISTRY` is the offered public surface. `INTERNAL_TOOL_REGISTRY`
holds leaf executors. `TOOLS` is the offered schema list for Anthropic.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import os
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any, Awaitable, Callable

from app.services.agent.public_surface import offered_custom_tools
from app.services.agent.tools import (
    complete_turn,
    declare_goals,
)
from app.services.agent.tools.places import discover_places, place_reference, present_places
from app.services.agent.tools._types import ToolContext, ToolResult
from app.services.agent.tools.route import prepare_route_options, present_route
from app.services.agent.tools.transit import (
    accessibility_status,
    check_area_conditions,
    check_transit,
    lookup_arrivals,
    lookup_facts,
    present_transit,
    transit_snapshot,
    venue_crowd_window as venues,
)

ToolExecutor = Callable[[dict, ToolContext], Awaitable[ToolResult]]
@dataclasses.dataclass
class ToolSpec:
    schema: dict
    executor: ToolExecutor
    label_fn: Callable[[dict], str]
    timeout_s: float


def _transit_snapshot_label(tool_input: dict) -> str:
    near = str(tool_input.get("near") or "").strip()
    if near:
        return f"Checking conditions near {near}…"
    lines = tool_input.get("lines") or []
    if lines:
        return f"Checking alerts for {'/'.join(str(line) for line in lines)}…"
    return "Checking live transit conditions…"


def _check_area_conditions_label(tool_input: dict) -> str:
    area = str(tool_input.get("area") or "that area").strip()
    return f"Checking conditions near {area}…"


def _event_lookup_label(tool_input: dict) -> str:
    query = str(tool_input.get("query") or "that event").strip()
    return f"Checking {query} schedule…"


def _lookup_arrivals_label(tool_input: dict) -> str:
    route = str(tool_input.get("route_id") or "your line").strip().upper()
    stop = str(tool_input.get("stop_query") or "").strip()
    suffix = f" at {stop}" if stop else ""
    return f"Checking {route} arrivals{suffix}..."


def _venue_crowd_window_label(tool_input: dict) -> str:
    return "Estimating post-event crowds…"


def _accessibility_status_label(tool_input: dict) -> str:
    station = str(tool_input.get("station") or "that station").strip()
    return f"Checking elevators at {station}…"


def _lookup_facts_label(tool_input: dict) -> str:
    topic = str(tool_input.get("topic") or "that").strip()
    return f"Looking up {topic}…"


def _prepare_route_options_label(tool_input: dict) -> str:
    destination_place_id = str(tool_input.get("destination_place_id") or "").strip()
    destination = str(tool_input.get("destination") or "your destination").strip()
    if destination_place_id or destination.startswith("pl_"):
        return "Preparing routes to your selected place…"
    return f"Preparing routes to {destination}…"


def _present_route_label(tool_input: dict) -> str:
    return "Presenting the recommended route…"


def _discover_places_label(tool_input: dict) -> str:
    scope = tool_input.get("scope") if isinstance(tool_input.get("scope"), dict) else {}
    kind = str(scope.get("kind") or "")
    values = [str(item).strip() for item in (scope.get("values") or []) if str(item).strip()]
    if kind == "current_location":
        where = "near you"
    elif kind == "named_area" and values:
        where = f"in {values[0]}"
    elif kind == "boroughs" and values:
        where = f"in {' and '.join(values)}"
    else:
        where = "in NYC"
    query = str(tool_input.get("query") or "places").strip()
    if str(tool_input.get("operation") or "") == "verify":
        return f"Verifying {query} {where}…"
    return f"Searching verified places {where}…"


def _present_places_label(tool_input: dict) -> str:
    return "Presenting verified places…"


def _check_transit_label(tool_input: dict) -> str:
    operation = str(tool_input.get("operation") or "").strip()
    routes = [
        str(item).strip().upper()
        for item in (tool_input.get("route_ids") or [])
        if str(item).strip()
    ]
    route = routes[0] if routes else ""
    if operation == "arrivals":
        stop = str(tool_input.get("stop_query") or "").strip()
        direction = str(tool_input.get("direction") or "").strip()
        head = " ".join(part for part in (direction, route) if part)
        suffix = f" at {stop}" if stop else ""
        target = head or "arrivals"
        return f"Checking {target}{suffix}…"
    if operation == "accessibility":
        station = str(tool_input.get("station") or "that station").strip()
        return f"Checking accessibility at {station}…"
    if operation == "area_conditions":
        area = str(tool_input.get("area") or "that area").strip()
        return f"Checking conditions near {area}…"
    if operation == "event_schedule":
        query = str(tool_input.get("event_query") or "that event").strip()
        return f"Checking {query} schedule…"
    if operation == "fact":
        topic = str(tool_input.get("topic") or "that").strip()
        return f"Looking up {topic}…"
    if route:
        return f"Checking {route} service…"
    return "Checking live transit conditions…"


def _complete_turn_label(tool_input: dict) -> str:
    return "Finishing your answer…"


def _declare_goals_label(tool_input: dict) -> str:
    return "Thinking through your request…"


def _present_transit_label(tool_input: dict) -> str:
    return "Presenting verified transit information…"


# ---- Fixture replay (eval harness hook -- plan doc section 7 Layer 2) ----
#
# AGENT_TOOL_FIXTURES=<dir>: every tool call is intercepted here and replayed
# from {dir}/{tool_name}/{canonical_hash_of_input}.json instead of running
# the real executor, so eval runs never touch a network and fail loudly (not
# silently) on a missing fixture. AGENT_TOOL_FIXTURES_RECORD=1: run the real
# executor AND write its result to that path before returning, to (re)record
# fixtures against live API keys. Wrapping happens once here, at registry
# build time, so route/transit tools get the hook without either
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
        schema=schema,
        executor=_with_fixture_replay(schema["name"], executor),
        label_fn=label_fn,
        timeout_s=timeout_s,
    )


INTERNAL_TOOL_REGISTRY: dict[str, ToolSpec] = {
    "transit_snapshot": _spec(
        transit_snapshot.TRANSIT_SNAPSHOT_SCHEMA, transit_snapshot.execute, _transit_snapshot_label, 8.0
    ),
    "check_area_conditions": _spec(
        check_area_conditions.AREA_CONDITIONS_SCHEMA,
        check_area_conditions.execute,
        _check_area_conditions_label,
        12.0,
    ),
    "event_lookup": _spec(
        check_transit.EVENT_LOOKUP_SCHEMA,
        check_transit.execute_event_lookup,
        _event_lookup_label,
        8.0,
    ),
    "lookup_arrivals": _spec(
        lookup_arrivals.LOOKUP_ARRIVALS_SCHEMA,
        lookup_arrivals.execute,
        _lookup_arrivals_label,
        12.0,
    ),
    "venue_crowd_window": _spec(
        venues.VENUE_CROWD_WINDOW_SCHEMA, venues.execute, _venue_crowd_window_label, 2.0
    ),
    "accessibility_status": _spec(
        accessibility_status.ACCESSIBILITY_STATUS_SCHEMA,
        accessibility_status.execute,
        _accessibility_status_label,
        8.0,
    ),
    "lookup_facts": _spec(lookup_facts.LOOKUP_FACTS_SCHEMA, lookup_facts.execute, _lookup_facts_label, 2.0),
    "get_place_details": _spec(
        place_reference.GET_PLACE_DETAILS_SCHEMA,
        place_reference.execute,
        lambda tool_input: "Checking place details…",
        8.0,
    ),
}

TOOL_REGISTRY: dict[str, ToolSpec] = {
    "declare_goals": _spec(
        declare_goals.DECLARE_GOALS_SCHEMA,
        declare_goals.execute,
        _declare_goals_label,
        2.0,
    ),
    "discover_places": _spec(
        discover_places.DISCOVER_PLACES_SCHEMA,
        discover_places.execute,
        _discover_places_label,
        10.0,
    ),
    "present_places": _spec(
        present_places.PRESENT_PLACES_SCHEMA,
        present_places.execute,
        _present_places_label,
        6.0,
    ),
    "present_transit": _spec(
        present_transit.PRESENT_TRANSIT_SCHEMA,
        present_transit.execute,
        _present_transit_label,
        6.0,
    ),
    "check_transit": _spec(
        check_transit.CHECK_TRANSIT_SCHEMA,
        check_transit.execute,
        _check_transit_label,
        12.0,
    ),
    "prepare_route_options": _spec(
        prepare_route_options.PREPARE_ROUTE_OPTIONS_SCHEMA,
        prepare_route_options.execute,
        _prepare_route_options_label,
        30.0,
    ),
    "present_route": _spec(
        present_route.PRESENT_ROUTE_SCHEMA,
        present_route.execute,
        _present_route_label,
        12.0,
    ),
    "complete_turn": _spec(
        complete_turn.COMPLETE_TURN_SCHEMA,
        complete_turn.execute,
        _complete_turn_label,
        4.0,
    ),
}

# Executors remain reachable by name for internal dispatch and fixtures.
COMBINED_TOOL_REGISTRY: dict[str, ToolSpec] = {**INTERNAL_TOOL_REGISTRY, **TOOL_REGISTRY}
TOOLS: list[dict] = offered_custom_tools(spec.schema for spec in TOOL_REGISTRY.values())

_UNSUPPORTED_STRICT_KEYWORDS = frozenset(
    {
        "maxItems",
        "maxLength",
        "minLength",
        "minimum",
        "maximum",
        "exclusiveMinimum",
        "exclusiveMaximum",
        "multipleOf",
        "pattern",
        "uniqueItems",
        "contains",
        "propertyNames",
        "minProperties",
        "maxProperties",
    }
)


def iter_unsupported_strict_keyword_paths(
    schema: Any, *, path: str = "$"
) -> list[str]:
    findings: list[str] = []
    if isinstance(schema, Mapping):
        for key, value in schema.items():
            child = f"{path}.{key}"
            if key in _UNSUPPORTED_STRICT_KEYWORDS:
                findings.append(child)
            elif key == "minItems" and value not in (0, 1):
                findings.append(child)
            findings.extend(iter_unsupported_strict_keyword_paths(value, path=child))
    elif isinstance(schema, list):
        for index, item in enumerate(schema):
            findings.extend(
                iter_unsupported_strict_keyword_paths(item, path=f"{path}[{index}]")
            )
    return findings


def assert_strict_tool_schemas_compatible(tools: Iterable[Mapping[str, Any]]) -> None:
    problems: list[str] = []
    for tool in tools:
        if not tool.get("strict"):
            continue
        name = str(tool.get("name") or "<unnamed>")
        input_schema = tool.get("input_schema")
        if not isinstance(input_schema, Mapping):
            problems.append(f"{name}: missing object input_schema")
            continue
        for finding in iter_unsupported_strict_keyword_paths(input_schema):
            problems.append(f"{name}: {finding}")
    if problems:
        joined = "; ".join(problems)
        raise AssertionError(
            "strict custom tool schema uses Anthropic-unsupported keywords: "
            f"{joined}"
        )


assert_strict_tool_schemas_compatible(TOOLS)

__all__ = [
    "COMBINED_TOOL_REGISTRY",
    "INTERNAL_TOOL_REGISTRY",
    "TOOL_REGISTRY",
    "TOOLS",
    "ToolSpec",
    "ToolContext",
    "ToolResult",
    "assert_strict_tool_schemas_compatible",
    "iter_unsupported_strict_keyword_paths",
]
