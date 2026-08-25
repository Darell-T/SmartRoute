"""Batch J1: failure-driven audit matrix through the real conversational loop.

AUDIT-ONLY: no production patch. Every scenario runs the actual
``app.services.agent.loop`` with the actual registered ``TOOL_REGISTRY``
executors, stores, and SSE projection; only narrow provider/data seams are
scripted (``tests.conversation.conversation_failure_matrix_fixtures``) and Anthropic
inference is deterministic mock text (``tests/_fake_anthropic``), labeled as
mock prose -- never a claim of model linguistic accuracy.

Matrix (23 tests):

- J1-RF-01..09: route-preparation failures in fresh and accepted replan
  contexts -- timeout-shaped, exception-shaped, empty aggregate, nonfatal
  no-route, malformed/unusable, Auto+Quick where applicable. No stale
  candidate/card/selection; accepted trip preserved as one unit; bounded
  pending/fallback/retry.
- J1-RF-10..11: fresh valid control (prepare + present) proving the failure
  tests are not trivially green.
- J1-IA-01..05: route succeeds with incident stale/unavailable evidence and
  accessibility unknown/unavailable, with/without the hard requirement:
  exact status/coverage, no false all-clear, no hard-constraint winner/card,
  presentation only when allowed, zero broad request-time X/Web tools.
- J1-SA-01..06: representative status/arrival/discovery timeout/exception/
  empty/stale failures: no route/destination/card mutation, accepted trip
  preserved, bounded events, no browser/route fallback.
- J1-SC-01: distinct failure-driven neighbor of the Batch B scenario seams:
  a failed what-if prepare never binds temporary scenario state.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

from app.services.agent import trip_state as trip_state_module

from tests.conversation.conversation_failure_matrix_fixtures import (
    EXACT_INCIDENT_STALE_COVERAGE,
    EXACT_INCIDENT_UNAVAILABLE_COVERAGE,
    VALID_COVERAGE,
    FakeSubwayGtfs,
    accessibility_unavailable_leg,
    empty_aggregate_leg,
    empty_poi_result,
    exception_prepare_seam,
    goal_completion_round,
    goal_declaration_round,
    incident_stale_leg,
    malformed_prepare_result,
    no_route_result,
    stale_subway_feed_bytes,
    timeout_prepare_seam,
    valid_leg,
)
from tests.conversation.conversation_failure_matrix_support import (
    _FailureMatrixBase,
    prepare_mock_returning,
    prepare_mock_with_side_effect,
)
from tests.conversation.conversation_matrix_harness import (
    _turn_round,
    check_transit_input,
    discover_search_input,
    load_agent_loop,
    new_session,
)


class RouteFailureFreshTests(_FailureMatrixBase):
    """J1-RF-01, 04, 06, 07, 08: prepare failures in a fresh context."""

    @classmethod
    def setUpClass(cls):
        cls.loop = load_agent_loop()

    async def test_rf01_fresh_timeout_prepare_auto(self):
        await self._run_failure_asserted(
            mode="auto", message="Change the route",
            prepare_mock=prepare_mock_with_side_effect(timeout_prepare_seam()),
            expected_error="timed out",
        )

    async def test_rf04_fresh_exception_prepare_auto(self):
        await self._run_failure_asserted(
            mode="auto", message="Change the route",
            prepare_mock=prepare_mock_with_side_effect(exception_prepare_seam()),
            expected_error="tool failed",
        )

    async def test_rf06_fresh_empty_aggregate_auto(self):
        await self._run_failure_asserted(
            mode="auto", message="Change the route",
            prepare_mock=prepare_mock_returning(empty_aggregate_leg()),
            audit_status="insufficient_coverage",
        )

    async def test_rf07_fresh_nonfatal_no_route_auto(self):
        await self._run_failure_asserted(
            mode="auto", message="Change the route",
            prepare_mock=prepare_mock_returning(no_route_result()),
            audit_status="insufficient_coverage",
            audit_evidence={"routes": "unavailable"},
        )

    async def test_rf08_fresh_malformed_prepare_auto(self):
        await self._run_failure_asserted(
            mode="auto", message="Change the route",
            prepare_mock=prepare_mock_returning(malformed_prepare_result()),
            expected_error="provider returned malformed route data",
        )


class RouteFailureAcceptedTests(_FailureMatrixBase):
    """J1-RF-02, 03, 05, 09: prepare failures preserve the accepted trip."""

    @classmethod
    def setUpClass(cls):
        cls.loop = load_agent_loop()

    async def test_rf02_accepted_timeout_prepare_auto(self):
        await self._run_failure_asserted(
            mode="auto", message="Change the route", accepted=True,
            prepare_mock=prepare_mock_with_side_effect(timeout_prepare_seam()),
            expected_error="timed out",
        )

    async def test_rf03_accepted_timeout_prepare_quick(self):
        await self._run_failure_asserted(
            mode="quick", message="Change the route", accepted=True,
            prepare_mock=prepare_mock_with_side_effect(timeout_prepare_seam()),
            expected_error="timed out",
        )

    async def test_rf05_accepted_exception_prepare_auto(self):
        await self._run_failure_asserted(
            mode="auto", message="Change the route", accepted=True,
            prepare_mock=prepare_mock_with_side_effect(exception_prepare_seam()),
            expected_error="tool failed",
        )

    async def test_rf09_accepted_malformed_prepare_auto(self):
        await self._run_failure_asserted(
            mode="auto", message="Change the route", accepted=True,
            prepare_mock=prepare_mock_returning(malformed_prepare_result()),
            expected_error="provider returned malformed route data",
        )


class RouteFailureValidControlTests(_FailureMatrixBase):
    """J1-RF-10..11: fresh valid prepare + present emits exactly one card."""

    FIXED_CANDIDATE_ID = "cd_j1_valid_control"

    @classmethod
    def setUpClass(cls):
        cls.loop = load_agent_loop()

    async def _assert_valid_control(self, *, mode):
        session_id = "sess-j1-rfv"
        _sid, session = new_session()
        events, trace, mocks = await self._run_prepare_present(
            session=session, session_id=session_id, mode=mode,
            message="Plan a trip to Work", prepare_leg=valid_leg(),
            candidate_id=self.FIXED_CANDIDATE_ID,
        )
        record = self._assert_presented_contract(
            events=events, trace=trace, mocks=mocks, session=session,
            session_id=session_id, mode=mode,
            candidate_id=self.FIXED_CANDIDATE_ID,
            expected_status="good", expected_coverage=VALID_COVERAGE,
        )
        self.assertEqual(record["route_status"], "good")
        if mode == "quick":
            prepare_input = next(
                tool_input
                for name, tool_input in trace.tool_calls
                if name == "prepare_route_options"
            )
            self.assertIs(
                prepare_input.get("include_first_leg_arrivals"),
                False,
            )

    async def test_rf10_fresh_valid_control_auto(self):
        await self._assert_valid_control(mode="auto")

    async def test_rf11_fresh_valid_control_quick(self):
        await self._assert_valid_control(mode="quick")


class IncidentAccessibilityTests(_FailureMatrixBase):
    """J1-IA-01..05: degraded evidence and accessibility constraints."""

    FIXED_CANDIDATE_ID = "cd_j1_ia"

    @classmethod
    def setUpClass(cls):
        cls.loop = load_agent_loop()

    async def _run_presentable_asserted(
        self, *, mode, prepare_leg, expected_coverage,
    ):
        session, session_id, seed = self._seed_accepted()
        events, trace, mocks = await self._run_prepare_present(
            session=session, session_id=session_id, mode=mode,
            message="Change the route", prepare_leg=prepare_leg,
            candidate_id=self.FIXED_CANDIDATE_ID, destination=seed.destination,
        )
        return (
            session, session_id, seed, events, trace, mocks,
            self._assert_presented_contract(
                events=events, trace=trace, mocks=mocks, session=session,
                session_id=session_id, mode=mode,
                candidate_id=self.FIXED_CANDIDATE_ID,
                expected_status="degraded_usable",
                expected_coverage=expected_coverage,
                seed=seed,
            ),
        )

    async def test_ia01_incident_stale_present_auto(self):
        await self._run_presentable_asserted(
            mode="auto", prepare_leg=incident_stale_leg(),
            expected_coverage=EXACT_INCIDENT_STALE_COVERAGE,
        )

    async def test_ia03_incident_unavailable_accessibility_unavailable_present_auto(self):
        session, session_id, seed, events, trace, mocks, record = (
            await self._run_presentable_asserted(
                mode="auto", prepare_leg=accessibility_unavailable_leg(),
                expected_coverage=EXACT_INCIDENT_UNAVAILABLE_COVERAGE,
            )
        )
        # Provider-reported "unavailable" accessibility normalizes to
        # "unknown" -- the digest must never claim accessible.
        self.assertEqual(
            record["candidates"][0]["digest"]["accessibility_status"],
            "unknown",
        )

    async def test_ia05_incident_stale_present_quick(self):
        await self._run_presentable_asserted(
            mode="quick", prepare_leg=incident_stale_leg(),
            expected_coverage=EXACT_INCIDENT_STALE_COVERAGE,
        )

    async def test_ia02_incident_stale_accessibility_required_auto(self):
        await self._run_no_match_asserted(
            prepare_leg=incident_stale_leg(),
        )

    async def test_ia04_incident_unavailable_accessibility_required_auto(self):
        await self._run_no_match_asserted(
            prepare_leg=accessibility_unavailable_leg(),
        )

    async def _run_no_match_asserted(self, *, prepare_leg):
        session, session_id, seed = self._seed_accepted()
        events, trace, mocks = await self._run_prepare_failure(
            session=session, session_id=session_id, mode="auto",
            message="Change the route",
            prepare_mock=prepare_mock_returning(prepare_leg),
            destination=seed.destination,
            extra_input={"accessibility_required": True},
            text="I could not find a route that meets your accessibility requirement.",
        )
        self._assert_no_match_contract(
            events=events, trace=trace, mocks=mocks, session=session,
            session_id=session_id, seed=seed,
        )


class StatusArrivalDiscoveryFailureTests(_FailureMatrixBase):
    """J1-SA-01..06: status/arrival/discovery provider failures."""

    @classmethod
    def setUpClass(cls):
        cls.loop = load_agent_loop()

    async def test_sa01_status_timeout_auto(self):
        session, session_id, seed = self._seed_accepted()
        events, trace, _mocks = await self._run_seam_turn(
            session=session, session_id=session_id,
            message="Are there delays on the uptown Q train?",
            rounds=[
                goal_declaration_round("transit", "service_status"),
                _turn_round(
                    "check_transit",
                    "tu_1",
                    {
                        **check_transit_input(
                            "service_status",
                            route_ids=["Q"],
                            direction="uptown",
                        ),
                        "goal_key": "transit",
                    },
                ),
                goal_completion_round(
                    "transit",
                    "Live service alerts are temporarily unavailable.",
                    outcome="unavailable",
                    tool_id="tu_2",
                ),
            ],
            seam_mocks={
                "app.services.mta.realtime.fetch_service_alerts": AsyncMock(
                    side_effect=asyncio.TimeoutError("simulated provider timeout")
                )
            },
        )
        self._assert_seam_turn(
            events=events, trace=trace, session=session,
            session_id=session_id, seed=seed, expected_tool="check_transit",
            tool_end_ok=False,
            summary_contains="Transit information could not be checked",
            text_contains="unavailable",
        )

    async def test_sa02_status_empty_auto(self):
        session, session_id, seed = self._seed_accepted()
        events, trace, _mocks = await self._run_seam_turn(
            session=session, session_id=session_id,
            message="Are there delays on the uptown Q train?",
            rounds=[
                goal_declaration_round("transit", "service_status"),
                _turn_round(
                    "check_transit",
                    "tu_1",
                    {
                        **check_transit_input(
                            "service_status",
                            route_ids=["Q"],
                            direction="uptown",
                        ),
                        "goal_key": "transit",
                    },
                ),
                _turn_round(
                    "present_transit",
                    "tu_2",
                    {
                        "goal_key": "transit",
                        "evidence_set_id": "te_failure_matrix",
                    },
                ),
            ],
            seam_mocks={
                "app.services.mta.realtime.fetch_service_alerts": AsyncMock(
                    return_value=b"empty-alert-feed"
                ),
                "app.services.mta.realtime.parse_service_alerts": MagicMock(
                    return_value=[]
                ),
            },
        )
        self._assert_seam_turn(
            events=events, trace=trace, session=session,
            session_id=session_id, seed=seed, expected_tool="check_transit",
            tool_end_ok=True,
            terminal_tool="present_transit",
        )

    async def test_sa03_arrival_exception_auto(self):
        session, session_id, seed = self._seed_accepted()
        events, trace, _mocks = await self._run_seam_turn(
            session=session, session_id=session_id,
            message="When is the next uptown Q train at Newkirk?",
            rounds=[
                goal_declaration_round("transit", "arrivals"),
                _turn_round(
                    "check_transit",
                    "tu_1",
                    {
                        **check_transit_input(
                            "arrivals",
                            route_ids=["Q"],
                            stop_query="Newkirk",
                            direction="uptown",
                        ),
                        "goal_key": "transit",
                    },
                ),
                goal_completion_round(
                    "transit",
                    "Current Q arrival information is unavailable.",
                    outcome="unavailable",
                    tool_id="tu_2",
                ),
            ],
            gtfs=FakeSubwayGtfs(),
            seam_mocks={
                "app.services.mta.realtime.fetch_feeds_with_metadata": AsyncMock(
                    side_effect=RuntimeError("simulated provider exception")
                )
            },
        )
        self._assert_seam_turn(
            events=events, trace=trace, session=session,
            session_id=session_id, seed=seed, expected_tool="check_transit",
            tool_end_ok=False, model_calls=3, text_contains="unavailable",
        )
        self.assertEqual(
            [event for event in events if event.type == "arrival_card"],
            [],
            "a provider exception must not fabricate an arrival card",
        )

    async def test_sa04_arrival_stale_auto(self):
        session, session_id, seed = self._seed_accepted()
        events, trace, _mocks = await self._run_seam_turn(
            session=session, session_id=session_id,
            message="When is the next uptown Q train at Newkirk?",
            rounds=[
                goal_declaration_round("transit", "arrivals"),
                _turn_round(
                    "check_transit",
                    "tu_1",
                    {
                        **check_transit_input(
                            "arrivals",
                            route_ids=["Q"],
                            stop_query="Newkirk",
                            direction="uptown",
                        ),
                        "goal_key": "transit",
                    },
                ),
                _turn_round(
                    "present_transit",
                    "tu_2",
                    {
                        "goal_key": "transit",
                        "evidence_set_id": "te_failure_matrix",
                    },
                ),
            ],
            gtfs=FakeSubwayGtfs(),
            seam_mocks={
                "app.services.mta.realtime.fetch_feeds_with_metadata": AsyncMock(
                    return_value=[
                        {"content": stale_subway_feed_bytes(), "source": "feed-0"}
                    ]
                )
            },
        )
        self._assert_seam_turn(
            events=events, trace=trace, session=session,
            session_id=session_id, seed=seed, expected_tool="check_transit",
            arrival_card_status="stale", model_calls=3,
            terminal_tool="present_transit",
        )
        self.assertNotRegex(trace.final_text, r"\bin \d+ minutes?\b")

    async def test_sa05_discovery_timeout_auto(self):
        session, session_id, seed = self._seed_accepted()
        events, trace, _mocks = await self._run_seam_turn(
            session=session, session_id=session_id,
            message="Find a good pizza place",
            rounds=[
                goal_declaration_round("discovery", "place_recommendation"),
                _turn_round(
                    "discover_places",
                    "tu_1",
                    {
                        **discover_search_input("pizza", borough="Brooklyn"),
                        "goal_key": "discovery",
                    },
                ),
                goal_completion_round(
                    "discovery",
                    "I could not search for places right now.",
                    outcome="unavailable",
                    tool_id="tu_2",
                ),
            ],
            seam_mocks={
                "app.services.agent.tools.places.search_local_places.execute": AsyncMock(
                    side_effect=asyncio.TimeoutError("simulated provider timeout")
                )
            },
        )
        self._assert_seam_turn(
            events=events, trace=trace, session=session,
            session_id=session_id, seed=seed,
            expected_tool="discover_places",
            tool_end_ok=False,
            summary_contains="Place search could not be completed",
        )

    async def test_sa06_discovery_empty_auto(self):
        session, session_id, seed = self._seed_accepted()
        events, trace, _mocks = await self._run_seam_turn(
            session=session, session_id=session_id,
            message="Find a good pizza place",
            rounds=[
                goal_declaration_round("discovery", "place_recommendation"),
                _turn_round(
                    "discover_places",
                    "tu_1",
                    {
                        **discover_search_input("pizza", borough="Brooklyn"),
                        "goal_key": "discovery",
                    },
                ),
                goal_completion_round(
                    "discovery",
                    "I could not find any matching places.",
                    outcome="unavailable",
                    tool_id="tu_2",
                ),
            ],
            seam_mocks={
                "app.services.agent.tools.places.search_local_places.execute": AsyncMock(
                    return_value=empty_poi_result()
                )
            },
        )
        self._assert_seam_turn(
            events=events, trace=trace, session=session,
            session_id=session_id, seed=seed,
            expected_tool="discover_places",
            tool_end_ok=True,
        )


class ScenarioFailureNeighborTests(_FailureMatrixBase):
    """J1-SC-01: a failed what-if prepare binds no temporary scenario state."""

    @classmethod
    def setUpClass(cls):
        cls.loop = load_agent_loop()

    async def test_sc01_what_if_prepare_exception_auto(self):
        session, session_id, seed, events, trace, mocks = (
            await self._run_failure_asserted(
                mode="auto", message="What if I leave 30 minutes later?",
                accepted=True,
                prepare_mock=prepare_mock_with_side_effect(exception_prepare_seam()),
                extra_input={
                    "departure_time": "2026-08-06T12:30:00-04:00",
                    "what_if": True,
                },
                expected_error="tool failed",
            )
        )
        # The model declares the what-if semantics; the backend enforces
        # isolation and leaves no temporary binding when preparation fails.
        prepare_input = next(
            tool_input
            for name, tool_input in trace.tool_calls
            if name == "prepare_route_options"
        )
        self.assertIs(prepare_input.get("what_if"), True)
        state = trip_state_module.get_trip_state(session)
        self.assertIsNone(state["temporary_candidate_set_id"])
        self.assertIsNone(state["temporary_selected_candidate_id"])
        self.assertIsNone(state["temporary_base_candidate_set_id"])
