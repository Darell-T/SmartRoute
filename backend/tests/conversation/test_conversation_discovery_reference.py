"""Batch C audit: C-DISC-05 / C-DISC-06 -- separate discovery-selection state.

Drives the exact three-turn transcript required by the exhaustive matrix in
ONE session, for both Auto and Quick:

  turn 1: "Find me pizza places in Brooklyn."   (real discovery, reused)
  turn 2: "The second one."                     (selection state transition)
  turn 3: "Take me there."                      (canonical route preparation)

The real loop, production state-scoped tool surface, real registry/executors,
discovery/candidate/trip stores, ledger, and SSE event path run untouched;
only the established provider/data seams and deterministic Anthropic rounds
are scripted. Real ids are read from the real store between turns.

Each transition is asserted immediately after it runs: the discovery turn
asserts its invariants inside ``_discovery_turn``, then the selection turn
is run and its OFFERED tool profile and chain are asserted BEFORE turn 3
runs, so a scripted unoffered tool can never create a false pass and turn 3
never executes before turn 2 is proven. The route turn then runs and its
OFFERED profile and chain are asserted. Production is not modified here.
"""

from __future__ import annotations

from tests.conversation import conversation_discovery_reference_support as _reference_support
from tests.conversation import conversation_discovery_support as _discovery_support
from tests.conversation.conversation_discovery_reference_support import _DiscoveryReferenceBase
from tests.conversation.conversation_matrix_harness import load_agent_loop, policy_model


INITIAL_TOOL_PROFILE = frozenset(
    {
        "declare_goals",
        "discover_places",
        "check_transit",
        "prepare_route_options",
        "complete_turn",
    }
)


def _goal_for_call(
    name: str, tool_input: dict, *, selection_only: bool = False
) -> tuple[str, str] | None:
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
        return "response", "general_response"
    return None


def _declared_rounds(rounds: list[dict]) -> list[dict]:
    calls = [
        call
        for scripted in rounds
        for call in scripted.get("tool_use") or []
        if str(call.get("name") or "") != "declare_goals"
    ]
    selection_only = bool(calls) and any(
        str(call.get("name") or "") == "present_places" for call in calls
    ) and not any(
        str(call.get("name") or "") == "discover_places" for call in calls
    )
    goals: list[dict] = []
    seen: set[str] = set()
    for scripted in rounds:
        for call in scripted.get("tool_use") or []:
            goal = _goal_for_call(
                str(call.get("name") or ""),
                call.get("input") or {},
                selection_only=selection_only,
            )
            if goal is None:
                continue
            key, kind = goal
            if key not in seen:
                goals.append({"goal_key": key, "kind": kind, "depends_on": []})
                seen.add(key)
    if not goals:
        return rounds
    adapted: list[dict] = []
    declared = False
    for scripted in rounds:
        tool_uses = scripted.get("tool_use") or []
        if not tool_uses:
            adapted.append(scripted)
            continue
        # The initial request intentionally exposes only the five provider
        # capabilities. A presenter for an already-owned discovery set becomes
        # state-valid only after declaration, so model it as the next response
        # rather than crediting an unoffered tool in the declaration response.
        if not declared and all(
            str(call.get("name") or "") not in INITIAL_TOOL_PROFILE
            for call in tool_uses
        ):
            adapted.append(
                {
                    "tool_use": [
                        {
                            "id": "tu-goals",
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
            goal = _goal_for_call(
                name, tool_input, selection_only=selection_only
            )
            if goal is not None:
                key, _kind = goal
                if name == "complete_turn":
                    tool_input.pop("goal_key", None)
                    tool_input["goal_keys"] = [key]
                else:
                    tool_input["goal_key"] = key
            transformed.append({**call, "input": tool_input})
        if not declared:
            transformed.insert(
                0,
                {
                    "id": "tu-goals",
                    "name": "declare_goals",
                    "input": {"goals": goals},
                },
            )
            declared = True
        adapted.append({**scripted, "tool_use": transformed})
    return adapted


async def _model_led_run_turn(loop, *args, **kwargs):
    original = _model_led_run_turn.original
    if not getattr(loop, "_model_led_contract_test", False):
        return await original(loop, *args, **kwargs)
    kwargs["rounds"] = _declared_rounds(kwargs.get("rounds") or [])
    events, trace = await original(loop, *args, **kwargs)
    raw_calls = list(trace.tool_calls)
    if raw_calls:
        if raw_calls[0][0] != "declare_goals":
            raise AssertionError("model-led discovery turn must declare goals first")
        offered = frozenset(
            schema["name"] for schema in loop.client.messages.calls[0]["tools"]
        )
        if offered != INITIAL_TOOL_PROFILE:
            raise AssertionError(
                f"unexpected initial discovery tool profile: {sorted(offered)}"
            )
        trace.model_led_tool_calls = raw_calls
        trace.tool_calls = [call for call in raw_calls if call[0] != "declare_goals"]
    return events, trace


if not getattr(_discovery_support.run_turn, "_model_led_adapter", False):
    _model_led_run_turn.original = _discovery_support.run_turn
    _model_led_run_turn._model_led_adapter = True
    _discovery_support.run_turn = _model_led_run_turn
if not getattr(_reference_support.run_turn, "_model_led_adapter", False):
    _model_led_run_turn.original = _reference_support.run_turn
    _model_led_run_turn._model_led_adapter = True
    _reference_support.run_turn = _model_led_run_turn

_ORIGINAL_REFERENCE_PROFILES = {
    _module: {
        _name: getattr(_module, _name)
        for _name in dir(_module)
        if _name.endswith("_PROFILE")
        and isinstance(getattr(_module, _name), set)
    }
    for _module in (_discovery_support, _reference_support)
}


def _activate_model_led_contract(loop) -> None:
    for _module, _profiles in _ORIGINAL_REFERENCE_PROFILES.items():
        for _name in _profiles:
            setattr(_module, _name, set(INITIAL_TOOL_PROFILE))
    loop._model_led_contract_test = True


def _deactivate_model_led_contract(loop) -> None:
    loop._model_led_contract_test = False
    for _module, _profiles in _ORIGINAL_REFERENCE_PROFILES.items():
        for _name, _value in _profiles.items():
            setattr(_module, _name, _value)


class DiscoverySelectionRouteTranscriptTests(_DiscoveryReferenceBase):
    """C-DISC-05 / C-DISC-06: selection then navigation in one session."""

    @classmethod
    def setUpClass(cls):
        cls.loop = load_agent_loop()
        _activate_model_led_contract(cls.loop)

    @classmethod
    def tearDownClass(cls):
        _deactivate_model_led_contract(cls.loop)

    def _assert_reference_attempted(self, *, scenario_id: str, mode: str, ev):
        """Prove declaration first, then the state-valid active-set presenter."""

        self.assertEqual(ev.offered, INITIAL_TOOL_PROFILE, scenario_id)
        self.assertGreaterEqual(
            len(self.loop.client.messages.calls),
            2,
            f"{scenario_id} selection requires a post-declaration model round",
        )
        state_profile = {
            schema["name"] for schema in self.loop.client.messages.calls[1]["tools"]
        }
        self.assertEqual(
            state_profile,
            {"complete_turn", "discover_places", "present_places"},
            f"{scenario_id} state-valid discovery presenter profile",
        )
        names = [name for name, _input in ev.trace.tool_calls]
        self.assertEqual(names, ["present_places"], scenario_id)
        self.assertEqual(
            ev.trace.tool_calls[0][1]["discovery_set_id"],
            ev.state["active_discovery_set_id"],
            f"{scenario_id} presenter uses the owned discovery set",
        )
        self.assertEqual(
            ev.trace.tool_calls[0][1]["selections"][0]["place_id"],
            ev.place2["place_id"],
            f"{scenario_id} presenter uses the opaque ordinal-2 place id",
        )
        expected_mode, expected_model = policy_model(self.loop, mode)
        self.assertEqual(
            (ev.trace.initial_mode, ev.trace.final_mode),
            (expected_mode, expected_mode),
            f"{scenario_id} policy mode",
        )
        self.assertEqual(
            list(ev.models), [expected_model, expected_model],
            f"{scenario_id} policy models",
        )
        self.assertIn("B Pizza", ev.trace.final_text, scenario_id)

    async def _transcript(self, mode: str, scenario_id: str):
        session_id, session = self._new_session(mode)
        session, session_id, set_id, record = await self._discovery_turn(
            mode=mode,
            scenario_id=f"{scenario_id}-t1",
            session=session,
            session_id=session_id,
        )
        selection = await self._reference_turn(
            mode=mode,
            scenario_id=scenario_id,
            session=session,
            session_id=session_id,
            set_id=set_id,
            record=record,
        )
        # Prove the selection turn before turn 3 runs. The active discovery
        # presenter must be state-valid after declaration and bind the exact
        # opaque ordinal-2 place without another provider search.
        self._assert_reference_attempted(
            scenario_id=scenario_id, mode=mode, ev=selection
        )
        self._assert_reference_chain(
            scenario_id=scenario_id, set_id=set_id, ev=selection
        )
        route = await self._route_turn(
            mode=mode,
            session=session,
            session_id=session_id,
            place2=selection.place2,
        )
        self._assert_route_attempted(
            scenario_id=scenario_id, mode=mode, set_id=set_id, ev=route
        )
        self._assert_route_chain(scenario_id=scenario_id, set_id=set_id, ev=route)

    async def test_c_disc_05_selection_then_route_auto(self):
        await self._transcript("auto", "C-DISC-05")

    async def test_c_disc_06_selection_then_route_quick(self):
        await self._transcript("quick", "C-DISC-06")


__all__ = ()
