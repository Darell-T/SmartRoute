"""Public facade for the conversational agent's turn lifecycle.

`run_agent_turn()` preserves the SSE contract: a `meta` event first and one
terminal `done` event last. This facade owns request admission and the local
preview fixture; focused collaborators own live execution and finalization.
"""

from __future__ import annotations

import ast
import operator
import os
import re
from collections.abc import AsyncIterator

import anthropic

from app import observability, runtime
from app.services.agent import events as agent_events
from app.services.agent import public_surface, tool_input_policy
from app.services.agent import session as session_module
from app.services.agent.model import budget, mock_turn
from app.services.agent.model import policy as agent_policy
from app.services.agent.model import prompt as agent_prompt
from app.services.agent.model import request as model_request
from app.services.agent.tools import COMBINED_TOOL_REGISTRY, ToolContext, ToolResult
from app.services.agent.turn import stream as turn_stream
from app.services.agent.turn import tool_round
from app.services.agent.turn.finalization import TurnTrace
from app.services.agent.turn.ledger import TurnToolLedger as _TurnToolLedger

# Keep the active registry injectable at the turn entry point so deterministic
# tests and replay runners can replace executors without changing production
# registry construction.
TOOL_REGISTRY = COMBINED_TOOL_REGISTRY


# Application retries are classified in the model stream. Disable SDK retries so
# one configured retry never expands into hidden provider attempts.
client = observability.wrap_anthropic(
    anthropic.AsyncAnthropic(api_key=os.getenv("ANTHROPIC_API_KEY"), max_retries=0)
)

AGENT_TURN_DEADLINE_S = float(os.getenv("AGENT_TURN_DEADLINE_S", "50"))
AGENT_MOCK_MODE = runtime.enabled("AGENT_MOCK_MODE")
MAX_TOOL_EXECUTIONS_PER_TURN = 12
MAX_TOOL_EXECUTIONS_PER_NAME = 4

_SUPPORTED_BINOPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}
_SUPPORTED_UNARY = {ast.UAdd: operator.pos, ast.USub: operator.neg}


def evaluate_simple_arithmetic(message: object) -> str | None:
    """Evaluate a deliberately tiny arithmetic grammar, never arbitrary code."""

    candidate = str(message or "").strip().rstrip("?.")
    candidate = re.sub(
        r"^(?:what(?:'s| is)|calculate)\s+",
        "",
        candidate,
        flags=re.IGNORECASE,
    )
    if (
        not candidate
        or len(candidate) > 80
        or not re.fullmatch(r"[\d\s+\-*/().%]+", candidate)
    ):
        return None
    try:
        value = _eval_math_node(ast.parse(candidate, mode="eval").body)
    except (SyntaxError, ValueError, ZeroDivisionError, OverflowError):
        return None
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    return f"{value:.10g}." if isinstance(value, float) else f"{value}."


def _eval_math_node(node: ast.AST) -> int | float:
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        if abs(node.value) > 1_000_000_000:
            raise ValueError("number too large")
        return node.value
    if isinstance(node, ast.UnaryOp) and type(node.op) in _SUPPORTED_UNARY:
        return _SUPPORTED_UNARY[type(node.op)](_eval_math_node(node.operand))
    if isinstance(node, ast.BinOp) and type(node.op) in _SUPPORTED_BINOPS:
        left = _eval_math_node(node.left)
        right = _eval_math_node(node.right)
        if isinstance(node.op, ast.Pow) and abs(right) > 8:
            raise ValueError("exponent too large")
        result = _SUPPORTED_BINOPS[type(node.op)](left, right)
        if abs(result) > 1_000_000_000_000:
            raise ValueError("result too large")
        return result
    raise ValueError("unsupported expression")


_TurnDeadlineReached = tool_round.TurnDeadlineReached
_rider_excluded_modes = tool_input_policy.rider_excluded_modes
_rider_excluded_route_ids = tool_input_policy.rider_excluded_route_ids
_constrained_tool_input = tool_input_policy.constrained_tool_input


async def _run_one_tool(
    name: str,
    tool_input: dict,
    ctx: ToolContext,
    *,
    deadline_monotonic: float | None = None,
) -> ToolResult:
    return await tool_round.run_one_tool(
        name,
        tool_input,
        ctx,
        tool_registry=TOOL_REGISTRY,
        deadline_monotonic=deadline_monotonic,
    )


class TurnToolLedger(_TurnToolLedger):
    """Loop-compatible ledger that reads patchable loop policy at creation."""

    def __init__(self) -> None:
        super().__init__(
            run_tool=_run_one_tool,
            max_executions=MAX_TOOL_EXECUTIONS_PER_TURN,
            max_executions_per_name=MAX_TOOL_EXECUTIONS_PER_NAME,
        )

    async def execute(self, *args, **kwargs) -> ToolResult:
        # Keep historical loop-level patch points dynamic for focused tests and
        # local instrumentation that alter caps or the tool runner mid-turn.
        self.run_tool = _run_one_tool
        self.max_executions = MAX_TOOL_EXECUTIONS_PER_TURN
        self.max_executions_per_name = MAX_TOOL_EXECUTIONS_PER_NAME
        return await super().execute(*args, **kwargs)


async def _execute_tool_round(*args, **kwargs) -> AsyncIterator:
    kwargs.pop("tool_registry", None)
    async for item in tool_round.execute_tool_round(
        *args, tool_registry=TOOL_REGISTRY, **kwargs
    ):
        yield item


def _system_blocks() -> list[dict]:
    return [
        {
            "type": "text",
            "text": agent_prompt.active_system_prompt(),
            "cache_control": {"type": "ephemeral"},
        }
    ]


def _messages_from_history(history: list[dict]) -> list[dict]:
    """Restore conversational prose without fabricating protocol messages.

    Tool summaries are persisted for bounded audit/context purposes, but an
    old tool result is not a valid Anthropic ``tool_result`` in a later
    request.  Replaying it as bracketed assistant prose taught the model to
    imitate tool syntax instead of emitting a structured tool call.
    """

    messages: list[dict] = []
    for entry in history or []:
        role = entry.get("role")
        if role in {"user", "assistant"}:
            messages.append({"role": role, "content": entry.get("text", "")})
    return messages


def _build_stream_kwargs(
    *,
    messages: list[dict],
    system_blocks: list[dict],
    mode_policy: agent_policy.AgentModePolicy,
    tools: list[dict],
    request_options: dict | None = None,
    allow_server_tool_continuation: bool = False,
) -> dict:
    return model_request.build_stream_kwargs(
        messages=messages,
        system_blocks=system_blocks,
        mode_policy=mode_policy,
        tools=tools,
        request_options=request_options,
        allow_server_tool_continuation=allow_server_tool_continuation,
    )


def _web_search_tool(mode_policy: agent_policy.AgentModePolicy) -> dict:
    return {
        "type": "web_search_20250305",
        "name": "web_search",
        "max_uses": 1,
        "allowed_callers": ["direct"],
        "user_location": {
            "type": "approximate",
            "city": "New York City",
            "region": "New York",
            "country": "US",
            "timezone": "America/New_York",
        },
    }


def _schema_optional_parameter_count(schema: object) -> int:
    return public_surface.schema_optional_parameter_count(schema)


def _optional_parameter_count(tools: list[dict]) -> int:
    return public_surface.optional_parameter_count(tools)


def _tools_for_state(
    mode_policy: agent_policy.AgentModePolicy | None = None,
    session: dict | None = None,
    include_web: bool = False,
    turn_evidence: object | None = None,
    session_id: str | None = None,
) -> list[dict]:
    """Return tools valid for current turn state, never for rider phrasing."""

    mode_policy = mode_policy or agent_policy.policy_for_mode("auto")
    tools = [
        dict(schema)
        for schema in public_surface.schemas_for_state(
            (spec.schema for spec in TOOL_REGISTRY.values()),
            turn_evidence,
            session=session,
            session_id=session_id,
        )
    ]
    if include_web:
        tools.append(_web_search_tool(mode_policy))
    return tools


_INTERNAL_CARD_REFERENCE = re.compile(
    r"\b(?:route\s+)?card\s+[`'\"]?(?:rc|mock)[_-][A-Za-z0-9_-]+[`'\"]?\s*(?:[\u2014\u2013-]\s*)?",
    re.IGNORECASE,
)
_OPAQUE_CARD_ID = re.compile(r"\b(?:rc|mock)[_-][A-Za-z0-9_-]{4,}\b", re.IGNORECASE)
_OPAQUE_CANDIDATE_ID = re.compile(r"\b(?:cd|cs)_[A-Za-z0-9_-]{4,}\b", re.IGNORECASE)
_OPAQUE_PLACE_ID = re.compile(
    r"\b(?:pl|ds)_[A-Za-z0-9_-]{4,}\b|\bChIJ[A-Za-z0-9_-]{6,}\b",
    re.IGNORECASE,
)
_INTERNAL_RUNTIME_LINE = re.compile(
    r"(?im)^.*\b(?:prepare_route_options|present_route|get_place_details|"
    r"search_local_places|accessibility_status|lookup_arrivals|"
    r"check_area_conditions|transit_snapshot|event_lookup|venue_crowd_window|"
    r"lookup_facts|web_search|plan_trip|destination_place_id|place_id|"
    r"candidate_id|candidate_set_id|discovery_set_id|tool_use|tool_result|"
    r"function\s*call)\b.*(?:\r?\n|$)"
)
_FAKE_WAIT_SENTENCE = re.compile(
    r"(?i)(?:^|(?<=[.!?])\s+)(?:please\s+)?(?:give\s+me\s+(?:a\s+)?moment"
    r"(?:\s+for\s+(?:the\s+)?results)?|i(?:'m|\s+am)\s+waiting\s+for\s+"
    r"(?:the\s+)?(?:results|alternatives)|i\s+should\s+have\s+(?:those\s+)?"
    r"(?:results|candidates)\s+shortly|let\s+me\s+call\s+that\s+now)"
    r"[.!?]*(?=\s|$)"
)


def _sanitize_rider_text(text: str) -> str:
    sanitized = _INTERNAL_CARD_REFERENCE.sub("", text)
    sanitized = _OPAQUE_CARD_ID.sub("the route", sanitized)
    sanitized = _OPAQUE_CANDIDATE_ID.sub("the route", sanitized)
    sanitized = _OPAQUE_PLACE_ID.sub("the selected place", sanitized)
    sanitized = _INTERNAL_RUNTIME_LINE.sub("", sanitized)
    sanitized = _FAKE_WAIT_SENTENCE.sub("", sanitized)
    sanitized = re.sub(r"\*\*(.*?)\*\*", r"\1", sanitized, flags=re.DOTALL)
    sanitized = re.sub(r"__(.*?)__", r"\1", sanitized, flags=re.DOTALL)
    sanitized = re.sub(r"`([^`]+)`", r"\1", sanitized)
    sanitized = re.sub(r"(?m)^\s*#{1,6}\s+", "", sanitized)
    sanitized = re.sub(r"~(?=\d)", "about ", sanitized)
    return re.sub(r"[ \t]{2,}", " ", sanitized)


def _turn_dependencies(session_id: str = "") -> turn_stream.TurnDependencies:
    def tools_for_turn(*args, **kwargs):
        kwargs.setdefault("session_id", session_id)
        return _tools_for_state(*args, **kwargs)

    return turn_stream.TurnDependencies(
        deadline_s=AGENT_TURN_DEADLINE_S,
        client=client,
        tool_registry=TOOL_REGISTRY,
        make_ledger=TurnToolLedger,
        system_blocks=_system_blocks,
        messages_from_history=_messages_from_history,
        build_stream_kwargs=_build_stream_kwargs,
        tools_for_state=tools_for_turn,
        sanitize_rider_text=_sanitize_rider_text,
        rider_excluded_modes=_rider_excluded_modes,
        rider_excluded_route_ids=_rider_excluded_route_ids,
        execute_tool_round=_execute_tool_round,
    )


async def _stream_turn(**kwargs) -> AsyncIterator[agent_events.AgentEvent]:
    async for event in turn_stream.stream_turn(
        dependencies=_turn_dependencies(kwargs.get("session_id", "")), **kwargs
    ):
        yield event


def _rejection_events(
    session_id: str, turn_id: str, code: str, text: str, retryable: bool
) -> tuple[agent_events.ErrorEvent, agent_events.DoneEvent]:
    return (
        agent_events.ErrorEvent(code=code, message=text, retryable=retryable),
        agent_events.DoneEvent(
            session_id=session_id,
            turn_id=turn_id,
            stop_reason="error",
            usage={"input_tokens": 0, "output_tokens": 0},
        ),
    )


def _admission_rejection(session_id: str) -> tuple[str, str, bool] | None:
    """Return the first ordered request-admission rejection, if any."""
    if not budget.agent_enabled():
        return (
            "budget_exceeded",
            "Trip planning is temporarily unavailable.",
            True,
        )
    if not budget.check_session_rate_limit(session_id):
        return (
            "rate_limited",
            "Too many messages in the last minute -- try again shortly.",
            True,
        )
    if budget.daily_spend_exceeded():
        return (
            "budget_exceeded",
            "Today's usage budget is reached -- please try again tomorrow.",
            False,
        )
    return None


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
    """Run one conversational turn; callers persist the mutated session."""
    yield agent_events.MetaEvent(session_id=session_id, turn_id=turn_id)
    if AGENT_MOCK_MODE:
        async for event in mock_turn.stream_mock_turn(
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
    rejection = _admission_rejection(session_id)
    if rejection is not None:
        for event in _rejection_events(session_id, turn_id, *rejection):
            yield event
        return
    deterministic_answer = evaluate_simple_arithmetic(message)
    if deterministic_answer is not None:
        session_module.append_history(session, "user", message, turn_id=turn_id)
        resume_offer = session_module.consume_resume_offer(session)
        final_text = deterministic_answer + (
            f"\n\n{resume_offer}" if resume_offer else ""
        )
        session_module.append_history(session, "assistant", final_text, turn_id=turn_id)
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
        for event in _rejection_events(
            session_id,
            turn_id,
            "rate_limited",
            "SmartRoute is busy helping other riders -- try again shortly.",
            True,
        ):
            yield event
        return
    ctx = ToolContext(
        gtfs=gtfs,
        session=session,
        session_id=session_id,
        turn_id=turn_id,
        now_et=now_et,
        origin=origin,
        telemetry=trace.telemetry if trace is not None else {},
    )
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
