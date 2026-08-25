"""Rider-copy regressions at the canonical agent plan-trip projection boundary."""

from __future__ import annotations

import unittest

from app.services.agent.tools.route import route_projection


class IncidentCoverageProjectionTests(unittest.TestCase):
    @staticmethod
    def _project(prose: str, metadata: dict | None) -> str:
        return route_projection.passenger_explanation(prose, metadata or {})

    def test_complete_incident_scan_does_not_add_an_incomplete_disclosure(self):
        explanation = self._project(
            "The Q is the faster option with one transfer.",
            {"status": "complete"},
        )

        self.assertEqual(explanation, "The Q is the faster option with one transfer.")

    def test_every_noncomplete_or_absent_incident_status_gets_one_disclosure(self):
        statuses = (
            None,
            "not_started",
            "partial",
            "timeout",
            "failed",
            "disabled",
            "unavailable",
            "unknown",
        )
        for status in statuses:
            with self.subTest(status=status):
                metadata = {} if status is None else {"status": status}
                explanation = self._project("Take the Q in about 20 minutes.", metadata)
                self.assertEqual(
                    explanation.casefold().count("incident coverage is incomplete"),
                    1,
                )

    def test_existing_incomplete_disclosure_is_not_duplicated(self):
        explanation = self._project(
            "Take the Q. Current incident coverage is incomplete, so allow extra time.",
            {"status": "timeout"},
        )

        self.assertEqual(
            explanation.casefold().count("incident coverage is incomplete"),
            1,
        )

    def test_single_leg_preserves_truthful_model_disclosure_variant(self):
        explanation = self._project(
            "Take the Q. Incident information was unavailable.",
            {"status": "timeout"},
        )

        self.assertIn("Incident information was unavailable.", explanation)

    def test_incomplete_scan_replaces_unsafe_all_clear_with_grounded_fallback(self):
        explanation = self._project(
            "No active incidents are affecting the Q. Incident coverage is incomplete.",
            {"status": "partial"},
        )

        self.assertNotIn("no active incidents", explanation.casefold())
        self.assertEqual(
            explanation,
            "I found the best available route from the current transit options. "
            "Current incident coverage is incomplete, so allow extra time.",
        )
