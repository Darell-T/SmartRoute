"""Deadline-bound live-model turn execution for the conversational agent."""

from __future__ import annotations

import logging
import time
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal

from app import observability
from app.services.agent import events as agent_events
from app.services.agent import passenger_output, public_surface
from app.services.agent import session as session_module
from app.services.agent.model import policy as agent_policy
from app.services.agent.model import prompt as agent_prompt
from app.services.agent.model import stream as model_stream
from app.services.agent.tools import ToolContext
from app.services.agent.turn import completion as turn_completion  # test patch point
from app.services.agent.turn.evidence import TurnEvidence
from app.services.agent.turn.finalization import (
    extract_safe_usage,
    finalize_trace,
    finalize_turn,
    record_capability_attempts,
    record_first_visible_token,
    record_model_call,
    record_model_round,
    record_phase_ms,
    stage_timings,
)
from app.services.agent.turn.ledger import TurnToolLedger
from app.services.agent.turn.tool_round import (
    TurnDeadlineReached,
    mixed_terminal_and_capability,
)

_LOGGER = logging.getLogger(__name__)

if TYPE_CHECKING:
    from app.services.agent.turn.finalization import TurnTrace


@dataclass(frozen=True)
class TurnDependencies:
    """Loop-owned seams kept injectable for compatibility and test patches."""

    deadline_s: float
    client: object
    tool_registry: dict
    make_ledger: Callable[[], TurnToolLedger]
    system_blocks: Callable[[], list[dict]]
    messages_from_history: Callable[[list[dict]], list[dict]]
    build_stream_kwargs: Callable[..., dict]
    tools_for_state: Callable[..., list[dict]]
    sanitize_rider_text: Callable[[str], str]
    rider_excluded_modes: Callable[[str, dict], set[str]]
    rider_excluded_route_ids: Callable[[str, dict], tuple[str, ...]]
    execute_tool_round: Callable[..., AsyncIterator]


@dataclass
class TurnState:
    """Mutable runtime and accounting state for one rider turn."""

    session: dict
    session_id: str
    turn_id: str
    ctx: ToolContext
    trace: TurnTrace | None
    dependencies: TurnDependencies
    mode_policy: agent_policy.AgentModePolicy
    initial_mode: str
    turn_start: float
    deadline_monotonic: float
    stage_ms: dict[str, float]
    messages: list[dict]
    tool_ledger: TurnToolLedger
    excluded_modes: set[str]
    excluded_route_ids: tuple[str, ...]
    stop_reason: str = "end_turn"
    needs_wrapup: bool = False
    clarification_pending: bool = False
    round_num: int = 0
    tool_failures: int = 0
    model_call_count: int = 0
    server_tool_call_count: int = 0
    retry_count_total: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    model_ms_total: float = 0.0
    tools_ms_total: float = 0.0
    text_parts: list[str] = field(default_factory=list)
    tool_calls: list[tuple[str, dict]] = field(default_factory=list)
    recommended_route_card: agent_events.RouteCardEvent | None = None
    first_route_card_ms: float | None = None
    server_tool_continuation_tools: list[dict] | None = None
    pending_server_web_used: bool = False
    pending_server_web_succeeded: bool = True

    def begin_round(self) -> bool:
        self.round_num += 1
        if time.monotonic() >= self.deadline_monotonic:
            self.stop_reason = "deadline"
            return False
        if self.round_num > self.mode_policy.max_rounds:
            self.needs_wrapup = True
            self.stop_reason = "max_rounds"
            return False
        return True

    def tools_for_round(self) -> tuple[list[dict], bool]:
        continuing = self.server_tool_continuation_tools is not None
        tools = (
            self.server_tool_continuation_tools
            if self.server_tool_continuation_tools is not None
            else self.dependencies.tools_for_state(
                self.mode_policy,
                session=self.session,
                include_web=self.ctx.turn_evidence.may_offer_web(),
                turn_evidence=self.ctx.turn_evidence,
            )
        )
        self.server_tool_continuation_tools = None
        return tools, continuing

    def text_event(self, text: str) -> agent_events.TokenEvent | None:
        body = passenger_output.append_text(self.text_parts, text)
        if not body.strip():
            return None
        record_first_visible_token(
            self.stage_ms,
            self.ctx.telemetry,
            self.turn_start,
            body,
        )
        return agent_events.TokenEvent(text=body)

    def fallback_event(self) -> agent_events.TokenEvent | None:
        limit = (
            3
            if self.mode_policy.mode == "quick"
            else self.mode_policy.max_presented_places
        )
        return self.text_event(
            turn_completion.fallback_text(
                self.ctx.turn_evidence,
                session_id=self.session_id,
                limit=limit,
            )
        )

    def record_route_card(self, event: agent_events.RouteCardEvent) -> None:
        if event.role != "recommended":
            return
        self.recommended_route_card = event
        if self.first_route_card_ms is not None:
            return
        self.first_route_card_ms = (time.monotonic() - self.turn_start) * 1000
        self.stage_ms["route_card_emit_ms"] = self.first_route_card_ms
        record_phase_ms(
            self.ctx.telemetry,
            "route_card_emit_ms",
            self.first_route_card_ms,
        )


@dataclass(frozen=True)
class ModelIteration:
    """Normalized result of one primary-model request."""

    outcome: model_stream.ModelCallCompleted
    final_message: object | None
    tool_use_blocks: tuple[object, ...]
    turn_tools: list[dict]
    allowed_tool_names: frozenset[str]


@dataclass
class _StreamCapture:
    outcome: model_stream.ModelCallCompleted | None = None
    first_token_ms: float | None = None


DirectiveKind = Literal["continue", "stop", "tools"]


@dataclass(frozen=True)
class ModelDirective:
    """One explicit next step selected after a normalized model iteration."""

    kind: DirectiveKind
    event: agent_events.AgentEvent | None = None
    tool_use_blocks: tuple[object, ...] = ()
    allowed_tool_names: frozenset[str] = frozenset()


@dataclass(frozen=True)
class CapabilityIteration:
    """Normalized result of one parallel capability round."""

    result_message: dict | None
    outcomes: tuple[tuple[str, dict, object], ...]
    deadline_reached: bool


async def stream_model_iteration(
    state: TurnState,
    *,
    request_options_for: Callable[[object, frozenset[str]], dict[str, object]],
) -> AsyncIterator[agent_events.AgentEvent | ModelIteration]:
    """Run one model request and normalize its accounting and tool selection."""

    turn_tools, continuing_server_turn = state.tools_for_round()
    allowed_tool_names = frozenset(
        schema["name"] for schema in turn_tools if schema.get("name")
    )
    request_options = request_options_for(
        state.ctx.turn_evidence,
        allowed_tool_names,
    )
    stream_kwargs = state.dependencies.build_stream_kwargs(
        messages=state.messages,
        system_blocks=state.dependencies.system_blocks(),
        mode_policy=state.mode_policy,
        tools=turn_tools,
        request_options=request_options,
        allow_server_tool_continuation=continuing_server_turn,
    )
    model_call_start = time.monotonic()
    if state.round_num == 1:
        state.stage_ms["conversation_request_start_ms"] = (
            model_call_start - state.turn_start
        ) * 1000
        record_phase_ms(
            state.ctx.telemetry,
            "conversation_request_start_ms",
            state.stage_ms["conversation_request_start_ms"],
        )

    capture = _StreamCapture()
    async for event in _capture_model_events(
        state,
        stream_kwargs,
        model_call_start,
        capture,
    ):
        yield event
    yield _finish_model_iteration(
        state,
        capture,
        turn_tools,
        allowed_tool_names,
        model_call_start,
    )


async def _capture_model_events(
    state: TurnState,
    stream_kwargs: dict,
    model_call_start: float,
    capture: _StreamCapture,
) -> AsyncIterator[agent_events.AgentEvent]:
    async for event in model_stream.stream_model_call(
        client=state.dependencies.client,
        stream_kwargs=stream_kwargs,
        log_tag="model call",
        retry_count=state.mode_policy.retry_count,
        sanitize_text=state.dependencies.sanitize_rider_text,
        deadline_monotonic=state.deadline_monotonic,
        web_timeout_s=state.mode_policy.web_research_timeout_s,
    ):
        if isinstance(event, model_stream.ModelCallCompleted):
            capture.outcome = event
            if event.web_sources:
                yield agent_events.SourcesEvent(
                    turn_id=state.turn_id,
                    sources=event.web_sources,
                )
        elif isinstance(event, agent_events.TokenEvent):
            if capture.first_token_ms is None and event.text.strip():
                capture.first_token_ms = (time.monotonic() - model_call_start) * 1000
        else:
            if isinstance(event, agent_events.ToolEndEvent) and not event.ok:
                state.tool_failures += 1
            yield event


def _finish_model_iteration(
    state: TurnState,
    capture: _StreamCapture,
    turn_tools: list[dict],
    allowed_tool_names: frozenset[str],
    model_call_start: float,
) -> ModelIteration:
    outcome = capture.outcome
    if outcome is None:
        raise RuntimeError("model stream ended without a completion record")
    state.stage_ms["web_search_ms"] += outcome.web_search_ms
    state.server_tool_call_count += outcome.server_tool_call_count
    state.model_call_count += 1
    state.retry_count_total += max(0, outcome.attempts - 1)
    duration_ms = (time.monotonic() - model_call_start) * 1000
    state.model_ms_total += duration_ms
    first_token_ms = outcome.first_token_ms or capture.first_token_ms
    _record_conversation_timings(state, first_token_ms)
    final_message = outcome.final_message
    error_event = outcome.error
    usage = getattr(final_message, "usage", None) if final_message is not None else None
    record_model_call(
        state.ctx.telemetry,
        role="conversation",
        provider="anthropic",
        model=state.mode_policy.model,
        duration_ms=duration_ms,
        outcome="complete" if error_event is None else error_event.code,
        first_token_ms=first_token_ms,
        usage=usage,
    )
    tool_use_blocks = tuple(
        block
        for block in getattr(final_message, "content", ())
        if getattr(block, "type", None) == "tool_use"
    )
    record_model_round(
        state.trace,
        round_number=state.round_num,
        offered_capabilities=allowed_tool_names,
        selected_capabilities=[
            str(getattr(block, "name", "") or "") for block in tool_use_blocks
        ],
        duration_ms=duration_ms,
        first_token_ms=first_token_ms,
        stop_reason=str(getattr(final_message, "stop_reason", "") or ""),
        outcome="complete" if error_event is None else error_event.code,
    )
    return ModelIteration(
        outcome=outcome,
        final_message=final_message,
        tool_use_blocks=tool_use_blocks,
        turn_tools=turn_tools,
        allowed_tool_names=allowed_tool_names,
    )


def _record_conversation_timings(
    state: TurnState,
    first_token_ms: float | None,
) -> None:
    if state.round_num == 1 and isinstance(first_token_ms, (int, float)):
        state.stage_ms["conversation_first_token_ms"] = state.stage_ms.get(
            "conversation_request_start_ms", 0.0
        ) + float(first_token_ms)
        record_phase_ms(
            state.ctx.telemetry,
            "conversation_first_token_ms",
            state.stage_ms["conversation_first_token_ms"],
        )
    if state.round_num == 1:
        state.stage_ms["conversation_complete_ms"] = (
            time.monotonic() - state.turn_start
        ) * 1000
        record_phase_ms(
            state.ctx.telemetry,
            "conversation_complete_ms",
            state.stage_ms["conversation_complete_ms"],
        )


def resolve_model_iteration(
    state: TurnState,
    iteration: ModelIteration,
) -> ModelDirective:
    """Translate provider protocol state into the agent loop's next operation."""

    outcome = iteration.outcome
    if outcome.web_timed_out:
        state.ctx.turn_evidence.note_web(ok=False)
        state.messages.append(
            {"role": "user", "content": agent_policy.WEB_TIMEOUT_CONTINUATION}
        )
        return ModelDirective("continue")
    if outcome.error is not None:
        state.stop_reason = "deadline" if outcome.error.code == "deadline" else "error"
        return ModelDirective("stop", event=outcome.error)
    if iteration.final_message is None:
        raise RuntimeError("model stream completed without a final message")

    usage = extract_safe_usage(getattr(iteration.final_message, "usage", None))
    state.input_tokens += usage.get("input_tokens", 0)
    state.output_tokens += usage.get("output_tokens", 0)
    stop_reason = str(getattr(iteration.final_message, "stop_reason", "") or "")
    if stop_reason == "max_tokens":
        state.stop_reason = "error"
        return ModelDirective(
            "stop",
            event=agent_events.ErrorEvent(
                code="response_incomplete",
                message="SmartRoute could not finish that response. Please try again.",
                retryable=True,
            ),
        )

    state.messages.append(
        {"role": "assistant", "content": iteration.final_message.content}
    )
    if outcome.web_used:
        state.pending_server_web_used = True
        state.pending_server_web_succeeded = bool(
            state.pending_server_web_succeeded and outcome.web_succeeded
        )
    if stop_reason == "pause_turn":
        state.server_tool_continuation_tools = iteration.turn_tools
        return ModelDirective("continue")
    if state.pending_server_web_used:
        state.ctx.turn_evidence.note_web(ok=state.pending_server_web_succeeded)
        state.pending_server_web_used = False
        state.pending_server_web_succeeded = True

    action_attached = bool(iteration.tool_use_blocks) or bool(
        outcome.server_tool_call_count
    )
    if not action_attached:
        state.ctx.turn_evidence.prose_without_tool_rounds += 1
        if (
            state.ctx.turn_evidence.prose_without_tool_rounds == 1
            and state.round_num < state.mode_policy.max_rounds
            and time.monotonic() < state.deadline_monotonic
        ):
            state.messages.append(
                {"role": "user", "content": agent_policy.NO_TOOL_CORRECTION}
            )
            return ModelDirective("continue")
        state.stop_reason = "end_turn"
        return ModelDirective("stop", event=state.fallback_event())
    if stop_reason != "tool_use" or not iteration.tool_use_blocks:
        state.stop_reason = (
            "clarification_required" if state.clarification_pending else "end_turn"
        )
        return ModelDirective("stop")
    if mixed_terminal_and_capability(
        [str(getattr(block, "name", "") or "") for block in iteration.tool_use_blocks]
    ):
        state.messages.append(
            {
                "role": "user",
                "content": (
                    "A terminal tool cannot be mixed with another capability in "
                    "the same round. Call one terminal tool alone, or finish the "
                    "capability first."
                ),
            }
        )
        return ModelDirective("continue")
    return ModelDirective(
        "tools",
        tool_use_blocks=iteration.tool_use_blocks,
        allowed_tool_names=iteration.allowed_tool_names,
    )


async def stream_capability_iteration(
    state: TurnState,
    tool_use_blocks: tuple[object, ...],
    allowed_tool_names: frozenset[str],
) -> AsyncIterator[agent_events.AgentEvent | CapabilityIteration]:
    """Execute one capability round and normalize all rider-visible events."""

    started = time.monotonic()
    result_message: dict | None = None
    outcomes: tuple[tuple[str, dict, object], ...] = ()
    deadline_reached = False
    async for item in state.dependencies.execute_tool_round(
        list(tool_use_blocks),
        state.ctx,
        state.session,
        state.tool_calls,
        state.excluded_modes,
        state.mode_policy,
        state.stage_ms,
        state.deadline_monotonic,
        state.tool_ledger,
        tool_registry=state.dependencies.tool_registry,
        excluded_route_ids=state.excluded_route_ids,
        allowed_tool_names=allowed_tool_names,
    ):
        if isinstance(item, dict) and "__tool_result_message__" in item:
            result_message = item["__tool_result_message__"]
            deadline_reached = bool(item.get("__deadline_reached__"))
            outcomes = tuple(item.get("__tool_outcomes__") or ())
            continue
        if isinstance(item, agent_events.ToolStartEvent):
            if (
                item.tool == "prepare_route_options"
                and "plan_trip_tool_start_ms" not in state.stage_ms
            ):
                state.stage_ms["plan_trip_tool_start_ms"] = (
                    time.monotonic() - state.turn_start
                ) * 1000
                record_phase_ms(
                    state.ctx.telemetry,
                    "plan_trip_tool_start_ms",
                    state.stage_ms["plan_trip_tool_start_ms"],
                )
            yield item
            continue
        if isinstance(item, agent_events.TokenEvent):
            event = state.text_event(item.text)
            if event is not None:
                yield event
            continue
        if isinstance(item, agent_events.ArrivalCardEvent):
            state.clarification_pending = item.resolution_status == "ambiguous"
        elif isinstance(item, agent_events.RouteCardEvent):
            state.record_route_card(item)
        elif isinstance(item, agent_events.ToolEndEvent) and not item.ok:
            state.tool_failures += 1
        yield item
    state.tools_ms_total += (time.monotonic() - started) * 1000
    yield CapabilityIteration(
        result_message=result_message,
        outcomes=outcomes,
        deadline_reached=deadline_reached,
    )


def apply_capability_iteration(
    state: TurnState,
    iteration: CapabilityIteration,
) -> None:
    """Commit capability observations to the one turn-evidence owner."""

    if iteration.result_message is not None:
        state.messages.append(iteration.result_message)
    for name, tool_input, result in iteration.outcomes:
        state.ctx.turn_evidence.record_capability_result(name, tool_input, result)
        result_data = getattr(result, "data", None)
        if (
            name == "complete_turn"
            and getattr(result, "ok", False)
            and isinstance(result_data, dict)
        ):
            state.clarification_pending = result_data.get("outcome") == "clarification"
    record_capability_attempts(state.trace, list(iteration.outcomes))
    turn_completion.apply_completion(
        session=state.session,
        evidence=state.ctx.turn_evidence,
        tool_outcomes=list(iteration.outcomes),
        selected_by_model=state.recommended_route_card is not None,
    )


@dataclass
class _ModelPhase:
    iteration: ModelIteration | None = None

    def result(self) -> ModelIteration:
        if self.iteration is None:
            raise RuntimeError("model iteration ended without a result")
        return self.iteration


@dataclass
class _CapabilityPhase:
    iteration: CapabilityIteration | None = None

    def result(self) -> CapabilityIteration:
        if self.iteration is None:
            raise RuntimeError("capability iteration ended without a result")
        return self.iteration


def _initialize_turn_context(
    ctx: ToolContext,
    mode_policy: agent_policy.AgentModePolicy,
    message: str,
) -> None:
    ctx.agent_mode = mode_policy.mode
    ctx.agent_model = mode_policy.model
    ctx.agent_explanation_style = mode_policy.explanation_style
    ctx.rider_message = message
    ctx.discovery_refinement = None
    ctx.turn_evidence = TurnEvidence()
    ctx.telemetry["mode"] = mode_policy.mode
    ctx.telemetry["capability_surface_version"] = (
        ctx.turn_evidence.capability_surface_version
    )


def _prepare_turn_messages(
    *,
    dependencies: TurnDependencies,
    session: dict,
    message: str,
    ctx: ToolContext,
    selected_card_id: str | None,
    response_presentation: str,
    turn_id: str,
) -> list[dict]:
    messages = dependencies.messages_from_history(
        session_module.model_context_history(session, message)
    )
    context_block = agent_prompt.build_turn_context(
        session,
        ctx.now_et,
        ctx.origin,
        selected_card_id,
        response_presentation,
        session_id=ctx.session_id,
    )
    messages.append({"role": "user", "content": f"{message}\n\n{context_block}"})
    session_module.append_history(session, "user", message, turn_id=turn_id)
    return messages


def _create_turn_state(
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
) -> TurnState:
    turn_start = time.monotonic()
    mode_policy = agent_policy.policy_for_mode(response_presentation)
    _initialize_turn_context(ctx, mode_policy, message)
    preprocessing_started = time.monotonic()
    if trace is not None:
        trace.rider_message = message
    messages = _prepare_turn_messages(
        dependencies=dependencies,
        session=session,
        message=message,
        ctx=ctx,
        selected_card_id=selected_card_id,
        response_presentation=response_presentation,
        turn_id=turn_id,
    )
    return TurnState(
        session=session,
        session_id=session_id,
        turn_id=turn_id,
        ctx=ctx,
        trace=trace,
        dependencies=dependencies,
        mode_policy=mode_policy,
        initial_mode=mode_policy.mode,
        turn_start=turn_start,
        deadline_monotonic=turn_start + dependencies.deadline_s,
        stage_ms=stage_timings(trace, preprocessing_started),
        messages=messages,
        tool_ledger=dependencies.make_ledger(),
        excluded_modes=dependencies.rider_excluded_modes(message, session),
        excluded_route_ids=dependencies.rider_excluded_route_ids(message, session),
    )


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
    """Run the visible ReAct loop: model, capability, evidence, completion."""

    state = _create_turn_state(
        session=session,
        session_id=session_id,
        turn_id=turn_id,
        message=message,
        ctx=ctx,
        selected_card_id=selected_card_id,
        response_presentation=response_presentation,
        trace=trace,
        dependencies=dependencies,
    )
    turn_span = observability.start_turn(
        ctx, turn_id=turn_id, mode=response_presentation
    )
    with observability.activate(turn_span):
        async for event in _stream_turn_body(state):
            yield event
        observability.finish_turn(
            turn_span,
            state.ctx.telemetry,
        )


async def _stream_turn_body(state: TurnState) -> AsyncIterator[agent_events.AgentEvent]:
    """Run one turn while the caller owns the parent telemetry span."""

    yield agent_events.ReasoningEvent(text="Thinking through your request…")
    try:
        async for event in _stream_react_loop(state):
            yield event
        async for event in _stream_post_loop_response(state):
            yield event
    except TurnDeadlineReached:
        state.stop_reason = "deadline"
    except Exception as exc:
        _LOGGER.exception(
            "agent turn failed unexpectedly type=%s",
            type(exc).__name__,
        )
        yield agent_events.ErrorEvent(
            code="internal",
            message="Something went wrong handling that request.",
            retryable=True,
        )
        state.stop_reason = "error"
    finally:
        _finalize_state(state)
    yield agent_events.DoneEvent(
        session_id=state.session_id,
        turn_id=state.turn_id,
        stop_reason=state.stop_reason,
        usage={
            "input_tokens": state.input_tokens,
            "output_tokens": state.output_tokens,
        },
    )


async def _stream_react_loop(
    state: TurnState,
) -> AsyncIterator[agent_events.AgentEvent]:
    while state.begin_round():
        model_phase = _ModelPhase()
        async for event in _stream_model_phase(state, model_phase):
            yield event

        directive = resolve_model_iteration(state, model_phase.result())
        if directive.event is not None:
            yield directive.event
        if directive.kind == "continue":
            continue
        if directive.kind == "stop":
            break

        capability_phase = _CapabilityPhase()
        async for event in _stream_capability_phase(
            state,
            capability_phase,
            directive.tool_use_blocks,
            directive.allowed_tool_names,
        ):
            yield event

        capability_iteration = capability_phase.result()
        if capability_iteration.deadline_reached:
            yield agent_events.ErrorEvent(
                code="deadline",
                message="The response took too long. Please try again.",
                retryable=True,
            )
            state.stop_reason = "deadline"
            break

        apply_capability_iteration(state, capability_iteration)
        if state.ctx.turn_evidence.terminal:
            state.stop_reason = _terminal_stop_reason(state)
            break
        if time.monotonic() >= state.deadline_monotonic:
            state.stop_reason = "deadline"
            break


async def _stream_model_phase(
    state: TurnState,
    phase: _ModelPhase,
) -> AsyncIterator[agent_events.AgentEvent]:
    async for event in stream_model_iteration(
        state,
        request_options_for=_initial_goal_request_options,
    ):
        if isinstance(event, ModelIteration):
            phase.iteration = event
        else:
            yield event


async def _stream_capability_phase(
    state: TurnState,
    phase: _CapabilityPhase,
    tool_use_blocks: tuple[object, ...],
    allowed_tool_names: frozenset[str],
) -> AsyncIterator[agent_events.AgentEvent]:
    async for event in stream_capability_iteration(
        state,
        tool_use_blocks,
        allowed_tool_names,
    ):
        if isinstance(event, CapabilityIteration):
            phase.iteration = event
        else:
            yield event


async def _stream_post_loop_response(
    state: TurnState,
) -> AsyncIterator[agent_events.AgentEvent]:
    if state.needs_wrapup:
        event = state.fallback_event()
        if event is not None:
            yield event
    if state.stop_reason != "end_turn" or _used_route_capability(state.tool_calls):
        return
    resume_offer = session_module.consume_resume_offer(state.session)
    if resume_offer:
        event = state.text_event(resume_offer)
        if event is not None:
            yield event


def _used_route_capability(tool_calls: list[tuple[str, dict]]) -> bool:
    route_tool_names = {"prepare_route_options", "present_route"}
    return any(name in route_tool_names for name, _tool_input in tool_calls)


def _terminal_stop_reason(state: TurnState) -> str:
    return "clarification_required" if state.clarification_pending else "end_turn"


def _finalize_state(state: TurnState) -> None:
    finalize_started = time.monotonic()
    telemetry = state.ctx.turn_evidence.telemetry()
    contract = state.ctx.turn_evidence.turn_contract
    if contract is not None:
        telemetry.update(
            turn_completion.completion_telemetry(contract, state.ctx.turn_evidence)
        )
    state.ctx.telemetry.update(telemetry)
    finalize_trace(state.trace, state.ctx.turn_evidence)
    state.ctx.telemetry["model_rounds"] = state.round_num
    finalize_turn(
        session=state.session,
        tool_calls=state.tool_calls,
        final_text="".join(state.text_parts),
        trace=state.trace,
        initial_mode=state.initial_mode,
        final_mode=state.mode_policy.mode,
        model=state.mode_policy.model,
        max_route_candidates=state.mode_policy.max_route_candidates,
        optional_enrichment=state.mode_policy.optional_enrichment,
        retry_policy_count=state.mode_policy.retry_count,
        model_call_count=state.model_call_count,
        server_tool_call_count=state.server_tool_call_count,
        retry_count_total=state.retry_count_total,
        tool_ledger=state.tool_ledger,
        input_tokens=state.input_tokens,
        output_tokens=state.output_tokens,
        stage_ms=state.stage_ms,
        turn_start=state.turn_start,
        finalize_started=finalize_started,
        turn_id=state.turn_id,
        session_id=state.session_id,
        telemetry=state.ctx.telemetry,
        required_tool_names=public_surface.called_evidence_capabilities(
            state.tool_calls
        ),
        round_num=state.round_num,
        tool_failures=state.tool_failures,
        model_ms_total=state.model_ms_total,
        tools_ms_total=state.tools_ms_total,
        stop_reason=state.stop_reason,
    )


def _initial_goal_request_options(
    evidence: object,
    allowed_tool_names: frozenset[str],
) -> dict[str, object]:
    """Enforce the next state-required contract step without choosing semantics."""

    if (
        getattr(evidence, "turn_contract", None) is None
        and "declare_goals" in allowed_tool_names
    ):
        # Leave parallel tool calls enabled. Anthropic can attach a valid first
        # capability to the declaration in the same request.
        return {"tool_choice": {"type": "tool", "name": "declare_goals"}}
    presenter = public_surface.required_presenter_tool(
        evidence,
        allowed_tool_names,
    )
    if presenter:
        return {"tool_choice": {"type": "tool", "name": presenter}}
    return {}
