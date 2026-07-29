"""Eval-only fuzzy fixture resolution.

The production replay hook (`app/services/agent/tools/__init__.py`,
`_with_fixture_replay`) keys fixtures by an exact sha256 of the canonical
JSON tool input -- deliberately strict, so a production replay run fails
loudly on drift instead of silently serving the wrong fixture. That is the
right default for production replay, but it is unusable for golden-query
evals: the real model chooses its own tool inputs freely (destination
spelling, whether it says "Costco" or "Costco Wholesale", exact ISO
seconds, etc.), so pre-naming fixture files by hash would be flaky by
construction -- a query would pass or fail depending on model phrasing we
never intended to test.

This module is the fallback resolver, and it is intentionally NOT wired
into `tools/__init__.py` (kept untouched per the harness plan). Instead it
monkeypatches `TOOL_REGISTRY` in place for the lifetime of one query run:
each tool's executor is swapped for a resolver that, per call, tries in
order:

  1. hash-exact match -- a file named `<canonical_hash(tool_input)>.json`
     in the query's `fixtures/<query_id>/<tool>/` dir (the same naming the
     production hook uses; lets a query "graduate" to exact-match once its
     fixtures were captured against a real recorded call).
  2. the query's explicit `fixtures: {tool: [file_a.json, file_b.json]}`
     mapping from golden_queries.yaml, consumed in call order (one entry
     consumed per call to that tool) -- the primary mechanism for tools
     called more than once in a query (e.g. two plan_trip legs).
  3. the single fixture file in that tool's directory, if there is exactly
     one -- covers the common case of a tool called once per query.

A miss at all three falls through to a loud `ToolResult(ok=False, ...)`,
matching the production hook's fail-loud philosophy rather than silently
serving nothing.

Every resolved (and missed) call is appended to `FuzzyResolver.call_log` --
`(tool, input, ok, data, error, fixture_filename)` -- which the runner
threads into `assertions.evaluate_all(..., call_log=...)` for the one
assertion that needs a tool's OUTPUT, not just its input: the multi-stop
`derived: leg2_departs_after_leg1_arrival_plus` check.

`AGENT_TOOL_FIXTURES_FUZZY` env var (default "1"): the runner reads this
and passes it through as `install(..., fuzzy=...)`. "1" (default) enables
steps 2/3 above. "0" disables them -- only a hash-exact match is accepted,
everything else is a loud miss. Use "0" once a query's fixtures have been
renamed to their real canonical hashes (e.g. after
`AGENT_TOOL_FIXTURES_RECORD=1` captured them against live keys) and you
want to confirm the model still produces byte-identical inputs -- a
stricter regression check than the default. Either way resolution stays
inside this module; `tools/__init__.py`'s own `AGENT_TOOL_FIXTURES` /
`AGENT_TOOL_FIXTURES_RECORD` env hook is never touched or engaged by the
eval harness.
"""

from __future__ import annotations

import contextlib
import dataclasses
import json
from pathlib import Path
from typing import Iterable

from app.services.agent.tools import TOOL_REGISTRY, ToolSpec
from app.services.agent.tools import _canonical_hash
from app.services.agent.tools._types import ToolContext, ToolResult


def _tool_dir(fixtures_root: Path, query_id: str, tool_name: str) -> Path:
    return Path(fixtures_root) / query_id / tool_name


def _list_fixture_files(tool_dir: Path) -> list[Path]:
    if not tool_dir.is_dir():
        return []
    return sorted(tool_dir.glob("*.json"))


def build_index(fixtures_root: Path, query_id: str) -> dict[str, list[str]]:
    """Scans `fixtures/<query_id>/*/*.json` and writes an `index.json`
    manifest alongside them (tool name -> sorted fixture filenames) -- a
    debugging aid, not something resolution reads back. Safe to call
    repeatedly; overwrites in place. No-ops (returns {}) if the query has
    no fixture directory at all (e.g. a T6 refusal query with zero tools)."""
    query_dir = Path(fixtures_root) / query_id
    manifest: dict[str, list[str]] = {}
    if not query_dir.is_dir():
        return manifest
    for tool_dir in sorted(p for p in query_dir.iterdir() if p.is_dir()):
        files = [p.name for p in _list_fixture_files(tool_dir)]
        if files:
            manifest[tool_dir.name] = files
    (query_dir / "index.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    return manifest


def load_fixture_file(path: Path) -> ToolResult:
    payload = json.loads(path.read_text())
    return ToolResult(
        ok=bool(payload.get("ok")), data=payload.get("data"), summary=payload.get("summary") or "", error=payload.get("error")
    )


class FuzzyResolver:
    """Per-query resolution state: tracks how many times each tool has
    been called (to walk the explicit call-order mapping and to label
    `call_log` entries), and accumulates the call log the derived
    assertion reads.

    `fuzzy=False` (the runner's `AGENT_TOOL_FIXTURES_FUZZY=0`) disables
    steps 2/3 below -- only a hash-exact match is accepted, everything else
    is a loud miss. Still goes through this same resolver (rather than the
    built-in `AGENT_TOOL_FIXTURES` env hook in tools/__init__.py) so
    `call_log` keeps working for the `derived` assertion even in strict
    mode."""

    def __init__(
        self,
        query_id: str,
        fixtures_root: Path,
        explicit_fixtures: dict[str, list[str]] | None = None,
        *,
        fuzzy: bool = True,
    ):
        self.query_id = query_id
        self.fixtures_root = Path(fixtures_root)
        self.explicit_fixtures = explicit_fixtures or {}
        self.fuzzy = fuzzy
        self._call_counts: dict[str, int] = {}
        self.call_log: list[dict] = []

    def _resolve_path(self, tool_name: str, tool_input: dict, call_index: int) -> tuple[Path | None, str]:
        tool_dir = _tool_dir(self.fixtures_root, self.query_id, tool_name)
        files = _list_fixture_files(tool_dir)

        exact_name = f"{_canonical_hash(tool_input)}.json"
        for candidate in files:
            if candidate.name == exact_name:
                return candidate, "hash-exact"

        if not self.fuzzy:
            return None, f"strict mode: no hash-exact fixture named {exact_name}"

        order = self.explicit_fixtures.get(tool_name)
        if order:
            if call_index < len(order):
                explicit_path = tool_dir / order[call_index]
                if explicit_path.is_file():
                    return explicit_path, "explicit-call-order"
            # An explicit list is declared but exhausted/misconfigured for
            # this call index -- do NOT silently fall through to the
            # single-file case, that would mask a query authoring bug.
            return None, f"explicit fixtures list for {tool_name} has no entry at call_index {call_index}"

        if len(files) == 1:
            return files[0], "single-file-fallback"

        return None, f"no fixture resolved ({len(files)} file(s) in {tool_dir})"

    def resolve(self, tool_name: str, tool_input: dict) -> ToolResult:
        call_index = self._call_counts.get(tool_name, 0)
        self._call_counts[tool_name] = call_index + 1

        path, reason = self._resolve_path(tool_name, tool_input, call_index)
        if path is None:
            result = ToolResult(
                ok=False,
                error=f"eval fixture miss: {tool_name} call #{call_index} in query '{self.query_id}' -- {reason}",
            )
            fixture_name = None
        else:
            result = load_fixture_file(path)
            fixture_name = path.name

        self.call_log.append(
            {
                "tool": tool_name,
                "call_index": call_index,
                "input": tool_input,
                "fixture": fixture_name,
                "resolution": reason,
                "ok": result.ok,
                "data": result.data,
                "error": result.error,
            }
        )
        return result


@contextlib.contextmanager
def install(
    query_id: str,
    fixtures_root: Path,
    explicit_fixtures: dict[str, list[str]] | None = None,
    *,
    fuzzy: bool = True,
):
    """Monkeypatches every `TOOL_REGISTRY` entry's executor to resolve via
    `FuzzyResolver` for the duration of the `with` block, then restores the
    original specs -- even if the block raises. Mutates the SAME dict
    object `app/services/agent/loop.py` imported (`TOOL_REGISTRY` is a
    module-level dict, not reassigned here), so the loop picks up the
    patched executors without needing a reload.
    """
    build_index(fixtures_root, query_id)
    resolver = FuzzyResolver(query_id, fixtures_root, explicit_fixtures, fuzzy=fuzzy)
    originals: dict[str, ToolSpec] = dict(TOOL_REGISTRY)

    def _make_executor(name: str):
        async def _executor(tool_input: dict, ctx: ToolContext) -> ToolResult:
            return resolver.resolve(name, tool_input)

        return _executor

    try:
        for name, spec in list(TOOL_REGISTRY.items()):
            TOOL_REGISTRY[name] = dataclasses.replace(spec, executor=_make_executor(name))
        yield resolver
    finally:
        TOOL_REGISTRY.clear()
        TOOL_REGISTRY.update(originals)


def iter_fixture_files(fixtures_root: Path, query_id: str) -> Iterable[tuple[str, Path]]:
    """Yields (tool_name, path) for every fixture file under a query's
    directory -- used by `--validate` to check every file that could ever
    be served (not just ones an explicit mapping names), since the
    single-file fallback can serve a fixture no query text points at
    directly."""
    query_dir = Path(fixtures_root) / query_id
    if not query_dir.is_dir():
        return
    for tool_dir in sorted(p for p in query_dir.iterdir() if p.is_dir()):
        for path in _list_fixture_files(tool_dir):
            yield tool_dir.name, path
