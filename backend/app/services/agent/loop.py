"""Public facade for the conversational agent's turn lifecycle.

`run_agent_turn()` preserves the SSE contract: a `meta` event first and one
terminal `done` event last. Focused collaborators own mock fixtures, per-turn
tool accounting, tool-round execution, and the deadline-bound live stream.
"""

from __future__ import annotations

import dataclasses
import os
import re
from collections.abc import AsyncIterator

import anthropic

from app import runtime
from app.services.agent import budget
from app.services.agent import discovery_followup
from app.services.agent import events as agent_events
from app.services.agent import intelligence
from app.services.agent import model_request
from app.services.agent import model_stream  # Compatibility patch point for loop tests.
from app.services.agent import mock_turn
from app.services.agent import policy as agent_policy
from app.services.agent import prompt as agent_prompt
from app.services.agent import scenario_followup
from app.services.agent import session as session_module
from app.services.agent import tool_round
from app.services.agent import turn_stream
from app.services.agent.tools import TOOL_REGISTRY, TOOLS, ToolContext, ToolResult
from app.services.agent.turn_ledger import TurnToolLedger as _TurnToolLedger


# Application retries are classified in model_stream. Disable SDK retries so
# one configured retry never expands into hidden provider attempts.
client = anthropic.AsyncAnthropic(api_key=os.getenv("ANTHROPIC_API_KEY"), max_retries=0)

AGENT_TURN_DEADLINE_S = float(os.getenv("AGENT_TURN_DEADLINE_S", "50"))
AGENT_MOCK_MODE = runtime.enabled("AGENT_MOCK_MODE")
MAX_TOOL_EXECUTIONS_PER_TURN = 12
MAX_TOOL_EXECUTIONS_PER_NAME = 4
WRAP_UP_INSTRUCTION = (
    "You are out of time or tool calls for this turn. Summarize what you "
    "know from the tool results so far, and plainly state what you could "
    "not determine. Do not call any more tools."
)


@dataclasses.dataclass
class TurnTrace:
    """Optional eval hook populated in place while a turn runs."""

    tool_calls: list[tuple[str, dict]] = dataclasses.field(default_factory=list)
    final_text: str = ""
    initial_mode: str = ""
    final_mode: str = ""
    escalation_reason: str | None = None
    stage_ms: dict[str, float] = dataclasses.field(default_factory=dict)
    model_call_count: int = 0
    tool_call_count: int = 0
    model_tool_use_count: int = 0
    provider_tool_execution_count: int = 0
    retry_count: int = 0


# Private aliases preserve direct test patches and existing local integrations
# while the implementation lives in focused modules.
_mock_step_delay_s = mock_turn.mock_step_delay_s
_mock_trip_copy = mock_turn.mock_trip_copy
_mock_token_chunks = mock_turn.mock_token_chunks
_stream_mock_turn = mock_turn.stream_mock_turn
_TurnDeadlineReached = tool_round.TurnDeadlineReached
_rider_excluded_modes = tool_round.rider_excluded_modes
_rider_excluded_route_ids = tool_round.rider_excluded_route_ids
_constrained_tool_input = tool_round.constrained_tool_input
_required_arrival_input = tool_round.required_arrival_input
_arrival_response = tool_round.arrival_response


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
    async for item in tool_round.execute_tool_round(*args, tool_registry=TOOL_REGISTRY, **kwargs):
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
    messages: list[dict] = []
    for entry in history or []:
        role = entry.get("role")
        if role in {"user", "assistant"}:
            messages.append({"role": role, "content": entry.get("text", "")})
        elif role == "tool":
            messages.append({"role": "assistant", "content": f"[{entry.get('tool', 'tool')} result] {entry.get('text', '')}"})
    return messages


def _build_stream_kwargs(
    *,
    force_final: bool,
    messages: list[dict],
    system_blocks: list[dict],
    mode_policy: agent_policy.AgentModePolicy,
    tools: list[dict],
    request_options: dict | None = None,
) -> dict:
    return model_request.build_stream_kwargs(
        force_final=force_final,
        messages=messages,
        system_blocks=system_blocks,
        mode_policy=mode_policy,
        tools=tools,
        request_options=request_options,
    )


def _web_search_tool(mode_policy: agent_policy.AgentModePolicy) -> dict:
    return {
        "type": "web_search_20250305",
        "name": "web_search",
        "max_uses": 2 if mode_policy.mode == "quick" else 3,
        "allowed_callers": ["direct"],
        "user_location": {
            "type": "approximate",
            "city": "New York City",
            "region": "New York",
            "country": "US",
            "timezone": "America/New_York",
        },
    }


# Canonical route profile and the initial discovery surface. Discovery does
# not expose get_place_details: search_local_places already returns canonical
# opaque place IDs that prepare_route_options resolves directly. Descriptive
# or ordinal references on later turns receive get_place_details through the
# state-aware SELECT surface below.
_ROUTE_TOOL_NAMES = frozenset(
    {
        "get_place_details",
        "prepare_route_options",
        "present_route",
        "accessibility_status",
    }
)
_DISCOVERY_TOOL_NAMES = (
    _ROUTE_TOOL_NAMES - {"get_place_details"}
) | {"search_local_places"}


def _tool_schemas(*names: str) -> list[dict]:
    """Return registered schemas for the given tool names in registry order."""

    wanted = frozenset(names)
    return [schema for schema in TOOLS if schema.get("name") in wanted]


def _tools_for_intent(
    parsed_intent: intelligence.ParsedIntent,
    mode_policy: agent_policy.AgentModePolicy | None = None,
    scenario_action: scenario_followup.ScenarioAction | None = None,
    session: dict | None = None,
    message: object | None = None,
) -> list[dict]:
    mode_policy = mode_policy or agent_policy.policy_for_mode("auto")
    if scenario_action is scenario_followup.ScenarioAction.ACCEPT:
        # Acceptance never re-prepares: the live preview is already bound and
        # the model only needs the canonical present_route contract.
        return _tool_schemas("present_route")
    if scenario_action is scenario_followup.ScenarioAction.REJECT:
        # Rejection is a grounded conversational acknowledgement with no
        # route, web, discovery, incident, or other tool surface.
        return []
    followup_action = discovery_followup.detect_followup_action(session, message)
    if followup_action is discovery_followup.DiscoveryFollowupAction.SELECT:
        # Pure discovery-result reference: resolve exactly the one selected
        # place; no search, route, status, or web surface this turn.
        return _tool_schemas("get_place_details")
    if followup_action is discovery_followup.DiscoveryFollowupAction.SEARCH_AGAIN:
        # Exact "Okay, search again." recovery against the same session's
        # active discovery-set reference: the one structured client search
        # tool. The real executor binds a fresh server-owned set and clears
        # any stale selected-place association; no route/status/web surface
        # and no reactivation of the expired set.
        return _tool_schemas("search_local_places")
    if followup_action in {
        discovery_followup.DiscoveryFollowupAction.ROUTE_SELECTED,
        discovery_followup.DiscoveryFollowupAction.ADD_WAYPOINT,
        discovery_followup.DiscoveryFollowupAction.REMOVE_ONLY_WAYPOINT,
    }:
        # Navigation pronoun / waypoint mutation on the already-accepted
        # trip: the canonical route profile with no discovery search or
        # web/status surface.
        return _tool_schemas(*_ROUTE_TOOL_NAMES)
    tool_names_by_intent = {
        "route_planning": _ROUTE_TOOL_NAMES,
        "destination_discovery": _DISCOVERY_TOOL_NAMES,
        "arrival_lookup": {"lookup_arrivals"},
        "area_conditions": {"check_area_conditions"},
        "transit_question": intelligence.transit_question_tool_names(message),
        "simple_general": set(),
        "unsupported": set(),
    }
    included = tool_names_by_intent.get(parsed_intent.intent, set())
    tools = _tool_schemas(*included)
    if parsed_intent.intent == "destination_discovery" or (
        parsed_intent.intent == "transit_question" and "web_search" in included
    ):
        # Structured discovery tools stay first; the single native server-side
        # web_search is appended last for current reporting grounded tools
        # cannot answer. Route planning never receives web search.
        tools.append(_web_search_tool(mode_policy))
    return tools


def _route_card_text_fallback(card: agent_events.RouteCardEvent) -> str:
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
        transfer_copy = "no transfers" if transfers == 0 else f"{transfers} transfer{'s' if transfers != 1 else ''}"
    if eta_copy:
        sentences.append(f"It takes {eta_copy} with {transfer_copy}." if transfer_copy else f"It takes {eta_copy}.")
    elif transfer_copy:
        sentences.append(f"It has {transfer_copy}.")
    if reason:
        reason = "it is the fastest option" if reason.casefold() == "fastest" else reason[:1].lower() + reason[1:]
        sentences.append(f"I picked it because {reason}.")
    return " ".join(sentences)


_INTERNAL_CARD_REFERENCE = re.compile(r"\b(?:route\s+)?card\s+[`'\"]?(?:rc|mock)[_-][A-Za-z0-9_-]+[`'\"]?\s*(?:[\u2014\u2013-]\s*)?", re.IGNORECASE)
_OPAQUE_CARD_ID = re.compile(r"\b(?:rc|mock)[_-][A-Za-z0-9_-]{4,}\b", re.IGNORECASE)
_OPAQUE_CANDIDATE_ID = re.compile(r"\b(?:cd|cs)_[A-Za-z0-9_-]{4,}\b", re.IGNORECASE)


def _sanitize_rider_text(text: str) -> str:
    sanitized = _INTERNAL_CARD_REFERENCE.sub("", text)
    sanitized = _OPAQUE_CARD_ID.sub("the route", sanitized)
    sanitized = _OPAQUE_CANDIDATE_ID.sub("the route", sanitized)
    sanitized = re.sub(r"\*\*(.*?)\*\*", r"\1", sanitized, flags=re.DOTALL)
    sanitized = re.sub(r"__(.*?)__", r"\1", sanitized, flags=re.DOTALL)
    sanitized = re.sub(r"`([^`]+)`", r"\1", sanitized)
    sanitized = re.sub(r"(?m)^\s*#{1,6}\s+", "", sanitized)
    return re.sub(r"~(?=\d)", "about ", sanitized)


def _turn_dependencies() -> turn_stream.TurnDependencies:
    return turn_stream.TurnDependencies(
        deadline_s=AGENT_TURN_DEADLINE_S,
        client=client,
        tool_registry=TOOL_REGISTRY,
        wrap_up_instruction=WRAP_UP_INSTRUCTION,
        make_ledger=TurnToolLedger,
        system_blocks=_system_blocks,
        messages_from_history=_messages_from_history,
        build_stream_kwargs=_build_stream_kwargs,
        tools_for_intent=_tools_for_intent,
        route_card_text_fallback=_route_card_text_fallback,
        sanitize_rider_text=_sanitize_rider_text,
        required_arrival_input=_required_arrival_input,
        arrival_response=_arrival_response,
        rider_excluded_modes=_rider_excluded_modes,
        rider_excluded_route_ids=_rider_excluded_route_ids,
        execute_tool_round=_execute_tool_round,
    )


async def _stream_turn(**kwargs) -> AsyncIterator[agent_events.AgentEvent]:
    async for event in turn_stream.stream_turn(dependencies=_turn_dependencies(), **kwargs):
        yield event


def _rejection_events(
    session_id: str, turn_id: str, code: str, text: str, retryable: bool
) -> tuple[agent_events.ErrorEvent, agent_events.DoneEvent]:
    return (
        agent_events.ErrorEvent(code=code, message=text, retryable=retryable),
        agent_events.DoneEvent(session_id=session_id, turn_id=turn_id, stop_reason="error", usage={"input_tokens": 0, "output_tokens": 0}),
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
    """Run one conversational turn; callers persist the mutated session."""
    yield agent_events.MetaEvent(session_id=session_id, turn_id=turn_id)
    if AGENT_MOCK_MODE:
        async for event in _stream_mock_turn(
            session=session, session_id=session_id, turn_id=turn_id, message=message,
            origin=origin, trace=trace, response_presentation=response_presentation,
        ):
            yield event
        return
    if not budget.agent_enabled():
        for event in _rejection_events(session_id, turn_id, "budget_exceeded", "Trip planning is temporarily unavailable.", True):
            yield event
        return
    if not budget.check_session_rate_limit(session_id):
        for event in _rejection_events(session_id, turn_id, "rate_limited", "Too many messages in the last minute -- try again shortly.", True):
            yield event
        return
    if budget.daily_spend_exceeded():
        for event in _rejection_events(session_id, turn_id, "budget_exceeded", "Today's usage budget is reached -- please try again tomorrow.", False):
            yield event
        return
    deterministic_answer = intelligence.deterministic_response(message)
    if deterministic_answer is not None:
        session_module.append_history(session, "user", message)
        resume_offer = session_module.consume_resume_offer(session)
        final_text = deterministic_answer + (f"\n\n{resume_offer}" if resume_offer else "")
        session_module.append_history(session, "assistant", final_text)
        if trace is not None:
            trace.final_text = final_text
        yield agent_events.TokenEvent(text=final_text)
        yield agent_events.DoneEvent(session_id=session_id, turn_id=turn_id, stop_reason="end_turn", usage={"input_tokens": 0, "output_tokens": 0})
        return
    sem = budget.concurrency_semaphore()
    if sem.locked():
        for event in _rejection_events(session_id, turn_id, "rate_limited", "SmartRoute is busy helping other riders -- try again shortly.", True):
            yield event
        return
    ctx = ToolContext(
        gtfs=gtfs,
        session=session,
        session_id=session_id,
        turn_id=turn_id,
        now_et=now_et,
        origin=origin,
    )
    async with sem:
        async for event in _stream_turn(
            session=session, session_id=session_id, turn_id=turn_id, message=message, ctx=ctx,
            selected_card_id=selected_card_id, response_presentation=response_presentation, trace=trace,
        ):
            yield event
