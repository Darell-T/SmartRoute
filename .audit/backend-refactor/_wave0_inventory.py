from __future__ import annotations

from collections import Counter
from pathlib import Path
import os
import re
import subprocess

ROOT = Path(__file__).resolve().parents[2]
AUDIT = Path(__file__).resolve().parent
BACKEND = ROOT / "backend"


def run(cmd: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, cwd=cwd or ROOT, capture_output=True, text=True)


def write(name: str, text: str) -> None:
    (AUDIT / name).write_text(text, encoding="utf-8")


def extract_added_defs(diff_text: str) -> list[tuple[str, str]]:
    rows: list[tuple[str, str]] = []
    current_file = ""
    pattern = re.compile(r"^\+(?:async )?def ([A-Za-z_][A-Za-z0-9_]*)|^\+class ([A-Za-z_][A-Za-z0-9_]*)")
    file_pat = re.compile(r"^\+\+\+ b/(.+)$")
    for line in diff_text.splitlines():
        mfile = file_pat.match(line)
        if mfile:
            current_file = mfile.group(1)
            continue
        if line.startswith("+++"):
            continue
        m = pattern.match(line)
        if not m:
            continue
        name = m.group(1) or m.group(2)
        rows.append((current_file, name))
    return rows


def count_hits(name: str) -> tuple[int, int, int, int]:
    pattern = rf"\b{re.escape(name)}\b"
    app = run(["rg", "-l", pattern, "backend/app"])
    tests = run(["rg", "-l", pattern, "backend/tests"])
    evaluation = run(["rg", "-l", pattern, "backend/evaluation"])
    all_hits = run(["rg", "-n", pattern, "backend/app", "backend/tests", "backend/evaluation"])
    app_files = [ln for ln in app.stdout.splitlines() if ln.strip()]
    test_files = [ln for ln in tests.stdout.splitlines() if ln.strip()]
    eval_files = [ln for ln in evaluation.stdout.splitlines() if ln.strip()]
    hit_lines = [ln for ln in all_hits.stdout.splitlines() if ln.strip()]
    return len(app_files), len(test_files), len(eval_files), len(hit_lines)


def classify(name: str, app_files: int, hit_lines: int, added_in: str) -> str:
    if not name.startswith("_"):
        return "public"
    if hit_lines <= 1:
        return "delete-candidate"
    if app_files <= 1 and hit_lines >= 2:
        return "inline-candidate-or-same-file"
    return "multi-caller"


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
        "cognitive-fixed-point.txt",
        "\n".join([str(len(rows))] + [f"{c} {p} {n}" for c, p, n in rows]) + "\n",
    )
    agent_cog = [r for r in rows if "/services/agent/" in r[1]]
    other_cog = [r for r in rows if "/services/agent/" not in r[1]]
    write(
        "cognitive-fixed-point-summary.txt",
        f"total={len(rows)}\nagent={len(agent_cog)}\nelsewhere={len(other_cog)}\n",
    )

    rg = run(["rg", "-o", "--no-filename", r"^(?:async )?def (_?[a-z][a-z0-9_]*)", "-r", "$1", "app"], cwd=BACKEND)
    names = [ln.strip() for ln in rg.stdout.splitlines() if ln.strip()]
    ctr = Counter(names)
    dup_lines = ["count\tname"]
    for name, count in sorted(ctr.items(), key=lambda x: (-x[1], x[0])):
        if count >= 2:
            dup_lines.append(f"{count}\t{name}")
    write("duplicate-names-fixed-point.txt", "\n".join(dup_lines) + "\n")

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
    write("cross-module-private-fixed-point.txt", "\n".join(priv_lines) + "\n")

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
        "print-count-fixed-point.txt",
        rg3.stdout + f"\nTOTAL={total_prints}\nAGENT={agent_prints}\nELSEWHERE={total_prints - agent_prints}\n",
    )

    ranges = [
        ("batch6e", "2298e32..ae27211", "backend/app/services/agent"),
        ("batch5", "22f6f0d^..22f6f0d", "backend/app"),
        ("batch6c", "c8a0381..676ff13", "backend/app"),
        ("batch6b", "c8a0381^..c8a0381", "backend/app/services/trips"),
        ("batch6A", "140495a^..140495a", "backend/app"),
    ]
    tsv = [
        "batch\tfile\tname\tapp_files\ttest_files\teval_files\thit_lines\tclassification"
    ]
    seen: set[tuple[str, str, str]] = set()
    for batch, git_range, path in ranges:
        diff = run(["git", "diff", git_range, "--", path], cwd=ROOT)
        added = extract_added_defs(diff.stdout)
        for file_path, name in added:
            key = (batch, file_path, name)
            if key in seen:
                continue
            seen.add(key)
            app_files, test_files, eval_files, hit_lines = count_hits(name)
            kind = classify(name, app_files, hit_lines, file_path)
            tsv.append(
                f"{batch}\t{file_path}\t{name}\t{app_files}\t{test_files}\t{eval_files}\t{hit_lines}\t{kind}"
            )
            print(f"{batch}\t{name}\t{hit_lines}\t{kind}", flush=True)
    write("batch-added-names.tsv", "\n".join(tsv) + "\n")

    stdout_rg = run(["rg", "-n", r"capsys|capfd|builtins.print", "backend/tests"], cwd=ROOT)
    write(
        "stdout-assertions-fixed-point.txt",
        stdout_rg.stdout if stdout_rg.stdout else "NO_MATCHES\n",
    )

    seam_names = [
        "loop.client",
        "loop.TurnTrace",
        "loop.run_agent_turn",
        "_tools_for_state",
        "agent_policy",
        "budget",
        "evaluate_simple_arithmetic",
        "_sanitize_rider_text",
        "TOOL_REGISTRY",
        "turn_stream",
        "_messages_from_history",
        "_build_stream_kwargs",
        "_web_search_tool",
        "TurnToolLedger",
        "public_surface",
    ]
    seam_lines = ["name\tcount"]
    combined = run(
        [
            "rg",
            "-n",
            r"loop\.(client|TurnTrace|run_agent_turn|_tools_for_state|agent_policy|budget|evaluate_simple_arithmetic|_sanitize_rider_text|TOOL_REGISTRY|turn_stream|_messages_from_history|_build_stream_kwargs|_web_search_tool|TurnToolLedger|public_surface)",
            "backend/tests",
        ],
        cwd=ROOT,
    )
    write("test-seams-fixed-point.txt", combined.stdout)
    for name in seam_names:
        counted = run(["rg", "-c", "-F", name, "backend/tests"], cwd=ROOT)
        total = 0
        for line in counted.stdout.splitlines():
            if ":" not in line:
                continue
            total += int(line.rsplit(":", 1)[1])
        seam_lines.append(f"{name}\t{total}")
    write("test-seams-counts-fixed-point.txt", "\n".join(seam_lines) + "\n")

    baseline_rg = run(
        ["rg", "-n", r'"file": "backend/', "quality/baseline.json"],
        cwd=ROOT,
    )
    baseline_files: list[str] = []
    for line in baseline_rg.stdout.splitlines():
        m = re.search(r'"file": "(backend/[^"]+)"', line)
        if m:
            baseline_files.append(m.group(1))
    write(
        "baseline-count-fixed-point.txt",
        f"Count : {len(baseline_files)}\nunique_files : {len(set(baseline_files))}\n"
        + "\n".join(sorted(set(baseline_files)))
        + "\n",
    )

    plan_locked = [
        "backend/app/services/agent/session.py",
        "backend/app/services/agent/discovery_store.py",
        "backend/app/services/agent/profile.py",
        "backend/app/services/agent/presented_entity_registry.py",
        "backend/app/services/agent/tool_input_policy.py",
        "backend/app/services/agent/model/stream.py",
        "backend/app/services/agent/turn/stream.py",
        "backend/app/services/agent/turn/tool_round.py",
        "backend/app/services/agent/turn/contract.py",
        "backend/app/services/agent/turn/completion.py",
        "backend/app/services/agent/tools/__init__.py",
        "backend/app/services/agent/tools/complete_turn.py",
        "backend/app/services/agent/tools/location_resolution.py",
        "backend/app/services/agent/tools/places/damn_lines.py",
        "backend/app/services/agent/tools/places/discover_places.py",
        "backend/app/services/agent/tools/places/present_places.py",
        "backend/app/services/agent/tools/route/present_route.py",
        "backend/app/services/agent/tools/route/present_route_state.py",
        "backend/app/services/agent/tools/route/present_route_commit.py",
        "backend/app/services/agent/tools/route/prepare_route_options.py",
        "backend/app/services/agent/tools/route/prepare_route_branches.py",
        "backend/app/services/agent/tools/route/route_projection.py",
        "backend/app/services/agent/tools/transit/check_transit.py",
        "backend/app/services/agent/tools/transit/check_area_conditions.py",
        "backend/app/services/agent/tools/transit/transit_snapshot.py",
        "backend/app/services/agent/tools/transit/evidence_projection.py",
        "backend/app/services/agent/tools/transit/evidence_binding.py",
        "backend/app/services/agent/tools/transit/evidence_matching.py",
        "backend/app/services/agent/tools/transit/lookup_facts.py",
        "backend/app/services/agent/tools/transit/lookup_arrivals.py",
        "backend/app/services/agent/tools/transit/lookup_arrivals_bus.py",
        "backend/app/main.py",
        "backend/app/observability.py",
        "backend/app/routers/trips.py",
        "backend/app/routers/live_feed/ticket.py",
        "backend/app/services/admission.py",
        "backend/app/services/live_feed/snapshot.py",
        "backend/app/services/live_feed/vehicle_enrichment.py",
        "backend/app/services/incidents/ny511.py",
        "backend/app/services/incidents/official.py",
        "backend/app/services/incidents/normalization.py",
        "backend/app/services/incidents/scout_normalization.py",
        "backend/app/services/mta/static_gtfs/stop_patterns.py",
        "backend/app/services/trips/scoring.py",
        "backend/app/services/trips/selection_decision.py",
        "backend/app/services/trips/location.py",
        "backend/app/services/trips/direct_plan.py",
        "backend/app/services/trips/preparation/input.py",
        "backend/app/services/trips/preparation/evidence.py",
        "backend/app/services/trips/route_incidents/index_adapter.py",
        "backend/app/services/trips/route_incidents/context.py",
        "backend/app/services/trips/route_incidents/association.py",
        "backend/app/services/trips/crowds/event_provider.py",
        "backend/app/services/trips/crowds/event.py",
        "backend/app/services/trips/crowds/hotspots.py",
        "backend/app/services/trips/crowds/search_normalization.py",
        "backend/app/services/trips/crowds/search_provider.py",
    ]
    measured_locked = set()
    for path in set(baseline_files):
        measured_locked.add(path.replace("\\", "/"))
    for _, rel, _ in rows:
        measured_locked.add("backend/" + rel)
    extra = sorted(measured_locked - set(plan_locked))
    missing = sorted(set(plan_locked) - measured_locked)
    write(
        "move-lock-diff-fixed-point.txt",
        "measured_count="
        + str(len(measured_locked))
        + "\nplan_count="
        + str(len(plan_locked))
        + "\nextra_vs_plan:\n"
        + "\n".join(extra)
        + "\nmissing_vs_plan:\n"
        + "\n".join(missing)
        + "\nmeasured:\n"
        + "\n".join(sorted(measured_locked))
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
    write("loc-fixed-point.txt", "\n".join(loc_lines) + "\n")

    print("done")


if __name__ == "__main__":
    main()
