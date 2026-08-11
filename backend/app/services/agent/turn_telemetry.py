"""Safe model-call and route-turn telemetry owned by the agent boundary."""

from __future__ import annotations

import json
from typing import Any


_MODEL_CALL_FIELDS = (
    "call_index",
    "role",
    "provider",
    "model",
    "duration_ms",
    "outcome",
    "first_token_ms",
    "input_tokens",
    "output_tokens",
    "cache_creation_input_tokens",
    "cache_read_input_tokens",
    "thinking_tokens",
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
    except Exception:
        # Telemetry must never affect the rider path.
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
    except Exception:
        return


def emit_trip_pipeline_timing(
    *,
    turn_id: str,
    stage_ms: dict[str, float],
    telemetry: dict[str, object],
    first_route_card_ms: float | None,
) -> dict[str, object] | None:
    """Emit one allowlisted timing record without rider or provider request data."""
    try:
        pipeline = telemetry.get("plan_trip")
        if not isinstance(pipeline, dict):
            return None

        def duration(name: str) -> int:
            value = pipeline.get(name, 0.0)
            return round(max(0.0, float(value))) if isinstance(value, (int, float)) else 0

        incident_status = str(pipeline.get("incident_status") or "unknown")
        if incident_status not in {
            "not_started",
            "complete",
            "partial",
            "failed",
            "disabled",
            "timeout",
            "unavailable",
        }:
            incident_status = "unknown"
        advisor_status = str(pipeline.get("advisor_status") or "unknown")
        if advisor_status not in {"not_started", "complete", "failed", "timeout", "invalid"}:
            advisor_status = "unknown"
        mode = str(telemetry.get("mode") or "unknown")
        if mode not in {"auto", "quick"}:
            mode = "unknown"
        cache_hit = pipeline.get("incident_cache_hit")
        record: dict[str, object] = {
            "event": "trip_pipeline_timing",
            "turn_id": turn_id,
            "mode": mode,
            "outcome": "success" if pipeline.get("outcome") == "success" else "error",
            "leg_count": max(0, int(pipeline.get("leg_count") or 0)),
            "outer_model_ms": round(max(0.0, float(stage_ms.get("model_ms", 0.0)))),
            "google_routes_ms": duration("google_routes_ms"),
            "mta_evidence_ms": duration("mta_evidence_ms"),
            "incident_ms": duration("incident_ms"),
            "incident_cache_hit": cache_hit if isinstance(cache_hit, bool) else None,
            "incident_status": incident_status,
            "advisor_ms": duration("advisor_ms"),
            "advisor_status": advisor_status,
            "advisor_fallback": pipeline.get("advisor_fallback") is True,
            "advisor_first_token_ms": (
                round(max(0.0, float(pipeline["advisor_first_token_ms"])))
                if isinstance(pipeline.get("advisor_first_token_ms"), (int, float))
                else None
            ),
            "selection_parse_ms": duration("selection_parse_ms"),
            "scoring_ms": duration("scoring_ms"),
            "enrichment_ms": duration("enrichment_ms"),
            "plan_trip_ms": duration("plan_trip_ms"),
            "first_route_card_ms": (
                round(max(0.0, first_route_card_ms))
                if isinstance(first_route_card_ms, (int, float))
                else None
            ),
            "turn_total_ms": round(max(0.0, float(stage_ms.get("total_ms", 0.0)))),
        }
        phases = telemetry.get("phases")
        safe_phases: dict[str, int] = {}
        if isinstance(phases, dict):
            for field in _PHASE_FIELDS:
                value = phases.get(field)
                if isinstance(value, (int, float)):
                    safe_phases[field] = round(max(0.0, float(value)))
        # Prefer explicit turn-relative marks from stage_ms when present.
        for field in (
            "conversation_request_start_ms",
            "conversation_first_token_ms",
            "conversation_complete_ms",
            "plan_trip_tool_start_ms",
            "route_card_emit_ms",
            "turn_complete_ms",
        ):
            value = stage_ms.get(field)
            if isinstance(value, (int, float)):
                safe_phases[field] = round(max(0.0, float(value)))
        record["phases"] = safe_phases
        calls = telemetry.get("model_calls")
        safe_calls: list[dict[str, object]] = []
        if isinstance(calls, list):
            safe_calls = [
                {field: call.get(field) for field in _MODEL_CALL_FIELDS if field in call or field in {
                    "call_index", "role", "provider", "model", "duration_ms", "outcome"
                }}
                for call in calls
                if isinstance(call, dict)
            ]
            # Keep only allowlisted keys that exist.
            cleaned: list[dict[str, object]] = []
            for call in safe_calls:
                cleaned.append({field: call[field] for field in _MODEL_CALL_FIELDS if field in call})
            safe_calls = cleaned
        record["model_calls"] = safe_calls
        record["model_call_total"] = len(safe_calls)
        record["outer_model_call_total"] = sum(
            call.get("role") in {"conversation", "conversation_wrapup"}
            for call in safe_calls
        )
        record["route_selection_call_total"] = sum(
            call.get("role") == "route_selection" for call in safe_calls
        )
        print(
            f"[trip-pipeline] {json.dumps(record, sort_keys=True, separators=(',', ':'))}",
            flush=True,
        )
        return record
    except Exception:
        return None
