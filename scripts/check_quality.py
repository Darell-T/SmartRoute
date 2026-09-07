"""Project-root structural quality gates.

New functions must keep cyclomatic and cognitive complexity at or below 10.
Existing functions above either threshold may remain as legacy debt but may
not get worse. Ruff separately enforces its default branch and statement
ceilings on changed paths.

CRAP(m) = CC(m)^2 * (1 - coverage(m))^3 + CC(m)

Python CC for CRAP is Radon McCabe. Ruff C901 remains the fast lint gate.
Those metrics are not interchangeable: Radon counts boolean operators and
comprehensions; Ruff C901 counts statement-level control flow only.

CRAP is a coverage-aware regression signal for existing high-complexity
functions. It has no tiny absolute ceiling that forces unnecessary tests or
helper extraction. Function and file lengths are review signals, not automatic
split requirements.

JS/TS coverage states:
    measured    mapping is unique and coverage > 0
    uncovered   mapping is reliable and coverage is 0
    unresolved  V8/tsx output cannot be confidently bound to the function

Unresolved functions never receive a fabricated 0% CRAP score.

Adoption uses quality/baseline.json as a ratchet, not a permanent allowlist.
New functions must meet the cyclomatic ceiling. Existing baseline entries may
not increase in cyclomatic complexity or CRAP. Compliant functions must be
removed from the baseline before they can be treated as clean.

Cognitive complexity is compared with a fixed Git point, HEAD by default.
Pass --quality-ref with the immutable batch checkpoint during independent
review.

Raw ESLint, Oxlint, and Ruff commands still report the full backlog.
This command applies the ratchet.

Run from the repository root:

    python scripts/check_quality.py
    python scripts/check_quality.py --self-test
    python scripts/check_quality.py --update-baseline
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
from collections import defaultdict
from collections.abc import Mapping
from pathlib import Path
from urllib.parse import unquote, urlparse

from complexipy import compute_diff, file_complexity

MAX_COMPLEXITY = 10
MAX_COGNITIVE_COMPLEXITY = 10
FUNCTION_LENGTH_REVIEW = 100
FILE_LENGTH_REVIEW = 500
BASELINE_VERSION = 2

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
FRONTEND = ROOT / "frontend"
PHASE2_REPORT = BACKEND / "scripts" / "phase2_quality_report.py"
JS_METRICS = ROOT / "scripts" / "js_function_metrics.mjs"
BASELINE_PATH = ROOT / "quality" / "baseline.json"

COMPLEXITY_IN_MESSAGE = re.compile(r"complexity of (\d+)", re.IGNORECASE)
RUFF_COMPLEXITY_IN_MESSAGE = re.compile(
    r"`(?P<name>[^`]+)` is too complex \((?P<cc>\d+)"
)
ESLINT_FUNCTION_IN_MESSAGE = re.compile(r"^(.*) has a complexity of ")


def crap_score(complexity: int, coverage: float) -> float:
    coverage = min(max(coverage, 0.0), 1.0)
    return complexity**2 * (1.0 - coverage) ** 3 + complexity


def language_key(language: str) -> str:
    return "python" if language.lower().startswith("py") else "typescript"


def function_id(language: str, file_name: str, function: str, occurrence: int) -> str:
    return f"{language_key(language)}:{file_name}:{function}#{occurrence}"


def assign_identities(rows: list[dict[str, object]]) -> list[dict[str, object]]:
    grouped: dict[tuple[str, str, str], list[dict[str, object]]] = defaultdict(list)
    for row in rows:
        grouped[(str(row["language"]), str(row["file"]), str(row["function"]))].append(
            row
        )
    for items in grouped.values():
        items.sort(key=lambda item: int(item["line"]))
        for occurrence, row in enumerate(items):
            row["occurrence"] = occurrence
            row["id"] = function_id(
                str(row["language"]),
                str(row["file"]),
                str(row["function"]),
                occurrence,
            )
    return rows


def is_violating(row: Mapping[str, object]) -> bool:
    return int(row["complexity"]) > MAX_COMPLEXITY


def worsened_vs(row: Mapping[str, object], previous: Mapping[str, object]) -> bool:
    if int(row["complexity"]) > int(previous["complexity"]):
        return True
    current_crap = row.get("crap")
    previous_crap = previous.get("crap")
    if current_crap is None or previous_crap is None:
        return False
    return float(current_crap) > float(previous_crap) + 1e-9


def classify_js_coverage(
    *,
    anonymous: bool,
    file_executed: bool,
    match_count: int,
    coverage: float | None,
) -> tuple[str, float | None, str]:
    """Return coverage_status, coverage, reason."""

    if not file_executed:
        return "uncovered", 0.0, "file was not executed during the coverage run"
    if match_count == 1 and coverage is not None:
        if coverage == 0.0:
            return "uncovered", 0.0, "unique function mapping with zero execution"
        return "measured", coverage, "unique function mapping"
    if anonymous:
        return (
            "unresolved",
            None,
            "anonymous function cannot be uniquely mapped in an executed file",
        )
    if match_count == 0:
        return (
            "unresolved",
            None,
            "named function missing from V8 inventory of an executed file",
        )
    return (
        "unresolved",
        None,
        "multiple V8 functions share this name with disjoint ranges",
    )


def v8_block_coverage(ranges: list[dict[str, object]]) -> float:
    if not ranges:
        return 0.0
    span = ranges[0]
    total = int(span["endOffset"]) - int(span["startOffset"])
    count = int(span["count"])
    if total <= 0:
        return 1.0 if count else 0.0
    if count <= 0:
        return 0.0
    uncovered = 0
    for rng in ranges[1:]:
        if int(rng["count"]) == 0:
            uncovered += max(0, int(rng["endOffset"]) - int(rng["startOffset"]))
    return max(0.0, min(1.0, 1.0 - min(uncovered, total) / total))


def collapse_v8_entries(
    entries: list[dict[str, object]],
) -> tuple[int, float | None]:
    # tsx can emit the same named function more than once in one compiled
    # script. Same-name copies are treated as one function. Coverage is the
    # best measured copy so an unexecuted wrapper cannot hide execution.
    if not entries:
        return 0, None
    return 1, max(float(item["coverage"]) for item in entries)


def evaluate_ratchet(
    current_rows: list[dict[str, object]],
    baseline_entries: list[dict[str, object]] | None,
) -> dict[str, object]:
    if baseline_entries is None:
        return {
            "missing_baseline": True,
            "new_debt": [row for row in current_rows if is_violating(row)],
            "worsened": [],
            "resolved": [],
            "legacy": [],
            "stale_baseline": [],
        }
    current_by_id = {str(row["id"]): row for row in current_rows}
    baseline_by_id = {str(entry["id"]): entry for entry in baseline_entries}
    new_debt = []
    worsened = []
    resolved = []
    legacy = []
    for row in current_rows:
        previous = baseline_by_id.get(str(row["id"]))
        violating = is_violating(row)
        if not violating:
            if previous is not None:
                resolved.append(row)
            continue
        if previous is None:
            new_debt.append(row)
            continue
        if worsened_vs(row, previous):
            worsened.append(row)
        else:
            legacy.append(row)
    stale = []
    for entry in baseline_entries:
        row = current_by_id.get(str(entry["id"]))
        if row is None or not is_violating(row):
            stale.append(entry)
    return {
        "missing_baseline": False,
        "new_debt": new_debt,
        "worsened": worsened,
        "resolved": resolved,
        "legacy": legacy,
        "stale_baseline": stale,
    }


def baseline_entries_from_rows(
    rows: list[dict[str, object]],
) -> list[dict[str, object]]:
    entries = []
    for row in rows:
        if not is_violating(row):
            continue
        entry = {
            "id": row["id"],
            "language": row["language"],
            "file": row["file"],
            "function": row["function"],
            "complexity": int(row["complexity"]),
            "coverage_status": row.get("coverage_status"),
        }
        if row.get("crap") is not None:
            entry["crap"] = float(row["crap"])
        else:
            entry["crap"] = None
        entries.append(entry)
    return sorted(entries, key=lambda item: str(item["id"]))


def shrink_baseline(
    current_rows: list[dict[str, object]],
    previous_entries: list[dict[str, object]],
) -> list[dict[str, object]]:
    current_by_id = {str(row["id"]): row for row in current_rows}
    shrunk = []
    for entry in previous_entries:
        row = current_by_id.get(str(entry["id"]))
        if row is None or not is_violating(row):
            continue
        complexity = min(int(entry["complexity"]), int(row["complexity"]))
        current_crap = row.get("crap")
        previous_crap = entry.get("crap")
        crap = None
        if current_crap is not None and previous_crap is not None:
            crap = min(float(current_crap), float(previous_crap))
        elif current_crap is not None:
            crap = float(current_crap)
        shrunk.append(
            {
                "id": entry["id"],
                "language": row["language"],
                "file": row["file"],
                "function": row["function"],
                "complexity": complexity,
                "crap": crap,
                "coverage_status": row.get("coverage_status"),
            }
        )
    return sorted(shrunk, key=lambda item: str(item["id"]))


def _self_test_crap() -> None:
    checks = (
        (6, 1.0, 6.0),
        (6, 0.5, 10.5),
        (4, 1.0, 4.0),
        (3, 0.0, 12.0),
        (2, 0.0, 6.0),
    )
    for complexity, coverage, expected in checks:
        actual = crap_score(complexity, coverage)
        if abs(actual - expected) > 1e-9:
            raise AssertionError(
                f"CRAP({complexity}, {coverage}) = {actual}, expected {expected}"
            )


def _self_test_js_coverage() -> None:
    status, coverage, _reason = classify_js_coverage(
        anonymous=False, file_executed=True, match_count=1, coverage=0.0
    )
    if status != "uncovered" or coverage != 0.0:
        raise AssertionError(f"known 0% should be uncovered, got {status} {coverage}")
    status, coverage, _reason = classify_js_coverage(
        anonymous=False, file_executed=True, match_count=1, coverage=0.5
    )
    if status != "measured" or coverage != 0.5:
        raise AssertionError(
            f"known partial should be measured, got {status} {coverage}"
        )
    status, coverage, _reason = classify_js_coverage(
        anonymous=True, file_executed=True, match_count=0, coverage=None
    )
    if status != "unresolved" or coverage is not None:
        raise AssertionError("mapping failure should be unresolved without coverage")
    status, coverage, _reason = classify_js_coverage(
        anonymous=False, file_executed=False, match_count=0, coverage=None
    )
    if status != "uncovered" or coverage != 0.0:
        raise AssertionError("unexecuted file should be uncovered, not unresolved")

    if collapse_v8_entries([]) != (0, None):
        raise AssertionError("empty V8 entries should be a miss")
    nested = collapse_v8_entries(
        [
            {"start": 0, "end": 100, "span": 100, "coverage": 0.8},
            {"start": 10, "end": 40, "span": 30, "coverage": 0.2},
        ]
    )
    if nested != (1, 0.8):
        raise AssertionError(
            f"same-name V8 copies should keep the best coverage, got {nested}"
        )
    copies = collapse_v8_entries(
        [
            {"start": 0, "end": 10, "span": 10, "coverage": 1.0},
            {"start": 50, "end": 80, "span": 30, "coverage": 0.0},
        ]
    )
    if copies != (1, 1.0):
        raise AssertionError(
            f"tsx duplicate copies should not look like a collision, got {copies}"
        )


def _assert_ids(actual: object, expected: object, label: str) -> None:
    if actual != expected:
        raise AssertionError(f"{label} mismatch: {actual}")


def _self_test_ratchet() -> None:
    current = [
        {
            "id": "python:a.py:legacy#0",
            "complexity": 12,
            "crap": 14.0,
            "language": "Python",
            "file": "a.py",
            "function": "legacy",
            "line": 1,
        },
        {
            "id": "python:a.py:fixed#0",
            "complexity": 3,
            "crap": 3.0,
            "language": "Python",
            "file": "a.py",
            "function": "fixed",
            "line": 2,
        },
        {
            "id": "python:a.py:worse#0",
            "complexity": 13,
            "crap": 17.0,
            "language": "Python",
            "file": "a.py",
            "function": "worse",
            "line": 3,
        },
        {
            "id": "python:a.py:new_bad#0",
            "complexity": 11,
            "crap": 12.0,
            "language": "Python",
            "file": "a.py",
            "function": "new_bad",
            "line": 4,
        },
        {
            "id": "python:a.py:new_ok#0",
            "complexity": 3,
            "crap": 3.0,
            "language": "Python",
            "file": "a.py",
            "function": "new_ok",
            "line": 5,
        },
    ]
    baseline = [
        {"id": "python:a.py:legacy#0", "complexity": 12, "crap": 14.0},
        {"id": "python:a.py:fixed#0", "complexity": 12, "crap": 14.0},
        {"id": "python:a.py:worse#0", "complexity": 12, "crap": 14.0},
        {"id": "python:a.py:gone#0", "complexity": 11, "crap": 12.0},
    ]
    result = evaluate_ratchet(current, baseline)
    _assert_ids(
        [row["id"] for row in result["legacy"]],
        ["python:a.py:legacy#0"],
        "legacy",
    )
    _assert_ids(
        [row["id"] for row in result["worsened"]],
        ["python:a.py:worse#0"],
        "worsened",
    )
    _assert_ids(
        [row["id"] for row in result["new_debt"]],
        ["python:a.py:new_bad#0"],
        "new debt",
    )
    _assert_ids(
        {row["id"] for row in result["resolved"]},
        {"python:a.py:fixed#0"},
        "resolved",
    )
    _assert_ids(
        {entry["id"] for entry in result["stale_baseline"]},
        {"python:a.py:fixed#0", "python:a.py:gone#0"},
        "stale",
    )
    if is_violating({"complexity": 3, "crap": 3.0}):
        raise AssertionError("compliant function must not violate")
    if not is_violating({"complexity": 11, "crap": None}):
        raise AssertionError("CC>10 is a violation even when CRAP is unresolved")
    if is_violating({"complexity": 10, "crap": 110.0}):
        raise AssertionError("CRAP is a regression signal, not an absolute gate")
    if is_violating({"complexity": 5, "crap": None}):
        raise AssertionError("unresolved CRAP must not fabricate a CRAP violation")
    cognitive_cases = (
        (10, 11, True, "crossing the cognitive ceiling"),
        (14, 15, True, "legacy cognitive regression"),
        (8, 9, False, "sub-threshold cognitive delta"),
        (14, 12, False, "legacy cognitive improvement"),
    )
    for old, new, expected, label in cognitive_cases:
        _assert_ids(cognitive_regression(old, new), expected, label)
    if approval_eligible(tests_ran=False, failed=False):
        raise AssertionError("skipped tests cannot be approval-eligible")
    if approval_eligible(tests_ran=True, failed=True):
        raise AssertionError("failed quality cannot be approval-eligible")
    if not approval_eligible(tests_ran=True, failed=False):
        raise AssertionError("full green quality must be approval-eligible")


def self_test() -> None:
    _self_test_crap()
    _self_test_js_coverage()
    _self_test_ratchet()
    _assert_command_output_survives_cp1252()
    result = run_command([node_executable(), str(JS_METRICS), "--self-test"], cwd=ROOT)
    if result.returncode != 0:
        raise RuntimeError(result.stderr or "JS function metric self-test failed")


def npm_executable() -> str:
    return "npm.cmd" if os.name == "nt" else "npm"


def node_executable() -> str:
    executable = shutil.which("node")
    if executable is None:
        raise RuntimeError("Node.js is required for the quality gate")
    return executable


def load_phase2():
    spec = importlib.util.spec_from_file_location(
        "phase2_quality_report", PHASE2_REPORT
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load {PHASE2_REPORT}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def run_command(
    command: list[str],
    *,
    cwd: Path,
    env: Mapping[str, str] | None = None,
    timeout: int | None = None,
) -> subprocess.CompletedProcess[str]:
    merged = os.environ.copy()
    if env:
        merged.update(env)
    # Every caller supplies an argv list for repository-owned tooling. Shell
    # execution is disabled, so user text cannot become a command string.
    return subprocess.run(  # noqa: S603
        command,
        cwd=cwd,
        env=merged,
        text=True,
        encoding="utf-8",
        capture_output=True,
        timeout=timeout,
    )


def configure_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if not callable(reconfigure):
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (OSError, ValueError):
            continue


def emit(text: str = "") -> None:
    try:
        print(text)
    except UnicodeEncodeError:
        encoding = getattr(sys.stdout, "encoding", None) or "utf-8"
        payload = f"{text}\n".encode(encoding, errors="replace")
        buffer = getattr(sys.stdout, "buffer", None)
        if buffer is not None:
            buffer.write(payload)
            return
        sys.stdout.write(payload.decode(encoding, errors="replace"))


def print_command_result(title: str, result: subprocess.CompletedProcess[str]) -> None:
    emit(f"\n== {title} (exit {result.returncode}) ==")
    output = (result.stdout or "") + (result.stderr or "")
    stripped = output.strip()
    if stripped.startswith("{") or stripped.startswith("["):
        emit(f"machine-readable diagnostics: {len(stripped)} characters")
        return
    if len(output) > 4000:
        emit(output[:1500])
        emit(f"... [{len(output) - 4000} characters omitted] ...")
        emit(output[-1500:])
        return
    emit(output)


def _assert_command_output_survives_cp1252() -> None:
    class _Cp1252:
        encoding = "cp1252"

        def write(self, text: str) -> int:
            return len(text.encode("cp1252"))

        def flush(self) -> None:
            return None

    previous = sys.stdout
    sys.stdout = _Cp1252()  # type: ignore[assignment]
    try:
        print_command_result(
            "frontend tests with V8 coverage",
            subprocess.CompletedProcess(["npm"], 0, "ok \u2714 tests\n", ""),
        )
    finally:
        sys.stdout = previous


def backend_test_env() -> dict[str, str]:
    return {
        "APP_KEY": os.environ.get("APP_KEY") or "ci-test-key",
        "ANTHROPIC_API_KEY": os.environ.get("ANTHROPIC_API_KEY")
        or "ci-test-anthropic-key",
        "SMARTROUTE_ENV": "test",
        "AGENT_ALLOW_MEMORY_SESSIONS": "1",
        "SMARTROUTE_RUN_LIVE_TESTS": "0",
        "RUN_LIVE_TESTS": "0",
        "TICKETMASTER_LIVE_SMOKE_TEST": "0",
        "RUN_ANTHROPIC_TOOL_CONTRACT": "0",
        "PYTHONPATH": str(BACKEND),
    }


def collect_python_coverage() -> subprocess.CompletedProcess[str]:
    coverage_file = BACKEND / ".coverage"
    if coverage_file.exists():
        coverage_file.unlink()
    run_command(
        [sys.executable, "-m", "coverage", "erase"],
        cwd=BACKEND,
        env=backend_test_env(),
    )
    return run_command(
        [
            sys.executable,
            "-m",
            "coverage",
            "run",
            "--branch",
            "--source=app",
            "-m",
            "pytest",
            "-q",
            "--basetemp",
            str(ROOT / ".pytest-quality"),
        ],
        cwd=BACKEND,
        env=backend_test_env(),
        timeout=1200,
    )


def collect_js_coverage(coverage_dir: Path) -> subprocess.CompletedProcess[str]:
    if coverage_dir.exists():
        shutil.rmtree(coverage_dir)
    coverage_dir.mkdir(parents=True, exist_ok=True)
    env = {
        "NODE_V8_COVERAGE": str(coverage_dir),
        "NODE_NO_WARNINGS": "1",
    }
    return run_command(
        [npm_executable(), "run", "test:unit"],
        cwd=FRONTEND,
        env=env,
        timeout=600,
    )


def python_functions() -> list[dict[str, object]]:
    phase2 = load_phase2()
    files = phase2.source_files(BACKEND / "app")
    coverage_file = BACKEND / ".coverage"
    if not coverage_file.exists():
        raise RuntimeError("Python coverage file backend/.coverage is missing")
    rows = []
    for row in phase2.measured_functions(files, BACKEND, coverage_file):
        coverage = float(row["coverage"])
        statement_count = int(row["statement_count"])
        status = "uncovered" if statement_count > 0 and coverage == 0.0 else "measured"
        rows.append(
            {
                "file": f"backend/{row['file']}",
                "function": row["function"],
                "language": "Python",
                "line": row["line"],
                "lines": row["loc"],
                "complexity": int(row["complexity"]),
                "coverage": coverage,
                "crap": float(row["crap"]),
                "coverage_status": status,
                "unresolved_reason": None,
                "cc_source": "radon",
            }
        )
    return assign_identities(rows)


def cognitive_regression(
    old_complexity: int | None,
    new_complexity: int | None,
) -> bool:
    if new_complexity is None or new_complexity <= MAX_COGNITIVE_COMPLEXITY:
        return False
    return old_complexity is None or new_complexity > old_complexity


def cognitive_complexity(git_ref: str) -> dict[str, object]:
    resolved_ref = run_command(
        ["git", "rev-parse", "--verify", f"{git_ref}^{{commit}}"],
        cwd=ROOT,
        timeout=30,
    )
    if resolved_ref.returncode != 0:
        raise RuntimeError(f"Cannot resolve cognitive-complexity reference {git_ref!r}")

    phase2 = load_phase2()
    analyses = [
        file_complexity(str(path), no_ignore=True)
        for path in phase2.source_files(BACKEND / "app")
    ]
    functions = [
        {
            "file": analysis.path.replace("\\", "/"),
            "function": function.name,
            "line": function.line_start,
            "lines": function.line_end - function.line_start + 1,
            "complexity": function.complexity,
        }
        for analysis in analyses
        for function in analysis.functions
    ]
    compared = compute_diff(analyses, git_ref, str(ROOT))
    regressions = [
        {
            "file": entry.file_path.replace("\\", "/"),
            "function": entry.func_name,
            "old_complexity": entry.old_complexity,
            "new_complexity": entry.new_complexity,
            "status": entry.status.value,
        }
        for entry in compared
        if cognitive_regression(entry.old_complexity, entry.new_complexity)
    ]
    return {
        "reference": resolved_ref.stdout.strip(),
        "functions": functions,
        "above_threshold": [
            row
            for row in functions
            if int(row["complexity"]) > MAX_COGNITIVE_COMPLEXITY
        ],
        "regressions": regressions,
    }


def normalize_v8_path(url: str) -> str | None:
    parsed = urlparse(url)
    raw_path = unquote(parsed.path)
    if "?" in raw_path:
        raw_path = raw_path.split("?", 1)[0]
    if raw_path.startswith("/") and len(raw_path) > 2 and raw_path[2] == ":":
        raw_path = raw_path[1:]
    try:
        resolved = Path(raw_path).resolve()
        return resolved.relative_to(FRONTEND.resolve()).as_posix()
    except (OSError, ValueError):
        return None


def load_v8_coverage(coverage_dir: Path) -> dict[str, object]:
    executed: set[str] = set()
    unique_coverages: dict[tuple[str, str], list[float]] = defaultdict(list)
    collided: set[tuple[str, str]] = set()
    if not coverage_dir.exists():
        return {
            "executed": executed,
            "unique_coverages": unique_coverages,
            "collided": collided,
        }
    for path in coverage_dir.glob("*.json"):
        payload = json.loads(path.read_text(encoding="utf-8"))
        for script in payload.get("result", []):
            relative = normalize_v8_path(str(script.get("url", "")))
            if relative is None:
                continue
            executed.add(relative)
            by_name: dict[str, list[dict[str, object]]] = defaultdict(list)
            for function in script.get("functions", []):
                name = str(function.get("functionName") or "").strip()
                if name.startswith("__"):
                    continue
                ranges = list(function.get("ranges") or [])
                start = int(ranges[0]["startOffset"]) if ranges else 0
                end = int(ranges[0]["endOffset"]) if ranges else 0
                by_name[name].append(
                    {
                        "start": start,
                        "end": end,
                        "span": max(0, end - start),
                        "coverage": v8_block_coverage(ranges),
                    }
                )
            for name, entries in by_name.items():
                match_count, coverage = collapse_v8_entries(entries)
                key = (relative, name)
                if match_count == 1 and coverage is not None:
                    unique_coverages[key].append(coverage)
                elif match_count > 1:
                    collided.add(key)
    return {
        "executed": executed,
        "unique_coverages": unique_coverages,
        "collided": collided,
    }


def lookup_v8_coverage(
    file_name: str,
    names: list[str],
    unique_coverages: Mapping[tuple[str, str], list[float]],
    collided: set[tuple[str, str]],
) -> tuple[int, float | None]:
    coverages: list[float] = []
    saw_collision = False
    for name in names:
        key = (file_name, name)
        coverages.extend(unique_coverages.get(key, []))
        if key in collided:
            saw_collision = True
    if coverages:
        return 1, max(coverages)
    if saw_collision:
        return 2, None
    return 0, None


def js_coverage_mapping(
    row: Mapping[str, object],
    *,
    executed: set[str],
    unique_coverages: Mapping[tuple[str, str], list[float]],
    collided: set[tuple[str, str]],
    anonymous_counts: Mapping[str, int],
) -> tuple[str, float | None, str]:
    file_name = str(row["file"])
    function_name = str(row["function"])
    anonymous = bool(row.get("anonymous") or function_name == "anonymous")
    if anonymous and anonymous_counts[file_name] != 1:
        match_count, coverage = 2, None
    else:
        names = ["", "(anonymous)"] if anonymous else [function_name]
        if not anonymous and "." in function_name:
            names.append(function_name.rsplit(".", 1)[-1])
        match_count, coverage = lookup_v8_coverage(
            file_name,
            names,
            unique_coverages,
            collided,
        )
    return classify_js_coverage(
        anonymous=anonymous,
        file_executed=file_name in executed,
        match_count=match_count,
        coverage=coverage,
    )


def js_functions(coverage_dir: Path) -> list[dict[str, object]]:
    result = run_command(["node", str(JS_METRICS)], cwd=ROOT, timeout=120)
    if result.returncode != 0:
        print_command_result("JS function inventory", result)
        raise RuntimeError("JS function inventory failed")
    inventory = json.loads(result.stdout)
    v8 = load_v8_coverage(coverage_dir)
    executed: set[str] = v8["executed"]
    unique_coverages: dict[tuple[str, str], list[float]] = v8["unique_coverages"]
    collided: set[tuple[str, str]] = v8["collided"]
    anonymous_counts: dict[str, int] = defaultdict(int)
    for row in inventory["functions"]:
        if row.get("anonymous") or row["function"] == "anonymous":
            anonymous_counts[str(row["file"])] += 1

    rows = []
    for row in inventory["functions"]:
        file_name = str(row["file"])
        function_name = str(row["function"])
        status, mapped_coverage, reason = js_coverage_mapping(
            row,
            executed=executed,
            unique_coverages=unique_coverages,
            collided=collided,
            anonymous_counts=anonymous_counts,
        )
        complexity = int(row["complexity"])
        crap = None
        if mapped_coverage is not None:
            crap = crap_score(complexity, mapped_coverage)
        rows.append(
            {
                "file": f"frontend/{file_name}",
                "function": function_name,
                "language": "TypeScript",
                "line": int(row["line"]),
                "lines": int(row["lines"]),
                "complexity": complexity,
                "coverage": mapped_coverage,
                "crap": crap,
                "coverage_status": status,
                "unresolved_reason": None if status != "unresolved" else reason,
                "cc_source": "eslint-classic",
            }
        )
    return assign_identities(rows)


def parse_json_output(text: str) -> object:
    stripped = text.strip()
    if not stripped:
        raise json.JSONDecodeError("empty", text, 0)
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        pass
    for opener, closer in (("[", "]"), ("{", "}")):
        start = stripped.find(opener)
        end = stripped.rfind(closer)
        if start != -1 and end > start:
            try:
                return json.loads(stripped[start : end + 1])
            except json.JSONDecodeError:
                continue
    raise json.JSONDecodeError("no json", text, 0)


def parse_ruff_structural(output: str) -> list[dict[str, object]]:
    if not output.strip():
        return []
    try:
        payload = parse_json_output(output)
    except json.JSONDecodeError:
        return []
    rows = []
    for item in payload:
        code = item.get("code")
        if code not in {"C901", "PLR0912", "PLR0915"}:
            continue
        message = str(item.get("message", ""))
        matched = RUFF_COMPLEXITY_IN_MESSAGE.search(message) if code == "C901" else None
        measured = re.search(r"\((?P<actual>\d+) > (?P<limit>\d+)\)", message)
        filename = str(item.get("filename") or item.get("file") or "")
        try:
            relative = Path(filename).resolve().relative_to(ROOT).as_posix()
        except ValueError:
            relative = filename.replace("\\", "/")
        rows.append(
            {
                "code": code,
                "file": relative,
                "function": matched.group("name") if matched else message,
                "actual": (
                    int(matched.group("cc"))
                    if matched
                    else int(measured.group("actual"))
                    if measured
                    else None
                ),
                "limit": (
                    MAX_COMPLEXITY
                    if code == "C901"
                    else int(measured.group("limit"))
                    if measured
                    else None
                ),
                "linter": "ruff",
                "line": (item.get("location") or {}).get("row"),
            }
        )
    return rows


def parse_eslint_complexity(output: str) -> list[dict[str, object]]:
    if not output.strip():
        return []
    try:
        payload = parse_json_output(output)
    except json.JSONDecodeError:
        return []
    rows = []
    for file_result in payload:
        filename = str(file_result.get("filePath", ""))
        try:
            relative = Path(filename).resolve().relative_to(ROOT).as_posix()
        except ValueError:
            relative = filename.replace("\\", "/")
        for message in file_result.get("messages", []):
            if message.get("ruleId") != "complexity":
                continue
            text = str(message.get("message", ""))
            cc_match = COMPLEXITY_IN_MESSAGE.search(text)
            name_match = ESLINT_FUNCTION_IN_MESSAGE.search(text)
            rows.append(
                {
                    "file": relative,
                    "function": name_match.group(1) if name_match else text,
                    "complexity": int(cc_match.group(1)) if cc_match else None,
                    "linter": "eslint",
                    "line": message.get("line"),
                }
            )
    return rows


def parse_oxlint_complexity(output: str) -> list[dict[str, object]]:
    if not output.strip():
        return []
    try:
        payload = parse_json_output(output)
    except json.JSONDecodeError:
        return []
    diagnostics = payload
    if isinstance(payload, dict):
        diagnostics = payload.get("diagnostics") or payload.get("lint") or []
    rows = []
    for item in diagnostics:
        code = str(
            item.get("code")
            or item.get("rule")
            or (
                (item.get("ruleId") or item.get("rule_id"))
                if isinstance(item, dict)
                else ""
            )
            or ""
        )
        if "complexity" not in code.lower():
            continue
        filename = str(
            item.get("filename") or item.get("filePath") or item.get("file") or ""
        )
        try:
            relative = Path(filename).resolve().relative_to(ROOT).as_posix()
        except ValueError:
            relative = filename.replace("\\", "/")
        message = str(item.get("message") or item.get("help") or "")
        cc_match = COMPLEXITY_IN_MESSAGE.search(message)
        name_match = re.search(r"function `([^`]+)`", message, re.IGNORECASE)
        label = item.get("labels") or item.get("label") or []
        line = None
        if isinstance(label, list) and label:
            span = label[0].get("span") if isinstance(label[0], dict) else {}
            line = (span or {}).get("line") or (span or {}).get("start", {}).get("line")
        if relative and not relative.startswith("frontend/"):
            relative = f"frontend/{relative}"
        rows.append(
            {
                "file": relative,
                "function": name_match.group(1)
                if name_match
                else (message or "anonymous"),
                "complexity": int(cc_match.group(1)) if cc_match else None,
                "linter": "oxlint",
                "line": line or item.get("line"),
            }
        )
    return rows


def run_complexity_linters() -> dict[str, object]:
    ruff_structural = run_command(
        [
            sys.executable,
            "-m",
            "ruff",
            "check",
            "--config",
            str(ROOT / "pyproject.toml"),
            "--select",
            "C901,PLR0912,PLR0915",
            "--output-format",
            "json",
            "backend",
        ],
        cwd=ROOT,
        timeout=180,
    )
    ruff_diagnostics = parse_ruff_structural(ruff_structural.stdout)
    eslint = run_command(
        ["node", "node_modules/eslint/bin/eslint.js", ".", "--format", "json"],
        cwd=FRONTEND,
        timeout=300,
    )
    oxlint = run_command(
        [
            "node",
            "--import",
            "tsx",
            "node_modules/oxlint/bin/oxlint",
            "--deny-warnings",
            "--format",
            "json",
            ".",
        ],
        cwd=FRONTEND,
        timeout=180,
    )
    return {
        "ruff_structural_result": ruff_structural,
        "eslint": eslint,
        "oxlint": oxlint,
        "ruff_structural": ruff_diagnostics,
        "ruff_complexity": [
            row for row in ruff_diagnostics if row.get("code") == "C901"
        ],
        "eslint_complexity": parse_eslint_complexity(eslint.stdout),
        "oxlint_complexity": parse_oxlint_complexity(oxlint.stdout or oxlint.stderr),
    }


def load_baseline(path: Path) -> list[dict[str, object]] | None:
    if not path.exists():
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    entries = payload.get("entries")
    if not isinstance(entries, list):
        raise TypeError(f"{path} is missing an entries array")
    return entries


def write_baseline(path: Path, entries: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": BASELINE_VERSION,
        "max_cyclomatic_complexity": MAX_COMPLEXITY,
        "max_cognitive_complexity": MAX_COGNITIVE_COMPLEXITY,
        "formula": "CC^2 * (1 - coverage)^3 + CC",
        "python_cc_source": "radon",
        "python_lint_source": "ruff C901, PLR0912, PLR0915",
        "python_cognitive_source": "complexipy",
        "crap_policy": "regression signal for baseline entries; no absolute ceiling",
        "identity": "language:file:qualified_name#source_order",
        "entries": entries,
    }
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def format_violation(row: Mapping[str, object]) -> str:
    coverage = row.get("coverage")
    coverage_text = "unresolved" if coverage is None else f"{float(coverage):.0%}"
    crap = row.get("crap")
    crap_text = "n/a" if crap is None else f"{float(crap):.2f}"
    return "\n".join(
        [
            f"file: {row['file']}",
            f"function: {row['function']}",
            f"id: {row.get('id')}",
            f"language: {row['language']}",
            f"lines: {row.get('lines')}",
            f"cyclomatic complexity: {row['complexity']}",
            f"coverage status: {row.get('coverage_status')}",
            f"function coverage: {coverage_text}",
            f"CRAP: {crap_text}",
            "violation: cyclomatic complexity",
        ]
    )


def collect_measurements(skip_tests: bool) -> tuple[list[dict[str, object]], list[str]]:
    python_rows: list[dict[str, object]] = []
    js_rows: list[dict[str, object]] = []
    test_failures: list[str] = []
    coverage_dir = FRONTEND / "coverage" / "v8"
    if not skip_tests:
        print("Collecting frontend coverage via npm run test:unit")
        js_tests = collect_js_coverage(coverage_dir)
        print_command_result("frontend tests with V8 coverage", js_tests)
        if js_tests.returncode != 0:
            test_failures.append("frontend tests")
        print("Collecting backend coverage via coverage.py + pytest")
        py_tests = collect_python_coverage()
        print_command_result("backend tests with coverage.py", py_tests)
        if py_tests.returncode != 0:
            test_failures.append("backend tests")
    if test_failures:
        return [], test_failures
    python_rows = python_functions()
    js_rows = js_functions(coverage_dir)
    return python_rows + js_rows, test_failures


def summarize(rows: list[dict[str, object]]) -> dict[str, int]:
    unresolved = [row for row in rows if row.get("coverage_status") == "unresolved"]
    uncovered = [row for row in rows if row.get("coverage_status") == "uncovered"]
    measured = [row for row in rows if row.get("coverage_status") == "measured"]
    cc_violations = [row for row in rows if int(row["complexity"]) > MAX_COMPLEXITY]
    return {
        "total_functions": len(rows),
        "measured": len(measured),
        "uncovered": len(uncovered),
        "unresolved": len(unresolved),
        "cc_violations": len(cc_violations),
    }


def size_signals(rows: list[dict[str, object]]) -> dict[str, list[dict[str, object]]]:
    long_functions = [
        row for row in rows if int(row.get("lines") or 0) > FUNCTION_LENGTH_REVIEW
    ]
    long_files = []
    for file_name in sorted({str(row["file"]) for row in rows}):
        path = ROOT / file_name
        if not path.is_file():
            continue
        line_count = len(path.read_text(encoding="utf-8").splitlines())
        if line_count > FILE_LENGTH_REVIEW:
            long_files.append({"file": file_name, "lines": line_count})
    return {
        "functions": long_functions,
        "files": long_files,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument(
        "--skip-tests",
        action="store_true",
        help="Reuse coverage already produced in this working tree (debug only; approval_eligible: false; exits 1)",
    )
    parser.add_argument(
        "--update-baseline",
        action="store_true",
        help="Shrink quality/baseline.json to remaining violating functions",
    )
    parser.add_argument("--output", help="Optional JSON report path")
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Print every current violating function, including legacy debt",
    )
    parser.add_argument(
        "--quality-ref",
        default="HEAD",
        help="Immutable Git point for cognitive-complexity delta checks",
    )
    parser.add_argument(
        "--cognitive-only",
        action="store_true",
        help="Run the complexipy delta without collecting test coverage (prints approval_eligible: false)",
    )
    return parser.parse_args()


def update_quality_baseline(
    rows: list[dict[str, object]],
    previous: list[dict[str, object]] | None,
) -> list[dict[str, object]]:
    if previous is None:
        entries = baseline_entries_from_rows(rows)
        action = "bootstrap"
    else:
        entries = shrink_baseline(rows, previous)
        action = f"shrunk from {len(previous)} entries"
    write_baseline(BASELINE_PATH, entries)
    print(f"Wrote {action} baseline with {len(entries)} entries to {BASELINE_PATH}")
    return entries


def print_quality_summary(
    stats: Mapping[str, int],
    cognitive: Mapping[str, object],
    sizes: Mapping[str, list[dict[str, object]]],
    ratchet: Mapping[str, object],
    lint_results: Mapping[str, object],
) -> None:
    print("\n== Quality gates ==")
    print(f"max cyclomatic complexity for new functions: {MAX_COMPLEXITY}")
    print(f"max cognitive complexity for new functions: {MAX_COGNITIVE_COMPLEXITY}")
    print("formula: CRAP(m) = CC(m)^2 * (1 - coverage(m))^3 + CC(m)")
    print("CRAP policy: no absolute ceiling; baseline entries may not worsen")
    print("python CC source: radon (CRAP); ruff C901 (raw lint)")
    print("python cognitive source: complexipy")
    print(f"cognitive comparison: {cognitive['reference']}")
    print(f"total functions: {stats['total_functions']}")
    print(f"measured: {stats['measured']}")
    print(f"uncovered: {stats['uncovered']}")
    print(f"unresolved coverage mappings: {stats['unresolved']}")
    print(f"CC violations: {stats['cc_violations']}")
    print(f"cognitive violations: {len(cognitive['above_threshold'])}")
    print(f"new or worsened cognitive violations: {len(cognitive['regressions'])}")
    print(f"functions over {FUNCTION_LENGTH_REVIEW} lines: {len(sizes['functions'])}")
    print(f"files over {FILE_LENGTH_REVIEW} lines: {len(sizes['files'])}")
    print(f"baseline violations remaining: {len(ratchet['legacy'])}")
    print(f"new violations: {len(ratchet['new_debt'])}")
    print(f"worsened violations: {len(ratchet['worsened'])}")
    print(f"resolved violations: {len(ratchet['resolved'])}")
    print(f"stale baseline entries: {len(ratchet['stale_baseline'])}")
    print(f"Ruff C901 diagnostics: {len(lint_results['ruff_complexity'])}")
    print(f"Ruff structural diagnostics: {len(lint_results['ruff_structural'])}")
    print(f"ESLint complexity diagnostics: {len(lint_results['eslint_complexity'])}")
    print(f"Oxlint complexity diagnostics: {len(lint_results['oxlint_complexity'])}")
    print(
        "Raw ESLint/Oxlint/Ruff commands still fail on the full backlog. "
        "This command applies the baseline ratchet and does not use those exit codes."
    )


def print_cognitive_regressions(cognitive: Mapping[str, object]) -> None:
    regressions = cognitive["regressions"]
    if not regressions:
        return
    print("\n== New or worsened cognitive complexity ==")
    for row in regressions:
        print(
            f"{row['file']} {row['function']} "
            f"{row['old_complexity']} -> {row['new_complexity']}"
        )


def print_cognitive_summary(cognitive: Mapping[str, object]) -> None:
    print(f"cognitive comparison: {cognitive['reference']}")
    print(f"functions analyzed: {len(cognitive['functions'])}")
    print(
        f"functions above {MAX_COGNITIVE_COMPLEXITY}: {len(cognitive['above_threshold'])}"
    )
    print(f"new or worsened violations: {len(cognitive['regressions'])}")
    print_cognitive_regressions(cognitive)


def print_unresolved_coverage(unresolved_rows: list[dict[str, object]]) -> None:
    if not unresolved_rows:
        return
    print("\n== Unresolved coverage mappings ==")
    for row in unresolved_rows[:50]:
        print(f"{row['file']} {row['function']} reason={row.get('unresolved_reason')}")
    if len(unresolved_rows) > 50:
        print(f"... {len(unresolved_rows) - 50} more unresolved mappings")


def print_ratchet_details(ratchet: Mapping[str, object]) -> None:
    if ratchet["missing_baseline"]:
        print(
            "\nquality/baseline.json is missing. Snapshot current debt with --update-baseline."
        )
    for title, bucket in (
        ("New debt", ratchet["new_debt"]),
        ("Worsened debt", ratchet["worsened"]),
    ):
        if not bucket:
            continue
        print(f"\n== {title} ==")
        for row in bucket:
            print()
            print(format_violation(row))
    stale = ratchet["stale_baseline"]
    if stale:
        print("\n== Baseline entries that must be removed ==")
        for entry in stale[:50]:
            print(entry["id"])
        if len(stale) > 50:
            print(f"... {len(stale) - 50} more stale entries")
        print(
            "Run python scripts/check_quality.py --update-baseline to shrink the ratchet."
        )


def print_verbose_violations(rows: list[dict[str, object]], *, verbose: bool) -> None:
    if not verbose:
        return
    print("\n== All current violating functions ==")
    violating = sorted(
        [item for item in rows if is_violating(item)],
        key=lambda item: (
            -float(item["crap"] or 0),
            str(item["file"]),
            str(item["function"]),
        ),
    )
    for row in violating:
        print()
        print(format_violation(row))


def print_quality_details(
    rows: list[dict[str, object]],
    cognitive: Mapping[str, object],
    unresolved_rows: list[dict[str, object]],
    ratchet: Mapping[str, object],
    *,
    verbose: bool,
) -> None:
    print_cognitive_regressions(cognitive)
    print_unresolved_coverage(unresolved_rows)
    print_ratchet_details(ratchet)
    print_verbose_violations(rows, verbose=verbose)


def quality_report(
    rows: list[dict[str, object]],
    stats: Mapping[str, int],
    sizes: Mapping[str, list[dict[str, object]]],
    cognitive: Mapping[str, object],
    unresolved_rows: list[dict[str, object]],
    ratchet: Mapping[str, object],
    lint_results: Mapping[str, object],
) -> dict[str, object]:
    return {
        "max_cyclomatic_complexity": MAX_COMPLEXITY,
        "max_cognitive_complexity": MAX_COGNITIVE_COMPLEXITY,
        "formula": "CC^2 * (1 - coverage)^3 + CC",
        "crap_policy": "regression signal for baseline entries; no absolute ceiling",
        "python_cc_source": "radon",
        "python_lint_source": "ruff C901, PLR0912, PLR0915",
        "python_cognitive_source": "complexipy",
        "summary": stats,
        "ratchet": {
            "missing_baseline": ratchet["missing_baseline"],
            "legacy": len(ratchet["legacy"]),
            "new_debt": len(ratchet["new_debt"]),
            "worsened": len(ratchet["worsened"]),
            "resolved": len(ratchet["resolved"]),
            "stale_baseline": len(ratchet["stale_baseline"]),
        },
        "new_debt": ratchet["new_debt"],
        "worsened": ratchet["worsened"],
        "resolved": ratchet["resolved"],
        "legacy": ratchet["legacy"],
        "stale_baseline": ratchet["stale_baseline"],
        "unresolved": unresolved_rows,
        "cognitive": cognitive,
        "size_signals": sizes,
        "functions": rows,
        "linter_complexity": (
            list(lint_results["ruff_structural"])
            + list(lint_results["eslint_complexity"])
            + list(lint_results["oxlint_complexity"])
        ),
    }


def write_report(report: Mapping[str, object], output: str | None) -> None:
    if not output:
        return
    output_path = Path(output)
    if not output_path.is_absolute():
        output_path = ROOT / output_path
    output_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(f"\nWrote {output_path}")


def quality_failed(
    ratchet: Mapping[str, object],
    cognitive: Mapping[str, object],
) -> bool:
    return bool(
        ratchet["missing_baseline"]
        or ratchet["stale_baseline"]
        or ratchet["new_debt"]
        or ratchet["worsened"]
        or cognitive["regressions"]
    )


def approval_eligible(*, tests_ran: bool, failed: bool) -> bool:
    return tests_ran and not failed


def print_approval_status(*, tests_ran: bool, failed: bool) -> bool:
    eligible = approval_eligible(tests_ran=tests_ran, failed=failed)
    print(f"approval_eligible: {str(eligible).lower()}")
    print(f"tests_ran: {str(tests_ran).lower()}")
    return eligible


def main() -> int:
    args = parse_args()
    configure_stdio()

    if args.self_test:
        self_test()
        print("self-test passed")
        return 0

    self_test()
    print("self-test passed")

    try:
        cognitive = cognitive_complexity(args.quality_ref)
    except (OSError, RuntimeError, SyntaxError) as exc:
        print(f"cognitive-complexity analysis failed: {exc}")
        return 1
    if args.cognitive_only:
        print_cognitive_summary(cognitive)
        print_approval_status(
            tests_ran=False, failed=bool(cognitive["regressions"])
        )
        return 1 if cognitive["regressions"] else 0

    rows, test_failures = collect_measurements(args.skip_tests)
    if test_failures:
        print("quality failed because required tests did not pass")
        print_approval_status(tests_ran=True, failed=True)
        return 1

    stats = summarize(rows)
    sizes = size_signals(rows)
    unresolved_rows = [
        row for row in rows if row.get("coverage_status") == "unresolved"
    ]
    previous = load_baseline(BASELINE_PATH)
    if args.update_baseline:
        previous = update_quality_baseline(rows, previous)

    ratchet = evaluate_ratchet(rows, previous)
    lint_results = run_complexity_linters()
    print_quality_summary(stats, cognitive, sizes, ratchet, lint_results)
    print_quality_details(
        rows,
        cognitive,
        unresolved_rows,
        ratchet,
        verbose=args.verbose,
    )
    report = quality_report(
        rows,
        stats,
        sizes,
        cognitive,
        unresolved_rows,
        ratchet,
        lint_results,
    )
    failed = quality_failed(ratchet, cognitive)
    tests_ran = not args.skip_tests
    eligible = print_approval_status(tests_ran=tests_ran, failed=failed)
    report["approval_eligible"] = eligible
    report["tests_ran"] = tests_ran
    report["quality_ref"] = args.quality_ref
    write_report(report, args.output)
    return 0 if eligible else 1


if __name__ == "__main__":
    sys.exit(main())
