"""Frontend production coverage, complexity, and growth report.

One file because the report, self-test, and CLI share one contract.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import shutil
import subprocess
import sys
from collections import defaultdict
from collections.abc import Mapping, Sequence
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FRONTEND = ROOT / "frontend"
SCOPE_PATH = ROOT / "scripts" / "frontend_quality_scope.json"
JS_METRICS = ROOT / "scripts" / "js_function_metrics.mjs"
CHECK_QUALITY = ROOT / "scripts" / "check_quality.py"
DEFAULT_COVERAGE = FRONTEND / "coverage" / "coverage-final.json"
DEFAULT_V8 = FRONTEND / "coverage" / "v8"
DEFAULT_OUTPUT = ROOT / ".audit" / "frontend-debt.json"
UNIT_RUNNER = FRONTEND / "tools" / "run-unit-tests.mjs"
SURVIVOR_BAND = (11, 12)
HIGH_CRAP = 30
TARGET_COVERAGE = 0.95
PRODUCTION_DIFF_PATHS = (
    "frontend/app",
    "frontend/lib",
    "frontend/components",
    "frontend/scripts",
)


def load_json(path: Path) -> object:
    return json.loads(path.read_text(encoding="utf-8"))


def load_scope() -> dict[str, object]:
    payload = load_json(SCOPE_PATH)
    if not isinstance(payload, dict):
        raise TypeError(f"{SCOPE_PATH} must contain an object")
    return payload


def load_check_quality():
    spec = importlib.util.spec_from_file_location("check_quality", CHECK_QUALITY)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load {CHECK_QUALITY}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def posix(relative: str) -> str:
    return relative.replace("\\", "/")


def batch_for(relative: str, scope: Mapping[str, object]) -> str:
    path = posix(relative)
    match = max(
        (
            str(rule["prefix"])
            for rule in scope["batchRules"]
            if isinstance(rule, dict) and path.startswith(str(rule["prefix"]))
        ),
        key=len,
        default=None,
    )
    if match is None:
        return "unassigned"
    for rule in scope["batchRules"]:
        if isinstance(rule, dict) and str(rule["prefix"]) == match:
            return str(rule["batch"])
    return "unassigned"


def skipped_relative(relative: str, scope: Mapping[str, object]) -> bool:
    path = posix(relative)
    parts = path.split("/")
    skip = {str(name) for name in scope["skipDirNames"]}
    if any(part in skip for part in parts) or parts[-1].endswith(".d.ts"):
        return True
    for prefix in scope.get("skipPathPrefixes") or []:
        trimmed = str(prefix).rstrip("/")
        if path == trimmed or path.startswith(f"{trimmed}/"):
            return True
    return False


def classify_relative(relative: str, scope: Mapping[str, object]) -> dict[str, str]:
    path = posix(relative)
    parts = path.split("/")
    base = parts[-1]
    if skipped_relative(path, scope):
        return {"role": "excluded", "batch": "unassigned"}
    test_file = ".test." in base or ".spec." in base or "tests" in parts
    tool_file = ".check." in base or path.startswith("tools/")
    if test_file:
        role = "test"
    elif tool_file:
        role = "tool"
    elif any(path.startswith(f"{root}/") for root in scope["productionRoots"]):
        role = "production"
    else:
        role = "excluded"
    return {"role": role, "batch": batch_for(path, scope)}


def in_coverage_denominator(relative: str, scope: Mapping[str, object]) -> bool:
    return classify_relative(relative, scope)["role"] == "production"


def run_tool(
    executable: str,
    args: Sequence[str],
    *,
    cwd: Path,
    input_text: str | None = None,
) -> subprocess.CompletedProcess[str]:
    resolved = shutil.which(executable)
    if resolved is None:
        raise RuntimeError(f"{executable} is required")
    return subprocess.run(  # noqa: S603
        [resolved, *args],
        cwd=cwd,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        input=input_text,
    )


def git_head() -> str:
    return (run_tool("git", ["rev-parse", "HEAD"], cwd=ROOT).stdout or "").strip()


def resolve_git_ref(ref: str) -> str:
    result = run_tool("git", ["rev-parse", "--verify", f"{ref}^{{commit}}"], cwd=ROOT)
    sha = (result.stdout or "").strip()
    if result.returncode != 0 or not sha:
        raise RuntimeError(result.stderr or f"cannot resolve git ref {ref}")
    return sha


def report_identity(*, quality_ref: str, current_head: str | None = None) -> dict[str, str]:
    return {
        "fixed_point": resolve_git_ref(quality_ref),
        "current_head": current_head if current_head is not None else git_head(),
    }


def type_only_files(inventory: Mapping[str, object]) -> list[str]:
    return [
        str(item["file"])
        for item in inventory.get("files", [])
        if isinstance(item, dict) and item.get("runtime") is False
    ]


def run_js_inventory(kind: str) -> dict[str, object]:
    args = [str(JS_METRICS)]
    if kind == "authored":
        args.append("--authored")
    result = run_tool("node", args, cwd=ROOT)
    if result.returncode != 0:
        raise RuntimeError(result.stderr or "JS function inventory failed")
    payload = json.loads(result.stdout)
    if not isinstance(payload, dict):
        raise TypeError("JS inventory must be an object")
    return payload


def line_hits(record: Mapping[str, object]) -> tuple[int, int]:
    statement_map = record.get("statementMap") or {}
    statements = record.get("s") or {}
    lines: dict[int, int] = {}
    if isinstance(statement_map, dict) and isinstance(statements, dict):
        for key, loc in statement_map.items():
            if not isinstance(loc, dict):
                continue
            start = loc.get("start") if isinstance(loc.get("start"), dict) else {}
            line = start.get("line") if isinstance(start, dict) else None
            if not isinstance(line, int):
                continue
            hit = int(statements.get(key) or 0)
            lines[line] = max(lines.get(line, 0), hit)
    if lines:
        return len(lines), sum(1 for hit in lines.values() if hit)
    if isinstance(statements, dict):
        return len(statements), sum(1 for hit in statements.values() if hit)
    return 0, 0


def branch_hits(record: Mapping[str, object]) -> tuple[int, int]:
    branches = record.get("b") or {}
    total = 0
    hit = 0
    if isinstance(branches, dict):
        for counts in branches.values():
            if not isinstance(counts, list):
                continue
            total += len(counts)
            hit += sum(1 for count in counts if count)
    return total, hit


def coverage_index(coverage_file: Path) -> dict[str, dict[str, object]]:
    if not coverage_file.is_file():
        return {}
    payload = load_json(coverage_file)
    if not isinstance(payload, dict):
        raise TypeError(f"{coverage_file} must contain an object")
    indexed: dict[str, dict[str, object]] = {}
    frontend = FRONTEND.resolve()
    for record in payload.values():
        if not isinstance(record, dict):
            continue
        raw = Path(str(record.get("path") or ""))
        try:
            relative = raw.resolve().relative_to(frontend).as_posix()
        except (OSError, ValueError):
            continue
        indexed[relative] = record
    return indexed


def decorate_functions(
    functions: Sequence[Mapping[str, object]],
    *,
    v8: Mapping[str, object],
    quality,
) -> list[dict[str, object]]:
    executed: set[str] = v8["executed"]
    unique_coverages = v8["unique_coverages"]
    collided: set[tuple[str, str]] = v8["collided"]
    anonymous_counts: dict[str, int] = defaultdict(int)
    for row in functions:
        if row.get("anonymous") or row["function"] == "anonymous":
            anonymous_counts[str(row["file"])] += 1
    decorated = []
    for row in functions:
        status, mapped, reason = quality.js_coverage_mapping(
            row,
            executed=executed,
            unique_coverages=unique_coverages,
            collided=collided,
            anonymous_counts=anonymous_counts,
        )
        complexity = int(row["complexity"])
        crap = None
        if mapped is not None:
            crap = quality.crap_score(complexity, mapped)
        decorated.append(
            {
                "file": f"frontend/{row['file']}",
                "function": row["function"],
                "language": "TypeScript",
                "line": int(row["line"]),
                "complexity": complexity,
                "role": row.get("role"),
                "batch": row.get("batch") or "unassigned",
                "coverage": mapped,
                "crap": None if crap is None else round(float(crap), 3),
                "coverage_status": status,
                "anonymous": bool(row.get("anonymous") or row["function"] == "anonymous"),
                "unresolved_reason": None if status != "unresolved" else reason,
            }
        )
    return quality.assign_identities(decorated)


def static_branch_total(rows: Sequence[Mapping[str, object]]) -> int:
    return sum(max(0, int(row["complexity"]) - 1) for row in rows)


def coverage_metrics(
    production_files: Sequence[str],
    records: Mapping[str, Mapping[str, object]],
    *,
    production_rows: Sequence[Mapping[str, object]] = (),
    type_only_files: Sequence[str] = (),
) -> dict[str, object]:
    by_file: dict[str, list[Mapping[str, object]]] = defaultdict(list)
    for row in production_rows:
        by_file[posix(str(row["file"])).removeprefix("frontend/")].append(row)
    type_only = {
        posix(name).removeprefix("frontend/") for name in type_only_files
    }
    line_total = line_hit = 0
    branch_total = branch_hit = 0
    unexecuted = []
    for relative in production_files:
        if relative in type_only:
            continue
        record = records.get(relative)
        file_line_total, file_line_hit = line_hits(record or {})
        if record is None:
            source = FRONTEND / relative
            file_line_total = (
                len(source.read_text(encoding="utf-8").splitlines())
                if source.is_file()
                else 0
            )
            file_line_hit = 0
        if file_line_hit == 0:
            file_branch_total = static_branch_total(by_file[relative])
            file_branch_hit = 0
            unexecuted.append(f"frontend/{relative}")
        else:
            file_branch_total, file_branch_hit = branch_hits(record or {})
        line_total += file_line_total
        line_hit += file_line_hit
        branch_total += file_branch_total
        branch_hit += file_branch_hit
    counted = {
        "line_coverage": 0.0 if line_total == 0 else line_hit / line_total,
        "line_total": line_total,
        "line_hit": line_hit,
        "branch_total": branch_total,
        "branch_hit": branch_hit,
        "production_files": len(production_files),
        "unexecuted_files": unexecuted,
    }
    return {**counted, **resolve_branch_gate(counted)}


def function_metrics(rows: Sequence[Mapping[str, object]]) -> dict[str, int]:
    measured = sum(1 for row in rows if row["coverage_status"] == "measured")
    uncovered = sum(1 for row in rows if row["coverage_status"] == "uncovered")
    unresolved_rows = [
        row for row in rows if row["coverage_status"] == "unresolved"
    ]
    anonymous_unresolved = sum(
        1
        for row in unresolved_rows
        if row.get("anonymous") or row.get("function") == "anonymous"
    )
    return {
        "functions": len(rows),
        "measured": measured,
        "uncovered": uncovered,
        "unresolved": len(unresolved_rows),
        "anonymous_unresolved": anonymous_unresolved,
        "named_unresolved": len(unresolved_rows) - anonymous_unresolved,
        "above_12": sum(1 for row in rows if int(row["complexity"]) > 12),
        "at_11_or_12": sum(
            1 for row in rows if int(row["complexity"]) in SURVIVOR_BAND
        ),
        "high_crap": sum(
            1
            for row in rows
            if row["crap"] is not None and float(row["crap"]) > HIGH_CRAP
        ),
        "function_coverage_mapped": measured + uncovered,
    }


def mapped_only_function_coverage(metrics: Mapping[str, int]) -> float:
    mapped = int(metrics["function_coverage_mapped"])
    if mapped == 0:
        return 0.0
    return int(metrics["measured"]) / mapped


def c8_implementation_function_coverage(
    production_files: Sequence[str],
    records: Mapping[str, Mapping[str, object]],
) -> dict[str, object]:
    total = hit = 0
    for relative in production_files:
        record = records.get(relative)
        functions = record.get("f") if isinstance(record, dict) else None
        if not isinstance(functions, dict):
            continue
        total += len(functions)
        hit += sum(1 for count in functions.values() if count)
    return {
        "hit": hit,
        "total": total,
        "ratio": 0.0 if total == 0 else hit / total,
    }


def unexecuted_count(value: object) -> int:
    if isinstance(value, list):
        return len(value)
    return int(value or 0)


def resolve_branch_gate(
    file_coverage: Mapping[str, object],
    *,
    stable_denominator: bool = True,
) -> dict[str, object]:
    unexecuted = unexecuted_count(file_coverage["unexecuted_files"])
    total = int(file_coverage["branch_total"])
    hit = int(file_coverage["branch_hit"])
    proxy = 0.0 if total == 0 else hit / total
    unresolved = unexecuted > 0 or not stable_denominator
    return {
        "branch_proxy": proxy,
        "branch_coverage": None if unresolved else proxy,
        "branch_status": "unresolved" if unresolved else "exact",
        "meets_branch_target": (not unresolved)
        and (total == 0 or proxy >= TARGET_COVERAGE),
        "branch_denominator_stable": stable_denominator and unexecuted == 0,
    }


def branch_counts(metrics: Mapping[str, object]) -> tuple[int, int]:
    return int(metrics["branch_total"]), int(metrics["branch_hit"])


def mark_unstable_branch_report(report: Mapping[str, object]) -> dict[str, object]:
    coverage = dict(report["coverage"])
    coverage["branch"] = None
    coverage["branch_status"] = "unresolved"
    coverage["meets_branch_target"] = False
    coverage["branch_denominator_stable"] = False
    coverage["branch_proxy_note"] = (
        "two unfiltered source-mapped c8 artifacts over the same sources "
        "produced different branch totals"
    )
    batches: dict[str, dict[str, object]] = {}
    for name, bucket in dict(report["by_batch"]).items():
        updated = dict(bucket)
        updated["branch_coverage"] = None
        updated["branch_status"] = "unresolved"
        updated["meets_branch_target"] = False
        batches[str(name)] = updated
    return {**dict(report), "coverage": coverage, "by_batch": batches}


def resolve_function_gate(
    file_coverage: Mapping[str, object],
    c8_functions: Mapping[str, object],
) -> dict[str, object]:
    unexecuted = unexecuted_count(file_coverage["unexecuted_files"])
    total = int(c8_functions["total"])
    hit = int(c8_functions["hit"])
    ratio = float(c8_functions["ratio"])
    unresolved = unexecuted > 0
    return {
        "function_c8": ratio,
        "function_c8_hit": hit,
        "function_c8_total": total,
        "function": None if unresolved else ratio,
        "function_status": "unresolved" if unresolved else "exact",
        "meets_function_target": (not unresolved)
        and (total == 0 or ratio >= TARGET_COVERAGE),
    }


def batch_coverage_report(
    *,
    production_rows: Sequence[Mapping[str, object]],
    authored_rows: Sequence[Mapping[str, object]],
    file_coverage: Mapping[str, object],
    records: Mapping[str, Mapping[str, object]] | None = None,
    production_files: Sequence[str] = (),
) -> dict[str, object]:
    production_metrics = function_metrics(production_rows)
    authored_metrics = function_metrics(authored_rows)
    line = float(file_coverage["line_coverage"])
    line_total = int(file_coverage["line_total"])
    branch_total = int(file_coverage["branch_total"])
    production_functions = production_metrics["functions"]
    branch = resolve_branch_gate(file_coverage)
    c8_functions = c8_implementation_function_coverage(
        production_files, records or {}
    )
    functions = resolve_function_gate(file_coverage, c8_functions)
    return {
        "production_files": int(file_coverage["production_files"]),
        "production_functions": production_functions,
        "line_coverage": line,
        "line_hit": int(file_coverage["line_hit"]),
        "line_total": line_total,
        "branch_coverage": branch["branch_coverage"],
        "branch_proxy": branch["branch_proxy"],
        "branch_status": branch["branch_status"],
        "branch_hit": int(file_coverage["branch_hit"]),
        "branch_total": branch_total,
        "function_coverage": functions["function"],
        "function_c8": functions["function_c8"],
        "function_status": functions["function_status"],
        "function_c8_hit": functions["function_c8_hit"],
        "function_c8_total": functions["function_c8_total"],
        "mapped_only_function_coverage": mapped_only_function_coverage(
            production_metrics
        ),
        "measured_functions": production_metrics["measured"],
        "uncovered_functions": production_metrics["uncovered"],
        "unresolved_functions": production_metrics["unresolved"],
        "anonymous_unresolved": production_metrics["anonymous_unresolved"],
        "named_unresolved": production_metrics["named_unresolved"],
        "unexecuted_files": unexecuted_count(file_coverage["unexecuted_files"]),
        "meets_line_target": line_total == 0 or line >= TARGET_COVERAGE,
        "meets_branch_target": branch["meets_branch_target"],
        "meets_function_target": functions["meets_function_target"],
        "authored_functions": len(authored_rows),
        "above_12": authored_metrics["above_12"],
        "at_11_or_12": authored_metrics["at_11_or_12"],
        "high_crap": authored_metrics["high_crap"],
    }


def count_untracked_production_lines(
    listed: Sequence[str],
    scope: Mapping[str, object],
    line_counts: Mapping[str, int],
) -> int:
    added = 0
    for raw in listed:
        relative = posix(raw).removeprefix("frontend/")
        if in_coverage_denominator(relative, scope):
            added += int(line_counts.get(raw, 0))
    return added


def untracked_production_additions(scope: Mapping[str, object]) -> int:
    result = run_tool(
        "git",
        [
            "ls-files",
            "-z",
            "--others",
            "--exclude-standard",
            "--",
            *PRODUCTION_DIFF_PATHS,
        ],
        cwd=ROOT,
    )
    listed = [item for item in (result.stdout or "").split("\0") if item]
    line_counts: dict[str, int] = {}
    for raw in listed:
        path = ROOT / posix(raw)
        if path.is_file():
            line_counts[raw] = len(path.read_text(encoding="utf-8").splitlines())
    return count_untracked_production_lines(listed, scope, line_counts)


def production_growth(git_ref: str, scope: Mapping[str, object]) -> dict[str, int]:
    result = run_tool(
        "git",
        [
            "diff",
            "--numstat",
            git_ref,
            "--",
            *PRODUCTION_DIFF_PATHS,
        ],
        cwd=ROOT,
    )
    added = removed = 0
    for line in (result.stdout or "").splitlines():
        parts = line.split("\t")
        if len(parts) != 3 or parts[0] == "-" or parts[1] == "-":
            continue
        relative = posix(parts[2]).removeprefix("frontend/")
        if not in_coverage_denominator(relative, scope):
            continue
        added += int(parts[0])
        removed += int(parts[1])
    added += untracked_production_additions(scope)
    return {"added": added, "removed": removed, "net": added - removed}


def detect_browser_source_mapped() -> bool:
    marker = FRONTEND / "coverage" / "browser-source-mapped.json"
    if not marker.is_file():
        return False
    try:
        payload = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    return bool(payload.get("file") and payload.get("hit"))


def build_report(
    *,
    authored: Sequence[Mapping[str, object]],
    production: Sequence[Mapping[str, object]],
    production_files: Sequence[str],
    coverage: Mapping[str, object],
    records: Mapping[str, Mapping[str, object]],
    scope: Mapping[str, object],
    identity: Mapping[str, str],
    growth: Mapping[str, int],
    browser_source_mapped: bool,
    type_only: Sequence[str] = (),
) -> dict[str, object]:
    production_metrics = function_metrics(production)
    authored_metrics = function_metrics(authored)
    c8_functions = c8_implementation_function_coverage(production_files, records)
    functions = resolve_function_gate(coverage, c8_functions)
    mapped_only = mapped_only_function_coverage(production_metrics)
    by_batch: dict[str, dict[str, object]] = {}
    for batch in ("7", "8", "9", "unassigned"):
        files = [
            name
            for name in production_files
            if classify_relative(name, scope)["batch"] == batch
        ]
        prod_rows = [row for row in production if str(row["batch"]) == batch]
        by_batch[batch] = batch_coverage_report(
            production_rows=prod_rows,
            authored_rows=[row for row in authored if str(row["batch"]) == batch],
            file_coverage=coverage_metrics(
                files,
                records,
                production_rows=prod_rows,
                type_only_files=type_only,
            ),
            records=records,
            production_files=files,
        )
    return {
        "fixed_point": identity["fixed_point"],
        "current_head": identity["current_head"],
        "max_frontend_complexity": 12,
        "max_python_complexity": 10,
        "coverage_target": TARGET_COVERAGE,
        "browser_tsx_source_mapped": browser_source_mapped,
        "production_files": len(production_files),
        "authored_functions": len(authored),
        "coverage": {
            "scope": "frontend authored production TypeScript/JavaScript",
            "line": coverage["line_coverage"],
            "branch": coverage["branch_coverage"],
            "function": functions["function"],
            "function_c8": functions["function_c8"],
            "mapped_only_function": mapped_only,
            "function_gate": "source_mapped_c8",
            "function_status": functions["function_status"],
            "function_denominator": (
                "source-mapped c8 function map; exact only after every owned "
                "production file executes"
            ),
            "mapped_only_function_note": (
                "diagnostic only: measured / (measured + uncovered)"
            ),
            "function_c8_note": (
                "source-mapped c8 function totals; exact gate while unexecuted "
                "files remain is unresolved"
            ),
            "branch_unexecuted_inventory": "mccabe_decision_points",
            "branch_executed_inventory": "source_mapped_c8",
            "branch_status": coverage["branch_status"],
            "branch_proxy": coverage["branch_proxy"],
            "branch_denominator_stable": coverage.get(
                "branch_denominator_stable", coverage["branch_status"] == "exact"
            ),
            "branch_proxy_note": (
                "upper bound mixing c8 outcomes for executed files with McCabe "
                "decision points for unexecuted files"
                if coverage["branch_status"] == "unresolved"
                else None
            ),
            "line_total": coverage["line_total"],
            "line_hit": coverage["line_hit"],
            "branch_total": coverage["branch_total"],
            "branch_hit": coverage["branch_hit"],
            "unexecuted_files": len(list(coverage["unexecuted_files"])),
            "measured_functions": production_metrics["measured"],
            "uncovered_functions": production_metrics["uncovered"],
            "unresolved_functions": production_metrics["unresolved"],
            "anonymous_unresolved": production_metrics["anonymous_unresolved"],
            "named_unresolved": production_metrics["named_unresolved"],
            "inventoried_functions": production_metrics["functions"],
            "function_c8_hit": functions["function_c8_hit"],
            "function_c8_total": functions["function_c8_total"],
            "meets_line_target": float(coverage["line_coverage"]) >= TARGET_COVERAGE,
            "meets_branch_target": bool(coverage["meets_branch_target"]),
            "meets_function_target": functions["meets_function_target"],
        },
        "complexity": {
            "above_12": authored_metrics["above_12"],
            "at_11_or_12": authored_metrics["at_11_or_12"],
            "high_crap": authored_metrics["high_crap"],
            "production_above_12": production_metrics["above_12"],
        },
        "by_batch": by_batch,
        "unexecuted_files": list(coverage["unexecuted_files"]),
        "above_12": [
            {
                "id": row["id"],
                "batch": row["batch"],
                "file": row["file"],
                "function": row["function"],
                "complexity": row["complexity"],
                "role": row.get("role"),
                "coverage_status": row["coverage_status"],
                "crap": row["crap"],
            }
            for row in authored
            if int(row["complexity"]) > 12
        ],
        "production_growth": growth,
        "unassigned_production": by_batch["unassigned"]["production_files"],
    }


def _pct(ratio: float) -> str:
    return f"{ratio:.2%}"


def print_summary(report: Mapping[str, object]) -> None:
    coverage = report["coverage"]
    print("Frontend production coverage")
    print(f"  line coverage: {_pct(float(coverage['line']))}")
    if coverage["branch_status"] == "unresolved":
        print(
            "  branch coverage: unresolved "
            f"(proxy upper bound {_pct(float(coverage['branch_proxy']))})"
        )
    else:
        print(f"  branch coverage: {_pct(float(coverage['branch']))}")
        print(
            "  executed branch inventory: unfiltered source-mapped c8"
        )
    if coverage["function_status"] == "unresolved":
        print(
            "  function coverage: unresolved "
            f"(source-mapped c8 {_pct(float(coverage['function_c8']))} "
            f"over executed files; {coverage['function_c8_hit']} / "
            f"{coverage['function_c8_total']})"
        )
    else:
        print(
            "  function coverage: "
            f"{_pct(float(coverage['function']))} "
            f"({coverage['function_c8_hit']} / {coverage['function_c8_total']})"
        )
    print(
        "  authored mapping diagnostics: "
        f"measured={coverage['measured_functions']} "
        f"uncovered={coverage['uncovered_functions']} "
        f"anonymous_unresolved={coverage['anonymous_unresolved']} "
        f"named_unresolved={coverage['named_unresolved']}"
    )
    print(
        "  mapped-only function coverage (diagnostic only, "
        "measured / measured+uncovered): "
        f"{_pct(float(coverage['mapped_only_function']))}"
    )
    print(f"  unexecuted files: {coverage['unexecuted_files']}")
    print(f"  unresolved functions: {coverage['unresolved_functions']}")
    print(
        "  95% production gate: "
        f"lines={coverage['meets_line_target']} "
        f"branches={coverage['meets_branch_target']} "
        f"functions={coverage['meets_function_target']}"
    )
    for batch in ("7", "8", "9", "unassigned"):
        bucket = report["by_batch"][batch]
        print(f"\nBatch {batch}")
        print(f"  production files: {bucket['production_files']}")
        print(f"  production functions: {bucket['production_functions']}")
        print(
            f"  line coverage: {_pct(float(bucket['line_coverage']))} "
            f"({bucket['line_hit']} / {bucket['line_total']}) "
            f"meets_95={bucket['meets_line_target']}"
        )
        if bucket["branch_status"] == "unresolved":
            print(
                "  branch coverage: unresolved "
                f"(proxy upper bound {_pct(float(bucket['branch_proxy']))}; "
                f"{bucket['branch_hit']} / {bucket['branch_total']}) "
                f"meets_95={bucket['meets_branch_target']}"
            )
        else:
            print(
                f"  branch coverage: {_pct(float(bucket['branch_coverage']))} "
                f"({bucket['branch_hit']} / {bucket['branch_total']}) "
                f"meets_95={bucket['meets_branch_target']}"
            )
        if bucket["function_status"] == "unresolved":
            print(
                "  function coverage: unresolved "
                f"(source-mapped c8 {_pct(float(bucket['function_c8']))}; "
                f"{bucket['function_c8_hit']} / {bucket['function_c8_total']}) "
                f"meets_95={bucket['meets_function_target']}"
            )
        else:
            print(
                f"  function coverage: {_pct(float(bucket['function_coverage']))} "
                f"({bucket['function_c8_hit']} / {bucket['function_c8_total']}) "
                f"meets_95={bucket['meets_function_target']}"
            )
        print(
            "  authored mapping diagnostics: "
            f"measured={bucket['measured_functions']} "
            f"uncovered={bucket['uncovered_functions']} "
            f"anonymous_unresolved={bucket['anonymous_unresolved']} "
            f"named_unresolved={bucket['named_unresolved']}"
        )
        print(
            "  mapped-only function coverage (diagnostic): "
            f"{_pct(float(bucket['mapped_only_function_coverage']))}"
        )
        print(f"  unresolved functions: {bucket['unresolved_functions']}")
        print(f"  unexecuted files: {bucket['unexecuted_files']}")
        print(f"  authored functions: {bucket['authored_functions']}")
        print(f"  above_12: {bucket['above_12']}")
        print(f"  at_11_or_12: {bucket['at_11_or_12']}")
        print(f"  high_CRAP_signal: {bucket['high_crap']}")


def _self_test_policy() -> None:
    scope = load_scope()
    cases = {
        "app/page.tsx": ("production", "7", True),
        "lib/nyc-route-clock.ts": ("production", "7", True),
        "lib/nyc-route-clock.test.mjs": ("test", "7", False),
        "components/map/smart-route-map.tsx": ("production", "8", True),
        "tests/release/smartroute-chat.spec.ts": ("test", "8", False),
        "scripts/build/bundle-stage.ts": ("production", "9", True),
        "scripts/build/spine.test.ts": ("test", "9", False),
        "components/map/subway-renderer.check.mjs": ("tool", "8", False),
        "types/api.ts": ("excluded", "unassigned", False),
        "next-env.d.ts": ("excluded", "unassigned", False),
        "public/generated.json": ("excluded", "unassigned", False),
        "tools/run-unit-tests.mjs": ("tool", "unassigned", False),
        "tools/oxlint/anti-slop/index.ts": ("excluded", "unassigned", False),
    }
    for relative, (role, batch, covered) in cases.items():
        actual = classify_relative(relative, scope)
        if actual["role"] != role or actual["batch"] != batch:
            raise AssertionError(f"{relative}: {actual}")
        if in_coverage_denominator(relative, scope) != covered:
            raise AssertionError(f"{relative} coverage membership drifted")


def _self_test_thresholds() -> None:
    quality = load_check_quality()
    if quality.is_violating({"language": "TypeScript", "complexity": 12}):
        raise AssertionError("frontend complexity 12 must pass")
    if not quality.is_violating({"language": "TypeScript", "complexity": 13}):
        raise AssertionError("frontend complexity 13 must fail")
    if quality.is_violating({"language": "Python", "complexity": 10}):
        raise AssertionError("Python complexity 10 must pass")
    if not quality.is_violating({"language": "Python", "complexity": 11}):
        raise AssertionError("Python complexity 11 must still fail")
    if quality.MAX_PYTHON_COMPLEXITY != 10 or quality.MAX_COMPLEXITY != 10:
        raise AssertionError("backend complexity ceiling changed")


def _self_test_coverage_states() -> None:
    quality = load_check_quality()
    status, coverage, _reason = quality.classify_js_coverage(
        anonymous=False, file_executed=False, match_count=0, coverage=None
    )
    if status != "uncovered" or coverage != 0.0:
        raise AssertionError("unexecuted production file must count as zero")
    status, coverage, _reason = quality.classify_js_coverage(
        anonymous=False, file_executed=True, match_count=1, coverage=0.0
    )
    if status != "uncovered" or coverage != 0.0:
        raise AssertionError("unique unexecuted function must be uncovered")
    status, coverage, _reason = quality.classify_js_coverage(
        anonymous=False, file_executed=True, match_count=2, coverage=None
    )
    if status != "unresolved" or coverage is not None:
        raise AssertionError("ambiguous same-name functions must stay unresolved")
    fake = coverage_metrics(
        ["lib/nyc-route-clock.ts"],
        {},
    )
    if fake["line_total"] <= 0:
        raise AssertionError("missing-file denominator needs positive source lines")
    if fake["line_hit"] != 0:
        raise AssertionError("missing production file must have zero hits")
    if fake["unexecuted_files"] != ["frontend/lib/nyc-route-clock.ts"]:
        raise AssertionError(f"missing c8 record must stay unexecuted, got {fake}")


def _self_test_function_coverage_definitions() -> None:
    measured = {
        "functions": 2461,
        "measured": 847,
        "uncovered": 926,
        "unresolved": 688,
        "anonymous_unresolved": 618,
        "named_unresolved": 70,
        "function_coverage_mapped": 1773,
        "above_12": 0,
        "at_11_or_12": 0,
        "high_crap": 0,
    }
    mapped_only = mapped_only_function_coverage(measured)
    if abs(mapped_only - (847 / 1773)) > 1e-12:
        raise AssertionError(
            f"mapped-only diagnostic must ignore unresolved, got {mapped_only}"
        )
    split = function_metrics(
        [
            {
                "coverage_status": "measured",
                "function": "load",
                "complexity": 1,
                "crap": 1.0,
            },
            {
                "coverage_status": "uncovered",
                "function": "save",
                "complexity": 1,
                "crap": 1.0,
            },
            {
                "coverage_status": "unresolved",
                "function": "anonymous",
                "anonymous": True,
                "complexity": 1,
                "crap": None,
            },
            {
                "coverage_status": "unresolved",
                "function": "renderPage",
                "anonymous": False,
                "complexity": 1,
                "crap": None,
            },
        ]
    )
    if split["anonymous_unresolved"] != 1 or split["named_unresolved"] != 1:
        raise AssertionError(
            "anonymous and named unresolved mapping counts must stay separate, "
            f"got {split}"
        )
    if split["unresolved"] != 2:
        raise AssertionError("unresolved total must remain the diagnostic sum")


def _self_test_batch_production_coverage() -> None:
    production = [
        {
            "batch": "9",
            "coverage_status": "measured",
            "complexity": 4,
            "crap": 4.0,
        },
        {
            "batch": "9",
            "coverage_status": "uncovered",
            "complexity": 4,
            "crap": 4.0,
        },
        {
            "batch": "9",
            "coverage_status": "unresolved",
            "complexity": 4,
            "crap": None,
        },
    ]
    authored = [
        *production,
        {
            "batch": "9",
            "coverage_status": "measured",
            "complexity": 20,
            "crap": 20.0,
        },
    ]
    batch = batch_coverage_report(
        production_rows=production,
        authored_rows=authored,
        file_coverage={
            "line_coverage": 0.5,
            "branch_coverage": 0.4,
            "line_hit": 10,
            "line_total": 20,
            "branch_hit": 2,
            "branch_total": 5,
            "unexecuted_files": ["frontend/scripts/a.ts"],
            "production_files": 1,
        },
    )
    if batch["production_functions"] != 3:
        raise AssertionError("batch function coverage must ignore tests and tools")
    if batch["function_status"] != "unresolved" or batch["function_coverage"] is not None:
        raise AssertionError("unexecuted batch files keep the exact function gate unresolved")
    if batch["above_12"] != 1:
        raise AssertionError("authored complexity must still count over-12 tools and tests")
    if batch["meets_function_target"] is not False:
        raise AssertionError("unresolved function status cannot meet the 95% gate")
    if batch["meets_branch_target"] is not False:
        raise AssertionError("unexecuted batch files keep the exact branch gate unresolved")
    if batch["branch_status"] != "unresolved" or batch["branch_coverage"] is not None:
        raise AssertionError("batch branch coverage must stay unresolved while files are unexecuted")


def _self_test_unexecuted_branches_use_decisions() -> None:
    record = {
        "s": {"0": 0},
        "statementMap": {"0": {"start": {"line": 1}}},
        "b": {"0": [0]},
    }
    rows = [
        {
            "file": "frontend/app/page.tsx",
            "complexity": 50,
            "coverage_status": "uncovered",
        }
    ]
    metrics = coverage_metrics(
        ["app/page.tsx"],
        {"app/page.tsx": record},
        production_rows=rows,
    )
    if metrics["branch_total"] != 49:
        raise AssertionError(
            f"unexecuted files must inventory decision points, got {metrics['branch_total']}"
        )
    if metrics["branch_hit"] != 0:
        raise AssertionError("unexecuted branches must have zero hits")
    executed = {
        "s": {"0": 1},
        "statementMap": {"0": {"start": {"line": 1}}},
        "b": {"0": [1, 0, 1]},
    }
    kept = coverage_metrics(
        ["app/page.tsx"],
        {"app/page.tsx": executed},
        production_rows=rows,
    )
    if kept["branch_total"] != 3 or kept["branch_hit"] != 2:
        raise AssertionError("executed files must keep c8 branch counts")
    if kept["branch_status"] != "exact" or kept["branch_coverage"] is None:
        raise AssertionError("all-executed files must report exact c8 branch coverage")


def _self_test_branch_gate_stays_unresolved() -> None:
    record = {
        "s": {"0": 0},
        "statementMap": {"0": {"start": {"line": 1}}},
        "b": {"0": [0]},
    }
    mixed = coverage_metrics(
        ["lib/nyc-route-clock.ts", "app/page.tsx"],
        {
            "lib/nyc-route-clock.ts": {
                "s": {"0": 1},
                "statementMap": {"0": {"start": {"line": 1}}},
                "b": {"0": [1, 0, 1, 1, 0, 1, 1, 0, 1, 1]},
            },
            "app/page.tsx": record,
        },
        production_rows=[
            {"file": "frontend/lib/nyc-route-clock.ts", "complexity": 5},
            {"file": "frontend/app/page.tsx", "complexity": 50},
        ],
    )
    if mixed["branch_total"] != 59:
        raise AssertionError(
            f"proxy still sums mixed units, got {mixed['branch_total']}"
        )
    if mixed["branch_status"] != "unresolved" or mixed["branch_coverage"] is not None:
        raise AssertionError("mixed c8 and McCabe units must not claim exact coverage")
    if mixed["meets_branch_target"]:
        raise AssertionError("unexecuted files keep the 95% branch gate unresolved")
    high = resolve_branch_gate(
        {
            "branch_hit": 99,
            "branch_total": 100,
            "unexecuted_files": ["frontend/app/page.tsx"],
        }
    )
    if high["meets_branch_target"] or high["branch_coverage"] is not None:
        raise AssertionError("a high proxy must not satisfy the exact branch gate")
    exact = resolve_branch_gate(
        {
            "branch_hit": 99,
            "branch_total": 100,
            "unexecuted_files": [],
        }
    )
    if exact["branch_status"] != "exact" or exact["meets_branch_target"] is not True:
        raise AssertionError("all-executed c8 coverage may satisfy the 95% branch gate")


def _self_test_c8_function_gate() -> None:
    executed = {
        "s": {"0": 1},
        "statementMap": {"0": {"start": {"line": 1}}},
        "b": {"0": [1, 0, 1]},
        "f": {"0": 1, "1": 1, "2": 0},
    }
    missing = coverage_metrics(["app/page.tsx"], {})
    missing_c8 = c8_implementation_function_coverage(["app/page.tsx"], {})
    missing_gate = resolve_function_gate(missing, missing_c8)
    if missing["branch_status"] != "unresolved" or missing["branch_coverage"] is not None:
        raise AssertionError("one unexecuted production file must keep exact branch unresolved")
    if missing_gate["function_status"] != "unresolved" or missing_gate["function"] is not None:
        raise AssertionError("one unexecuted production file must keep exact function unresolved")
    if missing_gate["meets_function_target"]:
        raise AssertionError("unresolved function status cannot meet the 95% gate")
    kept = coverage_metrics(["app/page.tsx"], {"app/page.tsx": executed})
    kept_c8 = c8_implementation_function_coverage(
        ["app/page.tsx"], {"app/page.tsx": executed}
    )
    kept_gate = resolve_function_gate(kept, kept_c8)
    if kept["branch_status"] != "exact" or kept["branch_total"] != 3 or kept["branch_hit"] != 2:
        raise AssertionError("all-executed files must report exact c8 branch totals")
    if kept_gate["function_status"] != "exact" or kept_gate["function"] is None:
        raise AssertionError("all-executed files must report exact c8 function totals")
    if kept_c8["hit"] != 2 or kept_c8["total"] != 3:
        raise AssertionError(
            f"c8 function totals drifted, got hit={kept_c8['hit']} total={kept_c8['total']}"
        )
    renamed = {
        "s": {"0": 1},
        "statementMap": {"0": {"start": {"line": 1}}},
        "b": {"0": [1]},
        "f": {"0": 1, "1": 0},
    }
    files = ["app/page.tsx"]
    records = {"app/page.tsx": renamed}
    file_coverage = coverage_metrics(files, records)
    anonymous_rows = [
        {
            "file": "frontend/app/page.tsx",
            "function": "anonymous",
            "anonymous": True,
            "coverage_status": "unresolved",
            "complexity": 1,
            "crap": None,
            "batch": "7",
        }
    ]
    named_rows = [
        {
            "file": "frontend/app/page.tsx",
            "function": "handleClick",
            "anonymous": False,
            "coverage_status": "measured",
            "complexity": 1,
            "crap": 1.0,
            "batch": "7",
        }
    ]
    anonymous_batch = batch_coverage_report(
        production_rows=anonymous_rows,
        authored_rows=anonymous_rows,
        file_coverage=file_coverage,
        records=records,
        production_files=files,
    )
    named_batch = batch_coverage_report(
        production_rows=named_rows,
        authored_rows=named_rows,
        file_coverage=file_coverage,
        records=records,
        production_files=files,
    )
    if anonymous_batch["function_c8"] != named_batch["function_c8"]:
        raise AssertionError(
            "renaming an anonymous authored callback must not change c8 function coverage"
        )
    if anonymous_batch["measured_functions"] == named_batch["measured_functions"]:
        raise AssertionError(
            "authored mapping diagnostics may change when a callback is named; "
            "the c8 aggregate must stay independent of that rename"
        )
    if abs(float(anonymous_batch["function_c8"]) - 0.5) > 1e-12:
        raise AssertionError("c8 function ratio must follow the f map, not authored names")


def _self_test_untracked_production_growth() -> None:
    scope = load_scope()
    added = count_untracked_production_lines(
        ["frontend/lib/new-module.ts", "frontend/lib/new-module.test.ts"],
        scope,
        {
            "frontend/lib/new-module.ts": 40,
            "frontend/lib/new-module.test.ts": 99,
        },
    )
    if added != 40:
        raise AssertionError(
            f"untracked production files must count as added lines, got {added}"
        )


def _self_test_fixed_point() -> None:
    identity = report_identity(
        quality_ref="HEAD",
        current_head="0" * 40,
    )
    if identity["fixed_point"] == "0" * 40:
        raise AssertionError("fixed_point must store the resolved quality-ref, not HEAD")
    if identity["current_head"] != "0" * 40:
        raise AssertionError("current_head must stay separate from fixed_point")
    if identity["fixed_point"] == identity["current_head"]:
        raise AssertionError("fixed_point and current_head must remain separate fields")


def _self_test_discovery() -> None:
    result = run_tool("node", [str(UNIT_RUNNER), "--list"], cwd=FRONTEND)
    if result.returncode != 0:
        raise RuntimeError(result.stderr or "unit test listing failed")
    listed = [posix(line) for line in result.stdout.splitlines() if line.strip()]
    if any(path.startswith("tests/release/") or path.endswith(".spec.ts") for path in listed):
        raise AssertionError("Playwright specs must not enter the unit manifest")
    if any(not path.endswith((".test.ts", ".test.tsx", ".test.mjs", ".test.js")) for path in listed):
        raise AssertionError("unit discovery drifted away from *.test.* files")
    if "lib/nyc-route-clock.test.mjs" not in listed:
        raise AssertionError("canonical unit discovery missed an existing test")
    if "scripts/build-artifact-manifest.test.ts" in listed:
        raise AssertionError("generated artifact hash lock must stay off the unit contract")


def _self_test_type_only_coverage() -> None:
    type_only = ["scripts/build/types.ts"]
    missing = coverage_metrics(
        ["scripts/build/types.ts", "app/page.tsx"],
        {},
        production_rows=[
            {"file": "frontend/scripts/build/types.ts", "complexity": 1},
            {"file": "frontend/app/page.tsx", "complexity": 50},
        ],
        type_only_files=type_only,
    )
    if "frontend/scripts/build/types.ts" in missing["unexecuted_files"]:
        raise AssertionError("type-only modules must not be executable coverage")
    if "frontend/app/page.tsx" not in missing["unexecuted_files"]:
        raise AssertionError("runtime modules without hits stay unexecuted")
    if int(missing["branch_total"]) != 49:
        raise AssertionError(
            f"type-only files must not add branch debt, got {missing['branch_total']}"
        )
    executed_type_only = coverage_metrics(
        ["scripts/build/types.ts"],
        {
            "scripts/build/types.ts": {
                "s": {"0": 1},
                "statementMap": {"0": {"start": {"line": 1}}},
                "b": {"0": [0, 0, 0]},
            }
        },
        type_only_files=type_only,
    )
    if executed_type_only["line_total"] != 0:
        raise AssertionError("type-only modules must not enter the line denominator")
    if executed_type_only["unexecuted_files"]:
        raise AssertionError("type-only modules must not be marked unexecuted")
    if executed_type_only["branch_status"] != "exact":
        raise AssertionError("dropping type-only files must keep the exact branch gate")


def _self_test_runtime_inventory() -> None:
    typed = run_tool(
        "node",
        [str(JS_METRICS), "--source", "scripts/build/types-self.ts"],
        cwd=ROOT,
        input_text="export type Foo = string;\n",
    )
    valued = run_tool(
        "node",
        [str(JS_METRICS), "--source", "scripts/build/value-self.ts"],
        cwd=ROOT,
        input_text="export const n = 1;\n",
    )
    if typed.returncode != 0 or valued.returncode != 0:
        raise RuntimeError(typed.stderr or valued.stderr or "runtime inventory failed")
    typed_payload = json.loads(typed.stdout)
    valued_payload = json.loads(valued.stdout)
    if typed_payload["files"][0]["runtime"] is not False:
        raise AssertionError("pure type module must be runtime=false")
    if valued_payload["files"][0]["runtime"] is not True:
        raise AssertionError("value export must be runtime=true")
    if typed_payload["functions"]:
        raise AssertionError("pure type module must have no runtime functions")


def _self_test_live_ownership() -> None:
    inventory = run_js_inventory("production")
    unassigned = [
        str(item["file"])
        for item in inventory.get("files", [])
        if str(item.get("batch")) == "unassigned"
    ]
    if unassigned:
        raise AssertionError(f"unassigned production files: {unassigned[:20]}")


def _self_test_true_if_does_not_cover_false() -> None:
    record = {
        "s": {"0": 1, "1": 1},
        "statementMap": {
            "0": {"start": {"line": 1}},
            "1": {"start": {"line": 2}},
        },
        "b": {"if": [1, 0]},
    }
    metrics = coverage_metrics(["app/page.tsx"], {"app/page.tsx": record})
    if metrics["branch_total"] != 2 or metrics["branch_hit"] != 1:
        raise AssertionError(
            "executing only the true side of an if must leave the false "
            f"outcome missing, got {metrics['branch_hit']}/{metrics['branch_total']}"
        )
    if metrics["meets_branch_target"]:
        raise AssertionError("a missing if outcome must not meet the 95% gate")


def _self_test_untested_short_circuit_remains_missing() -> None:
    record = {
        "s": {"0": 1},
        "statementMap": {"0": {"start": {"line": 1}}},
        "b": {
            "nullish": [1],
            "or": [0],
            "and": [1],
            "optional": [0],
        },
    }
    metrics = coverage_metrics(["app/page.tsx"], {"app/page.tsx": record})
    if metrics["branch_total"] != 4 or metrics["branch_hit"] != 2:
        raise AssertionError(
            "untested ??, ||, &&, or optional-chaining outcomes must remain "
            f"missing, got {metrics['branch_hit']}/{metrics['branch_total']}"
        )


def _self_test_wrappers_do_not_inflate() -> None:
    records = {
        "app/page.tsx": {
            "s": {"0": 1},
            "statementMap": {"0": {"start": {"line": 1}}},
            "b": {"real": [1, 0]},
        },
        "node_modules/c8/wrapper.js": {
            "s": {"0": 1},
            "statementMap": {"0": {"start": {"line": 1}}},
            "b": {"wrap": [0, 0, 0, 0, 0]},
        },
        "scripts/build/types.ts": {
            "s": {"0": 1},
            "statementMap": {"0": {"start": {"line": 1}}},
            "b": {"wrap": [0, 0, 0]},
        },
    }
    metrics = coverage_metrics(
        ["app/page.tsx", "scripts/build/types.ts"],
        records,
        type_only_files=["scripts/build/types.ts"],
    )
    if metrics["branch_total"] != 2 or metrics["branch_hit"] != 1:
        raise AssertionError(
            "wrapper artifacts and type-only files must not inflate the "
            f"branch denominator, got {metrics['branch_hit']}/{metrics['branch_total']}"
        )


def _self_test_saved_artifact_is_stable() -> None:
    record = {
        "s": {"0": 1},
        "statementMap": {"0": {"start": {"line": 1}}},
        "b": {"if": [1, 0], "nullish": [0], "or": [1]},
    }
    records = {"app/page.tsx": record}
    first = coverage_metrics(["app/page.tsx"], records)
    second = coverage_metrics(["app/page.tsx"], records)
    if branch_counts(first) != branch_counts(second):
        raise AssertionError(
            "two reports over the same coverage artifact must have identical "
            f"branch counts, got {branch_counts(first)} vs {branch_counts(second)}"
        )
    if first["branch_hit"] != second["branch_hit"]:
        raise AssertionError("saved coverage hit counts must be identical")


def _self_test_unstable_denominator_cannot_pass() -> None:
    high = {
        "branch_hit": 99,
        "branch_total": 100,
        "unexecuted_files": [],
    }
    exact = resolve_branch_gate(high, stable_denominator=True)
    if exact["branch_status"] != "exact" or exact["meets_branch_target"] is not True:
        raise AssertionError("a stable all-executed 99% c8 result may meet 95%")
    unstable = resolve_branch_gate(high, stable_denominator=False)
    if unstable["branch_status"] != "unresolved" or unstable["meets_branch_target"]:
        raise AssertionError("unresolved evidence cannot pass the 95% branch gate")
    if unstable["branch_coverage"] is not None:
        raise AssertionError("unstable denominators must not report exact coverage")
    report = {
        "coverage": {
            "branch": 0.99,
            "branch_status": "exact",
            "meets_branch_target": True,
            "branch_total": 100,
            "branch_hit": 99,
            "branch_proxy": 0.99,
        },
        "by_batch": {
            "9": {
                "branch_coverage": 0.99,
                "branch_status": "exact",
                "meets_branch_target": True,
                "branch_total": 50,
                "branch_hit": 49,
            }
        },
    }
    marked = mark_unstable_branch_report(report)
    if marked["coverage"]["meets_branch_target"] or marked["by_batch"]["9"]["meets_branch_target"]:
        raise AssertionError("a mismatched repeat run cannot pass the 95% gate")


def self_test() -> None:
    _self_test_policy()
    _self_test_thresholds()
    _self_test_coverage_states()
    _self_test_function_coverage_definitions()
    _self_test_batch_production_coverage()
    _self_test_unexecuted_branches_use_decisions()
    _self_test_branch_gate_stays_unresolved()
    _self_test_c8_function_gate()
    _self_test_type_only_coverage()
    _self_test_runtime_inventory()
    _self_test_untracked_production_growth()
    _self_test_fixed_point()
    _self_test_discovery()
    _self_test_live_ownership()
    _self_test_true_if_does_not_cover_false()
    _self_test_untested_short_circuit_remains_missing()
    _self_test_wrappers_do_not_inflate()
    _self_test_saved_artifact_is_stable()
    _self_test_unstable_denominator_cannot_pass()
    print("self-test passed")


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    parser.add_argument("--coverage-file", default=str(DEFAULT_COVERAGE))
    parser.add_argument("--coverage-repeat", default=None)
    parser.add_argument("--quality-ref", default="HEAD")
    return parser.parse_args(argv)


def resolve_coverage_path(raw: str) -> Path:
    path = Path(raw)
    return path if path.is_absolute() else ROOT / path


def write_json(path: Path, payload: Mapping[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def assemble_report(
    *,
    args: argparse.Namespace,
    quality,
    scope: Mapping[str, object],
    authored_inventory: Mapping[str, object],
    production_inventory: Mapping[str, object],
    production_files: Sequence[str],
) -> dict[str, object]:
    records = coverage_index(resolve_coverage_path(args.coverage_file))
    type_only = type_only_files(production_inventory)
    v8 = quality.load_v8_coverage(DEFAULT_V8)
    authored = decorate_functions(authored_inventory["functions"], v8=v8, quality=quality)
    production = decorate_functions(
        production_inventory["functions"], v8=v8, quality=quality
    )
    coverage = coverage_metrics(
        production_files,
        records,
        production_rows=production,
        type_only_files=type_only,
    )
    identity = report_identity(quality_ref=args.quality_ref)
    report = build_report(
        authored=authored,
        production=production,
        production_files=production_files,
        coverage=coverage,
        records=records,
        scope=scope,
        identity=identity,
        growth=production_growth(identity["fixed_point"], scope),
        browser_source_mapped=detect_browser_source_mapped(),
        type_only=type_only,
    )
    if not args.coverage_repeat:
        return report
    repeat_records = coverage_index(resolve_coverage_path(args.coverage_repeat))
    repeat = coverage_metrics(
        production_files,
        repeat_records,
        production_rows=production,
        type_only_files=type_only,
    )
    if branch_counts(coverage) == branch_counts(repeat):
        return report
    return mark_unstable_branch_report(report)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    if args.self_test:
        self_test()
        return 0
    self_test()
    quality = load_check_quality()
    scope = load_scope()
    authored_inventory = run_js_inventory("authored")
    production_inventory = run_js_inventory("production")
    production_files = [
        str(item["file"])
        for item in production_inventory.get("files", [])
        if in_coverage_denominator(str(item["file"]), scope)
    ]
    unassigned = [
        name
        for name in production_files
        if classify_relative(name, scope)["batch"] == "unassigned"
    ]
    if unassigned:
        raise RuntimeError(f"unassigned production files: {unassigned[:20]}")
    report = assemble_report(
        args=args,
        quality=quality,
        scope=scope,
        authored_inventory=authored_inventory,
        production_inventory=production_inventory,
        production_files=production_files,
    )
    print_summary(report)
    output = resolve_coverage_path(args.output)
    write_json(output, report)
    print(f"Wrote {output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
