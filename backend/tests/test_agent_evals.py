"""Tests for backend/evals/ -- the golden-query eval harness.

Two layers, per plan doc section 7 item 6:

  (a) Pure unit tests for `evals/assertions.py` -- every op, including
      `iso_within` and the special-cased `derived` multi-stop check --
      exercised with hand-built trace/call_log data, no model involved.
  (b) A fake-model end-to-end run: the `tests/_fake_anthropic.py` scripted
      client drives `run_agent_turn` through the RUNNER's own machinery
      (`evals.run_agent_evals.run_single_query`) for a few representative
      golden queries, proving runner + fixture_router (fuzzy resolution)
      + assertions work together, not just in isolation.
  (c) `evals/run_agent_evals.py --validate` is exercised directly against
      the real golden_queries.yaml/fixtures (must be clean) and against a
      deliberately broken temp query bank (must report problems).

Follows test_agent_loop.py's sys.modules fake-anthropic convention: swap
just the "anthropic" key by hand (never `patch.dict(sys.modules, {...})`,
which would also undo every submodule loop.py's own import graph newly
registers -- see that file's comment for the full rationale), and reload
`app.services.agent.loop` exactly once per test, not per-assertion.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from app.utils import cache
from evals import assertions
from evals import run_agent_evals
from tests._fake_anthropic import reload_agent_loop_module

FIXTURES_ROOT = Path(__file__).resolve().parent.parent / "evals" / "fixtures"
QUERIES_PATH = Path(__file__).resolve().parent.parent / "evals" / "golden_queries.yaml"


def _trace(tool_calls, final_text: str = "") -> SimpleNamespace:
    return SimpleNamespace(tool_calls=list(tool_calls), final_text=final_text)


def _reload_agent_loop_with_fake(rounds: list[dict]):
    # See _fake_anthropic.reload_agent_loop_module's docstring for why this
    # is a manual sys.modules swap rather than patch.dict(sys.modules, ...).
    return reload_agent_loop_module(rounds=rounds)


# ------------------------------------------------------- assertion engine --


class ToolCalledTests(unittest.TestCase):
    def test_any_passes_when_present(self):
        trace = _trace([("plan_trip", {"destination": "Costco"})])
        result = assertions.evaluate_one({"tool_called": {"name": "plan_trip"}}, trace=trace, final_text="")
        self.assertTrue(result.ok)

    def test_any_fails_when_absent(self):
        result = assertions.evaluate_one({"tool_called": {"name": "plan_trip"}}, trace=_trace([]), final_text="")
        self.assertFalse(result.ok)

    def test_where_first(self):
        trace = _trace([("event_lookup", {}), ("venue_crowd_window", {})])
        good = assertions.evaluate_one({"tool_called": {"name": "event_lookup", "where": "first"}}, trace=trace, final_text="")
        bad = assertions.evaluate_one({"tool_called": {"name": "venue_crowd_window", "where": "first"}}, trace=trace, final_text="")
        self.assertTrue(good.ok)
        self.assertFalse(bad.ok)

    def test_where_none_before(self):
        trace = _trace([("event_lookup", {}), ("venue_crowd_window", {})])
        good = assertions.evaluate_one(
            {"tool_called": {"name": "event_lookup", "where": {"none_before": "venue_crowd_window"}}},
            trace=trace,
            final_text="",
        )
        bad = assertions.evaluate_one(
            {"tool_called": {"name": "venue_crowd_window", "where": {"none_before": "event_lookup"}}},
            trace=trace,
            final_text="",
        )
        self.assertTrue(good.ok)
        self.assertFalse(bad.ok)

    def test_none_before_passes_when_other_tool_never_called(self):
        trace = _trace([("event_lookup", {})])
        result = assertions.evaluate_one(
            {"tool_called": {"name": "event_lookup", "where": {"none_before": "venue_crowd_window"}}},
            trace=trace,
            final_text="",
        )
        self.assertTrue(result.ok)


class ToolNotCalledAndNoToolsTests(unittest.TestCase):
    def test_tool_not_called(self):
        self.assertTrue(assertions.evaluate_one({"tool_not_called": {"name": "plan_trip"}}, trace=_trace([("poi_search", {})]), final_text="").ok)
        self.assertFalse(assertions.evaluate_one({"tool_not_called": {"name": "plan_trip"}}, trace=_trace([("plan_trip", {})]), final_text="").ok)

    def test_no_tools(self):
        self.assertTrue(assertions.evaluate_one({"no_tools": True}, trace=_trace([]), final_text="").ok)
        self.assertFalse(assertions.evaluate_one({"no_tools": True}, trace=_trace([("plan_trip", {})]), final_text="").ok)


class ToolInputOpsTests(unittest.TestCase):
    def setUp(self):
        self.trace = _trace(
            [
                ("plan_trip", {"destination": "Costco Wholesale", "exclude_modes": ["BUS"], "routing_preference": "FEWER_TRANSFERS"}),
                ("plan_trip", {"destination": "Grand Central", "departure_time": "2026-07-16T22:10:00-04:00"}),
            ]
        )

    def test_contains_on_a_list(self):
        r = assertions.evaluate_one(
            {"tool_input": {"name": "plan_trip", "path": "exclude_modes", "op": "contains", "value": "BUS"}},
            trace=self.trace,
            final_text="",
        )
        self.assertTrue(r.ok)

    def test_contains_on_a_string_is_case_insensitive_substring(self):
        r = assertions.evaluate_one(
            {"tool_input": {"name": "plan_trip", "path": "destination", "op": "contains", "value": "costco"}},
            trace=self.trace,
            final_text="",
        )
        self.assertTrue(r.ok)

    def test_equals_is_case_insensitive(self):
        r = assertions.evaluate_one(
            {"tool_input": {"name": "plan_trip", "path": "routing_preference", "op": "equals", "value": "fewer_transfers"}},
            trace=self.trace,
            final_text="",
        )
        self.assertTrue(r.ok)

    def test_absent_true_and_false(self):
        present = assertions.evaluate_one(
            {"tool_input": {"name": "plan_trip", "path": "exclude_modes", "op": "absent", "call_index": 1}},
            trace=self.trace,
            final_text="",
        )
        absent = assertions.evaluate_one(
            {"tool_input": {"name": "plan_trip", "path": "exclude_modes", "op": "absent", "call_index": 0}},
            trace=self.trace,
            final_text="",
        )
        self.assertTrue(present.ok)
        self.assertFalse(absent.ok)

    def test_matches_regex(self):
        r = assertions.evaluate_one(
            {"tool_input": {"name": "plan_trip", "path": "destination", "op": "matches_regex", "value": "grand.*central", "call_index": 1}},
            trace=self.trace,
            final_text="",
        )
        self.assertTrue(r.ok)

    def test_iso_within_pass_and_fail(self):
        spec = {
            "tool_input": {
                "name": "plan_trip",
                "path": "departure_time",
                "op": "iso_within",
                "value": {"of": "2026-07-16T22:00:00-04:00", "minutes": 30},
                "call_index": 1,
            }
        }
        self.assertTrue(assertions.evaluate_one(spec, trace=self.trace, final_text="").ok)
        spec["tool_input"]["value"]["minutes"] = 5
        self.assertFalse(assertions.evaluate_one(spec, trace=self.trace, final_text="").ok)

    def test_iso_within_rejects_naive_or_garbage_values(self):
        trace = _trace([("plan_trip", {"departure_time": "not-a-time"})])
        spec = {"tool_input": {"name": "plan_trip", "path": "departure_time", "op": "iso_within", "value": {"of": "2026-07-16T22:00:00-04:00", "minutes": 30}}}
        self.assertFalse(assertions.evaluate_one(spec, trace=trace, final_text="").ok)

    def test_call_index_out_of_range_fails_cleanly_not_an_exception(self):
        r = assertions.evaluate_one(
            {"tool_input": {"name": "plan_trip", "path": "destination", "op": "contains", "value": "x", "call_index": 5}},
            trace=self.trace,
            final_text="",
        )
        self.assertFalse(r.ok)


class CallOrderAndCountTests(unittest.TestCase):
    def test_call_order_pass_and_fail(self):
        trace = _trace([("poi_search", {}), ("plan_trip", {})])
        self.assertTrue(assertions.evaluate_one({"call_order": ["poi_search", "plan_trip"]}, trace=trace, final_text="").ok)
        self.assertFalse(assertions.evaluate_one({"call_order": ["plan_trip", "poi_search"]}, trace=trace, final_text="").ok)

    def test_call_order_fails_when_a_tool_never_called(self):
        trace = _trace([("poi_search", {})])
        self.assertFalse(assertions.evaluate_one({"call_order": ["poi_search", "plan_trip"]}, trace=trace, final_text="").ok)

    def test_call_count_eq_and_gte(self):
        trace = _trace([("plan_trip", {}), ("plan_trip", {})])
        self.assertTrue(assertions.evaluate_one({"call_count": {"name": "plan_trip", "op": "eq", "value": 2}}, trace=trace, final_text="").ok)
        self.assertTrue(assertions.evaluate_one({"call_count": {"name": "plan_trip", "op": "gte", "value": 1}}, trace=trace, final_text="").ok)
        self.assertFalse(assertions.evaluate_one({"call_count": {"name": "plan_trip", "op": "eq", "value": 3}}, trace=trace, final_text="").ok)


class FinalTextTests(unittest.TestCase):
    def test_matches_and_not_matches(self):
        text = "I can't drive you to Boston -- I only cover NYC transit."
        self.assertTrue(assertions.evaluate_one({"final_text": {"op": "matches_regex", "pattern": "boston"}}, trace=_trace([]), final_text=text).ok)
        self.assertFalse(assertions.evaluate_one({"final_text": {"op": "not_matches_regex", "pattern": "boston"}}, trace=_trace([]), final_text=text).ok)
        self.assertTrue(assertions.evaluate_one({"final_text": {"op": "not_matches_regex", "pattern": "9:15\\s*pm"}}, trace=_trace([]), final_text=text).ok)


class DerivedLeg2Tests(unittest.TestCase):
    def test_leg2_after_leg1_plus_dwell_passes(self):
        call_log = [
            {"tool": "poi_search", "input": {}, "data": {}},
            {"tool": "plan_trip", "input": {}, "data": {"candidates": [{"arrives_iso": "2026-07-15T14:17:00-04:00"}]}},
            {"tool": "plan_trip", "input": {"departure_time": "2026-07-15T14:45:00-04:00"}, "data": {}},
        ]
        r = assertions.evaluate_one(
            {"derived": {"leg2_departs_after_leg1_arrival_plus": {"minutes": 25}}},
            trace=_trace([]),
            final_text="",
            call_log=call_log,
        )
        self.assertTrue(r.ok)

    def test_leg2_too_soon_fails(self):
        call_log = [
            {"tool": "plan_trip", "input": {}, "data": {"candidates": [{"arrives_iso": "2026-07-15T14:17:00-04:00"}]}},
            {"tool": "plan_trip", "input": {"departure_time": "2026-07-15T14:20:00-04:00"}, "data": {}},
        ]
        r = assertions.evaluate_one(
            {"derived": {"leg2_departs_after_leg1_arrival_plus": {"minutes": 25}}},
            trace=_trace([]),
            final_text="",
            call_log=call_log,
        )
        self.assertFalse(r.ok)

    def test_missing_second_plan_trip_call_fails_cleanly(self):
        call_log = [{"tool": "plan_trip", "input": {}, "data": {"candidates": [{"arrives_iso": "2026-07-15T14:17:00-04:00"}]}}]
        r = assertions.evaluate_one(
            {"derived": {"leg2_departs_after_leg1_arrival_plus": {"minutes": 25}}},
            trace=_trace([]),
            final_text="",
            call_log=call_log,
        )
        self.assertFalse(r.ok)

    def test_exactly_at_the_threshold_passes(self):
        # >= , not strictly >
        call_log = [
            {"tool": "plan_trip", "input": {}, "data": {"candidates": [{"arrives_iso": "2026-07-15T14:17:00-04:00"}]}},
            {"tool": "plan_trip", "input": {"departure_time": "2026-07-15T14:42:00-04:00"}, "data": {}},
        ]
        r = assertions.evaluate_one(
            {"derived": {"leg2_departs_after_leg1_arrival_plus": {"minutes": 25}}},
            trace=_trace([]),
            final_text="",
            call_log=call_log,
        )
        self.assertTrue(r.ok)


class SpecErrorTests(unittest.TestCase):
    def test_unknown_op_raises_spec_error_not_a_silent_failure(self):
        with self.assertRaises(assertions.AssertionSpecError):
            assertions.evaluate_one(
                {"tool_input": {"name": "plan_trip", "path": "x", "op": "bogus"}}, trace=_trace([]), final_text=""
            )

    def test_multiple_known_keys_is_a_spec_error(self):
        with self.assertRaises(assertions.AssertionSpecError):
            assertions.evaluate_one({"no_tools": True, "tool_called": {"name": "plan_trip"}}, trace=_trace([]), final_text="")

    def test_validate_spec_catches_bad_shapes(self):
        self.assertIsNotNone(assertions.validate_spec({"tool_called": {}}))
        self.assertIsNotNone(assertions.validate_spec({"call_order": ["only_one"]}))
        self.assertIsNotNone(assertions.validate_spec({"tool_input": {"name": "plan_trip", "path": "x", "op": "iso_within", "value": {}}}))
        self.assertIsNone(assertions.validate_spec({"no_tools": True}))
        self.assertIsNone(assertions.validate_spec({"call_order": ["poi_search", "plan_trip"]}))


# ---------------------------------------------------------- --validate mode --


class ValidateModeTests(unittest.TestCase):
    def test_real_query_bank_is_clean(self):
        problems = run_agent_evals.validate(QUERIES_PATH, FIXTURES_ROOT)
        self.assertEqual(problems, [])

    def test_broken_query_bank_reports_problems(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            (tmp_path / "queries.yaml").write_text(
                """
queries:
  - id: broken_one
    tier: T9
    messages: []
    context: {now: "not-a-timestamp"}
    assertions:
      - tool_called: {}
      - tool_input: {name: plan_trip, path: destination, op: bogus_op, value: x}
"""
            )
            problems = run_agent_evals.validate(tmp_path / "queries.yaml", tmp_path / "fixtures")
        joined = "\n".join(problems)
        self.assertIn("unknown tier", joined)
        self.assertIn("non-empty list", joined)
        self.assertIn("RFC3339", joined)
        self.assertTrue(len(problems) >= 4)

    def test_missing_explicit_fixture_file_is_reported(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            (tmp_path / "queries.yaml").write_text(
                """
queries:
  - id: q1
    tier: T1
    messages: ["hi"]
    context: {now: "2026-07-15T14:00:00-04:00"}
    fixtures:
      plan_trip: [missing.json]
    assertions:
      - tool_called: {name: plan_trip}
"""
            )
            problems = run_agent_evals.validate(tmp_path / "queries.yaml", tmp_path / "fixtures")
        self.assertTrue(any("does not exist" in p for p in problems))


# --------------------------------------------------- fake-model end-to-end --


class EndToEndFakeModelTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        cls.queries = {q["id"]: q for q in run_agent_evals.load_queries(QUERIES_PATH)}

    def setUp(self):
        cache._mem.clear()

    async def test_t2_constraint_query_end_to_end(self):
        # T2: "no bus, I've got a cart" -- the model freely phrases its own
        # destination string ("Costco Wholesale" here, not the literal
        # "Costco" the fixture's filename or query text used) to prove the
        # fuzzy single-file fallback -- not exact-hash matching -- is what
        # resolves this call.
        rounds = [
            {
                "tool_use": [
                    {
                        "id": "tu_1",
                        "name": "plan_trip",
                        "input": {"origin": "user", "destination": "Costco Wholesale", "exclude_modes": ["BUS"]},
                    }
                ],
                "stop_reason": "tool_use",
            },
            {
                "text": ["Take the 6 train straight there, about 40 minutes -- no bus involved so your cart stays easy to manage."],
                "stop_reason": "end_turn",
            },
        ]
        loop_module = _reload_agent_loop_with_fake(rounds)
        result = await run_agent_evals.run_single_query(self.queries["t2_no_bus_cart"], fixtures_root=FIXTURES_ROOT)
        call_count = len(loop_module.client.messages.calls)

        failures = [(r.spec, r.detail) for r in result.assertion_results if not r.ok]
        self.assertTrue(result.ok, failures)
        self.assertEqual(call_count, 2)
        self.assertEqual(result.tool_calls, [("plan_trip", rounds[0]["tool_use"][0]["input"])])

    async def test_t5_multi_stop_query_end_to_end(self):
        # T5: pizza-first multi-stop -- poi_search, then TWO plan_trip legs
        # with different inputs. The explicit call-order fixture mapping
        # (not hash-exact, not single-file) is what disambiguates leg 1
        # from leg 2 here.
        rounds = [
            {
                "tool_use": [{"id": "tu_1", "name": "poi_search", "input": {"query": "pizza", "near": "Union Square"}}],
                "stop_reason": "tool_use",
            },
            {
                "tool_use": [
                    {"id": "tu_2", "name": "plan_trip", "input": {"origin": "Union Square", "destination": "Joe's Pizza"}}
                ],
                "stop_reason": "tool_use",
            },
            {
                "tool_use": [
                    {
                        "id": "tu_3",
                        "name": "plan_trip",
                        "input": {
                            "origin": "Joe's Pizza",
                            "destination": "Barclays Center",
                            "departure_time": "2026-07-15T14:45:00-04:00",
                        },
                    }
                ],
                "stop_reason": "tool_use",
            },
            {
                "text": ["Grab a slice at Joe's Pizza, then continue to Barclays Center -- about 45 minutes total including the stop."],
                "stop_reason": "end_turn",
            },
        ]
        loop_module = _reload_agent_loop_with_fake(rounds)
        result = await run_agent_evals.run_single_query(self.queries["t5_pizza_first"], fixtures_root=FIXTURES_ROOT)
        call_count = len(loop_module.client.messages.calls)

        failures = [(r.spec, r.detail) for r in result.assertion_results if not r.ok]
        self.assertTrue(result.ok, failures)
        self.assertEqual(call_count, 4)
        self.assertEqual([name for name, _ in result.tool_calls], ["poi_search", "plan_trip", "plan_trip"])

    async def test_t6_refusal_query_end_to_end_zero_tools(self):
        rounds = [
            {
                "text": [
                    "I can only help with New York City transit, so I can't plan driving directions to Boston -- "
                    "happy to help you get around NYC by subway or bus instead."
                ],
                "stop_reason": "end_turn",
            }
        ]
        loop_module = _reload_agent_loop_with_fake(rounds)
        result = await run_agent_evals.run_single_query(self.queries["t6_drive_to_boston"], fixtures_root=FIXTURES_ROOT)
        call_count = len(loop_module.client.messages.calls)

        failures = [(r.spec, r.detail) for r in result.assertion_results if not r.ok]
        self.assertTrue(result.ok, failures)
        self.assertEqual(result.tool_calls, [])
        self.assertEqual(call_count, 1)


if __name__ == "__main__":
    unittest.main()
