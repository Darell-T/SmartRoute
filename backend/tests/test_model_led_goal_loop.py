from __future__ import annotations

import dataclasses
import secrets
import unittest
from unittest.mock import patch

from app.services import cache
from app.services.agent import events as agent_events
from app.services.agent import session as session_module
from app.services.agent.tools import ToolResult

from tests._fake_anthropic import reload_agent_loop_module


def _tool(tool_id: str, name: str, tool_input: dict) -> dict:
    return {"id": tool_id, "name": name, "input": tool_input}


def _round(*tools: dict) -> dict:
    return {"tool_use": list(tools), "stop_reason": "tool_use"}


async def _discover(_tool_input, _ctx):
    return ToolResult(
        ok=True,
        data={
            "discovery_set_id": "ds_goal_loop",
            "operation": "search",
            "places": [{"place_id": "pl_ramen", "name": "Ramen Place"}],
        },
    )


async def _present_places(_tool_input, _ctx):
    return ToolResult(
        ok=True,
        data={"presented": [{"place_id": "pl_ramen"}]},
        events=[agent_events.TokenEvent(text="Ramen Place is the verified pick.")],
    )


async def _prepare(_tool_input, _ctx):
    return ToolResult(
        ok=True,
        data={
            "candidate_set_id": "cs_goal_loop",
            "presentation_allowed": True,
            "candidates": [{"candidate_id": "cd_goal_loop"}],
        },
    )


async def _present_route(tool_input, ctx):
    card = agent_events.RouteCardEvent(
        card_id="rc_goal_loop",
        turn_id=ctx.turn_id,
        role="recommended",
        origin={"label": "Your location"},
        destination={"label": "Ramen Place"},
        summary={"eta_minutes": 20, "transfers": 0, "lines": ["Q"]},
        route=[{"type": "SUBWAY", "route_id": "Q"}],
        alerts=[],
    )
    lead_in = str(tool_input.get("lead_in") or "").strip()
    events = [agent_events.TokenEvent(text=f"{lead_in}\n\n"), card]
    return ToolResult(ok=True, data={"candidate_id": "cd_goal_loop"}, events=events)


async def _check_status(_tool_input, _ctx):
    return ToolResult(
        ok=True,
        data={
            "operation": "service_status",
            "evidence_set_id": "te_goal_loop",
            "evidence": {"checked_routes": ["Q"]},
        },
    )


async def _present_status(_tool_input, _ctx):
    return ToolResult(
        ok=True,
        events=[agent_events.TokenEvent(text="A confirmed Q service alert is active.")],
    )


class ModelLedGoalLoopTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.loop = reload_agent_loop_module()

    def setUp(self) -> None:
        cache._mem.clear()

    def _registry(self, **executors):
        registry = dict(self.loop.TOOL_REGISTRY)
        for name, executor in executors.items():
            registry[name] = dataclasses.replace(registry[name], executor=executor)
        return registry

    async def _run(self, rounds: list[dict], registry: dict, message: str):
        self.loop.client.messages._rounds = list(rounds)
        self.loop.client.messages.calls = []
        _session_id, session = session_module.new_session()
        trace = self.loop.TurnTrace()
        with patch.object(self.loop, "TOOL_REGISTRY", registry):
            events = [
                event
                async for event in self.loop.run_agent_turn(
                    session=session,
                    session_id=secrets.token_hex(8),
                    turn_id="turn",
                    message=message,
                    now_et="2026-08-15T12:00:00-04:00",
                    origin={"lat": 40.65, "lng": -73.95},
                    trace=trace,
                )
            ]
        return events, trace

    async def test_compound_discovery_and_route_cannot_stop_after_places(self) -> None:
        rounds = [
            _round(
                _tool(
                    "goals",
                    "declare_goals",
                    {
                        "goals": [
                            {
                                "goal_key": "destination",
                                "kind": "destination_selection",
                                "depends_on": [],
                            },
                            {
                                "goal_key": "route",
                                "kind": "route",
                                "depends_on": ["destination"],
                            },
                        ]
                    },
                ),
                _tool(
                    "discover",
                    "discover_places",
                    {"goal_key": "destination"},
                ),
            ),
            _round(
                _tool(
                    "prepare",
                    "prepare_route_options",
                    {
                        "goal_key": "route",
                        "destination_place_id": "pl_ramen",
                        "destination_source": "current_turn",
                    },
                ),
            ),
            _round(
                _tool(
                    "route",
                    "present_route",
                    {
                        "goal_key": "route",
                        "candidate_id": "cd_goal_loop",
                        "lead_in": "This route satisfies your subway request.",
                        "follow_up": "",
                        "reason_code": "meets_hard_constraints",
                    },
                )
            ),
        ]
        events, trace = await self._run(
            rounds,
            self._registry(
                discover_places=_discover,
                present_places=_present_places,
                prepare_route_options=_prepare,
                present_route=_present_route,
            ),
            "Find a good ramen spot and route me there by subway.",
        )

        assert [name for name, _tool_input in trace.tool_calls] == ["declare_goals", "discover_places", "prepare_route_options", "present_route"]
        assert trace.model_call_count == 3
        assert any(event.type == "route_card" for event in events)
        assert events[-1].stop_reason == "end_turn"

    async def test_service_status_uses_canonical_transit_presenter(self) -> None:
        rounds = [
            _round(
                _tool(
                    "goals",
                    "declare_goals",
                    {
                        "goals": [
                            {
                                "goal_key": "q_status",
                                "kind": "service_status",
                                "depends_on": [],
                            }
                        ]
                    },
                ),
                _tool(
                    "status",
                    "check_transit",
                    {
                        "goal_key": "q_status",
                        "operation": "service_status",
                    },
                ),
            ),
            _round(
                _tool(
                    "present",
                    "present_transit",
                    {
                        "goal_key": "q_status",
                        "evidence_set_id": "te_goal_loop",
                        "lead_in": "",
                        "follow_up": "",
                    },
                )
            ),
        ]
        events, trace = await self._run(
            rounds,
            self._registry(
                check_transit=_check_status,
                present_transit=_present_status,
            ),
            "Does the downtown Q have any stalled trains currently?",
        )

        assert [name for name, _tool_input in trace.tool_calls] == ["declare_goals", "check_transit", "present_transit"]
        assert trace.model_call_count == 2
        assert "confirmed Q service alert" in "".join(event.text for event in events if event.type == "token")


if __name__ == "__main__":
    unittest.main()
