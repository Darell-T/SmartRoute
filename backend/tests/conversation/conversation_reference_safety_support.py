"""Batch E1 audit support: discovery-reference expiry/stale/invented safety.

Non-test module (no ``Test*``/``test_*`` names at module level): pytest never
collects it. Drives the real agent loop (``loop.run_agent_turn``) with the
production state-scoped tool surface, real registry/executors, real
discovery/candidate/trip stores, prompt context, ledger, and events. Only
deterministic Anthropic rounds and the external structured POI seam are
scripted; set expiry goes through the real store's TTL boundary by advancing
the discovery-store clock deterministically (no sleep, no network).

Every loop test asserts the OFFERED tool profile before crediting any
scripted tool state, so a scripted unoffered tool can never create a false
pass. Real ``ds_*``/``pl_*`` ids are always read back from the real store;
invented ids are used only in the explicit malicious-input cases.
"""

from __future__ import annotations

import dataclasses
from contextlib import contextmanager
from unittest.mock import AsyncMock, patch

from app.services.agent import discovery_store
from app.services.agent import trip_state as trip_state_module
from app.services.agent.tools._types import ToolContext
from app.services.agent.tools.places import discover_places

from tests.conversation.conversation_discovery_support import _DiscoveryRouteBase
from tests.conversation.conversation_long_state_support import _model_led_rounds
from tests.conversation.conversation_matrix_harness import (
    _turn_round,
    complete_turn_round,
    discovery_id_tokens,
    policy_model,
    present_places_round,
    route_cards,
    run_turn,
)
from tests.conversation.conversation_reference_safety_fixtures import (
    CONFLICTING_LABEL,
    CONTROL_RESEARCH_MESSAGE,
    DISCOVERY_MESSAGE,
    DISCOVERY_REFERENCE_TOOL_PROFILE,
    DISCOVERY_TOOL_PROFILE,
    EXPIRED_ERROR_MARKER,
    LEAK_MARKERS,
    NAVIGATION_MESSAGE,
    RECOVERY_MESSAGE,
    RECOVERY_REQUIRED_PROFILE,
    REFERENCE_MESSAGE,
    ROUTE_NAVIGATION_TOOL_PROFILE,
    SEARCH_INPUT,
    discovery_leg_for,
    poi_result,
)


def present_one_round(tool_id: str, set_id: str, place_id: str) -> dict:
    return _turn_round(
        "present_places",
        tool_id,
        {
            "discovery_set_id": set_id,
            "selections": [{"place_id": place_id, "reason": "preference_match"}],
            "research_used": False,
            "goal_key": "places",
            "lead_in": "",
            "follow_up": "",
        },
    )


def _complete_public_inputs(rounds: list[dict]) -> list[dict]:
    """Fill strict-schema fields omitted by compact reference scenarios."""

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
            elif name == "prepare_route_options":
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
                    tool_input.setdefault(key, None)
                tool_input.setdefault("activity_label", None)
            elif name in {"present_places", "present_transit", "present_route"}:
                tool_input.setdefault("lead_in", "")
                tool_input.setdefault("follow_up", "")
            calls.append({**call, "input": tool_input})
        completed.append({**scripted, "tool_use": calls})
    return completed


@dataclasses.dataclass(frozen=True)
class TurnSnapshot:
    """One loop turn's evidence, captured before the next turn resets mocks."""

    events: list
    trace: object
    mocks: dict
    state: dict
    offered: frozenset
    context: str
    result_blob: str
    models: tuple


class _ReferenceSafetyBase(_DiscoveryRouteBase):
    """Shared invariants for the Batch E1 discovery-reference safety cases."""

    loop = None  # set in setUpClass by subclasses

    @contextmanager
    def _expired_clock(self, record: dict):
        """Advance the real store clock past ``expires_at`` (deterministic)."""

        with patch(
            "app.services.agent.discovery_store.time.time",
            return_value=float(record["expires_at"]) + 60.0,
        ):
            yield

    def _tool_ctx(self, session: dict, session_id: str) -> ToolContext:
        return ToolContext(
            session=session,
            session_id=session_id,
            turn_id="t-e1",
            now_et="2026-08-08T12:00:00-04:00",
            origin={"lat": 40.75, "lng": -73.99},
        )

    async def _fresh_discovery(
        self, *, mode: str, scenario_id: str, message: str, turn_id: str = "t1"
    ) -> tuple[dict, str, str, dict]:
        """One new session with one real discovery search."""

        session_id, session = self._new_session(mode)
        return await self._search_turn(
            mode=mode, scenario_id=scenario_id, session=session,
            session_id=session_id, message=message, turn_id=turn_id,
        )

    async def _expired_turn(
        self, *, mode: str, session: dict, session_id: str, record: dict,
        message: str, rounds: list, turn_id: str, prepare_leg=None,
    ) -> TurnSnapshot:
        """Run one scripted turn under the expired clock of ``record``."""

        with self._expired_clock(record):
            return await self._scripted_turn(
                mode=mode, session=session, session_id=session_id,
                message=message, rounds=rounds, turn_id=turn_id,
                prepare_leg=prepare_leg,
            )

    async def _search_turn(
        self, *, mode: str, scenario_id: str, session: dict, session_id: str,
        message: str, turn_id: str,
    ) -> tuple[dict, str, str, dict]:
        """One real discovery search; returns (session, session_id, set_id, record)."""

        set_token, place_tokens = discovery_id_tokens(session_id, turn_id)
        place_ids = iter(place_tokens)
        # The session registry intentionally keeps one stable opaque id for a
        # canonical place across successive discovery sets. Reusing the
        # synthetic first-search ids in the second present call would make the
        # real presenter reject the current set before latest-ordinal
        # resolution was exercised.
        registry = discovery_store.presented_entity_registry(session)
        registry_place_ids = {
            int(entry["ordinal"]): str(entry["place_id"])
            for entry in registry
            if isinstance(entry, dict)
            and str(entry.get("place_id") or "").strip()
            and str(entry.get("ordinal") or "").isdigit()
        }
        presentation_ids = tuple(
            registry_place_ids.get(index, place_tokens[index - 1])
            for index in range(1, len(place_tokens) + 1)
        )
        rounds = _complete_public_inputs([
            _turn_round("discover_places", f"tu-disc-{turn_id}", dict(SEARCH_INPUT)),
            present_places_round(
                f"tu-pres-{turn_id}", set_token, presentation_ids
            ),
        ])
        trace = self.loop.TurnTrace()
        mocks: dict = {}
        with (
            patch.object(
                discover_places.search_local_places, "execute",
                new=AsyncMock(return_value=poi_result()),
            ),
            patch.object(discovery_store, "new_discovery_set_id", return_value=set_token),
            patch.object(discovery_store, "new_place_id", side_effect=lambda: next(place_ids)),
        ):
            adapted_rounds, _evidence_id = _model_led_rounds(
                rounds, turn_id=turn_id
            )
            events, trace = await run_turn(
                self.loop, session=session, session_id=session_id,
                message=message, rounds=adapted_rounds, mode=mode, trace=trace,
                mocks=mocks, turn_id=turn_id,
            )
        state = trip_state_module.get_trip_state(session)
        set_id = state["active_discovery_set_id"]
        assert set_id
        assert set_id.startswith("ds_"), f"{scenario_id} search binds a real server-owned discovery set"
        record = discovery_store.load_discovery_set(set_id, session_id=session_id)
        assert record is not None, f"{scenario_id} stored discovery record"
        names = [name for name, _input in trace.tool_calls]
        assert names == ["declare_goals", "discover_places", "present_places"], f"{scenario_id} runs the public discovery path"
        assert route_cards(events) == [], f"{scenario_id} discovery emits no route card"
        assert mocks["stored_candidate_set_ids"] == [], f"{scenario_id} discovery stores no candidate set"
        assert state["selected_place_id"] is None, f"{scenario_id} discovery clears any previous selection"
        expected_mode, expected_model = policy_model(self.loop, mode)
        assert (trace.initial_mode, trace.final_mode) == (expected_mode, expected_mode), f"{scenario_id} policy mode"
        assert [call["model"] for call in self.loop.client.messages.calls] == [expected_model] * len(self.loop.client.messages.calls), f"{scenario_id} policy models"
        offered = frozenset(
            schema["name"] for schema in self.loop.client.messages.calls[0]["tools"]
        )
        assert offered == DISCOVERY_TOOL_PROFILE, f"{scenario_id} discovery tool profile; actual={sorted(offered)}"
        return session, session_id, set_id, record

    async def _scripted_turn(
        self, *, mode: str, session: dict, session_id: str, message: str,
        rounds: list, turn_id: str, prepare_leg=None,
    ) -> TurnSnapshot:
        """Run one real loop turn and snapshot the offered/executed contract."""

        trace = self.loop.TurnTrace()
        mocks: dict = {}
        adapted_rounds, _evidence_id = _model_led_rounds(
            _complete_public_inputs(rounds), turn_id=turn_id
        )
        events, trace = await run_turn(
            self.loop, session=session, session_id=session_id,
            message=message, rounds=adapted_rounds, mode=mode, trace=trace,
            mocks=mocks, turn_id=turn_id, prepare_leg=prepare_leg,
        )
        state = dict(trip_state_module.get_trip_state(session))
        offered = frozenset(
            schema["name"] for schema in self.loop.client.messages.calls[0]["tools"]
        )
        context = str(self.loop.client.messages.calls[0]["messages"][-1]["content"])
        result_blob = ""
        if len(self.loop.client.messages.calls) >= 2:
            result_blob = str(
                self.loop.client.messages.calls[1]["messages"][-1]["content"]
            )
        models = tuple(call["model"] for call in self.loop.client.messages.calls)
        return TurnSnapshot(
            events=events, trace=trace, mocks=mocks, state=state,
            offered=offered, context=context, result_blob=result_blob,
            models=models,
        )

    def _tool_ends(self, ev: TurnSnapshot) -> dict:
        return {
            event.tool: (event.ok, event.summary)
            for event in ev.events
            if event.type == "tool_end"
        }

    def _assert_meta_done(self, scenario_id: str, ev: TurnSnapshot) -> None:
        assert ev.events[0].type == "meta", f"{scenario_id} meta first"
        assert ev.events[-1].type == "done", f"{scenario_id} done last"

    def _assert_no_route_surface(
        self, scenario_id: str, ev: TurnSnapshot, *,
        forbidden: tuple = ("prepare_route_options", "present_route"),
    ) -> None:
        """No provider route call, card, or candidate storage on this turn."""

        names = [name for name, _input in ev.trace.tool_calls]
        assert not set(names) & set(forbidden), f"{scenario_id} forbidden tool executed; actual={names}"
        assert route_cards(ev.events) == [], f"{scenario_id} no route card emitted"
        assert ev.mocks["stored_candidate_set_ids"] == [], f"{scenario_id} no candidate set stored; " f"actual={ev.mocks['stored_candidate_set_ids']}"
        prepare = ev.mocks["prepare_single_leg"]
        assert prepare is None or prepare.await_count == 0, f"{scenario_id} provider route seam must not be reached; " f"actual_await_count={prepare.await_count if prepare is not None else None}"
        self._assert_meta_done(scenario_id, ev)

    def _assert_pristine_trip_state(
        self, scenario_id: str, state: dict
    ) -> None:
        """Discovery-only turns never touch route/candidate/profile fields."""

        assert (state["origin"], state["destination"], state["waypoints"]) == (None, None, []), f"{scenario_id} route facts untouched"
        assert (state["active_candidate_set_id"], state["selected_candidate_id"], state["temporary_candidate_set_id"], state["temporary_selected_candidate_id"], state["temporary_base_candidate_set_id"]) == (None, None, None, None, None), f"{scenario_id} candidate/scenario fields untouched"

    def _assert_policy(self, scenario_id: str, mode: str, ev: TurnSnapshot) -> None:
        expected_mode, expected_model = policy_model(self.loop, mode)
        assert (ev.trace.initial_mode, ev.trace.final_mode) == (expected_mode, expected_mode), f"{scenario_id} policy mode"
        assert list(ev.models) == [expected_model] * len(ev.models), f"{scenario_id} policy models; actual={list(ev.models)}"

    def _assert_no_text_leak(self, scenario_id: str, ev: TurnSnapshot) -> None:
        lowered = ev.trace.final_text.casefold()
        for marker in LEAK_MARKERS:
            assert marker not in lowered, f"{scenario_id} text leaked {marker}"

    def _assert_safe_reference_failure(
        self, *, scenario_id: str, mode: str, set_id: str, ev: TurnSnapshot,
        message: str,
    ) -> None:
        """Case 1: an expired set resolves nothing and binds nothing."""

        names = [name for name, _input in ev.trace.tool_calls]
        end_map = self._tool_ends(ev)
        assert ev.offered == DISCOVERY_REFERENCE_TOOL_PROFILE, f"{scenario_id} selection must offer the model-led initial surface; " f"actual={sorted(ev.offered)}; " f"executed={names}; tool_ends={end_map}; " f"state.set={ev.state['active_discovery_set_id']!r}; " f"state.place={ev.state['selected_place_id']!r}; " f"message={message!r}"
        assert names == ["declare_goals", "complete_turn"], f"{scenario_id} stale selection stays on the bounded terminal path"
        assert not set(names) & {"present_places", "prepare_route_options", "present_route"}, f"{scenario_id} no stale presenter or route surface"
        complete_attempt = next(
            (
                attempt
                for attempt in ev.trace.capability_attempts
                if attempt["capability"] == "complete_turn"
            ),
            None,
        )
        assert complete_attempt is not None, (
            f"{scenario_id} terminal fallback must complete safely; "
            f"attempts={ev.trace.capability_attempts}"
        )
        assert complete_attempt["ok"] is True, (
            f"{scenario_id} terminal fallback must complete safely; "
            f"attempts={ev.trace.capability_attempts}"
        )
        state = ev.state
        assert state["active_discovery_set_id"] == set_id, f"{scenario_id} expired set stays bound (no silent reactivation)"
        assert state["selected_place_id"] is None, f"{scenario_id} no place is selected from the expired set"
        self._assert_pristine_trip_state(scenario_id, state)
        self._assert_no_route_surface(scenario_id, ev)
        assert "active_discovery:" not in ev.context, f"{scenario_id} expired set is not surfaced to the model"
        for marker in ("latitude", "longitude", "ChIJ"):
            assert marker not in ev.result_blob, f"{scenario_id} model result leaked {marker}"
        self._assert_no_text_leak(scenario_id, ev)
        self._assert_policy(scenario_id, mode, ev)

    async def _case2_recovery(self, mode: str) -> None:
        """E1-CASE2 loop transcript: exact recovery offers+executes the search.

        The scripted model emits exactly one ``discover_places`` call with
        the deterministic prior-query input (``SEARCH_INPUT``), followed by
        the state-valid ``present_places`` call, so the executed tool state can never be credited before the
        offered-profile gate. Live-model phrasing/query reconstruction is a
        later live-model evaluation item; this wiring proves the tool is
        offered and the real executor binds a fresh server-owned set.
        """

        scenario_id = f"E1C2-{mode}"
        session, session_id, set_id, record = await self._fresh_discovery(
            mode=mode, scenario_id=scenario_id, message=DISCOVERY_MESSAGE)
        rec_set, rec_places = discovery_id_tokens(session_id, "t2")
        rec_ids = iter(rec_places)
        rounds = [
            _turn_round("discover_places", "tu-rec", dict(SEARCH_INPUT)),
            present_places_round("tu-rec-pres", rec_set, rec_places),
        ]
        with self._expired_clock(record), patch.object(
            discover_places.search_local_places, "execute",
            new=AsyncMock(return_value=poi_result()),
        ), patch.object(
            discovery_store, "new_discovery_set_id", return_value=rec_set,
        ), patch.object(
            discovery_store, "new_place_id", side_effect=lambda: next(rec_ids),
        ):
            ev = await self._scripted_turn(
                mode=mode, session=session, session_id=session_id,
                message=RECOVERY_MESSAGE, rounds=rounds, turn_id="t2")
            old_loads_none = (
                discovery_store.load_discovery_set(
                    set_id, session_id=session_id)
                is None)
            new_set_id = ev.state["active_discovery_set_id"]
            new_record = discovery_store.load_discovery_set(
                new_set_id, session_id=session_id)
        names = [name for name, _input in ev.trace.tool_calls]
        evidence = (
            f"offered={sorted(ev.offered)}; "
            f"required={sorted(RECOVERY_REQUIRED_PROFILE)}; executed={names}; "
            f"state.set={ev.state['active_discovery_set_id']!r}; "
            f"state.selected={ev.state['selected_place_id']!r}; "
            f"old_set_loads_none={old_loads_none}; "
            f"cards={len(route_cards(ev.events))}; "
            f"stored_candidate_sets={ev.mocks['stored_candidate_set_ids']}")
        # P1 offer gate: assert the exact search-only offer BEFORE crediting
        # tool state, so a scripted unoffered tool can never create a false pass.
        assert ev.offered == RECOVERY_REQUIRED_PROFILE, f"{scenario_id} P1 recovery offer gate: {RECOVERY_MESSAGE!r} must " f"offer exactly {sorted(RECOVERY_REQUIRED_PROFILE)}; {evidence}"
        assert names == ["declare_goals", "discover_places", "present_places"], f"{scenario_id} recovery executes the public discovery path; {evidence}"
        assert not set(names) & {"prepare_route_options", "present_route"}, f"{scenario_id} no route surface on recovery; {evidence}"
        assert old_loads_none, f"{scenario_id} old set stays expired"
        assert new_set_id != set_id, f"{scenario_id} recovery creates a NEW set; {evidence}"
        assert new_set_id
        assert new_set_id.startswith("ds_"), f"{scenario_id} new set is server-owned; {evidence}"
        assert new_record is not None, f"{scenario_id} new set is session-owned; {evidence}"
        assert ev.state["selected_place_id"] is None, f"{scenario_id} recovery binds no place; {evidence}"
        self._assert_no_route_surface(
            scenario_id, ev, forbidden=("prepare_route_options", "present_route"))
        self._assert_policy(scenario_id, mode, ev)

    async def _case2_control(self, mode: str) -> None:
        """E1-CASE2 control: recognized discovery intent creates a fresh set."""

        scenario_id = f"E1C2-CTL-{mode}"
        session, session_id, set_id, record = await self._fresh_discovery(
            mode=mode, scenario_id=scenario_id, message=DISCOVERY_MESSAGE)
        with self._expired_clock(record):
            _session, _sid, new_set_id, new_record = await self._search_turn(
                mode=mode, scenario_id=scenario_id, session=session,
                session_id=session_id, message=CONTROL_RESEARCH_MESSAGE,
                turn_id="t2")
            old_loads_none = (
                discovery_store.load_discovery_set(set_id, session_id=session_id)
                is None)
        assert new_set_id
        assert new_set_id.startswith("ds_"), f"{scenario_id} new server-owned set; old={set_id!r} new={new_set_id!r}"
        assert new_set_id != set_id, f"{scenario_id} new server-owned set; old={set_id!r} new={new_set_id!r}"
        assert old_loads_none, f"{scenario_id} old set stays expired"
        assert new_record is not None, f"{scenario_id} new set is session-owned"
        assert trip_state_module.get_trip_state(session)["selected_place_id"] is None, f"{scenario_id} binds no place"

    async def _case3_stale_navigation(self, mode: str) -> None:
        """E1-CASE3 loop transcript: stale selection must never route/present."""

        await self._case3_stale_prepare(mode, label_only=False)

    async def _case3_stale_label_only_loop(self, mode: str) -> None:
        """E1-CASE3 loop variant: label-only prepare after expiry fails bounded.

        After real selection and deterministic expiry, the scripted model
        emits canonical ``prepare_route_options`` with the matching label and
        a null ``destination_place_id``; destination resolution must fail
        before provider/store/state/card mutation (no text-label fallback
        authority from a stale selection).
        """

        await self._case3_stale_prepare(mode, label_only=True)

    async def _case3_stale_prepare(self, mode: str, *, label_only: bool) -> None:
        """One shared stale-selection transcript, explicit-id or label-only."""

        scenario_id = f"E1C3-{'LABEL-' if label_only else ''}{mode}"
        session, session_id, set_id, record = await self._fresh_discovery(
            mode=mode, scenario_id=scenario_id, message=DISCOVERY_MESSAGE)
        place2 = record["places"][1]
        rounds_ref = [present_one_round("tu-ref", set_id, place2["place_id"])]
        ev_ref = await self._scripted_turn(
            mode=mode, session=session, session_id=session_id,
            message=REFERENCE_MESSAGE, rounds=rounds_ref, turn_id="t2",
            prepare_leg=discovery_leg_for(place2))
        assert ev_ref.offered == DISCOVERY_REFERENCE_TOOL_PROFILE, f"{scenario_id} selection offers the model-led initial surface"
        assert ev_ref.state["selected_place_id"] == place2["place_id"], f"{scenario_id} selection binds the real ordinal-2 opaque id"
        prepare_input = (
            {"destination": place2["name"]}
            if label_only
            else {"destination": CONFLICTING_LABEL,
                  "destination_place_id": place2["place_id"]}
        )
        rounds_prep = [_turn_round(
            "prepare_route_options", "tu-prep", prepare_input),
            complete_turn_round(
                "tu-prep-done",
                "I could not resolve that place.",
                outcome="unavailable",
            )]
        ev = await self._expired_turn(
            mode=mode, session=session, session_id=session_id, record=record,
            message=NAVIGATION_MESSAGE, rounds=rounds_prep, turn_id="t3",
            prepare_leg=discovery_leg_for(place2))
        names = [name for name, _input in ev.trace.tool_calls]
        end_map = self._tool_ends(ev)
        assert ev.offered == ROUTE_NAVIGATION_TOOL_PROFILE, f"{scenario_id} navigation offers the canonical route profile; " f"actual={sorted(ev.offered)}; " f"executed={names}; tool_ends={end_map}; " f"state.place={ev.state['selected_place_id']!r}; " f"state.set={ev.state['active_discovery_set_id']!r}"
        assert names[:2] == ["declare_goals", "prepare_route_options"], f"{scenario_id} runs the real prepare executor"
        assert not set(names) & {"present_route"}, f"{scenario_id} no search/present on stale navigation"
        prepare_call = ev.trace.tool_calls[1]
        assert prepare_call[0] == "prepare_route_options", f"{scenario_id} declaration is followed by prepare"
        if label_only:
            assert prepare_call[1].get("destination_place_id") is None, f"{scenario_id} model leaves the opaque id null (label-only)"
        else:
            assert prepare_call[1]["destination_place_id"] == place2["place_id"], f"{scenario_id} prepare routes by the exact stale opaque id"
        prep_end = next(
            (e for e in ev.events
             if e.type == "tool_end" and e.tool == "prepare_route_options"), None)
        assert prep_end is not None, f"{scenario_id} prepare must fail safely; tool_ends={end_map}"
        assert prep_end.ok is False, f"{scenario_id} prepare must fail safely; tool_ends={end_map}"
        marker = "no longer available" if label_only else EXPIRED_ERROR_MARKER
        assert (prep_end.summary if prep_end else None) == "Route options could not be prepared", f"{scenario_id} rider-safe stale-reference failure"
        assert marker not in (prep_end.summary or "" if prep_end else ""), f"{scenario_id} hides internal stale-reference diagnostics"
        assert ev.mocks["prepare_single_leg"].await_count == 0, f"{scenario_id} provider route seam must not be reached; " f"actual={ev.mocks['prepare_single_leg'].await_count}"
        assert route_cards(ev.events) == [], f"{scenario_id} no route card"
        assert ev.mocks["stored_candidate_set_ids"] == [], f"{scenario_id} no candidate set stored"
        assert ev.state["selected_place_id"] == place2["place_id"], f"{scenario_id} stale selection stays bound after safe failure"
        assert ev.state["active_discovery_set_id"] == set_id, f"{scenario_id} expired set stays bound after safe failure"
        assert ev.state["destination"] is None, f"{scenario_id} no destination set"
        self._assert_pristine_trip_state(scenario_id, ev.state)
        assert "active_discovery:" not in ev.context, f"{scenario_id} expired set is not surfaced to the model"
        assert place2["place_id"] not in ev.context, f"{scenario_id} stale opaque id is not surfaced to the model"
        for marker in ("latitude", "longitude", "ChIJ"):
            assert marker not in ev.result_blob, f"{scenario_id} model result leaked {marker}"
        self._assert_no_text_leak(scenario_id, ev)
        self._assert_policy(scenario_id, mode, ev)


__all__ = ("TurnSnapshot", "_ReferenceSafetyBase", "present_one_round")
