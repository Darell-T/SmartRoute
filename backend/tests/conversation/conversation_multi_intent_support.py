"""Batch F2 audit support: multi-intent sequencing through the real loop.

Non-test module (no ``Test*``/``test_*`` names): pytest never collects it.
Probes drive the REAL agent loop (``loop.run_agent_turn``) with the real
``TOOL_REGISTRY``, per-turn ledger, candidate/discovery/trip stores, and SSE
path. Anthropic inference is scripted through ``tests._fake_anthropic``; only
genuine provider/data/id seams are patched (poi_search.execute,
prepare_route_options.prepare_single_leg, trips.enrichment._enrich_route,
tools.lookup_arrivals.execute, mta.realtime.fetch/parse_service_alerts,
discovery_store.new_place_id, candidate_store.new_candidate_id).
``store_discovery_set`` is observed, never replaced. Every unoffered-case seam
is a FAIL-LOUD spy: a recorded call count proves an unoffered executor started
provider work. No production file is modified.
"""

from __future__ import annotations

import copy
import dataclasses
import secrets
import unittest
from unittest.mock import AsyncMock, patch

from app.services.agent import discovery_store
from app.services.agent import trip_state as trip_state_module
from app.services.agent.tools.transit import evidence as transit_evidence

from tests.conversation.conversation_matrix_harness import (
    clear_caches,
    new_session,
    policy_model,
    run_turn,
    seed_accepted_active_trip,
)
from tests.conversation.conversation_multi_intent_fixtures import _model_led_rounds

LEAK_MARKERS = ("pl_", "ds_", "cd_", "cs_", "rc_", "ChIJ", "tu-")
_STATE_KEYS = (
    "origin", "destination", "waypoints", "planning_mode",
    "requested_departure", "requested_arrival", "active_candidate_set_id",
    "selected_candidate_id", "temporary_candidate_set_id",
    "temporary_selected_candidate_id", "temporary_base_candidate_set_id",
    "active_discovery_set_id", "selected_place_id",
)


def fail_loud_spy(label: str) -> AsyncMock:
    """A provider-seam spy that fails the probe when an executor reaches it."""
    return AsyncMock(side_effect=AssertionError(f"F2 fail-loud spy reached: {label}"))


def state_projection(session: dict) -> dict:
    """Canonical trip-state fields any executor could mutate."""
    state = trip_state_module.get_trip_state(session)
    return {key: state.get(key) for key in _STATE_KEYS}


def session_projection(session: dict) -> dict:
    """Session-level facts an executor could mutate."""
    return {
        "slots": copy.deepcopy(session.get("slots")),
        "trip_state": state_projection(session),
        "pending_trip": copy.deepcopy(session.get("pending_trip")),
        "active_trip": copy.deepcopy(session.get("active_trip")),
        "route_cards": copy.deepcopy(session.get("route_cards") or []),
        "history_tool_summaries": [
            entry.get("tool")
            for entry in session.get("history") or []
            if entry.get("role") == "tool"
        ],
    }


def _preamble_normalized(projection: dict) -> dict:
    """Drop semantically-empty constraint writes from the turn preamble."""
    normalized = copy.deepcopy(projection)
    constraints = (normalized.get("slots") or {}).get("constraints")
    if isinstance(constraints, dict):
        for key in ("exclude_modes", "excluded_route_ids"):
            if constraints.get(key) == []:
                constraints.pop(key)
        if not constraints:
            normalized.setdefault("slots", {}).pop("constraints", None)
    return normalized


@dataclasses.dataclass(frozen=True)
class MultiIntentEvidence:
    """One real-loop probe: offered surface, execution, and impact."""

    message: str
    mode: str
    session_id: str
    offered: frozenset
    offered_profiles: tuple[frozenset, ...]
    tool_calls: tuple
    model_led_tool_calls: tuple
    declared_goals: tuple[dict, ...]
    capability_attempts: tuple[dict, ...]
    tool_starts: tuple
    tool_ends: tuple
    spies: dict
    state_before: dict
    state_after: dict
    stored_candidate_set_ids: tuple
    discovery_store_calls: tuple
    discovery_record: dict | None
    cards: tuple
    stop_reason: str
    final_text: str
    provider_execution_count: int
    model_call_count: int
    models: tuple
    context: str
    seed: object | None

    def compact(self) -> str:
        """Bounded one-block evidence for failure messages."""
        spy_counts = {name: mock.await_count for name, mock in self.spies.items()}
        return (
            f"msg={self.message!r} mode={self.mode} "
            f"offered={sorted(self.offered)} calls={list(self.tool_calls)} "
            f"starts={list(self.tool_starts)} ends={list(self.tool_ends)} "
            f"spies={spy_counts} models={list(self.models)} "
            f"stop={self.stop_reason} prov={self.provider_execution_count} "
            f"mcalls={self.model_call_count} sets="
            f"{list(self.stored_candidate_set_ids)} cards={list(self.cards)} "
            f"before={self.state_before} after={self.state_after}"
        )


async def run_multi_probe(
    loop,
    *,
    session: dict,
    session_id: str,
    message: str,
    rounds: list[dict],
    mode: str,
    turn_id: str = "t1",
    seams: dict | None = None,
    prepare_leg=None,
    prepare_legs=None,
    fixed_candidate_id: str | None = None,
    new_candidate_ids: tuple[str, ...] | None = None,
    discovery_set_id: str | None = None,
    place_ids: tuple[str, ...] | None = None,
    record_discovery_store: bool = False,
    seed=None,
) -> MultiIntentEvidence:
    """Run one real turn with fail-loud seams and capture compact evidence."""
    seams = seams or {}
    trace = loop.TurnTrace()
    mocks: dict = {}
    evidence_handle = f"te_f2_{str(session_id)[-8:]}_{turn_id}"
    model_rounds, evidence_id, declared_goals = _model_led_rounds(
        rounds, turn_id=turn_id, evidence_id=evidence_handle
    )
    loop.client.messages._rounds = list(model_rounds)
    loop.client.messages.calls = []
    state_before = session_projection(session)
    discovery_store_calls: list = []
    original_discovery_store = discovery_store.store_discovery_set

    def _recording_discovery_store(*args, **kwargs):
        result = original_discovery_store(*args, **kwargs)
        discovery_store_calls.append(result)
        return result

    patchers = [patch(target, new=mock) for target, mock in seams.values()]
    if record_discovery_store:
        patchers.append(patch(
            "app.services.agent.discovery_store.store_discovery_set",
            new=_recording_discovery_store,
        ))
    if place_ids is not None:
        patchers.append(patch(
            "app.services.agent.discovery_store.new_place_id",
            side_effect=list(place_ids),
        ))
    if discovery_set_id is not None:
        patchers.append(patch(
            "app.services.agent.discovery_store.new_discovery_set_id",
            return_value=discovery_set_id,
        ))
    if new_candidate_ids is not None:
        patchers.append(patch(
            "app.services.agent.candidate_store.new_candidate_id",
            side_effect=list(new_candidate_ids),
        ))
    patchers.append(
        patch.object(
            transit_evidence,
            "new_evidence_set_id",
            return_value=evidence_id,
        )
    )
    for patcher in patchers:
        patcher.start()
    try:
        events, trace = await run_turn(
            loop, session=session, session_id=session_id, message=message,
            rounds=model_rounds, mode=mode, trace=trace, mocks=mocks,
            turn_id=turn_id,
            prepare_leg=prepare_leg, prepare_legs=prepare_legs,
            fixed_candidate_id=fixed_candidate_id,
        )
    finally:
        for patcher in patchers:
            patcher.stop()
    state_after = session_projection(session)
    calls = loop.client.messages.calls
    offered = (
        frozenset(schema["name"] for schema in calls[0]["tools"])
        if calls
        else frozenset()
    )
    offered_profiles = tuple(
        frozenset(schema["name"] for schema in call["tools"])
        for call in calls
    )
    models = tuple(call["model"] for call in calls)
    context = str(calls[0]["messages"][-1]["content"]) if calls else ""
    raw_tool_calls = tuple(trace.tool_calls)
    if raw_tool_calls and raw_tool_calls[0][0] == "declare_goals":
        trace.model_led_tool_calls = list(raw_tool_calls)
        trace.tool_calls = [
            call for call in raw_tool_calls if call[0] != "declare_goals"
        ]
    discovery_record = None
    active_set = state_after["trip_state"]["active_discovery_set_id"]
    if active_set:
        discovery_record = discovery_store.load_discovery_set(
            active_set, session_id=session_id
        )
    return MultiIntentEvidence(
        message=message, mode=mode,
        session_id=session_id, offered=offered,
        offered_profiles=offered_profiles,
        tool_calls=tuple(trace.tool_calls),
        model_led_tool_calls=raw_tool_calls,
        declared_goals=tuple(declared_goals.values()),
        capability_attempts=tuple(trace.capability_attempts),
        tool_starts=tuple(
            (event.tool, event.tool_call_id)
            for event in events
            if event.type == "tool_start"
        ),
        tool_ends=tuple(
            (event.tool, event.ok, event.summary, event.tool_call_id)
            for event in events
            if event.type == "tool_end"
        ),
        spies={name: mock for name, (_target, mock) in seams.items()},
        state_before=state_before, state_after=state_after,
        stored_candidate_set_ids=tuple(mocks.get("stored_candidate_set_ids") or []),
        discovery_store_calls=tuple(discovery_store_calls),
        discovery_record=discovery_record,
        cards=tuple(
            (event.card_id, event.role)
            for event in events
            if event.type == "route_card"
        ),
        stop_reason=next(
            (event.stop_reason for event in reversed(events) if event.type == "done"),
            None,
        ),
        final_text=trace.final_text,
        provider_execution_count=trace.provider_tool_execution_count,
        model_call_count=trace.model_call_count,
        models=models, context=context, seed=seed,
    )


class _MultiIntentBase(unittest.IsolatedAsyncioTestCase):
    """Shared harness for Batch F2 multi-intent sequencing probes."""

    loop = None  # set in setUpClass by subclasses

    def setUp(self):
        clear_caches()

    def _new_session(self, mode: str) -> tuple[str, dict]:
        session_id = f"sess-f2-{mode}-{secrets.token_hex(4)}"
        _sid, session = new_session()
        return session_id, session

    async def _probe(
        self,
        *,
        mode: str,
        message: str,
        rounds: list[dict],
        seams: dict | None = None,
        prepare_leg=None,
        prepare_legs=None,
        fixed_candidate_id: str | None = None,
        new_candidate_ids: tuple[str, ...] | None = None,
        discovery_set_id: str | None = None,
        place_ids: tuple[str, ...] | None = None,
        record_discovery_store: bool = False,
        seed: bool = False,
        turn_id: str = "t1",
    ) -> MultiIntentEvidence:
        session_id, session = self._new_session(mode)
        seed_snapshot = (
            seed_accepted_active_trip(session, session_id) if seed else None
        )
        return await run_multi_probe(
            self.loop, session=session, session_id=session_id, message=message,
            rounds=rounds, mode=mode, turn_id=turn_id, seams=seams,
            prepare_leg=prepare_leg, prepare_legs=prepare_legs,
            fixed_candidate_id=fixed_candidate_id,
            new_candidate_ids=new_candidate_ids,
            discovery_set_id=discovery_set_id,
            place_ids=place_ids,
            record_discovery_store=record_discovery_store, seed=seed_snapshot,
        )

    # ---- assertion helpers ------------------------------------------------
    def _assert_offered_exact(self, ev, expected, sid):
        assert ev.offered == expected, f"{sid}: offer exactly {sorted(expected)}; {ev.compact()}"

    def _assert_declared_goals(self, ev, expected, sid):
        assert ev.declared_goals == tuple(expected), f"{sid}: explicit model-led goal contract; {ev.compact()}"

    def _assert_state_valid_presenter(self, ev, presenter, sid):
        assert any(presenter in profile for profile in ev.offered_profiles[1:]), f"{sid}: {presenter} must become state-valid after evidence"

    def _assert_policy(
        self,
        ev,
        mode,
        sid,
        *,
        model_calls,
        stop_reason="end_turn",
    ):
        _expected_mode, expected_model = policy_model(self.loop, mode)
        assert ev.models == (expected_model,) * model_calls, f"{sid}: policy models; {ev.compact()}"
        assert ev.model_call_count == model_calls, f"{sid}: model call count; {ev.compact()}"
        assert ev.stop_reason == stop_reason, f"{sid}: terminal stop reason; {ev.compact()}"

    def _assert_executed(self, ev, expected, sid):
        names = [name for name, _input in ev.tool_calls]
        assert names == list(expected), f"{sid}: tool sequence; {ev.compact()}"
        for name in expected:
            assert names.count(name) == 1, f"{sid}: exactly one {name}; {ev.compact()}"

    def _assert_rejected(self, ev, name, sid, *, zero_spies=None):
        """F1 enforcement: bounded rejection with zero side effects."""
        assert name not in [tool for tool, _input in ev.tool_calls], f"{sid}: rejected {name} never reaches ledger; {ev.compact()}"
        if zero_spies is None:
            zero_spies = tuple(ev.spies)
        counts = {key: ev.spies[key].await_count for key in zero_spies}
        assert counts == dict.fromkeys(zero_spies, 0), f"{sid}: rejected {name} never reaches provider; {ev.compact()}"
        assert name not in [tool for tool, _call_id in ev.tool_starts], f"{sid}: rejected {name} emits no false in-flight activity; {ev.compact()}"
        ends = {t: (ok, summary, cid) for t, ok, summary, cid in ev.tool_ends}
        assert name in ends, f"{sid}: rejected {name} ToolEnd; {ev.compact()}"
        ok, summary, _call_id = ends[name]
        assert not ok, f"{sid}: rejected {name} ToolEnd failure"
        assert "not offered" in summary or "not available" in summary, f"{sid}: rejected {name} bounded error; {ev.compact()}"
        assert name not in ev.state_after["history_tool_summaries"], f"{sid}: rejected {name} no summary"

    def _assert_no_forbidden(self, ev, forbidden, sid):
        executed = {name for name, _input in ev.tool_calls}
        assert executed & set(forbidden) == set(), f"{sid}: forbidden tools executed; {ev.compact()}"

    def _assert_no_card(self, ev, sid):
        assert ev.cards == (), f"{sid}: unexpected card"
        assert ev.stored_candidate_set_ids == (), f"{sid}: no candidate set may store; {ev.compact()}"

    def _assert_one_card(self, ev, sid, *, expected_selected):
        assert len(ev.cards) == 1, f"{sid}: exactly one card; {ev.compact()}"
        assert len(ev.stored_candidate_set_ids) == 1, f"{sid}: exactly one candidate set; {ev.compact()}"
        state = ev.state_after["trip_state"]
        assert state["active_candidate_set_id"] == ev.stored_candidate_set_ids[0], f"{sid}: active candidate set committed"
        assert state["selected_candidate_id"] == expected_selected, f"{sid}: selected candidate committed"
        lowered = ev.final_text.casefold()
        for marker in LEAK_MARKERS:
            assert marker not in lowered, f"{sid}: rider text leaked {marker}"

    def _assert_seed_preserved(self, ev, sid):
        seed = ev.seed
        assert seed is not None, f"{sid}: probe requires a seed"
        state = ev.state_after["trip_state"]
        assert state["active_candidate_set_id"] == seed.candidate_set_id, f"{sid}: accepted candidate set preserved"
        assert state["selected_candidate_id"] == seed.candidate_id, f"{sid}: accepted selected candidate preserved"
        assert state["destination"] == seed.destination, f"{sid}: accepted destination preserved"
        assert ev.state_after["active_trip"] == seed.card, f"{sid}: active trip card preserved"
        assert [card["card_id"] for card in ev.state_after["route_cards"]] == [seed.card_id], f"{sid}: route cards preserved"

    def _assert_discovery_bound(self, ev, sid):
        set_id = ev.state_after["trip_state"]["active_discovery_set_id"]
        assert set_id, f"{sid}: real discovery set; {ev.compact()}"
        assert set_id.startswith("ds_"), f"{sid}: real discovery set; {ev.compact()}"
        record = ev.discovery_record
        assert record is not None, f"{sid}: stored discovery record"
        assert [place["ordinal"] for place in record["places"]] == [1, 2, 3], f"{sid}: stored ordinals"
        assert record["places"][1]["name"] == "B Pizza", f"{sid}: ordinal 2 stored name"

    def _assert_no_discovery(self, ev, sid):
        assert ev.discovery_store_calls == (), f"{sid}: no discovery set may bind; {ev.compact()}"
        state = ev.state_after["trip_state"]
        assert state["active_discovery_set_id"] is None, f"{sid}: no active discovery set; {ev.compact()}"
        assert state["selected_place_id"] is None, f"{sid}: no selected place; {ev.compact()}"


__all__ = (
    "LEAK_MARKERS",
    "MultiIntentEvidence",
    "_MultiIntentBase",
    "_preamble_normalized",
    "fail_loud_spy",
    "run_multi_probe",
    "session_projection",
    "state_projection",
)
