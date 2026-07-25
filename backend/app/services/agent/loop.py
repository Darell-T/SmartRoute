"""The conversational agent's turn loop: a manual round loop over
`AsyncAnthropic().messages.stream(...)`, not the SDK tool runner -- we own
per-round streaming (re-emitted as `token` SSE events), parallel tool
execution, and the round/deadline wrap-up policy ourselves.

`run_agent_turn()` is the public entry point. It always yields a `meta`
event first and a `done` event last, even when a budget check rejects the
turn before any model call, or something fails mid-turn.
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
import os
import re
import time
from typing import AsyncIterator

import anthropic

from app.services.agent import budget
from app.services.agent import events as agent_events
from app.services.agent import intelligence
from app.services.agent import model_request
from app.services.agent import policy as agent_policy
from app.services.agent import prompt as agent_prompt
from app.services.agent import session as session_module
from app.services.agent.tools import TOOL_REGISTRY, TOOLS, ToolContext, ToolResult

# Application retries are classified below. Disable the SDK's own retries so
# one configured retry never expands into several hidden provider attempts.
client = anthropic.AsyncAnthropic(api_key=os.getenv("ANTHROPIC_API_KEY"), max_retries=0)

AGENT_TURN_DEADLINE_S = float(os.getenv("AGENT_TURN_DEADLINE_S", "50"))
# Local UI work should be repeatable and must not spend model credits. This
# flag swaps the entire turn (including tool work) for a deterministic SSE
# fixture; production remains live unless explicitly configured otherwise.
AGENT_MOCK_MODE = os.getenv("AGENT_MOCK_MODE", "0").strip() == "1"


def _mock_step_delay_s() -> float:
    try:
        return max(0.0, float(os.getenv("AGENT_MOCK_STEP_DELAY_MS", "280")) / 1000)
    except ValueError:
        return 0.28

WRAP_UP_INSTRUCTION = (
    "You are out of time or tool calls for this turn. Summarize what you "
    "know from the tool results so far, and plainly state what you could "
    "not determine. Do not call any more tools."
)


@dataclasses.dataclass
class TurnTrace:
    """Optional eval hook: pass an instance in via `trace=` and it is
    populated in place as the turn runs -- every (tool_name, input) call and
    the final assistant text."""

    tool_calls: list[tuple[str, dict]] = dataclasses.field(default_factory=list)
    final_text: str = ""


def _mock_trip_copy(
    message: str,
    response_presentation: str = "auto",
) -> tuple[str, dict, int, list[str]]:
    """Return stable preview content without inferring live service status."""
    query = message.casefold()
    if "costco" in query:
        text = (
            "Take the A train to Costco in this preview — about 34 minutes "
            "with no transfers."
            if response_presentation == "quick"
            else (
                "I'd take the A train to Costco in this preview. It is the best fit "
                "because it uses one train and keeps the final walk short with your cart."
            )
        )
        return (
            text,
            {"label": "Costco Sunset Park", "lat": 40.6559, "lng": -74.0089},
            34,
            ["A"],
        )
    if "pizza" in query:
        text = (
            "Take the N and Q in this preview — about 27 minutes with one transfer."
            if response_presentation == "quick"
            else (
                "I'd take the N and Q for this preview and stop for pizza near Midtown. "
                "I picked it because the sample itinerary keeps the transfer count low."
            )
        )
        return (
            text,
            {"label": "Pizza stop near Midtown", "lat": 40.7549, "lng": -73.9840},
            27,
            ["N", "Q"],
        )
    return (
        "I’m showing a simulated transit option so you can preview the chat experience. "
        "Switch off mock mode when you’re ready to use live route data.",
        {"label": "Demo destination", "lat": 40.7306, "lng": -73.9866},
        22,
        ["Q"],
    )


def _mock_token_chunks(text: str) -> list[str]:
    words = text.split(" ")
    return [
        ("" if index == 0 else " ") + " ".join(words[index : index + 3])
        for index in range(0, len(words), 3)
    ]


async def _stream_mock_turn(
    *,
    session: dict,
    session_id: str,
    turn_id: str,
    message: str,
    origin: dict | None,
    trace: TurnTrace | None,
    response_presentation: str,
) -> AsyncIterator[agent_events.AgentEvent]:
    """Stream a small, deterministic fixture for local chat/UI development.

    It deliberately avoids calling model clients, route providers, or agent
    tools. The event order mirrors a live turn so the production reducer,
    reasoning panel, and route-card UI are exercised unchanged.
    """
    started_at = time.monotonic()
    delay_s = _mock_step_delay_s()
    text, destination, eta_minutes, lines = _mock_trip_copy(
        message,
        response_presentation,
    )
    mock_origin = {
        "label": "Your location",
        "lat": float((origin or {}).get("lat", 40.7484)),
        "lng": float((origin or {}).get("lng", -73.9857)),
    }
    tool_calls = [("mock_plan_trip", {"destination": destination["label"]})]

    session_module.append_history(session, "user", message)
    yield agent_events.ToolStartEvent(
        tool_call_id=f"mock-route-{turn_id}",
        tool="plan_trip",
        label="Previewing a cart-friendly subway route…",
    )
    await asyncio.sleep(delay_s)
    yield agent_events.ToolEndEvent(
        tool_call_id=f"mock-route-{turn_id}",
        tool="plan_trip",
        ok=True,
        duration_ms=round(delay_s * 1000),
        summary="Preview route ready",
    )

    yield agent_events.ToolStartEvent(
        tool_call_id=f"mock-service-{turn_id}",
        tool="transit_snapshot",
        label="Loading simulated service conditions…",
    )
    await asyncio.sleep(delay_s)
    yield agent_events.ToolEndEvent(
        tool_call_id=f"mock-service-{turn_id}",
        tool="transit_snapshot",
        ok=True,
        duration_ms=round(delay_s * 1000),
        summary="Preview data only",
    )

    for chunk in _mock_token_chunks(text):
        await asyncio.sleep(min(0.06, delay_s))
        yield agent_events.TokenEvent(text=chunk)

    card_id = f"mock-{turn_id}"
    card = agent_events.RouteCardEvent(
        card_id=card_id,
        turn_id=turn_id,
        role="recommended",
        origin=mock_origin,
        destination=destination,
        summary={
            "eta_minutes": eta_minutes,
            "transfers": max(0, len(lines) - 1),
            "lines": lines,
            "reason": "A simple sample route with a short final walk.",
        },
        route=[],
        alerts=[],
    )
    yield card

    session_module.append_history(session, "assistant", text)
    session_module.append_tool_summary(session, "mock_agent", "served deterministic preview data")
    session_module.add_route_cards(
        session,
        [{"card_id": card_id, "role": "recommended", "lines": lines, "eta_minutes": eta_minutes}],
    )
    if trace is not None:
        trace.tool_calls = tool_calls
        trace.final_text = text

    elapsed_ms = round((time.monotonic() - started_at) * 1000)
    print(f"[agent] turn={turn_id} sess={session_id[:6]} mock=1 total_ms={elapsed_ms}")
    yield agent_events.DoneEvent(
        session_id=session_id,
        turn_id=turn_id,
        stop_reason="end_turn",
        usage={"input_tokens": 0, "output_tokens": 0},
    )


def _system_blocks() -> list[dict]:
    # cache_control on the last (only) system block caches tools + system
    # together (render order is tools -> system -> messages); per-turn
    # dynamic state lives in the <context> block of the latest user message
    # instead, so this stays byte-stable across turns/sessions.
    return [{"type": "text", "text": agent_prompt.SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}}]


def _messages_from_history(history: list[dict]) -> list[dict]:
    messages: list[dict] = []
    for entry in history or []:
        role = entry.get("role")
        if role == "user":
            messages.append({"role": "user", "content": entry.get("text", "")})
        elif role == "assistant":
            messages.append({"role": "assistant", "content": entry.get("text", "")})
        elif role == "tool":
            # Tool summaries are attributed to the assistant turn that ran
            # them; consecutive assistant-role messages are combined by the
            # API, so this doesn't break alternation.
            tool_name = entry.get("tool", "tool")
            messages.append({"role": "assistant", "content": f"[{tool_name} result] {entry.get('text', '')}"})
    return messages


def _build_stream_kwargs(
    *,
    force_final: bool,
    messages: list[dict],
    system_blocks: list[dict],
    mode_policy: agent_policy.AgentModePolicy,
) -> dict:
    return model_request.build_stream_kwargs(
        force_final=force_final,
        messages=messages,
        system_blocks=system_blocks,
        mode_policy=mode_policy,
        tools=TOOLS,
    )


def _route_card_text_fallback(card: agent_events.RouteCardEvent) -> str:
    """Grounded copy for the rare tool turn whose final model response is empty."""
    summary = card.summary or {}
    destination = (card.destination or {}).get("label") or "your destination"
    eta = summary.get("eta_minutes")
    lines = [str(line) for line in (summary.get("lines") or []) if line]
    transfers = summary.get("transfers")
    reason = str(summary.get("reason") or "").strip().rstrip(".")

    route_name = f"the {' and '.join(lines)}" if lines else "this route"
    sentences = [f"I'd take {route_name} to {destination}."]

    eta_copy = f"about {round(eta)} minutes" if isinstance(eta, (int, float)) else ""
    transfer_copy = ""
    if isinstance(transfers, int):
        transfer_copy = (
            "no transfers"
            if transfers == 0
            else f"{transfers} transfer{'s' if transfers != 1 else ''}"
        )
    if eta_copy:
        detail = f"{eta_copy} with {transfer_copy}" if transfer_copy else eta_copy
        sentences.append(f"It takes {detail}.")
    elif transfer_copy:
        sentences.append(f"It has {transfer_copy}.")

    if reason:
        if reason.casefold() == "fastest":
            reason = "it is the fastest option"
        else:
            reason = reason[:1].lower() + reason[1:]
        sentences.append(f"I picked it because {reason}.")

    return " ".join(sentences)


_INTERNAL_CARD_REFERENCE = re.compile(
    r"\b(?:route\s+)?card\s+[`'\"]?(?:rc|mock)[_-][A-Za-z0-9_-]+[`'\"]?\s*(?:[—–-]\s*)?",
    re.IGNORECASE,
)
_OPAQUE_CARD_ID = re.compile(r"\b(?:rc|mock)[_-][A-Za-z0-9_-]{4,}\b", re.IGNORECASE)


def _sanitize_rider_text(text: str) -> str:
    """Remove model formatting and internal route identifiers from prose.

    The prompt is the primary contract. This boundary sanitizer is a final
    guard so an ignored instruction cannot expose implementation details in
    the passenger-facing stream or persist them into conversation history.
    """
    sanitized = _INTERNAL_CARD_REFERENCE.sub("", text)
    sanitized = _OPAQUE_CARD_ID.sub("the route", sanitized)
    sanitized = re.sub(r"\*\*(.*?)\*\*", r"\1", sanitized, flags=re.DOTALL)
    sanitized = re.sub(r"__(.*?)__", r"\1", sanitized, flags=re.DOTALL)
    sanitized = re.sub(r"`([^`]+)`", r"\1", sanitized)
    sanitized = re.sub(r"(?m)^\s*#{1,6}\s+", "", sanitized)
    sanitized = re.sub(r"~(?=\d)", "about ", sanitized)
    return sanitized


def _sanitize_token_events(
    token_events: list[agent_events.TokenEvent],
) -> list[agent_events.TokenEvent]:
    original = "".join(event.text for event in token_events)
    sanitized = _sanitize_rider_text(original)
    if sanitized == original:
        return token_events
    return [agent_events.TokenEvent(text=sanitized)] if sanitized else []


async def _call_model(
    *,
    stream_kwargs: dict,
    log_tag: str,
    retry_count: int,
) -> tuple[object | None, list[agent_events.TokenEvent], agent_events.ErrorEvent | None]:
    """Runs one `messages.stream()` call to completion, collecting its text
    deltas as `TokenEvent`s. Returns `(final_message, token_events, None)` on
    success or `(None, token_events, ErrorEvent)` on failure -- callers
    re-yield the token events as they stream and, on failure, the error
    event, then decide their own next step (break the round loop vs. give up
    on the wrap-up call). Shared by both the per-round model call and the
    forced wrap-up call, which otherwise duplicated this stream/collect/
    error-handle sequence verbatim."""
    attempts = max(1, int(retry_count) + 1)
    for attempt in range(1, attempts + 1):
        token_events: list[agent_events.TokenEvent] = []
        try:
            async with client.messages.stream(**stream_kwargs) as stream:
                async for delta in stream.text_stream:
                    token_events.append(agent_events.TokenEvent(text=delta))
                final_message = await stream.get_final_message()
            return final_message, _sanitize_token_events(token_events), None
        except Exception as exc:
            print(
                model_request.format_failure_log(
                    exc=exc,
                    kwargs=stream_kwargs,
                    log_tag=log_tag,
                    attempt=attempt,
                    attempts=attempts,
                )
            )
            if token_events or attempt >= attempts or not model_request.should_retry(exc):
                return None, token_events, model_request.error_event_for(exc)
            await asyncio.sleep(min(0.15 * attempt, 0.45))
    raise AssertionError("model retry loop exited unexpectedly")


async def _run_one_tool(name: str, tool_input: dict, ctx: ToolContext) -> ToolResult:
    """Always returns a ToolResult -- timeouts and exceptions are captured
    as ToolResult(ok=False, error=<short reason>), never raised."""
    spec = TOOL_REGISTRY.get(name)
    if spec is None:
        return ToolResult(ok=False, error=f"unknown tool '{name}'")
    try:
        result = await asyncio.wait_for(spec.executor(tool_input, ctx), timeout=spec.timeout_s)
    except asyncio.TimeoutError:
        return ToolResult(ok=False, error="timed out")
    except Exception as exc:
        # Provider exception details can contain credential-bearing URLs.
        print(f"[agent-loop] tool {name} failed type={type(exc).__name__}")
        return ToolResult(ok=False, error="tool failed")
    return result


def _rider_excluded_modes(message: str, session: dict) -> set[str]:
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


def _constrained_tool_input(
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
        normalized["avoid_crowds"] = bool(
            normalized.get("avoid_crowds") or parsed_intent.avoid_crowds
        )
        normalized["include_first_leg_arrivals"] = mode_policy.optional_enrichment
    elif name == "lookup_arrivals":
        normalized["limit"] = min(
            int(normalized.get("limit") or 3),
            2 if mode_policy.mode == "quick" else 3,
        )
    return normalized


def _required_arrival_input(
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
    if ctx.origin:
        tool_input["user_location"] = {
            "latitude": ctx.origin.get("lat"),
            "longitude": ctx.origin.get("lng"),
        }
    return tool_input


async def _execute_tool_round(
    tool_use_blocks: list,
    ctx: ToolContext,
    session: dict,
    tool_calls_this_turn: list[tuple[str, dict]],
    excluded_modes: set[str],
    mode_policy: agent_policy.AgentModePolicy,
    parsed_intent: intelligence.ParsedIntent,
) -> AsyncIterator:
    """Emits tool_start/tool_end/route_card events for one round of (possibly
    parallel) tool calls, and yields the assembled tool_result message last
    (wrapped so the caller can tell it apart from an AgentEvent)."""
    tool_inputs = {
        block.id: _constrained_tool_input(
            getattr(block, "name", ""),
            getattr(block, "input", {}) or {},
            excluded_modes,
            mode_policy=mode_policy,
            parsed_intent=parsed_intent,
        )
        for block in tool_use_blocks
    }

    for block in tool_use_blocks:
        name = getattr(block, "name", "")
        tool_input = tool_inputs[block.id]
        spec = TOOL_REGISTRY.get(name)
        label = spec.label_fn(tool_input) if spec else f"Using {name}…"
        yield agent_events.ToolStartEvent(tool_call_id=block.id, tool=name, label=label)

    start_times = {block.id: time.monotonic() for block in tool_use_blocks}
    outcomes = await asyncio.gather(
        *(
            _run_one_tool(getattr(block, "name", ""), tool_inputs[block.id], ctx)
            for block in tool_use_blocks
        )
    )

    tool_result_content = []
    for block, result in zip(tool_use_blocks, outcomes):
        name = getattr(block, "name", "")
        tool_input = tool_inputs[block.id]
        duration_ms = round((time.monotonic() - start_times[block.id]) * 1000)
        tool_calls_this_turn.append((name, tool_input))

        if result.ok:
            wrapped = {"source": name, "data": result.data, "untrusted": True}
            tool_result_content.append(
                {"type": "tool_result", "tool_use_id": block.id, "content": json.dumps(wrapped, default=str)}
            )
            yield agent_events.ToolEndEvent(
                tool_call_id=block.id, tool=name, ok=True, duration_ms=duration_ms, summary=result.summary or None
            )
            if result.summary:
                session_module.append_tool_summary(session, name, result.summary)
            if result.session_route_cards:
                session_module.add_route_cards(session, result.session_route_cards)
            if name == "plan_trip":
                session_module.clear_pending_trip(session)
            for ev in result.events:
                yield ev
        else:
            reason = result.error or "tool failed"
            wrapped = {"source": name, "data": {"error": reason}, "untrusted": True}
            tool_result_content.append(
                {
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": json.dumps(wrapped, default=str),
                    "is_error": True,
                }
            )
            yield agent_events.ToolEndEvent(
                tool_call_id=block.id, tool=name, ok=False, duration_ms=duration_ms, summary=reason
            )
            if name == "plan_trip":
                session_module.mark_pending_trip_failed(session, tool_input, reason)

    yield {"__tool_result_message__": {"role": "user", "content": tool_result_content}}


async def _stream_turn(
    *,
    session: dict,
    session_id: str,
    turn_id: str,
    message: str,
    ctx: ToolContext,
    selected_card_id: str | None,
    response_presentation: str,
    trace: TurnTrace | None,
) -> AsyncIterator[agent_events.AgentEvent]:
    mode_policy = agent_policy.policy_for_mode(response_presentation)
    parsed_intent = intelligence.parse_intent(message)
    if intelligence.is_new_trip_request(message):
        session_module.reset_for_new_trip(session)
    system_blocks = _system_blocks()
    excluded_modes = _rider_excluded_modes(message, session)
    messages = _messages_from_history(session.get("history") or [])
    context_block = agent_prompt.build_turn_context(
        session,
        ctx.now_et,
        ctx.origin,
        selected_card_id,
        response_presentation,
    )
    messages.append({"role": "user", "content": f"{message}\n\n{context_block}"})
    session_module.append_history(session, "user", message)

    turn_start = time.monotonic()
    stop_reason_out = "end_turn"
    input_tokens = 0
    output_tokens = 0
    model_ms_total = 0.0
    tools_ms_total = 0.0
    round_num = 0
    text_parts: list[str] = []
    tool_calls_this_turn: list[tuple[str, dict]] = []
    recommended_route_card: agent_events.RouteCardEvent | None = None
    tool_failures = 0

    try:
        if parsed_intent.intent == "destination_discovery":
            required_input = {
                "query": message,
                "max_results": 2 if mode_policy.mode == "quick" else 3,
            }
            tool_call_id = f"required-places-{turn_id}"
            spec = TOOL_REGISTRY.get("poi_search")
            label = (
                spec.label_fn(required_input)
                if spec is not None
                else "Finding grounded places"
            )
            yield agent_events.ToolStartEvent(
                tool_call_id=tool_call_id,
                tool="poi_search",
                label=label,
            )
            tool_started = time.monotonic()
            result = await _run_one_tool("poi_search", required_input, ctx)
            tools_ms_total += (time.monotonic() - tool_started) * 1000
            tool_calls_this_turn.append(("poi_search", required_input))
            evidence = (
                result.data
                if result.ok
                else {
                    "source_status": "provider_unavailable",
                    "error": result.error or "place search unavailable",
                }
            )
            messages[-1]["content"] += (
                "\n\n<required_evidence source=\"poi_search\">"
                + json.dumps(evidence, separators=(",", ":"), default=str)
                + "</required_evidence>"
            )
            yield agent_events.ToolEndEvent(
                tool_call_id=tool_call_id,
                tool="poi_search",
                ok=result.ok,
                duration_ms=round((time.monotonic() - tool_started) * 1000),
                summary=result.summary or result.error or None,
            )
            if not result.ok:
                tool_failures += 1
            if result.summary:
                session_module.append_tool_summary(session, "poi_search", result.summary)

        if parsed_intent.intent == "arrival_lookup":
            required_input = _required_arrival_input(parsed_intent, ctx, mode_policy)
            if required_input is not None:
                tool_call_id = f"required-arrivals-{turn_id}"
                spec = TOOL_REGISTRY.get("lookup_arrivals")
                label = (
                    spec.label_fn(required_input)
                    if spec is not None
                    else f"Checking {required_input['route_id']} arrivals"
                )
                yield agent_events.ToolStartEvent(
                    tool_call_id=tool_call_id,
                    tool="lookup_arrivals",
                    label=label,
                )
                tool_started = time.monotonic()
                result = await _run_one_tool("lookup_arrivals", required_input, ctx)
                tools_ms_total += (time.monotonic() - tool_started) * 1000
                tool_calls_this_turn.append(("lookup_arrivals", required_input))
                evidence = result.data if result.ok else {"source_status": "provider_unavailable"}
                messages[-1]["content"] += (
                    "\n\n<required_evidence source=\"lookup_arrivals\">"
                    + json.dumps(evidence, separators=(",", ":"), default=str)
                    + "</required_evidence>"
                )
                yield agent_events.ToolEndEvent(
                    tool_call_id=tool_call_id,
                    tool="lookup_arrivals",
                    ok=result.ok,
                    duration_ms=round((time.monotonic() - tool_started) * 1000),
                    summary=result.summary or result.error or None,
                )
                if not result.ok:
                    tool_failures += 1
                if result.summary:
                    session_module.append_tool_summary(session, "lookup_arrivals", result.summary)
                for event in result.events:
                    yield event

        needs_wrapup = False

        while True:
            round_num += 1
            if time.monotonic() - turn_start > AGENT_TURN_DEADLINE_S:
                needs_wrapup = True
                stop_reason_out = "deadline"
                break
            if round_num > mode_policy.max_rounds:
                needs_wrapup = True
                stop_reason_out = "max_rounds"
                break

            stream_kwargs = _build_stream_kwargs(
                force_final=False,
                messages=messages,
                system_blocks=system_blocks,
                mode_policy=mode_policy,
            )
            model_call_start = time.monotonic()
            final_message, token_events, error_event = await _call_model(
                stream_kwargs=stream_kwargs,
                log_tag="model call",
                retry_count=mode_policy.retry_count,
            )
            model_ms_total += (time.monotonic() - model_call_start) * 1000
            for token_event in token_events:
                text_parts.append(token_event.text)
                yield token_event
            if error_event is not None:
                yield error_event
                stop_reason_out = "error"
                break

            usage = getattr(final_message, "usage", None)
            input_tokens += int(getattr(usage, "input_tokens", 0) or 0)
            output_tokens += int(getattr(usage, "output_tokens", 0) or 0)
            messages.append({"role": "assistant", "content": final_message.content})

            tool_use_blocks = [b for b in final_message.content if getattr(b, "type", None) == "tool_use"]
            if final_message.stop_reason != "tool_use" or not tool_use_blocks:
                stop_reason_out = "end_turn"
                break

            tool_round_start = time.monotonic()
            tool_result_message = None
            async for item in _execute_tool_round(
                tool_use_blocks,
                ctx,
                session,
                tool_calls_this_turn,
                excluded_modes,
                mode_policy,
                parsed_intent,
            ):
                if isinstance(item, dict) and "__tool_result_message__" in item:
                    tool_result_message = item["__tool_result_message__"]
                else:
                    if isinstance(item, agent_events.RouteCardEvent) and item.role == "recommended":
                        recommended_route_card = item
                    if isinstance(item, agent_events.ToolEndEvent) and not item.ok:
                        tool_failures += 1
                    yield item
            tools_ms_total += (time.monotonic() - tool_round_start) * 1000
            messages.append(tool_result_message)

            if time.monotonic() - turn_start > AGENT_TURN_DEADLINE_S:
                needs_wrapup = True
                stop_reason_out = "deadline"
                break

        if needs_wrapup:
            messages.append({"role": "user", "content": WRAP_UP_INSTRUCTION})
            stream_kwargs = _build_stream_kwargs(
                force_final=True,
                messages=messages,
                system_blocks=system_blocks,
                mode_policy=mode_policy,
            )
            model_call_start = time.monotonic()
            final_message, token_events, error_event = await _call_model(
                stream_kwargs=stream_kwargs,
                log_tag="wrap-up model call",
                retry_count=mode_policy.retry_count,
            )
            model_ms_total += (time.monotonic() - model_call_start) * 1000
            for token_event in token_events:
                text_parts.append(token_event.text)
                yield token_event
            if error_event is not None:
                yield error_event
                stop_reason_out = "error"
            else:
                usage = getattr(final_message, "usage", None)
                input_tokens += int(getattr(usage, "input_tokens", 0) or 0)
                output_tokens += int(getattr(usage, "output_tokens", 0) or 0)
                messages.append({"role": "assistant", "content": final_message.content})

        if not text_parts and recommended_route_card is not None:
            fallback_text = _route_card_text_fallback(recommended_route_card)
            text_parts.append(fallback_text)
            yield agent_events.TokenEvent(text=fallback_text)
        if stop_reason_out == "end_turn" and not any(
            name == "plan_trip" for name, _tool_input in tool_calls_this_turn
        ):
            resume_offer = session_module.consume_resume_offer(session)
            if resume_offer:
                prefix = "\n\n" if text_parts else ""
                text_parts.append(prefix + resume_offer)
                yield agent_events.TokenEvent(text=prefix + resume_offer)
    except Exception as exc:
        print(f"[agent-loop] turn failed unexpectedly type={type(exc).__name__}")
        yield agent_events.ErrorEvent(
            code="internal", message="Something went wrong handling that request.", retryable=True
        )
        stop_reason_out = "error"
    finally:
        final_text = "".join(text_parts)
        if final_text:
            session_module.append_history(session, "assistant", final_text)
        session_module.extract_slots(session, tool_calls_this_turn)
        if trace is not None:
            trace.tool_calls = list(tool_calls_this_turn)
            trace.final_text = final_text
        budget.record_usage_cost(input_tokens, output_tokens)

    total_ms = (time.monotonic() - turn_start) * 1000
    required_tools = ",".join(parsed_intent.required_evidence.required_tools()) or "none"
    # Single per-turn timing/spend log line, mirroring trips.py's `[trip]`
    # line -- no message text, session id truncated to 6 chars per the
    # existing sess[:6] logging convention (session.py, routers/agent_chat.py).
    print(
        f"[agent] turn={turn_id} sess={session_id[:6]} rounds={round_num} "
        f"mode={mode_policy.mode} model={agent_policy.safe_model_label(mode_policy.model)} "
        f"candidate_budget={mode_policy.max_route_candidates} retries={mode_policy.retry_count} "
        f"required_tools={required_tools} optional_enrichment="
        f"{int(mode_policy.optional_enrichment)} tools={len(tool_calls_this_turn)} "
        f"tool_failures={tool_failures} model_ms={model_ms_total:.0f} "
        f"tools_ms={tools_ms_total:.0f} total_ms={total_ms:.0f} "
        f"in_tok={input_tokens} out_tok={output_tokens} stop={stop_reason_out}"
    )

    yield agent_events.DoneEvent(
        session_id=session_id,
        turn_id=turn_id,
        stop_reason=stop_reason_out,
        usage={"input_tokens": input_tokens, "output_tokens": output_tokens},
    )


async def run_agent_turn(
    *,
    session: dict,
    session_id: str,
    turn_id: str,
    message: str,
    now_et: str,
    gtfs=None,
    origin: dict | None = None,
    selected_card_id: str | None = None,
    response_presentation: str = "auto",
    trace: TurnTrace | None = None,
) -> AsyncIterator[agent_events.AgentEvent]:
    """Run one conversational turn, yielding SSE events as they happen.

    `session` is mutated in place (history/slots/route_cards); the caller
    (routers/agent_chat.py) owns persisting it via session.save_session --
    including on early client disconnect, which is why this function does
    not save the session itself.
    """
    yield agent_events.MetaEvent(session_id=session_id, turn_id=turn_id)

    if AGENT_MOCK_MODE:
        async for event in _stream_mock_turn(
            session=session,
            session_id=session_id,
            turn_id=turn_id,
            message=message,
            origin=origin,
            trace=trace,
            response_presentation=response_presentation,
        ):
            yield event
        return

    def _reject(code: str, text: str, retryable: bool) -> tuple[agent_events.ErrorEvent, agent_events.DoneEvent]:
        return (
            agent_events.ErrorEvent(code=code, message=text, retryable=retryable),
            agent_events.DoneEvent(
                session_id=session_id, turn_id=turn_id, stop_reason="error", usage={"input_tokens": 0, "output_tokens": 0}
            ),
        )

    if not budget.agent_enabled():
        for event in _reject("budget_exceeded", "Trip planning is temporarily unavailable.", True):
            yield event
        return

    if not budget.check_session_rate_limit(session_id):
        for event in _reject("rate_limited", "Too many messages in the last minute -- try again shortly.", True):
            yield event
        return

    if budget.daily_spend_exceeded():
        for event in _reject("budget_exceeded", "Today's usage budget is reached -- please try again tomorrow.", False):
            yield event
        return

    arithmetic_answer = intelligence.evaluate_simple_arithmetic(message)
    if arithmetic_answer is not None:
        session_module.append_history(session, "user", message)
        resume_offer = session_module.consume_resume_offer(session)
        final_text = arithmetic_answer + (f"\n\n{resume_offer}" if resume_offer else "")
        session_module.append_history(session, "assistant", final_text)
        if trace is not None:
            trace.final_text = final_text
        yield agent_events.TokenEvent(text=final_text)
        yield agent_events.DoneEvent(
            session_id=session_id,
            turn_id=turn_id,
            stop_reason="end_turn",
            usage={"input_tokens": 0, "output_tokens": 0},
        )
        return

    sem = budget.concurrency_semaphore()
    if sem.locked():
        for event in _reject("rate_limited", "SmartRoute is busy helping other riders -- try again shortly.", True):
            yield event
        return

    ctx = ToolContext(gtfs=gtfs, session=session, turn_id=turn_id, now_et=now_et, origin=origin)

    async with sem:
        async for event in _stream_turn(
            session=session,
            session_id=session_id,
            turn_id=turn_id,
            message=message,
            ctx=ctx,
            selected_card_id=selected_card_id,
            response_presentation=response_presentation,
            trace=trace,
        ):
            yield event
