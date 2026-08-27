"""Batch C: deterministic discovery -> route follow-up scenarios (Auto+Quick).

Drives the *real* agent loop (``app.services.agent.loop.run_agent_turn``) with
production intent/tool filtering, the real registered executors for
``search_local_places`` / ``get_place_details`` / ``prepare_route_options`` /
``present_route``, and the real discovery/candidate/trip stores. Only the
narrow provider/data seams of ``tests.conversation.conversation_matrix_harness`` and the
discovery provider seam are scripted; Anthropic inference is deterministic
mock text (no model/provider/web/DB/network calls).

Scenario families:

- C-DISC-01 / C-DISC-02 -- discovery first turn (Auto + Quick): the real
  ``search_local_places`` executor stores a real server-owned discovery set
  with opaque place ids and emits no route planning, candidate set, or route
  card (required negative/control assertions shared by both modes).
- C-DISC-03 / C-DISC-04 -- the rider follow-up "Take me to the second one."
  through the real ``get_place_details`` (ordinal=2 against the real stored
  set) -> real ``prepare_route_options`` (destination_place_id read from the
  real store, conflicting free-text label ignored) -> real ``present_route``
  -> one canonical route card and committed backend state.

Where current production cannot satisfy a required invariant (the follow-up
loses the active discovery set before ``get_place_details`` can resolve
ordinal 2), the failing assertions carry the actual tool/state/context
evidence per the batch stop conditions -- production is not modified here.
"""

from __future__ import annotations

from tests.conversation import conversation_discovery_support as _discovery_support
from tests.conversation.conversation_discovery_support import _DiscoveryRouteBase
from tests.conversation.conversation_matrix_harness import load_agent_loop


INITIAL_TOOL_PROFILE = frozenset(
    {
        "declare_goals",
        "discover_places",
        "check_transit",
        "prepare_route_options",
        "complete_turn",
    }
)


def _goal_for_call(name: str, tool_input: dict) -> tuple[str, str] | None:
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
        if isinstance(call, dict)
    ]
    goals: list[dict] = []
    seen: set[str] = set()
    for call in calls:
        goal = _goal_for_call(str(call.get("name") or ""), call.get("input") or {})
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
        transformed: list[dict] = []
        for call in tool_uses:
            name = str(call.get("name") or "")
            tool_input = dict(call.get("input") or {})
            goal = _goal_for_call(name, tool_input)
            if goal is None:
                transformed.append(dict(call))
                continue
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


_model_led_run_turn.original = _discovery_support.run_turn
_model_led_run_turn._model_led_adapter = True
if not getattr(_discovery_support.run_turn, "_model_led_adapter", False):
    _discovery_support.run_turn = _model_led_run_turn

_ORIGINAL_DISCOVERY_PROFILES = {
    _name: getattr(_discovery_support, _name)
    for _name in dir(_discovery_support)
    if _name.endswith("_PROFILE")
    and isinstance(getattr(_discovery_support, _name), set)
}


def _activate_model_led_contract(loop) -> None:
    for _name in _ORIGINAL_DISCOVERY_PROFILES:
        setattr(_discovery_support, _name, set(INITIAL_TOOL_PROFILE))
    loop._model_led_contract_test = True


def _deactivate_model_led_contract(loop) -> None:
    loop._model_led_contract_test = False
    for _name, _value in _ORIGINAL_DISCOVERY_PROFILES.items():
        setattr(_discovery_support, _name, _value)


class DiscoveryFirstTurnTests(_DiscoveryRouteBase):
    """C-DISC-01 / C-DISC-02: discovery first turn (Auto + Quick)."""

    @classmethod
    def setUpClass(cls):
        cls.loop = load_agent_loop()
        _activate_model_led_contract(cls.loop)

    @classmethod
    def tearDownClass(cls):
        _deactivate_model_led_contract(cls.loop)

    async def _discovery(self, mode: str, scenario_id: str):
        session_id, session = self._new_session(mode)
        await self._discovery_turn(
            mode=mode,
            scenario_id=scenario_id,
            session=session,
            session_id=session_id,
        )

    async def test_c_disc_01_discovery_first_turn_auto(self):
        await self._discovery("auto", "C-DISC-01")


class DiscoveryRouteFollowupTests(_DiscoveryRouteBase):
    """C-DISC-03 / C-DISC-04: 'Take me to the second one.' full chain."""

    @classmethod
    def setUpClass(cls):
        cls.loop = load_agent_loop()
        _activate_model_led_contract(cls.loop)

    @classmethod
    def tearDownClass(cls):
        _deactivate_model_led_contract(cls.loop)

    async def _followup(self, mode: str, scenario_id: str):
        session_id, session = self._new_session(mode)
        session, session_id, set_id, record = await self._discovery_turn(
            mode=mode,
            scenario_id=f"{scenario_id}-t1",
            session=session,
            session_id=session_id,
        )
        events, trace, mocks, place2 = await self._followup_turn(
            mode=mode,
            scenario_id=scenario_id,
            session=session,
            session_id=session_id,
            set_id=set_id,
            record=record,
        )
        self._assert_followup_attempted(
            scenario_id=scenario_id,
            events=events,
            trace=trace,
            mocks=mocks,
            session=session,
            session_id=session_id,
            mode=mode,
            place2=place2,
        )
        self._assert_followup_chain(
            scenario_id=scenario_id,
            events=events,
            trace=trace,
            mocks=mocks,
            session=session,
            session_id=session_id,
            mode=mode,
            set_id=set_id,
            place2=place2,
        )

    async def test_c_disc_03_followup_second_one_auto(self):
        await self._followup("auto", "C-DISC-03")
