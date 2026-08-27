from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from app.services import cache
from app.services.agent import discovery_store
from app.services.agent import events as agent_events
from app.services.agent import session as session_module
from app.services.agent import trip_state as trip_state_module
from app.services.agent.tools import ToolResult, ToolSpec, declare_goals

from tests.test_agent_loop import (
    _AgentLoopHelpers,
    _fake_prepare_route_options_tool,
    _load_agent_loop,
    _test_registry,
)


def _tool_round(name: str, call_id: str, tool_input: dict) -> dict:
    return {
        "tool_use": [{"id": call_id, "name": name, "input": tool_input}],
        "stop_reason": "tool_use",
    }


def _declared_round(
    goal_key: str,
    kind: str,
    *tool_calls: dict,
    extra_goals: tuple[dict, ...] = (),
) -> dict:
    """Script one model-led round with its contract before any capability."""

    return {
        "tool_use": [
            {
                "id": f"goals-{goal_key}",
                "name": "declare_goals",
                "input": {
                    "goals": [
                        {
                            "goal_key": goal_key,
                            "kind": kind,
                            "depends_on": [],
                        },
                        *extra_goals,
                    ]
                },
            },
            *tool_calls,
        ],
        "stop_reason": "tool_use",
    }


def _rider_text(events: list) -> str:
    return "".join(event.text for event in events if event.type == "token")


def _action_registry() -> dict[str, ToolSpec]:
    """Offer the model-led contract alongside the shared fake capabilities."""

    return {
        **_test_registry(),
        "declare_goals": ToolSpec(
            schema=declare_goals.DECLARE_GOALS_SCHEMA,
            executor=declare_goals.execute,
            label_fn=lambda _input: "Understanding the request…",
            timeout_s=2.0,
        ),
    }


async def _fake_check_status_tool(_tool_input: dict, ctx) -> ToolResult:
    evidence = getattr(ctx, "turn_evidence", None)
    if evidence is not None:
        evidence.note_check_transit(ok=True, operation="service_status")
    return ToolResult(
        ok=True,
        data={
            "operation": "service_status",
            "evidence_set_id": "te_action_completion",
            "checked_routes": ["Q"],
        },
    )


async def _fake_present_transit_tool(_tool_input: dict, _ctx) -> ToolResult:
    return ToolResult(
        ok=True,
        data={"presentation_outcome": "presented"},
        events=[agent_events.TokenEvent(text="The Q currently has southbound delays.")],
    )


class AgentActionCompletionTests(_AgentLoopHelpers, unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.loop = _load_agent_loop()

    def setUp(self) -> None:
        cache._mem.clear()

    async def test_general_response_stays_conversational_without_unowned_follow_up(
        self,
    ) -> None:
        events, _session = await self._run(
            [
                _declared_round(
                    "response",
                    "general_response",
                    {
                        "id": "answer-offer",
                        "name": "complete_turn",
                        "input": {
                            "goal_keys": ["response"],
                            "outcome": "answer",
                            "message": (
                                "I can help with routes, service updates, and "
                                "nearby places."
                            ),
                        },
                    },
                )
            ],
            message="What can you help me with?",
        )

        assert (
            _rider_text(events)
            == "I can help with routes, service updates, and nearby places."
        )
        assert len(self.loop.client.messages.calls) == 1

    async def test_generic_end_turn_promise_is_replaced_by_complete_response(
        self,
    ) -> None:
        events, _session = await self._run(
            [
                {"text": ["Let me check that."], "stop_reason": "end_turn"},
                _declared_round(
                    "response",
                    "general_response",
                    {
                        "id": "answer-1",
                        "name": "complete_turn",
                        "input": {
                            "goal_keys": ["response"],
                            "outcome": "answer",
                            "message": "That refers to the route we just discussed.",
                        },
                    },
                ),
            ],
            message="Can you explain that?",
        )

        text = _rider_text(events)
        assert "Let me check" not in text
        assert text == "That refers to the route we just discussed."
        assert len(self.loop.client.messages.calls) == 2

    async def test_other_options_executes_canonical_route_capabilities(self) -> None:
        _discard_id, session = session_module.new_session()
        trip_state_module.update_trip_state(
            session,
            origin="Home",
            destination="Madison Square Garden",
            active_candidate_set_id="cs_active",
            selected_candidate_id="cd_active",
        )
        trace = self.loop.TurnTrace()
        events, _session = await self._run(
            [
                {
                    "text": ["I need to compare the other routes."],
                    "stop_reason": "end_turn",
                },
                _declared_round(
                    "route",
                    "route",
                    {
                        "id": "prepare-alt",
                        "name": "prepare_route_options",
                        "input": {
                            "goal_key": "route",
                            "destination_source": "accepted_trip",
                            "what_if": True,
                        },
                    },
                ),
                _tool_round(
                    "present_route",
                    "present-alt",
                    {
                        "goal_key": "route",
                        "candidate_id": "cd_test_only",
                        "lead_in": "This option best fits the trip and preferences you gave me.",
                        "follow_up": "",
                        "reason_code": "meets_hard_constraints",
                    },
                ),
            ],
            message="what are the other options",
            session=session,
            trace=trace,
            tool_registry=_action_registry(),
        )

        assert "need to compare" not in _rider_text(events).casefold()
        assert [name for name, _input in trace.tool_calls] == [
            "declare_goals",
            "prepare_route_options",
            "present_route",
        ]
        assert [event.type for event in events].count("route_card") == 1

    async def test_named_discovery_preference_executes_search(self) -> None:
        trace = self.loop.TurnTrace()
        events, _session = await self._run(
            [
                {
                    "text": [
                        "L'industrie isn't in the current results — let me search "
                        "for it specifically near you."
                    ],
                    "stop_reason": "end_turn",
                },
                _declared_round(
                    "destination",
                    "destination_selection",
                    {
                        "id": "search-lindustrie",
                        "name": "discover_places",
                        "input": {
                            "goal_key": "destination",
                            "operation": "verify",
                            "query": "L'industrie pizza",
                            "scope": {"kind": "current_location", "values": []},
                            "open_now": None,
                            "max_results": 8,
                            "candidate_names": ["L'industrie"],
                        },
                    },
                ),
                _tool_round(
                    "present_places",
                    "present-lindustrie",
                    {
                        "goal_key": "destination",
                        "discovery_set_id": "ds_test_only",
                        "selections": [
                            {"place_id": "pl_di_fara", "reason": "preference_match"}
                        ],
                        "research_used": False,
                    },
                ),
            ],
            message="actually kind of in the mood for L'industrie",
            trace=trace,
            tool_registry=_action_registry(),
        )

        assert "let me search" not in _rider_text(events).casefold()
        assert [name for name, _input in trace.tool_calls] == [
            "declare_goals",
            "discover_places",
            "present_places",
        ]

    async def test_q_delay_executes_status_instead_of_ending_on_intent(self) -> None:
        trace = self.loop.TurnTrace()
        events, _session = await self._run(
            [
                {"text": ["I'll check the Q now."], "stop_reason": "end_turn"},
                _declared_round(
                    "q_status",
                    "service_status",
                    {
                        "id": "q-status",
                        "name": "check_transit",
                        "input": {
                            "goal_key": "q_status",
                            "operation": "service_status",
                            "route_ids": ["Q"],
                            "stop_query": None,
                            "direction": None,
                            "area": None,
                            "station": None,
                            "topic": None,
                            "event_query": None,
                            "venue": None,
                            "at": None,
                            "window_start": None,
                            "window_end": None,
                            "concerns": [],
                        },
                    },
                ),
                _tool_round(
                    "present_transit",
                    "q-present",
                    {
                        "goal_key": "q_status",
                        "evidence_set_id": "te_action_completion",
                    },
                ),
            ],
            message="are there any delays on the Q?",
            trace=trace,
            tool_registry={
                **_action_registry(),
                "check_transit": ToolSpec(
                    schema={"name": "check_transit"},
                    executor=_fake_check_status_tool,
                    label_fn=lambda _input: "Checking transit…",
                    timeout_s=5.0,
                ),
                "present_transit": ToolSpec(
                    schema={"name": "present_transit"},
                    executor=_fake_present_transit_tool,
                    label_fn=lambda _input: "Presenting transit…",
                    timeout_s=5.0,
                ),
            },
        )

        assert "I'll check" not in _rider_text(events)
        assert [name for name, _input in trace.tool_calls] == [
            "declare_goals",
            "check_transit",
            "present_transit",
        ]
        assert trace.tool_calls[1][1]["operation"] == "service_status"
        assert not any(event.type == "arrival_card" for event in events)

    async def test_ordinal_selection_requires_place_resolution(self) -> None:
        _discard_id, session = session_module.new_session()
        trip_state_module.bind_discovery_set(session, "ds_active")
        trace = self.loop.TurnTrace()
        events, _session = await self._run(
            [
                {
                    "text": ["Let me look up the second place."],
                    "stop_reason": "end_turn",
                },
                _declared_round(
                    "clarify",
                    "general_response",
                    {
                        "id": "resolve-second",
                        "name": "complete_turn",
                        "input": {
                            "goal_keys": ["clarify"],
                            "outcome": "clarification",
                            "message": "Which place list should I use for the second one?",
                        },
                    },
                ),
            ],
            message="the second one",
            session=session,
            trace=trace,
            tool_registry=_action_registry(),
        )

        assert "Let me look up" not in _rider_text(events)
        assert [name for name, _input in trace.tool_calls] == [
            "declare_goals",
            "complete_turn",
        ]

    async def test_named_place_route_after_search_executes_and_presents(self) -> None:
        session_id = "sess-action-sottocasa"
        _discard_id, session = session_module.new_session()
        set_id = discovery_store.store_discovery_set(
            session_id=session_id,
            places=[
                {
                    "name": "Sottocasa",
                    "address": "298 Atlantic Ave, Brooklyn",
                    "latitude": 40.6886,
                    "longitude": -73.9921,
                }
            ],
            query="pizza near me",
        )
        trip_state_module.bind_discovery_set(session, set_id)
        registry = _action_registry()

        async def prepare_selected(tool_input: dict, ctx) -> ToolResult:
            trip_state_module.bind_candidate_set(ctx.session, "cs_test_only")
            return await _fake_prepare_route_options_tool(tool_input, ctx)

        registry["prepare_route_options"] = ToolSpec(
            schema={"name": "prepare_route_options"},
            executor=prepare_selected,
            label_fn=lambda _input: "Preparing routes…",
            timeout_s=5.0,
        )
        trace = self.loop.TurnTrace()
        events, _session = await self._run(
            [
                {"text": ["I'll route you to Sottocasa."], "stop_reason": "end_turn"},
                _declared_round(
                    "route",
                    "route",
                    {
                        "id": "prepare-sottocasa",
                        "name": "prepare_route_options",
                        "input": {
                            "goal_key": "route",
                            "destination": "Sottocasa",
                            "destination_source": "current_turn",
                        },
                    },
                ),
                _tool_round(
                    "present_route",
                    "present-sottocasa",
                    {
                        "goal_key": "route",
                        "candidate_id": "cd_test_only",
                        "lead_in": "This option best fits the trip and preferences you gave me.",
                        "follow_up": "",
                        "reason_code": "meets_hard_constraints",
                    },
                ),
            ],
            message="route me to sottocasa",
            session=session,
            session_id=session_id,
            trace=trace,
            tool_registry=registry,
        )

        assert "I'll route" not in _rider_text(events)
        assert [name for name, _input in trace.tool_calls] == [
            "declare_goals",
            "prepare_route_options",
            "present_route",
        ]
        assert [event.type for event in events].count("route_card") == 1

    async def test_route_followup_collects_new_evidence_before_synthesis(self) -> None:
        _discard_id, session = session_module.new_session()
        trip_state_module.update_trip_state(
            session,
            origin="Home",
            destination="Midtown",
            active_candidate_set_id="cs_active",
            selected_candidate_id="cd_active",
        )
        trace = self.loop.TurnTrace()
        events, _session = await self._run(
            [
                {
                    "text": ["I need to check the Q and compare the route."],
                    "stop_reason": "end_turn",
                },
                _declared_round(
                    "status",
                    "service_status",
                    {
                        "id": "followup-status",
                        "name": "check_transit",
                        "input": {
                            "goal_key": "status",
                            "operation": "service_status",
                            "route_ids": ["Q"],
                            "stop_query": None,
                            "direction": None,
                            "area": None,
                            "station": None,
                            "topic": None,
                            "event_query": None,
                            "venue": None,
                            "at": None,
                            "window_start": None,
                            "window_end": None,
                            "concerns": [],
                        },
                    },
                    extra_goals=(
                        {
                            "goal_key": "route",
                            "kind": "route",
                            "depends_on": [],
                        },
                    ),
                ),
                _tool_round(
                    "prepare_route_options",
                    "followup-prepare",
                    {
                        "goal_key": "route",
                        "destination_source": "accepted_trip",
                    },
                ),
                _tool_round(
                    "present_transit",
                    "followup-status-present",
                    {
                        "goal_key": "status",
                        "evidence_set_id": "te_action_completion",
                    },
                ),
                _tool_round(
                    "present_route",
                    "followup-present",
                    {
                        "goal_key": "route",
                        "candidate_id": "cd_test_only",
                        "lead_in": "This option best fits the trip and preferences you gave me.",
                        "follow_up": "",
                        "reason_code": "meets_hard_constraints",
                    },
                ),
            ],
            message="The Q is delayed — should I still take it to Midtown?",
            session=session,
            trace=trace,
            tool_registry={
                **_action_registry(),
                "check_transit": ToolSpec(
                    schema={"name": "check_transit"},
                    executor=_fake_check_status_tool,
                    label_fn=lambda _input: "Checking transit…",
                    timeout_s=5.0,
                ),
                "present_transit": ToolSpec(
                    schema={"name": "present_transit"},
                    executor=_fake_present_transit_tool,
                    label_fn=lambda _input: "Presenting transit…",
                    timeout_s=5.0,
                ),
            },
        )

        assert "need to check" not in _rider_text(events).casefold()
        assert [name for name, _input in trace.tool_calls] == [
            "declare_goals",
            "check_transit",
            "prepare_route_options",
            "present_transit",
            "present_route",
        ]

    async def test_pause_without_server_tool_cannot_expose_promise(self) -> None:
        events, _session = await self._run(
            [
                {"text": ["I'll check current sources."], "stop_reason": "pause_turn"},
                _declared_round(
                    "response",
                    "general_response",
                    {
                        "id": "pause-answer",
                        "name": "complete_turn",
                        "input": {
                            "goal_keys": ["response"],
                            "outcome": "answer",
                            "message": "I don't need another source to answer that.",
                        },
                    },
                ),
            ],
            message="Can you explain that?",
        )

        assert "I'll check" not in _rider_text(events)
        assert "don't need another source" in _rider_text(events)

    async def test_forced_wrapup_cannot_end_on_future_action(self) -> None:
        trace = self.loop.TurnTrace()
        with patch.dict(os.environ, {"AGENT_AUTO_MAX_ROUNDS": "1"}, clear=False):
            events, _session = await self._run(
                [
                    _declared_round(
                        "response",
                        "general_response",
                        {"id": "first-tool", "name": "ok_tool", "input": {}},
                    ),
                    {
                        "text": ["Let me check one more source."],
                        "stop_reason": "end_turn",
                    },
                ],
                message="Can you explain that?",
                trace=trace,
                tool_registry=_action_registry(),
            )

        text = _rider_text(events)
        assert "Let me check" not in text
        assert "couldn't complete that request" in text
        assert events[-1].stop_reason == "max_rounds"

    async def test_clause_level_progress_prose_cannot_finish_without_action(
        self,
    ) -> None:
        events, _session = await self._run(
            [
                {
                    "text": [
                        "Got it â€” L'industrie in Williamsburg, avoiding the L. "
                        "Got it, finding L-free routes now."
                    ],
                    "stop_reason": "end_turn",
                },
                {
                    "text": ["I couldn't complete that route search in this turn."],
                    "stop_reason": "end_turn",
                },
            ],
            message="Actually, L'industrie in Williamsburg, but avoid the L.",
        )

        text = _rider_text(events)
        assert "finding L-free routes" not in text
        assert "couldn't complete" in text


if __name__ == "__main__":
    unittest.main()
