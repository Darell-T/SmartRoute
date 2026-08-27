"""Layer-1 tests for P2 leaf tools and the model-led public capability surface,
the transit_facts.md data file, the P2 prompt clause, the per-turn timing log
line in loop.py, and the full eight-capability registry.

Follows test_agent_tools_p1.py's conventions: real imports, only the actual
I/O boundary each tool touches (httpx) is mocked -- never the whole module.
The fake client classes live in tests/_fake_http_tools.py, shared with
test_agent_tools_p1.py.
"""

from __future__ import annotations

import io
import unittest
from contextlib import redirect_stdout
from datetime import date
from unittest.mock import patch

from app.services import cache
from app.services.agent.tools import (
    ToolSpec,
    declare_goals,
)
from app.services.agent.tools import (
    provider_http as _http,
)
from app.services.agent.tools.transit import accessibility_status, lookup_facts

from tests._fake_http_tools import make_tool_ctx as _ctx
from tests._fake_http_tools import recording_get_client as _recording_get_client
from tests.test_agent_loop import _AgentLoopHelpers, _load_agent_loop, _test_registry


def _model_led_test_registry() -> dict[str, ToolSpec]:
    return {
        **_test_registry(),
        "declare_goals": ToolSpec(
            schema=declare_goals.DECLARE_GOALS_SCHEMA,
            executor=declare_goals.execute,
            label_fn=lambda _input: "Understanding the request…",
            timeout_s=2.0,
        ),
    }


def _general_round(*tool_calls: dict) -> dict:
    return {
        "tool_use": [
            {
                "id": "tu_goals",
                "name": "declare_goals",
                "input": {
                    "goals": [
                        {
                            "goal_key": "response",
                            "kind": "general_response",
                            "depends_on": [],
                        }
                    ]
                },
            },
            *tool_calls,
        ],
        "stop_reason": "tool_use",
    }


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
        assert not result.ok

    async def test_parses_realistic_feed_and_matches_station(self):
        payload = {"outages": [_outage(), _outage(station="Atlantic Av-Barclays Ctr", equipmenttype="ES")]}
        client_class = _recording_get_client(payload)
        with patch.object(_http.httpx, "AsyncClient", client_class):
            result = await accessibility_status.execute({"station": "34 St-Penn Station"}, _ctx())
        assert result.ok
        assert result.data["station_matched"] == "34 St-Penn Station"
        assert len(result.data["elevator_outages"]) == 1
        assert result.data["elevator_outages"][0]["equipment"] == "EL256"
        assert result.data["elevator_outages"][0]["serving"] == "Street to Mezzanine"
        assert result.data["escalator_outages_count"] == 0

    async def test_loose_matching_on_partial_station_name(self):
        payload = {"outages": [_outage(station="34 St-Penn Station")]}
        client_class = _recording_get_client(payload)
        with patch.object(_http.httpx, "AsyncClient", client_class):
            result = await accessibility_status.execute({"station": "Penn Station"}, _ctx())
        assert result.ok
        assert len(result.data["elevator_outages"]) == 1

    async def test_no_match_is_unavailable_not_a_positive_signal(self):
        payload = {"outages": [_outage(station="34 St-Penn Station")]}
        client_class = _recording_get_client(payload)
        with patch.object(_http.httpx, "AsyncClient", client_class):
            result = await accessibility_status.execute({"station": "Coney Island-Stillwell Av"}, _ctx())
        assert not result.ok
        assert result.outcome == "unavailable"
        assert "no accessibility record matched" in result.error

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
        assert len(result.data["elevator_outages"]) == 1
        assert result.data["escalator_outages_count"] == 2

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
        assert equipment_ids == {"EL_BK"}

    async def test_cache_hit_skips_second_http_call(self):
        payload = {"outages": [_outage()]}
        client_class = _recording_get_client(payload)
        with patch.object(_http.httpx, "AsyncClient", client_class):
            first = await accessibility_status.execute({"station": "34 St-Penn Station"}, _ctx())
            second = await accessibility_status.execute({"station": "34 St-Penn Station"}, _ctx())
        assert first.ok
        assert second.ok
        assert len(client_class.requests) == 1

    async def test_fetch_failure_is_fail_open_not_a_crash(self):
        client_class = _recording_get_client({}, status_code=404)
        with patch.object(_http.httpx, "AsyncClient", client_class):
            result = await accessibility_status.execute({"station": "34 St-Penn Station"}, _ctx())
        assert not result.ok
        assert result.error == "elevator status is temporarily unavailable"

    async def test_request_error_is_fail_open(self):
        import httpx

        class _RaisingClient:
            def __init__(self, *args, **kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *_args):
                return False

            async def get(self, _url, _params=None):
                raise httpx.ConnectError("boom", request=None)

        with patch.object(_http.httpx, "AsyncClient", _RaisingClient):
            result = await accessibility_status.execute({"station": "34 St-Penn Station"}, _ctx())
        assert not result.ok
        assert result.error == "elevator status is temporarily unavailable"

    async def test_malformed_payload_shape_yields_no_outages_not_a_crash(self):
        client_class = _recording_get_client({"unexpected": "shape"})
        with patch.object(_http.httpx, "AsyncClient", client_class):
            result = await accessibility_status.execute({"station": "34 St-Penn Station"}, _ctx())
        assert not result.ok
        assert result.outcome == "unavailable"


class LookupFactsTests(unittest.IsolatedAsyncioTestCase):
    async def test_topic_required(self):
        result = await lookup_facts.execute({"topic": ""}, _ctx())
        assert not result.ok

    async def test_every_section_retrievable_by_exact_slug(self):
        for slug in lookup_facts._SECTION_ORDER:
            result = await lookup_facts.execute({"topic": slug}, _ctx())
            assert result.ok, slug
            assert result.data["topic"] == slug
            assert result.data["text"]

    async def test_fuzzy_match_on_header_substring(self):
        result = await lookup_facts.execute({"topic": "fare"}, _ctx())
        assert result.ok
        assert "fare" in result.data["topic"]

    async def test_fare_facts_are_effective_dated_and_sourced(self):
        result = await lookup_facts.execute({"topic": "fare"}, _ctx())
        assert result.ok
        assert "$3.00" in result.data["text"]
        assert "$35.00" in result.data["text"]
        assert "MetroCard sales ended" in result.data["text"]
        assert result.data["source"]["version"] == lookup_facts.FARE_FACTS_VERSION
        assert result.data["source"]["effective_date"] == "2026-01-04"
        assert result.data["source"]["url"] == lookup_facts.FARE_FACTS_SOURCE_URL

    async def test_expired_fare_facts_fail_safely(self):
        with patch.object(lookup_facts, "date") as fake_date:
            fake_date.today.return_value = date(2026, 10, 28)
            result = await lookup_facts.execute({"topic": "fare"}, _ctx())
        assert not result.ok
        assert "require review" in result.error

    async def test_fuzzy_match_on_natural_phrase(self):
        result = await lookup_facts.execute({"topic": "elevator accessibility"}, _ctx())
        assert result.ok
        assert "accessibility" in result.data["topic"]

    async def test_unknown_topic_lists_available_topics(self):
        result = await lookup_facts.execute({"topic": "zzz-not-a-real-topic-zzz"}, _ctx())
        assert not result.ok
        for slug in lookup_facts._SECTION_ORDER:
            assert slug in result.error

    async def test_truncation_cap_applied(self):
        with patch.dict(
            lookup_facts._SECTIONS,
            {"huge": {"header": "Huge", "body": "x" * 5000}},
        ):
            result = await lookup_facts.execute({"topic": "huge"}, _ctx())
        assert result.ok
        assert len(result.data["text"]) <= lookup_facts.MAX_DIGEST_CHARS


class TransitFactsFileTests(unittest.TestCase):
    def test_sections_parse_and_none_are_empty(self):
        assert len(lookup_facts._SECTION_ORDER) >= 6
        for slug in lookup_facts._SECTION_ORDER:
            section = lookup_facts._SECTIONS[slug]
            assert section["header"].strip(), slug
            assert section["body"].strip(), slug

    def test_expected_topics_present(self):
        expected_substrings = ["fares", "transfers", "accessibility", "airport"]
        joined = " ".join(lookup_facts._SECTION_ORDER)
        for substr in expected_substrings:
            assert substr in joined


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
                [
                    _general_round(
                        {
                            "id": "tu_done",
                            "name": "complete_turn",
                            "input": {
                                "goal_keys": ["response"],
                                "outcome": "answer",
                                "message": "ok, taking the Q",
                            },
                        }
                    )
                ],
                tool_registry=_model_led_test_registry(),
                session_id="timing-log-session",
            )
        lines = [line for line in buf.getvalue().splitlines() if line.startswith("[agent] turn=")]
        assert len(lines) == 1
        line = lines[0]
        assert "sess=timing" in line
        assert "rounds=1" in line
        assert "model_tool_uses=2" in line
        assert "provider_tool_executions=1" in line
        assert "model_ms=" in line
        assert "tools_ms=" in line
        assert "intent_ms=" in line
        assert "session_load_ms=" in line
        assert "place_resolution_ms=" in line
        assert "route_provider_ms=" in line
        assert "mta_ms=" in line
        assert "ticketmaster_ms=" in line
        assert "arrival_lookup_ms=" in line
        assert "scoring_ms=" in line
        assert "stream_finalize_ms=" in line
        assert "model_calls=" in line
        assert "model_tool_uses=" in line
        assert "provider_tool_executions=" in line
        assert line.count("model_tool_uses=") == 1
        assert line.count("provider_tool_executions=") == 1
        assert "retry_count=" in line
        assert "total_ms=" in line
        assert "in_tok=" in line
        assert "out_tok=" in line
        assert "stop=end_turn" in line
        assert "ok, taking the Q" not in line

    async def test_timing_log_counts_tools(self):
        buf = io.StringIO()
        rounds = [
            _general_round({"id": "tu_1", "name": "ok_tool", "input": {}}),
            {
                "tool_use": [
                    {
                        "id": "tu_2",
                        "name": "complete_turn",
                        "input": {
                            "goal_keys": ["response"],
                            "outcome": "answer",
                            "message": "done",
                        },
                    }
                ],
                "stop_reason": "tool_use",
            },
        ]
        with redirect_stdout(buf):
            await self._run(
                rounds,
                tool_registry=_model_led_test_registry(),
                session_id="timing-log-tools",
            )
        line = next(line for line in buf.getvalue().splitlines() if line.startswith("[agent] turn="))
        assert "rounds=2" in line
        assert "model_tool_uses=3" in line
        assert "provider_tool_executions=2" in line


if __name__ == "__main__":
    unittest.main()
