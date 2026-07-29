"""Opt-in live certification of the pinned SmartRoute route-advisor model."""

from __future__ import annotations

import argparse
import asyncio
import os
import re
from typing import Any

from app.services import ai_advisor
from app.services.trips import advisor_context, candidates


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run one bounded live route-advisor certification.")
    parser.add_argument("--live", action="store_true", help="Explicitly allow a live advisor request.")
    return parser.parse_args()


def _payload() -> dict[str, Any]:
    """Fixed synthetic routes: no rider request, coordinates, or provider data."""

    routes = [
        [{"type": "SUBWAY", "route_id": "Q", "train_line": "Q", "minutes_until_arrival": 18}],
        [{"type": "SUBWAY", "route_id": "R", "train_line": "R", "minutes_until_arrival": 24}],
    ]
    return advisor_context.build_advisor_payload(
        routes=routes,
        service_alerts=[],
        incidents=[],
        stalled_trains=[],
        stalled_buses=[],
        mode=advisor_context.PlanningMode.INTELLIGENCE,
    )


async def certify() -> dict[str, Any]:
    if not os.getenv("ANTHROPIC_API_KEY", "").strip():
        return {"status": "skipped", "reason": "advisor key is not configured", **ai_advisor.advisor_identity()}
    try:
        # stream_recommendation carries the production retry limit (three
        # attempts per pinned model); no user prompt or hidden reasoning is
        # logged by this command.
        output = await asyncio.wait_for(ai_advisor.collect_recommendation(_payload()), timeout=20.0)
        explicit = re.search(r"\[ROUTE:(\d+)\]", output)
        analysis_selected, analysis = candidates._parse_candidate_analysis(output)
        selected, _parsed_analysis = advisor_context.parse_advisor_selection(output, 2)
        # parse_advisor_selection deliberately falls back to route zero for
        # rider safety. Certification must instead prove the live model emitted
        # a valid explicit decision and analysis for both candidates.
        if (
            explicit is None
            or int(explicit.group(1)) != selected
            or analysis_selected != selected
            or set(analysis) != {0, 1}
        ):
            return {
                "status": "failed",
                "reason": "advisor response did not satisfy the route-selection schema",
                **ai_advisor.advisor_identity(),
            }
    except Exception as exc:
        return {"status": "failed", "error_type": type(exc).__name__, **ai_advisor.advisor_identity()}
    return {
        "status": "passed",
        "selected_route_index": selected,
        "response_received": bool(output),
        **ai_advisor.advisor_identity(),
    }


def main() -> int:
    args = _arguments()
    if not args.live:
        print("SKIPPED: pass --live to allow one bounded live advisor request")
        return 0
    result = asyncio.run(certify())
    safe_fields = (
        "status", "advisor_provider", "advisor_model", "selected_route_index",
        "response_received", "error_type",
    )
    print("Advisor live certification: " + " ".join(
        f"{key}={result[key]}" for key in safe_fields if key in result
    ) + (
        f" reason={result['reason']}"
        if result.get("reason") in {
            "advisor key is not configured",
            "advisor response did not satisfy the route-selection schema",
        }
        else ""
    ))
    return 0 if result["status"] in {"passed", "skipped"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
