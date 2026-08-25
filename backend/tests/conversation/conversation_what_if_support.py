"""Batch B support for deterministic what-if lifecycle scenarios.

Non-test module (no ``Test*``/``test_*`` names): pytest never collects it.
Shares ``_WhatIfLifecycleBase`` for B-TEMP-*, B-BUS-*, B-REPLACEMENT,
B-UNRELATED (Auto + Quick). Every scenario drives the *real* loop with the
*real* registered ``prepare_route_options`` / ``present_route`` executors and
the *real* candidate store; only the narrow provider/data seams of
``tests.conversation.conversation_matrix_harness`` are scripted, and Anthropic inference is
deterministic mock text. The evidence-ready preview ``present_route`` input
uses the harness's documented ``new_candidate_id`` seam; follow-up turns read
the exact identity back from the real store.
"""

from __future__ import annotations

import secrets
import unittest

from app.services.agent import candidate_store
from app.services.agent import trip_state as trip_state_module
from tests.conversation.conversation_matrix_harness import (
    _turn_round,
    clear_caches,
    make_leg,
    new_session,
    policy_model,
    route_cards,
    run_turn,
    seed_accepted_active_trip,
)

TEMPORAL_DEPARTURE = "2026-08-06T12:30:00-04:00"  # now + 30 minutes
TEMPORAL_MESSAGE = "What if I leave 30 minutes later?"
BUS_MESSAGE = "What if I take the bus?"
ACCEPT_MESSAGE = "Use that instead."
REJECT_MESSAGE = "Never mind."
UNRELATED_MESSAGE = "What is the subway fare?"
STALE_PROBE_MESSAGE = "Show me the first option."
FIXED_PREVIEW_1 = "cd_b2_preview_1"
FIXED_PREVIEW_2 = "cd_b2_preview_2"
INITIAL_TOOL_PROFILE = frozenset(
    {
        "declare_goals",
        "discover_places",
        "check_transit",
        "prepare_route_options",
        "complete_turn",
    }
)
FORBIDDEN = (
    "plan_trip", "web_search", "search_local_places", "event_lookup",
    "transit_snapshot", "lookup_arrivals", "lookup_facts",
    "venue_crowd_window", "check_area_conditions", "poi_search",
)
ACTIVE_KEYS = (
    "origin", "destination", "waypoints", "planning_mode",
    "requested_departure", "requested_arrival", "active_candidate_set_id",
    "selected_candidate_id", "preferences",
)
TEMPORAL_INPUT = {"origin": "Home", "destination": "Work", "departure_time": TEMPORAL_DEPARTURE}
BUS_INPUT = {"origin": "Home", "destination": "Work", "preferred_modes": ["BUS"]}


def temporal_leg(destination: str = "Work"):
    return make_leg(route_ids=("Q",), destination=destination)


def bus_leg(destination: str = "Work"):
    leg = make_leg(route_ids=("Q",), destination=destination)
    leg.parsed_routes = [[
        {"type": "WALK", "duration_seconds": 120,
         "departure_time_iso": "2026-08-06T12:00:00-04:00",
         "arrival_time_iso": "2026-08-06T12:02:00-04:00"},
        {"type": "BUS", "route_id": "B38", "duration_seconds": 1560,
         "departure_stop": "Home St", "arrival_stop": destination,
         "departure_time_iso": "2026-08-06T12:05:00-04:00",
         "arrival_time_iso": "2026-08-06T12:31:00-04:00"},
    ]]
    leg.scored = [{
        "index": 0, "score": 21, "total_minutes": 31,
        "transfers": 0, "alert_count": 0, "transit_count": 1,
        "event_crowd_penalty": 0, "rank": 1,
    }]
    return leg


def capture_temporary_candidate(session: dict, session_id: str):
    """Read the real store record for the bound temporary scenario."""

    state = trip_state_module.get_trip_state(session)
    set_id = state["temporary_candidate_set_id"]
    record = candidate_store.load_candidate_set(set_id, session_id=session_id)
    entry = next((c for c in (record or {}).get("candidates") or [] if isinstance(c, dict)), None)
    if record is None or entry is None:
        raise AssertionError(f"temporary candidate set {set_id} not stored")
    return set_id, str(entry["candidate_id"]), record


def _goal_block(tool_id: str, *, goal_key: str = "route", kind: str = "route") -> dict:
    return {
        "id": tool_id,
        "name": "declare_goals",
        "input": {
            "goals": [{"goal_key": goal_key, "kind": kind, "depends_on": []}]
        },
    }


def _declared_route_round(tool_id: str, tool_input: dict) -> dict:
    """Declare the route outcome before preparing a what-if candidate."""

    route_input = {"goal_key": "route", "what_if": True, **tool_input}
    has_explicit_destination = bool(
        route_input.get("destination") or route_input.get("destination_place_id")
    )
    route_input.setdefault(
        "destination_source",
        "current_turn" if has_explicit_destination else "accepted_trip",
    )

    return {
        "tool_use": [
            _goal_block(f"{tool_id}-goals"),
            {
                "id": tool_id,
                "name": "prepare_route_options",
                "input": route_input,
            },
        ],
        "stop_reason": "tool_use",
    }


def _declared_route_only_round(tool_id: str = "tu-goals") -> dict:
    return {"tool_use": [_goal_block(tool_id)], "stop_reason": "tool_use"}


def _present_route_round(
    tool_id: str,
    candidate_id: str,
    *,
    commit_scenario: bool = False,
) -> dict:
    tool_input = {"goal_key": "route", "candidate_id": candidate_id}
    if commit_scenario:
        tool_input["commit_scenario"] = True
    return _turn_round("present_route", tool_id, tool_input)


def _cancelled_route_round(tool_id: str = "tu-cancel") -> dict:
    return {
        "tool_use": [
            _goal_block(f"{tool_id}-goals"),
            {
                "id": tool_id,
                "name": "complete_turn",
                "input": {
                    "goal_keys": ["route"],
                    "outcome": "cancelled",
                    "message": "Okay, keeping the original trip.",
                },
            },
        ],
        "stop_reason": "tool_use",
    }


def _answered_general_round(tool_id: str = "tu-answer") -> dict:
    return {
        "tool_use": [
            _goal_block(
                f"{tool_id}-goals",
                goal_key="response",
                kind="general_response",
            ),
            {
                "id": tool_id,
                "name": "complete_turn",
                "input": {
                    "goal_keys": ["response"],
                    "outcome": "answer",
                    "message": "The standard subway fare is $2.90.",
                },
            },
        ],
        "stop_reason": "tool_use",
    }


class _WhatIfLifecycleBase(unittest.IsolatedAsyncioTestCase):
    """Shared invariants for the Batch B what-if lifecycle scenarios."""

    loop = None  # set in setUpClass by subclasses

    def setUp(self):
        clear_caches()

    def _seed(self, mode: str):
        session_id = f"sess-{mode}-{secrets.token_hex(4)}"
        _sid, session = new_session()
        seed = seed_accepted_active_trip(session, session_id)
        return session, session_id, seed, trip_state_module.get_trip_state(session)

    async def _preview_turn(self, *, session, session_id, mode, message,
                            prepare_input, prepare_leg, candidate_id):
        rounds = [
            _declared_route_round("tu-prepare", prepare_input),
            _present_route_round("tu-preview", candidate_id),
        ]
        trace = self.loop.TurnTrace()
        mocks = {}
        events, trace = await run_turn(self.loop, session=session, session_id=session_id,
                                       message=message, rounds=rounds, mode=mode, prepare_leg=prepare_leg,
                                       fixed_candidate_id=candidate_id, trace=trace, mocks=mocks, turn_id="t1")
        return events, trace, mocks

    async def _plain_turn(self, *, session, session_id, message, rounds, mode, turn_id):
        trace = self.loop.TurnTrace()
        return await run_turn(self.loop, session=session, session_id=session_id, message=message,
                              rounds=rounds, mode=mode, trace=trace, mocks={}, turn_id=turn_id)

    async def _accept_turn(self, *, session, session_id, mode, candidate_id, turn_id="t2"):
        rounds = [
            _declared_route_only_round("tu-accept-goals"),
            _present_route_round(
                "tu-accept",
                candidate_id,
                commit_scenario=True,
            ),
        ]
        return await self._plain_turn(session=session, session_id=session_id, message=ACCEPT_MESSAGE,
                                      rounds=rounds, mode=mode, turn_id=turn_id)

    async def _reject_turn(self, *, session, session_id, mode, turn_id="t2"):
        return await self._plain_turn(session=session, session_id=session_id, message=REJECT_MESSAGE,
                                      rounds=[_cancelled_route_round()], mode=mode, turn_id=turn_id)

    async def _stale_probe(self, *, session, session_id, mode, candidate_id, turn_id="t3"):
        rounds = [
            _declared_route_only_round("tu-stale-goals"),
            _present_route_round("tu-stale", candidate_id),
        ]
        return await self._plain_turn(session=session, session_id=session_id, message=STALE_PROBE_MESSAGE,
                                      rounds=rounds, mode=mode, turn_id=turn_id)

    async def _temporal_preview(self, *, mode, scenario_id, candidate_id=FIXED_PREVIEW_1,
                                session=None, session_id=None, seed=None, state_before=None):
        if session is None:
            session, session_id, seed, state_before = self._seed(mode)
        events, trace, mocks = await self._preview_turn(
            session=session, session_id=session_id, mode=mode, message=TEMPORAL_MESSAGE,
            prepare_input={"destination": seed.destination, "departure_time": TEMPORAL_DEPARTURE},
            prepare_leg=temporal_leg(seed.destination), candidate_id=candidate_id)
        self._assert_preview(scenario_id=scenario_id, events=events, trace=trace, mocks=mocks,
                             session=session, session_id=session_id, seed=seed, state_before=state_before,
                             mode=mode, expected_prepare_input=TEMPORAL_INPUT,
                             expected_preference_patch={"avoid_crowds": False},
                             candidate_id=candidate_id)
        self.assertEqual(trace.tool_calls[1][1]["departure_time"], TEMPORAL_DEPARTURE, f"{scenario_id} later departure")
        self.assertEqual(route_cards(events)[0].depart_iso, TEMPORAL_DEPARTURE, f"{scenario_id} card departure")
        return session, session_id, seed

    async def _bus_preview(self, *, mode, scenario_id, candidate_id=FIXED_PREVIEW_1,
                           session=None, session_id=None, seed=None, state_before=None):
        if session is None:
            session, session_id, seed, state_before = self._seed(mode)
        events, trace, mocks = await self._preview_turn(
            session=session, session_id=session_id, mode=mode, message=BUS_MESSAGE,
            prepare_input={"destination": seed.destination, "preferred_modes": ["BUS"]},
            prepare_leg=bus_leg(seed.destination), candidate_id=candidate_id)
        self._assert_preview(scenario_id=scenario_id, events=events, trace=trace, mocks=mocks,
                             session=session, session_id=session_id, seed=seed, state_before=state_before,
                             mode=mode, expected_prepare_input=BUS_INPUT,
                             expected_preference_patch={"avoid_crowds": False,
                                                        "preferred_modes": ["BUS"]},
                             candidate_id=candidate_id)
        state = trip_state_module.get_trip_state(session)
        self.assertEqual(state["preferences"], state_before["preferences"], f"{scenario_id} active preferences unchanged")
        self.assertEqual(session["profile"]["preferences"]["preferred_modes"], [], f"{scenario_id} profile preferences unchanged")
        self.assertEqual(session.get("slots"), {}, f"{scenario_id} slots unchanged")
        _set_id, _candidate_id, record = capture_temporary_candidate(session, session_id)
        self.assertEqual(record["tool_input"]["preferred_modes"], ["BUS"], f"{scenario_id} record owns the BUS patch")
        return session, session_id, seed

    def _assert_preview(self, *, scenario_id, events, trace, mocks, session, session_id, seed,
                        state_before, mode, expected_prepare_input, expected_preference_patch, candidate_id):
        names = [name for name, _input in trace.tool_calls]
        self.assertEqual(
            names,
            ["declare_goals", "prepare_route_options", "present_route"],
            f"{scenario_id} tools",
        )
        self.assertFalse(set(names) & set(FORBIDDEN), f"{scenario_id} forbidden tool")
        seam = mocks["prepare_single_leg"].await_args.args[0]
        for key, value in expected_prepare_input.items():
            self.assertEqual(seam.get(key), value, f"{scenario_id} seam[{key}]")
        self.assertEqual(seam["scenario"], "what_if", f"{scenario_id} scenario")
        self.assertEqual(trace.tool_calls[1][1]["what_if"], True, f"{scenario_id} constrained what_if")
        self.assertEqual(
            trace.tool_calls[2][1],
            {
                "goal_key": "route",
                "candidate_id": candidate_id,
                "lead_in": "The route options were close, so I chose this one for your trip.",
                "follow_up": "",
                "reason_code": "meets_hard_constraints",
            },
            f"{scenario_id} preview present input",
        )
        expected_mode, expected_model = policy_model(self.loop, mode)
        self.assertEqual((trace.initial_mode, trace.final_mode), (expected_mode, expected_mode), scenario_id)
        self.assertEqual([call["model"] for call in self.loop.client.messages.calls], [expected_model, expected_model], f"{scenario_id} models")
        self.assertEqual(
            {schema["name"] for schema in self.loop.client.messages.calls[0]["tools"]},
            INITIAL_TOOL_PROFILE, f"{scenario_id} tool profile",
        )
        ready_profile = {
            schema["name"] for schema in self.loop.client.messages.calls[1]["tools"]
        }
        self.assertEqual(ready_profile, {"complete_turn", "present_route"},
                         f"{scenario_id} ready-evidence profile")
        self.assertEqual(trace.model_call_count, 2, scenario_id)
        self.assertEqual((events[0].type, events[-1].type, events[-1].stop_reason), ("meta", "done", "end_turn"), scenario_id)
        cards = route_cards(events)
        self.assertEqual([(len(cards), cards[0].role if cards else None)], [(1, "recommended")], f"{scenario_id} preview card")
        state = trip_state_module.get_trip_state(session)
        self.assertEqual({key: state[key] for key in ACTIVE_KEYS}, {key: state_before[key] for key in ACTIVE_KEYS}, f"{scenario_id} active facts")
        self.assertEqual(session["active_trip"]["card_id"], seed.card_id, scenario_id)
        self.assertEqual([card["card_id"] for card in session["route_cards"]], [seed.card_id], f"{scenario_id} card not persisted")
        set_id, stored_id, record = capture_temporary_candidate(session, session_id)
        self.assertEqual(stored_id, candidate_id, scenario_id)
        self.assertEqual(state["temporary_candidate_set_id"], set_id, scenario_id)
        self.assertEqual(state["temporary_selected_candidate_id"], candidate_id, scenario_id)
        self.assertEqual(state["temporary_base_candidate_set_id"], seed.candidate_set_id, scenario_id)
        self.assertEqual(record["scenario_mode"], "what_if", scenario_id)
        self.assertFalse(record["presented"], f"{scenario_id} unconsumed")
        self.assertIsNone(record["selected_candidate_id"], scenario_id)
        self.assertEqual(record["tool_input"]["scenario"], "what_if", scenario_id)
        self.assertEqual(record["tool_input"].get("preference_patch"), expected_preference_patch, f"{scenario_id} patch")
        self.assertEqual(mocks["prepare_single_leg"].await_count, 1, scenario_id)
        # Candidate evidence is finalized by ``prepare_route_options``.  The
        # canonical presenter consumes that stored evidence and must not
        # re-enter the retired route-enrichment seam.
        self.assertEqual(mocks["enrich_route"].await_count, 0, scenario_id)
        self.assertEqual(mocks["lookup_arrivals"].await_count, 0, scenario_id)
        self.assertEqual(len(mocks["stored_candidate_set_ids"]), 1, scenario_id)
        lowered = trace.final_text.casefold()
        for marker in ("cd_", "cs_", "rc_"):
            self.assertNotIn(marker, lowered, f"{scenario_id} leaked id")

    def _assert_accept_gaps(self, *, scenario_id, session_id, mode, candidate_id):
        """Prove the accept turn exposes the stable public surface and the
        exact session-owned temporary identity, then commits without
        re-preparing. Stateless intent is evidence only; temporary state is
        authoritative."""

        calls = self.loop.client.messages.calls
        initial_offered = {schema["name"] for schema in calls[0]["tools"]}
        offered = sorted({schema["name"] for schema in calls[-1]["tools"]})
        context = str(calls[0]["messages"][-1]["content"])
        self.assertEqual(calls[0]["model"], policy_model(self.loop, mode)[1],
                         f"{scenario_id} accept model")
        with self.subTest(gap="accept_model_request"):
            self.assertEqual(
                (initial_offered,
                 set(offered),
                 candidate_id in context),
                (INITIAL_TOOL_PROFILE,
                 {
                     "complete_turn",
                     "discover_places",
                     "prepare_route_options",
                     "present_route",
                 },
                 True),
                f"{scenario_id} accept request evidence: "
                f"offered_tools={offered}; "
                f"initial_offered={sorted(initial_offered)}; "
                f"state_valid_offered={offered}; "
                f"temporary_identity_in_context={candidate_id in context}; "
                f"context_tail={context[-500:]!r}",
            )

    def _assert_accept_commit(self, *, scenario_id, events, trace, session, session_id, seed,
                              mode, candidate_id, set_id, expected_state):
        names = [name for name, _input in trace.tool_calls]
        self.assertEqual(
            names,
            ["declare_goals", "present_route"],
            f"{scenario_id} accept tools",
        )
        self.assertEqual(
            trace.tool_calls[1][1],
            {
                "goal_key": "route",
                "candidate_id": candidate_id,
                "commit_scenario": True,
                "lead_in": "The route options were close, so I chose this one for your trip.",
                "follow_up": "",
                "reason_code": "meets_hard_constraints",
            },
            f"{scenario_id} present input",
        )
        expected_mode, expected_model = policy_model(self.loop, mode)
        self.assertEqual((trace.initial_mode, trace.final_mode, trace.model_call_count), (expected_mode, expected_mode, 2), scenario_id)
        self.assertEqual([call["model"] for call in self.loop.client.messages.calls], [expected_model, expected_model], f"{scenario_id} accept model")
        self.assertEqual((events[0].type, events[-1].type), ("meta", "done"), scenario_id)
        cards = route_cards(events)
        self.assertEqual([(len(cards), cards[0].role if cards else None)], [(1, "recommended")], f"{scenario_id} commit card")
        committed_card_id = cards[0].card_id
        state = trip_state_module.get_trip_state(session)
        self.assertEqual(state["active_candidate_set_id"], set_id, scenario_id)
        self.assertEqual(state["selected_candidate_id"], candidate_id, scenario_id)
        temp_ids = (
            state["temporary_candidate_set_id"],
            state["temporary_selected_candidate_id"],
            state["temporary_base_candidate_set_id"],
        )
        self.assertEqual(temp_ids, (None, None, None), f"{scenario_id} temp cleared")
        for key, value in expected_state.items():
            self.assertEqual(state[key], value, f"{scenario_id} committed[{key}]")
        record = candidate_store.load_candidate_set(set_id, session_id=session_id)
        self.assertTrue(record["presented"], f"{scenario_id} consumed once")
        self.assertEqual(record["selected_candidate_id"], candidate_id, scenario_id)
        original = candidate_store.load_candidate_set(seed.candidate_set_id, session_id=session_id)
        self.assertTrue(original["presented"], scenario_id)
        self.assertEqual(original["selected_candidate_id"], seed.candidate_id, scenario_id)
        self.assertEqual(session["active_trip"]["card_id"], committed_card_id, scenario_id)
        self.assertEqual([card["card_id"] for card in session["route_cards"]],
                         [seed.card_id, committed_card_id],
                         f"{scenario_id} one persisted committed card")

    def _assert_reject(self, *, scenario_id, events, trace, session, session_id, seed,
                       state_before, preview_set_id, preview_candidate_id):
        self.assertEqual(
            trace.tool_calls,
            [
                (
                    "declare_goals",
                    {
                        "goals": [
                            {
                                "goal_key": "route",
                                "kind": "route",
                                "depends_on": [],
                            }
                        ]
                    },
                ),
                (
                    "complete_turn",
                    {
                        "goal_keys": ["route"],
                        "outcome": "cancelled",
                        "message": "Okay, keeping the original trip.",
                    },
                ),
            ],
            f"{scenario_id} cancellation tools",
        )
        self.assertEqual((events[0].type, events[-1].type), ("meta", "done"), scenario_id)
        self.assertEqual(route_cards(events), [], scenario_id)
        state = trip_state_module.get_trip_state(session)
        self.assertEqual({key: state[key] for key in ACTIVE_KEYS}, {key: state_before[key] for key in ACTIVE_KEYS}, f"{scenario_id} active facts")
        self.assertEqual(session["active_trip"]["card_id"], seed.card_id, scenario_id)
        self.assertEqual([card["card_id"] for card in session["route_cards"]],
                         [seed.card_id], scenario_id)
        with self.subTest(gap="reject_clears_temporary"):
            self.assertEqual(
                (state["temporary_candidate_set_id"],
                 state["temporary_selected_candidate_id"],
                 state["temporary_base_candidate_set_id"]),
                (None, None, None),
                f"{scenario_id} reject clears temporary",
            )
        record = candidate_store.load_candidate_set(preview_set_id, session_id=session_id)
        self.assertFalse(record["presented"], f"{scenario_id} unconsumed")
        self.assertIsNone(record["selected_candidate_id"], scenario_id)
        self.assertEqual(record["scenario_mode"], "what_if", scenario_id)
        self.assertIn(preview_candidate_id,
                      [item.get("candidate_id") for item in record.get("candidates") or []],
                      f"{scenario_id} preview candidate auditable")

    async def _executor_eligibility_probe(
        self, *, scenario_id, session, session_id, mode, seed, candidate_id,
        turn_id="t3",
    ):
        """Guarded stale-candidate probe (executor eligibility). Scripts a
        ``present_route`` call the accept turn was not offered, so it proves
        only that the real executor stays promotable when invoked with the old
        candidate -- not that ordinary conversation can reach it."""

        rounds = [
            _declared_route_only_round("tu-probe-goals"),
            _present_route_round(
                "tu-probe",
                candidate_id,
                commit_scenario=True,
            ),
        ]
        events, trace = await self._plain_turn(
            session=session, session_id=session_id, message=ACCEPT_MESSAGE,
            rounds=rounds, mode=mode, turn_id=turn_id,
        )
        state = trip_state_module.get_trip_state(session)
        attempts = [
            attempt
            for attempt in trace.capability_attempts
            if attempt["capability"] == "present_route"
        ]
        actual_ok = attempts[0]["ok"] if attempts else None
        self.assertTrue(
            attempts and attempts[0]["ok"] is False,
            f"{scenario_id} guarded stale-candidate probe: after rejection the "
            f"server-owned temporary binding must be cleared and the real "
            f"present executor invoked with the old candidate must fail; "
            f"actual ok={actual_ok} (discard did not make the candidate "
            f"ineligible; compounds the accept-path defect but is not evidence "
            f"that the offered tool schema can reach it); "
            f"active_candidate_set_id={state['active_candidate_set_id']}; "
            f"temporary_candidate_set_id={state['temporary_candidate_set_id']}; "
            f"route_card_events={len(route_cards(events))}",
        )
        self.assertEqual(
            state["active_candidate_set_id"], seed.candidate_set_id,
            f"{scenario_id} probe must not promote; "
            f"actual={state['active_candidate_set_id']}",
        )
        self.assertEqual(route_cards(events), [], f"{scenario_id} probe must not emit a card")
        return events
