"""Tool input policy and one parallel model-tool round for an agent turn."""

from __future__ import annotations

import asyncio
import contextlib
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


_UNOFFERED_TOOL_ERROR = "tool not offered on this turn"

# Server-owned execution gate: on a destination_discovery turn, a
# prepare_route_options client-tool call must carry the opaque
# destination_place_id issued by search_local_places / get_place_details.
# A plain destination label alone is not routing authority on that intent
# surface; the canonical resolver still validates session/set ownership and
# expiry for any id that passes this presence gate. Route-planning turns
# keep the ordinary canonical provider path unchanged.
_DISCOVERY_PLACE_REQUIRED_ERROR = (
    "destination place reference required on this discovery turn; "
    "pass the destination_place_id of a search result place"
)


def _missing_discovery_destination_reference(
    name: str,
    tool_input: dict,
    parsed_intent: intelligence.ParsedIntent,
) -> str | None:
    """Return the bounded rejection reason for a discovery-turn prepare call
    that lacks a server-issued opaque destination place reference, else None.

    The gate derives only from the server-owned ``ParsedIntent`` and the
    actual tool call; a model-authored policy flag, webpage text, destination
    label, or ``discovery_set_id`` alone never satisfies it.
    """

    if (
        name == "prepare_route_options"
        and parsed_intent.intent == "destination_discovery"
        and not str((tool_input or {}).get("destination_place_id") or "").strip()
    ):
        return _DISCOVERY_PLACE_REQUIRED_ERROR
    return None


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
    if not intelligence.is_what_if_request(message):
        session.setdefault("slots", {}).setdefault("constraints", {})["exclude_modes"] = sorted(excluded)
    return excluded


def rider_excluded_route_ids(message: str, session: dict) -> tuple[str, ...]:
    """Keep explicit rider route exclusions authoritative across follow-ups.

    The effective set starts from the active server-owned slot exclusions,
    applies this message's new exclusions, then removes this message's
    explicit allowances ("allow the Q", "the Q is fine now"). Active
    (non-what-if) constraints persist into ``slots.constraints`` using the
    same server-owned convention as ``exclude_modes``; an empty effective
    set removes the key. A what-if request contributes its constraints to
    the current turn only and never mutates the active constraint set.
    """

    constraints = ((session.get("slots") or {}).get("constraints") or {})
    excluded = list(
        intelligence.normalize_route_ids(constraints.get("excluded_route_ids") or [])
    )
    for route_id in intelligence.extract_excluded_route_ids(message):
        if route_id not in excluded:
            excluded.append(route_id)
    allowed = set(intelligence.extract_allowed_route_ids(message))
    if allowed:
        excluded = [route_id for route_id in excluded if route_id not in allowed]
    if not intelligence.is_what_if_request(message):
        constraints_slots = session.setdefault("slots", {}).setdefault(
            "constraints", {}
        )
        if excluded:
            constraints_slots["excluded_route_ids"] = list(excluded)
        else:
            constraints_slots.pop("excluded_route_ids", None)
    return tuple(excluded)


def constrained_tool_input(
    name: str,
    tool_input: dict,
    excluded_modes: set[str],
    *,
    mode_policy: agent_policy.AgentModePolicy,
    parsed_intent: intelligence.ParsedIntent,
    excluded_route_ids: tuple[str, ...] = (),
) -> dict:
    normalized = dict(tool_input)
    if name in {"plan_trip", "prepare_route_options"}:
        if excluded_modes:
            requested = {
                str(mode).strip().upper()
                for mode in (normalized.get("exclude_modes") or [])
                if str(mode).strip()
            }
            normalized["exclude_modes"] = sorted(requested | excluded_modes)
        allowed_routes = set(
            intelligence.normalize_route_ids(
                getattr(parsed_intent, "allowed_route_ids", ()) or ()
            )
        )
        excluded_routes = set(intelligence.normalize_route_ids(excluded_route_ids))
        excluded_routes.update(
            getattr(parsed_intent, "excluded_route_ids", ()) or ()
        )
        excluded_routes.update(
            intelligence.normalize_route_ids(
                normalized.get("excluded_route_ids") or []
            )
        )
        # An explicit rider allowance wins over every stale exclusion source,
        # including parsed exclusions and model-supplied tool input.
        excluded_routes -= allowed_routes
        if excluded_routes:
            normalized["excluded_route_ids"] = sorted(excluded_routes)
        elif "excluded_route_ids" in normalized:
            del normalized["excluded_route_ids"]
        if name == "prepare_route_options" and getattr(parsed_intent, "what_if", False):
            # Server-enforced what-if isolation: the rider message, not the
            # model, decides that preparation is hypothetical, so a model
            # that omits what_if can never overwrite active trip state.
            normalized["what_if"] = True
        normalized["max_candidates"] = mode_policy.max_route_candidates
        normalized["avoid_crowds"] = bool(normalized.get("avoid_crowds") or parsed_intent.avoid_crowds)
        if mode_policy.mode != "auto" or parsed_intent.avoid_crowds:
            # A rider who explicitly asks to avoid crowds still receives the
            # complete crowd-research path.  That research depth is separate
            # from the conversational model, which remains Haiku in Quick.
            normalized["crowd_search_mode"] = "auto" if parsed_intent.avoid_crowds else mode_policy.mode
        if name in {"plan_trip", "prepare_route_options"}:
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
    excluded_route_ids: tuple[str, ...] = (),
    allowed_tool_names: frozenset[str] | None = None,
) -> AsyncIterator:
    """Emit a model tool round and its final synthetic tool-result message.

    ``allowed_tool_names`` is the exact per-turn offered surface. Every
    registered client tool the model emits outside that surface is rejected
    before the ledger, executor, provider, store, session, or pending-trip
    paths, regardless of which registry object supplies the executor; the
    model still receives a bounded error tool-result so the next round can
    recover. On destination_discovery turns, a ``prepare_route_options``
    call without an opaque ``destination_place_id`` is rejected by the same
    before-ledger boundary with a distinct bounded reason; invented ids pass
    the presence gate and fail in the canonical resolver. Unknown names keep
    their existing bounded safe failure (never a production executor).
    """
    ctx.agent_mode = mode_policy.mode
    ctx.agent_model = mode_policy.model
    ctx.agent_explanation_style = mode_policy.explanation_style
    tool_inputs = {
        block.id: constrained_tool_input(
            getattr(block, "name", ""), getattr(block, "input", {}) or {}, excluded_modes,
            mode_policy=mode_policy, parsed_intent=parsed_intent,
            excluded_route_ids=excluded_route_ids,
        )
        for block in tool_use_blocks
    }
    rejected_results: dict[str, ToolResult] = {
        block.id: ToolResult(ok=False, error=_UNOFFERED_TOOL_ERROR)
        for block in tool_use_blocks
        if allowed_tool_names is not None
        and getattr(block, "name", "") not in allowed_tool_names
        and getattr(block, "name", "") in tool_registry
    }
    for block in tool_use_blocks:
        name = getattr(block, "name", "")
        if block.id not in rejected_results:
            reason = _missing_discovery_destination_reference(
                name, tool_inputs[block.id], parsed_intent
            )
            if reason is not None:
                rejected_results[block.id] = ToolResult(ok=False, error=reason)
    for block in tool_use_blocks:
        name = getattr(block, "name", "")
        tool_input = tool_inputs[block.id]
        spec = tool_registry.get(name) if block.id not in rejected_results else None
        yield agent_events.ToolStartEvent(
            tool_call_id=block.id, tool=name, label=spec.label_fn(tool_input) if spec else f"Using {name}\u2026"
        )

    start_times = {block.id: time.monotonic() for block in tool_use_blocks}
    round_tasks: dict[str, asyncio.Task[ToolResult]] = {}
    pending_calls: dict[str, tuple[str, dict]] = {}
    outcomes_by_key: dict[str, ToolResult] = {}
    first_block_id_by_key: dict[str, str] = {}
    for block in tool_use_blocks:
        if block.id in rejected_results:
            # Rejected calls never touch the ledger, executor, or stores.
            continue
        name = getattr(block, "name", "")
        tool_input = tool_inputs[block.id]
        key = tool_ledger.key(name, tool_input)
        first_block_id_by_key.setdefault(key, block.id)
        cached = tool_ledger.successful.get(key)
        if cached is not None:
            outcomes_by_key[key] = cached
        elif key not in pending_calls:
            pending_calls[key] = (name, tool_input)
    if pending_calls:
        progress_queue: asyncio.Queue[agent_events.ProgressEvent] = asyncio.Queue()
        active_stages: set[str] = set()
        last_progress: tuple[str, str] | None = None

        async def publish_progress(stage: str, status: str) -> None:
            nonlocal last_progress
            if stage not in {
                "finding_routes",
                "checking_live_conditions",
                "comparing_options",
            } or status not in {"active", "complete"}:
                return
            progress = (stage, status)
            if progress == last_progress:
                return
            if status == "complete" and stage not in active_stages:
                return
            if status == "active":
                active_stages.add(stage)
            else:
                active_stages.remove(stage)
            last_progress = progress
            await progress_queue.put(agent_events.ProgressEvent(stage=stage, status=status))

        previous_progress_sink = ctx.progress_sink
        ctx.progress_sink = publish_progress
        round_task: asyncio.Task[list[ToolResult]] | None = None
        progress_wait_task: asyncio.Task[agent_events.ProgressEvent] | None = None
        try:
            for key, (name, tool_input) in pending_calls.items():
                round_tasks[key] = asyncio.create_task(
                    tool_ledger.execute(
                        name,
                        tool_input,
                        ctx,
                        deadline_monotonic=deadline_monotonic,
                    )
                )

            async def gather_round() -> list[ToolResult]:
                return await asyncio.gather(*round_tasks.values())

            round_task = asyncio.create_task(gather_round())
            while True:
                progress_wait_task = asyncio.create_task(progress_queue.get())
                done, _ = await asyncio.wait(
                    {round_task, progress_wait_task},
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if progress_wait_task in done:
                    yield progress_wait_task.result()
                    if round_task in done:
                        break
                    continue
                progress_wait_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await progress_wait_task
                break
            outcomes_by_key.update(zip(round_tasks, await round_task))
            while not progress_queue.empty():
                yield progress_queue.get_nowait()
        finally:
            if progress_wait_task is not None and not progress_wait_task.done():
                progress_wait_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await progress_wait_task
            if round_task is not None and not round_task.done():
                round_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await round_task
            ctx.progress_sink = previous_progress_sink
    outcomes = [
        rejected_results[block.id]
        if block.id in rejected_results
        else outcomes_by_key[
            tool_ledger.key(getattr(block, "name", ""), tool_inputs[block.id])
        ]
        for block in tool_use_blocks
    ]

    tool_result_content = []
    escalation_reason = None
    terminal_plan_trip_result: ToolResult | None = None
    required_tools = set(parsed_intent.required_evidence.required_tools())
    for block, result in zip(tool_use_blocks, outcomes):
        name = getattr(block, "name", "")
        tool_input = tool_inputs[block.id]
        key = tool_ledger.key(name, tool_input)
        surface_side_effects = key in round_tasks and first_block_id_by_key[key] == block.id
        duration_ms = round((time.monotonic() - start_times[block.id]) * 1000)
        if block.id not in rejected_results:
            tool_calls_this_turn.append((name, tool_input))
        if surface_side_effects:
            for stage, duration in result.timings.items():
                if stage in stage_ms:
                    stage_ms[stage] += max(0.0, float(duration))
        route_tool = name in {"plan_trip", "prepare_route_options", "present_route"}
        if block.id not in rejected_results:
            escalation_reason = escalation_reason or quick_escalation.reason_for_tool_result(
                name, result, required=name in required_tools or route_tool
            )
        if result.ok:
            tool_result_content.append({"type": "tool_result", "tool_use_id": block.id, "content": json.dumps({"source": name, "data": result.data, "untrusted": True}, default=str)})
            yield agent_events.ToolEndEvent(tool_call_id=block.id, tool=name, ok=True, duration_ms=duration_ms, summary=result.summary or None)
            if surface_side_effects:
                if result.summary:
                    session_module.append_tool_summary(session, name, result.summary)
                if result.session_route_cards:
                    session_module.add_route_cards(session, result.session_route_cards)
                if name in {"plan_trip", "present_route"}:
                    session_module.clear_pending_trip(session)
                for event in result.events:
                    yield event
            # Terminal only when a recommended route card is emitted. The
            # legacy REST/internal plan_trip remains terminal; preparation is
            # non-terminal so the conversational agent can call present_route.
            if name in {"plan_trip", "present_route"} and any(
                getattr(event, "type", None) == "route_card"
                and getattr(event, "role", None) == "recommended"
                for event in result.events
            ):
                terminal_plan_trip_result = result
        else:
            reason = result.error or "tool failed"
            tool_result_content.append({"type": "tool_result", "tool_use_id": block.id, "content": json.dumps({"source": name, "data": {"error": reason}, "untrusted": True}, default=str), "is_error": True})
            yield agent_events.ToolEndEvent(tool_call_id=block.id, tool=name, ok=False, duration_ms=duration_ms, summary=reason)
            if surface_side_effects and name in {"plan_trip", "prepare_route_options"}:
                session_module.mark_pending_trip_failed(session, tool_input, reason)
    yield {
        "__tool_result_message__": {"role": "user", "content": tool_result_content},
        "__quick_escalation_reason__": escalation_reason,
        "__terminal_plan_trip_result__": terminal_plan_trip_result,
    }
