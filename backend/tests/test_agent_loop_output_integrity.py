from __future__ import annotations

import dataclasses
import secrets
from unittest.mock import patch

from app.services.agent import events as agent_events
from app.services.agent import session as session_module
from app.services.agent.tools import ToolResult, ToolSpec

from tests.agent_loop_reliability_support import (
    AgentLoopReliabilityTestCase,
    goal,
    multi_tool_round,
    tool_use,
    transit_check,
    transit_present,
)
from tests.test_agent_loop import (
    _DEFAULT_ROUTE_EXPLANATION,
    _complete_round,
    _declared_route_round,
    _fake_check_transit_tool,
    _fake_prepare_route_options_tool,
    _model_led_registry,
    _offered_schemas_for_registry,
    _route_present_round,
    _test_registry,
    _tool_round,
)


class AgentLoopOutputIntegrityTests(AgentLoopReliabilityTestCase):
    async def test_terminal_tool_emits_rider_text_once(self):
        events_out, _ = await self._run(
            [_complete_round("Hello. World.")],
            tool_registry=_test_registry(),
        )
        tokens = [event.text for event in events_out if event.type == "token"]
        assert tokens == ["Hello. World."]

    def test_persisted_tool_summaries_are_not_replayed_as_assistant_prose(self):
        messages = self.loop._messages_from_history(
            [
                {"role": "user", "text": "Get me to MSG"},
                {
                    "role": "tool",
                    "tool": "prepare_route_options",
                    "text": "prepared three candidates",
                },
                {"role": "assistant", "text": "I found a route."},
            ]
        )

        assert messages == [{"role": "user", "content": "Get me to MSG"}, {"role": "assistant", "content": "I found a route."}]

    def test_runtime_syntax_and_fake_waiting_are_removed_from_rider_text(self):
        sanitized = self.loop._sanitize_rider_text(
            "[prepare_route_options destination_place_id=pl_secret]\n"
            "[get_place_details place_id=pl_other]\n"
            "Give me a moment for the results."
        )

        assert sanitized == ""

    async def test_truncated_tool_round_is_never_executed_or_marked_complete(self):
        trace = self.loop.TurnTrace()
        events_out, _ = await self._run(
            [
                {
                    "tool_use": [
                        {
                            "id": "truncated-goals",
                            "name": "declare_goals",
                            "input": {
                                "goals": [
                                    {
                                        "goal_key": "route",
                                        "kind": "route",
                                        "depends_on": [],
                                    }
                                ]
                            },
                        }
                    ],
                    "stop_reason": "max_tokens",
                }
            ],
            message="Get me to Barclays Center and check the downtown Q.",
            tool_registry=_model_led_registry(),
            trace=trace,
        )

        errors = [event for event in events_out if event.type == "error"]
        assert [event.code for event in errors] == ["response_incomplete"]
        assert errors[0].retryable
        assert events_out[-1].stop_reason == "error"
        assert trace.tool_calls == []
        assert not trace.terminal_resolution["terminal"]

    async def test_failed_tool_diagnostics_are_model_only(self):
        registry = _test_registry()
        canary = "PROVIDER_SECRET_CANARY_71C9 internal_cluster=transit-prod"

        async def leaking_failure(_tool_input, _ctx):
            return ToolResult(ok=False, error=canary)

        registry["fail_tool"] = dataclasses.replace(
            registry["fail_tool"],
            executor=leaking_failure,
        )
        rounds = [
            {
                "tool_use": [
                    {"id": "tu_1", "name": "fail_tool", "input": {}}
                ],
                "stop_reason": "tool_use",
            },
            {"text": ["done"], "stop_reason": "end_turn"},
        ]

        events_out, _ = await self._run(rounds, tool_registry=registry)

        tool_end = next(e for e in events_out if e.type == "tool_end")
        assert tool_end.summary == "That action could not be completed"
        assert canary not in str(tool_end)
        model_context = self.loop.client.messages.calls[1]["messages"][-1]
        assert canary in model_context["content"][0]["content"]

    async def test_model_progress_prose_is_discarded_during_route_execution(self):
        rounds = [
            _declared_route_round(
                "prepare_route_options", "tu_1", {"destination": "Costco"}
            ),
            _route_present_round("tu_2"),
        ]

        events_out, session = await self._run(
            rounds,
            tool_registry=_model_led_registry(),
        )

        event_types = [event.type for event in events_out]
        token_events = [event for event in events_out if event.type == "token"]
        assert event_types.index("meta") < event_types.index("tool_start")
        assert event_types.index("tool_start") < event_types.index("tool_end")
        assert event_types.index("tool_end") < event_types.index("route_card")
        route_card_index = event_types.index("route_card")
        assert event_types.index("token") < route_card_index
        assert event_types[-1] == "done"
        rider_text = "".join(event.text for event in events_out if event.type == "token")
        assert len(token_events) == 1
        assert token_events[0].text == _DEFAULT_ROUTE_EXPLANATION
        assert rider_text == token_events[0].text
        assert "I'll compare" not in rider_text
        assert "[ROUTE:" not in rider_text
        assert "CANDIDATE_ANALYSIS" not in rider_text
        assert session["history"][-1]["text"] == rider_text
        assert len(self.loop.client.messages.calls) == 2

    async def test_route_execution_does_not_inject_progress_acknowledgement(self):
        rounds = [
            _declared_route_round(
                "prepare_route_options",
                "tu_1",
                {
                    "destination": "Costco",
                    "exclude_modes": ["BUS"],
                    "routing_preference": "LESS_WALKING",
                },
            ),
            _route_present_round("tu_2"),
        ]

        events_out, _session = await self._run(
            rounds,
            tool_registry=_model_led_registry(),
        )

        token_events = [event for event in events_out if event.type == "token"]
        event_types = [event.type for event in events_out]
        assert event_types.index("token") < event_types.index("route_card")
        assert len(token_events) == 1
        assert token_events[0].text == _DEFAULT_ROUTE_EXPLANATION
        assert "I'll plan" not in token_events[0].text
        assert len(self.loop.client.messages.calls) == 2

    async def test_turn_summary_emits_before_done_when_client_closes_at_terminal_event(self):
        async def telemetry_route_preparation(tool_input, ctx):
            return await _fake_prepare_route_options_tool(tool_input, ctx)

        registry = _model_led_registry()
        registry["prepare_route_options"] = ToolSpec(
            schema={"name": "prepare_route_options"},
            executor=telemetry_route_preparation,
            label_fn=lambda _input: "Finding routes…",
            timeout_s=5.0,
        )
        self.loop.client.messages._rounds = [
            _declared_route_round(
                "prepare_route_options",
                "tu_telemetry",
                {"destination": "Costco"},
            ),
            _route_present_round("tu_present"),
        ]
        self.loop.client.messages.calls = []
        _discard_id, session = session_module.new_session()
        timeline = []

        def record_print(*args, **_kwargs):
            if args and str(args[0]).startswith("[agent]"):
                timeline.append("turn_summary")

        with (
            patch.object(self.loop, "TOOL_REGISTRY", registry),
            patch.object(
                self.loop,
                "_tools_for_state",
                lambda *_args, **_kwargs: _offered_schemas_for_registry(
                    registry
                ),
            ),
            patch("builtins.print", side_effect=record_print) as printed,
        ):
            stream = self.loop.run_agent_turn(
                session=session,
                session_id=secrets.token_hex(8),
                turn_id="t1",
                message="Get me to Costco",
                now_et="2026-07-15T21:00:00-04:00",
            )
            while True:
                event = await anext(stream)
                timeline.append(event.type)
                if event.type == "done":
                    break
            await stream.aclose()

        summary_prints = [
            call
            for call in printed.call_args_list
            if call.args and str(call.args[0]).startswith("[agent]")
        ]
        assert len(summary_prints) == 1
        assert timeline.index("turn_summary") < timeline.index("done")
        assert "mode=auto" in str(summary_prints[0].args[0])
        assert session["history"][-1]["text"] == _DEFAULT_ROUTE_EXPLANATION
        assert len(self.loop.client.messages.calls) == 2

    async def test_compound_arrival_status_and_route_synthesizes_all_evidence(self):
        trace = self.loop.TurnTrace()
        message = (
            "Can I catch the next Q at Church Ave and still make it to MSG "
            "by 7 without getting stuck in the delays?"
        )
        registry = _model_led_registry()
        transit_payloads: dict[str, dict] = {}

        async def compound_check_transit(tool_input, ctx):
            result = await _fake_check_transit_tool(tool_input, ctx)
            operation = str(tool_input.get("operation") or "transit")
            evidence_set_id = f"es_compound_{operation}"
            payload = dict(result.data or {})
            payload["evidence_set_id"] = evidence_set_id
            transit_payloads[evidence_set_id] = payload
            return dataclasses.replace(result, data=payload, events=[])

        async def compound_present_transit(tool_input, ctx):
            evidence_set_id = str(tool_input.get("evidence_set_id") or "")
            goal_key = str(tool_input.get("goal_key") or "")
            payload = transit_payloads.get(evidence_set_id) or {}
            operation = str(payload.get("operation") or "transit")
            result_payload = payload.get("result")
            events = []
            if operation == "arrivals":
                events.append(
                    agent_events.ArrivalCardEvent.from_lookup(
                        ctx.turn_id,
                        result_payload if isinstance(result_payload, dict) else {},
                    )
                )
            return ToolResult(
                ok=True,
                data={
                    "evidence_set_id": evidence_set_id,
                    "goal_key": goal_key,
                    "operation": operation,
                    "presentation_outcome": {"status": "presented"},
                },
                summary=f"Presented checked {operation.replace('_', ' ')}",
                events=events,
            )

        registry["check_transit"] = dataclasses.replace(
            registry["check_transit"], executor=compound_check_transit
        )
        registry["present_transit"] = ToolSpec(
            schema={"name": "present_transit"},
            executor=compound_present_transit,
            label_fn=lambda _input: "Presenting checked transit information…",
            timeout_s=5.0,
        )
        events_out, _session = await self._run(
            [
                multi_tool_round(
                    tool_use(
                        "declare_goals",
                        "compound-goals",
                        {
                            "goals": [
                                goal("status", "service_status"),
                                goal("arrivals", "arrivals"),
                                goal("route", "route"),
                            ]
                        },
                    ),
                    transit_check("compound-status", "service_status", goal_key="status"),
                    transit_check("compound-arrival", "arrivals", goal_key="arrivals"),
                ),
                _tool_round(
                    "prepare_route_options",
                    "compound-prepare",
                    {
                        "goal_key": "route",
                        "destination": "Madison Square Garden",
                        "destination_source": "current_turn",
                        "arrival_by": "2026-07-15T19:00:00-04:00",
                    },
                ),
                _tool_round(
                    "present_route",
                    "compound-present-route",
                    {
                        "candidate_id": "cd_test_only",
                        "goal_key": "route",
                        "lead_in": _DEFAULT_ROUTE_EXPLANATION,
                        "follow_up": "",
                        "reason_code": "meets_hard_constraints",
                    },
                ),
                multi_tool_round(
                    transit_present(
                        "compound-present-status",
                        "es_compound_service_status",
                        "status",
                    ),
                    transit_present(
                        "compound-present-arrivals",
                        "es_compound_arrivals",
                        "arrivals",
                    ),
                ),
            ],
            message=message,
            tool_registry=registry,
            trace=trace,
        )

        assert [name for name, _input in trace.tool_calls] == ["declare_goals", "check_transit", "check_transit", "prepare_route_options", "present_route", "present_transit", "present_transit"]
        assert len(self.loop.client.messages.calls) == 4
        assert [event.type for event in events_out].count("route_card") == 1
        assert any(event.type == "arrival_card" for event in events_out)
        assert _DEFAULT_ROUTE_EXPLANATION in trace.final_text
