"""Tool input policy and one parallel model-tool round for an agent turn."""

from __future__ import annotations

import asyncio
import contextlib
import json
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass, field

from app.services.agent import events as agent_events
from app.services.agent import public_surface
from app.services.agent import session as session_module
from app.services.agent.model import policy as agent_policy
from app.services.agent.model.output_projection import project_tool_result_data
from app.services.agent.passenger_output import pop_activity_label
from app.services.agent.tool_input_policy import (
    authoritative_discovery_input as _authoritative_discovery_input,
)
from app.services.agent.tool_input_policy import (
    constrained_tool_input,
)
from app.services.agent.tool_input_policy import (
    goal_error as _goal_error,
)
from app.services.agent.tool_input_policy import (
    missing_verified_destination as _missing_verified_destination,
)
from app.services.agent.tools import ToolContext, ToolResult
from app.services.agent.turn.contract import GoalState
from app.services.agent.turn.ledger import (
    ToolProgressRelay,
    TurnToolLedger,
    run_one_tool,
)


class TurnDeadlineReached(Exception):
    """Internal control flow that still reaches the turn's single DoneEvent."""

_UNOFFERED_TOOL_ERROR = "tool not offered on this turn"
_TERMINAL_TOOLS = frozenset({"complete_turn"})
_CAPABILITY_TOOLS = frozenset(
    {
        "discover_places",
        "check_transit",
        "prepare_route_options",
        "present_places",
        "present_transit",
        "present_route",
        "web_search",
    }
)


def mixed_terminal_and_capability(names: list[str]) -> bool:
    called = set(names)
    return bool(called & _TERMINAL_TOOLS) and bool(called & _CAPABILITY_TOOLS)

_UNAVAILABLE_ACTION_LABEL = "Checking the requested information\u2026"
_UNAVAILABLE_ACTION_SUMMARY = "That action is not available for this request"
_PUBLIC_FAILURE_SUMMARIES = {
    "discover_places": "Place search could not be completed",
    "check_transit": "Transit information could not be checked",
    "prepare_route_options": "Route options could not be prepared",
    "present_places": "Place results could not be shown",
    "present_transit": "Transit results could not be shown",
    "present_route": "The prepared route could not be shown",
    "complete_turn": "The response could not be completed",
}

def public_failure_summary(name: str) -> str:
    return _PUBLIC_FAILURE_SUMMARIES.get(name, "That action could not be completed")

def _shows_rider_activity(name: str) -> bool:
    """Expose only real evidence gathering, never internal handoff stages."""

    return name not in {
        "declare_goals",
        "present_places",
        "present_transit",
        "present_route",
        "complete_turn",
    }


def _presentation_identity(name: str, tool_input: dict) -> tuple[str, str] | None:
    field = {
        "present_places": "discovery_set_id",
        "present_transit": "evidence_set_id",
        "present_route": "candidate_id",
    }.get(name)
    if field is None:
        return None
    handle = str(tool_input.get(field) or "").strip()
    return (name, handle) if handle else None


@dataclass
class _ToolRoundExecution:
    blocks: list
    ctx: ToolContext
    session: dict
    tool_calls: list[tuple[str, dict]]
    excluded_modes: set[str]
    mode_policy: agent_policy.AgentModePolicy
    stage_ms: dict[str, float]
    deadline_monotonic: float
    ledger: TurnToolLedger
    registry: dict
    excluded_route_ids: tuple[str, ...]
    allowed_names: frozenset[str] | None
    tool_inputs: dict[str, dict] = field(default_factory=dict)
    activity_labels: dict[str, str | None] = field(default_factory=dict)
    rejected: dict[str, ToolResult] = field(default_factory=dict)
    declaration_results: dict[str, ToolResult] = field(default_factory=dict)
    outcomes_by_key: dict[str, ToolResult] = field(default_factory=dict)
    first_block_by_key: dict[str, str] = field(default_factory=dict)
    execution_keys: dict[str, str] = field(default_factory=dict)
    pending_calls: dict[str, tuple[str, dict]] = field(default_factory=dict)
    round_tasks: dict[str, asyncio.Task[ToolResult]] = field(default_factory=dict)
    start_times: dict[str, float] = field(default_factory=dict)
    contract_active: bool = False

    async def stream(self) -> AsyncIterator:
        self._normalize_inputs()
        await self._validate_calls()
        self._plan_calls()
        async for event in self._execute_calls():
            yield event
        async for event in self._surface_results():
            yield event

    def _normalize_inputs(self) -> None:
        self.ctx.agent_mode = self.mode_policy.mode
        self.ctx.agent_model = self.mode_policy.model
        self.ctx.agent_explanation_style = self.mode_policy.explanation_style
        for block in self.blocks:
            name = getattr(block, "name", "")
            raw_input = dict(getattr(block, "input", {}) or {})
            self.activity_labels[block.id] = pop_activity_label(raw_input)
            constrained = constrained_tool_input(
                name,
                raw_input,
                self.excluded_modes,
                mode_policy=self.mode_policy,
                excluded_route_ids=self.excluded_route_ids,
            )
            self.tool_inputs[block.id] = _authoritative_discovery_input(
                name, constrained, self.ctx
            )

    def _reject_duplicates(self, name: str, error: str) -> list:
        matching = [block for block in self.blocks if block.name == name]
        for duplicate in matching[1:]:
            self.rejected[duplicate.id] = ToolResult(ok=False, error=error)
        return matching

    async def _validate_calls(self) -> None:
        if self.allowed_names is not None:
            self.rejected.update(
                {
                    block.id: ToolResult(ok=False, error=_UNOFFERED_TOOL_ERROR)
                    for block in self.blocks
                    if getattr(block, "name", "") not in self.allowed_names
                }
            )
        declarations = self._reject_duplicates(
            "declare_goals", "declare_goals may be called only once per turn"
        )
        self._reject_duplicates(
            "complete_turn", "complete_turn may be called only once per tool round"
        )
        if declarations and declarations[0].id not in self.rejected:
            block = declarations[0]
            self.declaration_results[block.id] = await run_one_tool(
                "declare_goals",
                self.tool_inputs[block.id],
                self.ctx,
                tool_registry=self.registry,
                deadline_monotonic=self.deadline_monotonic,
            )
        self.contract_active = bool(
            "declare_goals" in (self.allowed_names or ())
            or getattr(self.ctx.turn_evidence, "turn_contract", None) is not None
        )
        for block in self.blocks:
            if block.id in self.rejected:
                continue
            reason = self._validation_error(block)
            if reason is not None:
                self.rejected[block.id] = ToolResult(ok=False, error=reason)

    def _validation_error(self, block) -> str | None:
        name = getattr(block, "name", "")
        tool_input = self.tool_inputs[block.id]
        if self.contract_active:
            reason = _goal_error(name, tool_input, self.ctx)
            if reason is not None:
                return reason
        return _missing_verified_destination(name, tool_input, self.ctx)

    def _plan_calls(self) -> None:
        self.start_times = {block.id: time.monotonic() for block in self.blocks}
        presentation_keys: dict[tuple[str, str], str] = {}
        for block in self.blocks:
            if block.id in self.rejected:
                continue
            name = getattr(block, "name", "")
            tool_input = self.tool_inputs[block.id]
            identity = _presentation_identity(name, tool_input)
            key = presentation_keys.get(identity) if identity is not None else None
            if key is None:
                key = self.ledger.key(name, tool_input)
                if identity is not None:
                    presentation_keys[identity] = key
            self.execution_keys[block.id] = key
            self.first_block_by_key.setdefault(key, block.id)
            if block.id in self.declaration_results:
                self.outcomes_by_key[key] = self.declaration_results[block.id]
                continue
            cached = self.ledger.reusable_results.get(key)
            if cached is not None:
                self.outcomes_by_key[key] = cached
            elif key not in self.pending_calls:
                self.pending_calls[key] = (name, tool_input)

    async def _execute_calls(self) -> AsyncIterator:
        if not self.pending_calls:
            return
        relay = ToolProgressRelay()
        previous_progress_sink = self.ctx.progress_sink
        self.ctx.progress_sink = relay.publish
        round_task: asyncio.Future | None = None
        try:
            for key, (name, tool_input) in self.pending_calls.items():
                self._mark_goal_in_flight(name, tool_input)
                self.round_tasks[key] = asyncio.create_task(
                    self.ledger.execute(
                        name,
                        tool_input,
                        self.ctx,
                        deadline_monotonic=self.deadline_monotonic,
                    )
                )
                event = self._tool_start_event(key, name, tool_input)
                if event is not None:
                    yield event
            round_task = asyncio.gather(*self.round_tasks.values())
            async for progress in relay.stream_until(round_task):
                yield progress
            self.outcomes_by_key.update(zip(self.round_tasks, await round_task, strict=False))
        finally:
            if round_task is not None and not round_task.done():
                round_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await round_task
            self.ctx.progress_sink = previous_progress_sink

    def _mark_goal_in_flight(self, name: str, tool_input: dict) -> None:
        if self.contract_active and public_surface.is_evidence_capability(name):
            self.ctx.turn_evidence.record_goal(
                str(tool_input.get("goal_key") or ""),
                GoalState.IN_FLIGHT,
                attempted=True,
            )

    def _tool_start_event(
        self, key: str, name: str, tool_input: dict
    ) -> agent_events.ToolStartEvent | None:
        if not _shows_rider_activity(name):
            return None
        block_id = self.first_block_by_key[key]
        spec = self.registry.get(name)
        label = self.activity_labels.get(block_id)
        if not label:
            label = spec.label_fn(tool_input) if spec else _UNAVAILABLE_ACTION_LABEL
        return agent_events.ToolStartEvent(
            tool_call_id=block_id, tool=name, label=label
        )

    def _outcomes(self) -> list[ToolResult]:
        return [
            self.rejected[block.id]
            if block.id in self.rejected
            else self.outcomes_by_key[self.execution_keys[block.id]]
            for block in self.blocks
        ]

    async def _surface_results(self) -> AsyncIterator:
        outcomes = self._outcomes()
        content = []
        for block, result in zip(self.blocks, outcomes, strict=False):
            result_content, events = self._record_result(block, result)
            content.append(result_content)
            for event in events:
                yield event
        yield {
            "__tool_result_message__": {"role": "user", "content": content},
            "__deadline_reached__": any(
                not result.ok and result.error == "turn deadline reached"
                for result in outcomes
            ),
            "__tool_outcomes__": [
                (block.name, self.tool_inputs[block.id], result)
                for block, result in zip(self.blocks, outcomes, strict=False)
            ],
        }

    def _record_result(self, block, result: ToolResult) -> tuple[dict, list]:
        name = getattr(block, "name", "")
        tool_input = self.tool_inputs[block.id]
        key = self.execution_keys.get(block.id, self.ledger.key(name, tool_input))
        owns_side_effects = (
            key in self.round_tasks and self.first_block_by_key[key] == block.id
        )
        duration_ms = round((time.monotonic() - self.start_times[block.id]) * 1000)
        if block.id not in self.rejected:
            self.tool_calls.append((name, tool_input))
        if owns_side_effects:
            self._record_timings(result)
        if result.ok:
            events = self._record_success(name, block.id, result, duration_ms, owns_side_effects)
            return self._success_content(name, block.id, result, tool_input), events
        events = self._record_failure(
            name, block.id, tool_input, result, duration_ms, owns_side_effects
        )
        return self._failure_content(name, block.id, result), events

    def _record_timings(self, result: ToolResult) -> None:
        for stage, duration in result.timings.items():
            if stage in self.stage_ms:
                self.stage_ms[stage] += max(0.0, float(duration))

    def _record_success(
        self,
        name: str,
        block_id: str,
        result: ToolResult,
        duration_ms: int,
        owns_side_effects: bool,
    ) -> list:
        events = []
        if _shows_rider_activity(name):
            events.append(
                agent_events.ToolEndEvent(
                    tool_call_id=block_id,
                    tool=name,
                    ok=True,
                    duration_ms=duration_ms,
                    summary=None,
                )
            )
        if not owns_side_effects:
            return events
        if result.summary:
            session_module.append_tool_summary(self.session, name, result.summary)
        if result.session_route_cards:
            session_module.add_route_cards(self.session, result.session_route_cards)
        session_module.add_visible_events(self.session, result.events)
        if name == "present_route":
            session_module.clear_pending_trip(self.session)
        events.extend(result.events)
        return events

    def _record_failure(
        self,
        name: str,
        block_id: str,
        tool_input: dict,
        result: ToolResult,
        duration_ms: int,
        owns_side_effects: bool,
    ) -> list:
        events = []
        if _shows_rider_activity(name):
            summary = (
                _UNAVAILABLE_ACTION_SUMMARY
                if block_id in self.rejected or name not in self.registry
                else public_failure_summary(name)
            )
            events.append(
                agent_events.ToolEndEvent(
                    tool_call_id=block_id,
                    tool=name,
                    ok=False,
                    duration_ms=duration_ms,
                    summary=summary,
                )
            )
        if owns_side_effects and name == "prepare_route_options":
            session_module.mark_pending_trip_failed(
                self.session, tool_input, result.error or "tool failed"
            )
        return events

    @staticmethod
    def _success_content(
        name: str,
        block_id: str,
        result: ToolResult,
        tool_input: dict | None = None,
    ) -> dict:
        model_data = project_tool_result_data(name, result.data, tool_input)
        return {
            "type": "tool_result",
            "tool_use_id": block_id,
            "content": json.dumps(
                {"source": name, "data": model_data, "untrusted": True},
                default=str,
            ),
        }

    @staticmethod
    def _failure_content(name: str, block_id: str, result: ToolResult) -> dict:
        return {
            "type": "tool_result",
            "tool_use_id": block_id,
            "content": json.dumps(
                {
                    "source": name,
                    "data": {"error": result.error or "tool failed"},
                    "untrusted": True,
                },
                default=str,
            ),
            "is_error": True,
        }


async def execute_tool_round(
    tool_use_blocks: list,
    ctx: ToolContext,
    session: dict,
    tool_calls_this_turn: list[tuple[str, dict]],
    excluded_modes: set[str],
    mode_policy: agent_policy.AgentModePolicy,
    stage_ms: dict[str, float],
    deadline_monotonic: float,
    tool_ledger: TurnToolLedger,
    *,
    tool_registry: dict,
    excluded_route_ids: tuple[str, ...] = (),
    allowed_tool_names: frozenset[str] | None = None,
) -> AsyncIterator:
    """Validate, execute, and surface one parallel model-tool round."""

    execution = _ToolRoundExecution(
        tool_use_blocks,
        ctx,
        session,
        tool_calls_this_turn,
        excluded_modes,
        mode_policy,
        stage_ms,
        deadline_monotonic,
        tool_ledger,
        tool_registry,
        excluded_route_ids,
        allowed_tool_names,
    )
    async for event in execution.stream():
        yield event
