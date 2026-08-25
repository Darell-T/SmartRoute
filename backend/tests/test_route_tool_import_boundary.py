"""Import-boundary proof for the active conversational route tools.

``prepare_route_options`` / ``present_route`` are the canonical
conversational route surface. Their import graph (including the tool
registry) must never load the evaluation-only advisor or shadow modules.
The probes run in a fresh interpreter so earlier in-process imports cannot
mask a regression, and they assert the loaded-module set rather than a brittle
substring grep against preparation module names.
"""

from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent

_BANNED_MODULES = {
    "evaluation.route_intelligence.advisor_context",
    "evaluation.route_intelligence.advisor",
    "evaluation.route_intelligence.shadow",
    "evaluation.route_intelligence.trip_shadow",
}

_PROBE_TEMPLATE = (
    "import sys\n"
    "{imports}\n"
    "banned = {banned!r}\n"
    "loaded = sorted(name for name in sys.modules if name in banned)\n"
    "if loaded:\n"
    "    print('LOADED:' + ','.join(loaded))\n"
    "    sys.exit(1)\n"
    "print('CLEAN')\n"
)


def _run_probe(imports: list[str], banned: set[str]) -> subprocess.CompletedProcess:
    probe = _PROBE_TEMPLATE.format(
        imports="\n".join(imports),
        banned=sorted(banned),
    )
    return subprocess.run(
        [sys.executable, "-c", probe],
        cwd=str(BACKEND_DIR),
        capture_output=True,
        text=True,
        timeout=120,
    )


class RouteToolImportBoundaryTests(unittest.TestCase):
    def test_shared_route_preparation_imports_without_agent_modules(self):
        """Neutral preparation and projection do not pull agent adapters."""

        completed = _run_probe(
            imports=[
                "from app.services.trips import direct_plan  # noqa: F401",
                "from app.services.trips.preparation import prepare  # noqa: F401",
                "from app.services.trips.preparation import constraints  # noqa: F401",
                "from app.services.trips.preparation import evidence  # noqa: F401",
                "from app.services.trips.preparation import multi_stop  # noqa: F401",
            ],
            banned={"app.services.agent", "app.services.agent.tools"},
        )
        self.assertEqual(
            completed.returncode,
            0,
            msg=f"agent modules loaded by shared trips code: {completed.stdout}{completed.stderr}",
        )
        self.assertIn("CLEAN", completed.stdout)

    def test_conversational_route_tools_import_without_evaluation_stack(self):
        """The registry plus both active tools never load evaluation code."""
        completed = _run_probe(
            imports=[
                "from app.services.agent.tools import TOOLS  # noqa: F401",
                "from app.services.agent.tools.route import prepare_route_options  # noqa: F401",
                "from app.services.agent.tools.route import present_route  # noqa: F401",
                "from app.services.agent.tools.route import route_projection  # noqa: F401",
                "from app.services.agent.tools.route import preparation_adapter  # noqa: F401",
            ],
            banned=_BANNED_MODULES,
        )
        self.assertEqual(
            completed.returncode,
            0,
            msg=f"evaluation modules loaded: {completed.stdout}{completed.stderr}",
        )
        self.assertIn("CLEAN", completed.stdout)

    def test_live_map_graph_stays_projection_and_advisor_free(self):
        """/api/trip loads neither the advisor stack nor conversational projection."""
        completed = _run_probe(
            imports=[
                "import app.routers.trips  # noqa: F401",
            ],
            banned={
                *_BANNED_MODULES,
                "app.services.agent",
                "app.services.agent.tools",
                "app.services.agent.tools.route.route_projection",
            },
        )
        self.assertEqual(
            completed.returncode,
            0,
            msg=f"advisor/projection modules loaded: {completed.stdout}{completed.stderr}",
        )
        self.assertIn("CLEAN", completed.stdout)


if __name__ == "__main__":
    unittest.main()
