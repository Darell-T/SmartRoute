from __future__ import annotations

import dataclasses

from app.services.agent import session as session_module
from app.services.agent.tools.transit import evidence as transit_evidence
from app.services.agent.tools.transit import present_transit
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
    _complete_round,
    _fake_check_transit_tool,
    _model_led_registry,
    _test_registry,
    _tool_round,
    _transit_input,
)


class AgentLoopTransitGroundingTests(AgentLoopReliabilityTestCase):
    async def test_q_delay_is_model_led_and_grounded_in_service_status(self):
        trace = self.loop.TurnTrace()
        message = "Are there any delays on the Q?"

        events_out, _session = await self._run(
            [
                _tool_round(
                    "check_transit",
                    "q-status",
                    _transit_input("service_status", route_ids=["Q"]),
                ),
                _complete_round(
                    "Yes. Southbound Q trains are currently delayed."
                ),
            ],
            message=message,
            tool_registry=_test_registry(),
            trace=trace,
        )

        self.assertEqual(
            [name for name, _input in trace.tool_calls],
            ["check_transit", "complete_turn"],
        )
        self.assertEqual(
            self.loop.client.messages.calls[0]["tool_choice"],
            {"type": "any"},
        )
        self.assertIn(message, self.loop.client.messages.calls[0]["messages"][-1]["content"])
        self.assertFalse(any(event.type == "arrival_card" for event in events_out))
        self.assertIn("currently delayed", trace.final_text)

    async def test_arrivals_cannot_substitute_for_required_service_status(self):
        trace = self.loop.TurnTrace()

        events_out, _session = await self._run(
            [
                _tool_round(
                    "check_transit",
                    "wrong-arrival-first",
                    _transit_input(
                        "arrivals",
                        route_ids=["Q"],
                        stop_query="Church Av",
                    ),
                ),
                _tool_round(
                    "check_transit",
                    "required-status",
                    _transit_input("service_status", route_ids=["Q"]),
                ),
                _complete_round("The Q has a current southbound delay."),
            ],
            message="Are there any delays on the Q?",
            tool_registry=_test_registry(),
            trace=trace,
        )

        self.assertEqual(
            [name for name, _input in trace.tool_calls],
            ["check_transit", "check_transit", "complete_turn"],
        )
        self.assertEqual(
            [
                call["tool_choice"]
                for call in self.loop.client.messages.calls
            ],
            [{"type": "any"}] * 3,
        )
        self.assertIn("current southbound delay", trace.final_text)
        self.assertTrue(any(event.type == "arrival_card" for event in events_out))

    async def test_failed_status_grounding_suppresses_a_false_all_clear(self):
        trace = self.loop.TurnTrace()
        registry = _test_registry()
        async def status_fails_arrivals_succeed(tool_input, ctx):
            if tool_input.get("operation") == "service_status":
                evidence = getattr(ctx, "turn_evidence", None)
                if evidence is not None:
                    evidence.note_check_transit(
                        ok=False,
                        operation="service_status",
                    )
                return ToolResult(ok=False, error="provider unavailable")
            return await _fake_check_transit_tool(tool_input, ctx)

        registry["check_transit"] = ToolSpec(
            schema={"name": "check_transit"},
            executor=status_fails_arrivals_succeed,
            label_fn=lambda _input: "Checking current service conditions",
            timeout_s=5.0,
        )

        events_out, _session = await self._run(
            [
                {
                    "tool_use": [
                        {
                            "id": "failed-status",
                            "name": "check_transit",
                            "input": _transit_input(
                                "service_status", route_ids=["Q"]
                            ),
                        },
                        {
                            "id": "supporting-arrivals",
                            "name": "check_transit",
                            "input": _transit_input(
                                "arrivals",
                                route_ids=["Q"],
                                stop_query="Church Ave",
                            ),
                        },
                    ],
                    "stop_reason": "tool_use",
                },
                _complete_round(
                    "Current Q service status is unavailable.",
                    outcome="unavailable",
                ),
            ],
            message="Are there any delays on the Q?",
            tool_registry=registry,
            trace=trace,
        )

        self.assertIn("unavailable", trace.final_text.casefold())
        self.assertNotIn("no q delays", trace.final_text.casefold())
        self.assertTrue(any(event.type == "arrival_card" for event in events_out))
        self.assertEqual(events_out[-1].terminal_state, "completed")

    async def test_known_direction_take_wait_checks_status_and_arrivals_before_advice(self):
        registry = _model_led_registry()

        async def checked_transit(tool_input, ctx):
            operation = str(tool_input.get("operation") or "")
            if operation == "service_status":
                raw = {
                    "source": "mta_service_alerts",
                    "freshness": "stale",
                    "status": "active_alerts",
                    "alerts": [
                        {
                            "alert_id": "q-stale",
                            "header": "Uptown Q service may be delayed",
                            "route_ids": ["Q"],
                            "direction": "uptown",
                        }
                    ],
                }
            else:
                raw = {
                    "source": "mta_arrivals",
                    "freshness": "live",
                    "route_id": "Q",
                    "source_status": "current",
                    "stop": {"id": "D28", "name": "Church Ave"},
                    "directions": [
                        {
                            "id": "uptown",
                            "label": "Uptown / Manhattan-bound",
                            "arrivals": [{"minutes": 4, "realtime": True}],
                        }
                    ],
                }
            evidence_id, evidence = transit_evidence.build_evidence_set(
                session_id=ctx.session_id,
                operation=operation,
                route_ids=["Q"],
                direction="uptown",
                result=raw,
                evidence_set_id=f"es_take_wait_{operation}",
                turn_id=ctx.turn_id,
            )
            return ToolResult(
                ok=True,
                data={
                    "operation": operation,
                    "result": raw,
                    "evidence_set_id": evidence_id,
                    "evidence": evidence,
                },
                summary=f"checked {operation}",
            )

        registry["check_transit"] = dataclasses.replace(
            registry["check_transit"], executor=checked_transit
        )
        registry["present_transit"] = ToolSpec(
            schema={"name": "present_transit"},
            executor=present_transit.execute,
            label_fn=lambda _input: "Presenting checked transit information…",
            timeout_s=5.0,
        )
        trace = self.loop.TurnTrace()
        _session_id, session = session_module.new_session()
        session["active_trip"] = {
            "first_boarding": {
                "route_id": "Q",
                "stop_id": "D28",
                "stop_name": "Church Ave",
                "direction_label": "uptown",
            }
        }
        events_out, _session = await self._run(
            [
                multi_tool_round(
                    tool_use(
                        "declare_goals",
                        "take-wait-goals",
                        {"goals": [goal("status", "service_status"), goal("arrivals", "arrivals")]},
                    ),
                    transit_check(
                        "take-wait-status",
                        "service_status",
                        goal_key="status",
                        stop_source="accepted_trip",
                        direction="uptown",
                    ),
                    transit_check(
                        "take-wait-arrivals",
                        "arrivals",
                        goal_key="arrivals",
                        stop_source="accepted_trip",
                        direction="uptown",
                    ),
                ),
                multi_tool_round(
                    transit_present(
                        "take-wait-present-status",
                        "es_take_wait_service_status",
                        "status",
                        lead_in="I would wait for a fresher status check before relying on this.",
                    ),
                    transit_present(
                        "take-wait-present-arrivals",
                        "es_take_wait_arrivals",
                        "arrivals",
                    ),
                ),
            ],
            message="Is it smart to take the uptown Q now?",
            session=session,
            tool_registry=registry,
            trace=trace,
        )

        names = [name for name, _input in trace.tool_calls]
        self.assertEqual(
            names,
            [
                "declare_goals",
                "check_transit",
                "check_transit",
                "present_transit",
                "present_transit",
            ],
        )
        check_indexes = [
            index for index, name in enumerate(names) if name == "check_transit"
        ]
        present_indexes = [
            index for index, name in enumerate(names) if name == "present_transit"
        ]
        self.assertLess(max(check_indexes), min(present_indexes))
        self.assertEqual(
            [call["operation"] for name, call in trace.tool_calls if name == "check_transit"],
            ["service_status", "arrivals"],
        )
        self.assertEqual(
            [call["stop_source"] for name, call in trace.tool_calls if name == "check_transit"],
            ["accepted_trip", "accepted_trip"],
        )
        self.assertFalse(any(event.type == "clarification" for event in events_out))
        visible = "".join(event.text for event in events_out if event.type == "token")
        self.assertIn("out of date", visible.casefold())
        self.assertIn("fresher", visible.casefold())
        self.assertNotIn("no active alert", visible.casefold())
        self.assertNotIn("no affected service", visible.casefold())
        arrival_card = next(event for event in events_out if event.type == "arrival_card")
        self.assertEqual(arrival_card.route_id, "Q")
        self.assertEqual(arrival_card.stop["id"], "D28")
        self.assertEqual(arrival_card.directions[0]["arrivals"][0]["minutes"], 4)

    async def test_resolved_arrival_is_selected_by_the_model_from_active_context(self):
        _session_id, session = session_module.new_session()
        session["active_trip"] = {
            "first_boarding": {
                "route_id": "q",
                "stop_id": "D28",
                "stop_name": "Church Av",
                "direction_id": 1,
                "direction_label": "Coney Island-Stillwell Av",
                "destination_stop_id": "D43",
            }
        }
        trace = self.loop.TurnTrace()
        events_out, _session = await self._run(
            [
                _tool_round(
                    "check_transit",
                    "arrival-active",
                    _transit_input("arrivals", route_ids=["Q"]),
                ),
                _complete_round(
                    "The next downtown Q at Newkirk Plaza is in 4 minutes."
                ),
            ],
            message="When does the next q arrive?",
            session=session,
            response_presentation="quick",
            tool_registry=_test_registry(),
            trace=trace,
        )
        self.assertEqual(trace.tool_calls[0][0], "check_transit")
        self.assertEqual(trace.tool_calls[0][1]["operation"], "arrivals")
        self.assertFalse(any(name == "plan_trip" for name, _ in trace.tool_calls))
        arrival_event = next(event for event in events_out if event.type == "arrival_card")
        self.assertEqual(arrival_event.resolution_status, "resolved")
        self.assertEqual(len(self.loop.client.messages.calls), 2)
        self.assertEqual(
            "".join(event.text for event in events_out if event.type == "token"),
            "The next downtown Q at Newkirk Plaza is in 4 minutes.",
        )
        self.assertEqual([event.type for event in events_out].count("done"), 1)
        self.assertEqual(events_out[-1].stop_reason, "end_turn")
        self.assertEqual(events_out[-1].terminal_state, "completed")
        self.assertEqual(trace.model_call_count, 2)
        self.assertEqual(trace.tool_call_count, 2)
        self.assertEqual(trace.retry_count, 0)
        self.assertGreaterEqual(trace.stage_ms["arrival_lookup_ms"], 0)

    async def test_implicit_arrival_without_active_trip_is_clarified_by_model(self):
        trace = self.loop.TurnTrace()
        events_out, _session = await self._run(
            [
                _complete_round(
                    "Which train or bus route do you mean?",
                    outcome="clarification",
                )
            ],
            message="when is the next arrival",
            tool_registry=_test_registry(),
            trace=trace,
        )

        self.assertEqual(len(self.loop.client.messages.calls), 1)
        self.assertFalse(any(name == "check_transit" for name, _ in trace.tool_calls))
        self.assertEqual(
            "".join(event.text for event in events_out if event.type == "token"),
            "Which train or bus route do you mean?",
        )
        self.assertEqual(events_out[-1].terminal_state, "clarification_required")
        self.assertFalse(any(event.type == "error" for event in events_out))

    async def test_ambiguous_q_take_wait_clarifies_before_any_transit_call(self):
        trace = self.loop.TurnTrace()
        events_out, _session = await self._run(
            [
                _complete_round(
                    "Should I take the Q uptown or downtown?",
                    outcome="clarification",
                )
            ],
            message="Is it smart to take the Q right now?",
            tool_registry=_test_registry(),
            trace=trace,
        )

        self.assertEqual(events_out[-1].terminal_state, "clarification_required")
        self.assertEqual(
            [name for name, _input in trace.tool_calls],
            ["complete_turn"],
        )
        self.assertEqual(
            "".join(event.text for event in events_out if event.type == "token"),
            "Should I take the Q uptown or downtown?",
        )
        self.assertFalse(any(event.type == "arrival_card" for event in events_out))
