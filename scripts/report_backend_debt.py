"""Report backend production cyclomatic, cognitive, coverage, and CRAP debt."""

from __future__ import annotations

import argparse
import importlib.util
import io
import json
import sys
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from functools import cache
from pathlib import Path

import coverage
from complexipy import file_complexity

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
PHASE2_REPORT = BACKEND / "scripts" / "phase2_quality_report.py"
APP_ROOT = BACKEND / "app"
DEFAULT_COVERAGE = BACKEND / ".coverage"
DEFAULT_MAX_EXISTING = 12
OFFICIAL_CEILING = 10
SURVIVOR_BAND = (11, 12)

BATCH_RULES: tuple[tuple[str, str], ...] = (
    ("6B", "backend/app/services/trips/"),
    ("6D", "backend/app/services/agent/tools/transit/"),
    ("6C", "backend/app/services/agent/tools/"),
    ("6E", "backend/app/services/agent/"),
    ("6A", "backend/app/"),
)


def crap_score(complexity: int, coverage_ratio: float) -> float:
    ratio = min(max(coverage_ratio, 0.0), 1.0)
    return complexity**2 * (1.0 - ratio) ** 3 + complexity


def function_id(file_name: str, function: str, occurrence: int) -> str:
    return f"python:{file_name}:{function}#{occurrence}"


def assign_identities(rows: list[dict[str, object]]) -> list[dict[str, object]]:
    grouped: dict[tuple[str, str], list[dict[str, object]]] = defaultdict(list)
    for row in rows:
        grouped[(str(row["file"]), str(row["function"]))].append(row)
    for items in grouped.values():
        items.sort(key=lambda item: int(item["line"]))
        for occurrence, row in enumerate(items):
            row["id"] = function_id(
                str(row["file"]), str(row["function"]), occurrence
            )
    return rows


def posix_rel(path: Path, base: Path) -> str:
    return path.resolve().relative_to(base.resolve()).as_posix()


def in_scope(file_name: str, prefixes: Sequence[str]) -> bool:
    if not prefixes:
        return True
    return any(file_name.startswith(prefix) for prefix in prefixes)


def batch_for(
    file_name: str,
    rules: Sequence[tuple[str, str]] = BATCH_RULES,
) -> str:
    match = max(
        (
            (prefix, batch)
            for batch, prefix in rules
            if file_name.startswith(prefix)
        ),
        key=lambda item: len(item[0]),
        default=None,
    )
    if match is None:
        return "unassigned"
    return match[1]


@cache
def load_phase2():
    spec = importlib.util.spec_from_file_location(
        "phase2_quality_report", PHASE2_REPORT
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load {PHASE2_REPORT}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def cognitive_index(files: Sequence[Path]) -> dict[tuple[str, int], int]:
    mapping: dict[tuple[str, int], int] = {}
    for path in files:
        analysis = file_complexity(str(path), no_ignore=True)
        rel = f"backend/{posix_rel(path, BACKEND)}"
        for function in analysis.functions:
            mapping[(rel, function.line_start)] = int(function.complexity)
    return mapping


def lookup_cognitive(
    cognitive: Mapping[tuple[str, int], int],
    file_name: str,
    line: int,
) -> int:
    for candidate in (line, line - 1, line + 1):
        value = cognitive.get((file_name, candidate))
        if value is not None:
            return int(value)
    return 0


def decorate_row(
    row: Mapping[str, object],
    cognitive: Mapping[tuple[str, int], int],
) -> dict[str, object]:
    file_name = f"backend/{row['file']}"
    line = int(row["line"])
    cyclomatic = int(row["complexity"])
    cognitive_cc = lookup_cognitive(cognitive, file_name, line)
    coverage_ratio = float(row["coverage"])
    uncovered = int(row["statement_count"]) > 0 and coverage_ratio == 0.0
    return {
        "file": file_name,
        "function": row["function"],
        "line": line,
        "complexity": cyclomatic,
        "cognitive": cognitive_cc,
        "coverage": coverage_ratio,
        "crap": crap_score(cyclomatic, coverage_ratio),
        "coverage_status": "uncovered" if uncovered else "measured",
        "batch": batch_for(file_name),
    }


def collect_rows(
    files: Sequence[Path], coverage_file: Path
) -> list[dict[str, object]]:
    phase2 = load_phase2()
    measured = phase2.measured_functions(list(files), BACKEND, coverage_file)
    cognitive = cognitive_index(files)
    rows = [decorate_row(row, cognitive) for row in measured]
    return assign_identities(rows)


def metric(row: Mapping[str, object]) -> int:
    return max(int(row["complexity"]), int(row["cognitive"]))


def exceeds_existing(row: Mapping[str, object], max_existing: int) -> bool:
    return metric(row) > max_existing


def overall_coverage_percent(coverage_file: Path) -> float:
    measured = coverage.Coverage(data_file=str(coverage_file))
    measured.load()
    sink = io.StringIO()
    return round(float(measured.report(file=sink)), 1)


def _sorted_rows(rows: Iterable[Mapping[str, object]]) -> list[Mapping[str, object]]:
    return sorted(
        rows,
        key=lambda row: (str(row["file"]), int(row["line"]), str(row["function"])),
    )


def count_bucket(
    rows: Sequence[Mapping[str, object]],
    max_existing: int = DEFAULT_MAX_EXISTING,
) -> dict[str, int]:
    return {
        "functions": len(rows),
        "above_10": sum(1 for row in rows if metric(row) > OFFICIAL_CEILING),
        "above_12": sum(1 for row in rows if exceeds_existing(row, max_existing)),
        "at_11_or_12": sum(1 for row in rows if metric(row) in SURVIVOR_BAND),
        "crap_above_30": sum(1 for row in rows if float(row["crap"]) > 30),
        "zero_covered": sum(
            1 for row in rows if row["coverage_status"] == "uncovered"
        ),
    }


def by_batch(
    rows: Sequence[Mapping[str, object]],
    max_existing: int = DEFAULT_MAX_EXISTING,
) -> dict[str, dict[str, int]]:
    grouped: dict[str, list[Mapping[str, object]]] = defaultdict(list)
    for row in rows:
        grouped[str(row["batch"])].append(row)
    return {
        batch: count_bucket(items, max_existing)
        for batch, items in sorted(grouped.items())
    }


def compact(row: Mapping[str, object]) -> dict[str, object]:
    return {
        "id": row["id"],
        "batch": row["batch"],
        "file": row["file"],
        "function": row["function"],
        "line": row["line"],
        "complexity": int(row["complexity"]),
        "cognitive": int(row["cognitive"]),
        "coverage": row["coverage"],
        "crap": round(float(row["crap"]), 3),
        "coverage_status": row["coverage_status"],
    }


def build_report(
    rows: Sequence[Mapping[str, object]],
    *,
    max_existing: int,
    overall_coverage: float,
    coverage_file: str,
) -> dict[str, object]:
    ordered = _sorted_rows(rows)
    over_max = [
        compact(row) for row in ordered if exceeds_existing(row, max_existing)
    ]
    crap_hot = [
        compact(row) for row in ordered if float(row["crap"]) > 30
    ]
    zero = [
        compact(row)
        for row in ordered
        if row["coverage_status"] == "uncovered"
    ]
    survivors = [
        compact(row)
        for row in ordered
        if metric(row) in SURVIVOR_BAND
    ]
    return {
        "max_existing": max_existing,
        "coverage_file": coverage_file,
        "overall_coverage_percent": overall_coverage,
        "counts": count_bucket(ordered, max_existing),
        "by_batch": by_batch(ordered, max_existing),
        "above_max_existing": over_max,
        "crap_above_30": crap_hot,
        "zero_covered": zero,
        "survivors_11_or_12": survivors,
        "functions": [compact(row) for row in ordered],
    }


def print_summary(report: Mapping[str, object]) -> None:
    counts = report["counts"]
    print(f"max_existing: {report['max_existing']}")
    print(f"overall_coverage_percent: {report['overall_coverage_percent']}")
    print(
        "functions={functions} above_10={above_10} above_12={above_12} "
        "at_11_or_12={at_11_or_12} crap_above_30={crap_above_30} "
        "zero_covered={zero_covered}".format(**counts)
    )
    print("by_batch:")
    for batch, bucket in report["by_batch"].items():
        print(
            f"  {batch}: functions={bucket['functions']} "
            f"above_12={bucket['above_12']} at_11_or_12={bucket['at_11_or_12']} "
            f"crap_above_30={bucket['crap_above_30']} "
            f"zero_covered={bucket['zero_covered']}"
        )
    over = report["above_max_existing"]
    preview = over[:40]
    if over:
        print(f"above_max_existing ({len(over)}):")
        for row in preview:
            print(
                f"  {row['batch']} {row['complexity']}/{row['cognitive']} "
                f"crap={row['crap']} {row['file']}:{row['function']}"
            )
        hidden = len(over) - len(preview)
        if hidden:
            print(f"  ... {hidden} more in --output")


def write_output(report: Mapping[str, object], output: str | None) -> None:
    if not output:
        return
    path = Path(output)
    if not path.is_absolute():
        path = ROOT / path
    path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {path}")


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--max-existing", type=int, default=DEFAULT_MAX_EXISTING)
    parser.add_argument("--scope", action="append", default=[])
    parser.add_argument("--output")
    parser.add_argument("--coverage-file", default=str(DEFAULT_COVERAGE))
    parser.add_argument("--self-test", action="store_true")
    return parser.parse_args(argv)


def _self_test_threshold() -> None:
    low = {"complexity": 12, "cognitive": 10}
    high_cc = {"complexity": 13, "cognitive": 4}
    high_cog = {"complexity": 4, "cognitive": 13}
    if exceeds_existing(low, 12):
        raise AssertionError("12 must remain allowed for existing functions")
    if not exceeds_existing(high_cc, 12):
        raise AssertionError("cyclomatic 13 must fail --max-existing 12")
    if not exceeds_existing(high_cog, 12):
        raise AssertionError("cognitive 13 must fail --max-existing 12")


def _self_test_identities() -> None:
    rows = assign_identities(
        [
            {"file": "backend/app/a.py", "function": "dup", "line": 40},
            {"file": "backend/app/a.py", "function": "dup", "line": 10},
            {"file": "backend/app/b.py", "function": "once", "line": 1},
        ]
    )
    by_line = {int(row["line"]): row["id"] for row in rows}
    expected = {
        10: "python:backend/app/a.py:dup#0",
        40: "python:backend/app/a.py:dup#1",
        1: "python:backend/app/b.py:once#0",
    }
    if by_line != expected:
        raise AssertionError(f"unstable identities: {by_line}")


def _self_test_scope_and_batch() -> None:
    if not in_scope("backend/app/services/mta/gtfs.py", ["backend/app/services/mta"]):
        raise AssertionError("scope prefix must include matching files")
    if in_scope("backend/app/services/trips/x.py", ["backend/app/services/mta"]):
        raise AssertionError("scope prefix must exclude other packages")
    if not in_scope("backend/app/a.py", []):
        raise AssertionError("empty scope must include every file")
    mapping = {
        "backend/app/services/trips/foo.py": "6B",
        "backend/app/services/agent/tools/transit/x.py": "6D",
        "backend/app/services/agent/tools/places/y.py": "6C",
        "backend/app/services/agent/tools/shared.py": "6C",
        "backend/app/services/agent/turn/z.py": "6E",
        "backend/app/services/mta/gtfs.py": "6A",
        "frontend/lib/x.ts": "unassigned",
    }
    actual = {path: batch_for(path) for path in mapping}
    if actual != mapping:
        raise AssertionError(f"batch ownership mismatch: {actual}")
    reversed_rules = tuple(reversed(BATCH_RULES))
    if batch_for("backend/app/services/agent/tools/transit/x.py", reversed_rules) != "6D":
        raise AssertionError("longest prefix must win when a shorter rule is listed first")


def _sample_row(**overrides: object) -> dict[str, object]:
    row: dict[str, object] = {
        "file": "backend/app/a.py",
        "function": "f",
        "line": 1,
        "complexity": 12,
        "cognitive": 10,
        "coverage": 1.0,
        "crap": 12.0,
        "coverage_status": "measured",
        "batch": "6A",
    }
    row.update(overrides)
    return row


def _self_test_exit() -> None:
    allowed = assign_identities(
        [
            _sample_row(
                complexity=12,
                cognitive=10,
                crap=40.0,
                coverage_status="uncovered",
            )
        ]
    )
    blocked = assign_identities(
        [_sample_row(complexity=13, cognitive=4, function="g", line=2)]
    )
    clean = build_report(
        allowed, max_existing=12, overall_coverage=87.2, coverage_file="x"
    )
    dirty = build_report(
        blocked, max_existing=12, overall_coverage=87.2, coverage_file="x"
    )
    if clean["above_max_existing"]:
        raise AssertionError("metric 12 with high CRAP must not fail the 12 bound")
    if not dirty["above_max_existing"]:
        raise AssertionError("metric 13 must fail the 12 bound")


def _self_test_cognitive_lookup() -> None:
    indexed = {("backend/app/main.py", 177): 12}
    if lookup_cognitive(indexed, "backend/app/main.py", 178) != 12:
        raise AssertionError("decorator offset must still bind cognitive complexity")
    if lookup_cognitive(indexed, "backend/app/main.py", 177) != 12:
        raise AssertionError("exact line must bind cognitive complexity")
    if lookup_cognitive(indexed, "backend/app/main.py", 200) != 0:
        raise AssertionError("unknown line must not invent cognitive complexity")


def self_test() -> None:
    _self_test_threshold()
    _self_test_identities()
    _self_test_scope_and_batch()
    _self_test_exit()
    _self_test_cognitive_lookup()
    if abs(crap_score(3, 0.0) - 12.0) > 1e-9:
        raise AssertionError("CRAP formula drifted")


def selected_files(prefixes: Sequence[str]) -> list[Path]:
    phase2 = load_phase2()
    files = []
    for path in phase2.source_files(APP_ROOT):
        file_name = f"backend/{posix_rel(path, BACKEND)}"
        if in_scope(file_name, prefixes):
            files.append(path)
    return files


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    self_test()
    if args.self_test:
        print("self-test passed")
        return 0

    coverage_file = Path(args.coverage_file)
    if not coverage_file.is_absolute():
        coverage_file = ROOT / coverage_file
    if not coverage_file.is_file():
        print(f"coverage file missing: {coverage_file}")
        return 2

    files = selected_files(args.scope)
    rows = collect_rows(files, coverage_file)
    report = build_report(
        rows,
        max_existing=args.max_existing,
        overall_coverage=overall_coverage_percent(coverage_file),
        coverage_file=str(coverage_file),
    )
    print_summary(report)
    write_output(report, args.output)
    if report["above_max_existing"]:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
