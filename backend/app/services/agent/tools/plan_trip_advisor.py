"""Agent-selected route advisor invocation within the canonical plan-trip path."""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING

from app.services.agent.tools._types import ToolContext
from app.services.agent.turn_telemetry import record_model_call

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

    try:
        if not model:
            raise RuntimeError("agent route model is not configured")
        recommendation = await asyncio.wait_for(
            dependencies.ai_advisor.collect_agent_recommendation(
                payload,
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

    if not fallback:
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

    timings["advisor_ms"] = (time.monotonic() - started) * 1000
    record_model_call(
        ctx.telemetry,
        role="route_selection",
        provider="anthropic",
        model=model,
        duration_ms=timings["advisor_ms"],
        outcome=status,
    )
    if isinstance(leg_telemetry, dict):
        leg_telemetry["advisor_status"] = status
        leg_telemetry["advisor_fallback"] = fallback
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
