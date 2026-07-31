from __future__ import annotations

import copy
import asyncio
import json
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

from app.services.validation.shadow import (
    EvidenceKind,
    CounterfactualBaselineEvaluation,
    InMemoryShadowSink,
    JsonlShadowReviewStore,
    JsonlShadowSink,
    NullShadowSink,
    ReviewClassification,
    ShadowDecisionRecord,
    ShadowReview,
    ShadowEvaluationStatus,
    build_shadow_record,
    execute_counterfactual_shadow,
    submit_shadow_record_safely,
)


def _record(**overrides):
    values = {
        "evidence_kind": EvidenceKind.DETERMINISTIC_FIXTURE,
        "scenario_id": "stalled-subway",
        "advisor_identity": {
            "advisor_provider": "anthropic",
            "advisor_model": "claude-haiku-4-5-20251001",
        },
        "production_intelligence_route_id": "candidate-1",
        "counterfactual_baseline_route_id": "candidate-0",
        "counterfactual_baseline_status": ShadowEvaluationStatus.COMPLETE,
        "candidate_summaries": [
            {"id": "candidate-0", "lines": ["4"], "total_minutes": 29, "transfers": 0, "selection_score": 29},
            {"id": "candidate-1", "lines": ["2"], "total_minutes": 34, "transfers": 0, "selection_score": 34},
        ],
        "source_counts": {"stalled_subway": 1, "grok_web": 1, "not_a_source": 99},
        "incident_count": 1,
        "scan_status": "complete",
        "ny511_snapshot_status": "fresh",
        "recorded_at": datetime(2026, 7, 22, 12, tzinfo=UTC),
    }
    values.update(overrides)
    return build_shadow_record(**values)


class ShadowDecisionRecordTests(unittest.TestCase):
    def test_counterfactual_record_is_allow_list_only_and_has_safe_generated_uuid(self):
        record = _record(
            candidate_summaries=[
                {
                    "id": "candidate-0",
                    "lines": ["Q", "https://example.test/?apikey=secret"],
                    "total_minutes": 31,
                    "origin": {"lat": 40.7128, "lng": -74.0060},
                    "destination": "123 Private Street",
                    "prompt": "secret system prompt",
                    "api_key": "sk-live-secret",
                    "coordinates": [40.1, -73.1],
                    "polyline": "encoded-private-geometry",
                    "nested": {"url": "https://provider.test/?token=private"},
                }
            ],
            source_counts={"grok_x": 1, "evil_source": 99},
        )
        serialized = json.dumps(record.as_dict(), sort_keys=True)
        for forbidden in (
            "apikey=secret", "Private Street", "system prompt", "sk-live-secret",
            "coordinates", "polyline", "provider.test", "evil_source", "40.7128", "-74.006",
        ):
            self.assertNotIn(forbidden, serialized)
        self.assertRegex(record.observation_id, r"^[0-9a-f]{8}-[0-9a-f-]{27}$")
        self.assertEqual(UUID(record.observation_id).version, 4)
        self.assertEqual(record.route_changed, True)
        self.assertEqual(record.scenario_id, "stalled-subway")
        self.assertEqual(record.source_counts[0][0].value, "grok_x")

    def test_live_record_never_persists_scenario_id_or_raw_request_identifiers(self):
        record = _record(
            evidence_kind="live_shadow",
            scenario_id="request-123-session-456",
            production_intelligence_route_id="request-abc",
            counterfactual_baseline_route_id="session-def",
        )
        payload = record.as_dict()
        self.assertIsNone(payload["scenario_id"])
        self.assertIsNone(payload["production_intelligence"]["selected_route_id"])
        self.assertIsNone(payload["counterfactual_baseline"]["selected_route_id"])
        self.assertIsNone(payload["route_changed"])

    def test_missing_or_failed_counterfactual_never_becomes_all_clear(self):
        record = _record(
            counterfactual_baseline_route_id="candidate-0",
            counterfactual_baseline_status="timeout",
        )
        self.assertIsNone(record.route_changed)
        self.assertEqual(record.counterfactual_baseline_status, ShadowEvaluationStatus.TIMEOUT)

    def test_null_and_memory_sinks_never_return_or_mutate_a_displayed_decision(self):
        displayed = {
            "selected_route_index": 1,
            "route": [{"departure_stop": "Sensitive stop", "polyline": "private"}],
            "route_candidates": [{"id": "candidate-1"}],
        }
        before = copy.deepcopy(displayed)
        self.assertTrue(submit_shadow_record_safely(NullShadowSink(), _record()))
        memory = InMemoryShadowSink()
        self.assertTrue(submit_shadow_record_safely(memory, _record()))
        self.assertEqual(displayed, before)
        self.assertEqual(len(memory.records), 1)
        self.assertNotIn("Sensitive stop", json.dumps(memory.records))

    def test_sink_exception_is_contained_without_a_replacement_route(self):
        class BrokenSink:
            def submit(self, record: ShadowDecisionRecord) -> None:
                del record
                raise RuntimeError("token=private raw error")

        response = {"selected_route_index": 0, "route": ["unchanged"]}
        before = copy.deepcopy(response)
        self.assertFalse(submit_shadow_record_safely(BrokenSink(), _record()))
        self.assertEqual(response, before)

    def test_jsonl_sink_writes_only_sanitized_record(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "records.jsonl"
            JsonlShadowSink(path).submit(_record())
            rows = path.read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(rows), 1)
            payload = json.loads(rows[0])
            self.assertEqual(payload["evidence_kind"], "deterministic_fixture")
            self.assertNotIn("origin", payload)
            self.assertNotIn("destination", payload)

    def test_reviews_are_enum_only_and_do_not_accept_free_text(self):
        record = _record()
        review = ShadowReview.create(record.observation_id, ReviewClassification.CORRECT_IMPROVEMENT)
        self.assertEqual(review.classification, ReviewClassification.CORRECT_IMPROVEMENT)
        with self.assertRaisesRegex(ValueError, "classification"):
            ShadowReview.create(record.observation_id, "this has a private note")
        with self.assertRaisesRegex(ValueError, "UUID"):
            ShadowReview.create("session-abcdef", ReviewClassification.EQUIVALENT_ROUTE)
        with self.assertRaisesRegex(ValueError, "UUIDv4"):
            ShadowReview.create("6ba7b810-9dad-11d1-80b4-00c04fd430c8", ReviewClassification.EQUIVALENT_ROUTE)
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "reviews.jsonl"
            JsonlShadowReviewStore(path).submit_review(review)
            self.assertNotIn("note", path.read_text(encoding="utf-8").lower())

    def test_enum_instances_preserve_their_intended_statuses(self):
        record = _record(
            evidence_kind=EvidenceKind.DETERMINISTIC_FIXTURE,
            production_intelligence_status=ShadowEvaluationStatus.COMPLETE,
            counterfactual_baseline_status=ShadowEvaluationStatus.COMPLETE,
        )
        self.assertEqual(record.evidence_kind, EvidenceKind.DETERMINISTIC_FIXTURE)
        self.assertEqual(record.production_intelligence_status, ShadowEvaluationStatus.COMPLETE)
        review = ShadowReview.create(record.observation_id, ReviewClassification.EQUIVALENT_ROUTE)
        self.assertEqual(review.classification, ReviewClassification.EQUIVALENT_ROUTE)

    def test_jsonl_sinks_reject_ambiguous_file_types(self):
        with self.assertRaisesRegex(ValueError, "jsonl"):
            JsonlShadowSink("records.txt")
        with self.assertRaisesRegex(ValueError, "jsonl"):
            JsonlShadowReviewStore("reviews.txt")

class CounterfactualShadowExecutionTests(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    def _record_factory(evaluation, baseline_latency_ms, shadow_overhead_ms):
        return _record(
            counterfactual_baseline_route_id=evaluation.selected_route_id,
            counterfactual_baseline_status=evaluation.status,
            baseline_latency_ms=baseline_latency_ms,
            shadow_overhead_ms=shadow_overhead_ms,
        )

    async def test_disabled_by_default_never_evaluates_or_persists_and_returns_same_object(self):
        displayed = {"selected_route_index": 1, "route": [{"stop": "private"}]}
        called = False

        async def evaluator():
            nonlocal called
            called = True
            return CounterfactualBaselineEvaluation("candidate-0")

        outcome = await execute_counterfactual_shadow(displayed, baseline_evaluator=evaluator)
        self.assertIs(outcome.displayed_result, displayed)
        self.assertFalse(called)
        self.assertEqual(outcome.baseline_status, ShadowEvaluationStatus.DISABLED)
        self.assertIsNone(outcome.record)
        self.assertFalse(outcome.submitted)

    async def test_complete_counterfactual_is_bounded_recorded_and_cannot_mutate_display(self):
        displayed = {"selected_route_index": 1, "route": [{"stop": "private"}]}
        before = copy.deepcopy(displayed)
        sink = InMemoryShadowSink()

        async def evaluator():
            return CounterfactualBaselineEvaluation("candidate-0", ShadowEvaluationStatus.COMPLETE)

        outcome = await execute_counterfactual_shadow(
            displayed, enabled=True, baseline_evaluator=evaluator, timeout_s=1,
            record_factory=self._record_factory, sink=sink,
        )
        self.assertIs(outcome.displayed_result, displayed)
        self.assertEqual(displayed, before)
        self.assertEqual(outcome.baseline_status, ShadowEvaluationStatus.COMPLETE)
        self.assertTrue(outcome.submitted)
        self.assertEqual(outcome.record.counterfactual_baseline_route_id, "candidate-0")
        self.assertIsNotNone(outcome.record.baseline_latency_ms)
        self.assertIsNotNone(outcome.record.shadow_overhead_ms)
        self.assertEqual(len(sink.records), 1)

    async def test_timeout_and_evaluator_failure_return_same_display_and_safe_statuses(self):
        displayed = {"selected_route_index": 1, "route": ["unchanged"]}
        before = copy.deepcopy(displayed)

        async def too_slow():
            await asyncio.sleep(0.05)
            return CounterfactualBaselineEvaluation("candidate-0")

        async def broken():
            raise RuntimeError("api_key=private raw exception")

        for evaluator, expected in ((too_slow, ShadowEvaluationStatus.TIMEOUT), (broken, ShadowEvaluationStatus.FAILED)):
            outcome = await execute_counterfactual_shadow(
                displayed, enabled=True, baseline_evaluator=evaluator, timeout_s=0.001,
                record_factory=self._record_factory, sink=InMemoryShadowSink(),
            )
            self.assertIs(outcome.displayed_result, displayed)
            self.assertEqual(displayed, before)
            self.assertEqual(outcome.baseline_status, expected)
            self.assertIsNone(outcome.record.counterfactual_baseline_route_id)
            self.assertEqual(outcome.record.counterfactual_baseline_status, expected)

    async def test_sink_failure_is_contained_after_evaluation_without_replacing_display(self):
        class BrokenSink:
            def submit(self, _record):
                raise RuntimeError("token=private")

        displayed = {"selected_route_index": 1}

        async def evaluator():
            return CounterfactualBaselineEvaluation("candidate-0")

        outcome = await execute_counterfactual_shadow(
            displayed, enabled=True, baseline_evaluator=evaluator,
            record_factory=self._record_factory, sink=BrokenSink(),
        )
        self.assertIs(outcome.displayed_result, displayed)
        self.assertFalse(outcome.submitted)
        self.assertIsNotNone(outcome.record)
