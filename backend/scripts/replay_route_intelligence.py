"""Run deterministic SmartRoute baseline/intelligence replay comparisons.

Example: ``python -m scripts.replay_route_intelligence clear-route``.
No live provider or advisor call is permitted by this command.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from evaluation.route_intelligence.comparison import (
    compare_all_scenarios,
    compare_scenario,
    render_human_report,
)
from evaluation.route_intelligence.failure_modes import run_source_ablations
from evaluation.route_intelligence.replay import (
    ScenarioValidationError,
    load_all_scenarios,
    load_scenario,
)
from evaluation.route_intelligence.reporting import build_fixture_validation_results


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run deterministic SmartRoute route-intelligence replays.")
    parser.add_argument("scenario", nargs="?", help="Scenario id/directory; omit to run every scenario.")
    parser.add_argument("--json-out", type=Path, help="Write machine-readable report JSON.")
    parser.add_argument("--text-out", type=Path, help="Write concise human-readable report.")
    parser.add_argument("--metrics-out", type=Path, help="Write deterministic fixture metrics JSON.")
    parser.add_argument("--ablations-out", type=Path, help="Write one-source ablation reports JSON.")
    return parser.parse_args()


async def _run(args: argparse.Namespace) -> list[dict]:
    return [await compare_scenario(args.scenario)] if args.scenario else await compare_all_scenarios()


async def _validation_details(args: argparse.Namespace, reports: list[dict]) -> tuple[list[dict], dict]:
    scenarios = [load_scenario(args.scenario)] if args.scenario else load_all_scenarios()
    if [item.scenario_id for item in scenarios] != [str(row.get("scenario_id")) for row in reports]:
        order_mismatch = "comparison report order does not match replay scenarios"
        raise ScenarioValidationError(order_mismatch)
    ablations = [await run_source_ablations(scenario) for scenario in scenarios]
    return ablations, build_fixture_validation_results(reports, scenarios, ablations)


def _print_validation_error(exc: ScenarioValidationError) -> int:
    print(f"Replay validation error: {exc}")
    return 2


def _write_text(path: Path | None, text: str) -> None:
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _write_json(path: Path | None, payload: object) -> None:
    if path is None:
        return
    _write_text(path, json.dumps(payload, indent=2, sort_keys=True) + "\n")


def _run_reports(args: argparse.Namespace) -> list[dict] | int:
    try:
        return asyncio.run(_run(args))
    except ScenarioValidationError as exc:
        return _print_validation_error(exc)


def _validation_payload(
    args: argparse.Namespace, reports: list[dict]
) -> tuple[list[dict] | None, dict | None] | int:
    if not (args.json_out or args.metrics_out or args.ablations_out):
        return None, None
    try:
        return asyncio.run(_validation_details(args, reports))
    except ScenarioValidationError as exc:
        return _print_validation_error(exc)


def _write_outputs(
    args: argparse.Namespace,
    machine: dict,
    rendered: str,
    ablations: list[dict] | None,
    validation: dict | None,
) -> None:
    _write_json(args.json_out, machine)
    _write_text(args.text_out, rendered + "\n")
    if validation is not None:
        _write_json(args.metrics_out, validation)
    if ablations is not None:
        _write_json(args.ablations_out, {"ablations": ablations})


def main() -> int:
    args = _arguments()
    reports = _run_reports(args)
    if isinstance(reports, int):
        return reports
    machine = {
        "reports": reports,
        "all_expectations_matched": all(
            row["comparison"]["matched_expectation"] for row in reports
        ),
    }
    extras = _validation_payload(args, reports)
    if isinstance(extras, int):
        return extras
    ablations, validation = extras
    if validation is not None:
        machine["ablations"] = ablations
        machine["validation"] = validation
    rendered = "\n\n".join(render_human_report(report) for report in reports)
    _write_outputs(args, machine, rendered, ablations, validation)
    print(rendered)
    return 0 if machine["all_expectations_matched"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
