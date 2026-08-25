"""Negative terminal-contract tests: complete_turn cannot skip grounding."""

from __future__ import annotations

import dataclasses
import unittest
from unittest.mock import AsyncMock, patch

from app.services.agent import discovery_store
from app.services.agent.model import stream as model_stream
from app.services.agent.tools.places import discover_places
from tests.conversation.conversation_discovery_fixtures import poi_result
from tests.conversation.conversation_matrix_harness import (
    _turn_round,
    complete_turn_round,
    discover_search_input,
    discovery_id_tokens,
    load_agent_loop,
    new_session,
    present_places_round,
    run_turn,
)


def _capability_oks(trace, capability: str) -> list[bool]:
    return [
        bool(attempt["ok"])
        for attempt in trace.capability_attempts
        if attempt["capability"] == capability
    ]


def _declared_discovery_round(
    tool_id: str,
    tool_input: dict,
    *,
    goal_key: str = "places",
    declaration_id: str = "tu-goals",
) -> dict:
    """Script the model-led declaration and first capability in one round."""

    capability_input = dict(tool_input)
    capability_input["goal_key"] = goal_key
    return {
        "tool_use": [
            {
                "id": declaration_id,
                "name": "declare_goals",
                "input": {
                    "goals": [
                        {
                            "goal_key": goal_key,
                            "kind": "place_recommendation",
                            "depends_on": [],
                        }
                    ]
                },
            },
            {
                "id": tool_id,
                "name": "discover_places",
                "input": capability_input,
            },
        ],
        "stop_reason": "tool_use",
    }


def _goal_complete_round(
    tool_id: str,
    message: str,
    goal_key: str,
    *,
    outcome: str = "answer",
) -> dict:
    return _turn_round(
        "complete_turn",
        tool_id,
        {
            "goal_keys": [goal_key],
            "outcome": outcome,
            "message": message,
        },
    )


def _goal_present_places_round(
    tool_id: str,
    set_id: str,
    place_ids: tuple[str, ...] | list[str],
    *,
    goal_key: str = "places",
    research_used: bool = False,
) -> dict:
    round_data = present_places_round(
        tool_id,
        set_id,
        place_ids,
        research_used=research_used,
    )
    round_data["tool_use"][0]["input"]["goal_key"] = goal_key
    if research_used:
        round_data["tool_use"][0]["input"]["lead_in"] = (
            "Current research highlights what these verified places are known for."
        )
    return round_data


class CompleteTurnGroundingLoopTests(unittest.IsolatedAsyncioTestCase):
    loop = None

    @classmethod
    def setUpClass(cls):
        cls.loop = load_agent_loop()

    async def _turn(self, message: str, rounds: list):
        _sid, session = new_session()
        return await run_turn(
            self.loop,
            session=session,
            session_id="sess-term",
            message=message,
            rounds=rounds,
            mode="auto",
            turn_id="t1",
            trace=self.loop.TurnTrace(),
        )

    async def test_q_status_answer_without_check_transit_is_rejected(self):
        events, trace = await self._turn(
            "Are there delays on the Q?",
            [
                complete_turn_round("tu-lie", "No delays."),
                complete_turn_round(
                    "tu-ask",
                    "Which station should I check?",
                    outcome="clarification",
                ),
            ],
        )
        self.assertFalse(_capability_oks(trace, "complete_turn")[0])
        self.assertNotIn("no delays", (trace.final_text or "").casefold())

    async def test_pizza_recommendations_without_discover_are_rejected(self):
        events, trace = await self._turn(
            "Find good pizza in Manhattan.",
            [
                complete_turn_round("tu-lie", "Try Joe's Pizza on Carmine."),
                complete_turn_round(
                    "tu-ask",
                    "Which neighborhood?",
                    outcome="clarification",
                ),
            ],
        )
        self.assertFalse(_capability_oks(trace, "complete_turn")[0])
        self.assertNotIn("joe's", (trace.final_text or "").casefold())

    async def test_explicit_discovery_scope_cannot_be_replaced_by_clarification(self):
        events, trace = await self._turn(
            "Find good pizza in Manhattan.",
            [
                complete_turn_round(
                    "tu-ask",
                    "Which neighborhood?",
                    outcome="clarification",
                ),
            ],
        )
        self.assertFalse(_capability_oks(trace, "complete_turn")[0])
        self.assertNotIn("which neighborhood", (trace.final_text or "").casefold())

    async def test_route_answer_cannot_replace_canonical_route_workflow(self):
        events, trace = await self._turn(
            "Route me to Barclays Center.",
            [
                complete_turn_round(
                    "tu-fake-route",
                    "Take the Q to Atlantic Av.",
                ),
            ],
        )
        self.assertFalse(_capability_oks(trace, "complete_turn")[0])
        self.assertNotIn("take the q", (trace.final_text or "").casefold())
        self.assertFalse(any(event.type == "route_card" for event in events))

    async def test_successful_discovery_cannot_complete_with_clarification(self):
        set_id, place_ids = discovery_id_tokens("sess-term", "t1")
        ids = iter(place_ids)
        _sid, session = new_session()
        with (
            patch.object(
                discover_places.search_local_places,
                "execute",
                new=AsyncMock(return_value=poi_result()),
            ),
            patch.object(discovery_store, "new_discovery_set_id", return_value=set_id),
            patch.object(discovery_store, "new_place_id", side_effect=lambda: next(ids)),
        ):
            events, trace = await run_turn(
                self.loop,
                session=session,
                session_id="sess-term",
                message="Find good pizza in Brooklyn.",
                rounds=[
                    _declared_discovery_round(
                        "tu-disc",
                        discover_search_input("pizza", borough="Brooklyn"),
                    ),
                    _goal_complete_round(
                        "tu-stall",
                        "I found a few places. What would you like?",
                        "places",
                        outcome="clarification",
                    ),
                    _goal_present_places_round("tu-pres", set_id, place_ids),
                ],
                mode="auto",
                turn_id="t1",
                trace=self.loop.TurnTrace(),
            )
        names = [name for name, _input in trace.tool_calls]
        self.assertEqual(names[:3], ["declare_goals", "discover_places", "complete_turn"])
        self.assertFalse(_capability_oks(trace, "complete_turn")[0])
        self.assertIn("present_places", names)

    async def test_unsupported_superlative_is_truthfully_downgraded(self):
        set_id, place_ids = discovery_id_tokens("sess-term", "t1")
        ids = iter(place_ids)
        _sid, session = new_session()
        unsupported = _turn_round(
            "present_places",
            "tu-invalid-superlative",
            {
                "discovery_set_id": set_id,
                "selections": [
                    {"place_id": place_ids[1], "reason": "most_reviewed"}
                ],
                "research_used": False,
            },
        )
        with (
            patch.object(
                discover_places.search_local_places,
                "execute",
                new=AsyncMock(return_value=poi_result()),
            ),
            patch.object(discovery_store, "new_discovery_set_id", return_value=set_id),
            patch.object(discovery_store, "new_place_id", side_effect=lambda: next(ids)),
        ):
            events, trace = await run_turn(
                self.loop,
                session=session,
                session_id="sess-term",
                message="Find good pizza in Brooklyn.",
                rounds=[
                    _declared_discovery_round(
                        "tu-disc",
                        discover_search_input("pizza", borough="Brooklyn"),
                    ),
                    {
                        **unsupported,
                        "tool_use": [
                            {
                                **unsupported["tool_use"][0],
                                "input": {
                                    **unsupported["tool_use"][0]["input"],
                                    "goal_key": "places",
                                },
                            }
                        ],
                    },
                    _goal_present_places_round("tu-pres", set_id, place_ids),
                ],
                mode="auto",
                turn_id="t1",
                trace=self.loop.TurnTrace(),
            )

        self.assertTrue(_capability_oks(trace, "present_places")[0])
        self.assertNotIn("most_reviewed", trace.final_text)
        self.assertNotIn("stored facts", trace.final_text.casefold())
        self.assertIn("B Pizza", trace.final_text)
        self.assertNotIn("matches your request", trace.final_text.casefold())


class WebLifecycleLoopTests(unittest.IsolatedAsyncioTestCase):
    loop = None

    @classmethod
    def setUpClass(cls):
        cls.loop = load_agent_loop()

    async def test_web_is_offered_once_after_search_then_consumed(self):
        set_id, place_ids = discovery_id_tokens("sess-web", "t1")
        ids = iter(place_ids)
        calls = {"n": 0}
        real = model_stream.stream_model_call

        async def inject_web(**kwargs):
            calls["n"] += 1
            async for item in real(**kwargs):
                if calls["n"] == 2 and isinstance(item, model_stream.ModelCallCompleted):
                    yield dataclasses.replace(
                        item,
                        web_used=True,
                        web_succeeded=True,
                        server_tool_call_count=max(item.server_tool_call_count, 1),
                    )
                else:
                    yield item

        _sid, session = new_session()
        with (
            patch.object(model_stream, "stream_model_call", new=inject_web),
            patch.object(
                discover_places.search_local_places,
                "execute",
                new=AsyncMock(return_value=poi_result()),
            ),
            patch.object(discovery_store, "new_discovery_set_id", return_value=set_id),
            patch.object(discovery_store, "new_place_id", side_effect=lambda: next(ids)),
        ):
            events, trace = await run_turn(
                self.loop,
                session=session,
                session_id="sess-web",
                message="Find good pizza in Brooklyn.",
                rounds=[
                    _declared_discovery_round(
                        "tu-disc",
                        discover_search_input("pizza", borough="Brooklyn"),
                    ),
                    {"text": [], "stop_reason": "pause_turn"},
                    _goal_present_places_round(
                        "tu-pres", set_id, place_ids, research_used=True
                    ),
                ],
                mode="auto",
                turn_id="t1",
                trace=self.loop.TurnTrace(),
            )
        recorded = self.loop.client.messages.calls
        first_names = [tool.get("name") for tool in recorded[0]["tools"]]
        second_names = [tool.get("name") for tool in recorded[1]["tools"]]
        third_names = [tool.get("name") for tool in recorded[2]["tools"]]
        self.assertNotIn("web_search", first_names)
        self.assertIn("web_search", second_names)
        # Anthropic requires an exact tool-surface replay for the paused
        # server-tool continuation. Web is consumed only after that response
        # completes; the presenter then proves the successful result carried
        # across the protocol boundary.
        self.assertIn("web_search", third_names)
        self.assertTrue(_capability_oks(trace, "present_places")[0])
        self.assertTrue(trace.terminal_resolution["terminal"])

if __name__ == "__main__":
    unittest.main()
