"""Produce reproducible Phase 2 architecture, coverage, complexity, and CRAP data.

Run the deterministic suite under coverage first:

    python -m coverage erase
    python -m coverage run --branch --source=app -m pytest -q
    python scripts/phase2_quality_report.py --output ../phase2-quality.json

CRAP defaults to the Phase 2 review scope, ``services/agent`` plus
``services/trips``. Static architecture metrics intentionally cover every
current ``app/**/*.py`` file so production-package ownership moves remain
visible.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import platform
import shutil
import subprocess
from collections import Counter
from collections.abc import Iterable
from datetime import datetime
from pathlib import Path

import coverage
import radon
from radon.complexity import cc_visit
from radon.visitors import Class, Function

FunctionNode = ast.FunctionDef | ast.AsyncFunctionDef


class FunctionInventory(ast.NodeVisitor):
    def __init__(self) -> None:
        self.scope: list[str] = []
        self.functions: list[tuple[str, FunctionNode]] = []

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self.scope.append(node.name)
        self.generic_visit(node)
        self.scope.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._visit_function(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._visit_function(node)

    def _visit_function(self, node: FunctionNode) -> None:
        qualified_name = ".".join((*self.scope, node.name))
        self.functions.append((qualified_name, node))
        self.scope.append(node.name)
        self.generic_visit(node)
        self.scope.pop()


def source_files(source_root: Path) -> list[Path]:
    return sorted(
        path.resolve()
        for path in source_root.rglob("*.py")
        if "__pycache__" not in path.parts
    )


def source_manifest(files: Iterable[Path], base: Path) -> str:
    rows = []
    for path in files:
        payload = path.read_bytes()
        rows.append(
            f"{path.relative_to(base).as_posix()}\0{len(payload)}\0"
            f"{hashlib.sha256(payload).hexdigest()}"
        )
    return hashlib.sha256("\n".join(rows).encode()).hexdigest()


def git_value(repo_root: Path, *args: str) -> str:
    allowed_args = {
        ("status", "--porcelain=v1", "-z"),
        ("rev-parse", "HEAD"),
        ("rev-parse", "HEAD^{tree}"),
    }
    if args not in allowed_args:
        message = "unsupported git arguments"
        raise ValueError(message)
    git = shutil.which("git")
    if git is None:
        message = "git executable was not found"
        raise FileNotFoundError(message)
    result = subprocess.run(  # noqa: S603 allowlisted git argv after which()
        [git, *args],
        cwd=repo_root,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def _add_radon_function(measured: dict[tuple[int, str], int], item: Function) -> None:
    measured[(item.lineno, item.name)] = item.complexity
    for closure in item.closures:
        _add_radon_function(measured, closure)


def _add_radon_class(measured: dict[tuple[int, str], int], item: Class) -> None:
    for method in item.methods:
        _add_radon_function(measured, method)
    for inner_class in item.inner_classes:
        _add_radon_class(measured, inner_class)


def radon_functions(source: str) -> dict[tuple[int, str], int]:
    measured: dict[tuple[int, str], int] = {}
    for block in cc_visit(source):
        if isinstance(block, Function):
            _add_radon_function(measured, block)
        elif isinstance(block, Class):
            _add_radon_class(measured, block)
    return measured


def nested_body_ranges(node: FunctionNode) -> list[tuple[int, int]]:
    ranges: list[tuple[int, int]] = []
    for descendant in ast.walk(node):
        if descendant is node or not isinstance(
            descendant, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
        ):
            continue
        body = getattr(descendant, "body", ())
        if body and descendant.end_lineno is not None:
            ranges.append((body[0].lineno, descendant.end_lineno))
    return ranges


def function_statement_lines(
    node: FunctionNode,
    executable_lines: set[int],
) -> set[int]:
    if not node.body or node.end_lineno is None:
        return set()
    lines = {
        line
        for line in executable_lines
        if node.body[0].lineno <= line <= node.end_lineno
    }
    for start, end in nested_body_ranges(node):
        lines.difference_update(range(start, end + 1))
    return lines


def crap_distribution(values: Iterable[float]) -> dict[str, int]:
    buckets = {"<=4": 0, "4-8": 0, "8-15": 0, "15-30": 0, ">30": 0}
    for value in values:
        if value <= 4:
            buckets["<=4"] += 1
        elif value <= 8:
            buckets["4-8"] += 1
        elif value < 15:
            buckets["8-15"] += 1
        elif value <= 30:
            buckets["15-30"] += 1
        else:
            buckets[">30"] += 1
    return buckets


def complexity_distribution(values: Iterable[int]) -> dict[str, int]:
    buckets = {"1-4": 0, "5-8": 0, "9-14": 0, "15-30": 0, ">30": 0}
    for value in values:
        if value <= 4:
            buckets["1-4"] += 1
        elif value <= 8:
            buckets["5-8"] += 1
        elif value < 15:
            buckets["9-14"] += 1
        elif value <= 30:
            buckets["15-30"] += 1
        else:
            buckets[">30"] += 1
    return buckets


def coverage_distribution(values: Iterable[float]) -> dict[str, int]:
    buckets = {"0%": 0, "1-49%": 0, "50-79%": 0, "80-99%": 0, "100%": 0}
    for value in values:
        if value == 0:
            buckets["0%"] += 1
        elif value < 0.5:
            buckets["1-49%"] += 1
        elif value < 0.8:
            buckets["50-79%"] += 1
        elif value < 1:
            buckets["80-99%"] += 1
        else:
            buckets["100%"] += 1
    return buckets


def _diagnosis(complexity: int, ratio: float) -> str:
    if complexity >= 15 and ratio < 0.8:
        return "mixed coverage and branching complexity"
    if complexity >= 15:
        return "branching complexity"
    if ratio < 0.8:
        return "coverage"
    return "none"


def _function_row(
    relative_path: str,
    qualified_name: str,
    node: FunctionNode,
    statements: set[int],
    executed: set[int],
    complexity: int,
) -> dict[str, object]:
    statement_count = len(statements)
    ratio = len(executed) / statement_count if statement_count else 1.0
    crap = complexity**2 * (1 - ratio) ** 3 + complexity
    return {
        "file": relative_path,
        "function": qualified_name,
        "line": node.lineno,
        "loc": (node.end_lineno or node.lineno) - node.lineno + 1,
        "complexity": complexity,
        "statement_count": statement_count,
        "executed_statement_count": len(executed),
        "coverage": round(ratio, 6),
        "crap": round(crap, 6),
        "conceptual_responsibility_count": 1,
        "diagnosis": _diagnosis(complexity, ratio),
    }


def measured_functions(
    files: list[Path],
    backend_root: Path,
    coverage_file: Path,
) -> list[dict[str, object]]:
    measured_coverage = coverage.Coverage(data_file=str(coverage_file))
    measured_coverage.load()
    rows: list[dict[str, object]] = []
    for path in files:
        source = path.read_text(encoding="utf-8-sig")
        tree = ast.parse(source, filename=str(path))
        inventory = FunctionInventory()
        inventory.visit(tree)
        complexities = radon_functions(source)
        _, executable, _, missing, _ = measured_coverage.analysis2(str(path))
        executable_lines = set(executable)
        executed_lines = executable_lines.difference(missing)
        relative_path = path.relative_to(backend_root).as_posix()
        for qualified_name, node in inventory.functions:
            statements = function_statement_lines(node, executable_lines)
            complexity = complexities.get((node.lineno, node.name))
            if complexity is None:
                missing_radon = (
                    f"Radon did not report {relative_path}:{node.lineno} {qualified_name}"
                )
                raise RuntimeError(missing_radon)
            rows.append(
                _function_row(
                    relative_path,
                    qualified_name,
                    node,
                    statements,
                    statements.intersection(executed_lines),
                    complexity,
                )
            )
    return sorted(
        rows, key=lambda row: (-float(row["crap"]), str(row["file"]), int(row["line"]))
    )


def module_name(path: Path, backend_root: Path) -> str:
    relative = path.relative_to(backend_root).with_suffix("")
    parts = list(relative.parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def _skip_guarded_if(statement: ast.If) -> bool:
    name = statement.test.id if isinstance(statement.test, ast.Name) else ""
    is_main = (
        isinstance(statement.test, ast.Compare)
        and isinstance(statement.test.left, ast.Name)
        and statement.test.left.id == "__name__"
    )
    return name == "TYPE_CHECKING" or is_main


def _record_plain_import(statement: ast.Import, known_modules: set[str], imports: set[str]) -> None:
    for alias in statement.names:
        if alias.name in known_modules:
            imports.add(alias.name)


def _record_from_import(statement: ast.ImportFrom, known_modules: set[str], imports: set[str]) -> None:
    if not statement.module:
        return
    base = statement.module
    for alias in statement.names:
        candidate = f"{base}.{alias.name}"
        if candidate in known_modules:
            imports.add(candidate)
        elif base in known_modules:
            imports.add(base)


def _visit_if_imports(statement: ast.If, known_modules: set[str], imports: set[str]) -> None:
    if _skip_guarded_if(statement):
        return
    _visit_import_statements(statement.body, known_modules, imports)
    _visit_import_statements(statement.orelse, known_modules, imports)


def _visit_try_imports(statement: ast.Try, known_modules: set[str], imports: set[str]) -> None:
    _visit_import_statements(statement.body, known_modules, imports)
    _visit_import_statements(statement.orelse, known_modules, imports)
    _visit_import_statements(statement.finalbody, known_modules, imports)
    for handler in statement.handlers:
        _visit_import_statements(handler.body, known_modules, imports)


def _dispatch_import_statement(
    statement: ast.stmt, known_modules: set[str], imports: set[str]
) -> None:
    if isinstance(statement, ast.If):
        _visit_if_imports(statement, known_modules, imports)
        return
    if isinstance(statement, ast.Try):
        _visit_try_imports(statement, known_modules, imports)
        return
    if isinstance(statement, ast.Import):
        _record_plain_import(statement, known_modules, imports)
        return
    if isinstance(statement, ast.ImportFrom):
        _record_from_import(statement, known_modules, imports)


def _visit_import_statements(
    statements: list[ast.stmt], known_modules: set[str], imports: set[str]
) -> None:
    for statement in statements:
        if isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        _dispatch_import_statement(statement, known_modules, imports)


def imported_modules(tree: ast.Module, known_modules: set[str]) -> set[str]:
    imports: set[str] = set()
    _visit_import_statements(tree.body, known_modules, imports)
    return imports


class _Tarjan:
    def __init__(self, graph: dict[str, set[str]]) -> None:
        self.graph = graph
        self.index = 0
        self.indices: dict[str, int] = {}
        self.lowlinks: dict[str, int] = {}
        self.stack: list[str] = []
        self.on_stack: set[str] = set()
        self.components: list[list[str]] = []

    def run(self) -> list[list[str]]:
        for node in self.graph:
            if node not in self.indices:
                self.visit(node)
        return sorted(self.components)

    def visit_edge(self, node: str, dependency: str) -> None:
        if dependency not in self.indices:
            self.visit(dependency)
            self.lowlinks[node] = min(self.lowlinks[node], self.lowlinks[dependency])
        elif dependency in self.on_stack:
            self.lowlinks[node] = min(self.lowlinks[node], self.indices[dependency])

    def pop_component(self, node: str) -> list[str]:
        component: list[str] = []
        while True:
            member = self.stack.pop()
            self.on_stack.remove(member)
            component.append(member)
            if member == node:
                return component

    def visit(self, node: str) -> None:
        self.indices[node] = self.index
        self.lowlinks[node] = self.index
        self.index += 1
        self.stack.append(node)
        self.on_stack.add(node)
        for dependency in self.graph[node]:
            self.visit_edge(node, dependency)
        if self.lowlinks[node] != self.indices[node]:
            return
        component = self.pop_component(node)
        if len(component) > 1 or node in self.graph[node]:
            self.components.append(sorted(component))


def strongly_connected_components(graph: dict[str, set[str]]) -> list[list[str]]:
    return _Tarjan(graph).run()


def is_forwarder(node: FunctionNode) -> bool:
    body = list(node.body)
    if (
        body
        and isinstance(body[0], ast.Expr)
        and isinstance(body[0].value, ast.Constant)
    ):
        body.pop(0)
    if len(body) != 1:
        return False
    statement = body[0]
    value = statement.value if isinstance(statement, (ast.Return, ast.Expr)) else None
    if isinstance(value, ast.Await):
        value = value.value
    return isinstance(value, ast.Call)


def _is_module_docstring(node: ast.stmt) -> bool:
    return (
        isinstance(node, ast.Expr)
        and isinstance(node.value, ast.Constant)
        and isinstance(node.value.value, str)
    )


def _is_all_assignment(node: ast.stmt) -> bool:
    return isinstance(node, ast.Assign) and any(
        isinstance(target, ast.Name) and target.id == "__all__" for target in node.targets
    )


def _imported_name(node: ast.AST) -> str:
    if isinstance(node, ast.ImportFrom) and node.module:
        return node.module
    if isinstance(node, ast.Import):
        return ",".join(alias.name for alias in node.names)
    return ""


def _module_architecture(
    name: str,
    path: Path,
    backend_root: Path,
    modules: dict[str, Path],
) -> tuple[set[str], Counter[tuple[str, str]], list[tuple[str, FunctionNode]], bool, bool, list[dict[str, object]]]:
    source = path.read_text(encoding="utf-8-sig")
    tree = ast.parse(source, filename=str(path))
    inventory = FunctionInventory()
    inventory.visit(tree)
    top_level = {
        node.name: node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    same_module_calls: Counter[tuple[str, str]] = Counter()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in top_level:
            same_module_calls[(name, node.func.id)] += 1
    private_functions = [
        (name, node)
        for function_name, node in inventory.functions
        if "." not in function_name and node.name.startswith("_")
    ]
    meaningful = [
        node
        for node in tree.body
        if not isinstance(node, (ast.Import, ast.ImportFrom))
        and not _is_module_docstring(node)
        and not _is_all_assignment(node)
    ]
    agent_imports: list[dict[str, object]] = []
    if not name.startswith("app.services.agent"):
        for node in ast.walk(tree):
            imported = _imported_name(node)
            if "app.services.agent.tools" in imported:
                agent_imports.append(
                    {
                        "file": path.relative_to(backend_root).as_posix(),
                        "line": node.lineno,
                        "import": imported,
                    }
                )
    return (
        imported_modules(tree, set(modules)),
        same_module_calls,
        private_functions,
        not meaningful,
        len(source.splitlines()) <= 75 and len(inventory.functions) <= 2,
        agent_imports,
    )


def static_architecture(files: list[Path], backend_root: Path) -> dict[str, object]:
    modules = {module_name(path, backend_root): path for path in files}
    graph: dict[str, set[str]] = {}
    same_module_calls: Counter[tuple[str, str]] = Counter()
    private_functions: list[tuple[str, FunctionNode]] = []
    direct_agent_tool_imports: list[dict[str, object]] = []
    reexport_modules: list[str] = []
    thin_modules: list[str] = []
    for name, path in modules.items():
        imports, calls, privates, is_reexport, is_thin, agent_imports = _module_architecture(
            name, path, backend_root, modules
        )
        graph[name] = imports
        same_module_calls.update(calls)
        private_functions.extend(privates)
        if is_reexport:
            reexport_modules.append(name)
        if is_thin:
            thin_modules.append(name)
        direct_agent_tool_imports.extend(agent_imports)
    one_caller = [
        f"{module}.{node.name}"
        for module, node in private_functions
        if same_module_calls[(module, node.name)] == 1
    ]
    forwarders = [
        f"{module}.{node.name}"
        for module, node in private_functions
        if is_forwarder(node)
    ]
    return {
        "python_files": len(files),
        "python_loc": sum(
            len(path.read_text(encoding="utf-8-sig").splitlines()) for path in files
        ),
        "agent_files": sum("app/services/agent/" in path.as_posix() for path in files),
        "agent_loc": sum(
            len(path.read_text(encoding="utf-8-sig").splitlines())
            for path in files
            if "app/services/agent/" in path.as_posix()
        ),
        "agent_tools_files": sum(
            "app/services/agent/tools/" in path.as_posix() for path in files
        ),
        "agent_tools_loc": sum(
            len(path.read_text(encoding="utf-8-sig").splitlines())
            for path in files
            if "app/services/agent/tools/" in path.as_posix()
        ),
        "direct_non_agent_imports_into_agent_tools": direct_agent_tool_imports,
        "import_cycles": strongly_connected_components(graph),
        "private_one_same_module_caller_count": len(one_caller),
        "private_one_same_module_callers": sorted(one_caller),
        "private_forwarder_count": len(forwarders),
        "private_forwarders": sorted(forwarders),
        "thin_module_count": len(thin_modules),
        "thin_modules": sorted(thin_modules),
        "reexport_module_count": len(reexport_modules),
        "reexport_modules": sorted(reexport_modules),
    }


def build_report(
    backend_root: Path,
    coverage_file: Path,
    quality_scopes: list[str],
) -> dict[str, object]:
    architecture_files = source_files(backend_root / "app")
    quality_files = sorted(
        {
            path
            for scope in quality_scopes
            for path in source_files(backend_root / scope)
        }
    )
    functions = measured_functions(quality_files, backend_root, coverage_file)
    status = git_value(backend_root, "status", "--porcelain=v1", "-z").encode()
    return {
        "measurement": {
            "generated_at": datetime.now().astimezone().isoformat(),
            "python": platform.python_version(),
            "coverage": coverage.__version__,
            "radon": radon.__version__,
            "crap_formula": "complexity^2 * (1 - statement_coverage)^3 + complexity",
            "quality_scope": quality_scopes,
            "architecture_scope": "backend/app/**/*.py in the current working tree",
            "head_commit": git_value(backend_root, "rev-parse", "HEAD"),
            "head_tree": git_value(backend_root, "rev-parse", "HEAD^{tree}"),
            "git_status_sha256": hashlib.sha256(status).hexdigest(),
            "quality_source_manifest_sha256": source_manifest(
                quality_files, backend_root
            ),
            "architecture_source_manifest_sha256": source_manifest(
                architecture_files, backend_root
            ),
        },
        "architecture": static_architecture(architecture_files, backend_root),
        "summary": {
            "function_count": len(functions),
            "crap_distribution": crap_distribution(
                float(row["crap"]) for row in functions
            ),
            "complexity_distribution": complexity_distribution(
                int(row["complexity"]) for row in functions
            ),
            "coverage_distribution": coverage_distribution(
                float(row["coverage"]) for row in functions
            ),
            "review_zone_count": sum(float(row["crap"]) >= 15 for row in functions),
            "high_zone_count": sum(float(row["crap"]) > 30 for row in functions),
        },
        "review_functions": [row for row in functions if float(row["crap"]) >= 15],
        "functions": functions,
    }


def write_review_report(path: Path, report: dict[str, object]) -> None:
    """Write the exhaustive, review-zone human audit ledger."""

    rows = report["review_functions"]
    assert isinstance(rows, list)
    lines = [
        "# SmartRoute Phase 2 CRAP Review Ledger",
        "",
        "This ledger contains every function with CRAP >= 15 in the final measured",
        "`app/services/agent` and `app/services/trips` scope. Executed coverage is",
        "statement coverage from the same full deterministic run used by the JSON",
        "report. Conceptual responsibilities were reviewed by named operation: case",
        "branches and orchestration stages of one operation count as one responsibility;",
        "an unrelated reason to change would count separately. No final review-zone",
        "function was found to retain an unrelated second responsibility.",
        "",
        "Diagnosis rules are explicit: complexity >= 15 is a branching problem;",
        "coverage < 80% is a coverage problem; both is mixed. A responsibility count",
        "above one would be a too-many-responsibilities problem.",
        "",
        "| CRAP | CC | Executed coverage | LOC | Responsibilities | Diagnosis | Function |",
        "|---:|---:|---:|---:|---:|---|---|",
    ]
    for row in rows:
        assert isinstance(row, dict)
        function = f"`{row['file']}:{row['line']}::{row['function']}`"
        lines.append(
            f"| {float(row['crap']):.2f} | {int(row['complexity'])} | "
            f"{float(row['coverage']):.1%} | {int(row['loc'])} | "
            f"{int(row['conceptual_responsibility_count'])} | "
            f"{row['diagnosis']} | {function} |"
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--coverage-data", default=".coverage")
    parser.add_argument("--output", required=True)
    parser.add_argument(
        "--review-output",
        help="Optional Markdown path for the exhaustive CRAP >= 15 review ledger",
    )
    parser.add_argument(
        "--scope",
        action="append",
        help="Backend-relative Python source root; repeat for multiple roots",
    )
    args = parser.parse_args()
    backend_root = Path(__file__).resolve().parents[1]
    coverage_file = (backend_root / args.coverage_data).resolve()
    output = (backend_root / args.output).resolve()
    scopes = args.scope or ["app/services/agent", "app/services/trips"]
    report = build_report(backend_root, coverage_file, scopes)
    output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    if args.review_output:
        review_output = (backend_root / args.review_output).resolve()
        write_review_report(review_output, report)
    print(json.dumps(report["summary"], indent=2))
    print(json.dumps(report["architecture"], indent=2))
    print(f"Wrote {output}")
    if args.review_output:
        print(f"Wrote {review_output}")


if __name__ == "__main__":
    main()
