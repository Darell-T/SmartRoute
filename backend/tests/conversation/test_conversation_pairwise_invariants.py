"""Batch I audit: pairwise invariants and metamorphic properties.

AUDIT-ONLY batch: every scenario drives the real agent loop with production
state-scoped tool filtering, real registry/executors, real candidate /
discovery / trip / session stores, ledger, and SSE events. Only
deterministic Anthropic rounds and the documented provider/data seams are
scripted; legacy ``plan_trip`` is never used and no fake executor replaces a
canonical route tool. Scenario methods live here; reusable invariants live
in ``tests.conversation.conversation_pairwise_support``. Batch I reports findings only
and never patches production.

Coverage (see ``SCENARIO_ROWS``): I-01 status/explanation non-mutation across
fresh/accepted/temporary/discovery/stale contexts; I-02 (Auto + Quick)
one-present/one-card plus duplicate, wrong-session, invented, and model-prose
authority neighbors; I-03 no-good with preserved accepted selection plus one
valid-presentation control; I-04 what-if isolation (time / bus / exclusion /
preference / destination, accept or reject, status inside a live preview);
I-05 discovery canonicalization (named and ordinal references, latest set,
expired safe failure, label-only never a destination); I-06 wording
equivalence plus a parser-boundary row; I-07 a consistency walk over
``SCENARIO_ROWS``. Auto runs all rows; I-02 also runs Quick.
"""

from __future__ import annotations

import sys

from app.services.agent import trip_state as trip_state_module
from app.services.agent.public_surface import INITIAL_TOOL_NAMES, schemas_for_state
from tests.conversation.conversation_pairwise_fixtures import (
    ALREADY_PRESENTED_MARKER, ALT_DESTINATION_MESSAGE, BASE_ROUTE_MESSAGE,
    AMBIGUOUS_LINE_STATUS_MESSAGE,
    BUS_WHAT_IF_MESSAGE, CANDIDATE_I2, CANDIDATE_I2_V2, CANDIDATE_I4_PREVIEW,
    CANDIDATE_I5_ROUTE, CANDIDATE_I6_ROUTE, CANDIDATE_SESSION_B,
    CANDIDATE_UNKNOWN_MARKER, CHANGE_ROUTE_MESSAGE, DISCOVERY_WORDING_VARIANTS,
    EXCLUSION_ROUTE_MESSAGE, EXPIRED_SET_MARKER, EXPLANATION_MESSAGES,
    FEWER_TRANSFERS_MESSAGE, INVENTED_CANDIDATE_ID, LINE_STATUS_MESSAGES,
    MODEL_PROSE_CANDIDATE_ID, NO_GOOD_VARIANTS, PARSER_BOUNDARY_MESSAGE,
    ROUTE_NAVIGATION_TOOL_PROFILE, ROUTE_WORDING_VARIANTS, SCENARIO_ROWS,
    STATUS_INSIDE_PREVIEW_MESSAGE, STATUS_WORDING_VARIANTS,
    STATION_STATUS_MESSAGES, TEMPORAL_DEPARTURE, TEMPORAL_WHAT_IF_MESSAGE,
    VALID_ROUTE_MESSAGE, WHAT_IF_EXCLUSION_VARIANTS, bus_leg, coffee_poi_result,
    coney_island_leg, grand_central_leg, no_good_rounds, r_leg,
)
from tests.conversation.conversation_pairwise_support import (
    _PairwiseBase, capture_temporary_candidate,
)
from tests.conversation.conversation_matrix_harness import (
    _turn_round, complete_turn_round, load_agent_loop, text_round,
)

MODES = ("auto", "quick")


class _LoopTestCase(_PairwiseBase):
    """One real loop per class tree (mirrors accepted Batch A..E modules)."""

    @classmethod
    def setUpClass(cls):
        cls.loop = load_agent_loop()

    async def _status(self, scenario_id, message, session=None,
                      session_id=None, clock=None):
        if session is None:
            session_id, session = self._new_session("auto")
        await self._status_turn(
            mode="auto", scenario_id=scenario_id, session=session,
            session_id=session_id, message=message,
            before=self._projection(session), clock=clock)


class StatusExplanationNonMutationTests(_LoopTestCase):
    """I-01-A..E: status/explanation turns never mutate server state."""

    async def test_i01_status_explanation_fresh(self):
        await self._status("I-01-A", LINE_STATUS_MESSAGES[0])

    async def test_i01_route_status_without_direction_runs_linewide(self):
        session_id, session = self._new_session("auto")
        await self._status_turn(
            mode="auto",
            scenario_id="I-01-A-linewide-status",
            session=session,
            session_id=session_id,
            message=AMBIGUOUS_LINE_STATUS_MESSAGE,
            before=self._projection(session),
            direction=None,
            expect_clarification=False,
        )

    async def test_i01_status_explanation_accepted_trip(self):
        session, session_id, _seed = self._seed_accepted("auto")
        await self._status("I-01-B", EXPLANATION_MESSAGES[0],
                           session, session_id)

    async def test_i01_status_explanation_temporary_what_if(self):
        session, session_id, _seed = await self._seed_temporary(
            mode="auto", scenario_id="I-01-C",
            message=WHAT_IF_EXCLUSION_VARIANTS[0],
            prepare_input={"destination": "Work"},
            prepare_leg=r_leg("Work"), candidate_id=CANDIDATE_I4_PREVIEW)
        await self._status("I-01-C", EXPLANATION_MESSAGES[1],
                           session, session_id)

    async def test_i01_status_explanation_active_discovery(self):
        session, session_id, _set_id, _record = await self._fresh_discovery(
            mode="auto", scenario_id="I-01-D",
            message=DISCOVERY_WORDING_VARIANTS[0])
        await self._status("I-01-D", STATION_STATUS_MESSAGES[0],
                           session, session_id)

    async def test_i01_status_explanation_expired_reference(self):
        session, session_id, _set_id, record = await self._fresh_discovery(
            mode="auto", scenario_id="I-01-E",
            message=DISCOVERY_WORDING_VARIANTS[0])
        await self._status("I-01-E", LINE_STATUS_MESSAGES[1],
                           session, session_id,
                           clock=self._expired_clock(record))


class PresentAuthorityTests(_LoopTestCase):
    """I-02-A..D (Auto + Quick): one present -> one card; invalid/duplicate
    reference neighbors are rejected before mutation; model prose never
    authors a present."""

    async def _rejected_present_turn(self, *, mode, scenario_id, session,
                                     session_id, message, candidate_id, marker,
                                     before, turn_id="t2"):
        rounds = [_turn_round("present_route", f"tu-{turn_id}",
                              {"candidate_id": candidate_id}),
                  complete_turn_round(
                      f"tu-{turn_id}-done",
                      "I will keep the current route.",
                      outcome="cancelled",
                  )]
        events, trace, mocks = await self._route_turn(
            mode=mode, scenario_id=scenario_id, session=session,
            session_id=session_id, message=message, rounds=rounds,
            turn_id=turn_id)
        self._assert_turn_contract(
            scenario_id=scenario_id, events=events, trace=trace, mocks=mocks,
            mode=mode, expected_tools=("complete_turn",),
            expected_profile=ROUTE_NAVIGATION_TOOL_PROFILE, expect_card=0,
            expect_stored=0)
        attempts = [
            attempt
            for attempt in trace.capability_attempts
            if attempt["capability"] == "present_route"
        ]
        self.assertTrue(attempts and attempts[0]["ok"] is False, scenario_id)
        self.assertNotIn(marker, trace.final_text, scenario_id)
        self._assert_projection_unchanged(
            before, self._projection(session), scenario_id)

    async def test_i02_single_present_single_card(self):
        for mode in MODES:
            with self.subTest(mode=mode):
                await self._one_present(
                    mode=mode, scenario_id=f"I-02-A-{mode}",
                    message=VALID_ROUTE_MESSAGE, destination="Work",
                    candidate_id=CANDIDATE_I2, prepare_leg=r_leg("Work"))

    async def test_i02_cross_round_duplicate_present_rejected(self):
        for mode in MODES:
            with self.subTest(mode=mode):
                session, session_id = await self._one_present(
                    mode=mode, scenario_id=f"I-02-B-{mode}-t1",
                    message=VALID_ROUTE_MESSAGE, destination="Work",
                    candidate_id=CANDIDATE_I2, prepare_leg=r_leg("Work"))
                await self._rejected_present_turn(
                    mode=mode, scenario_id=f"I-02-B-{mode}", session=session,
                    session_id=session_id, message=CHANGE_ROUTE_MESSAGE,
                    candidate_id=CANDIDATE_I2, marker=ALREADY_PRESENTED_MARKER,
                    before=self._projection(session), turn_id="t2")

    async def test_i02_wrong_session_and_invented_present_rejected(self):
        for mode in MODES:
            with self.subTest(mode=mode):
                session, session_id, _seed = self._seed_accepted(mode)
                sid_b, session_b = self._new_session(mode)
                await self._one_present(
                    mode=mode, scenario_id=f"I-02-C-{mode}-b",
                    message=VALID_ROUTE_MESSAGE, destination="Work",
                    candidate_id=CANDIDATE_SESSION_B, prepare_leg=r_leg("Work"),
                    session=session_b, session_id=sid_b)
                for label, candidate_id in (
                    ("wrong-session", CANDIDATE_SESSION_B),
                    ("invented", INVENTED_CANDIDATE_ID),
                ):
                    with self.subTest(case=label):
                        await self._rejected_present_turn(
                            mode=mode, scenario_id=f"I-02-C-{mode}-{label}",
                            session=session, session_id=session_id,
                            message=CHANGE_ROUTE_MESSAGE,
                            candidate_id=candidate_id,
                            marker=CANDIDATE_UNKNOWN_MARKER,
                            before=self._projection(session), turn_id="t2")

    async def test_i02_model_candidate_text_never_authority(self):
        for mode in MODES:
            with self.subTest(mode=mode):
                session_id, session = self._new_session(mode)
                rounds = [_turn_round("prepare_route_options", "tu-prep",
                                      {"destination": "Work"}),
                          text_round("The second option sounds good.")]
                events, trace, mocks = await self._route_turn(
                    mode=mode, scenario_id=f"I-02-D-{mode}", session=session,
                    session_id=session_id, message=VALID_ROUTE_MESSAGE,
                    rounds=rounds, prepare_leg=r_leg("Work"),
                    fixed_candidate_id=CANDIDATE_I2)
                self._assert_turn_contract(
                    scenario_id=f"I-02-D-{mode}", events=events, trace=trace,
                    mocks=mocks, mode=mode,
                    expected_tools=("prepare_route_options",),
                    expected_profile=ROUTE_NAVIGATION_TOOL_PROFILE,
                    expect_card=0, expect_stored=1, model_calls=3)
                state = trip_state_module.get_trip_state(session)
                self.assertEqual(
                    (state["active_candidate_set_id"] is not None,
                     state["selected_candidate_id"],
                     MODEL_PROSE_CANDIDATE_ID in trace.final_text),
                    (True, None, False),
                    f"I-02-D-{mode} prose never commits or surfaces an id")


class NoGoodPreservedTests(_LoopTestCase):
    """I-03-A..E: non-presentable replans preserve the accepted selection as
    one bound unit; I-03-E is the valid-presentation control."""

    async def test_i03_no_good_preserves_accepted_context(self):
        for scenario_id, message, leg, violations, prepare_input, slots, \
                digest_status, extra, expected_preferences in NO_GOOD_VARIANTS:
            with self.subTest(row=scenario_id):
                session, session_id, seed = self._seed_accepted("auto")
                state_before = trip_state_module.get_trip_state(session)
                events, trace, mocks = await self._route_turn(
                    mode="auto", scenario_id=scenario_id, session=session,
                    session_id=session_id, message=message,
                    rounds=no_good_rounds(extra), prepare_leg=leg)
                audit = self._assert_no_good_audit(
                    scenario_id=scenario_id, events=events, trace=trace,
                    mocks=mocks, session=session, session_id=session_id,
                    mode="auto", seed=seed, state_before=state_before,
                    expected_violations=violations,
                    expected_prepare_input=prepare_input,
                    expected_slots=slots, expected_preferences=expected_preferences)
                if digest_status is not None:
                    self.assertEqual(
                        audit["candidates"][0]["digest"]["accessibility_status"],
                        digest_status, f"{scenario_id} accessibility status")

    async def test_i03_control_valid_prepare_presents_one_card(self):
        await self._one_present(
            mode="auto", scenario_id="I-03-E", message=VALID_ROUTE_MESSAGE,
            destination="Work", candidate_id=CANDIDATE_I2,
            prepare_leg=r_leg("Work"))


class WhatIfIsolationTests(_LoopTestCase):
    """I-04-A..G: what-if isolation across time / bus / route exclusion /
    preference / destination with preview -> accept or reject, plus a status
    turn inside a live preview."""

    async def test_i04_route_exclusion_what_if_accept(self):
        session, session_id, _seed = await self._seed_temporary(
            mode="auto", scenario_id="I-04-A",
            message=WHAT_IF_EXCLUSION_VARIANTS[0],
            prepare_input={"destination": "Work", "excluded_route_ids": ["Q"]},
            prepare_leg=r_leg("Work"), candidate_id=CANDIDATE_I4_PREVIEW)
        await self._accept_turn(
            mode="auto", scenario_id="I-04-A", session=session,
            session_id=session_id, candidate_id=CANDIDATE_I4_PREVIEW)
        state = trip_state_module.get_trip_state(session)
        self.assertEqual(state["selected_candidate_id"], CANDIDATE_I4_PREVIEW,
                         "I-04-A commit binds the preview selection")
        self._assert_temporary_clear("I-04-A", state)

    async def test_i04_route_exclusion_what_if_reject(self):
        await self._preview_reject(
            mode="auto", scenario_id="I-04-B",
            message=WHAT_IF_EXCLUSION_VARIANTS[1],
            prepare_input={"destination": "Work", "excluded_route_ids": ["Q"]},
            prepare_leg=r_leg("Work"), candidate_id=CANDIDATE_I4_PREVIEW)

    async def test_i04_temporal_what_if_preview_reject(self):
        await self._preview_reject(
            mode="auto", scenario_id="I-04-C",
            message=TEMPORAL_WHAT_IF_MESSAGE,
            prepare_input={"destination": "Work",
                           "departure_time": TEMPORAL_DEPARTURE},
            prepare_leg=r_leg("Work"), candidate_id=CANDIDATE_I4_PREVIEW)

    async def test_i04_bus_what_if_preview_reject(self):
        await self._preview_reject(
            mode="auto", scenario_id="I-04-D", message=BUS_WHAT_IF_MESSAGE,
            prepare_input={"destination": "Work",
                           "preferred_modes": ["BUS"]},
            prepare_leg=bus_leg("Work"), candidate_id=CANDIDATE_I4_PREVIEW)

    async def test_i04_routing_what_if_preview_reject(self):
        await self._preview_reject(
            mode="auto", scenario_id="I-04-E",
            message=FEWER_TRANSFERS_MESSAGE,
            prepare_input={"destination": "Work"},
            prepare_leg=r_leg("Work"), candidate_id=CANDIDATE_I4_PREVIEW)

    async def test_i04_alternate_destination_what_if_reject(self):
        await self._preview_reject(
            mode="auto", scenario_id="I-04-F",
            message=ALT_DESTINATION_MESSAGE,
            prepare_input={"destination": "Coney Island"},
            prepare_leg=coney_island_leg("Coney Island"),
            candidate_id=CANDIDATE_I4_PREVIEW)

    async def test_i04_status_turn_inside_live_preview(self):
        session, session_id, seed = await self._seed_temporary(
            mode="auto", scenario_id="I-04-G",
            message=WHAT_IF_EXCLUSION_VARIANTS[0],
            prepare_input={"destination": "Work"},
            prepare_leg=r_leg("Work"), candidate_id=CANDIDATE_I4_PREVIEW)
        await self._status("I-04-G", STATUS_INSIDE_PREVIEW_MESSAGE,
                           session, session_id)
        state = trip_state_module.get_trip_state(session)
        self.assertIsNotNone(state["temporary_candidate_set_id"],
                             "I-04-G status turn preserves the live preview")
        set_id, candidate_id, _record = capture_temporary_candidate(
            session, session_id)
        await self._reject_turn(
            mode="auto", scenario_id="I-04-G", session=session,
            session_id=session_id, seed=seed,
            state_before=self._projection(session), preview_set_id=set_id,
            preview_candidate_id=candidate_id)


class DiscoveryCanonicalizationTests(_LoopTestCase):
    """I-05-A..D: discovery references resolve through the real store; the
    latest set wins; expired references fail safely; label-only text is never
    a routing destination."""

    async def _selection_transcript(self, *, mode, scenario_id, tool_input):
        session, session_id, set_id, record = await self._fresh_discovery(
            mode=mode, scenario_id=f"{scenario_id}-t1",
            message=DISCOVERY_WORDING_VARIANTS[0],
            poi_result=coffee_poi_result())
        place2 = record["places"][1]
        _e, _t, _m, state = await self._selection_turn(
            mode=mode, scenario_id=scenario_id, session=session,
            session_id=session_id, tool_input=tool_input,
            expected_place_id=place2["place_id"], turn_id="t2")
        self.assertEqual(state["selected_place_id"], place2["place_id"],
                         f"{scenario_id} stored identity bound, not label text")
        await self._route_after_selection(
            mode=mode, scenario_id=scenario_id, session=session,
            session_id=session_id, place2=place2,
            candidate_id=CANDIDATE_I5_ROUTE, set_id=set_id, turn_id="t3")

    async def test_i05_named_selection_canonical_control(self):
        await self._selection_transcript(
            mode="auto", scenario_id="I-05-A",
            tool_input={"description": "B Coffee"})

    async def test_i05_latest_set_supersedes(self):
        session, session_id, set1, _r1 = await self._fresh_discovery(
            mode="auto", scenario_id="I-05-B-t1",
            message=DISCOVERY_WORDING_VARIANTS[0],
            poi_result=coffee_poi_result())
        set2, record2 = await self._discovery_turn(
            mode="auto", scenario_id="I-05-B-t2", session=session,
            session_id=session_id, message=DISCOVERY_WORDING_VARIANTS[0],
            poi_result=coffee_poi_result(), turn_id="t2")
        self.assertNotEqual(set1, set2,
                            "I-05-B second search binds a fresh set")
        place2 = record2["places"][1]
        _e, _t, _m, state = await self._selection_turn(
            mode="auto", scenario_id="I-05-B", session=session,
            session_id=session_id, tool_input={"ordinal": 2},
            expected_place_id=place2["place_id"], turn_id="t3")
        self.assertEqual(state["active_discovery_set_id"], set2,
                         "I-05-B selection targets the latest set only")
        await self._route_after_selection(
            mode="auto", scenario_id="I-05-B", session=session,
            session_id=session_id, place2=place2,
            candidate_id=CANDIDATE_I5_ROUTE, set_id=set2, turn_id="t4")

    async def test_i05_expired_set_reference_fails_safely(self):
        session, session_id, _set_id, record = await self._fresh_discovery(
            mode="auto", scenario_id="I-05-C-t1",
            message=DISCOVERY_WORDING_VARIANTS[0])
        before = self._projection(session)
        await self._selection_turn(
            mode="auto", scenario_id="I-05-C", session=session,
            session_id=session_id, tool_input={"ordinal": 2},
            expected_place_id=None, turn_id="t2",
            clock=self._expired_clock(record), fail_marker=EXPIRED_SET_MARKER,
            before=before)

    async def test_i05_label_only_never_destination_authority(self):
        _session, _session_id, set_id, record = await self._fresh_discovery(
            mode="auto", scenario_id="I-05-D",
            message=DISCOVERY_WORDING_VARIANTS[0],
            poi_result=coffee_poi_result())
        self.assertTrue(set_id.startswith("ds_") and bool(record["places"]),
                        "I-05-D label text stores a server-owned set only")
        # _discovery_turn already proved search-only execution, no card, and
        # no candidate set: label text alone is never a routing destination.


class MetamorphicEquivalenceTests(_LoopTestCase):
    """I-06-A..F: wording-equivalence families and the parser-boundary row."""

    async def test_i06_route_wording_equivalence(self):
        for wording in ROUTE_WORDING_VARIANTS:
            with self.subTest(wording=wording):
                await self._one_present(
                    mode="auto", scenario_id="I-06-A", message=wording,
                    destination="Grand Central",
                    candidate_id=CANDIDATE_I6_ROUTE,
                    prepare_leg=grand_central_leg())

    async def test_i06_status_wording_equivalence(self):
        for wording in STATUS_WORDING_VARIANTS:
            with self.subTest(wording=wording):
                await self._status("I-06-B", wording)

    async def test_i06_discovery_wording_equivalence(self):
        for wording in DISCOVERY_WORDING_VARIANTS:
            with self.subTest(wording=wording):
                _session, _session_id, _set_id, _record = (
                    await self._fresh_discovery(
                        mode="auto", scenario_id="I-06-C", message=wording))

    async def test_i06_what_if_wording_equivalence(self):
        for wording in WHAT_IF_EXCLUSION_VARIANTS:
            with self.subTest(wording=wording):
                _session, _session_id, _seed = await self._seed_temporary(
                    mode="auto", scenario_id="I-06-D", message=wording,
                    prepare_input={"destination": "Work", "excluded_route_ids": ["Q"]},
                    prepare_leg=r_leg("Work"),
                    candidate_id=CANDIDATE_I4_PREVIEW,
                    expected_prepare_subset={"excluded_route_ids": ["Q"]})

    async def test_i06_semantic_change_appears_in_state(self):
        session, session_id = await self._one_present(
            mode="auto", scenario_id="I-06-E-t1", message=BASE_ROUTE_MESSAGE,
            destination="Grand Central", candidate_id=CANDIDATE_I6_ROUTE,
            prepare_leg=grand_central_leg())
        rounds = [_turn_round("prepare_route_options", "tu-prep",
                              {"destination": "Grand Central",
                               "excluded_route_ids": ["Q"]}),
                  _turn_round("present_route", "tu-pres",
                              {"candidate_id": CANDIDATE_I2_V2})]
        events, trace, mocks = await self._route_turn(
            mode="auto", scenario_id="I-06-E-t2", session=session,
            session_id=session_id, message=EXCLUSION_ROUTE_MESSAGE,
            rounds=rounds, prepare_leg=r_leg("Grand Central"),
            fixed_candidate_id=CANDIDATE_I2_V2)
        self.assertEqual(trace.tool_calls[0][1]["excluded_route_ids"], ["Q"],
                         "I-06-E exclusion in canonical prepare input")
        self.assertEqual(
            (session.get("slots") or {}).get("constraints", {}).get(
                "excluded_route_ids"),
            ["Q"], "I-06-E exclusion persists in constraint state")
        self._assert_single_present_single_card(
            scenario_id="I-06-E-t2", events=events, trace=trace, mocks=mocks,
            session=session, session_id=session_id, mode="auto",
            candidate_id=CANDIDATE_I2_V2, destination="Grand Central",
            expected_tools=("prepare_route_options", "present_route"))

    async def test_i06_parser_boundary_label_not_authority(self):
        await self._status("I-06-F", PARSER_BOUNDARY_MESSAGE)


class ScenarioTableConsistencyTests(_LoopTestCase):
    """I-07: each behavior row has a test and the stable initial surface."""

    def _defined_nodes(self) -> frozenset:
        module = sys.modules[__name__]
        return frozenset(name for _cls in vars(module).values()
                         if isinstance(_cls, type)
                         and issubclass(_cls, _PairwiseBase)
                         for name in vars(_cls)
                         if name.startswith("test_"))

    async def test_i07_scenario_table_consistency(self):
        nodes = self._defined_nodes()
        for row_id, message, _legacy_intent, _legacy_profile, node in SCENARIO_ROWS:
            with self.subTest(row=row_id):
                self.assertIn(node, nodes,
                              f"{row_id} declared test node exists")
                if row_id in {"I-05-A", "I-05-B", "I-05-C"}:
                    _s, _sid, _set_id, _record = await self._fresh_discovery(
                        mode="auto", scenario_id=f"{row_id}-consistency",
                        message=DISCOVERY_WORDING_VARIANTS[0])
                offered = frozenset(
                    schema["name"]
                    for schema in schemas_for_state(
                        (spec.schema for spec in self.loop.TOOL_REGISTRY.values()),
                        None,
                    )
                )
                self.assertEqual(offered, frozenset(INITIAL_TOOL_NAMES),
                                 f"{row_id} exact offered profile; "
                                 f"message={message!r}")


__all__ = ()
