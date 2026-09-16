from __future__ import annotations

import ast
from pathlib import Path

from complexipy import file_complexity

ROOT = Path(r"C:\Users\19293\jarvis\jarvis-design\.worktrees\damn-lines-integration")
TSV = ROOT / ".audit" / "backend-refactor" / "batch-added-names.tsv"
BASELINE = ROOT / "quality" / "baseline.json"
OUT = ROOT / ".audit" / "backend-refactor" / "inline-candidates.tsv"


def baseline_ids() -> set[tuple[str, str]]:
    text = BASELINE.read_text(encoding="utf-8")
    ids: set[tuple[str, str]] = set()
    for line in text.splitlines():
        if "python:backend/app/services/agent/" not in line:
            continue
        # "id": "python:backend/app/services/agent/foo.py:name#0"
        start = line.find("python:")
        if start < 0:
            continue
        token = line[start:].split('"')[0]
        parts = token.split(":")
        if len(parts) < 3:
            continue
        path = parts[1]
        fn = parts[2].split("#")[0]
        ids.add((path.replace("\\", "/"), fn))
    return ids


class Indexer(ast.NodeVisitor):
    def __init__(self, source: str) -> None:
        self.source = source
        self.functions: dict[str, ast.AST] = {}
        self.calls: list[tuple[str, str, int]] = []
        self._stack: list[str] = []

    def _name(self, node: ast.AST) -> str:
        return node.name

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._visit_fn(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._visit_fn(node)

    def _visit_fn(self, node: ast.AST) -> None:
        qual = node.name if not self._stack else f"{self._stack[-1]}.{node.name}"
        self.functions[node.name] = node
        self.functions[qual] = node
        self._stack.append(qual)
        self.generic_visit(node)
        self._stack.pop()

    def visit_Call(self, node: ast.Call) -> None:
        name = None
        if isinstance(node.func, ast.Name):
            name = node.func.id
        elif isinstance(node.func, ast.Attribute):
            name = node.func.attr
        if name and self._stack:
            self.calls.append((self._stack[-1], name, node.lineno))
        self.generic_visit(node)


def complexity_map(path: Path) -> dict[str, int]:
    rows = {}
    try:
        for fn in file_complexity(str(path)).functions:
            rows[fn.name] = fn.complexity
            short = fn.name.split("::")[-1]
            rows[short] = fn.complexity
    except Exception as exc:
        rows["__error__"] = str(exc)
    return rows


def main() -> None:
    locked_fns = baseline_ids()
    lines = ["file\tname\thit_lines\tcaller\tcallee_cc\tcaller_cc\tbaselined\tdecision\treason"]
    seen: set[tuple[str, str]] = set()
    for raw in TSV.read_text(encoding="utf-8").splitlines()[1:]:
        parts = raw.split("\t")
        if len(parts) < 8:
            continue
        batch, file_path, name, app_files, test_files, eval_files, hit_lines, kind = parts[:8]
        if not file_path.startswith("backend/app/services/agent/"):
            continue
        if not name.startswith("_"):
            continue
        key = (file_path, name)
        if key in seen:
            continue
        seen.add(key)
        if name in {"_mapping_facts"}:
            lines.append(f"{file_path}\t{name}\t{hit_lines}\t\t\t\tno\tskip\talready deleted in w1-u3")
            continue
        if int(app_files) != 1:
            lines.append(f"{file_path}\t{name}\t{hit_lines}\t\t\t\tno\tleave\tnot single production file")
            continue
        path = ROOT / file_path
        src = path.read_text(encoding="utf-8")
        tree = ast.parse(src)
        idx = Indexer(src)
        idx.visit(tree)
        callers = [c for c in idx.calls if c[1] == name]
        callee_node = idx.functions.get(name)
        if callee_node is None:
            lines.append(f"{file_path}\t{name}\t{hit_lines}\t\t\t\tno\tskip\tdefinition not found")
            continue
        if len(callers) != 1:
            lines.append(
                f"{file_path}\t{name}\t{hit_lines}\t{';'.join(c[0] for c in callers)}\t\t\tno\tleave\t{len(callers)} same-file callers"
            )
            continue
        caller_qual = callers[0][0]
        caller_short = caller_qual.split(".")[-1]
        cmap = complexity_map(path)
        callee_cc = cmap.get(name, cmap.get(f"{caller_qual.split('.')[0]}::{name}", ""))
        caller_cc = cmap.get(caller_qual.replace(".", "::"), cmap.get(caller_short, ""))
        baselined = (file_path.replace("\\", "/"), name) in locked_fns or (
            file_path.replace("\\", "/"),
            caller_short,
        ) in locked_fns
        decision = "try"
        reason = "one caller"
        if (file_path.replace("\\", "/"), name) in locked_fns:
            decision, reason = "leave", "callee in baseline"
        elif isinstance(caller_cc, int) and caller_cc > 10:
            decision, reason = "leave", "caller already above 10"
        elif isinstance(caller_cc, int) and isinstance(callee_cc, int) and caller_cc + max(callee_cc - 1, 0) > 10:
            decision, reason = "leave", "inlining would likely exceed 10"
        elif isinstance(caller_cc, int) and caller_cc == 10 and isinstance(callee_cc, int) and callee_cc > 0:
            decision, reason = "leave", "caller at 10"
        lines.append(
            f"{file_path}\t{name}\t{hit_lines}\t{caller_qual}\t{callee_cc}\t{caller_cc}\t{str(baselined).lower()}\t{decision}\t{reason}"
        )
    OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    try_rows = [ln for ln in lines if "\ttry\t" in ln]
    leave_rows = [ln for ln in lines if "\tleave\t" in ln]
    print(f"try={len(try_rows)} leave={len(leave_rows)} total={len(lines)-1}")


if __name__ == "__main__":
    main()
