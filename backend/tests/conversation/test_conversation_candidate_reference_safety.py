"""Batch E2 audit: candidate/reference identity safety through the real loop.

Rejection and identity-authoring cases (Auto + Quick): E2-CASE1 an invented
id after a natural routing turn fails bounded and a fresh replan commits;
E2-CASE2 cross-session candidate ids are rejected before mutation; E2-CASE6
raw provider identities are never accepted; E2-CASE7 reference-like rider
text never authors a candidate. Lifecycle/state-transition cases (E2-CASE3,
CASE4, CASE5, CASE8) live in ``test_conversation_candidate_lifecycle_safety``.

Real loop, production state-scoped tool surface, registry/executors, stores,
ledger, and SSE events run untouched; only deterministic Anthropic rounds and
documented provider/data seams are scripted. Offered profiles are asserted
before any scripted tool state is credited.
"""

from __future__ import annotations

from app.services.agent import candidate_store
from app.services.agent.tools.route import present_route
from app.services.agent.turn.contract import GoalState
from app.services.agent.turn.contract import TurnContract
from app.services.agent.turn.evidence import TurnEvidence
from tests.conversation.conversation_candidate_reference_fixtures import (
    CANDIDATE_A,
    CANDIDATE_B,
    CANDIDATE_UNKNOWN_MARKER,
    CANDIDATE_V1,
    CANDIDATE_V2,
    CHANGE_ROUTE_MESSAGE,
    FIRST_OPTION_MESSAGE,
    INVENTED_CANDIDATE_ID,
    NO_ACTIVE_SET_MARKER,
    PRETEND_CANDIDATE_MESSAGE,
    RAW_ROUTE_ID,
    RAW_SHAPE_ID,
    RAW_TRIP_ID,
    REPLAN_MESSAGE,
    ROUTE_MESSAGE,
)
from tests.conversation.conversation_candidate_reference_support import _CandidateReferenceBase
from tests.conversation.conversation_matrix_harness import (
    _turn_round,
    load_agent_loop,
    make_leg,
    route_cards,
)

MODES = ("auto", "quick")
INITIAL_TOOL_PROFILE = frozenset(
    {
        "declare_goals",
        "discover_places",
        "check_transit",
        "prepare_route_options",
        "complete_turn",
    }
)


def _goal_round(
    goal_key: str,
    kind: str,
    tool_name: str,
    tool_id: str,
    tool_input: dict,
    *,
    depends_on: list[str] | None = None,
) -> dict:
    """Build one model round with declaration before capability execution."""

    declaration = {
        "id": f"{tool_id}-goals",
        "name": "declare_goals",
        "input": {
            "goals": [
                {
                    "goal_key": goal_key,
                    "kind": kind,
                    "depends_on": list(depends_on or []),
                }
            ]
        },
    }
    capability_input = dict(tool_input)
    if tool_name != "complete_turn":
        capability_input = {"goal_key": goal_key, **capability_input}
    if tool_name == "prepare_route_options":
        has_explicit_destination = bool(
            capability_input.get("destination")
            or capability_input.get("destination_place_id")
        )
        capability_input.setdefault(
            "destination_source",
            "current_turn" if has_explicit_destination else "accepted_trip",
        )
    capability = {
        "id": tool_id,
        "name": tool_name,
        "input": capability_input,
    }
    return {"tool_use": [declaration, capability], "stop_reason": "tool_use"}


def _present_route_round(tool_id: str, candidate_id: str, *, goal_key: str = "route") -> dict:
    return _turn_round(
        "present_route",
        tool_id,
        {"goal_key": goal_key, "candidate_id": candidate_id},
    )


def _complete_goal_round(
    tool_id: str,
    goal_key: str,
    message: str,
    *,
    outcome: str,
) -> dict:
    return _goal_round(
        goal_key,
        "general_response",
        "complete_turn",
        tool_id,
        {"goal_keys": [goal_key], "outcome": outcome, "message": message},
    )


class _ModelLedCandidateMixin:
    """Natural route setup using declaration-first, state-valid rounds."""

    async def _natural_route_turn(
        self,
        *,
        mode: str,
        scenario_id: str,
        session: dict,
        session_id: str,
        destination: str,
        candidate_id: str = CANDIDATE_V1,
        turn_id: str = "t1",
    ) -> tuple[str, dict]:
        ev = await self._scripted_turn(
            mode=mode,
            session=session,
            session_id=session_id,
            message=ROUTE_MESSAGE,
            rounds=[
                _goal_round(
                    "route", "route", "prepare_route_options", f"tu-prep-{turn_id}",
                    {"destination": destination},
                ),
                _present_route_round(f"tu-pres-{turn_id}", candidate_id),
            ],
            turn_id=turn_id,
            prepare_leg=make_leg(destination=destination),
            fixed_candidate_id=candidate_id,
        )
        names = [name for name, _input in ev.trace.tool_calls]
        self.assertEqual(ev.offered, INITIAL_TOOL_PROFILE, f"{scenario_id} initial state-valid profile")
        self.assertEqual(names, ["declare_goals", "prepare_route_options", "present_route"], scenario_id)
        self.assertEqual(len(route_cards(ev.events)), 1, scenario_id)
        set_id = ev.state["active_candidate_set_id"]
        self.assertTrue(bool(set_id) and set_id.startswith("cs_"), scenario_id)
        self.assertEqual(ev.state["selected_candidate_id"], candidate_id, scenario_id)
        self.assertEqual(ev.mocks["stored_candidate_set_ids"], [set_id], scenario_id)
        record = candidate_store.load_candidate_set(set_id, session_id=session_id)
        self.assertIsNotNone(record, scenario_id)
        self.assertTrue(record["presented"], scenario_id)
        self.assertEqual(record["selected_candidate_id"], candidate_id, scenario_id)
        self.assertEqual(len(session.get("route_cards") or []), 1, scenario_id)
        self.assertIsNotNone(session.get("active_trip"), scenario_id)
        self._assert_policy(scenario_id, mode, ev)
        self._assert_meta_done(scenario_id, ev)
        return set_id, record

    async def _rejected_present_turn(
        self,
        *,
        mode: str,
        scenario_id: str,
        session: dict,
        session_id: str,
        message: str,
        candidate_id: str,
        marker: str,
        set_id: str,
        turn_id: str,
        bypass_accepted_replay: bool = False,
    ):
        """Run a terminal model turn, then probe the real presenter gate.

        A fresh model turn cannot offer ``present_route`` until it has current
        route evidence. The identity probe therefore calls the real presenter
        executor with a server-owned ready evidence handle after the normal
        declaration-first terminal path, preserving the no-mutation invariant
        without crediting an unoffered model call.
        """

        session_before = self._snapshot_session(session)
        record_before = self._snapshot_record(set_id, session_id)
        ev = await self._scripted_turn(
            mode=mode,
            session=session,
            session_id=session_id,
            message=message,
            rounds=[
                _complete_goal_round(
                    f"tu-{turn_id}",
                    "reply",
                    "That option is no longer available.",
                    outcome="unavailable",
                )
            ],
            turn_id=turn_id,
        )
        contract = TurnContract.from_payload(
            {
                "goals": [
                    {"goal_key": "route", "kind": "route", "depends_on": []}
                ]
            }
        )
        evidence = TurnEvidence()
        evidence.bind_contract(contract)
        evidence.record_goal_handle("route", set_id)
        evidence.record_goal("route", GoalState.EVIDENCE_READY, attempted=True)
        ctx = self._tool_ctx(session, session_id)
        ctx.turn_evidence = evidence
        payload = {"goal_key": "route", "candidate_id": candidate_id}
        if bypass_accepted_replay:
            result = await present_route.load_validated_presentation(payload, ctx)
        else:
            result = await present_route.execute(payload, ctx)
        self.assertFalse(result.ok, f"{scenario_id} presenter identity probe rejects")
        self.assertIn(marker, result.error or "", f"{scenario_id} real presenter reports the canonical gate")
        self.assertNotIn(marker, ev.trace.final_text, f"{scenario_id} hides identity detail from rider text")
        self.assertEqual(route_cards(ev.events), [], scenario_id)
        self.assertEqual(ev.mocks["stored_candidate_set_ids"], [], scenario_id)
        self.assertEqual(self._snapshot_session(session), session_before, scenario_id)
        self.assertEqual(self._snapshot_record(set_id, session_id), record_before, scenario_id)
        return ev


class InventedCandidateAfterNaturalRoutingTests(_ModelLedCandidateMixin, _CandidateReferenceBase):
    """E2-CASE1 (Auto + Quick): invented id never presents or corrupts."""

    @classmethod
    def setUpClass(cls):
        cls.loop = load_agent_loop()

    async def _transcript(self, mode: str):
        s = f"E2C1-{mode}"
        session_id, session = self._new_session(mode)
        set_id, _record = await self._natural_route_turn(
            mode=mode, scenario_id=f"{s}-t1", session=session,
            session_id=session_id, destination="Work",
            candidate_id=CANDIDATE_V1, turn_id="t1")
        ev = await self._rejected_present_turn(
            mode=mode, scenario_id=s, session=session,
            session_id=session_id, message=CHANGE_ROUTE_MESSAGE,
            candidate_id=INVENTED_CANDIDATE_ID,
            marker=CANDIDATE_UNKNOWN_MARKER, set_id=set_id, turn_id="t2")
        self.assertNotIn(INVENTED_CANDIDATE_ID, ev.result_blob,
                         f"{s} invented id is not echoed to the model")

    async def _control_replan(self, mode: str):
        s = f"E2C1-CTL-{mode}"
        session_id, session = self._new_session(mode)
        set_id, _record = await self._natural_route_turn(
            mode=mode, scenario_id=f"{s}-t1", session=session,
            session_id=session_id, destination="Work",
            candidate_id=CANDIDATE_V1, turn_id="t1")
        await self._rejected_present_turn(
            mode=mode, scenario_id=s, session=session,
            session_id=session_id, message=CHANGE_ROUTE_MESSAGE,
            candidate_id=INVENTED_CANDIDATE_ID,
            marker=CANDIDATE_UNKNOWN_MARKER, set_id=set_id, turn_id="t2")
        rounds = [
            _goal_round(
                "route", "route", "prepare_route_options", "tu-replan",
                {"destination": "Coney Island"},
            ),
            _present_route_round("tu-replan-present", CANDIDATE_V2),
        ]
        ev = await self._scripted_turn(
            mode=mode, session=session, session_id=session_id,
            message=REPLAN_MESSAGE, rounds=rounds, turn_id="t3",
            prepare_leg=make_leg(destination="Coney Island"),
            fixed_candidate_id=CANDIDATE_V2)
        self.assertEqual(
            [name for name, _input in ev.trace.tool_calls],
            ["declare_goals", "prepare_route_options", "present_route"],
            f"{s} fresh replan after rejection works")
        self.assertEqual(
            (len(route_cards(ev.events)), ev.state["selected_candidate_id"]),
            (1, CANDIDATE_V2),
            f"{s} one card and the new candidate committed")

    async def test_e2_case1_invented_present(self):
        for mode in MODES:
            with self.subTest(mode=mode):
                await self._transcript(mode)

    async def test_e2_case1_control_replan_after_rejection(self):
        for mode in MODES:
            with self.subTest(mode=mode):
                await self._control_replan(mode)


class CrossSessionCandidateTests(_ModelLedCandidateMixin, _CandidateReferenceBase):
    """E2-CASE2 (Auto + Quick): candidate ids never cross sessions."""

    @classmethod
    def setUpClass(cls):
        cls.loop = load_agent_loop()

    async def _transcript(self, mode: str):
        s = f"E2C2-{mode}"
        sid_a, s_a = self._new_session(mode)
        sid_b, s_b = self._new_session(mode)
        set_a, record_a = await self._natural_route_turn(
            mode=mode, scenario_id=f"{s}-A", session=s_a, session_id=sid_a,
            destination="Work", candidate_id=CANDIDATE_A, turn_id="t1")
        set_b, record_b = await self._natural_route_turn(
            mode=mode, scenario_id=f"{s}-B", session=s_b, session_id=sid_b,
            destination="Home", candidate_id=CANDIDATE_B, turn_id="t1")
        self.assertNotEqual(set_a, set_b, f"{s} sessions own distinct sets")
        a_before = self._snapshot_session(s_a)
        record_a_before = self._snapshot_record(set_a, sid_a)
        await self._rejected_present_turn(
            mode=mode, scenario_id=f"{s}-B-uses-A", session=s_b,
            session_id=sid_b, message=CHANGE_ROUTE_MESSAGE,
            candidate_id=CANDIDATE_A, marker=CANDIDATE_UNKNOWN_MARKER,
            set_id=set_b, turn_id="t2")
        self.assertEqual(
            (self._snapshot_session(s_a), self._snapshot_record(set_a, sid_a)),
            (a_before, record_a_before),
            f"{s} A session and record untouched by B's probe")
        b_before = self._snapshot_session(s_b)
        record_b_before = self._snapshot_record(set_b, sid_b)
        await self._rejected_present_turn(
            mode=mode, scenario_id=f"{s}-A-uses-B", session=s_a,
            session_id=sid_a, message=CHANGE_ROUTE_MESSAGE,
            candidate_id=CANDIDATE_B, marker=CANDIDATE_UNKNOWN_MARKER,
            set_id=set_a, turn_id="t2")
        self.assertEqual(
            (self._snapshot_session(s_b), self._snapshot_record(set_b, sid_b)),
            (b_before, record_b_before),
            f"{s} B session and record untouched by A's probe")
        for src, dst in ((set_a, sid_b), (set_b, sid_a)):
            self.assertIsNone(
                candidate_store.load_candidate_set(src, session_id=dst),
                f"{s} set {src} does not load under {dst}")

    async def test_e2_case2_cross_session_rejected(self):
        for mode in MODES:
            with self.subTest(mode=mode):
                await self._transcript(mode)


class RawProviderIdentityTests(_ModelLedCandidateMixin, _CandidateReferenceBase):
    """E2-CASE6 (Auto + Quick): raw/provider ids never become candidates."""

    @classmethod
    def setUpClass(cls):
        cls.loop = load_agent_loop()

    async def _transcript(self, mode: str):
        s = f"E2C6-{mode}"
        session_id, session = self._new_session(mode)
        set_id, record = await self._natural_route_turn(
            mode=mode, scenario_id=f"{s}-t1", session=session,
            session_id=session_id, destination="Work",
            candidate_id=CANDIDATE_V1, turn_id="t1")
        # The natural turn's first tool result is the real prepare digest.
        prepare_blob = str(
            self.loop.client.messages.calls[1]["messages"][-1]["content"]
        )
        prepare_content = self.loop.client.messages.calls[1]["messages"][-1]["content"]
        self._assert_no_raw_provider_identity(s, prepare_content)
        for index, probe_id in enumerate((RAW_ROUTE_ID, RAW_TRIP_ID, RAW_SHAPE_ID)):
            await self._rejected_present_turn(
                mode=mode, scenario_id=f"{s}-raw-{probe_id}", session=session,
                session_id=session_id, message=CHANGE_ROUTE_MESSAGE,
                candidate_id=probe_id, marker=CANDIDATE_UNKNOWN_MARKER,
                set_id=set_id, turn_id=f"t2-{index}")
        self.assertIn("cd_", prepare_blob, f"{s} model sees opaque candidate ids")
        self.assertNotIn("trip_", prepare_blob, f"{s} model never sees raw trip ids")
        self.assertNotIn("shape_", prepare_blob, f"{s} model never sees raw shape ids")
        for entry in record.get("candidates") or []:
            digest = entry.get("digest") if isinstance(entry, dict) else None
            destination = entry.get("destination_place") if isinstance(entry, dict) else None
            digest_id = digest.get("destination_place_id") if isinstance(digest, dict) else None
            destination_id = destination.get("place_id") if isinstance(destination, dict) else None
            ids = [value for value in (digest_id, destination_id) if value]
            for place_id in ids:
                self.assertTrue(
                    str(place_id).startswith("pl_"),
                    f"{s} stored place identities stay opaque: {place_id!r}",
                )
            if ids:
                self.assertEqual(
                    len(ids), 2,
                    f"{s} stored endpoint and digest identities are paired",
                )
                self.assertEqual(
                    digest_id,
                    destination_id,
                    f"{s} stored endpoint and digest identities agree",
                )
            self._assert_no_raw_provider_identity(s, entry)

    def _assert_no_raw_provider_identity(self, scenario_id: str, value: object) -> None:
        if isinstance(value, dict):
            for key, nested in value.items():
                lowered_key = str(key).casefold()
                self.assertNotIn(
                    lowered_key,
                    {"provider_place_id", "provider_place_ids"},
                    f"{scenario_id} stored entry leaked provider identity field {key!r}",
                )
                self._assert_no_raw_provider_identity(scenario_id, nested)
            return
        if isinstance(value, (list, tuple)):
            for nested in value:
                self._assert_no_raw_provider_identity(scenario_id, nested)
            return
        lowered = str(value or "").casefold()
        for marker in ("chij", RAW_TRIP_ID.casefold(), RAW_SHAPE_ID.casefold()):
            self.assertNotIn(
                marker,
                lowered,
                f"{scenario_id} stored entry leaked raw provider identity {marker!r}",
            )

    async def test_e2_case6_raw_provider_identity(self):
        for mode in MODES:
            with self.subTest(mode=mode):
                await self._transcript(mode)


class CandidateLikeTextDoesNotAuthorTests(_ModelLedCandidateMixin, _CandidateReferenceBase):
    """E2-CASE7 (Auto + Quick): rider text never authors a candidate."""

    @classmethod
    def setUpClass(cls):
        cls.loop = load_agent_loop()

    async def _no_author_turn(
        self,
        mode: str,
        s: str,
        message: str,
        rounds: list,
        expected_calls: list[str],
    ):
        session_id, session = self._new_session(mode)
        ev = await self._scripted_turn(
            mode=mode, session=session, session_id=session_id,
            message=message, rounds=rounds, turn_id="t1")
        self.assertEqual(
            ev.offered, INITIAL_TOOL_PROFILE,
            f"{s} offered={sorted(ev.offered)}")
        self.assertEqual(
            [name for name, _tool_input in ev.trace.tool_calls],
            expected_calls,
            f"{s} bounded calls",
        )
        self._assert_no_candidate_authoring(
            scenario_id=s,
            ev=ev,
            expected_execution_count=sum(
                name != "declare_goals" for name in expected_calls
            ),
        )
        return ev

    async def _pretend_variant(self, mode: str):
        s = f"E2C7-{mode}-PRETEND"
        rounds = [
            _complete_goal_round(
                "tu-refuse",
                "reply",
                "I can't use an unverified route option.",
                outcome="refusal",
            ),
        ]
        ev = await self._no_author_turn(
            mode,
            s,
            PRETEND_CANDIDATE_MESSAGE,
            rounds,
            ["declare_goals", "complete_turn"],
        )
        present_end = next(
            (event for event in ev.events
             if event.type == "tool_end" and event.tool == "present_route"),
            None)
        self.assertIsNone(present_end, f"{s} no presenter is state-valid without prepared evidence")
        self.assertNotIn(NO_ACTIVE_SET_MARKER, ev.trace.final_text, s)

    async def _first_option_variant(self, mode: str):
        s = f"E2C7-{mode}-FIRST"
        ev = await self._no_author_turn(
            mode, s, FIRST_OPTION_MESSAGE,
            [
                _complete_goal_round(
                    "tu-clarify",
                    "reply",
                    "Which trip or destination do you mean?",
                    outcome="clarification",
                )
            ],
            ["declare_goals", "complete_turn"],
        )
        self.assertEqual(ev.events[-1].stop_reason, "clarification_required")

    async def test_e2_case7_pretend_candidate_text(self):
        for mode in MODES:
            with self.subTest(mode=mode):
                await self._pretend_variant(mode)

    async def test_e2_case7_first_option_text(self):
        for mode in MODES:
            with self.subTest(mode=mode):
                await self._first_option_variant(mode)


__all__ = ()
