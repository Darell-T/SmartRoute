"""Turn finalization, terminal telemetry, and usage accounting."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, TYPE_CHECKING

from app.services.agent import budget
from app.services.agent import policy as agent_policy
from app.services.agent import session as session_module
from app.services.agent.turn_telemetry import (
    emit_trip_pipeline_timing,
    record_phase_ms,
)
from app.services.agent.turn_ledger import TurnToolLedger

if TYPE_CHECKING:
    from app.services.agent.loop import TurnTrace


@dataclass(frozen=True)
class FinalizationResult:
    stage_ms: dict[str, float]
    total_ms: float
    total_model_call_count: int


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
    escalation_reason: str | None,
    model_call_count: int,
    server_tool_call_count: int,
    retry_count_total: int,
    tool_ledger: TurnToolLedger,
    input_tokens: int,
    output_tokens: int,
    stage_ms: dict[str, float],
    turn_start: float,
    finalize_started: float,
    turn_id: str,
    session_id: str,
    telemetry: dict,
    first_route_card_ms: float | None,
    parsed_intent: object,
    round_num: int,
    tool_failures: int,
    model_ms_total: float,
    tools_ms_total: float,
    stop_reason: str,
) -> FinalizationResult:
    if final_text:
        session_module.append_history(session, "assistant", final_text)
    session_module.extract_slots(session, tool_calls)
    if trace is not None:
        trace.tool_calls = list(tool_calls)
        trace.final_text = final_text
        trace.initial_mode = initial_mode
        trace.final_mode = final_mode
        trace.escalation_reason = escalation_reason
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
    emit_trip_pipeline_timing(
        turn_id=turn_id,
        stage_ms=stage_ms,
        telemetry=telemetry,
        first_route_card_ms=first_route_card_ms,
    )

    required_evidence = getattr(parsed_intent, "required_evidence", None)
    required_tools = (
        ",".join(required_evidence.required_tools())
        if required_evidence is not None
        else "none"
    )
    recorded_model_calls = telemetry.get("model_calls")
    total_model_call_count = (
        len(recorded_model_calls) if isinstance(recorded_model_calls, list) else 0
    )
    print(
        f"[agent] turn={turn_id} sess={session_id[:6]} rounds={round_num} "
        f"mode={initial_mode} final_mode={final_mode} "
        f"escalation={escalation_reason or 'none'} "
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
