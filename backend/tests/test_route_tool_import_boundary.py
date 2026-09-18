"""Import-boundary proof for the active conversational route tools.

``prepare_route_options`` / ``present_route`` are the canonical
conversational route surface. Their import graph (including the tool
registry) must never load the evaluation-only advisor modules.
The probes run in a fresh interpreter so earlier in-process imports cannot
mask a regression, and they assert the loaded-module set rather than a brittle
substring grep against preparation module names.
"""

from __future__ import annotations

import importlib
import multiprocessing
import sys
import unittest

_BANNED_MODULES = {
    "evaluation.route_intelligence.advisor_context",
    "evaluation.route_intelligence.advisor",
}


def _probe_modules(
    module_names: tuple[str, ...],
    banned: tuple[str, ...],
    result_queue,
) -> None:
    for name in module_names:
        importlib.import_module(name)
    result_queue.put(sorted(name for name in sys.modules if name in banned))


def _run_probe(module_names: tuple[str, ...], banned: set[str]) -> list[str]:
    context = multiprocessing.get_context("spawn")
    result_queue = context.Queue()
    process = context.Process(
        target=_probe_modules,
        args=(module_names, tuple(sorted(banned)), result_queue),
    )
    process.start()
    process.join(120)
    loaded = result_queue.get(timeout=1)
    result_queue.close()
    assert process.exitcode == 0, f"probe failed with loaded={loaded}"
    return loaded


class RouteToolImportBoundaryTests(unittest.TestCase):
    def test_shared_route_preparation_imports_without_agent_modules(self):
        """Neutral preparation and projection do not pull agent adapters."""

        loaded = _run_probe(
            (
                "app.services.trips.direct_plan",
                "app.services.trips.preparation.prepare",
                "app.services.trips.preparation.constraints",
                "app.services.trips.preparation.evidence",
                "app.services.trips.preparation.multi_stop",
            ),
            {"app.services.agent", "app.services.agent.tools"},
        )
        assert loaded == []

    def test_conversational_route_tools_import_without_evaluation_stack(self):
        """The registry plus both active tools never load evaluation code."""
        loaded = _run_probe(
            (
                "app.services.agent.tools",
                "app.services.agent.tools.route.prepare_route_options",
                "app.services.agent.tools.route.present_route",
                "app.services.agent.tools.route.route_projection",
                "app.services.agent.tools.route.preparation_adapter",
            ),
            _BANNED_MODULES,
        )
        assert loaded == []

    def test_live_map_graph_stays_projection_and_advisor_free(self):
        """/api/trip loads neither the advisor stack nor conversational projection."""
        loaded = _run_probe(
            ("app.routers.trips",),
            {
                *_BANNED_MODULES,
                "app.services.agent",
                "app.services.agent.tools",
                "app.services.agent.tools.route.route_projection",
            },
        )
        assert loaded == []


if __name__ == "__main__":
    unittest.main()
