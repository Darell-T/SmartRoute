"""Offline replay/provider fault coordinator for release validation."""

from __future__ import annotations

import asyncio
import base64
import contextlib
import io
import tempfile
from dataclasses import replace
from pathlib import Path

from scripts.release.provider_fault_cases import MODEL_CASES, run_model_fault_cases


REPLAY_CASES = ("malformed_payload", "optional_provider_failure")


class FaultValidationError(RuntimeError):
    """A fixed offline replay safety contract failed."""


def _require(condition: bool, reason: str) -> None:
    if not condition:
        raise FaultValidationError(reason)


async def _replay_faults() -> None:
    from app.services.trips import candidates, scoring
    from app.services.trips.itinerary import build_canonical_itinerary
    from evaluation.route_intelligence import advisor_context
    from evaluation.route_intelligence.comparison import compare_scenario
    from evaluation.route_intelligence.replay import ReplayFixtureAdapters, ScenarioValidationError, load_scenario

    scenario = load_scenario("partial-source-failure")
    inputs = await ReplayFixtureAdapters(scenario).load()
    report = await compare_scenario(scenario)
    selected_index, candidate_analysis = advisor_context.parse_advisor_selection(
        inputs.advisor_outputs["intelligence"], len(inputs.route_candidates)
    )
    projected_candidates = candidates._build_route_candidates(
        inputs.route_candidates, selected_index, candidate_analysis,
        scoring._score_routes(inputs.route_candidates, []),
    )
    selected_candidate = projected_candidates[selected_index]
    selected_id = str(selected_candidate["id"])
    itinerary = build_canonical_itinerary(
        selected_candidate["steps"], origin=dict(scenario.origin), destination=dict(scenario.destination),
        generated_at=scenario.clock.now().isoformat(), itinerary_id=selected_id,
    )
    _require(report["comparison"]["matched_expectation"] is True, "partial replay no longer matches its canonical selection")
    _require(report["scan_status"] == "partial", "optional provider failure became false certainty")
    _require(report["intelligence"]["selected_route_id"] == selected_id, "candidate identity changed after optional failure")
    _require(report["baseline"]["selected_route_id"] == selected_id, "baseline candidate identity changed after optional failure")
    _require(itinerary["itinerary_id"] == selected_id, "canonical itinerary identity changed after optional failure")
    _require("partial" in str(report["intelligence"]["recommendation_reason"]).casefold(), "partial scan is not disclosed")

    with tempfile.TemporaryDirectory() as directory:
        malformed_path = Path(directory) / "malformed-alerts.pb64"
        malformed_path.write_text(base64.b64encode(b"not a GTFS realtime payload").decode("ascii"), encoding="ascii")
        malformed = replace(scenario, fixture_paths={**scenario.fixture_paths, "mta_alerts": malformed_path})
        try:
            await ReplayFixtureAdapters(malformed).load()
        except ScenarioValidationError:
            return
    raise FaultValidationError("malformed provider data was accepted")


async def _validate() -> dict[str, object]:
    await _replay_faults()
    evidence = await run_model_fault_cases()
    return {
        **evidence,
        "named_cases": ",".join((*REPLAY_CASES, *MODEL_CASES)),
        "named_case_count": len(REPLAY_CASES) + len(MODEL_CASES),
        "network": "disabled_by_replay_and_fake_provider_seams",
    }


def run_provider_fault_jitter_validation() -> tuple[str, str, dict[str, object]]:
    """Run fixed offline faults and return sanitized release-check inputs."""

    try:
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            evidence = asyncio.run(_validate())
    except Exception as exc:
        return "FAILED", f"offline provider fault validation failed: {type(exc).__name__}", {}
    return "PASSED", "fixed-seed offline provider fault validation passed", evidence
