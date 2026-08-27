"""Batch C audit support: the separate discovery-selection state machine.

C-DISC-05 / C-DISC-06 drive the exact required transcript -- discovery
search -> "The second one." -> "Take me there." -- through the real agent
loop in ONE session. Turn 1 reuses ``_DiscoveryRouteBase._discovery_turn``;
turn 2 must offer the eight public tools and present/bind the real ordinal-2
``pl_*`` id; turn 3 must offer the same state-valid surface and run real
prepare (real opaque id + conflicting label) -> real present -> one card.

Only the established provider/data seams and deterministic Anthropic rounds
are scripted; every canonical executor, registry, store, ledger, and event
path is real. All real ids are read back from the real store between turns.

The tests fail at the EARLIEST missing production capability: the OFFERED
tool profile is asserted before any state from a scripted tool call is
credited, so a scripted unoffered tool can never create a false pass.
Failure messages embed parsed intent, offered tool names, context state,
actual tools/events, and trip-state ids.
"""

from __future__ import annotations

import dataclasses

from app.services.agent import trip_state as trip_state_module

from tests.conversation.conversation_discovery_fixtures import (
    CONFLICTING_LABEL,
    DISCOVERY_REFERENCE_TOOL_PROFILE,
    FIXED_CANDIDATE_ID,
    LEAK_MARKERS,
    NAVIGATION_FORBIDDEN_TOOLS,
    NAVIGATION_MESSAGE,
    REFERENCE_FORBIDDEN_TOOLS,
    REFERENCE_MESSAGE,
    ROUTE_NAVIGATION_TOOL_PROFILE,
    discovery_leg_for,
)
from tests.conversation.conversation_discovery_support import _DiscoveryRouteBase
from tests.conversation.conversation_matrix_harness import (
    _turn_round,
    policy_model,
    route_cards,
    run_turn,
    text_round,
)


@dataclasses.dataclass(frozen=True)
class SelectionEvidence:
    """Turn-2 snapshot captured before turn 3 resets the mock client calls."""

    events: list
    trace: object
    mocks: dict
    place2: dict
    state: dict
    offered: frozenset
    context: str
    result_blob: str
    models: tuple


@dataclasses.dataclass(frozen=True)
class RouteEvidence:
    """Turn-3 snapshot captured at the end of the transcript."""

    events: list
    trace: object
    mocks: dict
    place2: dict
    state: dict
    offered: frozenset
    context: str
    models: tuple


class _DiscoveryReferenceBase(_DiscoveryRouteBase):
    """Shared invariants for the C-DISC-05/06 three-turn transcript."""

    loop = None  # set in setUpClass by subclasses

    async def _reference_turn(
        self,
        *,
        mode: str,
        scenario_id: str,
        session: dict,
        session_id: str,
        set_id: str,
        record: dict,
    ) -> SelectionEvidence:
        """Turn 2: present the stored ordinal-2 place as the selection."""

        place2 = record["places"][1]
        rounds = [
            _turn_round(
                "present_places",
                "tu-ref",
                {
                    "discovery_set_id": set_id,
                    "selections": [
                        {"place_id": place2["place_id"], "reason": "preference_match"}
                    ],
                    "research_used": False,
                },
            ),
        ]
        trace = self.loop.TurnTrace()
        mocks: dict = {}
        events, trace = await run_turn(
            self.loop,
            session=session,
            session_id=session_id,
            message=REFERENCE_MESSAGE,
            rounds=rounds,
            mode=mode,
            trace=trace,
            mocks=mocks,
            turn_id="t2",
        )
        state = dict(trip_state_module.get_trip_state(session))
        offered = frozenset(
            schema["name"]
            for schema in self.loop.client.messages.calls[0]["tools"]
        )
        context = str(
            self.loop.client.messages.calls[0]["messages"][-1]["content"]
        )
        result_blob = self._model_tool_result_blob(round_index=0)
        models = tuple(
            call["model"] for call in self.loop.client.messages.calls
        )
        assert scenario_id
        return SelectionEvidence(
            events=events,
            trace=trace,
            mocks=mocks,
            place2=place2,
            state=state,
            offered=offered,
            context=context,
            result_blob=result_blob,
            models=models,
        )

    async def _route_turn(
        self,
        *,
        mode: str,
        session: dict,
        session_id: str,
        place2: dict,
    ) -> RouteEvidence:
        """Turn 3: real prepare (real id + conflicting label) -> present -> text."""

        rounds = [
            _turn_round(
                "prepare_route_options",
                "tu-prepare",
                {
                    "destination": CONFLICTING_LABEL,
                    "destination_place_id": place2["place_id"],
                },
            ),
            _turn_round(
                "present_route",
                "tu-present",
                {"candidate_id": FIXED_CANDIDATE_ID},
            ),
            text_round("Here is your route."),
        ]
        trace = self.loop.TurnTrace()
        mocks: dict = {}
        events, trace = await run_turn(
            self.loop,
            session=session,
            session_id=session_id,
            message=NAVIGATION_MESSAGE,
            rounds=rounds,
            mode=mode,
            trace=trace,
            mocks=mocks,
            turn_id="t3",
            prepare_leg=discovery_leg_for(place2),
            fixed_candidate_id=FIXED_CANDIDATE_ID,
        )
        state = dict(trip_state_module.get_trip_state(session))
        offered = frozenset(
            schema["name"]
            for schema in self.loop.client.messages.calls[0]["tools"]
        )
        context = str(
            self.loop.client.messages.calls[0]["messages"][-1]["content"]
        )
        models = tuple(
            call["model"] for call in self.loop.client.messages.calls
        )
        return RouteEvidence(
            events=events,
            trace=trace,
            mocks=mocks,
            place2=place2,
            state=state,
            offered=offered,
            context=context,
            models=models,
        )

    def _assert_reference_attempted(
        self,
        *,
        scenario_id: str,
        mode: str,
        ev: SelectionEvidence,
    ) -> None:
        """Earliest gate: the real request must offer the public initial surface."""

        names = [name for name, _input in ev.trace.tool_calls]
        end_map = {
            event.tool: (event.ok, event.summary)
            for event in ev.events
            if event.type == "tool_end"
        }
        assert ev.offered == DISCOVERY_REFERENCE_TOOL_PROFILE, f"{scenario_id} selection must offer exactly " f"{sorted(DISCOVERY_REFERENCE_TOOL_PROFILE)}; " f"actual offered={sorted(ev.offered)}; " f"executed={names}; tool_ends={end_map}; " f"state.set={ev.state['active_discovery_set_id']!r}; " f"state.place={ev.state['selected_place_id']!r}; " f"ctx_active_discovery={'active_discovery:' in ev.context}; " f"ctx_has_selected_place={'has_selected_place' in ev.context}"
        assert names == ["present_places"], f"{scenario_id} selection presents the stored ordinal-2 place"
        assert not set(names) & set(REFERENCE_FORBIDDEN_TOOLS), f"{scenario_id} forbidden tool executed on selection"
        expected_mode, expected_model = policy_model(self.loop, mode)
        assert (ev.trace.initial_mode, ev.trace.final_mode) == (expected_mode, expected_mode), f"{scenario_id} policy mode"
        assert list(ev.models) == [expected_model], f"{scenario_id} policy models"

    def _assert_reference_chain(
        self,
        *,
        scenario_id: str,
        set_id: str,
        ev: SelectionEvidence,
    ) -> None:
        """Required selection-only invariants (checked after the offer gate)."""

        state = ev.state
        with self.subTest(gap="ordinal2_selected_from_store"):
            assert state["active_discovery_set_id"] == set_id, f"{scenario_id} selection keeps the real discovery set"
            assert state["selected_place_id"] == ev.place2["place_id"], f"{scenario_id} selected place is the exact real ordinal-2 " f"opaque id; actual={state['selected_place_id']!r}"
        assert (state["active_candidate_set_id"], state["selected_candidate_id"]) == (None, None), f"{scenario_id} selection stores/selects no candidate"
        assert (state["temporary_candidate_set_id"], state["temporary_selected_candidate_id"], state["temporary_base_candidate_set_id"]) == (None, None, None), f"{scenario_id} no temporary scenario after selection"
        assert ev.mocks["stored_candidate_set_ids"] == [], f"{scenario_id} selection persists no candidate set"
        assert route_cards(ev.events) == [], f"{scenario_id} selection emits no route card"
        assert ev.events[0].type == "meta", f"{scenario_id} meta first"
        assert ev.events[-1].type == "done", f"{scenario_id} done last"
        assert "active_discovery:" in ev.context, f"{scenario_id} context carries the active discovery block"
        assert ev.place2["place_id"] in ev.context, f"{scenario_id} context carries the real ordinal-2 opaque id"
        for marker in ("latitude", "longitude", "ChIJ", "provider_place_id"):
            assert marker not in ev.context, f"{scenario_id} context leaked {marker}"
        for marker in ("latitude", "longitude", "ChIJ"):
            assert marker not in ev.result_blob, f"{scenario_id} model tool result leaked {marker}"
        lowered = ev.trace.final_text.casefold()
        for marker in LEAK_MARKERS:
            assert marker not in lowered, f"{scenario_id} rider text leaked {marker}"

    def _assert_route_attempted(
        self,
        *,
        scenario_id: str,
        mode: str,
        set_id: str,
        ev: RouteEvidence,
    ) -> None:
        """Turn 3 gate: the real request must offer the canonical route profile."""

        names = [name for name, _input in ev.trace.tool_calls]
        end_map = {
            event.tool: (event.ok, event.summary)
            for event in ev.events
            if event.type == "tool_end"
        }
        assert ev.offered == ROUTE_NAVIGATION_TOOL_PROFILE, f"{scenario_id} navigation must offer exactly " f"{sorted(ROUTE_NAVIGATION_TOOL_PROFILE)}; " f"actual offered={sorted(ev.offered)}; " f"executed={names}; tool_ends={end_map}; " f"set_id={set_id!r}; " f"state.place={ev.state['selected_place_id']!r}; " f"state.set={ev.state['active_discovery_set_id']!r}; " f"state.candidate_set={ev.state['active_candidate_set_id']!r}; " f"ctx_active_discovery={'active_discovery:' in ev.context}; " f"ctx_has_selected_place={'has_selected_place' in ev.context}"
        assert names == ["prepare_route_options", "present_route"], f"{scenario_id} navigation tool sequence"
        assert not set(names) & set(NAVIGATION_FORBIDDEN_TOOLS), f"{scenario_id} forbidden tool executed on navigation"
        expected_mode, expected_model = policy_model(self.loop, mode)
        assert (ev.trace.initial_mode, ev.trace.final_mode) == (expected_mode, expected_mode), f"{scenario_id} policy mode"
        # Two scripted tool rounds (prepare then present); present_route is
        # terminal when it emits the recommended card, so the scripted
        # post-present text round is never consumed by a third model call.
        assert list(ev.models) == [expected_model, expected_model], f"{scenario_id} policy models"
        lowered = ev.trace.final_text.casefold()
        for marker in LEAK_MARKERS:
            assert marker not in lowered, f"{scenario_id} rider text leaked {marker}"

    def _assert_route_chain(
        self,
        *,
        scenario_id: str,
        set_id: str,
        ev: RouteEvidence,
    ) -> None:
        """Required navigation invariants (checked after the offer gate)."""

        state = ev.state
        assert ev.trace.tool_calls[0][1]["destination_place_id"] == ev.place2["place_id"], f"{scenario_id} prepare routes by the exact selected opaque id"
        with self.subTest(gap="stored_identity_at_boundary"):
            assert ev.mocks["prepare_single_leg"].await_count == 1, f"{scenario_id} provider seam reached exactly once; " f"actual={ev.mocks['prepare_single_leg'].await_count}"
            resolved = (
                ev.mocks["prepare_single_leg"].await_args.kwargs.get(
                    "resolved_destination"
                )
                if ev.mocks["prepare_single_leg"].await_count
                else None
            )
            assert resolved is not None, f"{scenario_id} provider boundary must receive the stored " f"canonical destination"
            assert resolved.name == "B Pizza", f"{scenario_id} stored name wins over the conflicting label"
            assert resolved.latitude == float(ev.place2["latitude"]), f"{scenario_id} stored latitude wins"
            assert resolved.longitude == float(ev.place2["longitude"]), f"{scenario_id} stored longitude wins"
            assert resolved.place_id == ev.place2["place_id"], f"{scenario_id} stored opaque identity wins"
            assert resolved.provider_place_id == ev.place2["provider_place_id"], f"{scenario_id} stored provider identity stays private"
        cards = route_cards(ev.events)
        assert len(cards) == 1, f"{scenario_id} exactly one route card; actual={len(cards)}"
        assert len(ev.mocks["stored_candidate_set_ids"]) == 1, f"{scenario_id} exactly one candidate set stored; " f"actual={ev.mocks['stored_candidate_set_ids']}"
        assert state["active_candidate_set_id"] == ev.mocks["stored_candidate_set_ids"][0], f"{scenario_id} accepted active candidate set committed"
        assert state["selected_candidate_id"] == FIXED_CANDIDATE_ID, f"{scenario_id} accepted selected candidate committed"
        assert (state["temporary_candidate_set_id"], state["temporary_selected_candidate_id"], state["temporary_base_candidate_set_id"]) == (None, None, None), f"{scenario_id} no temporary scenario after the commit"
        assert state["selected_place_id"] == ev.place2["place_id"], f"{scenario_id} selected place stays the ordinal-2 opaque id"
        assert state["active_discovery_set_id"] == set_id, f"{scenario_id} discovery set stays correctly associated"
        assert state["destination"] == "B Pizza", f"{scenario_id} destination is the stored canonical name"
        assert ev.events[-1].type == "done", f"{scenario_id} terminal event"


__all__ = ("RouteEvidence", "SelectionEvidence", "_DiscoveryReferenceBase")
