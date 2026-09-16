from __future__ import annotations

import ast
import subprocess
from pathlib import Path

ROOT = Path(r"C:\Users\19293\jarvis\jarvis-design\.worktrees\damn-lines-integration")
AGENT = ROOT / "backend" / "app" / "services" / "agent"
OUT = ROOT / ".audit" / "backend-refactor" / "dead-private-names.tsv"


def top_level_privates(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: list[str] = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and node.name.startswith("_") and not node.name.startswith("__"):
            names.append(node.name)
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id.startswith("_") and not target.id.startswith("__"):
                    names.append(target.id)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            if node.target.id.startswith("_") and not node.target.id.startswith("__"):
                names.append(node.target.id)
    return names


def hits(name: str) -> list[str]:
    r = subprocess.run(
        ["rg", "-n", rf"\b{name}\b", "backend"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    return [ln for ln in r.stdout.splitlines() if ln.strip()]


def main() -> None:
    rows = ["file\tname\thit_count\tdecision"]
    seen: set[str] = set()
    for path in sorted(AGENT.rglob("*.py")):
        if "__pycache__" in str(path):
            continue
        rel = path.relative_to(ROOT).as_posix()
        for name in top_level_privates(path):
            key = f"{rel}:{name}"
            if key in seen:
                continue
            seen.add(key)
            found = hits(name)
            decision = "delete" if len(found) == 1 else "keep"
            rows.append(f"{rel}\t{name}\t{len(found)}\t{decision}")
            if decision == "delete":
                print(f"DELETE {rel} {name} hits={len(found)}", flush=True)
    OUT.write_text("\n".join(rows) + "\n", encoding="utf-8")
    deleted = [r for r in rows if r.endswith("\tdelete")]
    print(f"delete_candidates={len(deleted)} scanned={len(rows)-1}")


if __name__ == "__main__":
    main()
