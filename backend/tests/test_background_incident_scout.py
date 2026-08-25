"""Contract tests for bounded background Grok X scouting + Web corroboration."""

from __future__ import annotations

import contextlib
import io
import json
import os
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

from app.services.incidents import scout
from app.services.incidents import scout_provider as transport
from app.services.incidents.batches import INCIDENT_BATCHES
from app.services.incidents.scout_normalization import claim_ref_for, per_post_source_id

BATCH = INCIDENT_BATCHES[1]  # midtown-manhattan
NOW = datetime(2026, 8, 2, 16, 0, tzinfo=timezone.utc)
X_URL = "https://x.com/nycdesk/status/1234567890"
X_URL_2 = "https://x.com/nycdesk/status/9999999999"
WEB_URL = "https://news.example.test/report"
X_ID = per_post_source_id(X_URL)
X_ID_2 = per_post_source_id(X_URL_2)


def _claim(*, source_url=X_URL, scope="subway_operations", minutes_ago=20, observed_at=None):
    return {
        "location": "Lexington Avenue",
        "description": "FDNY on scene at a street-level emergency.",
        "severity": "high",
        "impact_scope": scope,
        "route_ids": ["6"],
        "source_url": source_url,
        "observed_at": observed_at or (NOW - timedelta(minutes=minutes_ago)).isoformat(),
    }


def _x_result(payload, *, citations=(X_URL,), completed=True):
    return scout.ScoutSearchResult(
        response_text=json.dumps(payload), citations=citations, tool_completed=completed
    )


def _web_result(payload, *, citations=(WEB_URL,), completed=True):
    return scout.ScoutSearchResult(
        response_text=json.dumps(payload), citations=citations, tool_completed=completed
    )


class PublicSurfaceTests(unittest.TestCase):
    def test_reexports_public_transport_surface(self):
        self.assertIs(scout.ScoutSearchResult, transport.ScoutSearchResult)


class ScoutBatchTests(unittest.IsolatedAsyncioTestCase):
    async def test_empty_valid_x_complete_no_web_no_all_clear(self):
        x_runner = AsyncMock(return_value=_x_result({"incidents": []}))
        web_runner = AsyncMock()
        result = await scout.scout_incident_batch(
            BATCH, run_x_search=x_runner, run_web_search=web_runner, clock=lambda: NOW
        )
        self.assertEqual(result.x_status, "complete")
        self.assertEqual(result.web_status, "not_triggered")
        self.assertEqual(result.model_calls, 1)
        self.assertEqual(result.incidents, ())
        self.assertEqual(result.batch_id, BATCH.batch_id)
        self.assertTrue(result.attempted_at.endswith("Z"))
        self.assertFalse(hasattr(result, "all_clear"))
        x_runner.assert_awaited_once_with(BATCH, now=NOW)
        web_runner.assert_not_awaited()

    async def test_x_only_claim_unconfirmed_with_stable_source_id(self):
        x_runner = AsyncMock(return_value=_x_result({"incidents": [_claim()]}))
        web_runner = AsyncMock(return_value=_web_result({"corroborations": []}))
        result = await scout.scout_incident_batch(
            BATCH, run_x_search=x_runner, run_web_search=web_runner, clock=lambda: NOW
        )
        self.assertEqual(result.x_status, "complete")
        self.assertEqual(result.web_status, "complete")
        self.assertEqual(result.model_calls, 2)
        incident = result.incidents[0]
        self.assertEqual(incident["state"], "unconfirmed")
        self.assertEqual(incident["corroboration_state"], "uncorroborated")
        self.assertFalse(incident["advisor_eligible"])
        self.assertEqual(incident["source"], "x_search")
        self.assertEqual(incident["source_id"], X_ID)
        self.assertEqual(incident["affected_batch_ids"], [BATCH.batch_id])
        self.assertEqual(incident["source_coverage"], ["x_search"])
        self.assertEqual(len(incident["source_records"]), 1)
        self.assertEqual(incident["source_records"][0]["source_id"], X_ID)

    async def test_tool_not_completed_prevents_acceptance_and_web(self):
        x_runner = AsyncMock(
            return_value=scout.ScoutSearchResult(
                response_text=json.dumps({"incidents": [_claim()]}),
                citations=(X_URL,),
                tool_completed=False,
            )
        )
        web_runner = AsyncMock()
        result = await scout.scout_incident_batch(
            BATCH, run_x_search=x_runner, run_web_search=web_runner, clock=lambda: NOW
        )
        self.assertEqual(result.x_status, "partial")
        self.assertEqual(result.web_status, "not_triggered")
        self.assertEqual(result.incidents, ())
        web_runner.assert_not_awaited()

    async def test_malformed_x_json_is_partial_and_blocks_web(self):
        x_runner = AsyncMock(return_value=scout.ScoutSearchResult("not json", (), True))
        web_runner = AsyncMock()
        result = await scout.scout_incident_batch(
            BATCH, run_x_search=x_runner, run_web_search=web_runner, clock=lambda: NOW
        )
        self.assertEqual(result.x_status, "partial")
        self.assertEqual(result.web_status, "not_triggered")
        self.assertEqual(result.incidents, ())
        web_runner.assert_not_awaited()

    async def test_x_invalid_json_contract_is_partial_and_blocks_web(self):
        cases = [
            _x_result({}),
            _x_result({"incidents": None}),
            _x_result({"incidents": "nope"}),
            _x_result({"incidents": {"a": 1}}),
            _x_result({"incidents": 3}),
        ]
        for x_result in cases:
            with self.subTest(x_result=x_result):
                result = await scout.scout_incident_batch(
                    BATCH,
                    run_x_search=AsyncMock(return_value=x_result),
                    run_web_search=AsyncMock(),
                    clock=lambda: NOW,
                )
                self.assertEqual(result.x_status, "partial")
                self.assertEqual(result.web_status, "not_triggered")
                self.assertEqual(result.incidents, ())

    async def test_non_scout_result_contract_is_partial_without_leak(self):
        x_runner = AsyncMock(
            return_value={
                "response_text": json.dumps({"incidents": [_claim()]}),
                "citations": (X_URL,),
                "tool_completed": True,
            }
        )
        web_runner = AsyncMock()
        result = await scout.scout_incident_batch(
            BATCH, run_x_search=x_runner, run_web_search=web_runner, clock=lambda: NOW
        )
        self.assertEqual(result.x_status, "partial")
        self.assertEqual(result.web_status, "not_triggered")
        self.assertEqual(result.incidents, ())
        web_runner.assert_not_awaited()

    async def test_web_never_called_without_accepted_claims_or_complete_x(self):
        web_runner = AsyncMock()
        cases = [
            _x_result({"incidents": [_claim(source_url="https://x.com/unknown/status/1")]}),
            scout.ScoutSearchResult(
                response_text=json.dumps({"incidents": [_claim()]}),
                citations=(X_URL,),
                tool_completed=False,
            ),
            scout.ScoutSearchResult("garbage", (), True),
        ]
        for x_result in cases:
            with self.subTest(x_result=x_result):
                result = await scout.scout_incident_batch(
                    BATCH,
                    run_x_search=AsyncMock(return_value=x_result),
                    run_web_search=web_runner,
                    clock=lambda: NOW,
                )
                self.assertEqual(result.web_status, "not_triggered")
                self.assertEqual(result.incidents, ())
        web_runner.assert_not_awaited()

    async def test_one_web_call_confirms_only_matching_claim(self):
        claims = [
            _claim(),
            _claim(source_url=X_URL_2, minutes_ago=5),
        ]
        web_runner = AsyncMock(
            return_value=_web_result(
                {
                    "corroborations": [
                        {
                            "claim_ref": claim_ref_for(X_ID),
                            "source_url": WEB_URL,
                            "observed_at": (NOW - timedelta(minutes=4)).isoformat(),
                        }
                    ]
                }
            )
        )
        x_runner = AsyncMock(
            return_value=_x_result({"incidents": claims}, citations=(X_URL, X_URL_2))
        )
        result = await scout.scout_incident_batch(
            BATCH, run_x_search=x_runner, run_web_search=web_runner, clock=lambda: NOW
        )
        self.assertEqual(result.web_status, "complete")
        web_runner.assert_awaited_once()
        passed = web_runner.await_args.args[0]
        self.assertEqual(len(passed), 2)
        self.assertEqual(
            {claim["claim_ref"] for claim in passed},
            {claim_ref_for(X_ID), claim_ref_for(X_ID_2)},
        )
        self.assertNotIn("source_url", passed[0])
        self.assertNotIn("source_id", passed[0])
        by_source = {
            incident["source_records"][0]["source_id"]: incident for incident in result.incidents
        }
        self.assertEqual(by_source[X_ID]["state"], "confirmed")
        self.assertTrue(by_source[X_ID]["advisor_eligible"])
        self.assertEqual(by_source[X_ID_2]["state"], "unconfirmed")
        self.assertFalse(by_source[X_ID_2]["advisor_eligible"])

    async def test_web_failure_and_invalid_contract_leave_claims_unconfirmed(self):
        payload = {"incidents": [_claim()]}
        cases = [
            (AsyncMock(side_effect=RuntimeError("boom")), "unavailable"),
            (AsyncMock(return_value=scout.ScoutSearchResult("junk", (), True)), "partial"),
            (
                AsyncMock(
                    return_value=scout.ScoutSearchResult('{"corroborations": []}', (), False)
                ),
                "partial",
            ),
        ]
        for web_runner, expected in cases:
            with self.subTest(expected=expected):
                result = await scout.scout_incident_batch(
                    BATCH,
                    run_x_search=AsyncMock(return_value=_x_result(payload)),
                    run_web_search=web_runner,
                    clock=lambda: NOW,
                )
                self.assertEqual(result.web_status, expected)
                incident = result.incidents[0]
                self.assertEqual(incident["state"], "unconfirmed")
                self.assertFalse(incident["advisor_eligible"])

    async def test_web_invalid_contract_and_non_scout_result_leave_x_unconfirmed(self):
        x_runner = AsyncMock(return_value=_x_result({"incidents": [_claim()]}))
        invalid_web_runners = [
            AsyncMock(return_value=_web_result({})),
            AsyncMock(return_value=_web_result({"corroborations": None})),
            AsyncMock(return_value=_web_result({"corroborations": "nope"})),
            AsyncMock(return_value=_web_result({"corroborations": {"a": 1}})),
            AsyncMock(return_value={"corroborations": []}),
        ]
        for web_runner in invalid_web_runners:
            with self.subTest(web_runner=web_runner):
                result = await scout.scout_incident_batch(
                    BATCH,
                    run_x_search=x_runner,
                    run_web_search=web_runner,
                    clock=lambda: NOW,
                )
                self.assertEqual(result.web_status, "partial")
                incident = result.incidents[0]
                self.assertEqual(incident["state"], "unconfirmed")
                self.assertFalse(incident["advisor_eligible"])
                self.assertEqual(len(incident["source_records"]), 1)

    async def test_runner_exception_degrades_without_leaks(self):
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            result = await scout.scout_incident_batch(
                BATCH,
                run_x_search=AsyncMock(side_effect=RuntimeError("boom")),
                clock=lambda: NOW,
            )
        self.assertEqual(result.x_status, "unavailable")
        self.assertEqual(result.web_status, "not_triggered")
        self.assertEqual(result.model_calls, 1)
        self.assertEqual(result.incidents, ())
        self.assertNotIn("boom", buffer.getvalue())

    async def test_unconfigured_provider_zero_model_calls(self):
        with patch.object(transport, "AsyncClient", None), patch.dict(
            os.environ, {"XAI_API_KEY": ""}
        ):
            result = await scout.scout_incident_batch(BATCH, clock=lambda: NOW)
        self.assertEqual(result.x_status, "unavailable")
        self.assertEqual(result.web_status, "not_triggered")
        self.assertEqual(result.model_calls, 0)
        self.assertEqual(result.incidents, ())
        self.assertNotIn("not_triggered", (result.x_status,))

    async def test_web_unavailable_when_claims_exist_but_no_web_transport(self):
        with patch.object(transport, "AsyncClient", None), patch.dict(
            os.environ, {"XAI_API_KEY": ""}
        ):
            result = await scout.scout_incident_batch(
                BATCH,
                run_x_search=AsyncMock(return_value=_x_result({"incidents": [_claim()]})),
                clock=lambda: NOW,
            )
        self.assertEqual(result.x_status, "complete")
        self.assertEqual(result.web_status, "unavailable")
        self.assertEqual(result.model_calls, 1)
        self.assertEqual(result.incidents[0]["state"], "unconfirmed")

    async def test_non_utc_aware_clock_normalized_to_utc(self):
        def clock():
            return datetime(
                2026, 8, 2, 12, 0, tzinfo=timezone(timedelta(hours=-4))
            )

        x_runner = AsyncMock(return_value=_x_result({"incidents": []}))
        result = await scout.scout_incident_batch(BATCH, run_x_search=x_runner, clock=clock)
        self.assertEqual(result.attempted_at, "2026-08-02T16:00:00Z")
        x_runner.assert_awaited_once_with(
            BATCH, now=datetime(2026, 8, 2, 16, 0, tzinfo=timezone.utc)
        )

    async def test_freshness_validation_uses_utc_normalized_clock(self):
        def clock():
            return datetime(
                2026, 8, 2, 12, 0, tzinfo=timezone(timedelta(hours=-4))
            )

        claim = _claim(observed_at="2026-08-02T10:00:00-04:00")  # 14:00Z, two hours old
        x_runner = AsyncMock(return_value=_x_result({"incidents": [claim]}))
        result = await scout.scout_incident_batch(BATCH, run_x_search=x_runner, clock=clock)
        self.assertEqual(result.x_status, "complete")
        self.assertEqual(len(result.incidents), 1)
        self.assertEqual(result.incidents[0]["observed_at"], "2026-08-02T14:00:00Z")

    async def test_naive_clock_is_rejected_not_interpreted(self):
        with self.assertRaises(ValueError):
            await scout.scout_incident_batch(
                BATCH,
                run_x_search=AsyncMock(return_value=_x_result({"incidents": []})),
                clock=lambda: datetime(2026, 8, 2, 12, 0),
            )


if __name__ == "__main__":
    unittest.main()
