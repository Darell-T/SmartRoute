"""Generate docs/reference/backend-package-map.md from docstrings and imports."""

from __future__ import annotations

import ast
import re
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
APP = ROOT / "backend" / "app"
EVAL = ROOT / "backend" / "evaluation"
TESTS = ROOT / "backend" / "tests"
OUT = ROOT / "docs" / "reference" / "backend-package-map.md"

SKIP_INITS = {
    "backend/app/__init__.py",
    "backend/app/routers/__init__.py",
    "backend/app/routers/live_feed/__init__.py",
    "backend/app/services/__init__.py",
    "backend/app/services/agent/__init__.py",
    "backend/app/services/agent/model/__init__.py",
    "backend/app/services/agent/turn/__init__.py",
    "backend/app/services/agent/tools/places/__init__.py",
    "backend/app/services/agent/tools/route/__init__.py",
    "backend/app/services/agent/tools/transit/__init__.py",
    "backend/app/services/incidents/__init__.py",
    "backend/app/services/live_feed/__init__.py",
    "backend/app/services/mta/__init__.py",
    "backend/app/services/mta/static_gtfs/__init__.py",
    "backend/app/services/trips/__init__.py",
    "backend/app/services/trips/crowds/__init__.py",
    "backend/app/services/trips/preparation/__init__.py",
    "backend/app/services/trips/route_incidents/__init__.py",
}

# First-sentence ownership for modules with no module docstring.
# Derived from the module's public names and opening comments, not invented flow.
NO_DOC_OWNERS = {
    "backend/app/main.py": (
        "Loads environment and configuration, starts shared clients, registers "
        "routers, and exposes `health` and `readiness`."
    ),
    "backend/app/routers/live_feed/router.py": (
        "Owns the HTTP and WebSocket routes for live feed, service alerts, and vehicles."
    ),
    "backend/app/routers/subway.py": (
        "Owns `GET /api/subway-stops` through `subway_stops`."
    ),
    "backend/app/services/agent/model/output_projection.py": (
        "Projects model-visible place and route values through `project_model_value` "
        "and `project_presented_route`."
    ),
    "backend/app/services/agent/model/prompt.py": (
        "Owns `SINGLE_AGENT_SYSTEM_PROMPT` and `build_turn_context`."
    ),
    "backend/app/services/agent/tools/places/discover_places.py": (
        "Owns the `discover_places` tool schema and `execute`."
    ),
    "backend/app/services/agent/tools/places/present_places.py": (
        "Owns the `present_places` tool schema, `execute`, and `try_deterministic_fallback`."
    ),
    "backend/app/services/agent/tools/places/search_local_places.py": (
        "Owns Google Places search for discovery, including `execute` and `provider_search`."
    ),
    "backend/app/services/agent/tools/transit/accessibility_status.py": (
        "Owns MTA elevator and escalator status lookup through `execute`."
    ),
    "backend/app/services/agent/tools/transit/check_transit.py": (
        "Owns the `check_transit` tool schema and `execute`."
    ),
    "backend/app/services/agent/tools/transit/transit_snapshot.py": (
        "Owns transit snapshot collection through `execute` and `collect_service_status`."
    ),
    "backend/app/services/cache.py": (
        "Owns Redis-backed `cache_get` and `cache_set` with an in-memory fallback."
    ),
    "backend/app/services/directions.py": (
        "Owns Google Routes candidate fetching for trip preparation."
    ),
    "backend/app/services/geography.py": (
        "Owns NYC geocoding, `is_in_nyc`, `distance_meters`, and `find_nearest_stops`."
    ),
    "backend/app/services/live_feed/snapshot.py": (
        "Builds a rider-scoped live snapshot through `build_live_snapshot`."
    ),
    "backend/app/services/mta/alerts.py": (
        "Owns service-alert fetch and `parse_service_alerts`."
    ),
    "backend/app/services/mta/bus.py": (
        "Owns BusTime stop monitoring and bus vehicle helpers."
    ),
    "backend/app/services/mta/config.py": (
        "Owns NYC timezone, subway route colors, and `route_to_feed`."
    ),
    "backend/app/services/mta/feeds.py": (
        "Owns GTFS-realtime feed fetch and `parse_feed_message`."
    ),
    "backend/app/services/mta/static_gtfs/migration.py": (
        "Owns static GTFS Postgres migration helpers."
    ),
    "backend/app/services/mta/static_gtfs/scheduled_arrivals.py": (
        "Owns the scheduled-arrival index for static GTFS."
    ),
    "backend/app/services/mta/static_gtfs/stop_patterns.py": (
        "Owns stop-pattern lookup and `normalize_station_name`."
    ),
    "backend/app/services/mta/subway.py": (
        "Owns subway vehicle-position construction from GTFS-realtime."
    ),
    "backend/app/services/parsing.py": (
        "Owns `finite_float` and `nonnegative_int`."
    ),
    "backend/app/services/agent/tools/places/geography.py": (
        "Maps `discover_places` scope values onto canonical NYC borough geography."
    ),
    "backend/app/services/agent/tools/transit/lookup_facts.py": (
        "Owns the `lookup_facts` tool and the local `transit_facts.md` digest."
    ),
    "backend/app/services/agent/session.py": (
        "Owns conversational session state, leases, and pending continuations."
    ),
    "backend/app/runtime.py": (
        "Owns runtime-profile checks and `env_int` / `env_float`."
    ),
    "backend/app/routers/agent_chat.py": (
        "Owns `POST /api/agent/chat` SSE transport through `agent_chat`."
    ),
    "backend/app/observability.py": (
        "Owns turn and tool telemetry through `start_turn`, `finish_turn`, and `wrap_anthropic`."
    ),
}


def posix(p: Path) -> str:
    return p.relative_to(ROOT).as_posix()


def dotted_from(p: Path) -> str:
    rel = p.relative_to(APP).with_suffix("")
    parts = list(rel.parts)
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return "app." + ".".join(parts)


def first_sentence(doc: str) -> str:
    text = " ".join(doc.split())
    text = text.replace("\u2014", ". ").replace("\u2013", ". ")
    text = text.replace(" -- ", ". ")
    text = text.replace("`", "")
    # Keep one sentence.
    match = re.match(r"(.+?[.!?])(?:\s|$)", text)
    sentence = match.group(1) if match else text
    sentence = sentence.replace(";", ".")
    sentence = sentence.replace("\u201c", '"').replace("\u201d", '"')
    sentence = sentence.replace("\u2018", "'").replace("\u2019", "'")
    if not sentence.endswith("."):
        sentence += "."
    return sentence


def module_doc(p: Path) -> str:
    tree = ast.parse(p.read_text(encoding="utf-8"))
    return ast.get_docstring(tree) or ""


def parse_imports(
    text: str, src_dotted: str, is_package: bool
) -> tuple[list[tuple[str, list[str]]], list[str]]:
    tree = ast.parse(text)
    from_rows: list[tuple[str, list[str]]] = []
    module_rows: list[str] = []

    def resolved_from(node: ast.ImportFrom) -> str | None:
        if node.level == 0:
            return node.module
        if not src_dotted:
            return None
        parts = src_dotted.split(".")
        if not is_package:
            parts = parts[:-1]
        drop = node.level - 1
        if drop:
            parts = parts[: len(parts) - drop]
        if node.module:
            parts.extend(node.module.split("."))
        if not parts:
            return None
        dotted = ".".join(parts)
        return dotted if dotted.startswith("app") else None

    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            mod = resolved_from(node)
            if not mod:
                continue
            names = [alias.name for alias in node.names if alias.name != "*"]
            if names:
                from_rows.append((mod, names))
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith("app."):
                    module_rows.append(alias.name)
    for match in re.finditer(
        r"""importlib\.import_module\(\s*['"](app(?:\.[A-Za-z0-9_]+)+)['"]\s*\)""",
        text,
    ):
        module_rows.append(match.group(1))
    return from_rows, module_rows


def submodule_dotted(parent: str, name: str) -> str | None:
    rel = Path(*parent.split(".")[1:], name)
    file_path = APP / rel.with_suffix(".py")
    pkg_path = APP / rel / "__init__.py"
    if file_path.exists() or pkg_path.exists():
        return f"{parent}.{name}"
    return None


def collect_imports(paths: list[Path]) -> dict[str, dict[str, int]]:
    counts: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for path in paths:
        if "__pycache__" in path.parts:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        try:
            src_dotted = dotted_from(path)
        except ValueError:
            src_dotted = ""
        try:
            from_rows, module_rows = parse_imports(
                text, src_dotted, path.name == "__init__.py"
            )
        except SyntaxError:
            continue
        for mod, names in from_rows:
            if mod == src_dotted:
                continue
            for name in names:
                counts[mod][name] += 1
                child = submodule_dotted(mod, name)
                if child and child != src_dotted:
                    counts[child][name] += 1
        for mod in module_rows:
            if mod == src_dotted:
                continue
            counts[mod][mod.rsplit(".", 1)[-1]] += 1
    return counts


def section_for(rel: str) -> str | None:
    if rel.startswith("backend/app/routers/"):
        return "Routers"
    if rel.startswith("backend/app/services/agent/model/"):
        return "Agent model"
    if rel.startswith("backend/app/services/agent/turn/"):
        return "Agent turn"
    if rel.startswith("backend/app/services/agent/tools/places/"):
        return "Agent tools places"
    if rel.startswith("backend/app/services/agent/tools/route/"):
        return "Agent tools route"
    if rel.startswith("backend/app/services/agent/tools/transit/"):
        return "Agent tools transit"
    if rel.startswith("backend/app/services/agent/tools/"):
        return "Agent tools"
    if rel.startswith("backend/app/services/agent/"):
        return "Agent"
    if rel.startswith("backend/app/services/trips/crowds/"):
        return "Trips crowds"
    if rel.startswith("backend/app/services/trips/preparation/"):
        return "Trips preparation"
    if rel.startswith("backend/app/services/trips/route_incidents/"):
        return "Trips route incidents"
    if rel.startswith("backend/app/services/trips/"):
        return "Trips"
    if rel.startswith("backend/app/services/live_feed/"):
        return "Live feed"
    if rel.startswith("backend/app/services/incidents/"):
        return "Incidents"
    if rel.startswith("backend/app/services/mta/"):
        return "MTA"
    if rel.startswith("backend/app/services/"):
        return "Services root"
    if rel.startswith("backend/app/") and rel.count("/") == 2:
        return "App root"
    return None


SECTION_ORDER = [
    "App root",
    "Routers",
    "Services root",
    "Agent",
    "Agent model",
    "Agent turn",
    "Agent tools",
    "Agent tools places",
    "Agent tools route",
    "Agent tools transit",
    "Trips",
    "Trips crowds",
    "Trips preparation",
    "Trips route incidents",
    "Live feed",
    "Incidents",
    "MTA",
]


def ranked_names(prod: dict[str, int]) -> list[str]:
    public = [(n, c) for n, c in prod.items() if not n.startswith("_")]
    private = [(n, c) for n, c in prod.items() if n.startswith("_")]
    ordered = sorted(public, key=lambda kv: (-kv[1], kv[0])) + sorted(
        private, key=lambda kv: (-kv[1], kv[0])
    )
    return [n for n, _ in ordered[:6]]


def import_clause(dotted: str, prod: dict[str, int], tests: dict[str, int]) -> str:
    if prod:
        quoted = ", ".join(f"`{n}`" for n in ranked_names(prod))
        return f"Production importers use {quoted}."
    if tests:
        return "Imported by tests only."
    return "No production importers."


def owner_sentence(rel: str, p: Path) -> str:
    if rel in NO_DOC_OWNERS:
        return NO_DOC_OWNERS[rel]
    doc = module_doc(p)
    if doc:
        return first_sentence(doc)
    raise SystemExit(f"missing ownership sentence for {rel}")


def main() -> None:
    files = sorted(p for p in APP.rglob("*.py") if "__pycache__" not in p.parts)
    prod_paths = [
        p
        for p in list(APP.rglob("*.py")) + list(EVAL.rglob("*.py"))
        if "__pycache__" not in p.parts
    ]
    test_paths = [p for p in TESTS.rglob("*.py") if "__pycache__" not in p.parts]
    prod_imports = collect_imports(prod_paths)
    test_imports = collect_imports(test_paths)

    grouped: dict[str, list[Path]] = defaultdict(list)
    for path in files:
        rel = posix(path)
        if rel in SKIP_INITS:
            continue
        section = section_for(rel)
        if section is None:
            raise SystemExit(f"unsectioned {rel}")
        grouped[section].append(path)

    lines = [
        "# Backend package map",
        "",
        "Find the `backend/app` module that owns a behavior.",
        "The request path and owner diagram live in [`backend/ARCHITECTURE.md`](../../backend/ARCHITECTURE.md).",
        "Regenerate the module list with `rg --files backend/app -g \"*.py\"`.",
        "",
    ]

    for section in SECTION_ORDER:
        paths = grouped.get(section, [])
        if not paths:
            continue
        lines.append(f"## {section}")
        lines.append("")
        for path in paths:
            rel = posix(path)
            dotted = dotted_from(path)
            owner = owner_sentence(rel, path)
            clause = import_clause(
                dotted, prod_imports.get(dotted, {}), test_imports.get(dotted, {})
            )
            lines.append(f"- `{rel}`. {owner} {clause}")
        lines.append("")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    print("wrote", OUT)
    for section in SECTION_ORDER:
        print(f"{section}\t{len(grouped.get(section, []))}")


if __name__ == "__main__":
    main()
