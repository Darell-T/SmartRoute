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
import time
from typing import AsyncIterator

import anthropic

from app.services.agent import budget
from app.services.agent import events as agent_events
from app.services.agent import prompt as agent_prompt
from app.services.agent import session as session_module
from app.services.agent.tools import TOOL_REGISTRY, TOOLS, ToolContext, ToolResult

client = anthropic.AsyncAnthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

AGENT_MODEL = os.getenv("AGENT_MODEL", "claude-sonnet-5")
AGENT_MAX_ROUNDS = int(os.getenv("AGENT_MAX_ROUNDS", "5"))
AGENT_TURN_DEADLINE_S = float(os.getenv("AGENT_TURN_DEADLINE_S", "50"))
AGENT_MAX_TOKENS_PER_ROUND = int(os.getenv("AGENT_MAX_TOKENS_PER_ROUND", "1024"))
AGENT_WRAPUP_MAX_TOKENS = 300

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


def _build_stream_kwargs(*, force_final: bool, messages: list[dict], system_blocks: list[dict]) -> dict:
    kwargs: dict = {
        "model": AGENT_MODEL,
        "messages": messages,
        "system": system_blocks,
    }
    if force_final:
        kwargs["max_tokens"] = AGENT_WRAPUP_MAX_TOKENS
        kwargs["tool_choice"] = {"type": "none"}
    else:
        kwargs["max_tokens"] = AGENT_MAX_TOKENS_PER_ROUND
        kwargs["tools"] = TOOLS
    return kwargs


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
        print(f"[agent-loop] tool {name} failed: {type(exc).__name__}: {exc!r}")
        return ToolResult(ok=False, error="tool failed")
    return result


async def _execute_tool_round(
    tool_use_blocks: list,
    ctx: ToolContext,
    session: dict,
    tool_calls_this_turn: list[tuple[str, dict]],
) -> AsyncIterator:
    """Emits tool_start/tool_end/route_card events for one round of (possibly
    parallel) tool calls, and yields the assembled tool_result message last
    (wrapped so the caller can tell it apart from an AgentEvent)."""
    for block in tool_use_blocks:
        name = getattr(block, "name", "")
        tool_input = getattr(block, "input", {}) or {}
        spec = TOOL_REGISTRY.get(name)
        label = spec.label_fn(tool_input) if spec else f"Using {name}…"
        yield agent_events.ToolStartEvent(tool_call_id=block.id, tool=name, label=label)

    start_times = {block.id: time.monotonic() for block in tool_use_blocks}
    outcomes = await asyncio.gather(
        *(
            _run_one_tool(getattr(block, "name", ""), getattr(block, "input", {}) or {}, ctx)
            for block in tool_use_blocks
        )
    )

    tool_result_content = []
    for block, result in zip(tool_use_blocks, outcomes):
        name = getattr(block, "name", "")
        tool_input = getattr(block, "input", {}) or {}
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

    yield {"__tool_result_message__": {"role": "user", "content": tool_result_content}}


async def _stream_turn(
    *,
    session: dict,
    session_id: str,
    turn_id: str,
    message: str,
    ctx: ToolContext,
    selected_card_id: str | None,
    trace: TurnTrace | None,
) -> AsyncIterator[agent_events.AgentEvent]:
    system_blocks = _system_blocks()
    messages = _messages_from_history(session.get("history") or [])
    context_block = agent_prompt.build_turn_context(session, ctx.now_et, ctx.origin, selected_card_id)
    messages.append({"role": "user", "content": f"{message}\n\n{context_block}"})
    session_module.append_history(session, "user", message)

    turn_start = time.monotonic()
    stop_reason_out = "end_turn"
    input_tokens = 0
    output_tokens = 0
    text_parts: list[str] = []
    tool_calls_this_turn: list[tuple[str, dict]] = []

    try:
        round_num = 0
        needs_wrapup = False

        while True:
            round_num += 1
            if time.monotonic() - turn_start > AGENT_TURN_DEADLINE_S:
                needs_wrapup = True
                stop_reason_out = "deadline"
                break
            if round_num > AGENT_MAX_ROUNDS:
                needs_wrapup = True
                stop_reason_out = "max_rounds"
                break

            stream_kwargs = _build_stream_kwargs(force_final=False, messages=messages, system_blocks=system_blocks)
            try:
                async with client.messages.stream(**stream_kwargs) as stream:
                    async for delta in stream.text_stream:
                        text_parts.append(delta)
                        yield agent_events.TokenEvent(text=delta)
                    final_message = await stream.get_final_message()
            except Exception as exc:
                print(f"[agent-loop] model call failed: {type(exc).__name__}: {exc!r}")
                yield agent_events.ErrorEvent(
                    code="upstream_error",
                    message="The routing assistant is temporarily unavailable.",
                    retryable=True,
                )
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

            tool_result_message = None
            async for item in _execute_tool_round(tool_use_blocks, ctx, session, tool_calls_this_turn):
                if isinstance(item, dict) and "__tool_result_message__" in item:
                    tool_result_message = item["__tool_result_message__"]
                else:
                    yield item
            messages.append(tool_result_message)

            if time.monotonic() - turn_start > AGENT_TURN_DEADLINE_S:
                needs_wrapup = True
                stop_reason_out = "deadline"
                break

        if needs_wrapup:
            messages.append({"role": "user", "content": WRAP_UP_INSTRUCTION})
            stream_kwargs = _build_stream_kwargs(force_final=True, messages=messages, system_blocks=system_blocks)
            try:
                async with client.messages.stream(**stream_kwargs) as stream:
                    async for delta in stream.text_stream:
                        text_parts.append(delta)
                        yield agent_events.TokenEvent(text=delta)
                    final_message = await stream.get_final_message()
                usage = getattr(final_message, "usage", None)
                input_tokens += int(getattr(usage, "input_tokens", 0) or 0)
                output_tokens += int(getattr(usage, "output_tokens", 0) or 0)
                messages.append({"role": "assistant", "content": final_message.content})
            except Exception as exc:
                print(f"[agent-loop] wrap-up model call failed: {type(exc).__name__}: {exc!r}")
                yield agent_events.ErrorEvent(
                    code="upstream_error",
                    message="The routing assistant is temporarily unavailable.",
                    retryable=True,
                )
                stop_reason_out = "error"
    except Exception as exc:
        print(f"[agent-loop] turn failed unexpectedly: {type(exc).__name__}: {exc!r}")
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
    trace: TurnTrace | None = None,
) -> AsyncIterator[agent_events.AgentEvent]:
    """Run one conversational turn, yielding SSE events as they happen.

    `session` is mutated in place (history/slots/route_cards); the caller
    (routers/agent_chat.py) owns persisting it via session.save_session --
    including on early client disconnect, which is why this function does
    not save the session itself.
    """
    yield agent_events.MetaEvent(session_id=session_id, turn_id=turn_id)

    def _reject(code: str, text: str, retryable: bool) -> agent_events.ErrorEvent:
        return agent_events.ErrorEvent(code=code, message=text, retryable=retryable)

    if not budget.agent_enabled():
        yield _reject("budget_exceeded", "The conversational agent is temporarily disabled.", True)
        yield agent_events.DoneEvent(
            session_id=session_id, turn_id=turn_id, stop_reason="error", usage={"input_tokens": 0, "output_tokens": 0}
        )
        return

    if not budget.check_session_rate_limit(session_id):
        yield _reject("rate_limited", "Too many messages in the last minute -- try again shortly.", True)
        yield agent_events.DoneEvent(
            session_id=session_id, turn_id=turn_id, stop_reason="error", usage={"input_tokens": 0, "output_tokens": 0}
        )
        return

    if budget.daily_spend_exceeded():
        yield _reject("budget_exceeded", "Today's usage budget is reached -- please try again tomorrow.", False)
        yield agent_events.DoneEvent(
            session_id=session_id, turn_id=turn_id, stop_reason="error", usage={"input_tokens": 0, "output_tokens": 0}
        )
        return

    sem = budget.concurrency_semaphore()
    if sem.locked():
        yield _reject("rate_limited", "The agent is busy helping other riders -- try again shortly.", True)
        yield agent_events.DoneEvent(
            session_id=session_id, turn_id=turn_id, stop_reason="error", usage={"input_tokens": 0, "output_tokens": 0}
        )
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
            trace=trace,
        ):
            yield event
