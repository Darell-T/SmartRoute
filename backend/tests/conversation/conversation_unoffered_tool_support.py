"""Batch F1 audit support: fail-loud probes of the offered-tool boundary.

Non-test module (no ``Test*``/``test_*`` names at module level): pytest
never collects it.

Probes drive the REAL agent loop (``loop.run_agent_turn``) with the real
``TOOL_REGISTRY``, per-turn ledger, candidate/discovery/trip stores, and SSE
event path. Anthropic inference is scripted through ``tests._fake_anthropic``
and the established ``conversation_matrix_harness`` seams (enrichment,
arrival lookup, candidate-store recording). The only additional patched
production points are genuine external provider/data seams, and every
unoffered-case seam is a FAIL-LOUD spy: reaching it raises, so a recorded
call count proves an unoffered executor started provider work.

No production file is modified. ``CONVERSATION_MATRIX.md`` is owned by the
gate integrator and is intentionally not touched here.
"""

from __future__ import annotations

import copy
import dataclasses
import secrets
import unittest
from unittest.mock import AsyncMock, patch

from app.services.agent import discovery_store
from app.services.agent import trip_state as trip_state_module
from tests.conversation.conversation_matrix_harness import (
    clear_caches,
    new_session,
    run_turn,
)

_STATE_KEYS = (
    "origin",
    "destination",
    "waypoints",
    "planning_mode",
    "requested_departure",
    "requested_arrival",
    "active_candidate_set_id",
    "selected_candidate_id",
    "temporary_candidate_set_id",
    "temporary_selected_candidate_id",
    "temporary_base_candidate_set_id",
    "active_discovery_set_id",
    "selected_place_id",
)


def fail_loud_spy(label: str) -> AsyncMock:
    """A provider-seam spy that fails the probe when an executor reaches it."""

    return AsyncMock(
        side_effect=AssertionError(f"F1 fail-loud spy reached: {label}")
    )


def state_projection(session: dict) -> dict:
    """Canonical trip-state fields an unoffered executor could mutate."""

    state = trip_state_module.get_trip_state(session)
    return {key: state.get(key) for key in _STATE_KEYS}


def session_projection(session: dict) -> dict:
    """Session-level facts an unoffered executor could mutate."""

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
    """Drop semantically-empty constraint writes from the turn preamble.

    ``rider_excluded_modes``/``rider_excluded_route_ids`` persist empty
    exclusion lists into ``slots.constraints`` on ordinary (non-what-if)
    turns; an empty list means exactly the same as an absent one. Comparing
    projections through this normalization proves a rejected call adds no
    mutation beyond the turn's own constraint normalization.
    """

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
class ProbeEvidence:
    """One real-loop probe: offered surface, actual execution, and impact."""

    message: str
    mode: str
    orchestration: str
    offered: frozenset
    offered_surfaces: tuple[frozenset, ...]
    emitted: tuple
    tool_calls: tuple
    capability_attempts: tuple[dict, ...]
    tool_starts: tuple
    tool_ends: tuple
    spies: dict
    state_before: dict
    state_after: dict
    stored_candidate_set_ids: tuple
    discovery_store_calls: tuple
    cards: tuple
    stop_reason: str
    final_text: str
    provider_execution_count: int
    model_call_count: int

    def compact(self) -> str:
        """Bounded one-block evidence for failure messages."""

        spy_counts = {
            name: mock.await_count for name, mock in self.spies.items()
        }
        return "\n".join(
            [
                f"message={self.message!r} mode={self.mode} "
                f"orchestration={self.orchestration}",
                f"offered={sorted(self.offered)}",
                f"offered_surfaces={[sorted(surface) for surface in self.offered_surfaces]}",
                f"emitted={sorted(set(self.emitted))}",
                f"trace_calls={list(self.tool_calls)}",
                f"tool_starts={list(self.tool_starts)}",
                f"tool_ends={list(self.tool_ends)}",
                f"spy_await_counts={spy_counts}",
                f"state_before={self.state_before}",
                f"state_after={self.state_after}",
                f"stored_candidate_set_ids="
                f"{list(self.stored_candidate_set_ids)}",
                f"discovery_store_calls={list(self.discovery_store_calls)}",
                f"cards={list(self.cards)} stop_reason={self.stop_reason}",
                f"provider_executions={self.provider_execution_count} "
                f"model_calls={self.model_call_count}",
            ]
        )


async def run_probe(
    loop,
    *,
    session: dict,
    session_id: str,
    message: str,
    rounds: list[dict],
    mode: str,
    turn_id: str = "t1",
    seams: dict | None = None,
    record_discovery_store: bool = False,
) -> ProbeEvidence:
    """Run one real turn with fail-loud seams and capture compact evidence.

    ``seams`` maps a logical spy name to ``(patch_target, AsyncMock)``;
    ``record_discovery_store`` wraps the real discovery-store write in a
    recorder so tests can prove no discovery set binds.
    """

    seams = seams or {}
    trace = loop.TurnTrace()
    mocks: dict = {}
    loop.client.messages._rounds = list(rounds)
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
        patchers.append(
            patch(
                "app.services.agent.discovery_store.store_discovery_set",
                new=_recording_discovery_store,
            )
        )
    for patcher in patchers:
        patcher.start()
    try:
        events, trace = await run_turn(
            loop,
            session=session,
            session_id=session_id,
            message=message,
            rounds=rounds,
            mode=mode,
            trace=trace,
            mocks=mocks,
            turn_id=turn_id,
        )
    finally:
        for patcher in patchers:
            patcher.stop()
    state_after = session_projection(session)
    offered_surfaces = tuple(
        frozenset(schema["name"] for schema in call.get("tools") or [])
        for call in loop.client.messages.calls
    )
    offered = offered_surfaces[0] if offered_surfaces else frozenset()
    return ProbeEvidence(
        message=message,
        mode=mode,
        orchestration="model_led",
        offered=offered,
        offered_surfaces=offered_surfaces,
        emitted=tuple(name for name, _input in trace.tool_calls),
        tool_calls=tuple(trace.tool_calls),
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
        state_before=state_before,
        state_after=state_after,
        stored_candidate_set_ids=tuple(
            mocks.get("stored_candidate_set_ids") or []
        ),
        discovery_store_calls=tuple(discovery_store_calls),
        cards=tuple(
            (event.card_id, event.role)
            for event in events
            if event.type == "route_card"
        ),
        stop_reason=next(
            (
                event.stop_reason
                for event in reversed(events)
                if event.type == "done"
            ),
            None,
        ),
        final_text=trace.final_text,
        provider_execution_count=trace.provider_tool_execution_count,
        model_call_count=trace.model_call_count,
    )


class _OfferedSurfaceBase(unittest.IsolatedAsyncioTestCase):
    """Shared harness for Batch F1 offered-surface probes."""

    loop = None  # set in setUpClass by subclasses

    def setUp(self):
        clear_caches()

    def _new_session(self, mode: str) -> tuple[str, dict]:
        session_id = f"sess-f1-{mode}-{secrets.token_hex(4)}"
        _sid, session = new_session()
        return session_id, session

    async def _probe(
        self,
        *,
        mode: str,
        message: str,
        rounds: list[dict],
        seams: dict | None = None,
        record_discovery_store: bool = False,
        turn_id: str = "t1",
    ) -> ProbeEvidence:
        session_id, session = self._new_session(mode)
        return await run_probe(
            self.loop,
            session=session,
            session_id=session_id,
            message=message,
            rounds=rounds,
            mode=mode,
            turn_id=turn_id,
            seams=seams,
            record_discovery_store=record_discovery_store,
        )

    def _assert_turn_shape(self, ev: ProbeEvidence, scenario_id: str) -> None:
        self.assertIn(
            ev.stop_reason,
            {"end_turn", "clarification_required"},
            f"{scenario_id}: {ev.compact()}",
        )
        self.assertEqual(
            ev.model_call_count, 2,
            f"{scenario_id}: bounded recovery after the tool round; "
            f"{ev.compact()}",
        )

    def _assert_offered_exact(
        self, ev: ProbeEvidence, expected: frozenset, scenario_id: str
    ) -> None:
        self.assertEqual(
            ev.offered,
            expected,
            f"{scenario_id}: request must offer exactly {sorted(expected)}; "
            f"{ev.compact()}",
        )

    def _assert_unoffered_not_offered(
        self, ev: ProbeEvidence, name: str, scenario_id: str
    ) -> None:
        self.assertNotIn(
            name,
            ev.offered,
            f"{scenario_id}: {name} must not be in the offered allowlist; "
            f"{ev.compact()}",
        )

    def _assert_unoffered_rejected(
        self, ev: ProbeEvidence, name: str, scenario_id: str
    ) -> None:
        """The enforcement gate: bounded rejection with zero side effects.

        The attempted call stays observable as a paired ToolStart/ToolEnd
        failure, but it must never reach the ledger, an executor, a provider
        seam, a store, session/trip/pending state, or a card; the model
        receives a bounded error tool-result so the next round can recover.
        """
        self.assertEqual(
            {key: mock.await_count for key, mock in ev.spies.items()},
            {key: 0 for key in ev.spies},
            f"{scenario_id}: unoffered {name} must not reach any provider "
            f"seam; {ev.compact()}",
        )
        self.assertNotIn(
            name,
            [tool for tool, _input in ev.tool_calls],
            f"{scenario_id}: unoffered {name} must not enter the ledger; "
            f"{ev.compact()}",
        )
        self.assertEqual(
            ev.provider_execution_count,
            len(
                [
                    tool
                    for tool, _input in ev.tool_calls
                    if tool != "declare_goals"
                ]
            ),
            f"{scenario_id}: only non-rejected calls may consume the ledger; "
            f"{ev.compact()}",
        )
        ends = {tool: (ok, summary, call_id) for tool, ok, summary, call_id in ev.tool_ends}
        self.assertIn(
            name,
            ends,
            f"{scenario_id}: rejected {name} must surface a bounded ToolEnd "
            f"failure; {ev.compact()}",
        )
        ok, summary, _call_id = ends[name]
        self.assertFalse(
            ok,
            f"{scenario_id}: rejected {name} ToolEnd must report failure; "
            f"{ev.compact()}",
        )
        self.assertIn(
            "not available",
            summary,
            f"{scenario_id}: rejected {name} must carry a bounded error; "
            f"{ev.compact()}",
        )
        after = _preamble_normalized(ev.state_after)
        before = _preamble_normalized(ev.state_before)
        self.assertEqual(
            after["trip_state"],
            before["trip_state"],
            f"{scenario_id}: unoffered {name} must not mutate trip state; "
            f"{ev.compact()}",
        )
        self.assertEqual(
            after["slots"],
            before["slots"],
            f"{scenario_id}: unoffered {name} must not mutate slots; "
            f"{ev.compact()}",
        )
        self.assertEqual(ev.cards, (), f"{scenario_id}: rejected {name} emitted a card")
        self.assertNotIn(
            name,
            ev.state_after["history_tool_summaries"],
            f"{scenario_id}: rejected {name} must not append a summary; "
            f"{ev.compact()}",
        )
        self.assertEqual(
            ev.state_after["pending_trip"],
            ev.state_before["pending_trip"],
            f"{scenario_id}: rejected {name} must not mutate pending trip; "
            f"{ev.compact()}",
        )
        self.assertEqual(
            ev.state_after["active_trip"],
            ev.state_before["active_trip"],
            f"{scenario_id}: rejected {name} must not mutate active trip; "
            f"{ev.compact()}",
        )
        self.assertEqual(
            ev.state_after["route_cards"],
            ev.state_before["route_cards"],
            f"{scenario_id}: rejected {name} must not mutate route cards; "
            f"{ev.compact()}",
        )
        self.assertEqual(
            ev.stored_candidate_set_ids,
            (),
            f"{scenario_id}: rejected {name} must not store candidates; "
            f"{ev.compact()}",
        )
        self.assertEqual(
            ev.discovery_store_calls,
            (),
            f"{scenario_id}: rejected {name} must not write discovery state; "
            f"{ev.compact()}",
        )

    def _assert_discovery_untouched(
        self, ev: ProbeEvidence, scenario_id: str
    ) -> None:
        self.assertEqual(
            ev.discovery_store_calls,
            (),
            f"{scenario_id}: no discovery set may bind; {ev.compact()}",
        )
        self.assertIsNone(
            ev.state_after["trip_state"]["active_discovery_set_id"],
            f"{scenario_id}: no active discovery set; {ev.compact()}",
        )
        self.assertIsNone(
            ev.state_after["trip_state"]["selected_place_id"],
            f"{scenario_id}: no selected place; {ev.compact()}",
        )


__all__ = (
    "ProbeEvidence",
    "fail_loud_spy",
    "run_probe",
    "session_projection",
    "state_projection",
    "_OfferedSurfaceBase",
)
