"""Batch F1 audit: server enforcement of the per-turn offered-tool surface.

The probes drive the real agent loop, real registry, real ledger, real
stores, and real SSE path; only Anthropic inference and the genuine
provider/data seams are scripted. Each unoffered-case seam is a fail-loud
spy: a recorded call means an unoffered executor started provider work.

Enforced contract (accepted P1 remediation): every model ``tool_use`` block
is checked against the exact per-turn offered surface before the ledger,
executor, provider, store, session, pending-trip, progress, or retry paths.
A registered-but-unoffered call is rejected with a bounded failed
tool-result so the conversational model can recover next round. The
attempted call legitimately remains observable as a paired ToolStart/
ToolEnd failure (ok=False) -- observability is not overconstrained -- but it
must never execute or mutate anything. Unknown names use the same
before-ledger invariant; the native server-side web_search keeps its own
streamed path and has no registry executor.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

from app.services.agent import tools as agent_tools
from app.services.agent.tools.transit import evidence as transit_evidence

from tests.conversation.conversation_matrix_harness import (
    _turn_round,
    load_agent_loop,
    make_leg,
)
from tests.conversation.conversation_unoffered_tool_fixtures import (
    CHECK_TRANSIT_FACT_INPUT,
    DISCOVERY_GOALS_INPUT,
    DISCOVERY_MESSAGE,
    DISCOVERY_PROFILE,
    DISCOVERY_STATE_VALID_PROFILE,
    FIXED_CANDIDATE_ID,
    GENERAL_RESPONSE_GOALS_INPUT,
    GENERAL_RESPONSE_STATE_VALID_PROFILE,
    LOOKUP_FACTS_INPUT,
    PREPARE_ROUTE_OPTIONS_INPUT,
    PRESENT_ROUTE_FRAMING_INPUT,
    ROUTE_GOALS_INPUT,
    ROUTE_PLANNING_MESSAGE,
    ROUTE_PLANNING_TOOL_PROFILE,
    ROUTE_READY_PROFILE,
    ROUTE_STATE_VALID_PROFILE,
    SEARCH_LOCAL_PLACES_INPUT,
    SERVICE_STATUS_GOALS_INPUT,
    TRANSIT_FACT_GOALS_INPUT,
    TRANSIT_FACT_MESSAGE,
    TRANSIT_FACT_TOOL_PROFILE,
    TRANSIT_QUESTION_MESSAGE,
    TRANSIT_QUESTION_TOOL_PROFILE,
    TRANSIT_READY_PROFILE,
    TRANSIT_SNAPSHOT_INPUT,
    TRANSIT_STATE_VALID_PROFILE,
    UNKNOWN_TOOL_INPUT,
    UNKNOWN_TOOL_NAME,
    registered_tool_names,
)
from tests.conversation.conversation_unoffered_tool_support import (
    _OfferedSurfaceBase,
    fail_loud_spy,
    run_probe,
)

# Genuine provider/data seams each unoffered executor would cross first.
PREPARE_SEAM = "app.services.agent.tools.route.prepare_route_options.prepare_single_leg"
POI_SEAM = "app.services.agent.tools.places.search_local_places.execute"
ALERTS_SEAM = "app.services.mta.realtime.fetch_service_alerts"
LOOKUP_FACTS_SEAM = "app.services.agent.tools.transit.lookup_facts.execute"


class UnofferedToolEnforcementTests(_OfferedSurfaceBase):
    """Cases 1-4: non-public emissions must never execute."""

    @classmethod
    def setUpClass(cls):
        cls.loop = load_agent_loop()

    async def _case1(self, mode: str, scenario_id: str) -> None:
        # Premise: lookup_facts is registered internally but never offered.
        assert "lookup_facts" in registered_tool_names()
        seams = {
            "facts_seam": (LOOKUP_FACTS_SEAM, fail_loud_spy("lookup_facts seam"))
        }
        rounds = [
            {
                "tool_use": [
                    {
                        "id": "tu-1-goals",
                        "name": "declare_goals",
                        "input": dict(TRANSIT_FACT_GOALS_INPUT),
                    },
                    {
                        "id": "tu-1",
                        "name": "lookup_facts",
                        "input": dict(LOOKUP_FACTS_INPUT),
                    },
                ],
                "stop_reason": "tool_use",
            },
            _turn_round(
                "complete_turn",
                "tu-1-done",
                {
                    "goal_keys": ["transit"],
                    "outcome": "clarification",
                    "message": "I could not verify that transit fact.",
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
        assert ev.offered_surfaces[1] == TRANSIT_STATE_VALID_PROFILE, scenario_id
        self._assert_unoffered_not_offered(ev, "lookup_facts", scenario_id)
        self._assert_unoffered_rejected(ev, "lookup_facts", scenario_id)

    async def test_01a_transit_question_unoffered_lookup_facts_auto(
        self,
    ):
        await self._case1("auto", "F1-01a")

    async def _case2(self, mode: str, scenario_id: str) -> None:
        assert "search_local_places" not in registered_tool_names()
        seams = {"poi_seam": (POI_SEAM, fail_loud_spy("poi seam"))}
        rounds = [
            {
                "tool_use": [
                    {
                        "id": "tu-2-goals",
                        "name": "declare_goals",
                        "input": dict(SERVICE_STATUS_GOALS_INPUT),
                    },
                    {
                        "id": "tu-2",
                        "name": "search_local_places",
                        "input": dict(SEARCH_LOCAL_PLACES_INPUT),
                    },
                ],
                "stop_reason": "tool_use",
            },
            _turn_round(
                "complete_turn",
                "tu-2-done",
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
            record_discovery_store=True,
        )
        self._assert_turn_shape(ev, scenario_id)
        self._assert_offered_exact(
            ev, TRANSIT_QUESTION_TOOL_PROFILE, scenario_id
        )
        assert ev.offered_surfaces[1] == TRANSIT_STATE_VALID_PROFILE, scenario_id
        self._assert_unoffered_not_offered(
            ev, "search_local_places", scenario_id
        )
        self._assert_unoffered_rejected(
            ev, "search_local_places", scenario_id
        )
        self._assert_discovery_untouched(ev, scenario_id)

    async def test_02a_transit_question_unoffered_search_local_places_auto(
        self,
    ):
        await self._case2("auto", "F1-02a")

    async def _case3(self, mode: str, scenario_id: str) -> None:
        assert "search_local_places" not in registered_tool_names()
        seams = {"poi_seam": (POI_SEAM, fail_loud_spy("poi seam"))}
        rounds = [
            {
                "tool_use": [
                    {
                        "id": "tu-3-goals",
                        "name": "declare_goals",
                        "input": dict(ROUTE_GOALS_INPUT),
                    },
                    {
                        "id": "tu-3",
                        "name": "search_local_places",
                        "input": dict(SEARCH_LOCAL_PLACES_INPUT),
                    },
                ],
                "stop_reason": "tool_use",
            },
            _turn_round(
                "complete_turn",
                "tu-3-done",
                {
                    "goal_keys": ["route"],
                    "outcome": "clarification",
                    "message": "I could not verify that route.",
                },
            ),
        ]
        ev = await self._probe(
            mode=mode,
            message=ROUTE_PLANNING_MESSAGE,
            rounds=rounds,
            seams=seams,
            record_discovery_store=True,
        )
        self._assert_turn_shape(ev, scenario_id)
        self._assert_offered_exact(
            ev, ROUTE_PLANNING_TOOL_PROFILE, scenario_id
        )
        assert ev.offered_surfaces[1] == ROUTE_STATE_VALID_PROFILE, scenario_id
        self._assert_unoffered_not_offered(
            ev, "search_local_places", scenario_id
        )
        self._assert_unoffered_rejected(
            ev, "search_local_places", scenario_id
        )
        self._assert_discovery_untouched(ev, scenario_id)

    async def test_03a_route_planning_unoffered_search_local_places_auto(
        self,
    ):
        await self._case3("auto", "F1-03a")

    async def _case4(self, mode: str, scenario_id: str) -> None:
        assert "transit_snapshot" in registered_tool_names()
        seams = {"alerts_seam": (ALERTS_SEAM, fail_loud_spy("alerts seam"))}
        rounds = [
            {
                "tool_use": [
                    {
                        "id": "tu-4-goals",
                        "name": "declare_goals",
                        "input": dict(DISCOVERY_GOALS_INPUT),
                    },
                    {
                        "id": "tu-4",
                        "name": "transit_snapshot",
                        "input": dict(TRANSIT_SNAPSHOT_INPUT),
                    },
                ],
                "stop_reason": "tool_use",
            },
            _turn_round(
                "complete_turn",
                "tu-4-done",
                {
                    "goal_keys": ["places"],
                    "outcome": "clarification",
                    "message": "I could not verify those places.",
                },
            ),
        ]
        ev = await self._probe(
            mode=mode,
            message=DISCOVERY_MESSAGE,
            rounds=rounds,
            seams=seams,
        )
        self._assert_turn_shape(ev, scenario_id)
        self._assert_offered_exact(ev, DISCOVERY_PROFILE, scenario_id)
        assert ev.offered_surfaces[1] == DISCOVERY_STATE_VALID_PROFILE, scenario_id
        self._assert_unoffered_not_offered(
            ev, "transit_snapshot", scenario_id
        )
        self._assert_unoffered_rejected(
            ev, "transit_snapshot", scenario_id
        )

    async def test_04a_discovery_unoffered_transit_snapshot_auto(self):
        await self._case4("auto", "F1-04a")


class UnofferedToolImpactTests(_OfferedSurfaceBase):
    """Case 1 impact: a successful internal leaf is still rejected first."""

    @classmethod
    def setUpClass(cls):
        cls.loop = load_agent_loop()

    async def _case1_impact(self, mode: str, scenario_id: str) -> None:
        facts = AsyncMock(
            return_value=agent_tools.ToolResult(ok=True, summary="facts")
        )
        seams = {"facts_seam": (LOOKUP_FACTS_SEAM, facts)}
        rounds = [
            {
                "tool_use": [
                    {
                        "id": "tu-1i-goals",
                        "name": "declare_goals",
                        "input": dict(TRANSIT_FACT_GOALS_INPUT),
                    },
                    {
                        "id": "tu-1i",
                        "name": "lookup_facts",
                        "input": dict(LOOKUP_FACTS_INPUT),
                    },
                ],
                "stop_reason": "tool_use",
            },
            _turn_round(
                "complete_turn",
                "tu-1i-done",
                {
                    "goal_keys": ["transit"],
                    "outcome": "clarification",
                    "message": "I could not verify that transit fact.",
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
        assert ev.offered_surfaces[1] == TRANSIT_STATE_VALID_PROFILE, scenario_id
        self._assert_unoffered_not_offered(ev, "lookup_facts", scenario_id)
        self._assert_unoffered_rejected(ev, "lookup_facts", scenario_id)
        assert facts.await_count == 0, f"{scenario_id}: successful leaf must still be rejected first"

    async def test_01c_transit_question_unoffered_lookup_facts_impact_auto(self):
        await self._case1_impact("auto", "F1-01c")


class RegistryCopyEnforcementTests(_OfferedSurfaceBase):
    """Cases 12a/12b: the allowlist guard is registry-agnostic.

    A shallow copy of the production registry is a different object, not
    the canonical registry identity; every registered-but-unoffered client
    tool must still be rejected with zero executor/provider/store/session
    activity. The copy keeps the real ToolSpec executors, so any slipped
    execution would cross the fail-loud provider seam.
    """

    @classmethod
    def setUpClass(cls):
        cls.loop = load_agent_loop()

    async def _case12(self, mode: str, scenario_id: str) -> None:
        registry_copy = dict(agent_tools.COMBINED_TOOL_REGISTRY)
        assert registry_copy is not agent_tools.COMBINED_TOOL_REGISTRY
        assert "lookup_facts" in registry_copy
        seams = {
            "facts_seam": (LOOKUP_FACTS_SEAM, fail_loud_spy("lookup_facts seam"))
        }
        rounds = [
            {
                "tool_use": [
                    {
                        "id": "tu-copy-goals",
                        "name": "declare_goals",
                        "input": dict(TRANSIT_FACT_GOALS_INPUT),
                    },
                    {
                        "id": "tu-copy",
                        "name": "lookup_facts",
                        "input": dict(LOOKUP_FACTS_INPUT),
                    },
                ],
                "stop_reason": "tool_use",
            },
            _turn_round(
                "complete_turn",
                "tu-copy-done",
                {
                    "goal_keys": ["transit"],
                    "outcome": "clarification",
                    "message": "I could not verify that transit fact.",
                },
            ),
        ]
        session_id, session = self._new_session(mode)
        with patch.object(self.loop, "TOOL_REGISTRY", registry_copy):
            ev = await run_probe(
                self.loop,
                session=session,
                session_id=session_id,
                message=TRANSIT_QUESTION_MESSAGE,
                rounds=rounds,
                mode=mode,
                seams=seams,
            )
        self._assert_turn_shape(ev, scenario_id)
        self._assert_offered_exact(
            ev, TRANSIT_QUESTION_TOOL_PROFILE, scenario_id
        )
        assert ev.offered_surfaces[1] == TRANSIT_STATE_VALID_PROFILE, scenario_id
        self._assert_unoffered_not_offered(ev, "lookup_facts", scenario_id)
        self._assert_unoffered_rejected(ev, "lookup_facts", scenario_id)

    async def test_12a_registry_copy_still_rejects_unoffered_auto(self):
        await self._case12("auto", "F1-12a")


class OfferedSurfaceControlTests(_OfferedSurfaceBase):
    """Cases 5-7: unknown names, offered controls, and ledger duplicates."""

    @classmethod
    def setUpClass(cls):
        cls.loop = load_agent_loop()

    async def _case5(self, mode: str, scenario_id: str) -> None:
        assert UNKNOWN_TOOL_NAME not in registered_tool_names()
        rounds = [
            {
                "tool_use": [
                    {
                        "id": "tu-5-goals",
                        "name": "declare_goals",
                        "input": dict(GENERAL_RESPONSE_GOALS_INPUT),
                    },
                    {
                        "id": "tu-5",
                        "name": UNKNOWN_TOOL_NAME,
                        "input": dict(UNKNOWN_TOOL_INPUT),
                    },
                ],
                "stop_reason": "tool_use",
            },
            _turn_round(
                "complete_turn",
                "tu-5-done",
                {
                    "goal_keys": ["response"],
                    "outcome": "refusal",
                    "message": "I can't help with that request.",
                },
            ),
        ]
        ev = await self._probe(
            mode=mode, message=TRANSIT_QUESTION_MESSAGE, rounds=rounds
        )
        self._assert_turn_shape(ev, scenario_id)
        self._assert_offered_exact(
            ev, TRANSIT_QUESTION_TOOL_PROFILE, scenario_id
        )
        assert ev.offered_surfaces[1] == GENERAL_RESPONSE_STATE_VALID_PROFILE, scenario_id
        # Unknown names use the same before-ledger boundary as registered
        # unoffered names: the bounded failure is observable, but the unknown
        # call is absent from the ledger and only terminal recovery executes.
        self._assert_unoffered_rejected(ev, UNKNOWN_TOOL_NAME, scenario_id)
        assert ev.emitted == ("declare_goals", "complete_turn"), scenario_id
        assert ev.provider_execution_count == 1, scenario_id

    async def test_05a_unknown_tool_name_normalized_failure_auto(self):
        await self._case5("auto", "F1-05a")

    async def _case6a(self, mode: str, scenario_id: str) -> None:
        # Offered transit-fact tool control: executes exactly once.
        seams = {"poi_seam": (POI_SEAM, fail_loud_spy("poi seam"))}
        check_input = dict(CHECK_TRANSIT_FACT_INPUT)
        check_input["goal_key"] = "transit"
        rounds = [
            {
                "tool_use": [
                    {
                        "id": "tu-6-goals",
                        "name": "declare_goals",
                        "input": dict(TRANSIT_FACT_GOALS_INPUT),
                    },
                    {
                        "id": "tu-6",
                        "name": "check_transit",
                        "input": check_input,
                    },
                ],
                "stop_reason": "tool_use",
            },
            _turn_round(
                "present_transit",
                "tu-6-done",
                {
                    "goal_key": "transit",
                    "evidence_set_id": "te_f1_transit",
                },
            ),
        ]
        with patch.object(
            transit_evidence,
            "new_evidence_set_id",
            return_value="te_f1_transit",
        ):
            ev = await self._probe(
                mode=mode,
                message=TRANSIT_FACT_MESSAGE,
                rounds=rounds,
                seams=seams,
            )
        self._assert_turn_shape(ev, scenario_id)
        self._assert_offered_exact(
            ev, TRANSIT_FACT_TOOL_PROFILE, scenario_id
        )
        assert ev.offered_surfaces[1] == TRANSIT_READY_PROFILE, scenario_id
        assert "check_transit" in ev.offered, scenario_id
        assert ev.emitted == ("declare_goals", "check_transit", "present_transit"), scenario_id
        assert ev.provider_execution_count == 2, scenario_id
        ends = {
            tool: (ok, summary)
            for tool, ok, summary, _call_id in ev.tool_ends
        }
        assert ends["check_transit"][0], scenario_id
        present_attempts = [
            attempt
            for attempt in ev.capability_attempts
            if attempt["capability"] == "present_transit"
        ]
        assert present_attempts, scenario_id
        assert present_attempts[0]["ok"], scenario_id
        assert ev.spies["poi_seam"].await_count == 0, f"{scenario_id}: poi seam must stay untouched"

    async def test_06a_offered_check_transit_control_auto(self):
        await self._case6a("auto", "F1-06a")

    async def _case6c(self, mode: str, scenario_id: str) -> None:
        # Offered route tool control: executes exactly once and commits.
        leg = make_leg(route_ids=("Q",), destination="Coney Island")
        seams = {
            "prepare_seam": (PREPARE_SEAM, AsyncMock(return_value=leg))
        }
        prepare_input = dict(PREPARE_ROUTE_OPTIONS_INPUT)
        prepare_input["goal_key"] = "route"
        rounds = [
            {
                "tool_use": [
                    {
                        "id": "tu-6c-goals",
                        "name": "declare_goals",
                        "input": dict(ROUTE_GOALS_INPUT),
                    },
                    {
                        "id": "tu-6c",
                        "name": "prepare_route_options",
                        "input": prepare_input,
                    },
                ],
                "stop_reason": "tool_use",
            },
            _turn_round(
                "present_route",
                "tu-6c-done",
                {
                    "goal_key": "route",
                    "candidate_id": FIXED_CANDIDATE_ID,
                    **PRESENT_ROUTE_FRAMING_INPUT,
                },
            ),
        ]
        with patch(
            "app.services.agent.candidate_store.new_candidate_id",
            return_value=FIXED_CANDIDATE_ID,
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
        assert ev.offered_surfaces[1] == ROUTE_READY_PROFILE, scenario_id
        assert "prepare_route_options" in ev.offered, scenario_id
        assert ev.emitted == ("declare_goals", "prepare_route_options", "present_route"), scenario_id
        assert ev.spies["prepare_seam"].await_count == 1, f"{scenario_id}: offered prepare must execute exactly once"
        assert ev.provider_execution_count == 2, scenario_id
        assert ev.state_after["trip_state"]["destination"] == "Coney Island", scenario_id
        assert len(ev.stored_candidate_set_ids) == 1, scenario_id

    async def test_06c_offered_prepare_route_options_control_auto(self):
        await self._case6c("auto", "F1-06c")

    async def _case7(self, mode: str, scenario_id: str) -> None:
        # Duplicate offered calls: ledger dedup, not the surface guard.
        leg = make_leg(route_ids=("Q",), destination="Coney Island")
        seams = {
            "prepare_seam": (PREPARE_SEAM, AsyncMock(return_value=leg))
        }
        prepare_input = dict(PREPARE_ROUTE_OPTIONS_INPUT)
        prepare_input["goal_key"] = "route"
        rounds = [
            {
                "tool_use": [
                    {
                        "id": "tu-7-goals",
                        "name": "declare_goals",
                        "input": dict(ROUTE_GOALS_INPUT),
                    },
                    {
                        "id": "tu-7a",
                        "name": "prepare_route_options",
                        "input": dict(prepare_input),
                    },
                    {
                        "id": "tu-7b",
                        "name": "prepare_route_options",
                        "input": dict(prepare_input),
                    },
                ],
                "stop_reason": "tool_use",
            },
            _turn_round(
                "present_route",
                "tu-7-done",
                {
                    "goal_key": "route",
                    "candidate_id": FIXED_CANDIDATE_ID,
                    **PRESENT_ROUTE_FRAMING_INPUT,
                },
            ),
        ]
        with patch(
            "app.services.agent.candidate_store.new_candidate_id",
            return_value=FIXED_CANDIDATE_ID,
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
        assert ev.offered_surfaces[1] == ROUTE_READY_PROFILE, scenario_id
        assert "prepare_route_options" in ev.offered, scenario_id
        assert len(ev.emitted) == 4, scenario_id
        assert set(ev.emitted) == {"declare_goals", "prepare_route_options", "present_route"}, scenario_id
        assert ev.spies["prepare_seam"].await_count == 1, f"{scenario_id}: identical duplicates run once via the ledger"
        assert ev.provider_execution_count == 2, scenario_id
        assert len(ev.tool_starts) == 1, f"{scenario_id}: only the real route preparation is rider-visible"
        assert len(ev.tool_ends) == 2, f"{scenario_id}: duplicate prepare calls share one execution and " "the internal presenter stays hidden"
        assert len(ev.stored_candidate_set_ids) == 1, scenario_id

    async def test_07a_duplicate_offered_tool_ledger_auto(self):
        await self._case7("auto", "F1-07a")


__all__ = ()
