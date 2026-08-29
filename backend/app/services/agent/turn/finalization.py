"""Turn finalization, development trace, and safe terminal telemetry."""

from __future__ import annotations

import dataclasses
import time
from dataclasses import dataclass
from typing import Any

from app.services.agent import session as session_module
from app.services.agent.model import budget
from app.services.agent.model import policy as agent_policy
from app.services.agent.turn import completion as turn_completion


@dataclass(frozen=True)
class FinalizationResult:
    stage_ms: dict[str, float]
    total_ms: float
    total_model_call_count: int


def stage_timings(trace: TurnTrace | None, started: float) -> dict[str, float]:
    """Create the allowlisted timing map and preserve any upstream stages."""

    stage_ms = {
        "intent_ms": (time.monotonic() - started) * 1000,
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


def finalize_turn(
    *,
    session: dict,
    tool_calls: list[tuple[str, dict]],
    final_text: str,
    trace: TurnTrace | None,
    initial_mode: str,
    final_mode: str,
    model: str,
    max_route_candidates: int,
    optional_enrichment: bool,
    retry_policy_count: int,
    model_call_count: int,
    server_tool_call_count: int,
    retry_count_total: int,
    tool_ledger: Any,
    input_tokens: int,
    output_tokens: int,
    stage_ms: dict[str, float],
    turn_start: float,
    finalize_started: float,
    turn_id: str,
    session_id: str,
    telemetry: dict,
    required_tool_names: tuple[str, ...],
    round_num: int,
    tool_failures: int,
    model_ms_total: float,
    tools_ms_total: float,
    stop_reason: str,
) -> FinalizationResult:
    if final_text:
        session_module.append_history(
            session, "assistant", final_text, turn_id=turn_id
        )
    session_module.extract_slots(session, tool_calls)
    if trace is not None:
        trace.tool_calls = list(tool_calls)
        trace.final_text = final_text
        trace.initial_mode = initial_mode
        trace.final_mode = final_mode
        trace.model_call_count = model_call_count
        trace.tool_call_count = len(tool_calls) + server_tool_call_count
        trace.model_tool_use_count = len(tool_calls) + server_tool_call_count
        trace.provider_tool_execution_count = (
            tool_ledger.total_executions + server_tool_call_count
        )
        trace.retry_count = retry_count_total
    budget.record_usage_cost(input_tokens, output_tokens)
    stage_ms["stream_finalize_ms"] = (
        time.monotonic() - finalize_started
    ) * 1000

    total_ms = (time.monotonic() - turn_start) * 1000
    stage_ms["model_ms"] = model_ms_total
    stage_ms["evidence_ms"] = max(
        stage_ms["evidence_ms"],
        stage_ms["mta_ms"] + stage_ms["ticketmaster_ms"],
    )
    stage_ms["total_ms"] = total_ms
    stage_ms["turn_complete_ms"] = total_ms
    record_phase_ms(telemetry, "turn_complete_ms", total_ms)
    if trace is not None:
        trace.stage_ms = dict(stage_ms)
    required_tools = ",".join(required_tool_names) or "none"
    recorded_model_calls = telemetry.get("model_calls")
    total_model_call_count = (
        len(recorded_model_calls) if isinstance(recorded_model_calls, list) else 0
    )
    print(
        f"[agent] turn={turn_id} sess={session_id[:6]} rounds={round_num} "
        f"mode={initial_mode} final_mode={final_mode} "
        f"model={agent_policy.safe_model_label(model)} "
        f"candidate_budget={max_route_candidates} "
        f"retries={retry_policy_count} required_tools={required_tools} "
        f"optional_enrichment={int(optional_enrichment)} "
        f"model_tool_uses={len(tool_calls) + server_tool_call_count} "
        f"provider_tool_executions={tool_ledger.total_executions + server_tool_call_count} "
        f"tool_failures={tool_failures} intent_ms={stage_ms['intent_ms']:.0f} "
        f"session_load_ms={stage_ms['session_load_ms']:.0f} "
        f"place_resolution_ms={stage_ms['place_resolution_ms']:.0f} "
        f"web_search_ms={stage_ms['web_search_ms']:.0f} "
        f"place_normalization_ms={stage_ms['place_normalization_ms']:.0f} "
        f"route_provider_ms={stage_ms['route_provider_ms']:.0f} "
        f"evidence_ms={stage_ms['evidence_ms']:.0f} "
        f"mta_ms={stage_ms['mta_ms']:.0f} "
        f"ticketmaster_ms={stage_ms['ticketmaster_ms']:.0f} "
        f"arrival_lookup_ms={stage_ms['arrival_lookup_ms']:.0f} "
        f"stop_resolution_ms={stage_ms['stop_resolution_ms']:.0f} "
        f"feed_fetch_ms={stage_ms['feed_fetch_ms']:.0f} "
        f"feed_parse_ms={stage_ms['feed_parse_ms']:.0f} "
        f"render_ms={stage_ms['render_ms']:.0f} "
        f"scoring_ms={stage_ms['scoring_ms']:.0f} "
        f"model_ms={model_ms_total:.0f} "
        f"stream_finalize_ms={stage_ms['stream_finalize_ms']:.0f} "
        f"tools_ms={tools_ms_total:.0f} total_ms={total_ms:.0f} "
        f"outer_model_calls={model_call_count} "
        f"total_model_calls={total_model_call_count} "
        f"retry_count={retry_count_total} in_tok={input_tokens} "
        f"out_tok={output_tokens} stop={stop_reason}",
        flush=True,
    )
    return FinalizationResult(
        stage_ms=stage_ms,
        total_ms=total_ms,
        total_model_call_count=total_model_call_count,
    )


_USAGE_FIELDS = (
    "input_tokens",
    "output_tokens",
    "cache_creation_input_tokens",
    "cache_read_input_tokens",
    "thinking_tokens",
)
_PHASE_FIELDS = (
    "conversation_request_start_ms",
    "conversation_first_token_ms",
    "conversation_first_visible_token_ms",
    "conversation_complete_ms",
    "plan_trip_tool_start_ms",
    "place_resolution_complete_ms",
    "google_routes_complete_ms",
    "mta_context_complete_ms",
    "incident_start_ms",
    "incident_complete_ms",
    "advisor_request_start_ms",
    "advisor_first_token_ms",
    "advisor_complete_ms",
    "selection_parse_complete_ms",
    "enrichment_complete_ms",
    "route_card_emit_ms",
    "turn_complete_ms",
)


def extract_safe_usage(usage: object) -> dict[str, int]:
    """Copy allowlisted numeric usage fields; ignore missing or malformed values."""
    safe: dict[str, int] = {}
    if usage is None:
        return safe
    for field in _USAGE_FIELDS:
        value = getattr(usage, field, None)
        if value is None and isinstance(usage, dict):
            value = usage.get(field)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            continue
        safe[field] = max(0, int(value))
    return safe


def record_model_call(
    telemetry: dict[str, Any],
    *,
    role: str,
    provider: str,
    model: str,
    duration_ms: float,
    outcome: str,
    first_token_ms: float | None = None,
    usage: object | None = None,
) -> None:
    """Append one allowlisted model-call record without request contents."""
    try:
        calls = telemetry.setdefault("model_calls", [])
        if not isinstance(calls, list):
            return
        model_label = "".join(
            character for character in str(model) if character.isalnum() or character in "._-"
        )[:96] or "unconfigured"
        record: dict[str, object] = {
            "call_index": len(calls) + 1,
            "role": str(role),
            "provider": str(provider),
            "model": model_label,
            "duration_ms": round(max(0.0, float(duration_ms))),
            "outcome": str(outcome),
        }
        if isinstance(first_token_ms, (int, float)):
            record["first_token_ms"] = round(max(0.0, float(first_token_ms)))
        record.update(extract_safe_usage(usage))
        calls.append(record)
    except (TypeError, ValueError, KeyError, AttributeError):
        return


def record_phase_ms(telemetry: dict[str, Any], name: str, elapsed_ms: float) -> None:
    """Store one turn- or plan-relative phase mark; never raises to callers."""
    try:
        if name not in _PHASE_FIELDS:
            return
        phases = telemetry.setdefault("phases", {})
        if not isinstance(phases, dict):
            return
        if name in phases:
            return
        phases[name] = round(max(0.0, float(elapsed_ms)))
    except (TypeError, ValueError, KeyError, AttributeError):
        return


def record_first_visible_token(
    stage_ms: dict[str, float],
    telemetry: dict,
    turn_start: float,
    text: str,
) -> None:
    """Record the first rider-visible content once for latency diagnostics."""

    if not str(text).strip() or "conversation_first_visible_token_ms" in stage_ms:
        return
    elapsed_ms = (time.monotonic() - turn_start) * 1000
    stage_ms["conversation_first_visible_token_ms"] = elapsed_ms
    record_phase_ms(telemetry, "conversation_first_visible_token_ms", elapsed_ms)


@dataclasses.dataclass
class TurnTrace:
    rider_message: str = ""
    tool_calls: list[tuple[str, dict]] = dataclasses.field(default_factory=list)
    model_rounds: list[dict[str, Any]] = dataclasses.field(default_factory=list)
    capability_attempts: list[dict[str, Any]] = dataclasses.field(default_factory=list)
    contract: dict[str, Any] | None = None
    goal_transitions: list[dict[str, Any]] = dataclasses.field(default_factory=list)
    terminal_resolution: dict[str, Any] = dataclasses.field(default_factory=dict)
    telemetry: dict[str, Any] = dataclasses.field(default_factory=dict)
    final_text: str = ""
    initial_mode: str = ""
    final_mode: str = ""
    stage_ms: dict[str, float] = dataclasses.field(default_factory=dict)
    model_call_count: int = 0
    tool_call_count: int = 0
    model_tool_use_count: int = 0
    provider_tool_execution_count: int = 0
    retry_count: int = 0


def record_model_round(
    trace: TurnTrace | None,
    *,
    round_number: int,
    offered_capabilities: frozenset[str],
    selected_capabilities: list[str],
    duration_ms: float,
    first_token_ms: float | None,
    stop_reason: str,
    outcome: str,
) -> None:
    if trace is None:
        return
    trace.model_rounds.append(
        {
            "round": round_number,
            "offered_capabilities": sorted(offered_capabilities),
            "selected_capabilities": list(selected_capabilities),
            "duration_ms": round(max(0.0, duration_ms), 1),
            "first_token_ms": (
                round(max(0.0, first_token_ms), 1)
                if isinstance(first_token_ms, (int, float))
                else None
            ),
            "stop_reason": stop_reason,
            "outcome": outcome,
        }
    )


def record_capability_attempts(
    trace: TurnTrace | None,
    outcomes: list[tuple[str, dict, object]],
) -> None:
    if trace is None:
        return
    for capability, tool_input, result in outcomes:
        trace.capability_attempts.append(
            {
                "capability": capability,
                "goal_key": str(tool_input.get("goal_key") or ""),
                "ok": bool(getattr(result, "ok", False)),
                "outcome": str(getattr(result, "outcome", "") or ""),
                "internal_failure": bool(getattr(result, "internal_diagnostic", False)),
                "error": str(getattr(result, "error", "") or ""),
            }
        )


def finalize_trace(trace: TurnTrace | None, evidence: object) -> None:
    if trace is None:
        return
    contract = getattr(evidence, "turn_contract", None)
    trace.contract = contract.to_payload() if contract is not None else None
    trace.goal_transitions = list(getattr(evidence, "goal_transitions", ()))
    telemetry = evidence.telemetry() if hasattr(evidence, "telemetry") else {}
    if contract is not None:
        telemetry.update(turn_completion.completion_telemetry(contract, evidence))
    trace.terminal_resolution = {
        "terminal": bool(getattr(evidence, "terminal", False)),
        "path": str(getattr(evidence, "terminal_path", "") or "none"),
        "selection_source": str(getattr(evidence, "selection_source", "") or "none"),
        "resolution": telemetry.get("turn_resolution", "incomplete"),
        "remaining_goals": list(telemetry.get("remaining_goal_keys") or []),
        "required_next_actions": list(telemetry.get("required_next_actions") or []),
    }
