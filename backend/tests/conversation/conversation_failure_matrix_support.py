"""Batch J1 support: real-loop runner and shared failure invariants.

Non-test module (no ``Test*``/``test_*`` names): pytest never collects it.
``run_failure_turn`` mirrors ``tests.conversation.conversation_matrix_harness`` but accepts
an arbitrary ``prepare_single_leg`` mock (so the provider seam can *raise*
timeout/exception shapes), a ``gtfs`` object for the real arrivals lookup,
and extra provider-seam patches for status/arrival/discovery tools. The
canonical registry executors, stores, loop, and SSE projection always run
untouched.
"""

from __future__ import annotations

import copy
import secrets
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from app.services.agent import candidate_store
from app.services.agent import trip_state as trip_state_module
from app.services.agent.tools._types import ToolResult

from tests.conversation.conversation_failure_matrix_fixtures import (
    FAILURE_TEXT,
    prepare_present_rounds,
    prepare_rounds,
)
from tests.conversation.conversation_matrix_harness import (
    clear_caches,
    new_session,
    policy_model,
    route_cards,
    seed_accepted_active_trip,
)


FORBIDDEN_TOOL_NAMES = (
    "plan_trip", "web_search", "search_local_places", "get_place_details",
    "event_lookup", "transit_snapshot", "lookup_arrivals", "lookup_facts",
    "accessibility_status", "venue_crowd_window", "check_area_conditions",
    "poi_search",
)


def prepare_mock_with_side_effect(exception: BaseException) -> AsyncMock:
    """AsyncMock that raises the provider failure inside the real executor."""

    return AsyncMock(side_effect=exception)


def prepare_mock_returning(value: object) -> AsyncMock:
    """AsyncMock that yields one provider leg/result to the real executor."""

    return AsyncMock(return_value=value)


def _complete_public_inputs(rounds: list[dict]) -> list[dict]:
    """Fill strict-schema fields omitted by compact failure scenarios."""

    completed: list[dict] = []
    for scripted in rounds:
        calls: list[dict] = []
        for call in scripted.get("tool_use") or []:
            name = str(call.get("name") or "")
            tool_input = dict(call.get("input") or {})
            if name == "discover_places":
                tool_input.setdefault("activity_label", None)
            elif name == "check_transit":
                tool_input.setdefault("stop_source", "auto")
                tool_input.setdefault("concerns", [])
                tool_input.setdefault("activity_label", None)
            elif name in {"present_places", "present_transit", "present_route"}:
                tool_input.setdefault("lead_in", "")
                tool_input.setdefault("follow_up", "")
                if name == "present_route":
                    tool_input.setdefault("reason_code", "meets_hard_constraints")
            calls.append({**call, "input": tool_input})
        completed.append({**scripted, "tool_use": calls})
    return completed


async def run_failure_turn(
    loop,
    *,
    session: dict,
    session_id: str,
    message: str,
    rounds: list[dict],
    mode: str = "auto",
    prepare_mock: AsyncMock | None = None,
    fixed_candidate_id: str | None = None,
    gtfs=None,
    seam_mocks: dict[str, AsyncMock] | None = None,
    trace=None,
    turn_id: str = "t1",
    mocks: dict | None = None,
    origin: dict | None = None,
):
    """Run one real loop turn with scripted provider seams."""

    loop.client.messages._rounds = _complete_public_inputs(rounds)
    loop.client.messages.calls = []
    original_store = candidate_store.store_candidate_set
    stored_set_ids: list[str] = []

    def _recording_store(*args, **kwargs):
        set_id = original_store(*args, **kwargs)
        stored_set_ids.append(set_id)
        if mocks is not None:
            mocks.setdefault("session_at_store", []).append(
                {
                    "active_trip": copy.deepcopy(session.get("active_trip")),
                    "route_cards": copy.deepcopy(session.get("route_cards") or []),
                }
            )
        return set_id

    enrich_mock = AsyncMock(return_value=None)
    arrivals_mock = AsyncMock(
        return_value=ToolResult(ok=False, error="fixture: no live arrivals")
    )
    patchers = [
        patch("app.services.trips.enrichment._enrich_route", new=enrich_mock),
        patch(
            "app.services.agent.tools.transit.evidence.new_evidence_set_id",
            return_value="te_failure_matrix",
        ),
    ]
    if gtfs is None:
        patchers.append(
            patch(
                "app.services.agent.tools.transit.lookup_arrivals.execute",
                new=arrivals_mock,
            )
        )
    if mocks is not None:
        patchers.append(
            patch(
                "app.services.agent.candidate_store.store_candidate_set",
                new=_recording_store,
            )
        )
    if prepare_mock is not None:
        patchers.append(
            patch(
                "app.services.agent.tools.route.prepare_route_options.prepare_single_leg",
                new=prepare_mock,
            )
        )
    if fixed_candidate_id is not None:
        patchers.append(
            patch(
                "app.services.agent.candidate_store.new_candidate_id",
                return_value=fixed_candidate_id,
            )
        )
    for target, seam_mock in (seam_mocks or {}).items():
        patchers.append(patch(target, new=seam_mock))
    if mocks is not None:
        mocks["prepare_single_leg"] = prepare_mock
        mocks["enrich_route"] = enrich_mock
        mocks["lookup_arrivals"] = arrivals_mock
        mocks["stored_candidate_set_ids"] = stored_set_ids
    events: list = []
    for patcher in patchers:
        patcher.start()
    try:
        async for event in loop.run_agent_turn(
            session=session,
            session_id=session_id,
            turn_id=turn_id,
            message=message,
            now_et="2026-08-06T12:00:00-04:00",
            gtfs=gtfs,
            origin=origin if origin is not None else {"lat": 40.75, "lng": -73.99},
            response_presentation=mode,
            trace=trace,
        ):
            events.append(event)
    finally:
        for patcher in patchers:
            patcher.stop()
    return events, trace, mocks


class _FailureMatrixBase(unittest.IsolatedAsyncioTestCase):
    """Shared invariants for Batch J1 failure-driven scenarios."""

    loop = None  # set in setUpClass by subclasses

    def setUp(self):
        clear_caches()

    # -- seeding / running -------------------------------------------

    def _seed_accepted(self):
        session_id = f"sess-j1-{secrets.token_hex(4)}"
        _sid, session = new_session()
        seed = seed_accepted_active_trip(session, session_id)
        return session, session_id, seed

    async def _run_prepare_failure(
        self, *, session, session_id, mode, message, prepare_mock,
        destination="Work", extra_input=None, text=FAILURE_TEXT,
    ):
        trace = self.loop.TurnTrace()
        mocks = {}
        events, trace, mocks = await run_failure_turn(
            self.loop, session=session, session_id=session_id, message=message,
            rounds=prepare_rounds(destination=destination, tool_input_extra=extra_input, text=text),
            mode=mode, prepare_mock=prepare_mock, trace=trace, mocks=mocks,
        )
        return events, trace, mocks

    async def _run_prepare_present(
        self, *, session, session_id, mode, message, prepare_leg,
        candidate_id, destination="Work", extra_input=None,
    ):
        trace = self.loop.TurnTrace()
        mocks = {}
        events, trace, mocks = await run_failure_turn(
            self.loop, session=session, session_id=session_id, message=message,
            rounds=prepare_present_rounds(
                destination=destination, candidate_id=candidate_id,
                tool_input_extra=extra_input,
            ),
            mode=mode, prepare_mock=prepare_mock_returning(prepare_leg),
            fixed_candidate_id=candidate_id, trace=trace, mocks=mocks,
        )
        return events, trace, mocks

    async def _run_seam_turn(
        self, *, session, session_id, message, rounds, seam_mocks, gtfs=None,
    ):
        trace = self.loop.TurnTrace()
        mocks = {}
        seam_mocks = dict(seam_mocks or {})
        seam_mocks.setdefault(
            "app.services.mta.realtime.get_stalled_trains",
            AsyncMock(return_value=[]),
        )
        seam_mocks.setdefault(
            "app.services.mta.realtime.get_stalled_buses",
            AsyncMock(return_value=[]),
        )
        seam_mocks.setdefault(
            "app.services.incidents.index.lookup_incidents",
            MagicMock(return_value={"incidents": [], "coverage_status": "current"}),
        )
        events, trace, mocks = await run_failure_turn(
            self.loop, session=session, session_id=session_id, message=message,
            rounds=rounds, mode="auto", gtfs=gtfs, seam_mocks=seam_mocks,
            trace=trace, mocks=mocks,
        )
        return events, trace, mocks

    async def _run_failure_asserted(
        self, *, mode, message, prepare_mock, accepted=False,
        destination=None, extra_input=None, text=FAILURE_TEXT,
        expected_error=None, audit_status=None, audit_evidence=None,
    ):
        """Run one failed prepare turn and assert its full contract."""

        if accepted:
            session, session_id, seed = self._seed_accepted()
            destination = destination or seed.destination
        else:
            session_id = f"sess-j1-{secrets.token_hex(4)}"
            _sid, session = new_session()
            seed = None
            destination = destination or "Work"
        events, trace, mocks = await self._run_prepare_failure(
            session=session, session_id=session_id, mode=mode,
            message=message, prepare_mock=prepare_mock,
            destination=destination, extra_input=extra_input, text=text,
        )
        self._assert_failure_turn(
            events=events, trace=trace, mocks=mocks, session=session,
            session_id=session_id, mode=mode, seed=seed,
            expected_error=expected_error, audit_status=audit_status,
            audit_evidence=audit_evidence,
        )
        return session, session_id, seed, events, trace, mocks

    # -- shared assertion contracts -----------------------------------

    def _assert_turn_envelope(
        self, events, *, tool_count=1, expect_route_card=False, max_events=17,
    ):
        self.assertEqual(events[0].type, "meta")
        self.assertEqual(events[-1].type, "done")
        self.assertEqual(events[-1].stop_reason, "end_turn")
        self.assertLessEqual(len(events), max_events)
        cards = route_cards(events)
        if expect_route_card:
            self.assertEqual(len(cards), 1)
            self.assertEqual(cards[0].role, "recommended")
        else:
            self.assertEqual(cards, [])
        tool_starts = [event for event in events if event.type == "tool_start"]
        tool_ends = [event for event in events if event.type == "tool_end"]
        self.assertEqual(len(tool_starts), tool_count)
        self.assertEqual(len(tool_starts), len(tool_ends))
        progress = [event for event in events if event.type == "progress"]
        # At most finding_routes + comparing_options across a two-tool turn.
        self.assertLessEqual(len(progress), 4)
        for event in progress:
            self.assertIn(
                event.stage,
                {"finding_routes", "checking_live_conditions", "comparing_options"},
            )
            self.assertIn(event.status, {"active", "complete"})

    def _assert_truthful_failure_text(self, trace, *, marker: str):
        self.assertEqual(trace.model_call_count, 3)
        text = trace.final_text
        self.assertTrue(text.strip())
        lowered = text.casefold()
        for forbidden in ("recommended", "i'd take", "best option", "cd_", "cs_", "rc_"):
            self.assertNotIn(forbidden, lowered, f"leaked marker: {forbidden}")
        self.assertIn(marker, lowered)

    def _assert_no_selection(self, session):
        state = trip_state_module.get_trip_state(session)
        self.assertIsNone(state["active_candidate_set_id"])
        self.assertIsNone(state["selected_candidate_id"])
        self.assertIsNone(session.get("active_trip"))
        self.assertEqual(session.get("route_cards") or [], [])

    def _assert_accepted_preserved(self, session, session_id, seed):
        state = trip_state_module.get_trip_state(session)
        self.assertEqual(state["active_candidate_set_id"], seed.candidate_set_id)
        self.assertEqual(state["selected_candidate_id"], seed.candidate_id)
        self.assertEqual(state["origin"], seed.origin)
        self.assertEqual(state["destination"], seed.destination)
        self.assertEqual(state["waypoints"], [])
        self.assertEqual(state["planning_mode"], seed.planning_mode)
        self.assertEqual(state["requested_departure"], seed.requested_departure)
        self.assertEqual(state["requested_arrival"], seed.requested_arrival)
        self.assertEqual(session["active_trip"]["card_id"], seed.card_id)
        self.assertEqual(
            [card["card_id"] for card in session["route_cards"]],
            [seed.card_id],
        )
        record = candidate_store.load_candidate_set(
            seed.candidate_set_id, session_id=session_id,
        )
        self.assertIsNotNone(record)
        self.assertEqual(record["presented"], True)
        self.assertEqual(record["selected_candidate_id"], seed.candidate_id)
        self.assertEqual(record["route_status"], "good")

    def _assert_pending_failed(self, session, expected_error: str):
        pending = session.get("pending_trip") or {}
        self.assertEqual(pending.get("status"), "failed")
        self.assertEqual(pending.get("last_error"), expected_error)
        self.assertIs(pending.get("resume_offered"), False)

    def _assert_mode(self, trace, mode: str):
        expected_mode, expected_model = policy_model(self.loop, mode)
        self.assertEqual(trace.initial_mode, expected_mode)
        self.assertEqual(trace.final_mode, expected_mode)
        self.assertEqual(self.loop.client.messages.calls[0]["model"], expected_model)

    def _assert_audit_set(
        self, *, stored_set_ids, state, session_id, preserved_set_id=None,
        expected_status, evidence_coverage=None,
    ):
        self.assertEqual(len(stored_set_ids), 1)
        audit_set_id = stored_set_ids[0]
        if preserved_set_id is not None:
            self.assertNotEqual(audit_set_id, preserved_set_id)
        self.assertNotEqual(
            audit_set_id, state["active_candidate_set_id"],
            "audit set must not replace the active server-owned selection",
        )
        audit = candidate_store.load_candidate_set(audit_set_id, session_id=session_id)
        self.assertIsNotNone(audit)
        self.assertEqual(audit["route_status"], expected_status)
        self.assertFalse(audit["presented"])
        self.assertIsNone(audit["selected_candidate_id"])
        if evidence_coverage is not None:
            self.assertEqual(audit["evidence_coverage"], evidence_coverage)
        return audit

    def _assert_failure_turn(
        self, *, events, trace, mocks, session, session_id, mode,
        seed=None, expected_error=None, audit_status=None, audit_evidence=None,
    ):
        """One failed ``prepare_route_options`` turn, fresh or accepted."""

        # Only evidence-gathering work is rider-visible. Goal declaration and
        # terminal presenters remain traceable in ``trace.tool_calls`` but do
        # not emit activity rows.
        self._assert_turn_envelope(events, tool_count=1)
        self.assertEqual(
            [name for name, _input in trace.tool_calls],
            ["declare_goals", "prepare_route_options", "complete_turn"],
        )
        self.assertEqual(mocks["prepare_single_leg"].await_count, 1)
        self._assert_truthful_failure_text(trace, marker="could not")
        if seed is None:
            self._assert_no_selection(session)
        else:
            self._assert_accepted_preserved(session, session_id, seed)
        if audit_status is None:
            self.assertEqual(mocks["stored_candidate_set_ids"], [])
            self._assert_pending_failed(session, expected_error or "tool failed")
            tool_end = [event for event in events if event.type == "tool_end"][0]
            self.assertIs(tool_end.ok, False)
            self.assertEqual(trace.retry_count, 0)
            self.assertEqual(trace.tool_call_count, 3)
        else:
            self.assertEqual(
                (session.get("pending_trip") or {}).get("status"),
                "none",
                "a stored audit no-good is not a provider failure",
            )
            state = trip_state_module.get_trip_state(session)
            self._assert_audit_set(
                stored_set_ids=mocks["stored_candidate_set_ids"], state=state,
                session_id=session_id,
                preserved_set_id=seed.candidate_set_id if seed is not None else None,
                expected_status=audit_status, evidence_coverage=audit_evidence,
            )
        self._assert_mode(trace, mode)

    def _assert_presented_contract(
        self, *, events, trace, mocks, session, session_id, mode,
        candidate_id, expected_status, expected_coverage, seed=None,
    ):
        """Prepare + present emitted exactly one card and committed the set."""

        self._assert_turn_envelope(events, tool_count=1, expect_route_card=True)
        self.assertEqual(
            [name for name, _input in trace.tool_calls],
            ["declare_goals", "prepare_route_options", "present_route"],
        )
        for forbidden in FORBIDDEN_TOOL_NAMES:
            self.assertNotIn(
                forbidden, [name for name, _input in trace.tool_calls],
            )
        self.assertEqual(len(mocks["stored_candidate_set_ids"]), 1)
        state = trip_state_module.get_trip_state(session)
        self.assertEqual(state["selected_candidate_id"], candidate_id)
        record = candidate_store.load_candidate_set(
            state["active_candidate_set_id"], session_id=session_id,
        )
        self.assertEqual(record["route_status"], expected_status)
        self.assertEqual(record["evidence_coverage"], expected_coverage)
        self.assertTrue(record["presented"])
        digest = record["candidates"][0]["digest"]
        self.assertIs(digest["hard_constraints_satisfied"], True)
        self.assertEqual(digest["accessibility_status"], "unknown")
        self.assertEqual(route_cards(events)[0].summary["lines"], ["R"])
        self.assertEqual((session.get("active_trip") or {}).get("lines"), ["R"])
        self.assertEqual((session.get("pending_trip") or {}).get("status"), "none")
        if seed is not None:
            self.assertNotEqual(state["active_candidate_set_id"], seed.candidate_set_id)
        self._assert_mode(trace, mode)
        return record

    def _assert_no_match_contract(
        self, *, events, trace, mocks, session, session_id, seed,
    ):
        """Hard-constraint miss: audit set, no card, accepted trip preserved."""

        self._assert_turn_envelope(events, tool_count=1)
        self.assertEqual(
            [name for name, _input in trace.tool_calls],
            ["declare_goals", "prepare_route_options", "complete_turn"],
            "a hard-constraint miss must never reach present_route",
        )
        self._assert_truthful_failure_text(trace, marker="could not")
        self._assert_accepted_preserved(session, session_id, seed)
        state = trip_state_module.get_trip_state(session)
        audit = self._assert_audit_set(
            stored_set_ids=mocks["stored_candidate_set_ids"], state=state,
            session_id=session_id, preserved_set_id=seed.candidate_set_id,
            expected_status="no_hard_constraint_match",
        )
        digest = audit["candidates"][0]["digest"]
        self.assertEqual(digest["accessibility_status"], "unknown")
        self.assertIn(
            "accessibility_unknown_or_unavailable",
            digest["hard_constraint_violations"],
        )
        self.assertIs(digest["hard_constraints_satisfied"], False)

    def _assert_seam_turn(
        self, *, events, trace, session, session_id, seed, expected_tool,
        tool_end_ok=None, summary_contains=None, arrival_card_status=None,
        model_calls=None, text_contains=None, terminal_tool="complete_turn",
    ):
        """Status/arrival/discovery failure: no mutation, bounded events."""

        self._assert_turn_envelope(events, tool_count=1)
        self.assertEqual(
            [name for name, _input in trace.tool_calls],
            ["declare_goals", expected_tool, terminal_tool],
            "no browser/route/discovery fallback may run after a provider failure",
        )
        self._assert_accepted_preserved(session, session_id, seed)
        state = trip_state_module.get_trip_state(session)
        self.assertIsNone(state["active_discovery_set_id"])
        self.assertIsNone(state["selected_place_id"])
        tool_end = next(
            event
            for event in events
            if event.type == "tool_end" and event.tool == expected_tool
        )
        if tool_end_ok is not None:
            self.assertIs(tool_end.ok, tool_end_ok)
        if summary_contains is not None:
            self.assertIn(summary_contains, tool_end.summary or "")
        if arrival_card_status is not None:
            cards = [event for event in events if event.type == "arrival_card"]
            if arrival_card_status == "stale":
                # The canonical presenter keeps stale/no-prediction arrival
                # evidence as bounded prose; it must not emit a misleading
                # arrival card with no renderable predictions.
                self.assertEqual(cards, [])
                self.assertIn("out of date", trace.final_text.casefold())
            else:
                self.assertEqual(len(cards), 1)
                self.assertEqual(cards[0].source_status, arrival_card_status)
                self.assertEqual(cards[0].directions, [])
        if model_calls is not None:
            self.assertEqual(trace.model_call_count, model_calls)
        if text_contains is not None:
            self.assertIn(text_contains, trace.final_text.casefold())


__all__ = (
    "FORBIDDEN_TOOL_NAMES",
    "_FailureMatrixBase",
    "prepare_mock_returning",
    "prepare_mock_with_side_effect",
    "run_failure_turn",
)
