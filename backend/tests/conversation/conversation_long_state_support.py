"""Batch H support: shared invariants for long mixed-domain transcripts.

Non-test module (no ``Test*``/``test_*`` names): pytest never collects it.
Drives the real agent loop (``loop.run_agent_turn``) with production
intent/tool filtering and the real registered executors, plus the real
stores, across 10-34 turn transcripts in ONE session. Every turn records the
exact OFFERED tool profile, the ACTUAL executed tools and forbidden-tool
absence, and an immutable before/after ``SessionProjection`` of the
server-owned trip state, active card, route cards, and profile preferences.
Only genuine provider/data seams (``tests.conversation.conversation_long_state_fixtures``)
and deterministic model rounds are scripted; Anthropic is mock text. The
per-session per-minute rate limit is raised via env for these long
one-session transcripts -- the guardrail logic itself stays real.
"""

from __future__ import annotations

import copy
import dataclasses
import importlib
import os
import secrets
import unittest
from contextlib import nullcontext
from unittest.mock import AsyncMock, patch

from app.services.agent import candidate_store, discovery_store
from app.services.incidents import index as incident_index
from app.services.mta import realtime as mta_realtime
from app.services.agent.tools.transit import evidence as transit_evidence
from app.services.agent import trip_state as trip_state_module
from app.services.agent.tools.transit import lookup_arrivals
from app.services.agent.tools.places import discover_places
from app.services.agent.tools._types import ToolResult
from tests.conversation.conversation_discovery_fixtures import poi_result
from tests.conversation.conversation_long_state_fixtures import (
    ACCEPT_MESSAGE,
    ACCEPT_TOOL_PROFILE,
    ARRIVAL_TOOL_PROFILE,
    CONFLICTING_LABEL,
    DISCOVERY_TOOL_PROFILE,
    FORBIDDEN_TOOLS,
    LEAK_MARKERS,
    REJECT_MESSAGE,
    REJECT_TOOL_PROFILE,
    ROUTE_TOOL_PROFILE,
    SELECT_TOOL_PROFILE,
    STALE_PROBE_MESSAGE,
    TRANSIT_TOOL_PROFILE,
)
from tests.conversation.conversation_matrix_harness import (
    _turn_round,
    check_transit_input,
    clear_caches,
    complete_turn_round,
    discover_search_input,
    discovery_id_tokens,
    load_agent_loop,
    new_session,
    policy_model,
    present_places_round,
    route_cards,
    run_turn,
)


def coffee_poi_result() -> ToolResult:
    """Return fresh coffee identities for the later H-03 discovery turn."""

    places = [
        {
            "name": "A Coffee",
            "address": "11 Coffee Way, Brooklyn, NY",
            "lat": 40.684,
            "lng": -73.975,
            "open_now": True,
            "price_level": 2,
            "rating": 4.6,
            "review_count": 180,
            "place_id": "ChIJ-coffee-aaa",
            "address_components": [
                {"longText": "Brooklyn", "types": ["sublocality_level_1"]}
            ],
        },
        {
            "name": "B Coffee",
            "address": "12 Coffee Way, Brooklyn, NY",
            "lat": 40.685,
            "lng": -73.974,
            "open_now": True,
            "price_level": 1,
            "rating": 4.4,
            "review_count": 120,
            "place_id": "ChIJ-coffee-bbb",
            "address_components": [
                {"longText": "Brooklyn", "types": ["sublocality_level_1"]}
            ],
        },
        {
            "name": "C Coffee",
            "address": "13 Coffee Way, Brooklyn, NY",
            "lat": 40.686,
            "lng": -73.973,
            "open_now": True,
            "price_level": 3,
            "rating": 4.8,
            "review_count": 240,
            "place_id": "ChIJ-coffee-ccc",
            "address_components": [
                {"longText": "Brooklyn", "types": ["sublocality_level_1"]}
            ],
        },
    ]
    return ToolResult(
        ok=True,
        data={"results": places},
        summary="3 fresh coffee places",
    )
TRIP_STATE_KEYS = (
    "origin", "destination", "waypoints", "planning_mode",
    "requested_departure", "requested_arrival", "preferences",
    "active_candidate_set_id", "selected_candidate_id",
    "temporary_candidate_set_id", "temporary_selected_candidate_id",
    "temporary_base_candidate_set_id", "active_discovery_set_id",
    "selected_place_id",
)
TEMPORARY_KEYS = (
    "temporary_candidate_set_id",
    "temporary_selected_candidate_id",
    "temporary_base_candidate_set_id",
)

_INITIAL_MODEL_TOOL_PROFILE = frozenset(
    {
        "declare_goals",
        "discover_places",
        "check_transit",
        "prepare_route_options",
        "complete_turn",
    }
)


def _goal_for_model_tool(
    name: str, tool_input: dict, *, selection_only: bool = False
) -> tuple[str, str] | None:
    """Map a scripted capability to the outcome contract it would declare.

    These tests intentionally do not reproduce natural-language intent
    classification.  They only add the structured model output that the real
    loop now requires before executing a public capability.
    """

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
    rounds: list[dict],
    *,
    turn_id: str,
) -> tuple[list[dict], str | None]:
    """Adapt legacy scripted model rounds to the current public protocol.

    The adapter is test-only: the fake model declares goals before the first
    provider capability.  State-valid discovery presenters use a separate
    response after declaration. Provider-grounded transit answers use
    ``present_transit`` so these long transcripts exercise the canonical
    presenter rather than the retired free-form terminal.
    """

    calls = [
        call
        for scripted in rounds
        for call in scripted.get("tool_use") or []
        if str(call.get("name") or "") != "declare_goals"
    ]
    names = {str(call.get("name") or "") for call in calls}
    selection_only = "present_places" in names and "discover_places" not in names
    goals: list[dict] = []
    by_name: dict[str, tuple[str, str]] = {}
    for call in calls:
        name = str(call.get("name") or "")
        spec = _goal_for_model_tool(
            name, call.get("input") or {}, selection_only=selection_only
        )
        if spec is None:
            continue
        key, kind = spec
        if name == "complete_turn" and any(
            prior_name in {"discover_places", "prepare_route_options", "check_transit"}
            for prior_name in (str(item.get("name") or "") for item in calls)
            if prior_name != name
        ):
            # A provider-grounded terminal is only a recovery path.  Its
            # declared goal is the provider goal, not a second general answer.
            provider = next(
                (
                    _goal_for_model_tool(
                        str(item.get("name") or ""),
                        item.get("input") or {},
                        selection_only=selection_only,
                    )
                    for item in calls
                    if str(item.get("name") or "")
                    in {"discover_places", "prepare_route_options", "check_transit"}
                ),
                None,
            )
            if provider is not None:
                key, kind = provider
        if key not in by_name:
            by_name[key] = (key, kind)
            goals.append({"goal_key": key, "kind": kind, "depends_on": []})

    if not goals:
        return rounds, None

    evidence_id = f"te_test_{turn_id}"
    adapted: list[dict] = []
    declared = False
    for scripted in rounds:
        tool_uses = scripted.get("tool_use") or []
        if not tool_uses:
            adapted.append(scripted)
            continue
        # A fresh follow-up may only use the initial five capabilities in the
        # declaration response. A presenter for already-owned discovery or a
        # validated temporary route becomes state-valid after declaration, so
        # model it as the next response.
        if not declared and all(
            str(call.get("name") or "") in {"present_places", "present_route"}
            for call in tool_uses
        ):
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
                outcome = str(tool_input.get("outcome") or "answer")
                provider = next(
                    (
                        _goal_for_model_tool(
                            str(item.get("name") or ""),
                            item.get("input") or {},
                            selection_only=selection_only,
                        )
                        for item in calls
                        if str(item.get("name") or "")
                        in {"discover_places", "prepare_route_options", "check_transit"}
                    ),
                    None,
                )
                existing_goal_keys = tool_input.get("goal_keys")
                if provider is not None:
                    provider_key = provider[0]
                    tool_input["goal_keys"] = [provider_key]
                    tool_input.pop("goal_key", None)
                    if outcome == "answer":
                        tool_input["outcome"] = "unavailable"
                elif not (
                    isinstance(existing_goal_keys, list)
                    and existing_goal_keys
                ):
                    tool_input["goal_keys"] = ["response"]
                spec = _goal_for_model_tool(
                    name, tool_input, selection_only=selection_only
                )
            if name == "check_transit":
                # All provider-backed transit results use the canonical
                # presenter.  An unavailable arrival fixture is represented by
                # an empty evidence set, not a free-form terminal.
                pass
            elif name == "complete_turn":
                provider_name = next(
                    (
                        str(item.get("name") or "")
                        for item in calls
                        if str(item.get("name") or "")
                        in {"check_transit"}
                    ),
                    None,
                )
                if (
                    provider_name == "check_transit"
                    and outcome == "answer"
                ):
                    name = "present_transit"
                    tool_input = {
                        "goal_key": "transit",
                        "evidence_set_id": evidence_id,
                    }
            if name == "present_transit":
                tool_input["goal_key"] = "transit"
                tool_input.setdefault("evidence_set_id", evidence_id)
            elif spec is not None and name not in {"declare_goals", "complete_turn"}:
                tool_input["goal_key"] = spec[0]
            transformed.append({**call, "name": name, "input": tool_input})
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

def load_h_agent_loop():
    """Load the real loop with the raised per-session turn cap applied."""
    import app.services.agent.model.budget as budget_module
    with patch.dict(
        os.environ,
        {"AGENT_TURNS_PER_SESSION_PER_MIN": "1000"},
        clear=False,
    ):
        importlib.reload(budget_module)
        return load_agent_loop()

def stored_place(session_id: str, discovery_set_id: str, ordinal: int) -> dict:
    """Read the REAL server-owned discovery record place at 1-based ordinal."""
    from app.services.agent import discovery_store
    record = discovery_store.load_discovery_set(discovery_set_id, session_id=session_id)
    if record is None:
        raise AssertionError(f"discovery set {discovery_set_id} not stored")
    return record["places"][ordinal - 1]

@dataclasses.dataclass(frozen=True)
class SessionProjection:
    """Immutable before/after snapshot of server-owned conversational state."""

    trip_state: dict
    active_card_id: str | None
    route_card_ids: tuple
    preferences: dict

    @classmethod
    def capture(cls, session: dict) -> "SessionProjection":
        state = trip_state_module.get_trip_state(session)
        active = session.get("active_trip")
        return cls(
            trip_state={key: copy.deepcopy(state[key]) for key in TRIP_STATE_KEYS},
            active_card_id=active.get("card_id") if isinstance(active, dict) else None,
            route_card_ids=tuple(
                card.get("card_id")
                for card in session.get("route_cards") or []
                if isinstance(card, dict)),
            preferences=copy.deepcopy(
                (session.get("profile") or {}).get("preferences") or {}),
        )

    def diff(self, other: "SessionProjection", *, exclude: tuple = ()) -> list:
        changed = ["active_card_id"] if self.active_card_id != other.active_card_id else []
        changed += ["route_card_ids"] if self.route_card_ids != other.route_card_ids else []
        changed += ["preferences"] if self.preferences != other.preferences else []
        changed += [f"trip_state.{key}" for key in TRIP_STATE_KEYS
                    if key not in exclude
                    and self.trip_state[key] != other.trip_state[key]]
        return changed


class _LongStateBase(unittest.IsolatedAsyncioTestCase):
    """Shared invariants for the Batch H long-transcript scenarios."""

    loop = None  # set in setUpClass by subclasses

    @classmethod
    def tearDownClass(cls):
        """Restore ambient budget constants after the long-session fixture."""

        import app.services.agent.model.budget as budget_module

        importlib.reload(budget_module)
        super().tearDownClass()

    def setUp(self):
        clear_caches()

    def _new_session(self, mode: str):
        session_id = f"sess-h-{mode}-{secrets.token_hex(4)}"
        _sid, session = new_session()
        return session_id, session

    async def _run(self, *, session, session_id, mode, message, rounds, turn_id,
                   prepare_leg=None, prepare_legs=None, fixed_candidate_id=None,
                   mocks=None, poi=False, poi_result_override=None):
        before = SessionProjection.capture(session)
        trace = self.loop.TurnTrace()
        mocks = {} if mocks is None else dict(mocks)
        adapted_rounds, evidence_id = _model_led_rounds(rounds, turn_id=turn_id)
        kwargs = dict(session=session, session_id=session_id, message=message,
                      rounds=adapted_rounds, mode=mode, trace=trace, mocks=mocks,
                      turn_id=turn_id, prepare_leg=prepare_leg,
                      prepare_legs=prepare_legs,
                      fixed_candidate_id=fixed_candidate_id)
        evidence_patch = (
            patch.object(transit_evidence, "new_evidence_set_id", return_value=evidence_id)
            if evidence_id
            else nullcontext()
        )
        has_arrival_check = any(
            str(call.get("name") or "") == "check_transit"
            and str((call.get("input") or {}).get("operation") or "") == "arrivals"
            for scripted in adapted_rounds
            for call in scripted.get("tool_use") or []
        )
        registry_patch = nullcontext()
        if has_arrival_check:
            check_spec = self.loop.TOOL_REGISTRY["check_transit"]
            original_check = check_spec.executor

            async def _check_with_empty_arrivals(tool_input, ctx):
                async def _unavailable_arrivals(_input, _ctx):
                    return ToolResult(
                        ok=True,
                        data={
                            "source_status": "provider_unavailable",
                            "arrivals": [],
                        },
                        summary="No verified arrival predictions are available.",
                    )

                with patch.object(
                    lookup_arrivals,
                    "execute",
                    new=_unavailable_arrivals,
                ):
                    return await original_check(tool_input, ctx)

            registry = dict(self.loop.TOOL_REGISTRY)
            registry["check_transit"] = dataclasses.replace(
                check_spec,
                executor=_check_with_empty_arrivals,
            )
            registry_patch = patch.object(self.loop, "TOOL_REGISTRY", registry)
        with registry_patch:
            with (
                evidence_patch,
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
                if poi:
                    provider_result = poi_result_override or poi_result()
                    with patch.object(discover_places.search_local_places, "execute",
                                      new=AsyncMock(return_value=provider_result)):
                        events, trace = await run_turn(self.loop, **kwargs)
                else:
                    events, trace = await run_turn(self.loop, **kwargs)
        raw_calls = list(trace.tool_calls)
        if raw_calls and raw_calls[0][0] == "declare_goals":
            trace.model_led_tool_calls = raw_calls
            trace.tool_calls = [call for call in raw_calls if call[0] != "declare_goals"]
        return events, trace, mocks, before, SessionProjection.capture(session)

    def _assert_profile_from_calls(self, scenario_id: str, expected: set) -> None:
        calls = self.loop.client.messages.calls
        if not calls:
            self.fail(f"{scenario_id} expected a model request for the profile")
        offered = {schema["name"] for schema in calls[0]["tools"]}
        expected = _INITIAL_MODEL_TOOL_PROFILE if set(expected) >= {
            "declare_goals", "discover_places", "check_transit",
            "prepare_route_options", "present_places", "present_transit",
            "present_route", "complete_turn",
        } else set(expected)
        self.assertEqual(offered, expected,
                         f"{scenario_id} offered {sorted(offered)}")

    def _assert_forbidden_absent(self, scenario_id: str, names: list) -> None:
        for name in FORBIDDEN_TOOLS:
            self.assertNotIn(name, names, f"{scenario_id} ran forbidden {name}")

    def _assert_leak_free(self, scenario_id: str, text: str) -> None:
        lowered = str(text or "").casefold()
        for marker in LEAK_MARKERS:
            self.assertNotIn(marker, lowered,
                             f"{scenario_id} passenger text leaked {marker}")

    def _assert_no_candidate_sets(self, scenario_id: str, mocks: dict) -> None:
        self.assertEqual(mocks["stored_candidate_set_ids"], [],
                         f"{scenario_id} no candidate set may be stored")
    def _assert_unchanged(self, scenario_id: str, before, after) -> None:
        changed = before.diff(after)
        self.assertEqual(changed, [],
                         f"{scenario_id} non-mutating turn changed {changed}")

    def _assert_one_card(self, scenario_id: str, events: list) -> None:
        cards = route_cards(events)
        self.assertEqual(len(cards), 1, f"{scenario_id} exactly one card")
        self.assertEqual(cards[0].role, "recommended", scenario_id)
    def _assert_committed(self, *, scenario_id, session_id, state,
                          set_id, candidate_id) -> None:
        self.assertEqual(state["active_candidate_set_id"], set_id, scenario_id)
        self.assertEqual(state["selected_candidate_id"], candidate_id, scenario_id)
        record = candidate_store.load_candidate_set(set_id, session_id=session_id)
        self.assertIsNotNone(record, f"{scenario_id} committed set stored")
        self.assertTrue(record["presented"], f"{scenario_id} consumed once")
        self.assertEqual(record["selected_candidate_id"], candidate_id, scenario_id)
    def _assert_temp_cleared(self, scenario_id: str, state) -> None:
        self.assertEqual([state[k] for k in TEMPORARY_KEYS], [None] * 3,
                         f"{scenario_id} temporary scenario cleared")

    def _assert_policy(self, scenario_id: str, mode: str, trace, *, model_calls) -> None:
        expected_mode, expected_model = policy_model(self.loop, mode)
        if model_calls:
            self.assertEqual((trace.initial_mode, trace.final_mode),
                             (expected_mode, expected_mode), scenario_id)
        self.assertEqual([call["model"] for call in self.loop.client.messages.calls],
                         [expected_model] * model_calls,
                         f"{scenario_id} policy models")
    # ---- turn-family helpers (each asserts the full per-turn contract) ----

    async def _route_turn(self, *, scenario_id, session, session_id, mode, message,
                          turn_id, destination, candidate_id, provider_leg=None,
                          prepare_input=None, reset=False,
                          expected_excluded_route_ids=None, expected_state=None,
                          extra_round=None, prepare_legs=None,
                          expected_waypoints=None):
        prepare_input = {"destination": destination} if prepare_input is None else prepare_input
        rounds = ([extra_round] if extra_round is not None else []) + [
            _turn_round("prepare_route_options", f"tu-{turn_id}-p", prepare_input),
            _turn_round("present_route", f"tu-{turn_id}-v",
                        {"candidate_id": candidate_id}),
        ]
        events, trace, mocks, before, after = await self._run(
            session=session, session_id=session_id, mode=mode, message=message,
            rounds=rounds, turn_id=turn_id, prepare_leg=provider_leg,
            prepare_legs=prepare_legs, fixed_candidate_id=candidate_id, mocks={})
        names = [name for name, _input in trace.tool_calls]
        expected_names = (
            ["get_place_details", "prepare_route_options", "present_route"]
            if extra_round else ["prepare_route_options", "present_route"])
        self.assertEqual(names, expected_names, f"{scenario_id} canonical chain")
        self._assert_policy(scenario_id, mode, trace,
                            model_calls=3 if extra_round else 2)
        self._assert_forbidden_absent(scenario_id, names)
        self._assert_profile_from_calls(scenario_id, ROUTE_TOOL_PROFILE)
        self._assert_one_card(scenario_id, events)
        set_id = mocks["stored_candidate_set_ids"][-1]
        state = trip_state_module.get_trip_state(session)
        self._assert_committed(scenario_id=scenario_id, session_id=session_id,
                               state=state, set_id=set_id, candidate_id=candidate_id)
        card = route_cards(events)[0]
        self.assertEqual(after.active_card_id, card.card_id, scenario_id)
        self.assertEqual(
            after.route_card_ids,
            (before.route_card_ids + (card.card_id,))[-8:],
            f"{scenario_id} exactly one persisted card")
        if expected_excluded_route_ids is not None:
            self.assertEqual(trace.tool_calls[0][1].get("excluded_route_ids"),
                             expected_excluded_route_ids,
                             f"{scenario_id} exclusion reached prepare")
        if expected_state:
            for key, value in expected_state.items():
                self.assertEqual(state[key], value, f"{scenario_id} state[{key}]")
        if expected_waypoints is not None:
            self.assertEqual(state["waypoints"], expected_waypoints,
                             f"{scenario_id} waypoints")
        self._assert_leak_free(scenario_id, trace.final_text)
        return events, trace, mocks, before, after, set_id, card

    async def _no_tool_turn(self, *, scenario_id, session, session_id, mode,
                            message, turn_id, profile, text):
        events, trace, mocks, before, after = await self._run(
            session=session, session_id=session_id, mode=mode, message=message,
            rounds=[complete_turn_round(f"tu-{turn_id}-done", text)],
            turn_id=turn_id, mocks={})
        names = [name for name, _input in trace.tool_calls]
        self.assertEqual(names, ["complete_turn"], f"{scenario_id} terminal only")
        self._assert_policy(scenario_id, mode, trace, model_calls=1)
        self._assert_profile_from_calls(scenario_id, profile)
        self.assertEqual(route_cards(events), [], f"{scenario_id} no card")
        self._assert_unchanged(scenario_id, before, after)
        self._assert_leak_free(scenario_id, trace.final_text)
        return events, trace, before, after

    async def _status_turn(self, *, scenario_id, session, session_id, mode,
                           message, turn_id, route_ids=("Q",)):
        """Exercise a grounded service-status turn without mutating trip state."""
        events, trace, _mocks, before, after = await self._run(
            session=session,
            session_id=session_id,
            mode=mode,
            message=message,
            rounds=[
                _turn_round(
                    "check_transit",
                    f"tu-{turn_id}-status",
                    check_transit_input(
                        "service_status",
                        route_ids=list(route_ids),
                        direction="uptown" if route_ids else None,
                    ),
                ),
                complete_turn_round(
                    f"tu-{turn_id}-done",
                    "I checked the current Q service status.",
                ),
            ],
            turn_id=turn_id,
            mocks={},
        )
        names = [name for name, _input in trace.tool_calls]
        self.assertEqual(
            names,
            ["check_transit", "present_transit"],
            f"{scenario_id} grounded status only",
        )
        self._assert_policy(scenario_id, mode, trace, model_calls=2)
        self._assert_profile_from_calls(scenario_id, TRANSIT_TOOL_PROFILE)
        self.assertEqual(route_cards(events), [], f"{scenario_id} no card")
        self._assert_unchanged(scenario_id, before, after)
        self._assert_leak_free(scenario_id, trace.final_text)
        return events, trace, before, after

    async def _arrival_turn(self, *, scenario_id, session, session_id, mode,
                            message, turn_id, expected_route_id="Q"):
        from tests.conversation.conversation_matrix_harness import check_transit_input
        events, trace, mocks, before, after = await self._run(
            session=session, session_id=session_id, mode=mode, message=message,
            rounds=[
                _turn_round(
                    "check_transit",
                    f"tu-{turn_id}-arr",
                    check_transit_input(
                        "arrivals",
                        route_ids=[expected_route_id],
                        direction="uptown",
                    ),
                ),
                complete_turn_round(
                    f"tu-{turn_id}-done",
                    "Live arrivals are unavailable right now.",
                    outcome="unavailable",
                ),
            ],
            turn_id=turn_id, mocks={})
        names = [name for name, _input in trace.tool_calls]
        self.assertEqual(names, ["check_transit", "complete_turn"],
                         f"{scenario_id} arrival lookup only")
        self._assert_policy(scenario_id, mode, trace, model_calls=2)
        self._assert_profile_from_calls(scenario_id, ARRIVAL_TOOL_PROFILE)
        self.assertEqual(trace.tool_calls[0][1]["route_ids"], [expected_route_id])
        self.assertEqual(route_cards(events), [], f"{scenario_id} no card")
        self._assert_unchanged(scenario_id, before, after)
        text = "".join(e.text for e in events if e.type == "token")
        self.assertIn("unavailable", text.casefold(),
                      f"{scenario_id} truthful recovery, no fabricated arrival")
        self._assert_leak_free(scenario_id, text)
        return events, trace, before, after

    async def _simple_turn(self, *, scenario_id, session, session_id, mode, turn_id):
        events, trace, _m, before, after = await self._run(
            session=session, session_id=session_id, mode=mode, message="Thanks!",
            rounds=[complete_turn_round(f"tu-{turn_id}-done", "You're welcome.")],
            turn_id=turn_id, mocks={})
        names = [name for name, _input in trace.tool_calls]
        self.assertEqual(
            names,
            ["complete_turn"],
            f"{scenario_id} greeting uses only the terminal conversation tool",
        )
        self.assertEqual(
            trace.model_tool_use_count,
            2,
            f"{scenario_id} declares one goal and completes it",
        )
        self._assert_policy(scenario_id, mode, trace, model_calls=1)
        self.assertEqual(route_cards(events), [], f"{scenario_id} no card")
        self._assert_unchanged(scenario_id, before, after)
        text = "".join(e.text for e in events if e.type == "token")
        self.assertIn("welcome", text.casefold(), f"{scenario_id} greeting reply")
        return events

    async def _discovery_turn(self, *, scenario_id, session, session_id, mode,
                              message, turn_id, search_input,
                              poi_result_override=None):
        payload = dict(search_input)
        if "operation" not in payload:
            payload = discover_search_input(
                str(payload.get("query") or "pizza"),
                borough="Brooklyn" if "brooklyn" in str(payload.get("query") or "").casefold() else None,
            )
        set_token, fallback_place_tokens = discovery_id_tokens(session_id, turn_id)
        # Reuse canonical opaque ids when a later search returns the same
        # provider places.  A supplied fixture override represents a fresh
        # provider result and therefore receives fresh opaque ids.
        prior_set_id = trip_state_module.get_trip_state(session).get(
            "active_discovery_set_id"
        ) if poi_result_override is None else None
        prior_record = (
            discovery_store.load_discovery_set(
                prior_set_id, session_id=session_id
            )
            if prior_set_id
            else None
        )
        prior_places = (
            prior_record.get("places")
            if isinstance(prior_record, dict)
            else None
        )
        place_tokens = tuple(
            str(place.get("place_id") or "").strip()
            for place in (prior_places or [])
            if isinstance(place, dict) and str(place.get("place_id") or "").strip()
        ) or fallback_place_tokens
        place_ids = iter(fallback_place_tokens)
        rounds = [
            _turn_round("discover_places", f"tu-{turn_id}-d", payload),
            present_places_round(f"tu-{turn_id}-p", set_token, place_tokens),
        ]
        with (
            patch.object(discovery_store, "new_discovery_set_id", return_value=set_token),
            patch.object(discovery_store, "new_place_id", side_effect=lambda: next(place_ids)),
        ):
            events, trace, mocks, before, after = await self._run(
                session=session, session_id=session_id, mode=mode, message=message,
                rounds=rounds, turn_id=turn_id, mocks={}, poi=True,
                poi_result_override=poi_result_override)
        names = [name for name, _input in trace.tool_calls]
        self.assertEqual(
            names, ["discover_places", "present_places"], f"{scenario_id} search only"
        )
        self._assert_policy(scenario_id, mode, trace, model_calls=2)
        self._assert_forbidden_absent(scenario_id, names)
        self._assert_profile_from_calls(scenario_id, DISCOVERY_TOOL_PROFILE)
        self.assertEqual(route_cards(events), [], f"{scenario_id} no card")
        self._assert_no_candidate_sets(scenario_id, mocks)
        state = trip_state_module.get_trip_state(session)
        set_id = state["active_discovery_set_id"]
        self.assertTrue(bool(set_id) and set_id.startswith("ds_"),
                        f"{scenario_id} real discovery set bound")
        self.assertIsNone(state["selected_place_id"], f"{scenario_id} no selection")
        self.assertEqual(
            before.diff(
                after,
                exclude=("active_discovery_set_id", "selected_place_id"),
            ),
            [],
            f"{scenario_id} discovery only replaces discovery context",
        )
        self.assertIsNone(
            state["selected_place_id"],
            f"{scenario_id} new result set clears the prior selection",
        )
        self._assert_leak_free(scenario_id, trace.final_text)
        return events, trace, mocks, before, after, set_id

    async def _select_turn(self, *, scenario_id, session, session_id, mode,
                           message, turn_id, ordinal):
        state = trip_state_module.get_trip_state(session)
        set_id = state["active_discovery_set_id"]
        record = discovery_store.load_discovery_set(set_id, session_id=session_id)
        place = record["places"][int(ordinal) - 1]
        events, trace, mocks, before, after = await self._run(
            session=session, session_id=session_id, mode=mode, message=message,
            rounds=[
                _turn_round(
                    "present_places",
                    f"tu-{turn_id}-g",
                    {
                        "discovery_set_id": set_id,
                        "selections": [
                            {"place_id": place["place_id"], "reason": "preference_match"}
                        ],
                        "research_used": False,
                    },
                )
            ],
            turn_id=turn_id, mocks={})
        names = [name for name, _input in trace.tool_calls]
        self.assertEqual(names, ["present_places"],
                         f"{scenario_id} resolution only")
        self._assert_policy(scenario_id, mode, trace, model_calls=2)
        self._assert_profile_from_calls(scenario_id, SELECT_TOOL_PROFILE)
        self.assertEqual(route_cards(events), [], f"{scenario_id} no card")
        self._assert_no_candidate_sets(scenario_id, mocks)
        state = trip_state_module.get_trip_state(session)
        place_id = state["selected_place_id"]
        self.assertTrue(bool(place_id) and place_id.startswith("pl_"),
                        f"{scenario_id} real opaque place id bound")
        self.assertEqual(before.diff(after, exclude=("selected_place_id",)), [],
                         f"{scenario_id} selection never routes or mutates")
        return events, trace, before, after, place_id

    async def _route_selected_turn(self, *, scenario_id, session, session_id, mode,
                                   turn_id, place_id, provider_leg, candidate_id,
                                   expected_destination):
        prepare_input = {"destination": CONFLICTING_LABEL,
                         "destination_place_id": place_id}
        rounds = [
            _turn_round("prepare_route_options", f"tu-{turn_id}-p", prepare_input),
            _turn_round("present_route", f"tu-{turn_id}-v",
                        {"candidate_id": candidate_id}),
        ]
        events, trace, mocks, before, after = await self._run(
            session=session, session_id=session_id, mode=mode,
            message="Take me there.", rounds=rounds, turn_id=turn_id,
            prepare_leg=provider_leg, fixed_candidate_id=candidate_id, mocks={})
        names = [name for name, _input in trace.tool_calls]
        self.assertEqual(names, ["prepare_route_options", "present_route"],
                         f"{scenario_id} canonical chain, no re-search")
        self._assert_policy(scenario_id, mode, trace, model_calls=2)
        self._assert_profile_from_calls(scenario_id, ROUTE_TOOL_PROFILE)
        self.assertEqual(trace.tool_calls[0][1]["destination_place_id"], place_id,
                         f"{scenario_id} routes by the real opaque id")
        self._assert_one_card(scenario_id, events)
        set_id = mocks["stored_candidate_set_ids"][-1]
        state = trip_state_module.get_trip_state(session)
        self._assert_committed(scenario_id=scenario_id, session_id=session_id,
                               state=state, set_id=set_id, candidate_id=candidate_id)
        self.assertEqual(state["destination"], expected_destination, scenario_id)
        self.assertEqual(state["selected_place_id"], place_id, scenario_id)
        card = route_cards(events)[0]
        self.assertEqual(after.active_card_id, card.card_id, scenario_id)
        self.assertEqual(after.route_card_ids,
                         (before.route_card_ids + (card.card_id,))[-8:],
                         f"{scenario_id} one persisted card")
        self._assert_leak_free(scenario_id, trace.final_text)
        return events, trace, mocks, before, after, set_id, card

    async def _preview_turn(self, *, scenario_id, session, session_id, mode,
                            message, turn_id, destination, prepare_input,
                            provider_leg, candidate_id):
        prepare_input = dict(prepare_input)
        # Scenario selection is model-owned structured output now; the old
        # phrase classifier no longer injects this field at runtime.
        prepare_input["what_if"] = True
        rounds = [
            _turn_round("prepare_route_options", f"tu-{turn_id}-p", prepare_input),
            _turn_round("present_route", f"tu-{turn_id}-v",
                        {"candidate_id": candidate_id}),
        ]
        events, trace, mocks, before, after = await self._run(
            session=session, session_id=session_id, mode=mode, message=message,
            rounds=rounds, turn_id=turn_id, prepare_leg=provider_leg,
            fixed_candidate_id=candidate_id, mocks={})
        names = [name for name, _input in trace.tool_calls]
        self.assertEqual(names, ["prepare_route_options", "present_route"],
                         f"{scenario_id} preview tools")
        self._assert_policy(scenario_id, mode, trace, model_calls=2)
        self._assert_profile_from_calls(scenario_id, ROUTE_TOOL_PROFILE)
        self.assertEqual(trace.tool_calls[0][1].get("what_if"), True,
                         f"{scenario_id} server-forced what_if")
        self._assert_one_card(scenario_id, events)
        state = trip_state_module.get_trip_state(session)
        set_id = state["temporary_candidate_set_id"]
        self.assertTrue(bool(set_id), f"{scenario_id} temporary set bound")
        self.assertEqual(state["temporary_selected_candidate_id"], candidate_id,
                         scenario_id)
        self.assertEqual(state["temporary_base_candidate_set_id"],
                         before.trip_state["active_candidate_set_id"],
                         f"{scenario_id} temp base is the accepted set")
        self.assertEqual(before.diff(after, exclude=TEMPORARY_KEYS), [],
                         f"{scenario_id} preview never mutates the active trip")
        self.assertEqual(after.route_card_ids, before.route_card_ids,
                         f"{scenario_id} preview card not persisted")
        record = candidate_store.load_candidate_set(set_id, session_id=session_id)
        self.assertIsNotNone(record, f"{scenario_id} preview record stored")
        self.assertFalse(record["presented"], f"{scenario_id} unconsumed")
        self.assertIsNone(record["selected_candidate_id"], scenario_id)
        self._assert_leak_free(scenario_id, trace.final_text)
        return events, trace, before, after, set_id

    async def _accept_turn(self, *, scenario_id, session, session_id, mode,
                           turn_id, candidate_id):
        events, trace, _m, before, after = await self._run(
            session=session, session_id=session_id, mode=mode,
            message=ACCEPT_MESSAGE,
            rounds=[_turn_round("present_route", f"tu-{turn_id}-a",
                                {"candidate_id": candidate_id,
                                 "commit_scenario": True})],
            turn_id=turn_id, mocks={})
        names = [name for name, _input in trace.tool_calls]
        self.assertEqual(names, ["present_route"],
                         f"{scenario_id} accept never re-prepares")
        self._assert_policy(scenario_id, mode, trace, model_calls=2)
        self._assert_profile_from_calls(scenario_id, ACCEPT_TOOL_PROFILE)
        context = str(self.loop.client.messages.calls[0]["messages"][-1]["content"])
        self.assertIn(candidate_id, context,
                      f"{scenario_id} temp identity in context")
        self._assert_one_card(scenario_id, events)
        state = trip_state_module.get_trip_state(session)
        self._assert_temp_cleared(scenario_id, state)
        set_id = state["active_candidate_set_id"]
        self._assert_committed(scenario_id=scenario_id, session_id=session_id,
                               state=state, set_id=set_id, candidate_id=candidate_id)
        card = route_cards(events)[0]
        self.assertEqual(after.active_card_id, card.card_id, scenario_id)
        self.assertEqual(after.route_card_ids,
                         (before.route_card_ids + (card.card_id,))[-8:],
                         f"{scenario_id} committed branch adds one card")
        self._assert_leak_free(scenario_id, trace.final_text)
        return events, trace, before, after, set_id, card

    async def _reject_turn(self, *, scenario_id, session, session_id, mode, turn_id):
        events, trace, _m, before, after = await self._run(
            session=session, session_id=session_id, mode=mode,
            message=REJECT_MESSAGE,
            rounds=[
                _turn_round(
                    "complete_turn",
                    f"tu-{turn_id}-done",
                    {
                        "goal_keys": ["route"],
                        "outcome": "cancelled",
                        "message": "OK, keeping the original trip.",
                    },
                )
            ],
            turn_id=turn_id, mocks={})
        self.assertEqual(
            [name for name, _input in trace.tool_calls],
            ["complete_turn"],
            f"{scenario_id} terminal only",
        )
        self._assert_policy(scenario_id, mode, trace, model_calls=1)
        self._assert_profile_from_calls(scenario_id, REJECT_TOOL_PROFILE)
        self.assertEqual(route_cards(events), [], f"{scenario_id} no card")
        state = trip_state_module.get_trip_state(session)
        self._assert_temp_cleared(scenario_id, state)
        self.assertEqual(before.diff(after, exclude=TEMPORARY_KEYS), [],
                         f"{scenario_id} reject preserves the accepted trip")
        self._assert_leak_free(scenario_id, trace.final_text)
        return events, trace, before, after

    async def _stale_probe(self, *, scenario_id, session, session_id, mode,
                           turn_id, stale_candidate_id, expected_active_set_id):
        events, trace, _m, before, after = await self._run(
            session=session, session_id=session_id, mode=mode,
            message=STALE_PROBE_MESSAGE,
            rounds=[_turn_round("present_route", f"tu-{turn_id}-s",
                                {"candidate_id": stale_candidate_id}),
                    complete_turn_round(
                        f"tu-{turn_id}-done",
                        "I will keep the current route.",
                    )],
            turn_id=turn_id, mocks={})
        attempts = [
            attempt
            for attempt in trace.capability_attempts
            if attempt["capability"] == "present_route"
        ]
        self.assertTrue(attempts and attempts[0]["ok"] is False,
                        f"{scenario_id} superseded candidate must fail "
                        f"(ok={attempts[0]['ok'] if attempts else None}); "
                        "no resurrection")
        self.assertEqual(route_cards(events), [], f"{scenario_id} no card emitted")
        state = trip_state_module.get_trip_state(session)
        self.assertEqual(state["active_candidate_set_id"], expected_active_set_id,
                         f"{scenario_id} probe must not promote")
        return events, trace, before, after


__all__ = (
    "SessionProjection",
    "TEMPORARY_KEYS",
    "TRIP_STATE_KEYS",
    "_LongStateBase",
    "load_h_agent_loop",
    "stored_place",
)
