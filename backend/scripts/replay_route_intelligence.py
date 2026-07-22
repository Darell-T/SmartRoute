"""Run deterministic SmartRoute baseline/intelligence replay comparisons.

Example: ``python -m scripts.replay_route_intelligence clear-route``.
No live provider or advisor call is permitted by this command.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from app.services.validation.comparison import compare_all_scenarios, compare_scenario, render_human_report
from app.services.validation.replay import ScenarioValidationError


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run deterministic SmartRoute route-intelligence replays.")
    parser.add_argument("scenario", nargs="?", help="Scenario id/directory; omit to run every scenario.")
    parser.add_argument("--json-out", type=Path, help="Write machine-readable report JSON.")
    parser.add_argument("--text-out", type=Path, help="Write concise human-readable report.")
    return parser.parse_args()


async def _run(args: argparse.Namespace) -> list[dict]:
    return [await compare_scenario(args.scenario)] if args.scenario else await compare_all_scenarios()


def main() -> int:
    args = _arguments()
    try:
        reports = asyncio.run(_run(args))
    except ScenarioValidationError as exc:
        print(f"Replay validation error: {exc}")
        return 2
    machine = {"reports": reports, "all_expectations_matched": all(row["comparison"]["matched_expectation"] for row in reports)}
    rendered = "\n\n".join(render_human_report(report) for report in reports)
    if args.json_out:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(json.dumps(machine, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    if args.text_out:
        args.text_out.parent.mkdir(parents=True, exist_ok=True)
        args.text_out.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return 0 if machine["all_expectations_matched"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
