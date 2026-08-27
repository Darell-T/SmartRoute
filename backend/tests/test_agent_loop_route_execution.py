from __future__ import annotations

from app.services.agent import session as session_module
from app.services.agent import trip_state as trip_state_module
from app.services.agent.tools import ToolResult, ToolSpec

from tests.agent_loop_reliability_support import AgentLoopReliabilityTestCase
from tests.test_agent_loop import (
    _complete_round,
    _declared_route_round,
    _fake_ambiguous_route_tool,
    _fake_prepare_route_options_tool,
    _fake_present_route_tool,
    _model_led_registry,
    _route_present_round,
    _test_registry,
    _tool_round,
    _trace_tool_input,
)


class AgentLoopRouteExecutionTests(AgentLoopReliabilityTestCase):
    async def test_model_declared_required_route_reaches_route_preparation(self):
        trace = self.loop.TurnTrace()
        rounds = [
            _declared_route_round(
                "prepare_route_options",
                "tu_1",
                {"destination": "Coney Island", "required_route_ids": ["Q"]},
            ),
            _route_present_round("tu_2"),
        ]

        await self._run(
            rounds,
            message="Plan a Q route to Coney Island",
            tool_registry=_model_led_registry(),
            trace=trace,
        )

        assert _trace_tool_input(trace, "prepare_route_options")["required_route_ids"] == ["Q"]

    async def test_model_declared_what_if_exclusion_stays_temporary(self):
        trace = self.loop.TurnTrace()
        _discard_id, session = session_module.new_session()
        session["active_trip"] = {"card_id": "rc_active", "role": "recommended"}
        trip_state_module.update_trip_state(
            session,
            origin="Home",
            destination="Work",
            active_candidate_set_id="cs_active",
            selected_candidate_id="cd_active",
        )
        registry = _model_led_registry()

        async def present_preview(tool_input, ctx):
            result = await _fake_present_route_tool(tool_input, ctx)
            result.session_route_cards = []
            return result

        registry["present_route"] = ToolSpec(
            schema={"name": "present_route"},
            executor=present_preview,
            label_fn=lambda _input: "Presenting the route…",
            timeout_s=5.0,
        )
        rounds = [
            _declared_route_round(
                "prepare_route_options",
                "tu_1",
                {
                    "destination": "Coney Island",
                    "excluded_route_ids": ["Q"],
                    "what_if": True,
                },
            ),
            _route_present_round("tu_2"),
        ]

        _events, session = await self._run(
            rounds,
            message="What if I avoid the Q?",
            session=session,
            session_id="sess-what-if-avoid-q",
            tool_registry=registry,
            trace=trace,
        )

        tool_call = _trace_tool_input(trace, "prepare_route_options")
        assert tool_call["what_if"]
        assert tool_call["excluded_route_ids"] == ["Q"]
        assert "required_route_ids" not in tool_call
        # A what-if exclusion is temporary and never becomes an active slot.
        assert "excluded_route_ids" not in ((session.get("slots") or {}).get("constraints") or {})
        # Server-enforced what-if isolation keeps active candidate/trip state.
        state = trip_state_module.get_trip_state(session)
        assert state["active_candidate_set_id"] == "cs_active"
        assert state["selected_candidate_id"] == "cd_active"
        assert session["active_trip"]["card_id"] == "rc_active"

    async def test_named_discovery_handoff_routes_without_reresolving_place(self):
        from app.services.agent import discovery_store

        session_id = "sess-named-route"
        _discard_id, session = session_module.new_session()
        set_id = discovery_store.store_discovery_set(
            session_id=session_id,
            places=[
                {
                    "name": "Mike's Pizza",
                    "address": "5 Martense St, Brooklyn",
                    "latitude": 40.65,
                    "longitude": -73.96,
                },
                {
                    "name": "Angelo's Pizza",
                    "address": "1092 Flatbush Ave, Brooklyn",
                    "latitude": 40.64,
                    "longitude": -73.96,
                },
            ],
            query="pizza",
        )
        record = discovery_store.load_discovery_set(
            set_id, session_id=session_id
        )
        mike_id = record["places"][0]["place_id"]
        trip_state_module.bind_discovery_set(session, set_id)
        registry = _model_led_registry()

        async def prepare_named(tool_input, ctx):
            assert "destination" not in tool_input
            assert tool_input.get("destination_place_id") == mike_id
            trip_state_module.bind_discovery_context(
                ctx.session,
                discovery_set_id=set_id,
                selected_place_id=mike_id,
            )
            trip_state_module.bind_candidate_set(ctx.session, "cs_test_only")
            return await _fake_prepare_route_options_tool(tool_input, ctx)

        registry["prepare_route_options"] = ToolSpec(
            schema={"name": "prepare_route_options"},
            executor=prepare_named,
            label_fn=lambda _input: "Preparing routes…",
            timeout_s=5.0,
        )
        trace = self.loop.TurnTrace()
        events_out, final_session = await self._run(
            [
                _declared_route_round(
                    "prepare_route_options",
                    "prepare-named",
                    {"destination_place_id": mike_id},
                ),
                _route_present_round("present-named"),
            ],
            message="i can go to mikes",
            session=session,
            session_id=session_id,
            tool_registry=registry,
            trace=trace,
        )

        assert [name for name, _tool_input in trace.tool_calls] == ["declare_goals", "prepare_route_options", "present_route"]
        assert [call.get("tool_choice") for call in self.loop.client.messages.calls] == [{"type": "tool", "name": "declare_goals"}, {"type": "tool", "name": "present_route"}]
        state = trip_state_module.get_trip_state(final_session)
        assert state["active_discovery_set_id"] == set_id
        assert state["selected_place_id"] == mike_id
        assert [event.type for event in events_out].count("route_card") == 1

    async def test_accepted_trip_alternative_requires_real_prepare_then_present(self):
        _discard_id, session = session_module.new_session()
        trip_state_module.update_trip_state(
            session,
            origin="Home",
            destination="Madison Square Garden",
            active_candidate_set_id="cs_active",
            selected_candidate_id="cd_active",
        )
        registry = _model_led_registry()

        async def prepare_alternative(tool_input, ctx):
            assert tool_input.get("what_if")
            trip_state_module.bind_temporary_candidate_set(
                ctx.session,
                "cs_alternative",
                base_candidate_set_id="cs_active",
            )
            return ToolResult(
                ok=True,
                data={
                    "candidate_set_id": "cs_alternative",
                    "route_status": "good",
                    "presentation_allowed": True,
                    "candidates": [{"candidate_id": "cd_alternative"}],
                },
                summary="prepared an alternative",
            )

        registry["prepare_route_options"] = ToolSpec(
            schema={"name": "prepare_route_options"},
            executor=prepare_alternative,
            label_fn=lambda _input: "Checking alternative routes\u2026",
            timeout_s=5.0,
        )
        trace = self.loop.TurnTrace()
        events_out, session = await self._run(
            [
                _declared_route_round(
                    "prepare_route_options",
                    "prepare-alt",
                    {"what_if": True},
                ),
                _route_present_round("present-alt", "cd_alternative"),
            ],
            message="what are the other options",
            session=session,
            tool_registry=registry,
            trace=trace,
        )

        assert [name for name, _tool_input in trace.tool_calls] == ["declare_goals", "prepare_route_options", "present_route"]
        assert [call.get("tool_choice") for call in self.loop.client.messages.calls] == [{"type": "tool", "name": "declare_goals"}, {"type": "tool", "name": "present_route"}]
        rider_text = "".join(
            event.text for event in events_out if event.type == "token"
        )
        assert "prepare_route_options" not in rider_text
        assert "candidate_id" not in rider_text
        assert "moment for the results" not in rider_text
        deliberation = "".join(
            event.text for event in events_out if event.type == "reasoning"
        )
        assert "Thinking through your request" in deliberation
        assert "prepare_route_options" not in deliberation
        assert trip_state_module.get_trip_state(session)["active_candidate_set_id"] == "cs_active"
        assert [event.type for event in events_out].count("route_card") == 1

    async def test_route_prose_cannot_replace_canonical_tool_execution(self):
        trace = self.loop.TurnTrace()

        events_out, _session = await self._run(
            [
                {
                    "text": [
                        "Take the 2 train, transfer to the R, and allow about "
                        "35 minutes."
                    ],
                    "stop_reason": "end_turn",
                }
            ],
            message="Route me to Supreme Pizza",
            tool_registry=_test_registry(),
            trace=trace,
        )

        rider_text = "".join(
            event.text for event in events_out if event.type == "token"
        )
        assert rider_text == "I couldn't complete that request in this turn, so I don't have " "a verified result to share."
        assert "2 train" not in rider_text
        assert trace.tool_calls == []
        assert [call["tool_choice"] for call in self.loop.client.messages.calls] == [{"type": "any"}] * len(self.loop.client.messages.calls)

    async def test_quick_keeps_sonnet_when_route_preparation_requires_clarification(self):
        rounds = [
            _tool_round(
                "prepare_route_options",
                "tu_1",
                {"destination": "34th Street"},
            ),
            _complete_round(
                "Which 34th Street stop do you mean?",
                outcome="clarification",
            ),
        ]
        registry = _test_registry()
        registry["prepare_route_options"] = ToolSpec(
            schema={"name": "prepare_route_options"},
            executor=_fake_ambiguous_route_tool,
            label_fn=lambda _input: "Preparing routes…",
            timeout_s=5.0,
        )
        trace = self.loop.TurnTrace()

        await self._run(
            rounds,
            message="Take me to 34th Street",
            response_presentation="quick",
            tool_registry=registry,
            trace=trace,
        )

        assert trace.initial_mode == "quick"
        assert trace.final_mode == "quick"
        assert [name for name, _input in trace.tool_calls] == ["prepare_route_options", "complete_turn"]
        assert self.loop.client.messages.calls[1]["model"] == self.loop.agent_policy.policy_for_mode("quick").model

    async def test_model_declared_no_bus_is_enforced_at_route_preparation_boundary(self):
        rounds = [
            _declared_route_round(
                "prepare_route_options",
                "tu_1",
                {"destination": "Costco", "exclude_modes": ["BUS"]},
            ),
            _route_present_round("tu_2"),
        ]
        trace = self.loop.TurnTrace()

        _events, session = await self._run(
            rounds,
            message="Heading to Costco, no bus",
            tool_registry=_model_led_registry(),
            trace=trace,
        )

        assert _trace_tool_input(trace, "prepare_route_options")["exclude_modes"] == ["BUS"]
        assert session["slots"]["constraints"]["exclude_modes"] == ["BUS"]
