from __future__ import annotations

from collections import Counter
from pathlib import Path
import os
import re
import subprocess

ROOT = Path(__file__).resolve().parents[2]
AUDIT = Path(__file__).resolve().parent
BACKEND = ROOT / "backend"
SUFFIX = "after-wave5"


def run(cmd: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, cwd=cwd or ROOT, capture_output=True, text=True)


def write(name: str, text: str) -> None:
    (AUDIT / name).write_text(text, encoding="utf-8")


def main() -> None:
    os.chdir(BACKEND)
    from complexipy import file_complexity

    rows = [
        (f.complexity, str(p).replace("\\", "/"), f.name)
        for p in Path("app").rglob("*.py")
        for f in file_complexity(str(p)).functions
        if f.complexity > 10
    ]
    rows.sort(reverse=True)
    write(
        f"cognitive-{SUFFIX}.txt",
        "\n".join([str(len(rows))] + [f"{c} {p} {n}" for c, p, n in rows]) + "\n",
    )
    agent_cog = [r for r in rows if "/services/agent/" in r[1]]
    other_cog = [r for r in rows if "/services/agent/" not in r[1]]
    write(
        f"cognitive-{SUFFIX}-summary.txt",
        f"total={len(rows)}\nagent={len(agent_cog)}\nelsewhere={len(other_cog)}\n",
    )

    rg = run(
        ["rg", "-o", "--no-filename", r"^(?:async )?def (_?[a-z][a-z0-9_]*)", "-r", "$1", "app"],
        cwd=BACKEND,
    )
    names = [ln.strip() for ln in rg.stdout.splitlines() if ln.strip()]
    ctr = Counter(names)
    dup_lines = ["count\tname"]
    for name, count in sorted(ctr.items(), key=lambda x: (-x[1], x[0])):
        if count >= 2:
            dup_lines.append(f"{count}\t{name}")
    write(f"duplicate-names-{SUFFIX}.txt", "\n".join(dup_lines) + "\n")

    rg2 = run(
        [
            "rg",
            "-o",
            "--no-filename",
            r"\b(?!self|cls)([a-z][a-z0-9_]*)\._([a-z][a-z0-9_]*)\(",
            "-r",
            r"$1._$2",
            "app",
            "--pcre2",
        ],
        cwd=BACKEND,
    )
    priv = [ln.strip() for ln in rg2.stdout.splitlines() if ln.strip()]
    pctr = Counter(priv)
    priv_lines = ["count\tname"]
    for name, count in sorted(pctr.items(), key=lambda x: (-x[1], x[0])):
        priv_lines.append(f"{count}\t{name}")
    write(f"cross-module-private-{SUFFIX}.txt", "\n".join(priv_lines) + "\n")

    rg3 = run(["rg", "-c", r"print\(", "backend/app"], cwd=ROOT)
    total_prints = 0
    agent_prints = 0
    for line in rg3.stdout.splitlines():
        if ":" not in line:
            continue
        path, n_s = line.rsplit(":", 1)
        n = int(n_s)
        total_prints += n
        if "services/agent" in path.replace("\\", "/"):
            agent_prints += n
    write(
        f"print-count-{SUFFIX}.txt",
        rg3.stdout
        + f"\nTOTAL={total_prints}\nAGENT={agent_prints}\nELSEWHERE={total_prints - agent_prints}\n",
    )

    baseline_rg = run(["rg", "-n", r'"file": "backend/', "quality/baseline.json"], cwd=ROOT)
    baseline_files: list[str] = []
    for line in baseline_rg.stdout.splitlines():
        match = re.search(r'"file": "(backend/[^"]+)"', line)
        if match:
            baseline_files.append(match.group(1))
    write(
        f"baseline-count-{SUFFIX}.txt",
        f"Count : {len(baseline_files)}\nunique_files : {len(set(baseline_files))}\n"
        + "\n".join(sorted(set(baseline_files)))
        + "\n",
    )

    loc_lines = []
    pkgs = [
        "backend/app/services/agent",
        "backend/app/services/trips",
        "backend/app/services/incidents",
        "backend/app/services/live_feed",
        "backend/app/services/mta",
        "backend/app/routers",
    ]
    for pkg in pkgs:
        total = 0
        for py in Path(ROOT / pkg).rglob("*.py"):
            if "__pycache__" in str(py):
                continue
            total += sum(1 for _ in py.open(encoding="utf-8", errors="replace"))
        loc_lines.append(f"{pkg}\t{total}")
    write(f"loc-{SUFFIX}.txt", "\n".join(loc_lines) + "\n")
    print("done")
    print("cognitive", len(rows), "agent", len(agent_cog), "elsewhere", len(other_cog))
    print("\n".join(loc_lines))
    print("baseline", len(baseline_files))


if __name__ == "__main__":
    main()
