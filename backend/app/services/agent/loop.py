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
from app.services.agent import public_surface
from app.services.agent import session as session_module
from app.services.agent.model import budget, mock_turn
from app.services.agent.model import policy as agent_policy
from app.services.agent.tools import COMBINED_TOOL_REGISTRY, ToolContext, ToolResult
from app.services.agent.turn import stream as turn_stream
from app.services.agent.turn import tool_round
from app.services.agent.turn.finalization import TurnTrace
from app.services.agent.turn.ledger import TurnToolLedger

__all__ = [
    "TOOL_REGISTRY",
    "TurnTrace",
    "agent_policy",
    "budget",
    "client",
    "evaluate_simple_arithmetic",
    "public_surface",
    "run_agent_turn",
    "turn_stream",
]

# Keep the active registry injectable at the turn entry point so deterministic
# tests and replay runners can replace executors without changing production
# registry construction.
TOOL_REGISTRY = COMBINED_TOOL_REGISTRY


# Application retries are classified in the model stream. Disable SDK retries so
# one configured retry never expands into hidden provider attempts.
client = observability.wrap_anthropic(
    anthropic.AsyncAnthropic(api_key=os.getenv("ANTHROPIC_API_KEY"), max_retries=0)
)

AGENT_MOCK_MODE = runtime.enabled("AGENT_MOCK_MODE")

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


def _eval_bounded_binop(node: ast.BinOp) -> int | float:
    op = _SUPPORTED_BINOPS.get(type(node.op))
    if op is None:
        raise ValueError("unsupported expression")
    left = _eval_math_node(node.left)
    right = _eval_math_node(node.right)
    if isinstance(node.op, ast.Pow) and abs(right) > 8:
        raise ValueError("exponent too large")
    result = op(left, right)
    if abs(result) > 1_000_000_000_000:
        raise ValueError("result too large")
    return result


def _eval_math_node(node: ast.AST) -> int | float:
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        if abs(node.value) > 1_000_000_000:
            raise ValueError("number too large")
        return node.value
    if isinstance(node, ast.UnaryOp) and type(node.op) in _SUPPORTED_UNARY:
        return _SUPPORTED_UNARY[type(node.op)](_eval_math_node(node.operand))
    if isinstance(node, ast.BinOp):
        return _eval_bounded_binop(node)
    raise ValueError("unsupported expression")


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


def _turn_dependencies() -> turn_stream.TurnDependencies:
    def make_ledger() -> TurnToolLedger:
        return TurnToolLedger(run_tool=_run_one_tool)

    return turn_stream.TurnDependencies(
        client=client,
        tool_registry=TOOL_REGISTRY,
        make_ledger=make_ledger,
    )


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


def _arithmetic_shortcut_events(
    session: dict,
    session_id: str,
    turn_id: str,
    message: str,
    answer: str,
    trace: TurnTrace | None,
) -> tuple[agent_events.TokenEvent, agent_events.DoneEvent]:
    session_module.append_history(session, "user", message, turn_id=turn_id)
    resume_offer = session_module.consume_resume_offer(session)
    final_text = answer + (f"\n\n{resume_offer}" if resume_offer else "")
    session_module.append_history(session, "assistant", final_text, turn_id=turn_id)
    if trace is not None:
        trace.final_text = final_text
    return (
        agent_events.TokenEvent(text=final_text),
        agent_events.DoneEvent(
            session_id=session_id,
            turn_id=turn_id,
            stop_reason="end_turn",
            usage={"input_tokens": 0, "output_tokens": 0},
        ),
    )


async def _live_turn_events(
    *,
    session: dict,
    session_id: str,
    turn_id: str,
    message: str,
    now_et: str,
    gtfs,
    origin: dict | None,
    selected_card_id: str | None,
    response_presentation: str,
    trace: TurnTrace | None,
) -> AsyncIterator[agent_events.AgentEvent]:
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
        async for event in turn_stream.stream_turn(
            session=session,
            session_id=session_id,
            turn_id=turn_id,
            message=message,
            ctx=ctx,
            selected_card_id=selected_card_id,
            response_presentation=response_presentation,
            trace=trace,
            dependencies=_turn_dependencies(),
        ):
            yield event


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
        for event in _arithmetic_shortcut_events(
            session,
            session_id,
            turn_id,
            message,
            deterministic_answer,
            trace,
        ):
            yield event
        return
    async for event in _live_turn_events(
        session=session,
        session_id=session_id,
        turn_id=turn_id,
        message=message,
        now_et=now_et,
        gtfs=gtfs,
        origin=origin,
        selected_card_id=selected_card_id,
        response_presentation=response_presentation,
        trace=trace,
    ):
        yield event
