"""Batch J2: cancellation/recovery races through the real loop.

Deterministic scenarios (synchronization events/futures only, no sleeps):

- J2-CANCEL-1: cancel/disconnect during real-loop canonical route preparation
  with a controllable genuine provider seam (Google-Routes recovery seam and
  live-MTA alerts seam inside the real ``prepare_route_options`` executor).
  No leaked child task and no candidate/card/destination/selection stale
  commit; a second same-session turn then succeeds via prepare -> present
  exactly once (Auto + Quick; Quick shares the identical cancellation path).
- J2-CANCEL-2: cancel a what-if preparation after seeding a coherent accepted
  trip; the accepted trip/card/set/selection stay unchanged, no temporary
  set/selection commits, and a later ordinary turn is usable.
- J2-CANCEL-3: cancel at route preparation from a real server-owned discovery
  set and selected canonical place; discovery identity may remain but no
  route candidate/card/destination selection partially commits, and a later
  retry with the same still-valid reference succeeds and records it.
- J2-CANCEL-5: a cancelled turn and an expired-deadline turn leave the session
  usable; stale events from those turns never mutate session state.

The real loop, registry, executors, candidate/discovery/trip stores, ledger,
and SSE events run untouched; only deterministic Anthropic rounds and the
genuine provider/data seams documented in the harness/fixtures modules are
scripted. No production, existing tests, or ledger are modified.
"""

from __future__ import annotations

import asyncio
import copy
from unittest.mock import patch

from app.services.agent import candidate_store
from app.services.agent import discovery_store
from app.services.agent import events as agent_events
from app.services.agent import trip_state as trip_state_module
from tests.conversation.conversation_cancellation_fixtures import (
    ACCEPTED_DESTINATION,
    CANDIDATE_V1,
    CANDIDATE_V3,
    CHANGE_ROUTE_MESSAGE,
    ROUTE_MESSAGE,
    WHAT_IF_CANCEL_MESSAGE,
    alerts_seam,
    route_seam,
)
from tests.conversation.conversation_cancellation_support import CancellationBase
from tests.conversation.conversation_matrix_harness import (
    _turn_round,
    load_agent_loop,
    make_leg,
    route_cards,
    run_turn,
    seed_accepted_active_trip,
)


INITIAL_TOOL_PROFILE = frozenset(
    {
        "declare_goals",
        "discover_places",
        "check_transit",
        "prepare_route_options",
        "complete_turn",
    }
)


def _goal_round(goal_key: str, kind: str, tool_name: str, tool_id: str, tool_input: dict) -> dict:
    """Declare the outcome before the first state-valid capability call."""

    declaration = {
        "id": f"{tool_id}-goals",
        "name": "declare_goals",
        "input": {
            "goals": [
                {"goal_key": goal_key, "kind": kind, "depends_on": []}
            ]
        },
    }
    capability_input = dict(tool_input)
    if tool_name == "prepare_route_options":
        for key in (
            "origin",
            "destination",
            "destination_place_id",
            "exclude_modes",
            "allowed_modes",
            "excluded_route_ids",
            "required_route_ids",
            "allowed_route_ids",
            "preferred_modes",
            "routing_preference",
            "departure_time",
            "arrival_by",
            "waypoints",
            "waypoint_dwell_minutes",
            "avoid_crowds",
            "avoid_stairs",
            "accessibility_required",
            "walking_tolerance_minutes",
            "what_if",
        ):
            capability_input.setdefault(key, None)
        capability_input.setdefault("activity_label", None)
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
        "input": {"goal_key": goal_key, **capability_input},
    }
    return {"tool_use": [declaration, capability], "stop_reason": "tool_use"}


def _present_route_round(tool_id: str, candidate_id: str, *, goal_key: str = "route") -> dict:
    return _turn_round(
        "present_route",
        tool_id,
        {
            "goal_key": goal_key,
            "candidate_id": candidate_id,
            "lead_in": "The route options were close, so I chose this one for your trip.",
            "follow_up": "",
            "reason_code": "meets_hard_constraints",
        },
    )


class _ModelLedCancellationMixin:
    """Route-turn helpers matching the state-valid eight-capability contract."""

    async def _natural_route_turn(
        self,
        *,
        session: dict,
        session_id: str,
        destination: str = ACCEPTED_DESTINATION,
        candidate_id: str = CANDIDATE_V1,
        message: str = ROUTE_MESSAGE,
        mode: str = "auto",
        turn_id: str = "t1",
        record_mark_presented: bool = False,
    ) -> tuple[list, object, dict, str]:
        rounds = [
            _goal_round(
                "route",
                "route",
                "prepare_route_options",
                f"tu-prep-{turn_id}",
                {"destination": destination},
            ),
            _present_route_round(f"tu-pres-{turn_id}", candidate_id),
        ]
        trace = self.loop.TurnTrace()
        mocks: dict = {}
        mark_patchers: list = []
        if record_mark_presented:
            original_mark = candidate_store.mark_presented

            def _mark(*args, **kwargs):
                result = original_mark(*args, **kwargs)
                return result

            mark_patchers.append(
                patch("app.services.agent.candidate_store.mark_presented", new=_mark)
            )
        for patcher in mark_patchers:
            patcher.start()
        try:
            events, trace = await run_turn(
                self.loop,
                session=session,
                session_id=session_id,
                message=message,
                rounds=rounds,
                mode=mode,
                trace=trace,
                mocks=mocks,
                turn_id=turn_id,
                prepare_leg=make_leg(destination=destination),
                fixed_candidate_id=candidate_id,
            )
        finally:
            for patcher in mark_patchers:
                patcher.stop()
        self._assert_model_led_route_success(
            scenario_id=f"natural-{turn_id}",
            events=events,
            trace=trace,
            mocks=mocks,
            session=session,
            session_id=session_id,
            mode=mode,
            destination=destination,
            candidate_id=candidate_id,
        )
        set_id = trip_state_module.get_trip_state(session)["active_candidate_set_id"]
        return events, trace, mocks, set_id

    def _assert_model_led_route_success(
        self,
        *,
        scenario_id: str,
        events: list,
        trace,
        mocks: dict,
        session: dict,
        session_id: str,
        mode: str,
        destination: str,
        candidate_id: str,
    ) -> None:
        self.assertEqual(
            [name for name, _input in trace.tool_calls],
            ["declare_goals", "prepare_route_options", "present_route"],
            f"{scenario_id} canonical declaration -> prepare -> present chain",
        )
        self.assertEqual(
            frozenset(schema["name"] for schema in self.loop.client.messages.calls[0]["tools"]),
            INITIAL_TOOL_PROFILE,
            f"{scenario_id} initial state-valid profile",
        )
        self.assertEqual(
            frozenset(schema["name"] for schema in self.loop.client.messages.calls[1]["tools"]),
            frozenset({"present_route", "complete_turn"}),
            f"{scenario_id} evidence-ready presenter profile",
        )
        cards = route_cards(events)
        self.assertEqual((len(cards), cards[0].role if cards else None), (1, "recommended"))
        state = trip_state_module.get_trip_state(session)
        set_id = state["active_candidate_set_id"]
        self.assertTrue(bool(set_id) and set_id.startswith("cs_"))
        self.assertEqual((state["destination"], state["selected_candidate_id"]), (destination, candidate_id))
        self.assertEqual(mocks["stored_candidate_set_ids"], [set_id])
        record = candidate_store.load_candidate_set(set_id, session_id=session_id)
        self.assertIsNotNone(record)
        self.assertTrue(record["presented"])
        self.assertEqual(record["selected_candidate_id"], candidate_id)
        self.assertEqual(mocks["prepare_single_leg"].await_count, 1)
        # Prepared candidates are immutable at presentation time; live route
        # enrichment is no longer part of this request-critical path.
        self.assertEqual(mocks["enrich_route"].await_count, 0)
        self.assertEqual(events[0].type, "meta")
        self.assertEqual(events[-1].type, "done")
        self.assertEqual(events[-1].stop_reason, "end_turn")


class CancellationDuringPrepareTests(_ModelLedCancellationMixin, CancellationBase):
    """J2-CANCEL-1: disconnect/cancel during canonical route preparation."""

    @classmethod
    def setUpClass(cls):
        cls.loop = load_agent_loop()

    async def _cancel1_flow(self, mode: str) -> None:
        scenario = f"J2-CANCEL-1-{mode}"
        session_id, session = self._new_session()
        baseline = set(asyncio.all_tasks())
        session_before = self._snapshot_session(session)
        started = asyncio.Event()
        cleaned = asyncio.Event()
        seam = await route_seam(started=started, cleaned=cleaned)
        rounds = [
            _goal_round(
                "route", "route", "prepare_route_options", "tu-prep-1",
                {"destination": ACCEPTED_DESTINATION},
            )
        ]
        order: list = []
        mocks: dict = {}
        chunks, request, save_mock, release_mock = await self._disconnected_stream(
            session=session,
            session_id=session_id,
            message=ROUTE_MESSAGE,
            rounds=rounds,
            seam_started=started,
            seam_cleaned=cleaned,
            seam_patchers=self._route_seam_patchers(seam),
            mocks=mocks,
            order=order,
            mode=mode,
            scenario_id=scenario,
        )
        # Transport contract: meta first; nothing terminal or committed leaks.
        self.assertEqual(
            chunks[0], agent_events.sse_format(
                agent_events.MetaEvent(session_id=session_id, turn_id="t1")),
            f"{scenario} meta frame first",
        )
        joined = "\n".join(chunks)
        for forbidden in ("route_card", "tool_end", "done", "error"):
            self.assertNotIn(
                forbidden, joined, f"{scenario} no {forbidden} frame after disconnect"
            )
        self.assertTrue(request.is_disconnected.await_count >= 1,
                        f"{scenario} disconnect detected")
        # Production ordering: turn finalization completes before save before
        # lease release (client disconnect mid-stream).
        self.assertEqual(order, ["finalize", "save", "release"], f"{scenario} order")
        self._assert_cancelled_no_commit(
            scenario_id=scenario,
            events=[],
            mocks=mocks,
            session=session,
            session_before=session_before,
            seam_cleaned=cleaned,
            destination=ACCEPTED_DESTINATION,
        )
        self.assertEqual(
            save_mock.call_args.args[1].get("route_cards") or [],
            [],
            f"{scenario} persisted session carries no stale card",
        )
        release_mock.assert_awaited_once()
        await self._assert_no_owned_pending_tasks(baseline)
        # A second turn in the SAME session succeeds prepare -> present once.
        await self._natural_route_turn(
            session=session,
            session_id=session_id,
            turn_id="t2",
            destination=ACCEPTED_DESTINATION,
            candidate_id=CANDIDATE_V1,
            mode=mode,
        )
        self.assertEqual(
            [card["card_id"] for card in session.get("route_cards") or []],
            [session["active_trip"]["card_id"]],
            f"{scenario} exactly one persisted card after recovery",
        )

    async def test_j2_cancel1_disconnect_then_same_session_recovers_auto(self):
        await self._cancel1_flow("auto")

    async def test_j2_cancel1_disconnect_then_same_session_recovers_quick(self):
        await self._cancel1_flow("quick")

    async def test_caller_cancellation_at_mta_seam_drains_and_recovers(self):
        """Caller cancellation lands after the evidence tasks exist."""

        session_id, session = self._new_session()
        baseline = set(asyncio.all_tasks())
        session_before = self._snapshot_session(session)
        started = asyncio.Event()
        cleaned = asyncio.Event()
        seam = await alerts_seam(started=started, cleaned=cleaned)
        rounds = [
            _goal_round(
                "route", "route", "prepare_route_options", "tu-prep-1",
                {"destination": ACCEPTED_DESTINATION},
            )
        ]
        mocks: dict = {}
        events, _trace, mocks = await self._cancelled_turn(
            session=session,
            session_id=session_id,
            message=ROUTE_MESSAGE,
            rounds=rounds,
            seam_started=started,
            seam_cleaned=cleaned,
            seam_patchers=self._alerts_seam_patchers(seam),
            mocks=mocks,
            scenario_id="J2-CANCEL-1-mta",
        )
        self.assertTrue(
            any(
                event.type == "tool_start"
                and event.tool == "prepare_route_options"
                for event in events
            ),
            "J2-CANCEL-1-mta real prepare started before cancellation",
        )
        self._assert_cancelled_no_commit(
            scenario_id="J2-CANCEL-1-mta",
            events=events,
            mocks=mocks,
            session=session,
            session_before=session_before,
            seam_cleaned=cleaned,
            destination=ACCEPTED_DESTINATION,
        )
        self.assertEqual(
            self._offered_profile(),
            INITIAL_TOOL_PROFILE,
            "J2-CANCEL-1-mta offered the initial state-valid profile",
        )
        await self._assert_no_owned_pending_tasks(baseline)
        await self._natural_route_turn(
            session=session,
            session_id=session_id,
            turn_id="t2",
            destination=ACCEPTED_DESTINATION,
            candidate_id=CANDIDATE_V1,
        )


class WhatIfCancellationTests(_ModelLedCancellationMixin, CancellationBase):
    """J2-CANCEL-2: cancel a what-if preparation, accepted trip survives."""

    @classmethod
    def setUpClass(cls):
        cls.loop = load_agent_loop()

    async def test_cancel_what_if_preparation_preserves_accepted_trip(self):
        session_id, session = self._new_session()
        # Origin "user" resolves from the turn's GPS (no geocoder) so the real
        # preparation path reaches the route seam offline; the accepted trip
        # identity the scenario must preserve is unchanged by the label.
        seed = seed_accepted_active_trip(session, session_id, origin="user")
        session_before = self._snapshot_session(session)
        record_before = self._snapshot_record(seed.candidate_set_id, session_id)
        baseline = set(asyncio.all_tasks())
        started = asyncio.Event()
        cleaned = asyncio.Event()
        seam = await route_seam(started=started, cleaned=cleaned)
        rounds = [
            _goal_round(
                "route", "route", "prepare_route_options", "tu-wa",
                {"destination": ACCEPTED_DESTINATION},
            )
        ]
        mocks: dict = {}
        events, _trace, mocks = await self._cancelled_turn(
            session=session,
            session_id=session_id,
            message=WHAT_IF_CANCEL_MESSAGE,
            rounds=rounds,
            seam_started=started,
            seam_cleaned=cleaned,
            seam_patchers=self._route_seam_patchers(seam),
            mocks=mocks,
            scenario_id="J2-CANCEL-2",
        )
        self._assert_cancelled_no_commit(
            scenario_id="J2-CANCEL-2",
            events=events,
            mocks=mocks,
            session=session,
            session_before=session_before,
            seam_cleaned=cleaned,
        )
        state = trip_state_module.get_trip_state(session)
        for key in ("temporary_candidate_set_id",
                    "temporary_selected_candidate_id",
                    "temporary_base_candidate_set_id"):
            self.assertIsNone(state[key], "J2-CANCEL-2 no temporary commit")
        self.assertEqual(
            (state["active_candidate_set_id"], state["selected_candidate_id"]),
            (seed.candidate_set_id, seed.candidate_id),
            "J2-CANCEL-2 accepted set/selection unchanged",
        )
        self.assertEqual(
            session.get("active_trip"), session_before["active_trip"],
            "J2-CANCEL-2 accepted trip unchanged",
        )
        self.assertEqual(
            self._snapshot_record(seed.candidate_set_id, session_id),
            record_before,
            "J2-CANCEL-2 accepted store record unchanged",
        )
        await self._assert_no_owned_pending_tasks(baseline)
        # A later ordinary replan of the accepted trip remains usable and
        # commits exactly once; the seed card survives until that commit.
        await self._natural_route_turn(
            session=session,
            session_id=session_id,
            turn_id="t2",
            message=CHANGE_ROUTE_MESSAGE,
            destination="Work",
            candidate_id=CANDIDATE_V1,
        )
        self.assertEqual(
            [card["card_id"] for card in session.get("route_cards") or []],
            [seed.card_id, session["active_trip"]["card_id"]],
            "J2-CANCEL-2 seed card survives until the later turn commits",
        )


class DiscoveryCancellationTests(_ModelLedCancellationMixin, CancellationBase):
    """J2-CANCEL-3: cancel route preparation from a real discovery selection."""

    @classmethod
    def setUpClass(cls):
        cls.loop = load_agent_loop()

    async def _seed_discovery(
        self, session_id: str, session: dict
    ) -> tuple[str, str]:
        set_id = discovery_store.store_discovery_set(
            session_id=session_id,
            places=[
                {"name": "Barclays Center",
                 "address": "620 Atlantic Ave, Brooklyn, NY 11217",
                 "latitude": 40.6826, "longitude": -73.9754,
                 "category": "stadium"}
            ],
            query="barclays center",
        )
        record = discovery_store.load_discovery_set(set_id, session_id=session_id)
        place_id = record["places"][0]["place_id"]
        trip_state_module.bind_discovery_context(
            session,
            discovery_set_id=set_id,
            selected_place_id=place_id,
        )
        return set_id, place_id

    async def test_cancel_preparation_from_discovery_selection_then_retry(self):
        session_id, session = self._new_session()
        set_id, place_id = await self._seed_discovery(session_id, session)
        session_before = self._snapshot_session(session)
        discovery_before = copy.deepcopy(
            discovery_store.load_discovery_set(set_id, session_id=session_id)
        )
        baseline = set(asyncio.all_tasks())
        started = asyncio.Event()
        cleaned = asyncio.Event()
        seam = await route_seam(started=started, cleaned=cleaned)
        rounds = [
            _goal_round(
                "route", "route", "prepare_route_options", "tu-prep",
                {"destination": ACCEPTED_DESTINATION,
                 "destination_place_id": place_id},
            )
        ]
        mocks: dict = {}
        events, _trace, mocks = await self._cancelled_turn(
            session=session,
            session_id=session_id,
            message=CHANGE_ROUTE_MESSAGE,
            rounds=rounds,
            seam_started=started,
            seam_cleaned=cleaned,
            seam_patchers=self._route_seam_patchers(seam),
            mocks=mocks,
            scenario_id="J2-CANCEL-3",
        )
        self._assert_cancelled_no_commit(
            scenario_id="J2-CANCEL-3",
            events=events,
            mocks=mocks,
            session=session,
            session_before=session_before,
            seam_cleaned=cleaned,
            destination=ACCEPTED_DESTINATION,
        )
        state = trip_state_module.get_trip_state(session)
        # Discovery identity may remain; no route identity partially commits.
        self.assertEqual(state["active_discovery_set_id"], set_id)
        self.assertEqual(state["selected_place_id"], place_id)
        self.assertEqual(
            discovery_store.load_discovery_set(set_id, session_id=session_id),
            discovery_before,
            "J2-CANCEL-3 discovery record untouched",
        )
        await self._assert_no_owned_pending_tasks(baseline)
        # Retry with the same still-valid reference succeeds through the
        # canonical chain and records the reference in the store.
        rounds2 = [
            _goal_round(
                "route", "route", "prepare_route_options", "tu-prep2",
                {"destination": ACCEPTED_DESTINATION,
                 "destination_place_id": place_id},
            ),
            _present_route_round("tu-pres2", CANDIDATE_V3),
        ]
        trace = self.loop.TurnTrace()
        mocks2: dict = {}
        events2, trace = await run_turn(
            self.loop,
            session=session,
            session_id=session_id,
            message=CHANGE_ROUTE_MESSAGE,
            rounds=rounds2,
            mode="auto",
            trace=trace,
            mocks=mocks2,
            turn_id="t2",
            prepare_leg=make_leg(destination=ACCEPTED_DESTINATION),
            fixed_candidate_id=CANDIDATE_V3,
        )
        self._assert_model_led_route_success(
            scenario_id="J2-CANCEL-3-retry",
            events=events2,
            trace=trace,
            mocks=mocks2,
            session=session,
            session_id=session_id,
            mode="auto",
            destination=ACCEPTED_DESTINATION,
            candidate_id=CANDIDATE_V3,
        )
        new_set_id = trip_state_module.get_trip_state(session)[
            "active_candidate_set_id"
        ]
        new_record = candidate_store.load_candidate_set(
            new_set_id, session_id=session_id
        )
        self.assertEqual(new_record["discovery_set_id"], set_id)
        self.assertEqual(new_record["destination_place_id"], place_id)


class DeadlineRecoveryTests(_ModelLedCancellationMixin, CancellationBase):
    """J2-CANCEL-5: cancelled/expired-deadline turns leave a usable session."""

    @classmethod
    def setUpClass(cls):
        cls.loop = load_agent_loop()

    async def test_cancelled_and_deadline_turns_do_not_poison_next_turn(self):
        session_id, session = self._new_session()
        baseline = set(asyncio.all_tasks())
        session_before = self._snapshot_session(session)
        # 1) Caller cancellation during real canonical preparation.
        started = asyncio.Event()
        cleaned = asyncio.Event()
        seam = await route_seam(started=started, cleaned=cleaned)
        rounds = [
            _goal_round(
                "route", "route", "prepare_route_options", "tu-prep",
                {"destination": ACCEPTED_DESTINATION},
            )
        ]
        mocks: dict = {}
        stale_events, _trace, mocks = await self._cancelled_turn(
            session=session,
            session_id=session_id,
            message=ROUTE_MESSAGE,
            rounds=rounds,
            seam_started=started,
            seam_cleaned=cleaned,
            seam_patchers=self._route_seam_patchers(seam),
            mocks=mocks,
            turn_id="t1",
            scenario_id="J2-CANCEL-5-cancel",
        )
        self._assert_cancelled_no_commit(
            scenario_id="J2-CANCEL-5-cancel",
            events=stale_events,
            mocks=mocks,
            session=session,
            session_before=session_before,
            seam_cleaned=cleaned,
            destination=ACCEPTED_DESTINATION,
        )
        # 2) Expired-deadline turn: no model call, no tool, terminal deadline.
        with patch.object(self.loop, "AGENT_TURN_DEADLINE_S", -1.0):
            deadline_events, _deadline_trace = await run_turn(
                self.loop,
                session=session,
                session_id=session_id,
                message="What is the subway fare?",
                rounds=[],
                mode="auto",
                trace=self.loop.TurnTrace(),
                mocks={},
                turn_id="t2",
            )
        self.assertEqual(
            [event.type for event in deadline_events],
            ["meta", "reasoning", "done"],
            "J2-CANCEL-5 deadline turn emits working state then done",
        )
        self.assertEqual(deadline_events[-1].stop_reason, "deadline")
        self.assertEqual(
            len(self.loop.client.messages.calls),
            0, "J2-CANCEL-5 deadline turn never calls the model",
        )
        self.assertEqual(
            self._snapshot_session(session),
            session_before, "J2-CANCEL-5 deadline turn mutates no route state",
        )
        # 3) A normal turn still succeeds exactly once; stale events from the
        # cancelled and deadline turns leave no residue.
        events3, _trace3, _mocks3, set3 = await self._natural_route_turn(
            session=session,
            session_id=session_id,
            turn_id="t3",
            destination=ACCEPTED_DESTINATION,
            candidate_id=CANDIDATE_V1,
        )
        self.assertEqual(len(route_cards(events3)), 1)
        self.assertEqual(
            [card["card_id"] for card in session.get("route_cards") or []],
            [session["active_trip"]["card_id"]],
            "J2-CANCEL-5 only the final turn's card persists",
        )
        self.assertEqual(
            trip_state_module.get_trip_state(session)["active_candidate_set_id"],
            set3, "J2-CANCEL-5 final turn owns the active set",
        )
        self.assertEqual(
            [event.type for event in stale_events if event.type == "route_card"],
            [], "J2-CANCEL-5 stale cancelled-turn events carry no card",
        )
        await self._assert_no_owned_pending_tasks(baseline)


__all__ = ()
