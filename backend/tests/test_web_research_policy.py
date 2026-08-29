"""Phase 2B web research policy (replaces test_browser_tools.py).

Proves the single native server-side web_search path: state-valid tool exposure,
the direct-only server tool shape, the removed custom browser-tool surface,
structured discovery ordering, prompt guardrails, pause_turn continuation,
hardened web-result handling, and provider-content non-leakage in mocked
flows.
"""

from __future__ import annotations

import io
import os
import types
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

from app.services import cache
from app.services.agent import events as agent_events
from app.services.agent import loop as agent_loop
from app.services.agent import public_surface, tool_input_policy
from app.services.agent import session as session_module
from app.services.agent.model import policy
from app.services.agent.model import stream as model_stream
from app.services.agent.tools._types import ToolContext
from app.services.agent.turn.contract import (
    GoalKind,
    GoalState,
    OutcomeGoal,
    TurnContract,
)
from app.services.agent.turn.evidence import TurnEvidence

from tests._fake_anthropic import reload_agent_loop_module


def _state_tools(mode: str) -> list[dict]:
    return agent_loop._tools_for_state(policy.policy_for_mode(mode))


class WebSearchPolicyTests(unittest.TestCase):
    def test_web_search_is_native_direct_only_nyc_and_one_use(self):
        for mode in ("auto", "quick"):
            with self.subTest(mode=mode):
                evidence = TurnEvidence()
                assert not evidence.may_offer_web()
                evidence.note_discover_places(
                    ok=True,
                    discovery_set_id="ds_web_policy",
                    place_count=1,
                    operation="search",
                )
                tools = agent_loop._tools_for_state(
                    policy.policy_for_mode(mode),
                    include_web=evidence.may_offer_web(),
                    turn_evidence=evidence,
                )
                web_tool = next(
                    tool for tool in tools if tool.get("type") == "web_search_20250305"
                )
                assert web_tool["name"] == "web_search"
                assert web_tool["max_uses"] == 1
                assert web_tool["allowed_callers"] == ["direct"]
                assert web_tool["user_location"] == {"type": "approximate", "city": "New York City", "region": "New York", "country": "US", "timezone": "America/New_York"}

    def test_structured_discovery_precedes_web_search(self):
        for mode in ("auto", "quick"):
            with self.subTest(mode=mode):
                initial_evidence = TurnEvidence()
                initial_tools = _state_tools(mode)
                assert [tool.get("name") for tool in initial_tools] == ["declare_goals", "discover_places", "check_transit", "prepare_route_options", "complete_turn"]
                assert not initial_evidence.may_offer_web()

                search_evidence = TurnEvidence()
                search_evidence.note_discover_places(
                    ok=True,
                    discovery_set_id="ds_web_policy",
                    place_count=5,
                    operation="search",
                )
                tools = agent_loop._tools_for_state(
                    policy.policy_for_mode(mode),
                    include_web=search_evidence.may_offer_web(),
                    turn_evidence=search_evidence,
                )
                names = [tool.get("name") for tool in tools]
                assert names[-1] == "web_search"
                assert names[:5] == ["declare_goals", "discover_places", "check_transit", "prepare_route_options", "complete_turn"]

                verify_evidence = TurnEvidence()
                verify_evidence.note_discover_places(
                    ok=True,
                    discovery_set_id="ds_web_policy",
                    place_count=5,
                    operation="verify",
                )
                verify_tools = agent_loop._tools_for_state(
                    policy.policy_for_mode(mode),
                    include_web=verify_evidence.may_offer_web(),
                    turn_evidence=verify_evidence,
                )
                assert verify_tools[-1].get("name") == "web_search"

    def test_verified_place_details_require_web_before_presenter(self):
        evidence = TurnEvidence()
        evidence.bind_contract(
            TurnContract((OutcomeGoal("place_details", GoalKind.PLACE_RECOMMENDATION),))
        )
        evidence.note_discover_places(
            ok=True,
            discovery_set_id="ds_verified_place",
            place_count=1,
            operation="verify",
        )
        evidence.record_goal_handle("place_details", "ds_verified_place")
        evidence.record_goal("place_details", GoalState.EVIDENCE_READY, attempted=True)
        assert "present_places" not in public_surface.state_valid_tool_names(evidence)
        ctx = ToolContext(
            session={},
            session_id="sess-web-policy",
            turn_id="turn-web-policy",
            turn_evidence=evidence,
        )
        error = tool_input_policy.goal_error(
            "present_places",
            {
                "goal_key": "place_details",
                "discovery_set_id": "ds_verified_place",
                "research_used": False,
            },
            ctx,
        )
        assert "requires successful current-turn web research" in error

        evidence.note_web(ok=True)
        assert evidence.web_research_required
        assert "present_places" in public_surface.state_valid_tool_names(evidence)
        assert tool_input_policy.goal_error("present_places", {"goal_key": "place_details", "discovery_set_id": "ds_verified_place", "research_used": False}, ctx) == "present_places must present the successful current-turn research"
        assert tool_input_policy.goal_error("present_places", {"goal_key": "place_details", "discovery_set_id": "ds_verified_place", "research_used": True}, ctx) is None

    def test_failed_required_web_does_not_unlock_place_presenter(self):
        evidence = TurnEvidence()
        evidence.bind_contract(
            TurnContract((OutcomeGoal("place_details", GoalKind.PLACE_RECOMMENDATION),))
        )
        evidence.note_discover_places(
            ok=True,
            discovery_set_id="ds_verified_place",
            place_count=1,
            operation="verify",
        )
        evidence.record_goal_handle("place_details", "ds_verified_place")
        evidence.record_goal("place_details", GoalState.EVIDENCE_READY, attempted=True)

        evidence.note_web(ok=False)

        assert evidence.web_research_required
        assert "present_places" not in public_surface.state_valid_tool_names(evidence)

    def test_empty_structured_search_unlocks_one_web_recovery_pass(self):
        evidence = TurnEvidence()
        evidence.note_discover_places(
            ok=True,
            discovery_set_id=None,
            place_count=0,
            operation="search",
        )

        assert evidence.may_offer_web()
        tools = agent_loop._tools_for_state(
            policy.policy_for_mode("auto"),
            include_web=evidence.may_offer_web(),
            turn_evidence=evidence,
        )
        assert tools[-1].get("name") == "web_search"

    def test_compound_route_uses_web_only_when_structured_search_is_empty(self):
        route_contract = TurnContract((OutcomeGoal("route", GoalKind.ROUTE),))
        empty = TurnEvidence()
        empty.bind_contract(route_contract)
        empty.note_discover_places(
            ok=True,
            discovery_set_id=None,
            place_count=0,
            operation="search",
        )
        assert empty.may_offer_web()

        verified = TurnEvidence()
        verified.bind_contract(route_contract)
        verified.note_discover_places(
            ok=True,
            discovery_set_id="ds_route_ready",
            place_count=5,
            operation="search",
        )
        assert not verified.may_offer_web()

    def test_web_introduced_place_still_requires_structured_verification(self):
        evidence = TurnEvidence()
        evidence.note_discover_places(
            ok=True,
            discovery_set_id=None,
            place_count=0,
            operation="search",
        )
        evidence.note_web(ok=True)
        assert not evidence.may_offer_web()
        assert evidence.discovery_set_id is None
        assert evidence.verified_place_count == 0


class WebResultOkUnitTests(unittest.TestCase):
    def test_sdk_error_object_fails_list_and_scalar(self):
        error = types.SimpleNamespace(
            type="web_search_tool_result_error", error_code="unavailable"
        )
        assert not model_stream._web_result_ok([error])
        assert not model_stream._web_result_ok(error)


class _WebResultStream:
    def __init__(self, web_result_content: list[object]):
        self._web_result_content = web_result_content

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False

    async def __aiter__(self):
        yield types.SimpleNamespace(
            type="content_block_start",
            index=0,
            content_block=types.SimpleNamespace(
                type="server_tool_use", id="web-1", name="web_search"
            ),
        )
        yield types.SimpleNamespace(
            type="content_block_start",
            index=1,
            content_block=types.SimpleNamespace(
                type="web_search_tool_result",
                tool_use_id="web-1",
                content=self._web_result_content,
            ),
        )

    async def get_final_message(self):
        return types.SimpleNamespace(
            content=[],
            stop_reason="end_turn",
            usage=types.SimpleNamespace(input_tokens=1, output_tokens=1),
        )


class _WebResultMessages:
    def __init__(self, stream):
        self._stream = stream

    def stream(self, **_kwargs):
        return self._stream


async def _collect_web_result_items(web_result_content: list[object]):
    client = types.SimpleNamespace(
        messages=_WebResultMessages(_WebResultStream(web_result_content))
    )
    return [
        item
        async for item in model_stream.stream_model_call(
            client=client,
            stream_kwargs={},
            log_tag="test",
            retry_count=0,
            sanitize_text=lambda value: value,
        )
    ]


class WebResultStreamTests(unittest.IsolatedAsyncioTestCase):
    async def test_dict_error_emits_failed_tool_end_without_provider_content(self):
        items = await _collect_web_result_items(
            [
                {
                    "type": "web_search_tool_result_error",
                    "error_code": "unavailable",
                    "message": "provider secrets must never surface",
                }
            ]
        )
        progress = next(item for item in items if isinstance(item, agent_events.ToolEndEvent))
        assert not progress.ok
        assert progress.summary == "Current place search was unavailable"
        rendered = "".join(
            item.text for item in items if isinstance(item, agent_events.TokenEvent)
        )
        assert "secrets" not in rendered
        assert "error_code" not in rendered

    async def test_empty_results_succeed(self):
        items = await _collect_web_result_items([])
        progress = next(item for item in items if isinstance(item, agent_events.ToolEndEvent))
        assert progress.ok
        assert progress.summary == "Current place information checked"
        outcome = next(
            item for item in items if isinstance(item, model_stream.ModelCallCompleted)
        )
        assert outcome.error is None

    async def test_missing_content_fails_safely(self):
        items = await _collect_web_result_items(None)
        progress = next(item for item in items if isinstance(item, agent_events.ToolEndEvent))
        assert not progress.ok
        assert progress.summary == "Current place search was unavailable"

    async def test_encrypted_and_page_content_never_reach_sse_events(self):
        items = await _collect_web_result_items(
            [
                {"type": "encrypted_content", "encrypted_index": "0", "encrypted_data": "ENCRYPTEDSECRET"},
                {"type": "text", "text": "raw page text with instructions"},
            ]
        )
        progress = next(item for item in items if isinstance(item, agent_events.ToolEndEvent))
        assert progress.ok
        rendered = "".join(
            item.text for item in items if isinstance(item, agent_events.TokenEvent)
        )
        assert "ENCRYPTEDSECRET" not in rendered
        assert "raw page text" not in rendered
        assert "instructions" not in progress.summary


class _ScriptedMessages:
    """Minimal scripted messages API for pause/server-tool shapes."""

    def __init__(self, rounds: list[dict]):
        self._rounds = list(rounds)
        self.calls: list[dict] = []

    def stream(self, **kwargs):
        # Snapshot `messages` at call time; the turn loop keeps mutating the
        # same list object across rounds (see tests/_fake_anthropic.py).
        recorded = dict(kwargs)
        if "messages" in recorded:
            recorded["messages"] = list(recorded["messages"])
        self.calls.append(recorded)
        return _ScriptedStream(self._rounds)


class _ScriptedStream:
    def __init__(self, rounds: list[dict]):
        self._rounds = rounds

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False

    async def __aiter__(self):
        for chunk in self._rounds[0].get("text", []):
            yield types.SimpleNamespace(
                type="content_block_delta",
                delta=types.SimpleNamespace(type="text_delta", text=chunk),
            )

    async def get_final_message(self):
        spec = self._rounds.pop(0)
        blocks = list(spec.get("content_blocks", []))
        joined = "".join(spec.get("text", []))
        if joined:
            blocks.insert(0, types.SimpleNamespace(type="text", text=joined))
        return types.SimpleNamespace(
            content=blocks,
            stop_reason=spec.get("stop_reason", "end_turn"),
            usage=types.SimpleNamespace(input_tokens=1, output_tokens=1),
        )


class PauseTurnTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        cls.loop = reload_agent_loop_module(
            env={"AGENT_AUTO_MAX_ROUNDS": "4", "AGENT_TURN_DEADLINE_S": "60"}
        )

    def setUp(self):
        cache._mem.clear()

    async def _run_turn(self, rounds, *, message="Thanks for checking"):
        self.loop.client.messages = _ScriptedMessages(list(rounds))
        _discard_id, session = session_module.new_session()
        events_out = [
            event
            async for event in self.loop.run_agent_turn(
                session=session,
                session_id="sess-pause",
                turn_id="t-pause",
                message=message,
                now_et="2026-08-08T12:00:00-04:00",
                gtfs=None,
                origin=None,
                selected_card_id=None,
                response_presentation="auto",
                trace=None,
            )
        ]
        return events_out, session

    async def test_pause_turn_appends_unchanged_content_and_continues(self):
        events_out, session = await self._run_turn(
            [
                {"text": ["Checking current sources..."], "stop_reason": "pause_turn"},
                {
                    "text": [],
                    "stop_reason": "tool_use",
                    "content_blocks": [
                        types.SimpleNamespace(
                            type="tool_use",
                            id="tu-goals",
                            name="declare_goals",
                            input={
                                "goals": [
                                    {
                                        "goal_key": "answer",
                                        "kind": "general_response",
                                        "depends_on": [],
                                    }
                                ],
                            },
                        )
                    ],
                },
                {
                    "text": [],
                    "stop_reason": "tool_use",
                    "content_blocks": [
                        types.SimpleNamespace(
                            type="tool_use",
                            id="tu-done",
                            name="complete_turn",
                            input={
                                "goal_keys": ["answer"],
                                "outcome": "answer",
                                "message": "Found a grounded option.",
                            },
                        )
                    ],
                },
            ]
        )
        calls = self.loop.client.messages.calls
        assert len(calls) == 3
        assert events_out[-1].stop_reason == "end_turn"
        assistant_messages = [
            message
            for message in calls[1]["messages"]
            if message.get("role") == "assistant"
        ]
        assert len(assistant_messages) == 1
        blocks = assistant_messages[0]["content"]
        assert [getattr(block, "text", "") for block in blocks] == ["Checking current sources..."]
        history_text = session["history"][-1]["text"]
        assert "Found a grounded option." in history_text

    async def test_repeated_pauses_remain_bounded_by_max_rounds(self):
        with patch.dict(os.environ, {"AGENT_AUTO_MAX_ROUNDS": "2"}, clear=False):
            events_out, _session = await self._run_turn(
                [
                    {"text": ["Pause one."], "stop_reason": "pause_turn"},
                    {"text": ["Pause two."], "stop_reason": "pause_turn"},
                    {"text": ["Wrapped up."], "stop_reason": "end_turn"},
                ]
            )
        calls = self.loop.client.messages.calls
        # Wrap-up model calls were removed; max_rounds stops after the budget.
        assert len(calls) == 2
        assert events_out[-1].stop_reason == "max_rounds"

    async def test_encrypted_and_server_tool_content_never_leak_to_sse_history_or_logs(self):
        server_block = types.SimpleNamespace(
            type="server_tool_use",
            id="web-1",
            name="web_search",
            input={"query": "secret web query"},
        )
        encrypted_block = types.SimpleNamespace(
            type="encrypted_content", encrypted_index="0", encrypted_data="ENCRYPTEDSECRET"
        )
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            events_out, session = await self._run_turn(
                [
                    {
                        "text": [],
                        "stop_reason": "pause_turn",
                        "content_blocks": [server_block, encrypted_block],
                    },
                    {
                        "text": [],
                        "stop_reason": "tool_use",
                        "content_blocks": [
                            types.SimpleNamespace(
                                type="tool_use",
                                id="tu-done",
                                name="complete_turn",
                                input={
                                    "outcome": "answer",
                                    "message": "Grounded pick found.",
                                },
                            )
                        ],
                    },
                ]
            )
        calls = self.loop.client.messages.calls
        assistant_messages = [
            message
            for message in calls[1]["messages"]
            if message.get("role") == "assistant"
        ]
        assert len(assistant_messages) == 1
        # In-memory continuation keeps the server blocks unchanged...
        assert assistant_messages[0]["content"] == [server_block, encrypted_block]
        # ...but nothing leaks to SSE text, rider history, or logs.
        token_text = "".join(
            event.text for event in events_out if event.type == "token"
        )
        history_text = " ".join(
            entry.get("text", "") for entry in session.get("history") or []
        )
        log_output = buffer.getvalue()
        for secret in ("ENCRYPTEDSECRET", "secret web query", "web-1"):
            assert secret not in token_text
            assert secret not in history_text
            assert secret not in log_output


if __name__ == "__main__":
    unittest.main()
