"""Layer-1 tests for the P2 agent tools (accessibility_status, lookup_facts),
the transit_facts.md data file, the P2 prompt clause, the per-turn timing log
line in loop.py, and the full 7-tool registry.

Follows test_agent_tools_p1.py's conventions: real imports, only the actual
I/O boundary each tool touches (httpx) is mocked -- never the whole module.
The fake client classes live in tests/_fake_http_tools.py, shared with
test_agent_tools_p1.py.
"""

from __future__ import annotations

import io
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

from app.services.agent import prompt as agent_prompt
from app.services.agent import tools as agent_tools
from app.services.agent.tools import _http, accessibility_status, lookup_facts
from app.utils import cache
from tests._fake_http_tools import make_tool_ctx as _ctx
from tests._fake_http_tools import recording_get_client as _recording_get_client

from tests.test_agent_loop import _AgentLoopHelpers, _load_agent_loop, _test_registry


def _outage(
    station="34 St-Penn Station",
    borough="Manhattan",
    equipment="EL256",
    equipmenttype="EL",
    serving="Street to Mezzanine",
    outagedate="07/15/2026 08:00:00",
    estimatedreturntoservice="07/15/2026 18:00:00",
):
    return {
        "station": station,
        "borough": borough,
        "equipment": equipment,
        "equipmenttype": equipmenttype,
        "serving": serving,
        "trainno": "1/2/3",
        "outagedate": outagedate,
        "estimatedreturntoservice": estimatedreturntoservice,
    }


class AccessibilityStatusTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        cache._mem.clear()

    async def test_station_required(self):
        result = await accessibility_status.execute({"station": ""}, _ctx())
        self.assertFalse(result.ok)

    async def test_parses_realistic_feed_and_matches_station(self):
        payload = {"outages": [_outage(), _outage(station="Atlantic Av-Barclays Ctr", equipmenttype="ES")]}
        client_class = _recording_get_client(payload)
        with patch.object(_http.httpx, "AsyncClient", client_class):
            result = await accessibility_status.execute({"station": "34 St-Penn Station"}, _ctx())
        self.assertTrue(result.ok)
        self.assertEqual(result.data["station_matched"], "34 St-Penn Station")
        self.assertEqual(len(result.data["elevator_outages"]), 1)
        self.assertEqual(result.data["elevator_outages"][0]["equipment"], "EL256")
        self.assertEqual(result.data["elevator_outages"][0]["serving"], "Street to Mezzanine")
        self.assertEqual(result.data["escalator_outages_count"], 0)

    async def test_loose_matching_on_partial_station_name(self):
        payload = {"outages": [_outage(station="34 St-Penn Station")]}
        client_class = _recording_get_client(payload)
        with patch.object(_http.httpx, "AsyncClient", client_class):
            result = await accessibility_status.execute({"station": "Penn Station"}, _ctx())
        self.assertTrue(result.ok)
        self.assertEqual(len(result.data["elevator_outages"]), 1)

    async def test_no_match_is_a_positive_signal_not_an_error(self):
        payload = {"outages": [_outage(station="34 St-Penn Station")]}
        client_class = _recording_get_client(payload)
        with patch.object(_http.httpx, "AsyncClient", client_class):
            result = await accessibility_status.execute({"station": "Coney Island-Stillwell Av"}, _ctx())
        self.assertTrue(result.ok)
        self.assertEqual(result.data["elevator_outages"], [])
        self.assertIn("no elevator outages", result.summary)

    async def test_elevators_vs_escalators_split(self):
        payload = {
            "outages": [
                _outage(equipment="EL1", equipmenttype="EL"),
                _outage(equipment="ES1", equipmenttype="ES"),
                _outage(equipment="ES2", equipmenttype="ES"),
            ]
        }
        client_class = _recording_get_client(payload)
        with patch.object(_http.httpx, "AsyncClient", client_class):
            result = await accessibility_status.execute({"station": "34 St-Penn Station"}, _ctx())
        self.assertEqual(len(result.data["elevator_outages"]), 1)
        self.assertEqual(result.data["escalator_outages_count"], 2)

    async def test_borough_filter_narrows_match(self):
        payload = {
            "outages": [
                _outage(station="Broadway Junction", borough="Brooklyn", equipment="EL_BK"),
                _outage(station="Broadway Junction", borough="Manhattan", equipment="EL_MH"),
            ]
        }
        client_class = _recording_get_client(payload)
        with patch.object(_http.httpx, "AsyncClient", client_class):
            result = await accessibility_status.execute(
                {"station": "Broadway Junction", "borough": "Brooklyn"}, _ctx()
            )
        equipment_ids = {o["equipment"] for o in result.data["elevator_outages"]}
        self.assertEqual(equipment_ids, {"EL_BK"})

    async def test_cache_hit_skips_second_http_call(self):
        payload = {"outages": [_outage()]}
        client_class = _recording_get_client(payload)
        with patch.object(_http.httpx, "AsyncClient", client_class):
            first = await accessibility_status.execute({"station": "34 St-Penn Station"}, _ctx())
            second = await accessibility_status.execute({"station": "34 St-Penn Station"}, _ctx())
        self.assertTrue(first.ok)
        self.assertTrue(second.ok)
        self.assertEqual(len(client_class.requests), 1)

    async def test_fetch_failure_is_fail_open_not_a_crash(self):
        client_class = _recording_get_client({}, status_code=404)
        with patch.object(_http.httpx, "AsyncClient", client_class):
            result = await accessibility_status.execute({"station": "34 St-Penn Station"}, _ctx())
        self.assertFalse(result.ok)
        self.assertEqual(result.error, "elevator status is temporarily unavailable")

    async def test_request_error_is_fail_open(self):
        import httpx

        class _RaisingClient:
            def __init__(self, *args, **kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *_args):
                return False

            async def get(self, url, params=None):
                raise httpx.ConnectError("boom", request=None)

        with patch.object(_http.httpx, "AsyncClient", _RaisingClient):
            result = await accessibility_status.execute({"station": "34 St-Penn Station"}, _ctx())
        self.assertFalse(result.ok)
        self.assertEqual(result.error, "elevator status is temporarily unavailable")

    async def test_malformed_payload_shape_yields_no_outages_not_a_crash(self):
        client_class = _recording_get_client({"unexpected": "shape"})
        with patch.object(_http.httpx, "AsyncClient", client_class):
            result = await accessibility_status.execute({"station": "34 St-Penn Station"}, _ctx())
        self.assertTrue(result.ok)
        self.assertEqual(result.data["elevator_outages"], [])


class LookupFactsTests(unittest.IsolatedAsyncioTestCase):
    async def test_topic_required(self):
        result = await lookup_facts.execute({"topic": ""}, _ctx())
        self.assertFalse(result.ok)

    async def test_every_section_retrievable_by_exact_slug(self):
        for slug in lookup_facts._SECTION_ORDER:
            result = await lookup_facts.execute({"topic": slug}, _ctx())
            self.assertTrue(result.ok, slug)
            self.assertEqual(result.data["topic"], slug)
            self.assertTrue(result.data["text"])

    async def test_fuzzy_match_on_header_substring(self):
        result = await lookup_facts.execute({"topic": "fare"}, _ctx())
        self.assertTrue(result.ok)
        self.assertIn("fare", result.data["topic"])

    async def test_fuzzy_match_on_natural_phrase(self):
        result = await lookup_facts.execute({"topic": "elevator accessibility"}, _ctx())
        self.assertTrue(result.ok)
        self.assertIn("accessibility", result.data["topic"])

    async def test_unknown_topic_lists_available_topics(self):
        result = await lookup_facts.execute({"topic": "zzz-not-a-real-topic-zzz"}, _ctx())
        self.assertFalse(result.ok)
        for slug in lookup_facts._SECTION_ORDER:
            self.assertIn(slug, result.error)

    async def test_truncation_cap_applied(self):
        with patch.dict(
            lookup_facts._SECTIONS,
            {"huge": {"header": "Huge", "body": "x" * 5000}},
        ):
            result = await lookup_facts.execute({"topic": "huge"}, _ctx())
        self.assertTrue(result.ok)
        self.assertLessEqual(len(result.data["text"]), lookup_facts.MAX_DIGEST_CHARS)


class TransitFactsFileTests(unittest.TestCase):
    def test_sections_parse_and_none_are_empty(self):
        self.assertGreaterEqual(len(lookup_facts._SECTION_ORDER), 6)
        for slug in lookup_facts._SECTION_ORDER:
            section = lookup_facts._SECTIONS[slug]
            self.assertTrue(section["header"].strip(), slug)
            self.assertTrue(section["body"].strip(), slug)

    def test_expected_topics_present(self):
        expected_substrings = ["fares", "transfers", "accessibility", "airport"]
        joined = " ".join(lookup_facts._SECTION_ORDER)
        for substr in expected_substrings:
            self.assertIn(substr, joined)


class PromptGuardP2Tests(unittest.TestCase):
    def test_factual_grounding_clause_present(self):
        self.assertIn("FACTUAL GROUNDING", agent_prompt.SYSTEM_PROMPT)
        self.assertIn("lookup_facts", agent_prompt.SYSTEM_PROMPT)
        self.assertIn("accessibility_status", agent_prompt.SYSTEM_PROMPT)


class RegistryP2Tests(unittest.TestCase):
    def test_seven_tools_present_all_strict(self):
        expected = {
            "plan_trip",
            "transit_snapshot",
            "event_lookup",
            "poi_search",
            "venue_crowd_window",
            "accessibility_status",
            "lookup_facts",
        }
        self.assertEqual(set(agent_tools.TOOL_REGISTRY.keys()), expected)
        for name, spec in agent_tools.TOOL_REGISTRY.items():
            self.assertTrue(spec.schema.get("strict"), name)
            self.assertFalse(spec.schema["input_schema"].get("additionalProperties", True), name)
            self.assertGreater(spec.timeout_s, 0, name)

    def test_p2_tool_timeouts(self):
        self.assertEqual(agent_tools.TOOL_REGISTRY["accessibility_status"].timeout_s, 8.0)
        self.assertEqual(agent_tools.TOOL_REGISTRY["lookup_facts"].timeout_s, 2.0)


class TimingLogLineTests(_AgentLoopHelpers, unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        cls.loop = _load_agent_loop()

    def setUp(self):
        cache._mem.clear()

    async def test_turn_end_emits_agent_timing_log_line(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            await self._run(
                [{"text": ["ok, taking the Q"], "stop_reason": "end_turn"}],
                session_id="timing-log-session",
            )
        lines = [line for line in buf.getvalue().splitlines() if line.startswith("[agent] turn=")]
        self.assertEqual(len(lines), 1)
        line = lines[0]
        self.assertIn("sess=timing", line)
        self.assertIn("rounds=1", line)
        self.assertIn("tools=0", line)
        self.assertIn("model_ms=", line)
        self.assertIn("tools_ms=", line)
        self.assertIn("total_ms=", line)
        self.assertIn("in_tok=", line)
        self.assertIn("out_tok=", line)
        self.assertIn("stop=end_turn", line)
        self.assertNotIn("ok, taking the Q", line)

    async def test_timing_log_counts_tools(self):
        buf = io.StringIO()
        rounds = [
            {"tool_use": [{"id": "tu_1", "name": "ok_tool", "input": {}}], "stop_reason": "tool_use"},
            {"text": ["done"], "stop_reason": "end_turn"},
        ]
        with redirect_stdout(buf):
            await self._run(rounds, tool_registry=_test_registry(), session_id="timing-log-tools")
        line = next(line for line in buf.getvalue().splitlines() if line.startswith("[agent] turn="))
        self.assertIn("rounds=2", line)
        self.assertIn("tools=1", line)


if __name__ == "__main__":
    unittest.main()
