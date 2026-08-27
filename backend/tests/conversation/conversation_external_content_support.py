"""Batch G support: external-content injection / adversarial-turn invariants.

Non-test module (no ``Test*``/``test_*`` names at module level): pytest never
collects it. Drives the REAL agent loop (``loop.run_agent_turn``) with the
production intent/tool policy, the real registered ``TOOL_REGISTRY``
executors, and the real discovery/candidate/trip/session stores and SSE
event path. Anthropic inference is scripted deterministic text; the only
patched production points are the genuine external/provider/data seams of
``tests.conversation.conversation_matrix_harness`` plus the structured POI seam and the
bounded NYC geocoder seam (``app.services.geography``).

Pass-path rule: every test asserts the OFFERED tool profile before crediting
any scripted tool outcome, and scripted rounds only invoke offered tools
(the registered-but-unoffered execution boundary is owned by Batch F1).
Injected external content is modeled at its true boundary: the native
``web_search_20250305`` server tool is absent from the initial five-tool
surface, then offered only after successful structured place search while the
turn is not route-critical. It executes provider-side, so a scripted model
round IS the deterministic post-injection response.
"""

from __future__ import annotations

import json
import secrets
import unittest
from unittest.mock import AsyncMock, patch

from app.services.agent import candidate_store, discovery_store
from app.services.agent import tools as agent_tools
from app.services.agent import trip_state as trip_state_module
from app.services.agent.tools.places import search_local_places

from tests.conversation.conversation_discovery_fixtures import poi_result
from tests.conversation.conversation_external_content_fixtures import (
    FETCH_SURFACE_NAMES,
    FORBIDDEN_EXECUTION,
    G04_FIXED_CANDIDATE_ID,
    G04_MESSAGES,
    G04_VALID_WITH_SUFFIX,
    PASSENGER_MARKERS,
    REFUSAL_TEXT,
    REQUEST_OPAQUE_MARKERS,
    ROUTE_PROFILE,
    SECRET_MARKERS,
    STATE_MARKERS,
    transit_question_profile_for,
    work_leg,
)
from tests.conversation.conversation_matrix_harness import (
    _turn_round,
    clear_caches,
    discovery_id_tokens,
    new_session,
    policy_model,
    route_cards,
    run_turn,
    seed_accepted_active_trip,
)


def _tool_use(tool_id: str, name: str, tool_input: dict) -> dict:
    return {"id": tool_id, "name": name, "input": tool_input}


def _model_round(*tool_uses: dict) -> dict:
    """One scripted model response in the implemented tool protocol."""

    return {"tool_use": list(tool_uses), "stop_reason": "tool_use"}


def declaration_round(
    goals: list[dict],
    *capability_calls: tuple[str, str, dict],
    tool_id: str = "tu-goals",
) -> dict:
    """Declare outcome goals before any first-round capability calls."""

    calls = [_tool_use(tool_id, "declare_goals", {"goals": goals})]
    for call_id, name, tool_input in capability_calls:
        payload = dict(tool_input)
        if name == "prepare_route_options":
            has_explicit_destination = bool(
                payload.get("destination") or payload.get("destination_place_id")
            )
            payload.setdefault(
                "destination_source",
                "current_turn" if has_explicit_destination else "accepted_trip",
            )
        elif name == "present_route":
            payload.setdefault(
                "lead_in",
                "The route options were close, so I chose this one for your trip.",
            )
            payload.setdefault("follow_up", "")
            payload.setdefault("reason_code", "meets_hard_constraints")
        calls.append(_tool_use(call_id, name, payload))
    return _model_round(*calls)


def goal(goal_key: str, kind: str, depends_on: tuple[str, ...] = ()) -> dict:
    return {
        "goal_key": goal_key,
        "kind": kind,
        "depends_on": list(depends_on),
    }


def complete_goal_round(
    goal_keys: list[str],
    message: str,
    *,
    outcome: str = "unavailable",
    tool_id: str = "tu-complete",
) -> dict:
    """Recover an attempted unavailable goal through the terminal tool."""

    return _turn_round(
        "complete_turn",
        tool_id,
        {"goal_keys": goal_keys, "outcome": outcome, "message": message},
    )


def response_refusal_round(
    message: str,
    *,
    tool_id: str = "tu-refuse",
) -> dict:
    return declaration_round(
        [goal("response", "general_response")],
        (tool_id, "complete_turn", {
            "goal_keys": ["response"],
            "outcome": "refusal",
            "message": message,
        }),
    )


def present_places_goal_round(
    tool_id: str,
    set_id: str,
    place_ids: tuple[str, ...] | list[str],
    *,
    goal_key: str,
    research_used: bool = False,
) -> dict:
    selections = [
        {
            "place_id": place_id,
            "reason": "top_pick" if index == 0 else "preference_match",
        }
        for index, place_id in enumerate(place_ids)
    ]
    return _turn_round(
        "present_places",
        tool_id,
        {
            "goal_key": goal_key,
            "discovery_set_id": set_id,
            "selections": selections,
            "research_used": research_used,
        },
    )


class _ExternalContentBase(unittest.IsolatedAsyncioTestCase):
    """Shared Batch G invariants and turn runners (Auto and Quick)."""

    loop = None  # set in setUpClass by subclasses

    def setUp(self):
        clear_caches()

    # ------------------------------------------------------------------
    # Sessions and seeds
    # ------------------------------------------------------------------

    def _new_session(self) -> tuple[str, dict]:
        session_id = f"sess-g-{self.mode}-{secrets.token_hex(4)}"
        _sid, session = new_session()
        return session_id, session

    def _seed_accepted_trip(self, session, session_id):
        return seed_accepted_active_trip(session, session_id)

    # ------------------------------------------------------------------
    # Turn runners (real loop; only genuine seams patched)
    # ------------------------------------------------------------------

    async def _run_turn(
        self,
        *,
        session: dict,
        session_id: str,
        message: str,
        rounds: list,
        scenario_id: str,
        prepare_leg=None,
        fixed_candidate_id=None,
        turn_id: str = "t1",
    ) -> tuple[list, object, dict]:
        trace = self.loop.TurnTrace()
        mocks: dict = {}
        events, trace = await run_turn(
            self.loop,
            session=session,
            session_id=session_id,
            message=message,
            rounds=rounds,
            mode=self.mode,
            prepare_leg=prepare_leg,
            fixed_candidate_id=fixed_candidate_id,
            trace=trace,
            mocks=mocks,
            turn_id=turn_id,
        )
        assert scenario_id
        return events, trace, mocks

    async def _run_discovery_turn(
        self,
        *,
        session: dict,
        session_id: str,
        message: str,
        rounds: list,
        scenario_id: str,
        prepare_leg=None,
        fixed_candidate_id=None,
    ) -> tuple[list, object, dict]:
        set_token, place_tokens = discovery_id_tokens(session_id, "t1")
        place_ids = iter(place_tokens)
        with (
            patch.object(
                search_local_places,
                "execute",
                new=AsyncMock(return_value=poi_result()),
            ),
            patch.object(
                discovery_store,
                "new_discovery_set_id",
                return_value=set_token,
            ),
            patch.object(
                discovery_store,
                "new_place_id",
                side_effect=lambda: next(place_ids),
            ),
        ):
            return await self._run_turn(
                session=session,
                session_id=session_id,
                message=message,
                rounds=rounds,
                scenario_id=scenario_id,
                prepare_leg=prepare_leg,
                fixed_candidate_id=fixed_candidate_id,
            )

    # ------------------------------------------------------------------
    # Model-request and leak surfaces
    # ------------------------------------------------------------------

    def _server_authored_request_blob(self) -> str:
        """System blocks + tool schemas + per-turn <context> + tool results.

        Rider messages and assistant content are excluded on purpose: the
        rider's own words are not a server leak, and the scripted model's own
        prose/tool_use is the model's action echoed back to itself. The
        server-authored surface is the prompt, the offered schemas, the
        generated <context> block, and the tool-result content.
        """

        parts: list[str] = []
        for call in self.loop.client.messages.calls:
            parts.append(json.dumps(call.get("system") or [], default=str))
            parts.append(json.dumps(call.get("tools") or [], default=str))
            for entry in call.get("messages") or []:
                if entry.get("role") != "user":
                    continue
                content = entry.get("content")
                if isinstance(content, list):
                    parts.extend(
                        str(block.get("content") or "")
                        for block in content
                        if isinstance(block, dict)
                    )
                else:
                    text = str(content or "")
                    if "<context>" in text:
                        start = text.index("<context>")
                        parts.append(text[start:])
        return "\n".join(parts)

    def _system_blob(self) -> str:
        return json.dumps(self.loop.client.messages.calls[0].get("system") or [], default=str)

    def _passenger_blob(self, session: dict, events: list, trace) -> str:
        _ = events
        parts = [trace.final_text or ""]
        parts.extend(
            str(entry.get("text") or "")
            for entry in session.get("history") or []
            if entry.get("role") in {"assistant", "tool"}
        )
        return "\n".join(parts)

    def _events_blob(self, events: list) -> str:
        parts = []
        for event in events:
            data = event.to_data() if hasattr(event, "to_data") else str(event)
            parts.append(json.dumps(data, default=str))
        return "\n".join(parts)

    def _trip_state_blob(self, session: dict) -> str:
        return json.dumps(
            trip_state_module.get_trip_state(session),
            default=str,
            sort_keys=True,
        )

    def _assert_absent(self, scenario_id: str, label: str, blob: str, markers) -> None:
        lowered = blob.casefold()
        for marker in markers:
            assert str(marker).casefold() not in lowered, f"{scenario_id}: {label} leaked {marker!r}"

    def _assert_no_secret_leak(
        self,
        scenario_id: str,
        *,
        session: dict,
        events: list,
        trace,
        request_blob: str | None = None,
        state_blob: str | None = None,
        opaque_ids_expected_in_request: bool = False,
    ) -> None:
        """Sentinel absence across passenger, request, and trip state."""

        self._assert_absent(
            scenario_id,
            "passenger text/history",
            self._passenger_blob(session, events, trace),
            SECRET_MARKERS + PASSENGER_MARKERS,
        )
        # SSE events may legitimately carry sanctioned opaque ids (route_card
        # card_id, itinerary ids); injected secrets must never appear there.
        self._assert_absent(
            scenario_id,
            "SSE events",
            self._events_blob(events),
            SECRET_MARKERS,
        )
        if request_blob is not None:
            request_markers = SECRET_MARKERS + (
                REQUEST_OPAQUE_MARKERS
                if not opaque_ids_expected_in_request
                else ("chij",)
            )
            self._assert_absent(
                scenario_id,
                "model request",
                request_blob,
                request_markers,
            )
        if state_blob is not None:
            self._assert_absent(scenario_id, "trip_state", state_blob, STATE_MARKERS)

    def _assert_offered_profile(self, scenario_id: str, expected: set) -> None:
        calls = self.loop.client.messages.calls
        assert calls, f"{scenario_id}: no model request was recorded"
        offered = {schema["name"] for schema in calls[0].get("tools") or []}
        assert offered == expected, f"{scenario_id}: offered profile"
        assert not any(schema.get("type") == "web_search_20250305" for schema in calls[0].get("tools") or []), f"{scenario_id}: native Web must not be offered initially"

    def _assert_web_offer(
        self, scenario_id: str, call_index: int, *, expected: bool
    ) -> None:
        calls = self.loop.client.messages.calls
        assert len(calls) > call_index, f"{scenario_id}: missing model request {call_index + 1}"
        actual = any(
            schema.get("type") == "web_search_20250305"
            for schema in calls[call_index].get("tools") or []
        )
        assert actual == expected, f"{scenario_id}: native Web offer at request {call_index + 1}"

    def _assert_policy(self, scenario_id: str, trace, expected_model_calls: int) -> None:
        expected_mode, expected_model = policy_model(self.loop, self.mode)
        assert (trace.initial_mode, trace.final_mode) == (expected_mode, expected_mode), f"{scenario_id}: policy mode"
        assert [call["model"] for call in self.loop.client.messages.calls] == [expected_model] * expected_model_calls, f"{scenario_id}: policy models"

    def _assert_terminal(self, scenario_id: str, events: list, stop_reason: str = "end_turn") -> None:
        assert events[0].type == "meta", f"{scenario_id}: meta first"
        assert events[-1].type == "done", f"{scenario_id}: done last"
        assert events[-1].stop_reason == stop_reason, f"{scenario_id}: stop reason"

    def _assert_state_preserved(self, scenario_id: str, session: dict, session_id: str, seed) -> None:
        """The accepted canonical selection stays one bound, unchanged unit."""

        state = trip_state_module.get_trip_state(session)
        assert state["active_candidate_set_id"] == seed.candidate_set_id, scenario_id
        assert state["selected_candidate_id"] == seed.candidate_id, scenario_id
        assert state["origin"] == seed.origin, scenario_id
        assert state["destination"] == seed.destination, scenario_id
        assert state["waypoints"] == [], scenario_id
        assert state["planning_mode"] == seed.planning_mode, scenario_id
        assert state["requested_departure"] == seed.requested_departure, scenario_id
        assert state["requested_arrival"] == seed.requested_arrival, scenario_id
        assert session["active_trip"]["card_id"] == seed.card_id, scenario_id
        assert [card["card_id"] for card in session["route_cards"]] == [seed.card_id], scenario_id
        record = candidate_store.load_candidate_set(
            seed.candidate_set_id,
            session_id=session_id,
        )
        assert record is not None, scenario_id
        assert record["presented"], scenario_id
        assert record["selected_candidate_id"] == seed.candidate_id, scenario_id

    def _assert_pass_tail(
        self,
        scenario_id: str,
        *,
        events: list,
        trace,
        mocks: dict,
        session: dict,
        session_id: str,
        seed,
        expected_model_calls: int,
        request_blob: str | None,
        opaque_ids_expected: bool,
    ) -> None:
        """Common clean-pass invariants for no-card / no-mutation scenarios."""

        assert route_cards(events) == [], f"{scenario_id}: no route card"
        assert mocks["stored_candidate_set_ids"] == [], f"{scenario_id}: no candidate set stored"
        self._assert_terminal(scenario_id, events)
        self._assert_policy(scenario_id, trace, expected_model_calls=expected_model_calls)
        self._assert_state_preserved(scenario_id, session, session_id, seed)
        self._assert_no_secret_leak(
            scenario_id,
            session=session,
            events=events,
            trace=trace,
            request_blob=request_blob,
            state_blob=self._trip_state_blob(session),
            opaque_ids_expected_in_request=opaque_ids_expected,
        )

    def _assert_no_fetch_surface(self, scenario_id: str) -> None:
        """The product intentionally exposes no arbitrary URL-fetch capability."""

        registry_names = set(agent_tools.TOOL_REGISTRY)
        tools_names = {schema["name"] for schema in agent_tools.TOOLS}
        assert not registry_names & FETCH_SURFACE_NAMES, f"{scenario_id}: registry exposed a fetch surface"
        assert not tools_names & FETCH_SURFACE_NAMES, f"{scenario_id}: TOOLS exposed a fetch surface"
        for mode in ("auto",):
                offered = {
                    schema["name"]
                    for schema in self.loop._tools_for_state(
                        self.loop.agent_policy.policy_for_mode(mode)
                    )
                }
                assert not offered & FETCH_SURFACE_NAMES, f"{scenario_id}: {mode} offered a fetch surface"
                for name in offered:
                    schema = next(
                        (
                            item
                            for item in agent_tools.TOOLS
                            if item.get("name") == name
                        ),
                        None,
                    )
                    if schema is None:
                        # Native web_search has no local input schema.
                        continue
                    props = (schema.get("input_schema") or {}).get("properties") or {}
                    assert "url" not in props, f"{scenario_id}: {name} accepts a url input"

    def _assert_injection_defense_in_prompt(self, scenario_id: str) -> None:
        """The actual system block sent to the model carries the defenses."""

        system_blob = self._system_blob()
        assert "UNTRUSTED CONTENT / INJECTION DEFENSE" in system_blob, f"{scenario_id}: system prompt injection defense"
        assert "canonical route identity" in system_blob, f"{scenario_id}: web results must not become route identity"

    # ------------------------------------------------------------------
    # G-04 adversarial-turn helpers (server state wins over model claims)
    # ------------------------------------------------------------------

    async def _g04_refusal(
        self, scenario_id: str, message: str, *, extra_absent: tuple = ()
    ) -> None:
        sid = f"{scenario_id}-{self.mode}"
        session_id, session = self._new_session()
        seed = self._seed_accepted_trip(session, session_id)
        events, trace, mocks = await self._run_turn(
            session=session, session_id=session_id, message=message,
            scenario_id=sid,
            rounds=[
                response_refusal_round(REFUSAL_TEXT),
            ],
        )
        self._assert_offered_profile(
            sid, transit_question_profile_for(message)
        )
        assert [name for name, _input in trace.tool_calls] == ["declare_goals", "complete_turn"], sid
        for marker in extra_absent:
            assert marker.casefold() not in trace.final_text.casefold(), sid
        self._assert_pass_tail(
            sid, events=events, trace=trace, mocks=mocks, session=session,
            session_id=session_id, seed=seed, expected_model_calls=1,
            request_blob=self._server_authored_request_blob(),
            opaque_ids_expected=False,
        )

    async def _g04_04(self) -> None:
        """'Make up train arrival times' yields a clarification, never facts."""
        sid = f"G-04-04-{self.mode}"
        session_id, session = self._new_session()
        seed = self._seed_accepted_trip(session, session_id)
        refusal = "I cannot invent or substitute live train arrival times."
        events, trace, mocks = await self._run_turn(
            session=session, session_id=session_id,
            message=G04_MESSAGES["make_up_arrival_times"],
            scenario_id=sid,
            rounds=[
                response_refusal_round(refusal, tool_id="tu-refuse-arrivals"),
            ],
        )
        assert len(self.loop.client.messages.calls) == 1, sid
        assert [name for name, _input in trace.tool_calls] == ["declare_goals", "complete_turn"], sid
        assert route_cards(events) == [], sid
        assert mocks["stored_candidate_set_ids"] == [], sid
        self._assert_terminal(sid, events)
        assert trace.final_text == refusal, sid
        self._assert_state_preserved(sid, session, session_id, seed)
        self._assert_no_secret_leak(
            sid, session=session, events=events, trace=trace,
            request_blob=None, state_blob=self._trip_state_blob(session),
        )

    async def _g04_07(self) -> None:
        """A huge injection-like suffix cannot bypass canonical prepare/present."""
        sid = f"G-04-07-{self.mode}"
        session_id, session = self._new_session()
        events, trace, mocks = await self._run_turn(
            session=session, session_id=session_id, message=G04_VALID_WITH_SUFFIX,
            scenario_id=sid,
            rounds=[
                declaration_round(
                    [goal("route", "route")],
                    (
                        "tu-prepare",
                        "prepare_route_options",
                        {"goal_key": "route", "destination": "Work"},
                    ),
                ),
                _turn_round(
                    "present_route",
                    "tu-present",
                    {
                        "goal_key": "route",
                        "candidate_id": G04_FIXED_CANDIDATE_ID,
                    },
                ),
            ],
            prepare_leg=work_leg(), fixed_candidate_id=G04_FIXED_CANDIDATE_ID,
        )
        self._assert_offered_profile(sid, ROUTE_PROFILE)
        assert not any(schema["name"] == "web_search" for schema in self.loop.client.messages.calls[0]["tools"]), sid
        names = [name for name, _input in trace.tool_calls]
        assert names == ["declare_goals", "prepare_route_options", "present_route"], sid
        assert trace.tool_calls[1][1]["destination"] == "Work", sid
        assert not set(names) & set(FORBIDDEN_EXECUTION), sid
        cards = route_cards(events)
        assert len(cards) == 1, sid
        assert cards[0].destination.get("label") == "Work", sid
        assert len(mocks["stored_candidate_set_ids"]) == 1, sid
        state = trip_state_module.get_trip_state(session)
        assert state["active_candidate_set_id"] == mocks["stored_candidate_set_ids"][0], sid
        assert state["selected_candidate_id"] == G04_FIXED_CANDIDATE_ID, sid
        assert state["destination"] == "Work", sid
        self._assert_terminal(sid, events)
        self._assert_policy(sid, trace, expected_model_calls=2)
        self._assert_no_secret_leak(
            sid, session=session, events=events, trace=trace,
            request_blob=None, state_blob=self._trip_state_blob(session),
        )


__all__ = ("_ExternalContentBase",)
