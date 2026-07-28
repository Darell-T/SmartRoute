"""Deadline-bound live-model turn execution for the conversational agent."""

from __future__ import annotations

import time
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from app.services.agent import budget
from app.services.agent import events as agent_events
from app.services.agent import intelligence
from app.services.agent import model_stream
from app.services.agent import policy as agent_policy
from app.services.agent import prompt as agent_prompt
from app.services.agent import quick_escalation
from app.services.agent import session as session_module
from app.services.agent.tool_round import TurnDeadlineReached
from app.services.agent.tools import ToolContext
from app.services.agent.turn_ledger import TurnToolLedger

if TYPE_CHECKING:
    from app.services.agent.loop import TurnTrace


@dataclass(frozen=True)
class TurnDependencies:
    """Loop-owned seams kept injectable for compatibility and test patches."""

    deadline_s: float
    client: object
    tool_registry: dict
    wrap_up_instruction: str
    make_ledger: Callable[[], TurnToolLedger]
    system_blocks: Callable[[], list[dict]]
    messages_from_history: Callable[[list[dict]], list[dict]]
    build_stream_kwargs: Callable[..., dict]
    tools_for_intent: Callable[..., list[dict]]
    route_card_text_fallback: Callable[[agent_events.RouteCardEvent], str]
    sanitize_rider_text: Callable[[str], str]
    required_arrival_input: Callable[..., dict | None]
    arrival_response: Callable[[dict], tuple[str, str]]
    rider_excluded_modes: Callable[[str, dict], set[str]]
    execute_tool_round: Callable[..., AsyncIterator]


def _stage_timings(trace: TurnTrace | None, intent_started: float) -> dict[str, float]:
    stage_ms = {
        "intent_ms": (time.monotonic() - intent_started) * 1000,
        "session_load_ms": 0.0,
        "place_resolution_ms": 0.0,
        "web_search_ms": 0.0,
        "place_normalization_ms": 0.0,
        "route_provider_ms": 0.0,
        "evidence_ms": 0.0,
        "mta_ms": 0.0,
        "ticketmaster_ms": 0.0,
        "arrival_lookup_ms": 0.0,
        "stop_resolution_ms": 0.0,
        "feed_fetch_ms": 0.0,
        "feed_parse_ms": 0.0,
        "render_ms": 0.0,
        "scoring_ms": 0.0,
        "model_ms": 0.0,
        "stream_finalize_ms": 0.0,
        "total_ms": 0.0,
    }
    if trace is not None:
        for stage, duration in trace.stage_ms.items():
            if stage in stage_ms:
                stage_ms[stage] = max(0.0, float(duration))
    return stage_ms


async def _stream_model_round(
    *,
    dependencies: TurnDependencies,
    stream_kwargs: dict,
    log_tag: str,
    mode_policy: agent_policy.AgentModePolicy,
    deadline_monotonic: float,
) -> AsyncIterator[agent_events.AgentEvent | model_stream.ModelCallCompleted]:
    async for event in model_stream.stream_model_call(
        client=dependencies.client,
        stream_kwargs=stream_kwargs,
        log_tag=log_tag,
        retry_count=mode_policy.retry_count,
        sanitize_text=dependencies.sanitize_rider_text,
        deadline_monotonic=deadline_monotonic,
    ):
        yield event


async def stream_turn(
    *,
    session: dict,
    session_id: str,
    turn_id: str,
    message: str,
    ctx: ToolContext,
    selected_card_id: str | None,
    response_presentation: str,
    trace: TurnTrace | None,
    dependencies: TurnDependencies,
) -> AsyncIterator[agent_events.AgentEvent]:
    """Run the live portion of one turn and preserve its existing terminal contract."""
    turn_start = time.monotonic()
    deadline_monotonic = turn_start + dependencies.deadline_s
    mode_policy = agent_policy.policy_for_mode(response_presentation)
    initial_mode = mode_policy.mode
    escalation_reason: str | None = None
    intent_started = time.monotonic()
    parsed_intent = intelligence.parse_intent(message)
    if mode_policy.mode == "quick" and parsed_intent.avoid_crowds:
        escalation_reason = "explicit_crowd_evidence"
        mode_policy = agent_policy.policy_for_mode("auto")
        print(f"[agent-escalation] turn={turn_id} quick_to_auto=1 reason={escalation_reason}")
    stage_ms = _stage_timings(trace, intent_started)
    turn_tools = dependencies.tools_for_intent(parsed_intent, mode_policy)
    if intelligence.is_new_trip_request(message):
        session_module.reset_for_new_trip(session)
    messages = dependencies.messages_from_history(session.get("history") or [])
    context_block = agent_prompt.build_turn_context(session, ctx.now_et, ctx.origin, selected_card_id, response_presentation)
    messages.append({"role": "user", "content": f"{message}\n\n{context_block}"})
    session_module.append_history(session, "user", message)

    stop_reason_out = "end_turn"
    input_tokens = output_tokens = 0
    model_ms_total = tools_ms_total = 0.0
    round_num = tool_failures = model_call_count = server_tool_call_count = retry_count_total = 0
    text_parts: list[str] = []
    tool_calls_this_turn: list[tuple[str, dict]] = []
    recommended_route_card: agent_events.RouteCardEvent | None = None
    deterministic_arrival = False
    tool_ledger = dependencies.make_ledger()
    excluded_modes = dependencies.rider_excluded_modes(message, session)

    try:
        if parsed_intent.intent == "arrival_lookup":
            required_input = dependencies.required_arrival_input(parsed_intent, ctx, mode_policy)
            if required_input is None:
                clarification = "Which train or bus route do you want arrivals for?"
                text_parts.append(clarification)
                stop_reason_out = "clarification_required"
                deterministic_arrival = True
                yield agent_events.TokenEvent(text=clarification)
            else:
                tool_call_id = f"required-arrivals-{turn_id}"
                spec = dependencies.tool_registry.get("lookup_arrivals")
                label = spec.label_fn(required_input) if spec else f"Checking {required_input['route_id']} arrivals"
                yield agent_events.ToolStartEvent(tool_call_id=tool_call_id, tool="lookup_arrivals", label=label)
                tool_started = time.monotonic()
                result = await tool_ledger.execute("lookup_arrivals", required_input, ctx, deadline_monotonic=deadline_monotonic)
                tool_duration_ms = (time.monotonic() - tool_started) * 1000
                tools_ms_total += tool_duration_ms
                stage_ms["arrival_lookup_ms"] += tool_duration_ms
                for stage, duration in result.timings.items():
                    if stage in stage_ms:
                        stage_ms[stage] += max(0.0, float(duration))
                tool_calls_this_turn.append(("lookup_arrivals", required_input))
                yield agent_events.ToolEndEvent(
                    tool_call_id=tool_call_id, tool="lookup_arrivals", ok=result.ok,
                    duration_ms=round((time.monotonic() - tool_started) * 1000), summary=result.summary or result.error or None,
                )
                if not result.ok:
                    tool_failures += 1
                if result.error == "turn deadline reached":
                    yield agent_events.ErrorEvent(code="deadline", message="The response took too long. Please try again.", retryable=True)
                    stop_reason_out = "deadline"
                    raise TurnDeadlineReached
                reason = quick_escalation.reason_for_tool_result("lookup_arrivals", result, required=True)
                if mode_policy.mode == "quick" and escalation_reason is None and reason:
                    escalation_reason = reason
                    mode_policy = agent_policy.policy_for_mode("auto")
                    print(f"[agent-escalation] turn={turn_id} quick_to_auto=1 reason={reason}")
                if result.summary:
                    session_module.append_tool_summary(session, "lookup_arrivals", result.summary)
                for event in result.events:
                    yield event
                render_started = time.monotonic()
                response_text, stop_reason_out = dependencies.arrival_response(
                    result.data if result.ok and isinstance(result.data, dict) else {"route_id": required_input["route_id"], "source_status": "provider_unavailable"}
                )
                stage_ms["render_ms"] += (time.monotonic() - render_started) * 1000
                text_parts.append(response_text)
                deterministic_arrival = True
                yield agent_events.TokenEvent(text=response_text)

        needs_wrapup = False
        while not deterministic_arrival:
            round_num += 1
            if time.monotonic() >= deadline_monotonic:
                stop_reason_out = "deadline"
                break
            if round_num > mode_policy.max_rounds:
                needs_wrapup = True
                stop_reason_out = "max_rounds"
                break
            stream_kwargs = dependencies.build_stream_kwargs(
                force_final=False, messages=messages, system_blocks=dependencies.system_blocks(), mode_policy=mode_policy, tools=turn_tools
            )
            model_call_start = time.monotonic()
            model_outcome: model_stream.ModelCallCompleted | None = None
            async for model_event in _stream_model_round(
                dependencies=dependencies, stream_kwargs=stream_kwargs, log_tag="model call", mode_policy=mode_policy, deadline_monotonic=deadline_monotonic
            ):
                if isinstance(model_event, model_stream.ModelCallCompleted):
                    model_outcome = model_event
                    continue
                if isinstance(model_event, agent_events.TokenEvent):
                    text_parts.append(model_event.text)
                if isinstance(model_event, agent_events.ToolEndEvent) and not model_event.ok:
                    tool_failures += 1
                yield model_event
            if model_outcome is None:
                raise RuntimeError("model stream ended without a completion record")
            final_message = model_outcome.final_message
            error_event = model_outcome.error
            stage_ms["web_search_ms"] += model_outcome.web_search_ms
            server_tool_call_count += model_outcome.server_tool_call_count
            model_call_count += 1
            retry_count_total += max(0, model_outcome.attempts - 1)
            model_ms_total += (time.monotonic() - model_call_start) * 1000
            if error_event is not None:
                yield error_event
                stop_reason_out = "deadline" if error_event.code == "deadline" else "error"
                break
            if final_message is None:
                raise RuntimeError("model stream completed without a final message")
            usage = getattr(final_message, "usage", None)
            input_tokens += int(getattr(usage, "input_tokens", 0) or 0)
            output_tokens += int(getattr(usage, "output_tokens", 0) or 0)
            messages.append({"role": "assistant", "content": final_message.content})
            tool_use_blocks = [block for block in final_message.content if getattr(block, "type", None) == "tool_use"]
            if final_message.stop_reason != "tool_use" or not tool_use_blocks:
                stop_reason_out = "end_turn"
                break
            tool_round_start = time.monotonic()
            tool_result_message = None
            async for item in dependencies.execute_tool_round(
                tool_use_blocks, ctx, session, tool_calls_this_turn, excluded_modes, mode_policy, parsed_intent,
                stage_ms, deadline_monotonic, tool_ledger, tool_registry=dependencies.tool_registry,
            ):
                if isinstance(item, dict) and "__tool_result_message__" in item:
                    tool_result_message = item["__tool_result_message__"]
                    reason = item.get("__quick_escalation_reason__")
                    if mode_policy.mode == "quick" and escalation_reason is None and reason:
                        escalation_reason = str(reason)
                        mode_policy = agent_policy.policy_for_mode("auto")
                        print(f"[agent-escalation] turn={turn_id} quick_to_auto=1 reason={escalation_reason}")
                else:
                    if isinstance(item, agent_events.RouteCardEvent) and item.role == "recommended":
                        recommended_route_card = item
                    if isinstance(item, agent_events.ToolEndEvent) and not item.ok:
                        tool_failures += 1
                    yield item
            tools_ms_total += (time.monotonic() - tool_round_start) * 1000
            messages.append(tool_result_message)
            if time.monotonic() >= deadline_monotonic:
                stop_reason_out = "deadline"
                break

        if needs_wrapup and time.monotonic() < deadline_monotonic:
            messages.append({"role": "user", "content": dependencies.wrap_up_instruction})
            stream_kwargs = dependencies.build_stream_kwargs(
                force_final=True, messages=messages, system_blocks=dependencies.system_blocks(), mode_policy=mode_policy, tools=turn_tools
            )
            model_call_start = time.monotonic()
            model_outcome = None
            async for model_event in _stream_model_round(
                dependencies=dependencies, stream_kwargs=stream_kwargs, log_tag="wrap-up model call", mode_policy=mode_policy, deadline_monotonic=deadline_monotonic
            ):
                if isinstance(model_event, model_stream.ModelCallCompleted):
                    model_outcome = model_event
                    continue
                if isinstance(model_event, agent_events.TokenEvent):
                    text_parts.append(model_event.text)
                if isinstance(model_event, agent_events.ToolEndEvent) and not model_event.ok:
                    tool_failures += 1
                yield model_event
            if model_outcome is None:
                raise RuntimeError("wrap-up stream ended without a completion record")
            final_message = model_outcome.final_message
            error_event = model_outcome.error
            stage_ms["web_search_ms"] += model_outcome.web_search_ms
            server_tool_call_count += model_outcome.server_tool_call_count
            model_call_count += 1
            retry_count_total += max(0, model_outcome.attempts - 1)
            model_ms_total += (time.monotonic() - model_call_start) * 1000
            if error_event is not None:
                yield error_event
                stop_reason_out = "deadline" if error_event.code == "deadline" else "error"
            else:
                if final_message is None:
                    raise RuntimeError("wrap-up stream completed without a final message")
                usage = getattr(final_message, "usage", None)
                input_tokens += int(getattr(usage, "input_tokens", 0) or 0)
                output_tokens += int(getattr(usage, "output_tokens", 0) or 0)
                messages.append({"role": "assistant", "content": final_message.content})

        if not text_parts and recommended_route_card is not None:
            fallback_text = dependencies.route_card_text_fallback(recommended_route_card)
            text_parts.append(fallback_text)
            yield agent_events.TokenEvent(text=fallback_text)
        if stop_reason_out == "end_turn" and not any(name == "plan_trip" for name, _tool_input in tool_calls_this_turn):
            resume_offer = session_module.consume_resume_offer(session)
            if resume_offer:
                prefix = "\n\n" if text_parts else ""
                text_parts.append(prefix + resume_offer)
                yield agent_events.TokenEvent(text=prefix + resume_offer)
    except TurnDeadlineReached:
        stop_reason_out = "deadline"
    except Exception as exc:
        print(f"[agent-loop] turn failed unexpectedly type={type(exc).__name__}")
        yield agent_events.ErrorEvent(code="internal", message="Something went wrong handling that request.", retryable=True)
        stop_reason_out = "error"
    finally:
        finalize_started = time.monotonic()
        final_text = "".join(text_parts)
        if final_text:
            session_module.append_history(session, "assistant", final_text)
        session_module.extract_slots(session, tool_calls_this_turn)
        if trace is not None:
            trace.tool_calls = list(tool_calls_this_turn)
            trace.final_text = final_text
            trace.initial_mode = initial_mode
            trace.final_mode = mode_policy.mode
            trace.escalation_reason = escalation_reason
            trace.model_call_count = model_call_count
            trace.tool_call_count = len(tool_calls_this_turn) + server_tool_call_count
            trace.model_tool_use_count = len(tool_calls_this_turn) + server_tool_call_count
            trace.provider_tool_execution_count = tool_ledger.total_executions + server_tool_call_count
            trace.retry_count = retry_count_total
        budget.record_usage_cost(input_tokens, output_tokens)
        stage_ms["stream_finalize_ms"] = (time.monotonic() - finalize_started) * 1000

    total_ms = (time.monotonic() - turn_start) * 1000
    stage_ms["model_ms"] = model_ms_total
    stage_ms["evidence_ms"] = max(stage_ms["evidence_ms"], stage_ms["mta_ms"] + stage_ms["ticketmaster_ms"])
    stage_ms["total_ms"] = total_ms
    if trace is not None:
        trace.stage_ms = dict(stage_ms)
    required_tools = ",".join(parsed_intent.required_evidence.required_tools()) or "none"
    print(
        f"[agent] turn={turn_id} sess={session_id[:6]} rounds={round_num} "
        f"mode={initial_mode} final_mode={mode_policy.mode} escalation={escalation_reason or 'none'} "
        f"model={agent_policy.safe_model_label(mode_policy.model)} candidate_budget={mode_policy.max_route_candidates} "
        f"retries={mode_policy.retry_count} required_tools={required_tools} optional_enrichment={int(mode_policy.optional_enrichment)} "
        f"model_tool_uses={len(tool_calls_this_turn) + server_tool_call_count} provider_tool_executions={tool_ledger.total_executions + server_tool_call_count} "
        f"tool_failures={tool_failures} intent_ms={stage_ms['intent_ms']:.0f} session_load_ms={stage_ms['session_load_ms']:.0f} "
        f"place_resolution_ms={stage_ms['place_resolution_ms']:.0f} web_search_ms={stage_ms['web_search_ms']:.0f} "
        f"place_normalization_ms={stage_ms['place_normalization_ms']:.0f} route_provider_ms={stage_ms['route_provider_ms']:.0f} "
        f"evidence_ms={stage_ms['evidence_ms']:.0f} mta_ms={stage_ms['mta_ms']:.0f} ticketmaster_ms={stage_ms['ticketmaster_ms']:.0f} "
        f"arrival_lookup_ms={stage_ms['arrival_lookup_ms']:.0f} stop_resolution_ms={stage_ms['stop_resolution_ms']:.0f} "
        f"feed_fetch_ms={stage_ms['feed_fetch_ms']:.0f} feed_parse_ms={stage_ms['feed_parse_ms']:.0f} render_ms={stage_ms['render_ms']:.0f} "
        f"scoring_ms={stage_ms['scoring_ms']:.0f} model_ms={model_ms_total:.0f} stream_finalize_ms={stage_ms['stream_finalize_ms']:.0f} "
        f"tools_ms={tools_ms_total:.0f} total_ms={total_ms:.0f} model_calls={model_call_count} model_call_count={model_call_count} "
        f"model_tool_uses={len(tool_calls_this_turn) + server_tool_call_count} provider_tool_executions={tool_ledger.total_executions + server_tool_call_count} "
        f"retry_count={retry_count_total} in_tok={input_tokens} out_tok={output_tokens} stop={stop_reason_out}"
    )
    yield agent_events.DoneEvent(
        session_id=session_id,
        turn_id=turn_id,
        stop_reason=stop_reason_out,
        usage={"input_tokens": input_tokens, "output_tokens": output_tokens},
    )
