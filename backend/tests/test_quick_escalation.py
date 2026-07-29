from __future__ import annotations

import unittest

from app.services.agent.quick_escalation import (
    effectively_tied_scores,
    reason_for_tool_result,
)
from app.services.agent.tools._types import ToolResult


class QuickEscalationPolicyTests(unittest.TestCase):
    def test_ordinary_success_does_not_escalate(self):
        result = ToolResult(ok=True, data={"candidates": [{"card_id": "one"}]})
        self.assertIsNone(reason_for_tool_result("plan_trip", result, required=True))

    def test_unresolved_and_ambiguous_places_are_deterministic(self):
        unresolved = ToolResult(ok=False, error="could not resolve the destination")
        ambiguous = ToolResult(
            ok=True,
            data={"source_status": "stop_not_resolved", "ambiguity": [{}, {}]},
        )
        self.assertEqual(
            reason_for_tool_result("plan_trip", unresolved, required=True),
            "unresolved_place",
        )
        self.assertEqual(
            reason_for_tool_result("lookup_arrivals", ambiguous, required=True),
            "ambiguous_station_or_destination",
        )

    def test_only_effective_final_score_ties_escalate(self):
        self.assertTrue(
            effectively_tied_scores([{"score": 20.0}, {"score": 20.8}])
        )
        self.assertFalse(
            effectively_tied_scores([{"score": 20.0}, {"score": 22.0}])
        )
        tied = ToolResult(
            ok=True,
            data={"quick_escalation_reason": "effectively_tied_final_scores"},
        )
        self.assertEqual(
            reason_for_tool_result("plan_trip", tied, required=True),
            "effectively_tied_final_scores",
        )

    def test_optional_failure_does_not_escalate(self):
        failed = ToolResult(ok=False, error="provider failed")
        self.assertIsNone(
            reason_for_tool_result("event_lookup", failed, required=False)
        )


if __name__ == "__main__":
    unittest.main()
