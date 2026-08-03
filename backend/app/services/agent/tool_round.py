"""Tool input policy and one parallel model-tool round for an agent turn."""

from __future__ import annotations

import asyncio
import json
import re
import time
from collections.abc import AsyncIterator

from app.services.agent import events as agent_events
from app.services.agent import intelligence
from app.services.agent import policy as agent_policy
from app.services.agent import quick_escalation
from app.services.agent import session as session_module
from app.services.agent.tools import ToolContext, ToolResult
from app.services.agent.turn_ledger import TurnToolLedger


class TurnDeadlineReached(Exception):
    """Internal control flow that still reaches the turn's single DoneEvent."""


async def run_one_tool(
    name: str,
    tool_input: dict,
    ctx: ToolContext,
    *,
    tool_registry: dict,
    deadline_monotonic: float | None = None,
) -> ToolResult:
    """Return a normalized tool failure instead of leaking provider errors."""
    spec = tool_registry.get(name)
    if spec is None:
        return ToolResult(ok=False, error=f"unknown tool '{name}'")
    timeout_s = spec.timeout_s
    if deadline_monotonic is not None:
        timeout_s = min(timeout_s, deadline_monotonic - time.monotonic())
    if timeout_s <= 0:
        return ToolResult(ok=False, error="turn deadline reached")
    try:
        return await asyncio.wait_for(spec.executor(tool_input, ctx), timeout=timeout_s)
    except asyncio.TimeoutError:
        if deadline_monotonic is not None and time.monotonic() >= deadline_monotonic:
            return ToolResult(ok=False, error="turn deadline reached")
        return ToolResult(ok=False, error="timed out")
    except Exception as exc:
        print(f"[agent-loop] tool {name} failed type={type(exc).__name__}")
        return ToolResult(ok=False, error="tool failed")


def rider_excluded_modes(message: str, session: dict) -> set[str]:
    """Keep explicit rider constraints authoritative across follow-up turns."""
    constraints = ((session.get("slots") or {}).get("constraints") or {})
    excluded = {
        str(mode).strip().upper()
        for mode in (constraints.get("exclude_modes") or [])
        if str(mode).strip()
    }
    rider_text = message.casefold()
    if re.search(r"\b(?:no|without|avoid(?:ing)?)\s+(?:the\s+)?(?:bus|buses)\b", rider_text):
        excluded.add("BUS")
    elif re.search(
        r"\b(?:include|allow)\s+(?:the\s+)?(?:bus|buses)\b|\b(?:bus|buses)\s+(?:is|are)\s+(?:ok|okay|fine)\b",
        rider_text,
    ):
        excluded.discard("BUS")
    session.setdefault("slots", {}).setdefault("constraints", {})["exclude_modes"] = sorted(excluded)
    return excluded


def constrained_tool_input(
    name: str,
    tool_input: dict,
    excluded_modes: set[str],
    *,
    mode_policy: agent_policy.AgentModePolicy,
    parsed_intent: intelligence.ParsedIntent,
) -> dict:
    normalized = dict(tool_input)
    if name == "plan_trip":
        if excluded_modes:
            requested = {
                str(mode).strip().upper()
                for mode in (normalized.get("exclude_modes") or [])
                if str(mode).strip()
            }
            normalized["exclude_modes"] = sorted(requested | excluded_modes)
        normalized["max_candidates"] = mode_policy.max_route_candidates
        normalized["avoid_crowds"] = bool(normalized.get("avoid_crowds") or parsed_intent.avoid_crowds)
        if mode_policy.mode != "auto" or parsed_intent.avoid_crowds:
            normalized["crowd_search_mode"] = mode_policy.mode
        normalized["include_first_leg_arrivals"] = mode_policy.optional_enrichment
        if parsed_intent.requested_route_ids:
            normalized["required_route_ids"] = list(parsed_intent.requested_route_ids)
    elif name == "lookup_arrivals":
        normalized["limit"] = min(int(normalized.get("limit") or 3), 2 if mode_policy.mode == "quick" else 3)
    return normalized


def required_arrival_input(
    parsed_intent: intelligence.ParsedIntent,
    ctx: ToolContext,
    mode_policy: agent_policy.AgentModePolicy,
) -> dict | None:
    route_id = str(parsed_intent.arrival_route_id or "").strip().upper()
    active_trip = (ctx.session or {}).get("active_trip") or {}
    boarding = active_trip.get("first_boarding") if isinstance(active_trip, dict) else None
    if not route_id and isinstance(boarding, dict):
        route_id = str(boarding.get("route_id") or "").strip().upper()
    if not route_id:
        return None
    tool_input: dict = {
        "route_id": route_id,
        "mode": "bus" if any(char.isdigit() for char in route_id) and len(route_id) > 1 else "subway",
        "limit": 2 if mode_policy.mode == "quick" else 3,
    }
    if parsed_intent.arrival_stop_query:
        tool_input["stop_query"] = parsed_intent.arrival_stop_query
    if parsed_intent.arrival.direction_query:
        tool_input["direction"] = parsed_intent.arrival.direction_query
    if ctx.origin:
        tool_input["user_location"] = {"latitude": ctx.origin.get("lat"), "longitude": ctx.origin.get("lng")}
    return tool_input


def arrival_response(data: dict) -> tuple[str, str]:
    route_id = str(data.get("route_id") or "train")
    stop_name = str((data.get("stop") or {}).get("name") or "that stop")
    status = str(data.get("source_status") or "provider_unavailable")
    directions = list(data.get("directions") or [])
    if status == "stop_not_resolved":
        matches = [str(match.get("stop_name") or "").strip() for match in data.get("ambiguity") or [] if str(match.get("stop_name") or "").strip()]
        if matches:
            return f"Which {route_id} station do you mean: {', '.join(matches)}?", "clarification_required"
        return f"I need a more specific station or your location to check {route_id} arrivals.", "clarification_required"
    if status == "provider_unavailable":
        return f"Live and scheduled {route_id} arrival data at {stop_name} is temporarily unavailable.", "end_turn"
    if status == "no_predictions" or not directions:
        return f"No current {route_id} arrival predictions are available at {stop_name}.", "end_turn"
    group = directions[0]
    arrivals = [arrival for arrival in group.get("arrivals") or [] if int(arrival.get("minutes") or 0) > 0]
    if not arrivals:
        return f"No current {route_id} arrival predictions are available at {stop_name}.", "end_turn"
    minutes = max(0, int(arrivals[0].get("minutes") or 0))
    direction = str(group.get("id") or group.get("label") or "").strip().casefold()
    qualifier = f"{direction} " if direction else ""
    minute_copy = "1 minute" if minutes == 1 else f"{minutes} minutes"
    if status == "stale":
        return f"The latest available {qualifier}{route_id} prediction at {stop_name} is {minute_copy}, but live data is stale.", "end_turn"
    return f"The next {qualifier}{route_id} train at {stop_name} is in {minute_copy}.", "end_turn"


async def execute_tool_round(
    tool_use_blocks: list,
    ctx: ToolContext,
    session: dict,
    tool_calls_this_turn: list[tuple[str, dict]],
    excluded_modes: set[str],
    mode_policy: agent_policy.AgentModePolicy,
    parsed_intent: intelligence.ParsedIntent,
    stage_ms: dict[str, float],
    deadline_monotonic: float,
    tool_ledger: TurnToolLedger,
    *,
    tool_registry: dict,
) -> AsyncIterator:
    """Emit a model tool round and its final synthetic tool-result message."""
    tool_inputs = {
        block.id: constrained_tool_input(
            getattr(block, "name", ""), getattr(block, "input", {}) or {}, excluded_modes,
            mode_policy=mode_policy, parsed_intent=parsed_intent,
        )
        for block in tool_use_blocks
    }
    for block in tool_use_blocks:
        name = getattr(block, "name", "")
        tool_input = tool_inputs[block.id]
        spec = tool_registry.get(name)
        yield agent_events.ToolStartEvent(
            tool_call_id=block.id, tool=name, label=spec.label_fn(tool_input) if spec else f"Using {name}\u2026"
        )

    start_times = {block.id: time.monotonic() for block in tool_use_blocks}
    round_tasks: dict[str, asyncio.Task[ToolResult]] = {}
    outcomes_by_key: dict[str, ToolResult] = {}
    first_block_id_by_key: dict[str, str] = {}
    for block in tool_use_blocks:
        name = getattr(block, "name", "")
        tool_input = tool_inputs[block.id]
        key = tool_ledger.key(name, tool_input)
        first_block_id_by_key.setdefault(key, block.id)
        cached = tool_ledger.successful.get(key)
        if cached is not None:
            outcomes_by_key[key] = cached
        elif key not in round_tasks:
            round_tasks[key] = asyncio.create_task(tool_ledger.execute(name, tool_input, ctx, deadline_monotonic=deadline_monotonic))
    if round_tasks:
        outcomes_by_key.update(zip(round_tasks, await asyncio.gather(*round_tasks.values())))
    outcomes = [outcomes_by_key[tool_ledger.key(getattr(block, "name", ""), tool_inputs[block.id])] for block in tool_use_blocks]

    tool_result_content = []
    escalation_reason = None
    required_tools = set(parsed_intent.required_evidence.required_tools())
    for block, result in zip(tool_use_blocks, outcomes):
        name = getattr(block, "name", "")
        tool_input = tool_inputs[block.id]
        key = tool_ledger.key(name, tool_input)
        surface_side_effects = key in round_tasks and first_block_id_by_key[key] == block.id
        duration_ms = round((time.monotonic() - start_times[block.id]) * 1000)
        tool_calls_this_turn.append((name, tool_input))
        if surface_side_effects:
            for stage, duration in result.timings.items():
                if stage in stage_ms:
                    stage_ms[stage] += max(0.0, float(duration))
        escalation_reason = escalation_reason or quick_escalation.reason_for_tool_result(
            name, result, required=name in required_tools or name == "plan_trip"
        )
        if result.ok:
            tool_result_content.append({"type": "tool_result", "tool_use_id": block.id, "content": json.dumps({"source": name, "data": result.data, "untrusted": True}, default=str)})
            yield agent_events.ToolEndEvent(tool_call_id=block.id, tool=name, ok=True, duration_ms=duration_ms, summary=result.summary or None)
            if surface_side_effects:
                if result.summary:
                    session_module.append_tool_summary(session, name, result.summary)
                if result.session_route_cards:
                    session_module.add_route_cards(session, result.session_route_cards)
                if name == "plan_trip":
                    session_module.clear_pending_trip(session)
                for event in result.events:
                    yield event
        else:
            reason = result.error or "tool failed"
            tool_result_content.append({"type": "tool_result", "tool_use_id": block.id, "content": json.dumps({"source": name, "data": {"error": reason}, "untrusted": True}, default=str), "is_error": True})
            yield agent_events.ToolEndEvent(tool_call_id=block.id, tool=name, ok=False, duration_ms=duration_ms, summary=reason)
            if surface_side_effects and name == "plan_trip":
                session_module.mark_pending_trip_failed(session, tool_input, reason)
    yield {"__tool_result_message__": {"role": "user", "content": tool_result_content}, "__quick_escalation_reason__": escalation_reason}
