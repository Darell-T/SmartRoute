import os
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.services import incident_monitor
from app.services.trips import incidents as trip_incidents
from app.services.trips.incident_context import CandidateStopAssociation, CandidateStopContext
from app.services.trips.incident_matching import Cached511NYSearchTool


class _FakeResponse:
    def __init__(self, content, finish_reason, tool_calls=None):
        self.content = content
        self.finish_reason = finish_reason
        self.tool_calls = tool_calls or []


class _FakeToolCall:
    def __init__(self, name, arguments, call_id):
        self.id = call_id
        self.function = SimpleNamespace(name=name, arguments=arguments)


def _call(name, arguments, call_id, call_type):
    result = _FakeToolCall(name, arguments, call_id)
    result.call_type = call_type
    return result


class _FakeChatSession:
    def __init__(self, responses):
        self._responses = list(responses)
        self.appended = []
        self.sample_calls = 0

    def append(self, message):
        self.appended.append(message)

    def sample(self):
        self.sample_calls += 1
        if not self._responses:
            raise AssertionError("sample() called more times than expected")
        return self._responses.pop(0)


class _FakeChatAPI:
    def __init__(self, responses):
        self.responses = responses
        self.kwargs = None
        self.session = None

    def create(self, **kwargs):
        self.kwargs = kwargs
        self.session = _FakeChatSession(self.responses)
        return self.session


class _FakeClient:
    def __init__(self, responses):
        self.chat = _FakeChatAPI(responses)


class IncidentMonitorHelperTests(unittest.TestCase):
    def test_compact_context_keeps_late_candidate_ids_after_stop_cap(self):
        first_candidate_stops = [
            CandidateStopContext(
                f"stop-{index}", f"Stop {index}", 40.65, -73.96,
                [CandidateStopAssociation("candidate-0", mode="subway", route_id="Q")],
            )
            for index in range(81)
        ]
        late_candidate = CandidateStopContext(
            "late", "Late Stop", 40.66, -73.97,
            [CandidateStopAssociation("candidate-1", mode="bus", route_id="B68")],
        )
        contexts = [*first_candidate_stops, late_candidate]
        prompt_context = incident_monitor._compact_candidate_context(contexts)
        self.assertIn('"candidate_route_ids":["candidate-0","candidate-1"]', prompt_context)
        tool = Cached511NYSearchTool(lambda: {
            "status": "fresh",
            "incidents": [
                {"source_id": "q", "latitude": 40.65, "longitude": -73.96},
                {"source_id": "b", "latitude": 40.66, "longitude": -73.97},
            ],
        }, contexts)
        first_result = tool.execute({"candidate_route_ids": ["candidate-0"]})
        late_result = tool.execute({"candidate_route_ids": ["candidate-1"]})
        self.assertEqual(first_result["status"], "complete")
        self.assertEqual(late_result["status"], "complete")
        self.assertEqual({row["source_id"] for row in first_result["incidents"]}, {"q"})
        self.assertEqual({row["source_id"] for row in late_result["incidents"]}, {"b"})
    def test_xai_timeout_is_sanitized(self):
        with patch.dict(os.environ, {"XAI_INCIDENT_TIMEOUT_S": "bad"}):
            self.assertEqual(incident_monitor._bounded_timeout_seconds(), 12.0)
        with patch.dict(os.environ, {"XAI_INCIDENT_TIMEOUT_S": "999"}):
            self.assertEqual(incident_monitor._bounded_timeout_seconds(), 30.0)
    def test_normalize_station_names_dedupes_and_trims(self):
        normalized = incident_monitor._normalize_station_names(
            [
                "  Church Avenue  ",
                "",
                "church avenue",
                None,
                "Prospect   Park",
                "Atlantic Avenue - Barclays Center",
            ]
        )

        self.assertEqual(
            normalized,
            [
                "Church Avenue",
                "Prospect Park",
                "Atlantic Avenue - Barclays Center",
            ],
        )

    def test_parse_json_object_accepts_bare_json(self):
        payload = incident_monitor._parse_json_object(
            '{"incidents":[{"location":"Flatbush Ave","nearby_station":"Church Avenue","severity":"low","description":"Police activity reported nearby.","source":"@NYScanner"}]}'
        )

        self.assertIsInstance(payload, dict)
        self.assertEqual(payload["incidents"][0]["location"], "Flatbush Ave")

    def test_parse_json_object_accepts_fenced_json(self):
        payload = incident_monitor._parse_json_object(
            """```json
            {"incidents":[{"location":"Caton Ave","nearby_station":"Church Avenue","severity":"medium","description":"Fire department activity near the station.","source":"@CitizenAppNYC"}]}
            ```"""
        )

        self.assertIsInstance(payload, dict)
        self.assertEqual(payload["incidents"][0]["nearby_station"], "Church Avenue")

    def test_parse_json_object_returns_none_for_invalid_json(self):
        self.assertIsNone(incident_monitor._parse_json_object("not valid json"))

    def test_normalize_incident_payload_strips_fields_and_drops_invalid_items(self):
        payload = {
            "incidents": [
                {
                    "location": "  Flatbush Ave / Caton Ave  ",
                    "nearby_station": " Church Avenue ",
                    "severity": "HIGH",
                    "description": "  Police activity outside the station entrance.  ",
                    "source": " @NYScanner ",
                    "ignored": "drop me",
                },
                {
                    "location": "",
                    "nearby_station": "Church Avenue",
                    "severity": "medium",
                    "description": "Missing location should be removed.",
                    "source": "@CitizenAppNYC",
                },
                {
                    "location": "Prospect Park West",
                    "nearby_station": "Prospect Park",
                    "severity": "urgent",
                    "description": "Invalid severity should be removed.",
                    "source": "@NYCTSubway",
                },
                "not-a-dict",
            ]
        }

        normalized = incident_monitor._normalize_incident_payload(payload)

        self.assertEqual(
            normalized,
            {
                "incidents": [
                    {
                        "location": "Flatbush Ave / Caton Ave",
                        "nearby_station": "Church Avenue",
                        "severity": "high",
                        "description": "Police activity outside the station entrance.",
                        "source": "@NYScanner",
                    }
                ]
            },
        )

    def test_normalize_incident_payload_matches_station_aliases(self):
        payload = {
            "incidents": [
                {
                    "location": "Flatbush Ave / Caton Ave",
                    "nearby_station": "Church Avenue",
                    "severity": "medium",
                    "description": "Police activity outside the station.",
                    "source": "@NYScanner",
                }
            ]
        }

        normalized = incident_monitor._normalize_incident_payload(
            payload,
            ["Church Av", "Prospect Park"],
        )

        self.assertEqual(
            normalized,
            {
                "incidents": [
                    {
                        "location": "Flatbush Ave / Caton Ave",
                        "nearby_station": "Church Av",
                        "severity": "medium",
                        "description": "Police activity outside the station.",
                        "source": "@NYScanner",
                    }
                ]
            },
        )

    def test_normalize_incident_payload_requires_incidents_list(self):
        self.assertEqual(
            incident_monitor._normalize_incident_payload({"incidents": "bad-shape"}),
            {"incidents": []},
        )

    def test_normalize_incident_payload_drops_non_string_fields(self):
        payload = {
            "incidents": [
                {
                    "location": ["Flatbush Ave", "Caton Ave"],
                    "nearby_station": "Church Avenue",
                    "severity": "medium",
                    "description": "Police activity outside the station.",
                    "source": {"handle": "@NYScanner"},
                }
            ]
        }

        self.assertEqual(
            incident_monitor._normalize_incident_payload(payload),
            {"incidents": []},
        )


class IncidentMonitorAgentTests(unittest.TestCase):
    def test_candidate_context_exposes_real_ids_and_only_selected_511_evidence_reaches_final_result(self):
        routes = [[{
            "type": "SUBWAY", "route_id": "Q", "departure_stop": "Church Avenue", "arrival_stop": "Prospect Park",
            "departure_coords": {"latitude": 40.6500, "longitude": -73.9630},
            "arrival_coords": {"latitude": 40.6610, "longitude": -73.9620},
        }]]
        gtfs = SimpleNamespace(_pattern_index=SimpleNamespace(get_intermediate_stops_with_coords=lambda *args: ([
            {"id": "D24", "name": "Church Avenue", "lat": 40.6500, "lng": -73.9630},
        ], {})))
        contexts = trip_incidents.build_candidate_stop_context(gtfs, routes)
        snapshot = {"status": "fresh", "incidents": [
            {"source_id": "official-1", "source": "511ny", "latitude": 40.6501, "longitude": -73.9631, "reported_at": "2026-07-22T12:00:00Z", "description": "Road closure on Church Avenue."},
            {"source_id": "nearby-unselected", "source": "511ny", "latitude": 40.6502, "longitude": -73.9632, "description": "Separate nearby incident."},
        ]}
        local_tool = Cached511NYSearchTool(lambda: snapshot, contexts)
        self.assertEqual(local_tool.execute({"candidate_route_ids": ["not-a-candidate"]})["incidents"], [])
        self.assertEqual({row["source_id"] for row in local_tool.execute({"candidate_route_ids": ["candidate-0"]})["incidents"]}, {"official-1", "nearby-unselected"})
        local_call = _call("search_cached_511ny_incidents", '{"candidate_route_ids":["candidate-0"]}', "local-1", "client_side_tool")
        fake_client = _FakeClient([
            _FakeResponse("", "tool_calls", [local_call]),
            _FakeResponse('{"incidents":[{"location":"Church Avenue","nearby_station":"Church Avenue","severity":"high","description":"Road closure reported by @NYScanner.","source":"@NYScanner","source_ref":"official-1"}]}', "stop"),
        ])
        with patch.object(incident_monitor, "client", fake_client), patch.object(incident_monitor, "system", lambda value: value), patch.object(incident_monitor, "user", lambda value: value), patch.object(incident_monitor, "x_search", lambda: "x"), patch.object(incident_monitor, "web_search", lambda: "web"), patch.object(incident_monitor, "tool", lambda *args: "local"), patch.object(incident_monitor, "get_tool_call_type", lambda value: value.call_type), patch.object(incident_monitor, "tool_result", lambda *args, **kwargs: "tool-result"), patch.object(incident_monitor, "_XAI_API_KEY", "test-key"):
            result = incident_monitor._run_incident_agent("Church Avenue, Prospect Park", ["Church Avenue", "Prospect Park"], snapshot, contexts)
        self.assertIn("candidate-0", fake_client.chat.session.appended[0])
        self.assertIn('"route_id":"Q"', fake_client.chat.session.appended[0])
        self.assertEqual(len(result["incidents"]), 1)
        self.assertEqual(result["incidents"][0]["source_id"], "official-1")
        self.assertEqual(result["incidents"][0]["latitude"], 40.6501)
        self.assertEqual(set(result["incidents"][0]["sources"]), {"511ny", "@NYScanner"})
    def test_fresh_snapshot_requires_x_web_and_local_evidence_for_complete_empty_result(self):
        x_call = _call("x_search", "{}", "x1", "x_search_tool")
        web_call = _call("web_search", "{}", "w1", "web_search_tool")
        local_call = _call("search_cached_511ny_incidents", '{"candidate_route_ids":["candidate-0"]}', "l1", "client_side_tool")
        fake_client = _FakeClient([
            _FakeResponse("", "tool_calls", [x_call]), _FakeResponse("", "tool_calls", [web_call]),
            _FakeResponse("", "tool_calls", [local_call]), _FakeResponse('{"incidents":[]}', "stop"),
        ])
        with patch.object(incident_monitor, "client", fake_client), patch.object(incident_monitor, "system", lambda value: value), patch.object(incident_monitor, "user", lambda value: value), patch.object(incident_monitor, "x_search", lambda: "x-tool"), patch.object(incident_monitor, "web_search", lambda: "web-tool"), patch.object(incident_monitor, "tool", lambda *args: "local-tool"), patch.object(incident_monitor, "get_tool_call_type", lambda value: value.call_type), patch.object(incident_monitor, "tool_result", lambda *args, **kwargs: "tool-result"), patch.object(incident_monitor, "_XAI_API_KEY", "test-key"):
            result = incident_monitor._run_incident_agent("Church Avenue", ["Church Avenue"], {"incidents": [], "status": "fresh"})
        self.assertEqual(fake_client.chat.kwargs["tools"], ["x-tool", "web-tool", "local-tool"])
        self.assertEqual(result["incidents"], [])
        self.assertEqual(result["scan_metadata"]["status"], "complete")
        self.assertEqual(set(result["scan_metadata"]["sources"]["completed"]), {"x_search", "web_search", "cached_511ny"})

    def test_final_without_tools_is_partial_even_with_fresh_snapshot(self):
        fake_client = _FakeClient([_FakeResponse('{"incidents":[]}', "stop")])
        with patch.object(incident_monitor, "client", fake_client), patch.object(incident_monitor, "system", lambda value: value), patch.object(incident_monitor, "user", lambda value: value), patch.object(incident_monitor, "x_search", lambda: "x"), patch.object(incident_monitor, "web_search", lambda: "web"), patch.object(incident_monitor, "tool", lambda *args: "local"), patch.object(incident_monitor, "_XAI_API_KEY", "test-key"):
            result = incident_monitor._run_incident_agent("Church Avenue", ["Church Avenue"], {"incidents": [], "status": "fresh"})
        self.assertEqual(result["scan_metadata"]["status"], "partial")

    def test_stale_or_unavailable_snapshot_cannot_be_complete_after_server_searches(self):
        x_call = _call("x_search", "{}", "x1", "x_search_tool")
        web_call = _call("web_search", "{}", "w1", "web_search_tool")
        local_call = _call("search_cached_511ny_incidents", '{"candidate_route_ids":["candidate-0"]}', "l1", "client_side_tool")
        for status in ("stale", "unavailable"):
            fake_client = _FakeClient([_FakeResponse("", "tool_calls", [x_call]), _FakeResponse("", "tool_calls", [web_call]), _FakeResponse("", "tool_calls", [local_call]), _FakeResponse('{"incidents":[]}', "stop")])
            with patch.object(incident_monitor, "client", fake_client), patch.object(incident_monitor, "system", lambda value: value), patch.object(incident_monitor, "user", lambda value: value), patch.object(incident_monitor, "x_search", lambda: "x"), patch.object(incident_monitor, "web_search", lambda: "web"), patch.object(incident_monitor, "tool", lambda *args: "local"), patch.object(incident_monitor, "get_tool_call_type", lambda value: value.call_type), patch.object(incident_monitor, "tool_result", lambda *args, **kwargs: "tool-result"), patch.object(incident_monitor, "_XAI_API_KEY", "test-key"):
                result = incident_monitor._run_incident_agent("Church Avenue", ["Church Avenue"], {"incidents": [], "status": status})
            self.assertEqual(result["scan_metadata"]["status"], "partial")
            self.assertEqual(set(result["scan_metadata"]["sources"]["completed"]), {"x_search", "web_search"} | ({"cached_511ny"} if status == "stale" else set()))

    def test_round_cap_after_completed_sources_is_partial(self):
        calls = [_call("x_search", "{}", f"x{index}", "x_search_tool") for index in range(incident_monitor._MAX_TOTAL_TOOL_CALLS + 1)]
        fake_client = _FakeClient([_FakeResponse("", "tool_calls", [call]) for call in calls])
        with patch.object(incident_monitor, "client", fake_client), patch.object(incident_monitor, "system", lambda value: value), patch.object(incident_monitor, "user", lambda value: value), patch.object(incident_monitor, "x_search", lambda: "x"), patch.object(incident_monitor, "web_search", lambda: "web"), patch.object(incident_monitor, "tool", lambda *args: "local"), patch.object(incident_monitor, "get_tool_call_type", lambda value: value.call_type), patch.object(incident_monitor, "_XAI_API_KEY", "test-key"):
            result = incident_monitor._run_incident_agent("Church Avenue", ["Church Avenue"], {"incidents": [], "status": "fresh"})
        self.assertIn("xAI tool round limit reached", result["scan_metadata"]["sources"]["errors"])
        self.assertLessEqual(result["scan_metadata"]["tool_rounds"], incident_monitor._MAX_TOOL_ROUNDS)
        self.assertEqual(result["scan_metadata"]["status"], "partial")

    def test_total_tool_budget_counts_server_and_client_calls(self):
        calls = [_call("x_search", "{}", f"x{index}", "x_search_tool") for index in range(incident_monitor._MAX_TOTAL_TOOL_CALLS + 1)]
        fake_client = _FakeClient([_FakeResponse("", "tool_calls", calls)])
        with patch.object(incident_monitor, "client", fake_client), patch.object(incident_monitor, "system", lambda value: value), patch.object(incident_monitor, "user", lambda value: value), patch.object(incident_monitor, "x_search", lambda: "x"), patch.object(incident_monitor, "web_search", lambda: "web"), patch.object(incident_monitor, "tool", lambda *args: "local"), patch.object(incident_monitor, "get_tool_call_type", lambda value: value.call_type), patch.object(incident_monitor, "_XAI_API_KEY", "test-key"):
            result = incident_monitor._run_incident_agent("Church Avenue", ["Church Avenue"], {"incidents": [], "status": "fresh"})
        self.assertLessEqual(result["scan_metadata"]["total_tool_calls"], incident_monitor._MAX_TOTAL_TOOL_CALLS)
        self.assertIn("tool call batch exceeds total limit", result["scan_metadata"]["sources"]["errors"])

    def test_round_cap_without_completed_sources_is_failed(self):
        unknown = [_call("unknown", "{}", f"u{index}", "mcp_tool") for index in range(incident_monitor._MAX_TOOL_ROUNDS)]
        fake_client = _FakeClient([_FakeResponse("", "tool_calls", [call]) for call in unknown])
        with patch.object(incident_monitor, "client", fake_client), patch.object(incident_monitor, "system", lambda value: value), patch.object(incident_monitor, "user", lambda value: value), patch.object(incident_monitor, "x_search", lambda: "x"), patch.object(incident_monitor, "web_search", lambda: "web"), patch.object(incident_monitor, "tool", lambda *args: "local"), patch.object(incident_monitor, "get_tool_call_type", lambda value: value.call_type), patch.object(incident_monitor, "_XAI_API_KEY", "test-key"):
            result = incident_monitor._run_incident_agent("Church Avenue", ["Church Avenue"], {"incidents": [], "status": "fresh"})
        self.assertEqual(result["scan_metadata"]["status"], "failed")
        self.assertIn("xAI tool round limit reached", result["scan_metadata"]["sources"]["errors"])

    def test_total_budget_without_completed_sources_is_failed(self):
        over_budget = [_call("unknown", "{}", f"b{index}", "mcp_tool") for index in range(incident_monitor._MAX_TOTAL_TOOL_CALLS + 1)]
        fake_client = _FakeClient([_FakeResponse("", "tool_calls", over_budget)])
        with patch.object(incident_monitor, "client", fake_client), patch.object(incident_monitor, "system", lambda value: value), patch.object(incident_monitor, "user", lambda value: value), patch.object(incident_monitor, "x_search", lambda: "x"), patch.object(incident_monitor, "web_search", lambda: "web"), patch.object(incident_monitor, "tool", lambda *args: "local"), patch.object(incident_monitor, "get_tool_call_type", lambda value: value.call_type), patch.object(incident_monitor, "_XAI_API_KEY", "test-key"):
            result = incident_monitor._run_incident_agent("Church Avenue", ["Church Avenue"], {"incidents": [], "status": "fresh"})
        self.assertEqual(result["scan_metadata"]["status"], "failed")
        self.assertIn("tool call batch exceeds total limit", result["scan_metadata"]["sources"]["errors"])

    def test_sampling_exception_is_failed_without_exception_text(self):
        fake_client = _FakeClient([])
        with patch.object(incident_monitor, "client", fake_client), patch.object(incident_monitor, "system", lambda value: value), patch.object(incident_monitor, "user", lambda value: value), patch.object(incident_monitor, "x_search", lambda: "x"), patch.object(incident_monitor, "web_search", lambda: "web"), patch.object(incident_monitor, "tool", lambda *args: "local"), patch.object(incident_monitor, "_XAI_API_KEY", "test-key"):
            result = incident_monitor._run_incident_agent("Church Avenue", ["Church Avenue"], {"incidents": [], "status": "fresh"})
        self.assertEqual(result["scan_metadata"]["status"], "failed")
        self.assertEqual(result["scan_metadata"]["sources"]["errors"], ["xAI sampling failed"])

    def test_missing_or_duplicate_tool_ids_never_receive_a_tool_result(self):
        missing = _call("search_cached_511ny_incidents", '{"candidate_route_ids":["candidate-0"]}', "", "client_side_tool")
        duplicate_one = _call("search_cached_511ny_incidents", '{"candidate_route_ids":["candidate-0"]}', "same", "client_side_tool")
        duplicate_two = _call("search_cached_511ny_incidents", '{"candidate_route_ids":["candidate-1"]}', "same", "client_side_tool")
        fake_client = _FakeClient([_FakeResponse("", "tool_calls", [missing]), _FakeResponse("", "tool_calls", [duplicate_one]), _FakeResponse("", "tool_calls", [duplicate_two]), _FakeResponse('{"incidents":[]}', "stop")])
        results = []
        with patch.object(incident_monitor, "client", fake_client), patch.object(incident_monitor, "system", lambda value: value), patch.object(incident_monitor, "user", lambda value: value), patch.object(incident_monitor, "x_search", lambda: "x"), patch.object(incident_monitor, "web_search", lambda: "web"), patch.object(incident_monitor, "tool", lambda *args: "local"), patch.object(incident_monitor, "get_tool_call_type", lambda value: value.call_type), patch.object(incident_monitor, "tool_result", lambda _value, tool_call_id: results.append(tool_call_id) or "tool-result"), patch.object(incident_monitor, "_XAI_API_KEY", "test-key"):
            result = incident_monitor._run_incident_agent("Church Avenue", ["Church Avenue"], {"incidents": [], "status": "fresh"})
        self.assertEqual(results, ["same"])
        self.assertIn("missing tool call id", result["scan_metadata"]["sources"]["errors"])
        self.assertIn("duplicate tool call id", result["scan_metadata"]["sources"]["errors"])

    def test_local_call_cap_stops_safely(self):
        local_calls = [
            _call("search_cached_511ny_incidents", '{"candidate_route_ids":["candidate-%s"]}' % index, f"l{index}", "client_side_tool")
            for index in range(incident_monitor._MAX_LOCAL_TOOL_CALLS + 1)
        ]
        fake_client = _FakeClient([_FakeResponse("", "tool_calls", [call]) for call in local_calls])
        with patch.object(incident_monitor, "client", fake_client), patch.object(incident_monitor, "system", lambda value: value), patch.object(incident_monitor, "user", lambda value: value), patch.object(incident_monitor, "x_search", lambda: "x"), patch.object(incident_monitor, "web_search", lambda: "web"), patch.object(incident_monitor, "tool", lambda *args: "local"), patch.object(incident_monitor, "get_tool_call_type", lambda value: value.call_type), patch.object(incident_monitor, "tool_result", lambda *args, **kwargs: "tool-result"), patch.object(incident_monitor, "_XAI_API_KEY", "test-key"):
            result = incident_monitor._run_incident_agent("Church Avenue", ["Church Avenue"], {"incidents": [], "status": "fresh"})
        self.assertIn("local tool call limit reached", result["scan_metadata"]["sources"]["errors"])
        self.assertIn("xAI tool round limit reached", result["scan_metadata"]["sources"]["errors"])

    def test_unknown_server_tool_type_is_not_executed_locally(self):
        unknown = _call("unknown", "{}", "u1", "mcp_tool")
        fake_client = _FakeClient([_FakeResponse("", "tool_calls", [unknown]), _FakeResponse('{"incidents":[]}', "stop")])
        with patch.object(incident_monitor, "client", fake_client), patch.object(incident_monitor, "system", lambda value: value), patch.object(incident_monitor, "user", lambda value: value), patch.object(incident_monitor, "x_search", lambda: "x"), patch.object(incident_monitor, "web_search", lambda: "web"), patch.object(incident_monitor, "tool", lambda *args: "local"), patch.object(incident_monitor, "get_tool_call_type", lambda value: value.call_type), patch.object(incident_monitor, "_XAI_API_KEY", "test-key"):
            result = incident_monitor._run_incident_agent("Church Avenue", ["Church Avenue"], {"incidents": [], "status": "fresh"})
        self.assertEqual(result["scan_metadata"]["local_tool_calls"], 0)
        self.assertIn("unsupported tool call type", result["scan_metadata"]["sources"]["errors"])

    def test_local_tool_results_use_call_id_and_duplicate_calls_are_rejected(self):
        call = _FakeToolCall(
            "search_cached_511ny_incidents",
            '{"candidate_route_ids":["candidate-0"],"radius_miles":0.5}',
            "call-1",
        )
        duplicate = _FakeToolCall(call.function.name, call.function.arguments, "call-2")
        fake_client = _FakeClient([
            _FakeResponse("", "tool_calls", [call]),
            _FakeResponse("", "tool_calls", [duplicate]),
            _FakeResponse('{"incidents":[]}', "stop"),
        ])
        results = []
        with patch.object(incident_monitor, "client", fake_client), patch.object(incident_monitor, "system", lambda value: value), patch.object(incident_monitor, "user", lambda value: value), patch.object(incident_monitor, "x_search", lambda: "x"), patch.object(incident_monitor, "web_search", lambda: "web"), patch.object(incident_monitor, "tool", lambda *args: "local"), patch.object(incident_monitor, "get_tool_call_type", lambda value: "client_side_tool"), patch.object(incident_monitor, "tool_result", lambda value, tool_call_id: results.append((value, tool_call_id)) or "tool-result"), patch.object(incident_monitor, "_XAI_API_KEY", "test-key"):
            result = incident_monitor._run_incident_agent(
                "Church Avenue", ["Church Avenue"], {"incidents": [], "status": "fresh"}
            )
        self.assertEqual([call_id for _value, call_id in results], ["call-1", "call-2"])
        self.assertEqual(result["scan_metadata"]["local_tool_calls"], 2)
        self.assertIn("duplicate local tool call", result["scan_metadata"]["sources"]["errors"])
        self.assertEqual(result["scan_metadata"]["status"], "partial")

    def test_malformed_local_arguments_are_rejected_without_crashing(self):
        malformed = _FakeToolCall("search_cached_511ny_incidents", "not-json", "call-1")
        fake_client = _FakeClient([_FakeResponse("", "tool_calls", [malformed]), _FakeResponse('{"incidents":[]}', "stop")])
        with patch.object(incident_monitor, "client", fake_client), patch.object(incident_monitor, "system", lambda value: value), patch.object(incident_monitor, "user", lambda value: value), patch.object(incident_monitor, "x_search", lambda: "x"), patch.object(incident_monitor, "web_search", lambda: "web"), patch.object(incident_monitor, "tool", lambda *args: "local"), patch.object(incident_monitor, "get_tool_call_type", lambda value: "client_side_tool"), patch.object(incident_monitor, "tool_result", lambda *args, **kwargs: "tool-result"), patch.object(incident_monitor, "_XAI_API_KEY", "test-key"):
            result = incident_monitor._run_incident_agent("Church Avenue", ["Church Avenue"], {"incidents": [], "status": "fresh"})
        self.assertIn("invalid JSON arguments", result["scan_metadata"]["sources"]["errors"])

    def test_run_incident_agent_handles_tool_call_then_normalizes_response(self):
        responses = [
            _FakeResponse(
                """{
                  "incidents": [
                    {
                      "location": "  Flatbush Ave / Caton Ave ",
                      "nearby_station": " Church Avenue ",
                      "severity": "MEDIUM",
                      "description": "  Smoke investigation near the station entrance. ",
                      "source": " @CitizenAppNYC ",
                      "extra": "ignore"
                    }
                  ]
                }""",
                "stop",
            ),
        ]
        fake_client = _FakeClient(responses)

        with patch.object(incident_monitor, "client", fake_client), patch.object(
            incident_monitor, "system", lambda text: ("system", text)
        ), patch.object(incident_monitor, "user", lambda text: ("user", text)), patch.object(
            incident_monitor, "x_search", lambda: "x_search_tool"
        ), patch.object(
            incident_monitor, "_XAI_API_KEY", "test-key"
        ):
            result = incident_monitor._run_incident_agent(
                "Church Avenue, Prospect Park",
                ["Church Avenue", "Prospect Park"],
            )

        self.assertEqual(
            result["incidents"],
            [
                    {
                        "location": "Flatbush Ave / Caton Ave",
                        "nearby_station": "Church Avenue",
                        "severity": "medium",
                        "description": "Smoke investigation near the station entrance.",
                        "source": "@CitizenAppNYC",
                    }
            ],
        )
        self.assertEqual(fake_client.chat.kwargs["model"], "grok-4-1-fast-reasoning")
        self.assertEqual(fake_client.chat.kwargs["tools"][0], "x_search_tool")
        self.assertEqual(fake_client.chat.kwargs["temperature"], 0.0)
        self.assertEqual(fake_client.chat.session.sample_calls, 1)
        self.assertEqual(len(fake_client.chat.session.appended), 2)
        self.assertEqual(fake_client.chat.session.appended[0][0], "system")
        self.assertEqual(fake_client.chat.session.appended[1][0], "user")
        self.assertEqual(result["scan_metadata"]["status"], "partial")

    def test_run_incident_agent_returns_empty_for_malformed_payload(self):
        fake_client = _FakeClient([_FakeResponse("definitely not json", "stop")])

        with patch.object(incident_monitor, "client", fake_client), patch.object(
            incident_monitor, "system", lambda text: text
        ), patch.object(incident_monitor, "user", lambda text: text), patch.object(
            incident_monitor, "x_search", lambda: "x_search_tool"
        ), patch.object(
            incident_monitor, "_XAI_API_KEY", "test-key"
        ):
            result = incident_monitor._run_incident_agent("Church Avenue", ["Church Avenue"])

        self.assertEqual(result["incidents"], [])
        self.assertEqual(result["scan_metadata"]["status"], "failed")

    def test_run_incident_agent_returns_empty_when_nearby_station_is_not_on_route(self):
        fake_client = _FakeClient(
            [
                _FakeResponse(
                    """{
                      "incidents": [
                        {
                          "location": "Jay Street",
                          "nearby_station": "Utica Avenue",
                          "severity": "low",
                          "description": "Unrelated disruption away from the route.",
                          "source": "@NYScanner"
                        }
                      ]
                    }""",
                    "stop",
                )
            ]
        )

        with patch.object(incident_monitor, "client", fake_client), patch.object(
            incident_monitor, "system", lambda text: text
        ), patch.object(incident_monitor, "user", lambda text: text), patch.object(
            incident_monitor, "x_search", lambda: "x_search_tool"
        ), patch.object(
            incident_monitor, "_XAI_API_KEY", "test-key"
        ):
            result = incident_monitor._run_incident_agent("Church Avenue", ["Church Avenue"])

        self.assertEqual(result["incidents"], [])


class IncidentMonitorAsyncTests(unittest.IsolatedAsyncioTestCase):
    async def test_get_incidents_reads_the_cached_snapshot_once_before_thread(self):
        store = SimpleNamespace(get_snapshot=AsyncMock(return_value={"incidents": [], "status": "fresh"}))
        to_thread = AsyncMock(return_value={"incidents": [], "scan_metadata": {"status": "complete"}})
        with patch.object(incident_monitor.asyncio, "to_thread", to_thread):
            await incident_monitor.get_incidents(["Church Avenue"], snapshot_store=store)
        store.get_snapshot.assert_awaited_once()
        self.assertEqual(to_thread.await_args.args[3], {"incidents": [], "status": "fresh"})
    async def test_get_incidents_normalizes_station_names_before_dispatch(self):
        to_thread = AsyncMock(return_value={"incidents": []})

        with patch.object(incident_monitor.asyncio, "to_thread", to_thread):
            result = await incident_monitor.get_incidents(
                ["  Church Avenue ", "church avenue", "", "Prospect   Park"]
            )

        self.assertEqual(result["incidents"], [])
        to_thread.assert_awaited_once()
        self.assertEqual(
            to_thread.await_args.args[:3],
            (
                incident_monitor._run_incident_agent,
                "Church Avenue, Prospect Park",
                ["Church Avenue", "Prospect Park"],
            ),
        )

    async def test_get_incidents_returns_empty_when_xai_client_missing(self):
        with patch.object(incident_monitor, "client", None), patch.dict(
            os.environ, {"XAI_API_KEY": "test-key"}, clear=False
        ):
            result = await incident_monitor.get_incidents(["Church Avenue"])

        self.assertEqual(result["incidents"], [])
        self.assertEqual(result["scan_metadata"]["status"], "disabled")

    async def test_get_incidents_returns_empty_when_agent_raises(self):
        with patch.object(
            incident_monitor, "_run_incident_agent", side_effect=RuntimeError("boom")
        ):
            result = await incident_monitor.get_incidents(["Church Avenue"])

        self.assertEqual(result["incidents"], [])
        self.assertEqual(result["scan_metadata"]["status"], "failed")
