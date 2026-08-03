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
)


def record_model_call(
    telemetry: dict[str, Any],
    *,
    role: str,
    provider: str,
    model: str,
    duration_ms: float,
    outcome: str,
) -> None:
    """Append one allowlisted model-call record without request contents."""
    calls = telemetry.setdefault("model_calls", [])
    if not isinstance(calls, list):
        return
    model_label = "".join(
        character for character in str(model) if character.isalnum() or character in "._-"
    )[:96] or "unconfigured"
    calls.append(
        {
            "call_index": len(calls) + 1,
            "role": str(role),
            "provider": str(provider),
            "model": model_label,
            "duration_ms": round(max(0.0, float(duration_ms))),
            "outcome": str(outcome),
        }
    )


def emit_trip_pipeline_timing(
    *,
    turn_id: str,
    stage_ms: dict[str, float],
    telemetry: dict[str, object],
    first_route_card_ms: float | None,
) -> dict[str, object] | None:
    """Emit one allowlisted timing record without rider or provider request data."""
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
    calls = telemetry.get("model_calls")
    safe_calls: list[dict[str, object]] = []
    if isinstance(calls, list):
        safe_calls = [
            {field: call.get(field) for field in _MODEL_CALL_FIELDS}
            for call in calls
            if isinstance(call, dict)
        ]
    record["model_calls"] = safe_calls
    record["model_call_total"] = len(safe_calls)
    record["outer_model_call_total"] = sum(
        call["role"] in {"conversation", "conversation_wrapup"}
        for call in safe_calls
    )
    record["route_selection_call_total"] = sum(
        call["role"] == "route_selection" for call in safe_calls
    )
    print(
        f"[trip-pipeline] {json.dumps(record, sort_keys=True, separators=(',', ':'))}",
        flush=True,
    )
    return record
