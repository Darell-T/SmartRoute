from __future__ import annotations

import copy
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from evaluation.route_intelligence import trip_shadow
from evaluation.route_intelligence.shadow import (
    CounterfactualBaselineEvaluation,
    ShadowEvaluationStatus,
)


class TripShadowIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_disabled_default_never_evaluates_and_returns_same_object(self):
        displayed = {"selected_route_index": 1, "route": [{"private": "geometry"}]}
        evaluator = AsyncMock()
        with patch.dict(os.environ, {}, clear=True):
            result = await trip_shadow.run_trip_shadow(
                displayed,
                baseline_evaluator=evaluator,
                production_route_id="candidate-1",
                production_status=ShadowEvaluationStatus.COMPLETE,
                candidate_summaries=[],
                source_counts={},
                incident_count=0,
                scan_status="complete",
                snapshot_status="fresh",
                intelligence_latency_ms=10,
            )
        self.assertIs(result, displayed)
        evaluator.assert_not_awaited()

    async def test_enabled_jsonl_record_is_sanitized_and_cannot_replace_display(self):
        displayed = {"selected_route_index": 1, "route": [{"stop": "Private stop"}]}
        before = copy.deepcopy(displayed)
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "shadow.jsonl"
            with patch.dict(
                os.environ,
                {
                    "EVALUATION_SHADOW_ENABLED": "true",
                    "EVALUATION_SHADOW_LOG_PATH": str(path),
                    "EVALUATION_SHADOW_SAMPLE_RATE": "1",
                },
                clear=True,
            ):
                result = await trip_shadow.run_trip_shadow(
                    displayed,
                    baseline_evaluator=AsyncMock(
                        return_value=CounterfactualBaselineEvaluation("candidate-0")
                    ),
                    production_route_id="candidate-1",
                    production_status=ShadowEvaluationStatus.COMPLETE,
                    candidate_summaries=[
                        {
                            "id": "candidate-1",
                            "lines": ["Q"],
                            "origin": "Private stop",
                            "total_minutes": 20,
                        }
                    ],
                    source_counts={"grok_web": 1, "token=secret": 99},
                    incident_count=1,
                    scan_status="partial",
                    snapshot_status="unavailable",
                    intelligence_latency_ms=10,
                )
            record = json.loads(path.read_text(encoding="utf-8"))
        self.assertIs(result, displayed)
        self.assertEqual(displayed, before)
        self.assertTrue(record["route_changed"])
        self.assertEqual(record["scan_status"], "partial")
        serialized = json.dumps(record)
        self.assertNotIn("Private stop", serialized)
        self.assertNotIn("token=secret", serialized)

    async def test_enabled_flag_without_jsonl_path_remains_disabled(self):
        displayed = {"selected_route_index": 0}
        evaluator = AsyncMock()
        with patch.dict(
            os.environ, {"EVALUATION_SHADOW_ENABLED": "true"}, clear=True
        ):
            result = await trip_shadow.run_trip_shadow(
                displayed,
                baseline_evaluator=evaluator,
                production_route_id="candidate-0",
                production_status=ShadowEvaluationStatus.COMPLETE,
                candidate_summaries=[],
                source_counts={},
                incident_count=0,
                scan_status="complete",
                snapshot_status="fresh",
                intelligence_latency_ms=1,
            )
        self.assertIs(result, displayed)
        evaluator.assert_not_awaited()

    async def test_zero_sample_rate_never_evaluates(self):
        displayed = {"selected_route_index": 0}
        evaluator = AsyncMock()
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "shadow.jsonl"
            with patch.dict(
                os.environ,
                {
                    "EVALUATION_SHADOW_ENABLED": "true",
                    "EVALUATION_SHADOW_LOG_PATH": str(path),
                    "EVALUATION_SHADOW_SAMPLE_RATE": "0",
                },
                clear=True,
            ):
                result = await trip_shadow.run_trip_shadow(
                    displayed,
                    baseline_evaluator=evaluator,
                    production_route_id="candidate-0",
                    production_status=ShadowEvaluationStatus.COMPLETE,
                    candidate_summaries=[],
                    source_counts={},
                    incident_count=0,
                    scan_status="complete",
                    snapshot_status="fresh",
                    intelligence_latency_ms=1,
                )
        self.assertIs(result, displayed)
        evaluator.assert_not_awaited()

    def test_counterfactual_parser_rejects_route_zero_fallback_and_accepts_full_schema(self):
        failed = trip_shadow.parse_counterfactual_baseline("Q looks fastest", 2)
        self.assertEqual(failed.status, ShadowEvaluationStatus.FAILED)
        raw = (
            '[ROUTE:1][CANDIDATE_ANALYSIS]'
            '{"selected_route_index":1,"candidate_analysis":['
            '{"index":0,"is_recommended":false,"rejection_reason":"slower"},'
            '{"index":1,"is_recommended":true,"recommendation_reason":"better"}]}'
            '[/CANDIDATE_ANALYSIS]'
        )
        complete = trip_shadow.parse_counterfactual_baseline(raw, 2)
        self.assertEqual(complete.selected_route_id, "candidate-1")
        self.assertEqual(complete.status, ShadowEvaluationStatus.COMPLETE)

    def test_source_counts_are_allowlisted(self):
        counts = trip_shadow.safe_source_counts(
            incidents=[
                {"source": "grok_x, 511ny"},
                {"source": "https://provider.test/?api_key=secret"},
            ],
            alert_count=1,
            stalled_train_count=2,
            stalled_bus_count=0,
        )
        self.assertEqual(counts["grok_x"], 1)
        self.assertEqual(counts["cached_511ny"], 1)
        self.assertNotIn("secret", repr(counts))

    def test_fallback_production_choice_is_not_counted_as_route_change(self):
        from evaluation.route_intelligence.shadow import build_shadow_record

        record = build_shadow_record(
            evidence_kind="live_shadow",
            advisor_identity={"advisor_provider": "anthropic", "advisor_model": "test"},
            production_intelligence_route_id="candidate-0",
            production_intelligence_status=ShadowEvaluationStatus.FALLBACK,
            counterfactual_baseline_route_id="candidate-1",
            counterfactual_baseline_status=ShadowEvaluationStatus.COMPLETE,
        )
        self.assertIsNone(record.route_changed)


if __name__ == "__main__":
    unittest.main()
