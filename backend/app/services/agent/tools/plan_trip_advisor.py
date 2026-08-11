"""Agent-selected route advisor invocation within the canonical plan-trip path."""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING

from app.services.agent.tools._types import ToolContext
from app.services.agent.turn_telemetry import record_model_call, record_phase_ms

if TYPE_CHECKING:
    from app.services.agent.tools.plan_trip_executor import PlanTripDependencies


@dataclass(frozen=True)
class AdvisorRouteSelection:
    """Validated model selection or its server-owned deterministic fallback."""

    recommendation: str
    chosen_index: int
    candidate_analysis: dict[int, dict[str, str]]
    status: str
    fallback: bool


def _fallback_index(scored: list[dict]) -> int:
    """Select the deterministic score winner only after advisor failure/invalidity."""
    return min(
        scored,
        key=lambda row: (
            row["score"],
            row["total_minutes"],
            row["transfers"],
            row["index"],
        ),
    )["index"]


async def _collect_agent_recommendation_with_first_token(
    dependencies: PlanTripDependencies,
    *,
    payload: dict,
    model: str,
    explanation_style: str,
) -> tuple[str, float | None]:
    """Drain the advisor stream and record first-token latency only."""
    started = time.monotonic()
    first_token_ms: float | None = None
    chunks: list[str] = []
    async for chunk in dependencies.ai_advisor.stream_agent_recommendation(
        payload,
        model=model,
        explanation_style=explanation_style,
    ):
        if first_token_ms is None and str(chunk or ""):
            first_token_ms = (time.monotonic() - started) * 1000
        chunks.append(str(chunk or ""))
    return "".join(chunks), first_token_ms


async def select_agent_route(
    *,
    payload: dict,
    candidate_count: int,
    scored: list[dict],
    ctx: ToolContext,
    dependencies: PlanTripDependencies,
    timings: dict[str, float],
    leg_telemetry: dict | None,
) -> AdvisorRouteSelection:
    """Use the active mode model, then strictly validate its candidate choice."""
    started = time.monotonic()
    model = str(ctx.agent_model or "").strip()
    style = str(ctx.agent_explanation_style or "comparative")
    status = "complete"
    fallback = False
    recommendation = ""
    candidate_analysis: dict[int, dict[str, str]] = {}
    first_token_ms: float | None = None
    plan_origin = None
    if isinstance(leg_telemetry, dict):
        plan_origin = leg_telemetry.get("_plan_origin_monotonic")

    def _plan_elapsed_ms() -> float | None:
        if isinstance(plan_origin, (int, float)):
            return (time.monotonic() - float(plan_origin)) * 1000
        return None

    start_mark = _plan_elapsed_ms()
    if start_mark is not None:
        timings["advisor_request_start_ms"] = start_mark
        record_phase_ms(ctx.telemetry, "advisor_request_start_ms", start_mark)

    try:
        if not model:
            raise RuntimeError("agent route model is not configured")
        recommendation, first_token_ms = await asyncio.wait_for(
            _collect_agent_recommendation_with_first_token(
                dependencies,
                payload=payload,
                model=model,
                explanation_style=style,
            ),
            timeout=dependencies.advisor_timeout_seconds,
        )
    except asyncio.TimeoutError:
        status = "timeout"
        fallback = True
        print(
            "[agent-plan_trip] advisor timed out "
            f"({dependencies.advisor_timeout_seconds:.2f}s)"
        )
    except ValueError:
        status = "invalid"
        fallback = True
        print("[agent-plan_trip] advisor returned invalid route control data")
    except Exception as exc:
        status = "failed"
        fallback = True
        print(f"[agent-plan_trip] advisor unavailable type={type(exc).__name__}")

    advisor_complete_mark = _plan_elapsed_ms()
    if advisor_complete_mark is not None:
        timings["advisor_complete_ms"] = advisor_complete_mark
        record_phase_ms(ctx.telemetry, "advisor_complete_ms", advisor_complete_mark)
    if isinstance(first_token_ms, (int, float)):
        timings["advisor_first_token_ms"] = float(first_token_ms)
        if start_mark is not None:
            record_phase_ms(
                ctx.telemetry,
                "advisor_first_token_ms",
                float(start_mark) + float(first_token_ms),
            )

    if not fallback:
        parse_started = time.monotonic()
        try:
            chosen_index, candidate_analysis = dependencies.advisor_context.parse_advisor_selection(
                recommendation,
                candidate_count,
                strict=True,
            )
        except ValueError:
            status = "invalid"
            fallback = True
            print("[agent-plan_trip] advisor returned invalid route control data")
        except Exception as exc:
            status = "failed"
            fallback = True
            print(f"[agent-plan_trip] advisor parsing failed type={type(exc).__name__}")
        timings["selection_parse_ms"] = (time.monotonic() - parse_started) * 1000
        parse_mark = _plan_elapsed_ms()
        if parse_mark is not None:
            record_phase_ms(ctx.telemetry, "selection_parse_complete_ms", parse_mark)

    timings["advisor_ms"] = (time.monotonic() - started) * 1000
    record_model_call(
        ctx.telemetry,
        role="route_selection",
        provider="anthropic",
        model=model,
        duration_ms=timings["advisor_ms"],
        outcome=status,
        first_token_ms=first_token_ms,
    )
    if isinstance(leg_telemetry, dict):
        leg_telemetry["advisor_status"] = status
        leg_telemetry["advisor_fallback"] = fallback
        if isinstance(first_token_ms, (int, float)):
            leg_telemetry["advisor_first_token_ms"] = float(first_token_ms)
    if fallback:
        return AdvisorRouteSelection(
            recommendation="I found the best available route from the current transit options.",
            chosen_index=_fallback_index(scored),
            candidate_analysis={},
            status=status,
            fallback=True,
        )
    return AdvisorRouteSelection(
        recommendation=recommendation,
        chosen_index=chosen_index,
        candidate_analysis=candidate_analysis,
        status=status,
        fallback=False,
    )
