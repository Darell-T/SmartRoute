#!/usr/bin/env python3
"""CLI runner for the golden-query eval harness (plan doc section 7, Layer
2). Runs the REAL Anthropic model against REPLAYED tool fixtures and
asserts on the resulting `TurnTrace`. Manual/on-demand only -- spends real
tokens, never runs in CI.

Usage (from `backend/`):
    python evals/run_agent_evals.py                    # run every query
    python evals/run_agent_evals.py --only t2_no_bus_cart,t5_pizza_first
    python evals/run_agent_evals.py --tier T2
    python evals/run_agent_evals.py --model claude-sonnet-5 --verbose
    python evals/run_agent_evals.py --validate          # no model, no network

See README.md for the full workflow (recording new fixtures, adding a
query, expected cost per run).
"""

from __future__ import annotations

import argparse
import asyncio
import dataclasses
import json
import os
import re
import sys
import time
from pathlib import Path
from types import SimpleNamespace

# Bootstrap `backend/` onto sys.path regardless of how this file is
# invoked -- `python evals/run_agent_evals.py` from `backend/`, `python
# run_agent_evals.py` from inside `evals/`, or imported normally as
# `evals.run_agent_evals` by pytest (which already has `backend/` on
# sys.path via its rootdir-insertion for the `tests` package; inserting
# again here is a harmless no-op in that case).
_BACKEND_ROOT = Path(__file__).resolve().parent.parent
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

import yaml  # noqa: E402

from evals import assertions as assertions_mod  # noqa: E402
from evals import fixture_router  # noqa: E402

EVALS_DIR = Path(__file__).resolve().parent
DEFAULT_QUERIES_PATH = EVALS_DIR / "golden_queries.yaml"
DEFAULT_FIXTURES_ROOT = EVALS_DIR / "fixtures"

KNOWN_TIERS = {"T1", "T2", "T3", "T4", "T5", "T6"}

_ISO_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?(Z|[+-]\d{2}:\d{2})$")

# Rough $/token guardrail constants, mirrored from app/services/agent/budget.py
# (not imported from there to keep --validate import-light and dependency-free
# of the agent package's own env-driven module state).
_INPUT_COST_PER_TOKEN_USD = 0.000003
_OUTPUT_COST_PER_TOKEN_USD = 0.000015


@dataclasses.dataclass
class QueryResult:
    query_id: str
    tier: str
    ok: bool
    assertion_results: list
    final_text: str
    tool_calls: list
    usage: dict
    events: list = dataclasses.field(default_factory=list)
    run_error: str | None = None


def load_queries(path: Path = DEFAULT_QUERIES_PATH) -> list[dict]:
    with open(path) as f:
        doc = yaml.safe_load(f)
    return doc.get("queries") or []


def _select_queries(queries: list[dict], *, only: list[str] | None, tier: str | None) -> list[dict]:
    selected = queries
    if tier:
        selected = [q for q in selected if q.get("tier") == tier]
    if only:
        wanted = set(only)
        selected = [q for q in selected if q.get("id") in wanted]
    return selected


async def run_single_query(
    query: dict,
    *,
    fixtures_root: Path = DEFAULT_FIXTURES_ROOT,
    model: str | None = None,
    fuzzy: bool | None = None,
) -> QueryResult:
    """Runs every turn of one query on a fresh session, with fixture
    resolution installed, then evaluates its assertions. Imports
    `app.services.agent.{loop,session}` lazily (not at module import time)
    so `--validate` never needs a model client or ANTHROPIC_API_KEY -- and
    so tests can swap in a fake `anthropic` module via `sys.modules` before
    this function's first call and have it picked up (see
    `tests/test_agent_evals.py`).
    """
    from app.services.agent import loop as agent_loop
    from app.services.agent import session as session_module

    if fuzzy is None:
        fuzzy = os.getenv("AGENT_TOOL_FIXTURES_FUZZY", "1").strip() != "0"

    query_id = query["id"]
    tier = query.get("tier", "")
    context = query.get("context") or {}
    now_et = context.get("now")
    origin = context.get("origin_gps")
    explicit_fixtures = query.get("fixtures") or {}

    if model:
        previous_model = agent_loop.AGENT_MODEL
        agent_loop.AGENT_MODEL = model
    else:
        previous_model = None

    session_id, session = session_module.new_session()
    all_tool_calls: list[tuple[str, dict]] = []
    all_events: list = []
    last_final_text = ""
    total_usage = {"input_tokens": 0, "output_tokens": 0}
    run_error: str | None = None

    try:
        with fixture_router.install(query_id, fixtures_root, explicit_fixtures, fuzzy=fuzzy) as resolver:
            for message in query.get("messages") or []:
                turn_id = session_module.next_turn_id(session)
                trace = agent_loop.TurnTrace()
                try:
                    async for event in agent_loop.run_agent_turn(
                        session=session,
                        session_id=session_id,
                        turn_id=turn_id,
                        message=message,
                        now_et=now_et,
                        gtfs=None,
                        origin=origin,
                        trace=trace,
                    ):
                        all_events.append(event)
                        if getattr(event, "type", None) == "done":
                            usage = getattr(event, "usage", None) or {}
                            total_usage["input_tokens"] += int(usage.get("input_tokens") or 0)
                            total_usage["output_tokens"] += int(usage.get("output_tokens") or 0)
                except Exception as exc:  # pragma: no cover -- surfaced as a failed query, not a crash
                    run_error = f"{type(exc).__name__}: {exc}"
                    break
                all_tool_calls.extend(trace.tool_calls)
                last_final_text = trace.final_text
            call_log = list(resolver.call_log)
    finally:
        if previous_model is not None:
            agent_loop.AGENT_MODEL = previous_model

    trace_like = SimpleNamespace(tool_calls=all_tool_calls, final_text=last_final_text)

    if run_error is not None:
        return QueryResult(
            query_id=query_id,
            tier=tier,
            ok=False,
            assertion_results=[],
            final_text=last_final_text,
            tool_calls=all_tool_calls,
            usage=total_usage,
            events=all_events,
            run_error=run_error,
        )

    results = assertions_mod.evaluate_all(
        query.get("assertions") or [],
        trace=trace_like,
        final_text=last_final_text,
        events=all_events,
        call_log=call_log,
    )
    ok = all(r.ok for r in results)
    return QueryResult(
        query_id=query_id,
        tier=tier,
        ok=ok,
        assertion_results=results,
        final_text=last_final_text,
        tool_calls=all_tool_calls,
        usage=total_usage,
        events=all_events,
    )


# ---------------------------------------------------------------- validate --


def _require_keys(d: dict, keys: list[str], where: str) -> list[str]:
    return [f"{where}: missing required key '{k}'" for k in keys if k not in d]


_PLAN_TRIP_CANDIDATE_KEYS = [
    "card_id",
    "lines",
    "eta_minutes",
    "transfers",
    "departs_iso",
    "arrives_iso",
    "walk_minutes",
    "alert_headlines",
    "reason",
]
_EVENT_LOOKUP_EVENT_KEYS = ["name", "venue_name", "venue_key", "start_iso", "estimated_end_iso", "end_estimate_basis"]
_POI_RESULT_KEYS = ["name", "address", "lat", "lng", "open_now"]
_VENUE_CROWD_KEYS = ["venue", "stations", "lines", "surge_start_iso", "surge_end_iso", "alternates", "note", "is_heuristic"]


def _validate_fixture_shape(tool_name: str, payload: dict, where: str) -> list[str]:
    problems = _require_keys(payload, ["ok", "data", "summary", "error"], where)
    if problems:
        return problems
    if not payload["ok"]:
        return []  # error fixtures just need the envelope keys, checked above
    data = payload.get("data")
    if not isinstance(data, dict):
        return [f"{where}: ok=true but 'data' is not an object"]

    if tool_name == "plan_trip":
        candidates = data.get("candidates")
        if not isinstance(candidates, list) or not candidates:
            return [f"{where}: plan_trip data.candidates must be a non-empty list"]
        for i, cand in enumerate(candidates):
            problems += _require_keys(cand, _PLAN_TRIP_CANDIDATE_KEYS, f"{where}.candidates[{i}]")
    elif tool_name == "event_lookup":
        events = data.get("events")
        if not isinstance(events, list):
            problems.append(f"{where}: event_lookup data.events must be a list")
        else:
            for i, event in enumerate(events):
                problems += _require_keys(event, _EVENT_LOOKUP_EVENT_KEYS, f"{where}.events[{i}]")
    elif tool_name == "poi_search":
        results = data.get("results")
        if not isinstance(results, list):
            problems.append(f"{where}: poi_search data.results must be a list")
        else:
            for i, result in enumerate(results):
                problems += _require_keys(result, _POI_RESULT_KEYS, f"{where}.results[{i}]")
    elif tool_name == "venue_crowd_window":
        problems += _require_keys(data, _VENUE_CROWD_KEYS, where)
    elif tool_name == "transit_snapshot":
        pass  # no golden query currently exercises this tool; shape not pinned down here.
    return problems


def validate(queries_path: Path = DEFAULT_QUERIES_PATH, fixtures_root: Path = DEFAULT_FIXTURES_ROOT) -> list[str]:
    """Static, model-free, network-free sanity check: every query parses,
    every referenced fixture exists and is valid JSON with the right
    shape, every assertion references a known op/tool. Returns a list of
    problem strings (empty = clean)."""
    problems: list[str] = []

    try:
        queries = load_queries(queries_path)
    except (OSError, yaml.YAMLError) as exc:
        return [f"failed to parse {queries_path}: {exc}"]

    if not queries:
        return [f"{queries_path}: no queries found under the 'queries' key"]

    known_tools = {"plan_trip", "transit_snapshot", "event_lookup", "poi_search", "venue_crowd_window"}
    seen_ids: set[str] = set()

    for query in queries:
        where = f"query[{query.get('id', '?')}]"
        for key in ("id", "tier", "messages", "context", "assertions"):
            if key not in query:
                problems.append(f"{where}: missing required key '{key}'")
        query_id = query.get("id")
        if not query_id:
            continue
        if query_id in seen_ids:
            problems.append(f"{where}: duplicate query id")
        seen_ids.add(query_id)

        tier = query.get("tier")
        if tier not in KNOWN_TIERS:
            problems.append(f"{where}: unknown tier {tier!r}")

        messages = query.get("messages")
        if not isinstance(messages, list) or not messages or not all(isinstance(m, str) and m.strip() for m in messages):
            problems.append(f"{where}: 'messages' must be a non-empty list of non-empty strings")

        context = query.get("context") or {}
        now_raw = context.get("now")
        if not now_raw or not _ISO_RE.match(str(now_raw)):
            problems.append(f"{where}: context.now must be an RFC3339 timestamp with an offset, got {now_raw!r}")
        origin_gps = context.get("origin_gps")
        if origin_gps is not None and ("lat" not in origin_gps or "lng" not in origin_gps):
            problems.append(f"{where}: context.origin_gps must have 'lat' and 'lng'")

        assertion_list = query.get("assertions")
        if not isinstance(assertion_list, list) or not assertion_list:
            problems.append(f"{where}: 'assertions' must be a non-empty list")
        else:
            for i, assertion in enumerate(assertion_list):
                spec_problem = assertions_mod.validate_spec(assertion)
                if spec_problem:
                    problems.append(f"{where}.assertions[{i}]: {spec_problem}")
                else:
                    for key in ("tool_called", "tool_not_called", "tool_input", "call_count"):
                        if key in assertion and assertion[key].get("name") not in known_tools:
                            problems.append(
                                f"{where}.assertions[{i}]: unknown tool {assertion[key].get('name')!r} "
                                f"(known: {sorted(known_tools)})"
                            )
                    if "call_order" in assertion:
                        unknown = [n for n in assertion["call_order"] if n not in known_tools]
                        if unknown:
                            problems.append(f"{where}.assertions[{i}]: unknown tool(s) in call_order: {unknown}")

        explicit_fixtures = query.get("fixtures") or {}
        for tool_name, filenames in explicit_fixtures.items():
            if tool_name not in known_tools:
                problems.append(f"{where}.fixtures: unknown tool {tool_name!r}")
            for filename in filenames:
                path = fixtures_root / query_id / tool_name / filename
                if not path.is_file():
                    problems.append(f"{where}.fixtures: {tool_name}/{filename} does not exist at {path}")

        # Every fixture file actually on disk for this query must be valid
        # JSON with the right envelope/shape -- not just the ones an
        # explicit mapping names, since the single-file fallback can serve
        # any file sitting in a tool's directory.
        for tool_name, path in fixture_router.iter_fixture_files(fixtures_root, query_id):
            if tool_name not in known_tools:
                problems.append(f"{where}.fixtures: fixture under unknown tool dir {tool_name!r} ({path})")
                continue
            try:
                payload = json.loads(path.read_text())
            except (OSError, ValueError) as exc:
                problems.append(f"{where}.fixtures: {path} is not valid JSON ({exc})")
                continue
            if not isinstance(payload, dict):
                problems.append(f"{where}.fixtures: {path} must contain a JSON object")
                continue
            problems += [f"{where}.fixtures: {p}" for p in _validate_fixture_shape(tool_name, payload, str(path))]

    return problems


# ---------------------------------------------------------------- CLI --


def _print_verbose_failure(result: QueryResult) -> None:
    print(f"    final_text: {result.final_text!r}")
    print(f"    tool_calls: {result.tool_calls}")
    if result.run_error:
        print(f"    run_error: {result.run_error}")
    for assertion_result in result.assertion_results:
        marker = "PASS" if assertion_result.ok else "FAIL"
        print(f"    [{marker}] {assertion_result.spec} -> {assertion_result.detail}")


async def _run_all(
    queries: list[dict], *, fixtures_root: Path, model: str | None, verbose: bool
) -> list[QueryResult]:
    results: list[QueryResult] = []
    for query in queries:
        result = await run_single_query(query, fixtures_root=fixtures_root, model=model)
        status = "PASS" if result.ok else "FAIL"
        print(f"[{status}] {result.tier:<3} {result.query_id}")
        if not result.ok and verbose:
            _print_verbose_failure(result)
        results.append(result)
    return results


def _print_summary(results: list[QueryResult]) -> None:
    print("\n--- summary by tier ---")
    tiers = sorted({r.tier for r in results})
    total_pass = total_fail = 0
    for tier in tiers:
        tier_results = [r for r in results if r.tier == tier]
        passed = sum(1 for r in tier_results if r.ok)
        failed = len(tier_results) - passed
        total_pass += passed
        total_fail += failed
        print(f"  {tier}: {passed}/{len(tier_results)} passed")
    print(f"  TOTAL: {total_pass}/{total_pass + total_fail} passed")

    total_input = sum(r.usage.get("input_tokens", 0) for r in results)
    total_output = sum(r.usage.get("output_tokens", 0) for r in results)
    cost = total_input * _INPUT_COST_PER_TOKEN_USD + total_output * _OUTPUT_COST_PER_TOKEN_USD
    print(
        f"  tokens: {total_input} in / {total_output} out "
        f"(~${cost:.4f} at list price -- see budget.py for the live rate)"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Golden-query eval harness for the conversational transit agent.")
    parser.add_argument("--only", help="comma-separated query ids to run")
    parser.add_argument("--tier", choices=sorted(KNOWN_TIERS), help="restrict to one tier")
    parser.add_argument("--model", help="override AGENT_MODEL for this run")
    parser.add_argument("--verbose", action="store_true", help="dump trace + final text for failures")
    parser.add_argument("--validate", action="store_true", help="static check only -- no model, no network")
    parser.add_argument("--queries", type=Path, default=DEFAULT_QUERIES_PATH, help="path to golden_queries.yaml")
    parser.add_argument("--fixtures", type=Path, default=DEFAULT_FIXTURES_ROOT, help="path to the fixtures directory")
    args = parser.parse_args(argv)

    if args.validate:
        problems = validate(args.queries, args.fixtures)
        if problems:
            print(f"validate FAILED -- {len(problems)} problem(s):")
            for problem in problems:
                print(f"  - {problem}")
            return 1
        queries = load_queries(args.queries)
        print(f"validate OK -- {len(queries)} queries, all fixtures present and well-formed")
        return 0

    if not os.getenv("ANTHROPIC_API_KEY"):
        print(
            "ANTHROPIC_API_KEY is not set. This runner calls the real Anthropic model "
            "(with tool calls replayed from fixtures) and spends tokens -- set the key "
            "or run with --validate for a model-free static check.",
            file=sys.stderr,
        )
        return 1

    only = [q.strip() for q in args.only.split(",")] if args.only else None
    queries = _select_queries(load_queries(args.queries), only=only, tier=args.tier)
    if not queries:
        print("no queries matched --only/--tier", file=sys.stderr)
        return 1

    started = time.monotonic()
    results = asyncio.run(_run_all(queries, fixtures_root=args.fixtures, model=args.model, verbose=args.verbose))
    elapsed = time.monotonic() - started
    _print_summary(results)
    print(f"  wall clock: {elapsed:.1f}s")

    return 1 if any(not r.ok for r in results) else 0


if __name__ == "__main__":
    raise SystemExit(main())
