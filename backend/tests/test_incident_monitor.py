import os
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.services import incident_monitor


class _FakeResponse:
    def __init__(self, content, finish_reason):
        self.content = content
        self.finish_reason = finish_reason


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
    def test_run_incident_agent_handles_tool_call_then_normalizes_response(self):
        tool_response = _FakeResponse("", "tool_calls")
        responses = [
            tool_response,
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
            result,
            {
                "incidents": [
                    {
                        "location": "Flatbush Ave / Caton Ave",
                        "nearby_station": "Church Avenue",
                        "severity": "medium",
                        "description": "Smoke investigation near the station entrance.",
                        "source": "@CitizenAppNYC",
                    }
                ]
            },
        )
        self.assertEqual(fake_client.chat.kwargs["model"], "grok-4-1-fast-reasoning")
        self.assertEqual(fake_client.chat.kwargs["tools"], ["x_search_tool"])
        self.assertEqual(fake_client.chat.kwargs["temperature"], 0.0)
        self.assertNotIn("max_turns", fake_client.chat.kwargs)
        self.assertEqual(fake_client.chat.session.sample_calls, 2)
        self.assertEqual(len(fake_client.chat.session.appended), 3)
        self.assertEqual(fake_client.chat.session.appended[0][0], "system")
        self.assertEqual(fake_client.chat.session.appended[1][0], "user")
        self.assertIs(fake_client.chat.session.appended[2], tool_response)

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

        self.assertEqual(result, {"incidents": []})

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

        self.assertEqual(result, {"incidents": []})


class IncidentMonitorAsyncTests(unittest.IsolatedAsyncioTestCase):
    async def test_get_incidents_normalizes_station_names_before_dispatch(self):
        to_thread = AsyncMock(return_value={"incidents": []})

        with patch.object(incident_monitor.asyncio, "to_thread", to_thread):
            result = await incident_monitor.get_incidents(
                ["  Church Avenue ", "church avenue", "", "Prospect   Park"]
            )

        self.assertEqual(result, {"incidents": []})
        to_thread.assert_awaited_once()
        self.assertEqual(
            to_thread.await_args.args,
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

        self.assertEqual(result, {"incidents": []})

    async def test_get_incidents_returns_empty_when_agent_raises(self):
        with patch.object(
            incident_monitor, "_run_incident_agent", side_effect=RuntimeError("boom")
        ):
            result = await incident_monitor.get_incidents(["Church Avenue"])

        self.assertEqual(result, {"incidents": []})
