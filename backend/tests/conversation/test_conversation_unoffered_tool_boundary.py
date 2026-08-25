"""Batch F1 boundary contract: rejection behavior for unoffered calls.

Focused companion to ``test_conversation_unoffered_tool_enforcement.py``.
Cases 8-11 prove the corrected enforcement contract: mixed allowed +
unoffered rounds stay paired and execute only the allowed block; duplicate
unoffered calls execute nothing; a state-valid surface rejects any registered
client leaf; and the native server-side web_search keeps its own streamed
path with no registry executor. Each probe drives the real agent
loop, real registry, real ledger, real stores, and real SSE path with
fail-loud provider seams.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

from app.services.agent import discovery_store
from app.services.agent import trip_state as trip_state_module
from app.services.agent.tools.places import search_local_places
from tests.conversation.conversation_discovery_fixtures import poi_result
from tests.conversation.conversation_matrix_harness import (
    _turn_round,
    load_agent_loop,
    make_leg,
)
from tests.conversation.conversation_unoffered_tool_fixtures import (
    DISCOVER_PLACES_INPUT,
    DISCOVERY_GOALS_INPUT,
    DISCOVERY_MESSAGE,
    DISCOVERY_READY_PROFILE,
    DISCOVERY_PROFILE,
    GENERAL_RESPONSE_GOALS_INPUT,
    GENERAL_RESPONSE_STATE_VALID_PROFILE,
    ROUTE_GOALS_INPUT,
    ROUTE_PLANNING_MESSAGE,
    ROUTE_PLANNING_TOOL_PROFILE,
    ROUTE_READY_PROFILE,
    LOOKUP_FACTS_INPUT,
    PREPARE_ROUTE_OPTIONS_INPUT,
    PRESENT_ROUTE_FRAMING_INPUT,
    SERVICE_STATUS_GOALS_INPUT,
    SEARCH_LOCAL_PLACES_INPUT,
    TRANSIT_STATE_VALID_PROFILE,
    TRANSIT_QUESTION_MESSAGE,
    TRANSIT_QUESTION_TOOL_PROFILE,
    registered_tool_names,
)
from tests.conversation.conversation_unoffered_tool_support import (
    _OfferedSurfaceBase,
    _preamble_normalized,
    fail_loud_spy,
    run_probe,
)

# Genuine provider/data seams an unoffered leaf executor would cross first.
PREPARE_SEAM = (
    "app.services.agent.tools.route.prepare_route_options.prepare_single_leg"
)
POI_SEAM = "app.services.agent.tools.places.search_local_places.execute"


class UnofferedToolBoundaryTests(_OfferedSurfaceBase):
    """Cases 8-11: the corrected enforcement contract for rejected calls."""

    @classmethod
    def setUpClass(cls):
        cls.loop = load_agent_loop()

    async def _case8(self, mode: str, scenario_id: str) -> None:
        # Mixed same-round blocks: the offered tool executes once, the
        # unoffered registered tool is rejected, and every ToolEnd stays
        # paired to its own tool_use id.
        leg = make_leg(route_ids=("Q",), destination="Coney Island")
        seams = {
            "poi_seam": (POI_SEAM, fail_loud_spy("poi seam")),
            "prepare_seam": (
                PREPARE_SEAM,
                AsyncMock(return_value=leg),
            ),
        }
        prepare_input = dict(PREPARE_ROUTE_OPTIONS_INPUT)
        prepare_input["goal_key"] = "route"
        rounds = [
            {
                "tool_use": [
                    {
                        "id": "tu-mix-goals",
                        "name": "declare_goals",
                        "input": dict(ROUTE_GOALS_INPUT),
                    },
                    {
                        "id": "tu-mix-a",
                        "name": "prepare_route_options",
                        "input": prepare_input,
                    },
                    {
                        "id": "tu-mix-b",
                        "name": "search_local_places",
                        "input": dict(SEARCH_LOCAL_PLACES_INPUT),
                    },
                ],
                "stop_reason": "tool_use",
            },
            _turn_round(
                "present_route",
                "tu-mix-done",
                {
                    "goal_key": "route",
                    "candidate_id": "cd_f1_mix",
                    **PRESENT_ROUTE_FRAMING_INPUT,
                },
            ),
        ]
        with patch(
            "app.services.agent.candidate_store.new_candidate_id",
            return_value="cd_f1_mix",
        ):
            ev = await self._probe(
                mode=mode,
                message=ROUTE_PLANNING_MESSAGE,
                rounds=rounds,
                seams=seams,
            )
        self._assert_turn_shape(ev, scenario_id)
        self._assert_offered_exact(
            ev, ROUTE_PLANNING_TOOL_PROFILE, scenario_id
        )
        self.assertEqual(ev.offered_surfaces[1], ROUTE_READY_PROFILE, scenario_id)
        self._assert_unoffered_not_offered(
            ev, "search_local_places", scenario_id
        )
        self.assertEqual(ev.spies["poi_seam"].await_count, 0, scenario_id)
        self.assertEqual(ev.spies["prepare_seam"].await_count, 1, scenario_id)
        self.assertEqual(ev.provider_execution_count, 2, scenario_id)
        self.assertNotIn(
            "search_local_places",
            [tool for tool, _input in ev.tool_calls],
            f"{scenario_id}: unoffered leaf must not enter the ledger",
        )
        self.assertEqual(ev.discovery_store_calls, (), scenario_id)
        ends_by_id = {
            call_id: (tool, ok, summary)
            for tool, ok, summary, call_id in ev.tool_ends
        }
        tool_a, ok_a, _summary_a = ends_by_id["tu-mix-a"]
        self.assertEqual(tool_a, "prepare_route_options", scenario_id)
        self.assertTrue(ok_a, f"{scenario_id}: offered prepare must run")
        tool_b, ok_b, summary_b = ends_by_id["tu-mix-b"]
        self.assertEqual(tool_b, "search_local_places", scenario_id)
        self.assertFalse(ok_b, f"{scenario_id}: unoffered leaf rejected")
        self.assertIn("not available", summary_b, scenario_id)
        self.assertNotEqual(
            ev.state_after["trip_state"]["active_candidate_set_id"],
            None,
            f"{scenario_id}: offered prepare must bind its candidate set",
        )
        self.assertEqual(
            ev.state_after["trip_state"]["destination"],
            "Coney Island",
            scenario_id,
        )
        self.assertEqual(len(ev.cards), 1, scenario_id)

    async def test_08a_mixed_allowed_and_unoffered_same_round_auto(self):
        await self._case8("auto", "F1-08a")

    async def test_08b_mixed_allowed_and_unoffered_same_round_quick(self):
        await self._case8("quick", "F1-08b")

    async def _case9(self, mode: str, scenario_id: str) -> None:
        # Duplicate unoffered calls: every duplicate is rejected and nothing
        # reaches the executor, provider seam, or ledger.
        seams = {"poi_seam": (POI_SEAM, fail_loud_spy("poi seam"))}
        rounds = [
            {
                "tool_use": [
                    {
                        "id": "tu-dup-goals",
                        "name": "declare_goals",
                        "input": dict(SERVICE_STATUS_GOALS_INPUT),
                    },
                    {
                        "id": "tu-dup-a",
                        "name": "search_local_places",
                        "input": dict(SEARCH_LOCAL_PLACES_INPUT),
                    },
                    {
                        "id": "tu-dup-b",
                        "name": "search_local_places",
                        "input": dict(SEARCH_LOCAL_PLACES_INPUT),
                    },
                ],
                "stop_reason": "tool_use",
            },
            _turn_round(
                "complete_turn",
                "tu-dup-done",
                {
                    "goal_keys": ["transit"],
                    "outcome": "clarification",
                    "message": "I could not verify that transit request.",
                },
            ),
        ]
        ev = await self._probe(
            mode=mode,
            message=TRANSIT_QUESTION_MESSAGE,
            rounds=rounds,
            seams=seams,
        )
        self._assert_turn_shape(ev, scenario_id)
        self._assert_offered_exact(
            ev, TRANSIT_QUESTION_TOOL_PROFILE, scenario_id
        )
        self.assertEqual(
            ev.offered_surfaces[1], TRANSIT_STATE_VALID_PROFILE, scenario_id
        )
        self.assertEqual(
            ev.spies["poi_seam"].await_count,
            0,
            f"{scenario_id}: duplicate unoffered calls must never execute; "
            f"{ev.compact()}",
        )
        self.assertEqual(ev.provider_execution_count, 1, scenario_id)
        self._assert_unoffered_rejected(
            ev, "search_local_places", scenario_id
        )
        rejected = [
            (tool, ok, summary)
            for tool, ok, summary, _call_id in ev.tool_ends
            if tool == "search_local_places"
        ]
        self.assertEqual(len(rejected), 2, f"{scenario_id}: both duplicates surface")
        self.assertTrue(
            all(
                not ok and "not available" in summary
                for _tool, ok, summary in rejected
            ),
            f"{scenario_id}: both duplicates must be bounded rejections",
        )
        self.assertEqual(
            ev.state_after["trip_state"],
            ev.state_before["trip_state"],
            scenario_id,
        )
        self.assertEqual(ev.cards, (), scenario_id)

    async def test_09a_duplicate_unoffered_calls_execute_nothing_auto(self):
        await self._case9("auto", "F1-09a")

    async def test_09b_duplicate_unoffered_calls_execute_nothing_quick(self):
        await self._case9("quick", "F1-09b")

    async def _case10(self, mode: str, scenario_id: str) -> None:
        # A general-response state-valid surface contains only complete_turn;
        # any registered client leaf the model emits is rejected with a
        # bounded error. The explicit cancellation still discards the
        # temporary scenario.
        session_id, session = self._new_session(mode)
        trip_state_module.update_trip_state(
            session, temporary_candidate_set_id="cs_temp_seed"
        )
        rounds = [
            {
                "tool_use": [
                    {
                        "id": "tu-empty-goals",
                        "name": "declare_goals",
                        "input": dict(GENERAL_RESPONSE_GOALS_INPUT),
                    },
                    {
                        "id": "tu-empty",
                        "name": "lookup_facts",
                        "input": dict(LOOKUP_FACTS_INPUT),
                    },
                ],
                "stop_reason": "tool_use",
            },
            _turn_round(
                "complete_turn",
                "tu-empty-done",
                {
                    "goal_keys": ["response"],
                    "outcome": "cancelled",
                    "message": "Understood, I cancelled that request.",
                },
            ),
        ]
        ev = await run_probe(
            self.loop,
            session=session,
            session_id=session_id,
            message="never mind",
            rounds=rounds,
            mode=mode,
            turn_id="t1",
        )
        self._assert_turn_shape(ev, scenario_id)
        self.assertEqual(
            ev.offered, TRANSIT_QUESTION_TOOL_PROFILE, scenario_id
        )
        self.assertEqual(
            ev.offered_surfaces[1], GENERAL_RESPONSE_STATE_VALID_PROFILE, scenario_id
        )
        self.assertEqual(ev.provider_execution_count, 1, scenario_id)
        ends = {
            tool: (ok, summary)
            for tool, ok, summary, _call_id in ev.tool_ends
        }
        self.assertIn("lookup_facts", ends, scenario_id)
        ok, summary = ends["lookup_facts"]
        self.assertFalse(ok, f"{scenario_id}: leaf must stay unoffered")
        self.assertIn("not available", summary, scenario_id)
        self.assertEqual(
            _preamble_normalized(ev.state_after)["slots"],
            _preamble_normalized(ev.state_before)["slots"],
            scenario_id,
        )
        self.assertIsNone(
            ev.state_after["trip_state"]["destination"],
            f"{scenario_id}: rejected tool must not set destination",
        )
        self.assertEqual(
            ev.state_after["trip_state"]["active_candidate_set_id"],
            None,
            f"{scenario_id}: rejected tool must not bind a candidate set",
        )
        self.assertEqual(
            ev.state_after["history_tool_summaries"],
            ["complete_turn"],
            f"{scenario_id}: rejected leaf must not append a summary",
        )
        self.assertEqual(ev.state_after["pending_trip"], ev.state_before["pending_trip"], scenario_id)
        self.assertEqual(ev.cards, (), scenario_id)
        self.assertEqual(
            ev.state_before["trip_state"]["temporary_candidate_set_id"],
            "cs_temp_seed",
            scenario_id,
        )
        self.assertIsNone(
            ev.state_after["trip_state"]["temporary_candidate_set_id"],
            f"{scenario_id}: scenario reject must discard the temporary set",
        )

    async def test_10a_state_valid_surface_rejects_registered_tool_auto(self):
        await self._case10("auto", "F1-10a")

    async def test_10b_state_valid_surface_rejects_registered_tool_quick(self):
        await self._case10("quick", "F1-10b")

    async def test_11_web_search_offered_surface_and_no_fake_executor(self):
        # Native web_search is evidence-gated: it is absent from the initial
        # five, then appears only after a successful structured discovery.
        self.assertNotIn("web_search", registered_tool_names())
        set_id = "ds_f1_web"
        place_ids = ("pl_f1_web_1", "pl_f1_web_2", "pl_f1_web_3")
        discover_input = dict(DISCOVER_PLACES_INPUT)
        discover_input["goal_key"] = "places"
        rounds = [
            {
                "tool_use": [
                    {
                        "id": "tu-ws-goals",
                        "name": "declare_goals",
                        "input": dict(DISCOVERY_GOALS_INPUT),
                    },
                    {
                        "id": "tu-ws-discover",
                        "name": "discover_places",
                        "input": discover_input,
                    },
                ],
                "stop_reason": "tool_use",
            },
            _turn_round(
                "present_places",
                "tu-ws-done",
                {
                    "goal_key": "places",
                    "discovery_set_id": set_id,
                    "selections": [
                        {"place_id": place_ids[0], "reason": "top_pick"},
                        {"place_id": place_ids[1], "reason": "preference_match"},
                    ],
                    "research_used": False,
                },
            ),
        ]
        place_ids_iter = iter(place_ids)
        with (
            patch.object(
                search_local_places,
                "execute",
                new=AsyncMock(return_value=poi_result()),
            ),
            patch.object(
                discovery_store,
                "new_discovery_set_id",
                return_value=set_id,
            ),
            patch.object(
                discovery_store,
                "new_place_id",
                side_effect=lambda: next(place_ids_iter),
            ),
        ):
            ev = await self._probe(
                mode="auto",
                message=DISCOVERY_MESSAGE,
                rounds=rounds,
                record_discovery_store=True,
            )
        self._assert_turn_shape(ev, "F1-11")
        self._assert_offered_exact(ev, DISCOVERY_PROFILE, "F1-11")
        self.assertNotIn("web_search", ev.offered_surfaces[0], "F1-11")
        self.assertEqual(ev.offered_surfaces[1], DISCOVERY_READY_PROFILE, "F1-11")
        self.assertEqual(ev.discovery_store_calls, (set_id,), "F1-11")
        self.assertEqual(ev.provider_execution_count, 2, "F1-11")
        self.assertNotIn(
            "web_search",
            [tool for tool, _call_id in ev.tool_starts],
            "F1-11",
        )
        self.assertNotIn(
            "web_search",
            [tool for tool, _ok, _summary, _call_id in ev.tool_ends],
            "F1-11",
        )
        ends = {tool: ok for tool, ok, _summary, _call_id in ev.tool_ends}
        self.assertTrue(ends["discover_places"], "F1-11")
        present_attempts = [
            attempt
            for attempt in ev.capability_attempts
            if attempt["capability"] == "present_places"
        ]
        self.assertTrue(
            present_attempts and present_attempts[0]["ok"],
            "F1-11",
        )
        self.assertEqual(ev.cards, (), "F1-11")


__all__ = ()
