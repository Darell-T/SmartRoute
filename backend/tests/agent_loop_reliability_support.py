"""Shared setup for focused reliability tests extracted from the loop suite."""

from __future__ import annotations

import unittest

from app.services import cache

from tests.test_agent_loop import _AgentLoopHelpers, _load_agent_loop, _transit_input


def tool_use(name: str, tool_id: str, tool_input: dict) -> dict:
    return {"id": tool_id, "name": name, "input": tool_input}


def multi_tool_round(*uses: dict) -> dict:
    return {"tool_use": list(uses), "stop_reason": "tool_use"}


def goal(goal_key: str, kind: str) -> dict:
    return {"goal_key": goal_key, "kind": kind, "depends_on": []}


def transit_check(
    tool_id: str,
    operation: str,
    *,
    goal_key: str,
    stop_source: str = "auto",
    stop_query: str | None = None,
    direction: str | None = None,
) -> dict:
    return tool_use(
        "check_transit",
        tool_id,
        _transit_input(
            operation,
            route_ids=["Q"],
            stop_source=stop_source,
            stop_query=stop_query,
            direction=direction,
            goal_key=goal_key,
        ),
    )


def transit_present(
    tool_id: str,
    evidence_set_id: str,
    goal_key: str,
    *,
    lead_in: str = "",
) -> dict:
    return tool_use(
        "present_transit",
        tool_id,
        {
            "evidence_set_id": evidence_set_id,
            "goal_key": goal_key,
            "lead_in": lead_in,
            "follow_up": "",
        },
    )


class AgentLoopReliabilityTestCase(
    _AgentLoopHelpers,
    unittest.IsolatedAsyncioTestCase,
):
    @classmethod
    def setUpClass(cls):
        cls.loop = _load_agent_loop()

    def setUp(self):
        cache._mem.clear()


class AgentLoopRoundCapTestCase(AgentLoopReliabilityTestCase):
    @classmethod
    def setUpClass(cls):
        cls.loop = _load_agent_loop(
            {
                "AGENT_AUTO_MAX_ROUNDS": "2",
                "AGENT_QUICK_MAX_ROUNDS": "2",
                "AGENT_TURN_DEADLINE_S": "60",
            }
        )
