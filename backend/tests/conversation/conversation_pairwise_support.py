"""Batch I shared support: pairwise invariants and metamorphic properties.

Non-test module (no ``Test*``/``test_*`` names at module level): pytest never
collects it. Every scenario drives the real agent loop and real stores; only
genuine provider/data seams are scripted. Scenario orchestration lives in the
test module; this module holds the reusable invariants (immutable
projections, exact offered profiles, single-card presentation, no-good audit
preservation, what-if preview / accept / reject isolation, discovery
reference authority).
"""

from __future__ import annotations

import copy
import secrets
import unittest
from contextlib import contextmanager, nullcontext
from unittest.mock import AsyncMock, patch

from app.services.agent import candidate_store, discovery_store
from app.services.incidents import index as incident_index
from app.services.mta import realtime as mta_realtime
from app.services.agent.tools.transit import evidence as transit_evidence
from app.services.agent import trip_state as trip_state_module
from app.services.agent.tools.transit import transit_snapshot
from app.services.agent.tools.places import discover_places
from app.services.agent.tools._types import ToolResult
from tests.conversation.conversation_discovery_fixtures import DISCOVERY_TOOL_PROFILE
from tests.conversation.conversation_pairwise_fixtures import (
    ACCEPT_MESSAGE, DISCOVERY_REFERENCE_TOOL_PROFILE, FORBIDDEN_TOOLS,
    LEAK_MARKERS, NAVIGATE_SELECTED_MESSAGE, NO_HARD_CONSTRAINT_MATCH,
    REJECT_MESSAGE, ROUTE_NAVIGATION_TOOL_PROFILE, SELECT_SECOND_MESSAGE,
    STATUS_MODEL_TEXT, discovery_leg_for,
    transit_question_profile_for,
)
from tests.conversation.conversation_matrix_harness import (
    _turn_round,
    check_transit_input,
    clear_caches,
    complete_turn_round,
    new_session,
    policy_model,
    route_cards,
    run_turn,
    seed_accepted_active_trip,
)

ACTIVE_KEYS = (
    "origin", "destination", "waypoints", "planning_mode",
    "requested_departure", "requested_arrival", "active_candidate_set_id",
    "selected_candidate_id", "preferences",
)
TEMPORARY_KEYS = (
    "temporary_candidate_set_id", "temporary_selected_candidate_id",
    "temporary_base_candidate_set_id",
)


def _goal_for_model_tool(
    name: str, tool_input: dict, *, selection_only: bool = False
) -> tuple[str, str] | None:
    """Describe the structured rider outcome behind a scripted capability."""

    if name == "present_places" and selection_only:
        return "destination", "destination_selection"
    if name in {"discover_places", "present_places"}:
        return "places", "place_recommendation"
    if name in {"prepare_route_options", "present_route"}:
        return "route", "route"
    if name in {"check_transit", "present_transit"}:
        operation = str(tool_input.get("operation") or "service_status")
        kind = {
            "arrivals": "arrivals",
            "accessibility": "accessibility",
            "fact": "transit_fact",
            "area_conditions": "area_conditions",
            "event_schedule": "event_or_crowd",
            "venue_crowd_window": "event_or_crowd",
        }.get(operation, "service_status")
        return "transit", kind
    if name == "complete_turn":
        goal_keys = tool_input.get("goal_keys")
        if isinstance(goal_keys, list) and "route" in goal_keys:
            return "route", "route"
        return "response", "general_response"
    return None


def _model_led_rounds(
    rounds: list[dict], *, turn_id: str, session_id: str
) -> tuple[list[dict], str | None]:
    """Add the model-led goal declaration to deterministic test responses."""

    calls = [
        call
        for scripted in rounds
        for call in scripted.get("tool_use") or []
        if str(call.get("name") or "") != "declare_goals"
    ]
    names = {str(call.get("name") or "") for call in calls}
    selection_only = "present_places" in names and "discover_places" not in names
    provider = next(
        (
            _goal_for_model_tool(
                str(call.get("name") or ""),
                call.get("input") or {},
                selection_only=selection_only,
            )
            for call in calls
            if str(call.get("name") or "")
            in {"discover_places", "prepare_route_options", "check_transit"}
        ),
        None,
    )
    goals: list[dict] = []
    seen: set[str] = set()
    for call in calls:
        name = str(call.get("name") or "")
        spec = _goal_for_model_tool(
            name, call.get("input") or {}, selection_only=selection_only
        )
        if name == "complete_turn" and provider is not None:
            spec = provider
        if spec is None or spec[0] in seen:
            continue
        seen.add(spec[0])
        goals.append({"goal_key": spec[0], "kind": spec[1], "depends_on": []})
    if not goals:
        return rounds, None

    # Evidence handles are immutable and globally keyed in the test cache.
    # Include the session owner so repeated metamorphic variants cannot reuse
    # one prior session's handle and force the real store to rotate it.
    evidence_id = f"te_test_{session_id}_{turn_id}"
    adapted: list[dict] = []
    declared = False
    initial_tools = {
        "declare_goals",
        "discover_places",
        "check_transit",
        "prepare_route_options",
        "complete_turn",
    }
    for scripted in rounds:
        tool_uses = scripted.get("tool_use") or []
        if (
            not declared
            and tool_uses
            and all(
                str(call.get("name") or "") not in initial_tools
                for call in tool_uses
            )
        ):
            # A presenter for evidence from a prior turn is state-valid only
            # after the declaration has been accepted. Keep the declaration
            # in its own response so the test never credits an unoffered tool.
            adapted.append(
                {
                    "tool_use": [
                        {
                            "id": f"tu-{turn_id}-goals",
                            "name": "declare_goals",
                            "input": {"goals": goals},
                        }
                    ],
                    "stop_reason": "tool_use",
                }
            )
            declared = True
        transformed: list[dict] = []
        for call in tool_uses:
            name = str(call.get("name") or "")
            tool_input = dict(call.get("input") or {})
            spec = _goal_for_model_tool(
                name, tool_input, selection_only=selection_only
            )
            if name == "complete_turn":
                existing = tool_input.get("goal_keys")
                outcome = str(tool_input.get("outcome") or "answer")
                if provider is not None:
                    tool_input["goal_keys"] = [provider[0]]
                    if outcome == "answer":
                        tool_input["outcome"] = "unavailable"
                elif not isinstance(existing, list) or not existing:
                    tool_input["goal_keys"] = ["response"]
                if provider is not None and any(
                    str(item.get("name") or "") == "check_transit" for item in calls
                ) and outcome == "answer":
                    name = "present_transit"
                    tool_input = {
                        "goal_key": provider[0],
                        "evidence_set_id": evidence_id,
                    }
            if name == "present_transit":
                tool_input["goal_key"] = "transit"
                tool_input.setdefault("evidence_set_id", evidence_id)
            elif spec is not None and name not in {"complete_turn", "declare_goals"}:
                tool_input["goal_key"] = spec[0]
            transformed.append({**call, "name": name, "input": tool_input})
        if not transformed:
            continue
        if not declared:
            transformed.insert(
                0,
                {
                    "id": f"tu-{turn_id}-goals",
                    "name": "declare_goals",
                    "input": {"goals": goals},
                },
            )
            declared = True
        adapted.append({**scripted, "tool_use": transformed})
    return adapted, evidence_id


def _expected_initial_profile(expected_profile: object) -> frozenset[str]:
    """Translate retired phrase-specific expectations to state-based startup."""

    names = set(expected_profile or ())
    if names >= {
        "declare_goals", "discover_places", "check_transit",
        "prepare_route_options", "present_places", "present_transit",
        "present_route", "complete_turn",
    }:
        return frozenset(
            {
                "declare_goals",
                "discover_places",
                "check_transit",
                "prepare_route_options",
                "complete_turn",
            }
        )
    return frozenset(names)
# Preferences may be patched while persisting a hard constraint, so they are
# not part of ACCEPTED_KEYS; the no-good audit asserts the allowed delta.
ACCEPTED_KEYS = tuple(key for key in ACTIVE_KEYS if key != "preferences")


def capture_temporary_candidate(session: dict, session_id: str):
    """Read the real store record for the bound temporary scenario."""
    state = trip_state_module.get_trip_state(session)
    set_id = state["temporary_candidate_set_id"]
    record = candidate_store.load_candidate_set(set_id, session_id=session_id)
    entry = next((c for c in (record or {}).get("candidates") or []
                  if isinstance(c, dict)), None)
    if record is None or entry is None:
        raise AssertionError(f"temporary candidate set {set_id} not stored")
    return set_id, str(entry["candidate_id"]), record


class _PairwiseBase(unittest.IsolatedAsyncioTestCase):
    """Shared invariants and turn runners for the Batch I scenarios."""

    loop = None  # set in setUpClass by subclasses

    def setUp(self):
        clear_caches()

    def _new_session(self, mode: str) -> tuple[str, dict]:
        session_id = f"sess-i-{mode}-{secrets.token_hex(4)}"
        _sid, session = new_session()
        return session_id, session

    def _seed_accepted(self, mode: str):
        session_id, session = self._new_session(mode)
        return session, session_id, seed_accepted_active_trip(session, session_id)

    async def _seed_temporary(self, *, mode, scenario_id, message,
                              prepare_input, prepare_leg, candidate_id,
                              expected_prepare_subset=None):
        session, session_id, seed = self._seed_accepted(mode)
        await self._preview_turn(
            mode=mode, scenario_id=f"{scenario_id}-preview", session=session,
            session_id=session_id, seed=seed, message=message,
            prepare_input=prepare_input, prepare_leg=prepare_leg,
            candidate_id=candidate_id, state_before=self._projection(session),
            expected_prepare_subset=expected_prepare_subset)
        return session, session_id, seed

    async def _discovery_turn(self, *, mode, scenario_id, session, session_id,
                              message, poi_result, turn_id="t1"):
        from tests.conversation.conversation_matrix_harness import (
            discover_search_input,
            discovery_id_tokens,
            present_places_round,
        )
        set_token, place_tokens = discovery_id_tokens(session_id, turn_id)
        place_ids = iter(place_tokens)
        rounds = [
            _turn_round(
                "discover_places",
                f"tu-disc-{turn_id}",
                discover_search_input("pizza Brooklyn", borough="Brooklyn"),
            ),
            present_places_round(f"tu-pres-{turn_id}", set_token, place_tokens),
        ]
        trace = self.loop.TurnTrace()
        mocks: dict = {}
        with (
            patch.object(discover_places.search_local_places, "execute",
                          new=AsyncMock(return_value=poi_result)),
            patch.object(discovery_store, "new_discovery_set_id", return_value=set_token),
            patch.object(discovery_store, "new_place_id", side_effect=lambda: next(place_ids)),
        ):
            adapted_rounds, evidence_id = _model_led_rounds(
                rounds, turn_id=turn_id, session_id=session_id
            )
            with patch.object(
                transit_evidence,
                "new_evidence_set_id",
                return_value=evidence_id,
            ):
                events, trace = await run_turn(
                self.loop, session=session, session_id=session_id,
                message=message, rounds=adapted_rounds, mode=mode, trace=trace,
                mocks=mocks, turn_id=turn_id)
        raw_calls = list(trace.tool_calls)
        if raw_calls and raw_calls[0][0] == "declare_goals":
            trace.model_led_tool_calls = raw_calls
            trace.tool_calls = [call for call in raw_calls if call[0] != "declare_goals"]
        names = [n for n, _i in trace.tool_calls]
        self.assertEqual(names, ["discover_places", "present_places"],
                         f"{scenario_id} only the public discovery path")
        self.assertEqual((route_cards(events), mocks["stored_candidate_set_ids"]),
                         ([], []), f"{scenario_id} no card, no candidate set")
        self.assertEqual(self._offered(), _expected_initial_profile(DISCOVERY_TOOL_PROFILE),
                         f"{scenario_id} discovery tool profile")
        set_id = trip_state_module.get_trip_state(session)[
            "active_discovery_set_id"]
        self.assertTrue(set_id.startswith("ds_"),
                        f"{scenario_id} real server-owned discovery set")
        record = discovery_store.load_discovery_set(set_id, session_id=session_id)
        self.assertIsNotNone(record, f"{scenario_id} stored discovery record")
        return set_id, record

    async def _fresh_discovery(self, *, mode, scenario_id, message,
                               poi_result=None, turn_id="t1"):
        if poi_result is None:
            from tests.conversation.conversation_discovery_fixtures import poi_result as _poi
            poi_result = _poi()
        session_id, session = self._new_session(mode)
        set_id, record = await self._discovery_turn(
            mode=mode, scenario_id=scenario_id, session=session,
            session_id=session_id, message=message, poi_result=poi_result, turn_id=turn_id)
        return session, session_id, set_id, record

    @contextmanager
    def _expired_clock(self, record: dict):
        with patch("app.services.agent.discovery_store.time.time",
                   return_value=float(record["expires_at"]) + 60.0):
            yield

    def _projection(self, session: dict) -> dict:
        """Immutable server-state snapshot; excludes the volatile updated_at
        and the benign empty ``exclude_modes`` slot default (non-empty
        exclusions are preserved)."""
        state = copy.deepcopy(trip_state_module.get_trip_state(session))
        state.pop("updated_at", None)
        slots = copy.deepcopy(session.get("slots") or {})
        constraints = slots.get("constraints") or {}
        if constraints.get("exclude_modes") == []:
            constraints.pop("exclude_modes")
        if not constraints:
            slots.pop("constraints", None)
        return {"trip_state": state,
                "active_trip": copy.deepcopy(session.get("active_trip")),
                "route_cards": copy.deepcopy(session.get("route_cards") or []),
                "slots": slots,
                "profile": copy.deepcopy(session.get("profile") or {}),
                "pending_trip": copy.deepcopy(session.get("pending_trip") or {})}

    def _assert_projection_unchanged(self, before, after, scenario_id):
        self.assertEqual(after, before, f"{scenario_id} state must not mutate")

    def _offered(self) -> frozenset:
        return frozenset(s["name"] for s in self.loop.client.messages.calls[0]["tools"])

    def _assert_meta_done(self, scenario_id, events):
        self.assertEqual((events[0].type, events[-1].type,
                          events[-1].stop_reason),
                         ("meta", "done", "end_turn"),
                         f"{scenario_id} meta/done/end_turn")

    def _assert_policy(self, scenario_id, mode, trace):
        expected_mode, expected_model = policy_model(self.loop, mode)
        models = [call["model"] for call in self.loop.client.messages.calls]
        self.assertEqual((trace.initial_mode, trace.final_mode, models),
                         (expected_mode, expected_mode,
                          [expected_model] * len(models)),
                         f"{scenario_id} policy mode/models")

    def _assert_temporary_clear(self, scenario_id, state):
        self.assertEqual(tuple(state[k] for k in TEMPORARY_KEYS),
                         (None, None, None),
                         f"{scenario_id} no temporary residue")

    async def _run_turn(self, *, session, session_id, message, rounds, mode,
                        turn_id="t1", prepare_leg=None, fixed_candidate_id=None,
                        mocks=None, clock=None):
        trace = self.loop.TurnTrace()
        adapted_rounds, evidence_id = _model_led_rounds(
            rounds, turn_id=turn_id, session_id=session_id
        )
        with clock if clock is not None else nullcontext():
            with patch.object(
                transit_evidence,
                "new_evidence_set_id",
                return_value=evidence_id,
            ):
                events, trace = await run_turn(
                    self.loop, session=session, session_id=session_id,
                    message=message, rounds=adapted_rounds, mode=mode, trace=trace,
                    mocks=mocks, turn_id=turn_id, prepare_leg=prepare_leg,
                    fixed_candidate_id=fixed_candidate_id)
        raw_calls = list(trace.tool_calls)
        if raw_calls and raw_calls[0][0] == "declare_goals":
            trace.model_led_tool_calls = raw_calls
            trace.tool_calls = [call for call in raw_calls if call[0] != "declare_goals"]
        return events, trace, mocks

    def _assert_turn_contract(self, *, scenario_id, events, trace, mocks, mode,
                              expected_tools, expected_profile, expect_card=0,
                              expect_stored=0, model_calls=None,
                              expected_stop_reason="end_turn"):
        names = [n for n, _i in trace.tool_calls]
        self.assertEqual(names, list(expected_tools), f"{scenario_id} executed={names}")
        self.assertFalse(set(names) & set(FORBIDDEN_TOOLS), f"{scenario_id} forbidden tool executed")
        self.assertEqual(self._offered(), _expected_initial_profile(expected_profile),
                         f"{scenario_id} offered={sorted(self._offered())}")
        self.assertEqual(len(route_cards(events)), expect_card, f"{scenario_id} cards")
        self.assertEqual(len(mocks.get("stored_candidate_set_ids") or []),
                         expect_stored, f"{scenario_id} stored sets")
        if expected_stop_reason == "end_turn":
            self._assert_meta_done(scenario_id, events)
        else:
            self.assertEqual(
                (events[0].type, events[-1].type, events[-1].stop_reason),
                ("meta", "done", expected_stop_reason),
                f"{scenario_id} meta/done/{expected_stop_reason}",
            )
        if model_calls is not None:
            self.assertEqual(trace.model_call_count, model_calls, scenario_id)
        self._assert_policy(scenario_id, mode, trace)
        lowered = trace.final_text.casefold()
        for marker in LEAK_MARKERS:
            self.assertNotIn(marker, lowered, f"{scenario_id} leaked {marker}")
        return names

    async def _status_turn(self, *, mode, scenario_id, session, session_id,
                           message, before, turn_id="t2", clock=None,
                           route_ids=("Q",), direction="uptown",
                           expect_clarification=False):
        status = ToolResult(
            ok=True,
            data={"alerts": []},
            summary="No current Q service alerts.",
        )
        with (
            patch.object(
                transit_snapshot,
                "execute",
                new=AsyncMock(return_value=status),
            ) as status_lookup,
            patch.object(
                mta_realtime,
                "get_stalled_trains",
                new=AsyncMock(return_value=[]),
            ),
            patch.object(
                mta_realtime,
                "get_stalled_buses",
                new=AsyncMock(return_value=[]),
            ),
            patch.object(
                incident_index,
                "lookup_incidents",
                return_value={"incidents": [], "coverage_status": "current"},
            ),
        ):
            terminal = (
                complete_turn_round(
                    "tu-status-done",
                    "Which direction should I check for the Q—uptown or downtown?",
                    outcome="clarification",
                )
                if expect_clarification
                else complete_turn_round("tu-status-done", STATUS_MODEL_TEXT)
            )
            events, trace, mocks = await self._run_turn(
                session=session,
                session_id=session_id,
                message=message,
                rounds=[
                    _turn_round(
                        "check_transit",
                        "tu-status",
                        check_transit_input(
                            "service_status",
                            route_ids=list(route_ids),
                            direction=direction,
                        ),
                    ),
                    terminal,
                ],
                mode=mode,
                turn_id=turn_id,
                mocks={},
                clock=clock,
            )
        expected_tools = (
            ("check_transit", "complete_turn")
            if expect_clarification
            else ("check_transit", "present_transit")
        )
        self._assert_turn_contract(
            scenario_id=scenario_id, events=events, trace=trace, mocks=mocks,
            mode=mode, expected_tools=expected_tools,
            expected_profile=transit_question_profile_for(message), expect_card=0,
            expect_stored=0, model_calls=2,
            expected_stop_reason=(
                "clarification_required" if expect_clarification else "end_turn"
            ))
        if expect_clarification:
            status_lookup.assert_not_awaited()
            self.assertIn("uptown or downtown", trace.final_text.casefold(),
                          f"{scenario_id} asks for direction")
        else:
            status_lookup.assert_awaited_once()
            self.assertNotIn("uptown or downtown", trace.final_text.casefold(),
                             f"{scenario_id} does not ask for direction")
        prepare = mocks.get("prepare_single_leg")
        self.assertTrue(prepare is None or prepare.await_count == 0,
                        f"{scenario_id} provider seam never reached")
        self._assert_projection_unchanged(before, self._projection(session),
                                          scenario_id)

    async def _route_turn(self, *, mode, scenario_id, session, session_id,
                          message, rounds, prepare_leg=None,
                          fixed_candidate_id=None, turn_id="t1"):
        return await self._run_turn(
            session=session, session_id=session_id, message=message,
            rounds=rounds, mode=mode, turn_id=turn_id, prepare_leg=prepare_leg,
            fixed_candidate_id=fixed_candidate_id, mocks={})

    def _assert_single_present_single_card(
        self, *, scenario_id, events, trace, mocks, session, session_id, mode,
        candidate_id, destination, expected_tools):
        self._assert_turn_contract(
            scenario_id=scenario_id, events=events, trace=trace, mocks=mocks,
            mode=mode, expected_tools=expected_tools,
            expected_profile=ROUTE_NAVIGATION_TOOL_PROFILE, expect_card=1, expect_stored=1)
        cards = route_cards(events)
        self.assertEqual([c.role for c in cards], ["recommended"],
                         f"{scenario_id} one recommended card")
        stored = mocks["stored_candidate_set_ids"]
        state = trip_state_module.get_trip_state(session)
        self.assertEqual((state["active_candidate_set_id"],
                          state["selected_candidate_id"], state["destination"]),
                         (stored[0], candidate_id, destination), scenario_id)
        self._assert_temporary_clear(scenario_id, state)
        record = candidate_store.load_candidate_set(
            stored[0], session_id=session_id)
        self.assertEqual(
            (record["presented"], record["selected_candidate_id"],
             session["active_trip"]["card_id"], mocks["prepare_single_leg"].await_count),
            (True, candidate_id, cards[0].card_id, 1), scenario_id)
        return cards[0].card_id

    async def _one_present(self, *, mode, scenario_id, message, destination,
                           candidate_id, prepare_leg, session=None,
                           session_id=None, turn_id="t1"):
        if session is None:
            session_id, session = self._new_session(mode)
        rounds = [_turn_round("prepare_route_options", "tu-prep",
                              {"destination": destination}),
                  _turn_round("present_route", "tu-pres",
                              {"candidate_id": candidate_id})]
        events, trace, mocks = await self._route_turn(
            mode=mode, scenario_id=scenario_id, session=session,
            session_id=session_id, message=message, rounds=rounds,
            prepare_leg=prepare_leg, fixed_candidate_id=candidate_id,
            turn_id=turn_id)
        self._assert_single_present_single_card(
            scenario_id=scenario_id, events=events, trace=trace, mocks=mocks,
            session=session, session_id=session_id, mode=mode,
            candidate_id=candidate_id, destination=destination,
            expected_tools=("prepare_route_options", "present_route"))
        return session, session_id

    def _assert_no_good_audit(
        self, *, scenario_id, events, trace, mocks, session, session_id, mode,
        seed, state_before, expected_violations=(), expected_prepare_input=None,
        expected_slots=None, expected_preferences=None):
        self._assert_turn_contract(
            scenario_id=scenario_id, events=events, trace=trace, mocks=mocks,
            mode=mode,
            expected_tools=("prepare_route_options", "complete_turn"),
            expected_profile=ROUTE_NAVIGATION_TOOL_PROFILE, expect_card=0, expect_stored=1, model_calls=2)
        stored = mocks["stored_candidate_set_ids"]
        audit = candidate_store.load_candidate_set(stored[0], session_id=session_id)
        self.assertIsNotNone(audit, scenario_id)
        self.assertEqual(
            (audit["route_status"], audit["presented"],
             audit["selected_candidate_id"], audit["hard_constraints"]),
            (NO_HARD_CONSTRAINT_MATCH, False, None, {"required": True}),
            scenario_id)
        if expected_violations:
            digest = audit["candidates"][0]["digest"]
            self.assertTrue(
                all(v in digest.get("hard_constraint_violations") or []
                    for v in expected_violations),
                f"{scenario_id} violates {expected_violations}")
        if expected_prepare_input is not None:
            for key, value in expected_prepare_input.items():
                self.assertEqual(audit["tool_input"].get(key), value,
                                 f"{scenario_id} input[{key}]")
        if expected_slots is not None:
            constraints = (session.get("slots") or {}).get("constraints") or {}
            for key, value in expected_slots.items():
                self.assertEqual(constraints.get(key), value,
                                 f"{scenario_id} slots[{key}]")
        state = trip_state_module.get_trip_state(session)
        self.assertEqual(
            ({key: state[key] for key in ACCEPTED_KEYS},
             session["active_trip"]["card_id"],
             [c["card_id"] for c in session["route_cards"]]),
            ({key: state_before[key] for key in ACCEPTED_KEYS},
             seed.card_id, [seed.card_id]),
            f"{scenario_id} accepted selection preserved")
        preferences = state.get("preferences") or {}
        allowed = expected_preferences or {}
        self.assertEqual(
            {key: value for key, value in preferences.items()
             if key not in allowed},
            {key: value for key, value in
             (state_before.get("preferences") or {}).items()
             if key not in allowed},
            f"{scenario_id} unrelated preferences unchanged")
        for key, value in allowed.items():
            self.assertEqual(preferences.get(key), value,
                             f"{scenario_id} preferences[{key}]")
        self.assertNotEqual(stored[0], seed.candidate_set_id,
                            f"{scenario_id} audit set is separate")
        self.assertEqual(mocks["prepare_single_leg"].await_count, 1, scenario_id)
        self.assertIn("could not find", trace.final_text, scenario_id)
        for marker in ("recommended", "i'd take", "best option", "cd_", "cs_"):
            self.assertNotIn(marker, trace.final_text.casefold(),
                             f"{scenario_id} leaked {marker}")
        return audit

    async def _preview_turn(
        self, *, mode, scenario_id, session, session_id, seed, message,
        prepare_input, prepare_leg, candidate_id, state_before, turn_id="t1",
        expected_prepare_subset=None):
        prepare_input = dict(prepare_input)
        # The model declares a temporary scenario through structured tool
        # input; no phrase-family classifier may add this at runtime.
        prepare_input["what_if"] = True
        rounds = [_turn_round("prepare_route_options", "tu-prepare", prepare_input),
                  _turn_round("present_route", "tu-preview",
                              {"candidate_id": candidate_id})]
        events, trace, mocks = await self._run_turn(
            session=session, session_id=session_id, message=message,
            rounds=rounds, mode=mode, turn_id=turn_id, prepare_leg=prepare_leg,
            fixed_candidate_id=candidate_id, mocks={})
        self._assert_turn_contract(
            scenario_id=scenario_id, events=events, trace=trace, mocks=mocks,
            mode=mode, expected_tools=("prepare_route_options", "present_route"),
            expected_profile=ROUTE_NAVIGATION_TOOL_PROFILE, expect_card=1, expect_stored=1, model_calls=2)
        self.assertEqual(
            (trace.tool_calls[0][1]["what_if"], trace.tool_calls[1][1]),
            (
                True,
                {
                    "candidate_id": candidate_id,
                    "goal_key": "route",
                    "lead_in": "The route options were close, so I chose this one for your trip.",
                    "follow_up": "",
                    "reason_code": "meets_hard_constraints",
                },
            ),
            f"{scenario_id} server-forced what_if + preview input")
        if expected_prepare_subset is not None:
            for key, value in expected_prepare_subset.items():
                self.assertEqual(trace.tool_calls[0][1].get(key), value,
                                 f"{scenario_id} prepare input[{key}]")
        state = trip_state_module.get_trip_state(session)
        self.assertEqual(
            ({key: state[key] for key in ACTIVE_KEYS},
             session["active_trip"]["card_id"], [c["card_id"] for c in session["route_cards"]]),
            ({key: state_before["trip_state"][key] for key in ACTIVE_KEYS},
             seed.card_id, [seed.card_id]),
            f"{scenario_id} active facts/card unchanged by preview")
        temp_set_id, stored_id, record = capture_temporary_candidate(
            session, session_id)
        self.assertEqual(stored_id, candidate_id, scenario_id)
        self.assertEqual(
            (state["temporary_candidate_set_id"],
             state["temporary_selected_candidate_id"],
             state["temporary_base_candidate_set_id"]),
            (temp_set_id, candidate_id, seed.candidate_set_id),
            f"{scenario_id} temporary scenario bound")
        self.assertEqual(
            (record["scenario_mode"], record["presented"],
             record["tool_input"].get("scenario"),
             mocks["prepare_single_leg"].await_count),
            ("what_if", False, "what_if", 1), scenario_id)
        return events, trace, mocks, state, temp_set_id, candidate_id

    async def _accept_turn(self, *, mode, scenario_id, session, session_id,
                           candidate_id, turn_id="t2"):
        rounds = [_turn_round("present_route", "tu-accept",
                              {"candidate_id": candidate_id, "commit_scenario": True})]
        events, trace, mocks = await self._run_turn(
            session=session, session_id=session_id, message=ACCEPT_MESSAGE,
            rounds=rounds, mode=mode, turn_id=turn_id, mocks={})
        self._assert_turn_contract(
            scenario_id=scenario_id, events=events, trace=trace, mocks=mocks,
            mode=mode, expected_tools=("present_route",),
            expected_profile=ROUTE_NAVIGATION_TOOL_PROFILE,
            expect_card=1,
            expect_stored=0,
            model_calls=2)
        self.assertEqual(trace.tool_calls[0][1],
                         {
                             "candidate_id": candidate_id,
                             "commit_scenario": True,
                             "goal_key": "route",
                             "lead_in": "The route options were close, so I chose this one for your trip.",
                             "follow_up": "",
                             "reason_code": "meets_hard_constraints",
                         },
                         f"{scenario_id} commit input")

    async def _reject_turn(self, *, mode, scenario_id, session, session_id,
                           seed, state_before, preview_set_id,
                           preview_candidate_id, turn_id="t2"):
        events, trace, mocks = await self._run_turn(
            session=session, session_id=session_id, message=REJECT_MESSAGE,
            rounds=[
                _turn_round(
                    "complete_turn",
                    "tu-reject",
                    {
                        "goal_keys": ["route"],
                        "outcome": "cancelled",
                        "message": "OK, keeping the original trip.",
                    },
                )
            ], mode=mode,
            turn_id=turn_id, mocks={})
        self.assertEqual(
            (trace.tool_calls, self._offered(), route_cards(events)),
            ([
                (
                    "complete_turn",
                    {
                        "goal_keys": ["route"],
                        "outcome": "cancelled",
                        "message": "OK, keeping the original trip.",
                    },
                )
            ], _expected_initial_profile(ROUTE_NAVIGATION_TOOL_PROFILE), []),
            f"{scenario_id} terminal rejection response")
        state = trip_state_module.get_trip_state(session)
        self.assertEqual(
            ({key: state[key] for key in ACTIVE_KEYS},
             session["active_trip"]["card_id"]),
            ({key: state_before["trip_state"][key] for key in ACTIVE_KEYS},
             seed.card_id),
            f"{scenario_id} accepted selection preserved on reject")
        self._assert_temporary_clear(scenario_id, state)
        record = candidate_store.load_candidate_set(
            preview_set_id, session_id=session_id)
        self.assertEqual(
            (record["presented"], record["selected_candidate_id"],
             record["scenario_mode"]),
            (False, None, "what_if"), f"{scenario_id} unconsumed preview record")
        self.assertIn(preview_candidate_id,
                      [item.get("candidate_id") for item in record.get("candidates") or []],
                      f"{scenario_id} preview candidate stays auditable")
        self._assert_meta_done(scenario_id, events)
        self._assert_policy(scenario_id, mode, trace)

    async def _preview_reject(self, *, mode, scenario_id, message,
                              prepare_input, prepare_leg, candidate_id):
        session, session_id, seed = await self._seed_temporary(
            mode=mode, scenario_id=scenario_id, message=message,
            prepare_input=prepare_input, prepare_leg=prepare_leg,
            candidate_id=candidate_id)
        set_id, candidate_id, _record = capture_temporary_candidate(
            session, session_id)
        await self._reject_turn(
            mode=mode, scenario_id=scenario_id, session=session,
            session_id=session_id, seed=seed,
            state_before=self._projection(session),
            preview_set_id=set_id, preview_candidate_id=candidate_id)

    async def _selection_turn(self, *, mode, scenario_id, session, session_id,
                              tool_input, expected_place_id, turn_id="t2",
                              clock=None, fail_marker=None, before=None):
        state = trip_state_module.get_trip_state(session)
        set_id = str(
            tool_input.get("discovery_set_id")
            or state.get("active_discovery_set_id")
            or ""
        )
        place_id = str(tool_input.get("place_id") or "").strip()
        if not place_id and tool_input.get("description"):
            record = discovery_store.load_discovery_set(
                set_id,
                session_id=session_id,
            )
            requested = str(tool_input["description"]).strip().casefold()
            match = next(
                (
                    place
                    for place in (record or {}).get("places") or []
                    if str(place.get("name") or "").strip().casefold() == requested
                ),
                None,
            )
            place_id = str((match or {}).get("place_id") or "")
        if not place_id and tool_input.get("ordinal") is not None:
            record = discovery_store.load_discovery_set(set_id, session_id=session_id)
            places = (record or {}).get("places") or []
            index = int(tool_input["ordinal"]) - 1
            if 0 <= index < len(places):
                place_id = str(places[index].get("place_id") or "")
            else:
                place_id = "pl_missing_ordinal"
        rounds = [
            _turn_round(
                "present_places",
                "tu-ref",
                {
                    "discovery_set_id": set_id or "ds_missing",
                    "selections": [
                        {"place_id": place_id or "pl_missing", "reason": "preference_match"}
                    ],
                    "research_used": False,
                },
            )
        ]
        if fail_marker is not None:
            rounds.append(
                complete_turn_round(
                    "tu-ref-failed",
                    "That saved place reference is no longer available.",
                    outcome="clarification",
                )
            )
        events, trace, mocks = await self._run_turn(
            session=session, session_id=session_id, message=SELECT_SECOND_MESSAGE,
            rounds=rounds, mode=mode, turn_id=turn_id, mocks={}, clock=clock)
        expected_tools = (
            ("complete_turn",)
            if fail_marker is not None
            else ("present_places",)
        )
        self._assert_turn_contract(
            scenario_id=scenario_id, events=events, trace=trace, mocks=mocks,
            mode=mode,
            expected_tools=expected_tools,
            expected_profile=DISCOVERY_REFERENCE_TOOL_PROFILE,
            expect_card=0,
            expect_stored=0,
            model_calls=(None if fail_marker is not None else 2),
        )
        state = trip_state_module.get_trip_state(session)
        if fail_marker is not None:
            attempts = [
                attempt
                for attempt in trace.capability_attempts
                if attempt["capability"] == "present_places"
            ]
            self.assertTrue(attempts and attempts[0]["ok"] is False, scenario_id)
            self.assertNotIn(fail_marker, trace.final_text, scenario_id)
            self._assert_projection_unchanged(
                before, self._projection(session), scenario_id)
        else:
            self.assertEqual(state["selected_place_id"], expected_place_id,
                             f"{scenario_id} exact real place id bound")
        return events, trace, mocks, state

    async def _route_after_selection(self, *, mode, scenario_id, session,
                                     session_id, place2, candidate_id, set_id,
                                     turn_id="t3"):
        rounds = [_turn_round("prepare_route_options", "tu-prep",
                              {"destination": place2["name"],
                               "destination_place_id": place2["place_id"]}),
                  _turn_round("present_route", "tu-pres",
                              {"candidate_id": candidate_id})]
        events, trace, mocks = await self._run_turn(
            session=session, session_id=session_id,
            message=NAVIGATE_SELECTED_MESSAGE, rounds=rounds, mode=mode,
            turn_id=turn_id, prepare_leg=discovery_leg_for(place2),
            fixed_candidate_id=candidate_id, mocks={})
        self._assert_turn_contract(
            scenario_id=scenario_id, events=events, trace=trace, mocks=mocks,
            mode=mode, expected_tools=("prepare_route_options", "present_route"),
            expected_profile=ROUTE_NAVIGATION_TOOL_PROFILE, expect_card=1, expect_stored=1)
        self.assertEqual(trace.tool_calls[0][1]["destination_place_id"],
                         place2["place_id"],
                         f"{scenario_id} routes by the exact selected opaque id")
        state = trip_state_module.get_trip_state(session)
        self.assertEqual(
            (state["selected_place_id"], state["active_discovery_set_id"],
             state["destination"], state["selected_candidate_id"],
             mocks["prepare_single_leg"].await_count),
            (place2["place_id"], set_id, place2["name"], candidate_id, 1), scenario_id)


__all__ = ("ACCEPTED_KEYS", "ACTIVE_KEYS", "TEMPORARY_KEYS", "_PairwiseBase", "capture_temporary_candidate")
