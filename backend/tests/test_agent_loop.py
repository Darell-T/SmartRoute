"""Layer-1 tests for the conversational agent's turn loop
(app/services/agent/loop.py) -- SSE ordering, streaming, round-cap/deadline
wrap-up, parallel tool execution, and budget gating.

Follows the sys.modules fake-anthropic pattern from test_ai_advisor_mock.py,
scripted via tests/_fake_anthropic.py's FakeStream. Per the lesson learned
migrating directions.py's tests: each TestCase class reloads the module(s)
it needs exactly once in setUpClass, never per-test, to avoid churning
importlib.reload/sys.modules patching enough to trip the zoneinfo bug seen
under heavy reload cycling in this sandbox.
"""

from __future__ import annotations

import asyncio
import importlib
import os
import secrets
import time
import types
import unittest
from contextlib import contextmanager
from typing import ClassVar
from unittest.mock import patch

from app.services import cache
from app.services.agent import events as agent_events
from app.services.agent import session as session_module
from app.services.agent import trip_state as trip_state_module
from app.services.agent.model import mock_turn
from app.services.agent.model import stream as model_stream
from app.services.agent.tools import ToolContext, ToolResult, ToolSpec, declare_goals
from app.services.agent.tools import complete_turn as complete_turn_tool

from tests._fake_anthropic import reload_agent_loop_module


def _load_agent_loop(env: dict | None = None):
    # See _fake_anthropic.reload_agent_loop_module's docstring for why this
    # is a manual sys.modules swap rather than patch.dict(sys.modules, ...).
    return reload_agent_loop_module(env=env)


@contextmanager
def _reloaded_budget(env: dict[str, str]):
    """Reload budget constants for one test class, then restore ambient state.

    ``importlib.reload`` mutates the existing module object, which is also the
    object imported by ``loop``. The environment patch is therefore not enough:
    after it exits, reload once more against the restored environment so later
    classes and runner selections see their original budget configuration.
    """
    budget_module = importlib.import_module("app.services.agent.model.budget")
    try:
        with patch.dict(os.environ, env, clear=False):
            yield importlib.reload(budget_module)
    finally:
        importlib.reload(budget_module)


async def _fake_ok_tool(tool_input, _ctx):
    return ToolResult(ok=True, data={"echo": tool_input}, summary="did the thing")


async def _fake_fail_tool(_tool_input, _ctx):
    return ToolResult(ok=False, error="boom")


async def _fake_slow_tool(_tool_input, _ctx):
    import asyncio

    await asyncio.sleep(10)
    return ToolResult(ok=True, data={})


async def _fake_prepare_route_options_tool(_tool_input, _ctx):
    return ToolResult(
        ok=True,
        data={
            "candidate_set_id": "cs_test_only",
            "route_status": "good",
            "presentation_allowed": True,
            "candidates": [
                {
                    "candidate_id": "cd_test_only",
                    "duration_minutes": 20,
                    "transfers": 0,
                    "transit_lines": ["Q"],
                }
            ],
        },
        summary="prepared one route option",
    )


async def _fake_present_route_tool(tool_input, ctx):
    lead_in = str(tool_input.get("lead_in") or "").strip()
    follow_up = str(tool_input.get("follow_up") or "").strip()
    event = agent_events.RouteCardEvent(
        card_id="rc_conversational",
        turn_id=ctx.turn_id,
        role="recommended",
        origin={"label": "A", "lat": 40.7, "lng": -73.9},
        destination={"label": "B", "lat": 40.8, "lng": -74.0},
        summary={"eta_minutes": 20, "transfers": 0, "lines": ["Q"], "reason": "fits"},
        route=[{"type": "SUBWAY", "route_id": "Q"}],
        alerts=[],
    )
    return ToolResult(
        ok=True,
        data={"passenger_explanation": lead_in},
        summary="presented one route",
        events=[
            *([agent_events.TokenEvent(text=f"{lead_in}\n\n")] if lead_in else []),
            event,
            *([agent_events.TokenEvent(text=f"\n\n{follow_up}")] if follow_up else []),
        ],
        session_route_cards=[
            {
                "card_id": "rc_conversational",
                "role": "recommended",
                "lines": ["Q"],
                "eta_minutes": 20,
            }
        ],
    )


async def _fake_ambiguous_route_tool(_tool_input, _ctx):
    return ToolResult(
        ok=True,
        data={
            "source_status": "stop_not_resolved",
            "ambiguity": [{"name": "34 St"}, {"name": "34 St-Hudson Yards"}],
        },
        summary="destination is ambiguous",
    )


async def _fake_arrivals_tool(tool_input, ctx):
    payload = {
        "route_id": tool_input["route_id"],
        "stop": {
            "id": "D28",
            "name": tool_input.get("stop_query") or "Newkirk Plaza",
            "latitude": 40.6351,
            "longitude": -73.9628,
        },
        "directions": [
            {
                "id": "downtown",
                "label": "Downtown / Brooklyn-bound",
                "arrivals": [
                    {
                        "expected_at": "2026-07-25T14:04:00Z",
                        "minutes": 4,
                        "realtime": True,
                    }
                ],
            }
        ],
        "updated_at": "2026-07-25T14:00:00Z",
        "source_status": "live",
    }
    return ToolResult(
        ok=True,
        data=payload,
        summary="live arrivals",
        events=[agent_events.ArrivalCardEvent.from_lookup(ctx.turn_id, payload)],
    )


async def _fake_transit_snapshot_tool(tool_input, _ctx):
    lines = [str(line).upper() for line in tool_input.get("lines") or []]
    return ToolResult(
        ok=True,
        data={
            "source": "mta_service_alerts",
            "freshness": "live",
            "status": "active_alerts",
            "affected_routes": lines,
            "alerts": [
                {
                    "header": "Southbound Q trains are delayed.",
                    "route_ids": ["Q"],
                }
            ],
        },
        summary="1 active service alert for Q",
    )


async def _fake_arrival_clarification_tool(tool_input, ctx):
    payload = {
        "route_id": tool_input["route_id"],
        "stop": {"id": "", "name": "Transit stop"},
        "directions": [],
        "updated_at": "2026-07-25T14:00:00Z",
        "source_status": "stop_not_resolved",
        "ambiguity": [
            {"stop_id": "A01", "stop_name": "34 St-Penn Station"},
            {"stop_id": "A32", "stop_name": "34 St-Hudson Yards"},
        ],
    }
    return ToolResult(
        ok=True,
        data=payload,
        summary="station clarification required",
        events=[agent_events.ArrivalCardEvent.from_lookup(ctx.turn_id, payload)],
    )


async def _fake_poi_tool(_tool_input, _ctx):
    return ToolResult(
        ok=True,
        data={
            "results": [
                {
                    "name": "Di Fara Pizza",
                    "address": "1424 Avenue J, Brooklyn, NY",
                    "lat": 40.625,
                    "lng": -73.961,
                }
            ]
        },
        summary="found one grounded place",
    )


async def _fake_search_local_places_tool(_tool_input, _ctx):
    return ToolResult(
        ok=True,
        data={
            "discovery_set_id": "ds_test_only",
            "places": [
                {
                    "place_id": "pl_di_fara",
                    "ordinal": 1,
                    "name": "Di Fara Pizza",
                    "neighborhood": "Brooklyn",
                    "category": "pizza",
                    "open_status": "open",
                    "baseline_score": 0.9,
                    "address": "1424 Av J",
                }
            ],
        },
        summary="found one grounded place",
    )


async def _fake_discover_places_tool(tool_input, ctx):
    evidence = getattr(ctx, "turn_evidence", None)
    if evidence is not None:
        evidence.note_discover_places(
            ok=True, discovery_set_id="ds_test_only", place_count=1
        )
    return await _fake_search_local_places_tool(tool_input, ctx)


async def _fake_check_transit_tool(tool_input, ctx):
    operation = str(tool_input.get("operation") or "")
    if operation == "arrivals":
        route_ids = tool_input.get("route_ids") or ["Q"]
        result = await _fake_arrivals_tool(
            {
                "route_id": route_ids[0],
                "stop_query": tool_input.get("stop_query"),
            },
            ctx,
        )
        data = {"operation": operation, "result": result.data}
        grounded = True
        events = result.events
        summary = result.summary
    elif operation == "service_status":
        result = await _fake_transit_snapshot_tool(
            {"lines": tool_input.get("route_ids") or []},
            ctx,
        )
        data = {"operation": operation, "result": result.data}
        grounded = result.ok
        events = result.events
        summary = result.summary
    else:
        data = {"operation": operation, "result": {"ok": True}}
        grounded = True
        events = []
        summary = "checked transit"
    evidence = getattr(ctx, "turn_evidence", None)
    if evidence is not None:
        evidence.note_check_transit(ok=grounded, operation=operation)
    return ToolResult(
        ok=True,
        data=data,
        summary=summary,
        events=events,
    )


async def _fake_complete_turn_tool(tool_input, ctx):
    message = str(tool_input.get("message") or "").strip()
    evidence = getattr(ctx, "turn_evidence", None)
    if getattr(evidence, "turn_contract", None) is not None:
        return await complete_turn_tool.execute(tool_input, ctx)
    if evidence is not None:
        evidence.mark_terminal("complete_turn")
    return ToolResult(
        ok=True,
        data={"outcome": tool_input.get("outcome"), "message": message},
        summary="Completed the turn",
        events=[agent_events.TokenEvent(text=message)],
        terminal=True,
        terminal_path="complete_turn",
    )


async def _fake_present_places_tool(tool_input, _ctx):
    return ToolResult(
        ok=True,
        data={"presented": tool_input.get("selections") or []},
        summary="Presented verified places",
        events=[agent_events.TokenEvent(text="Here are current verified matches.")],
    )


def _test_registry() -> dict[str, ToolSpec]:
    return {
        "ok_tool": ToolSpec(
            schema={"name": "ok_tool"},
            executor=_fake_ok_tool,
            label_fn=lambda _input: "Doing the thing…",
            timeout_s=5.0,
        ),
        "fail_tool": ToolSpec(
            schema={"name": "fail_tool"},
            executor=_fake_fail_tool,
            label_fn=lambda _input: "Doing the failing thing…",
            timeout_s=5.0,
        ),
        "slow_tool": ToolSpec(
            schema={"name": "slow_tool"},
            executor=_fake_slow_tool,
            label_fn=lambda _input: "Doing the slow thing…",
            timeout_s=0.05,
        ),
        "prepare_route_options": ToolSpec(
            schema={"name": "prepare_route_options"},
            executor=_fake_prepare_route_options_tool,
            label_fn=lambda _input: "Preparing routes…",
            timeout_s=5.0,
        ),
        "present_route": ToolSpec(
            schema={"name": "present_route"},
            executor=_fake_present_route_tool,
            label_fn=lambda _input: "Presenting the route…",
            timeout_s=5.0,
        ),
        "lookup_arrivals": ToolSpec(
            schema={"name": "lookup_arrivals"},
            executor=_fake_arrivals_tool,
            label_fn=lambda _input: f"Checking {_input.get('route_id')} arrivals",
            timeout_s=5.0,
        ),
        "transit_snapshot": ToolSpec(
            schema={"name": "transit_snapshot"},
            executor=_fake_transit_snapshot_tool,
            label_fn=lambda _input: "Checking current service conditions",
            timeout_s=5.0,
        ),
        "poi_search": ToolSpec(
            schema={"name": "poi_search"},
            executor=_fake_poi_tool,
            label_fn=lambda _input: "Finding places",
            timeout_s=5.0,
        ),
        "search_local_places": ToolSpec(
            schema={"name": "search_local_places"},
            executor=_fake_search_local_places_tool,
            label_fn=lambda _input: "Finding places",
            timeout_s=5.0,
        ),
        "get_place_details": ToolSpec(
            schema={"name": "get_place_details"},
            executor=_fake_search_local_places_tool,
            label_fn=lambda _input: "Checking place details",
            timeout_s=5.0,
        ),
        "discover_places": ToolSpec(
            schema={"name": "discover_places"},
            executor=_fake_discover_places_tool,
            label_fn=lambda _input: "Searching verified places…",
            timeout_s=5.0,
        ),
        "present_places": ToolSpec(
            schema={"name": "present_places"},
            executor=_fake_present_places_tool,
            label_fn=lambda _input: "Presenting verified places…",
            timeout_s=5.0,
        ),
        "check_transit": ToolSpec(
            schema={"name": "check_transit"},
            executor=_fake_check_transit_tool,
            label_fn=lambda _input: "Checking transit…",
            timeout_s=5.0,
        ),
        "complete_turn": ToolSpec(
            schema={"name": "complete_turn"},
            executor=_fake_complete_turn_tool,
            label_fn=lambda _input: "Finishing your answer…",
            timeout_s=5.0,
        ),
    }


def _model_led_registry() -> dict[str, ToolSpec]:
    """Add the public goal declaration to the mechanics test registry."""

    registry = _test_registry()
    registry["declare_goals"] = ToolSpec(
        schema=declare_goals.DECLARE_GOALS_SCHEMA,
        executor=declare_goals.execute,
        label_fn=lambda _input: "Thinking through your request…",
        timeout_s=5.0,
    )
    return registry


def _trace_tool_input(trace, name: str) -> dict:
    """Return the recorded input for one executed public capability."""

    return next(
        tool_input for tool_name, tool_input in trace.tool_calls if tool_name == name
    )


def _declared_route_round(
    name: str,
    tool_id: str,
    tool_input: dict | None = None,
    *,
    goal_key: str = "route",
) -> dict:
    """Script a public route capability after declaring its rider outcome."""

    payload = dict(tool_input or {})
    payload.setdefault("goal_key", goal_key)
    if name == "prepare_route_options":
        has_explicit_destination = bool(
            payload.get("destination") or payload.get("destination_place_id")
        )
        payload.setdefault(
            "destination_source",
            "current_turn" if has_explicit_destination else "accepted_trip",
        )
    return {
        "tool_use": [
            {
                "id": f"goals-{tool_id}",
                "name": "declare_goals",
                "input": {
                    "goals": [
                        {
                            "goal_key": goal_key,
                            "kind": "route",
                            "depends_on": [],
                        }
                    ]
                },
            },
            {"id": tool_id, "name": name, "input": payload},
        ],
        "stop_reason": "tool_use",
    }


_DEFAULT_ROUTE_EXPLANATION = (
    "This option keeps the trip simple while meeting what you asked for."
)


def _route_present_round(
    tool_id: str = "present-route",
    candidate_id: str = "cd_test_only",
    *,
    goal_key: str = "route",
) -> dict:
    return _tool_round(
        "present_route",
        tool_id,
        {
            "candidate_id": candidate_id,
            "goal_key": goal_key,
            "lead_in": _DEFAULT_ROUTE_EXPLANATION,
            "follow_up": "",
            "reason_code": "meets_hard_constraints",
        },
    )


def _offered_schemas_for_registry(registry: dict) -> list[dict]:
    """Explicit offered surface for one fake-registry mechanics run.

    Loop-mechanics tests inject fake ``ToolSpec`` executors through
    ``_AgentLoopHelpers._run(tool_registry=...)``. Offering the injected
    registry's own schemas on that run makes scripted fake tools genuinely
    offered instead of bypassing the per-turn allowlist boundary.
    """

    return [spec.schema for spec in registry.values()]


def _tool_round(name: str, tool_id: str, tool_input: dict | None = None) -> dict:
    payload = dict(tool_input or {})
    if name == "prepare_route_options":
        has_explicit_destination = bool(
            payload.get("destination") or payload.get("destination_place_id")
        )
        payload.setdefault(
            "destination_source",
            "current_turn" if has_explicit_destination else "accepted_trip",
        )
    elif name == "present_route":
        payload.setdefault("lead_in", "This route fits the trip you requested.")
        payload.setdefault("follow_up", "")
        payload.setdefault("reason_code", "meets_hard_constraints")
    elif name == "present_transit":
        payload.setdefault("lead_in", "")
        payload.setdefault("follow_up", "")
    return {
        "tool_use": [{"id": tool_id, "name": name, "input": payload}],
        "stop_reason": "tool_use",
    }


def _complete_round(
    message: str,
    *,
    tool_id: str = "tu_done",
    outcome: str = "answer",
) -> dict:
    return _tool_round(
        "complete_turn",
        tool_id,
        {"outcome": outcome, "message": message},
    )


def _declared_general_round(message: str, *, outcome: str = "answer") -> dict:
    return {
        "tool_use": [
            {
                "id": "tu_goals",
                "name": "declare_goals",
                "input": {
                    "goals": [
                        {
                            "goal_key": "response",
                            "kind": "general_response",
                            "depends_on": [],
                        }
                    ]
                },
            },
            {
                "id": "tu_done",
                "name": "complete_turn",
                "input": {
                    "goal_keys": ["response"],
                    "outcome": outcome,
                    "message": message,
                },
            },
        ],
        "stop_reason": "tool_use",
    }


def _transit_input(
    operation: str,
    *,
    route_ids: list[str] | None = None,
    **overrides,
) -> dict:
    payload = {
        "operation": operation,
        "route_ids": route_ids or [],
        "stop_query": None,
        "direction": None,
        "area": None,
        "station": None,
        "topic": None,
        "event_query": None,
        "venue": None,
        "at": None,
        "window_start": None,
        "window_end": None,
    }
    payload.update(overrides)
    return payload


class _AgentLoopHelpers:
    """Mixin: subclasses set `cls.loop` in setUpClass."""

    async def _run(
        self,
        rounds,
        *,
        message="Can you explain that?",
        session=None,
        session_id=None,
        origin=None,
        trace=None,
        tool_registry=None,
        selected_card_id=None,
        response_presentation="auto",
    ):
        self.loop.client.messages._rounds = list(rounds)
        self.loop.client.messages.calls = []
        if session is None:
            _discard_id, session = session_module.new_session()
        if session_id is None:
            # Unique per call so per-session rate limiting never leaks
            # between unrelated tests.
            session_id = secrets.token_hex(8)

        patcher = (
            patch.object(self.loop, "TOOL_REGISTRY", tool_registry)
            if tool_registry is not None
            else None
        )
        surface_patcher = (
            patch.object(
                self.loop,
                "_tools_for_state",
                lambda *_args, **kwargs: (
                    _offered_schemas_for_registry(tool_registry)
                    + (
                        [
                            self.loop._web_search_tool(
                                self.loop.agent_policy.policy_for_mode(
                                    response_presentation
                                )
                            )
                        ]
                        if kwargs.get("include_web")
                        else []
                    )
                ),
            )
            if tool_registry is not None
            else None
        )
        active_patchers = [
            active for active in (patcher, surface_patcher) if active is not None
        ]
        for active in active_patchers:
            active.start()
        try:
            events_out = [
                event
                async for event in self.loop.run_agent_turn(
                    session=session,
                    session_id=session_id,
                    turn_id="t1",
                    message=message,
                    now_et="2026-07-15T21:00:00-04:00",
                    gtfs=None,
                    origin=origin,
                    selected_card_id=selected_card_id,
                    response_presentation=response_presentation,
                    trace=trace,
                )
            ]
        finally:
            for active in active_patchers:
                active.stop()
        return events_out, session


class _BudgetConfiguredAgentLoopTests(
    _AgentLoopHelpers, unittest.IsolatedAsyncioTestCase
):
    BUDGET_ENV: ClassVar[dict[str, str]]

    @classmethod
    def setUpClass(cls):
        cls.loop = _load_agent_loop()
        cls._budget_scope = _reloaded_budget(cls.BUDGET_ENV)
        cls.budget = cls._budget_scope.__enter__()
        cls.addClassCleanup(cls._budget_scope.__exit__, None, None, None)


class LoopMechanicsTests(_AgentLoopHelpers, unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        cls.loop = _load_agent_loop()

    def setUp(self):
        cache._mem.clear()

    async def test_meta_first_and_done_last_on_a_clean_turn(self):
        events_out, _session = await self._run(
            [_complete_round("Hello rider")],
            tool_registry=_test_registry(),
        )
        assert events_out[0].type == "meta"
        assert events_out[-1].type == "done"
        assert events_out[-1].stop_reason == "end_turn"

    async def test_final_text_persisted_to_session_history(self):
        _events, session = await self._run(
            [_complete_round("OK, taking the Q")],
            tool_registry=_test_registry(),
        )
        assistant_turns = [h for h in session["history"] if h["role"] == "assistant"]
        assert assistant_turns[-1]["text"] == "OK, taking the Q"

    def test_internal_card_ids_and_markdown_do_not_reach_rider_prose(self):
        prose = self.loop._sanitize_rider_text(
            "**Recommended: Card rc_b87e6f1a — Q/D trains, 1 transfer, ~31 min**"
        )
        assert prose == "Recommended: Q/D trains, 1 transfer, about 31 min"

    def test_opaque_candidate_ids_do_not_reach_rider_prose(self):
        sanitized = self.loop._sanitize_rider_text(
            "Selected cd_test_only from cs_test_only."
        )
        assert "cd_test_only" not in sanitized
        assert "cs_test_only" not in sanitized

    async def test_done_last_even_after_upstream_model_error(self):
        events_out, _ = await self._run([{"raise": True}])
        assert events_out[0].type == "meta"
        assert events_out[-1].type == "done"
        assert events_out[-1].stop_reason == "error"
        errors = [e for e in events_out if e.type == "error"]
        assert len(errors) == 1
        assert errors[0].code == "upstream_error"

    async def test_bad_request_is_attempted_once_and_emits_typed_error(self):
        class BadRequestError(Exception):
            status_code = 400
            request_id = "req_bad_request"
            body: ClassVar[dict] = {
                "type": "error",
                "error": {
                    "type": "invalid_request_error",
                    "message": "temperature is not supported",
                },
            }

        events_out, _ = await self._run(
            [
                {"exception": BadRequestError()},
                {"text": ["must not run"], "stop_reason": "end_turn"},
            ]
        )
        assert len(self.loop.client.messages.calls) == 1
        errors = [event for event in events_out if event.type == "error"]
        assert errors[0].code == "invalid_request"
        assert not errors[0].retryable
        assert events_out[-1].stop_reason == "error"

    async def test_transient_server_error_retries_within_application_bound(self):
        class ServerError(Exception):
            status_code = 503
            request_id = "req_server_error"
            body: ClassVar[dict] = {
                "type": "error",
                "error": {
                    "type": "api_error",
                    "message": "service temporarily unavailable",
                },
            }

        events_out, _ = await self._run(
            [
                {"exception": ServerError()},
                _complete_round("Recovered"),
            ],
            tool_registry=_test_registry(),
        )
        assert len(self.loop.client.messages.calls) == 2
        assert not any(event.type == "error" for event in events_out)
        assert events_out[-1].stop_reason == "end_turn"

    async def test_system_block_carries_ephemeral_cache_control(self):
        await self._run([{"text": ["hi"], "stop_reason": "end_turn"}])
        kwargs = self.loop.client.messages.calls[0]
        assert kwargs["system"][-1]["cache_control"] == {"type": "ephemeral"}

    async def test_context_block_appended_to_latest_user_message(self):
        await self._run(
            [{"text": ["hi"], "stop_reason": "end_turn"}],
            origin={"lat": 40.7, "lng": -73.9},
        )
        kwargs = self.loop.client.messages.calls[0]
        last_user_content = kwargs["messages"][-1]["content"]
        assert "<context>" in last_user_content
        assert "rider_location: 40.7000,-73.9000" in last_user_content

    async def test_quick_presentation_uses_the_shared_pipeline_with_smaller_budgets(
        self,
    ):
        rounds = [
            _declared_route_round(
                "prepare_route_options", "tu_1", {"destination": "Costco"}
            ),
            _route_present_round("tu_2"),
        ]
        trace = self.loop.TurnTrace()

        await self._run(
            rounds,
            message="Take me to Costco",
            response_presentation="quick",
            tool_registry=_model_led_registry(),
            trace=trace,
        )

        first_call = self.loop.client.messages.calls[0]
        assert "response_presentation: quick" in first_call["messages"][-1]["content"]
        prepare_input = _trace_tool_input(trace, "prepare_route_options")
        assert prepare_input["destination"] == "Costco"
        assert prepare_input["max_candidates"] == 2
        assert not prepare_input["avoid_crowds"]
        assert not prepare_input["include_first_leg_arrivals"]
        assert trace.initial_mode == "quick"
        assert trace.final_mode == "quick"
        assert trace.model_call_count == 2
        assert len(self.loop.client.messages.calls) == 2
        assert trace.rider_message == "Take me to Costco"
        assert [item["selected_capabilities"] for item in trace.model_rounds] == [["declare_goals", "prepare_route_options"], ["present_route"]]
        assert [item["capability"] for item in trace.capability_attempts] == ["declare_goals", "prepare_route_options", "present_route"]

    async def test_auto_crowd_avoidance_enables_bounded_crowd_research(self):
        trace = self.loop.TurnTrace()
        rounds = [
            _declared_route_round(
                "prepare_route_options",
                "tu_1",
                {"destination": "Columbus Circle", "avoid_crowds": True},
            ),
            _route_present_round("tu_2"),
        ]

        await self._run(
            rounds,
            message=(
                "I want to head to Columbus Circle later and avoid crowds "
                "on both the street and subway"
            ),
            tool_registry=_model_led_registry(),
            trace=trace,
        )

        plan_input = _trace_tool_input(trace, "prepare_route_options")
        assert plan_input["avoid_crowds"]
        assert plan_input["crowd_search_mode"] == "auto"
        assert "include_incident_scan" not in plan_input

    async def test_quick_crowd_request_keeps_the_quick_model_for_planning(self):
        trace = self.loop.TurnTrace()
        rounds = [
            _declared_route_round(
                "prepare_route_options",
                "tu_1",
                {"destination": "Columbus Circle", "avoid_crowds": True},
            ),
            _route_present_round("tu_2"),
        ]

        await self._run(
            rounds,
            message="Plan a trip to Columbus Circle and avoid crowds",
            response_presentation="quick",
            tool_registry=_model_led_registry(),
            trace=trace,
        )

        assert trace.final_mode == "quick"
        assert _trace_tool_input(trace, "prepare_route_options")["crowd_search_mode"] == "auto"
        assert self.loop.client.messages.calls[0]["model"] == self.loop.agent_policy.policy_for_mode("quick").model
        assert len(self.loop.client.messages.calls) == 2

    async def test_active_route_exclusion_persists_across_followups(self):
        trace = self.loop.TurnTrace()
        rounds = [
            _tool_round(
                "prepare_route_options",
                "tu_1",
                {"destination": "Coney Island", "excluded_route_ids": ["Q"]},
            ),
            _tool_round("present_route", "tu_2", {"candidate_id": "cd_test_only"}),
        ]

        _events, session = await self._run(
            rounds,
            message="Avoid the Q",
            tool_registry=_test_registry(),
            trace=trace,
        )

        assert trace.tool_calls[0][1]["excluded_route_ids"] == ["Q"]
        assert session["slots"]["constraints"]["excluded_route_ids"] == ["Q"]

    async def test_intent_tool_profiles_stay_within_provider_schema_limit(self):
        def optional_parameter_count(schema):
            if not schema:
                return 0
            properties = schema.get("properties") or {}
            required = set(schema.get("required") or [])
            return (
                sum(name not in required for name in properties)
                + sum(
                    optional_parameter_count(value)
                    for value in properties.values()
                    if isinstance(value, dict)
                )
                + optional_parameter_count(schema.get("items") or {})
            )

        cases = (
            "Plan a trip to Coney Island with less walking",
            "route me there please",
            "When is the next Q train?",
            "Find a good pizza place",
            "lets get some L'Industrie now",
            "Are there events at Barclays Center tonight?",
            "How much is the subway fare?",
            (
                "How much is the fare, is the Q delayed, is the elevator "
                "accessible, what events are at Barclays, and what are the "
                "latest news reports?"
            ),
            "Hello",
        )
        expected_tools = set(self.loop.public_surface.INITIAL_TOOL_NAMES)
        for message in cases:
            with self.subTest(message=message):
                schemas = self.loop._tools_for_state()
                total = sum(
                    optional_parameter_count(schema.get("input_schema"))
                    for schema in schemas
                )
                # Anthropic rejects an over-complex custom-tool request before
                # its first token. The stable public surface remains below the
                # provider's compilation boundary for every intent family.
                assert total <= 24
                assert expected_tools == {schema["name"] for schema in schemas}

    async def test_conversational_route_prepares_compares_and_presents_once(self):
        trace = self.loop.TurnTrace()
        events_out, session = await self._run(
            [
                _declared_route_round(
                    "prepare_route_options",
                    "prepare-1",
                    {"destination": "Coney Island"},
                ),
                _route_present_round("present-1"),
            ],
            message="Plan a route to Coney Island",
            tool_registry=_model_led_registry(),
            trace=trace,
        )

        assert [name for name, _tool_input in trace.tool_calls] == ["declare_goals", "prepare_route_options", "present_route"]
        assert len(self.loop.client.messages.calls) == 2
        route_cards = [event for event in events_out if event.type == "route_card"]
        assert [event.role for event in route_cards] == ["recommended"]
        assert len(session["route_cards"]) == 1
        # The harness explicitly offers the injected fake-registry schemas;
        # the real route-planning surface (which never offers the legacy
        # REST plan_trip) is asserted on the real _tools_for_state path.
        assert {schema["name"] for schema in self.loop.client.messages.calls[0]["tools"]} == set(_model_led_registry())

    async def test_route_rounds_keep_the_selected_outer_model(self):
        rounds = [
            _declared_route_round(
                "prepare_route_options",
                "prepare-1",
                {"destination": "Coney Island"},
            ),
            _route_present_round("present-1"),
        ]
        trace = self.loop.TurnTrace()
        await self._run(
            rounds,
            message="Plan a route to Coney Island",
            response_presentation="auto",
            tool_registry=_model_led_registry(),
            trace=trace,
        )
        expected_model = self.loop.agent_policy.policy_for_mode("auto").model
        assert [call["model"] for call in self.loop.client.messages.calls] == [expected_model, expected_model]
        assert [name for name, _tool_input in trace.tool_calls] == ["declare_goals", "prepare_route_options", "present_route"]

    async def test_what_if_preview_stays_temporary_until_a_later_acceptance(self):
        _discard_id, session = session_module.new_session()
        session["active_trip"] = {"card_id": "rc_active", "role": "recommended"}
        trip_state_module.update_trip_state(
            session,
            origin="Home",
            destination="Work",
            active_candidate_set_id="cs_active",
            selected_candidate_id="cd_active",
        )
        candidate_set_id = "cs_what_if"
        candidate_id = "cd_what_if"

        async def prepare_what_if(tool_input, ctx):
            assert tool_input.get("what_if")
            trip_state_module.bind_temporary_candidate_set(
                ctx.session,
                candidate_set_id,
                base_candidate_set_id="cs_active",
            )
            return ToolResult(
                ok=True,
                data={
                    "candidate_set_id": candidate_set_id,
                    "route_status": "good",
                    "presentation_allowed": True,
                    "candidates": [{"candidate_id": candidate_id}],
                },
                summary="prepared temporary route",
            )

        async def present_what_if(tool_input, ctx):
            assert tool_input.get("candidate_id") == candidate_id
            commit = tool_input.get("commit_scenario") is True
            if commit:
                trip_state_module.commit_scenario(
                    ctx.session,
                    candidate_set_id=candidate_set_id,
                    candidate_id=candidate_id,
                    tool_input={"origin": "Home", "destination": "Airport"},
                )
            else:
                trip_state_module.bind_temporary_selected_candidate(
                    ctx.session,
                    candidate_id,
                )
            event = agent_events.RouteCardEvent(
                card_id="rc_what_if",
                turn_id=ctx.turn_id,
                role="recommended",
                origin={"label": "Home", "lat": 40.7, "lng": -73.9},
                destination={"label": "Airport", "lat": 40.64, "lng": -73.78},
                summary={
                    "eta_minutes": 45,
                    "transfers": 1,
                    "lines": ["A"],
                    "reason": "fits",
                },
                route=[{"type": "SUBWAY", "route_id": "A"}],
                alerts=[],
            )
            cards = [{"card_id": "rc_what_if", "role": "recommended"}] if commit else []
            return ToolResult(
                ok=True,
                data={"passenger_explanation": "Take the A to the airport."},
                summary="presented temporary route",
                events=[event],
                session_route_cards=cards,
            )

        registry = _test_registry()
        registry["prepare_route_options"] = ToolSpec(
            schema={"name": "prepare_route_options"},
            executor=prepare_what_if,
            label_fn=lambda _input: "Preparing routes",
            timeout_s=5.0,
        )
        registry["present_route"] = ToolSpec(
            schema={"name": "present_route"},
            executor=present_what_if,
            label_fn=lambda _input: "Presenting route",
            timeout_s=5.0,
        )

        await self._run(
            [
                {
                    "tool_use": [
                        {
                            "id": "prepare-what-if",
                            "name": "prepare_route_options",
                            "input": {
                                "origin": "Home",
                                "destination": "Airport",
                                "destination_source": "current_turn",
                                "what_if": True,
                            },
                        }
                    ],
                    "stop_reason": "tool_use",
                },
                {
                    "tool_use": [
                        {
                            "id": "present-preview",
                            "name": "present_route",
                            "input": {
                                "candidate_id": candidate_id,
                                "lead_in": _DEFAULT_ROUTE_EXPLANATION,
                                "follow_up": "",
                                "reason_code": "meets_hard_constraints",
                            },
                        }
                    ],
                    "stop_reason": "tool_use",
                },
            ],
            message="What if I went to the airport instead?",
            session=session,
            session_id="sess-what-if",
            tool_registry=registry,
        )
        preview_state = trip_state_module.get_trip_state(session)
        assert preview_state["active_candidate_set_id"] == "cs_active"
        assert preview_state["selected_candidate_id"] == "cd_active"
        assert preview_state["temporary_candidate_set_id"] == candidate_set_id
        assert preview_state["temporary_selected_candidate_id"] == candidate_id
        assert session["active_trip"]["card_id"] == "rc_active"

        await self._run(
            [{"text": ["Keep my original trip."], "stop_reason": "end_turn"}],
            message="Never mind",
            session=session,
            session_id="sess-what-if",
            tool_registry=registry,
        )
        unchanged = trip_state_module.get_trip_state(session)
        assert unchanged["active_candidate_set_id"] == "cs_active"
        assert unchanged["selected_candidate_id"] == "cd_active"

        await self._run(
            [
                {
                    "tool_use": [
                        {
                            "id": "present-commit",
                            "name": "present_route",
                            "input": {
                                "candidate_id": candidate_id,
                                "commit_scenario": True,
                                "lead_in": _DEFAULT_ROUTE_EXPLANATION,
                                "follow_up": "",
                                "reason_code": "meets_hard_constraints",
                            },
                        }
                    ],
                    "stop_reason": "tool_use",
                }
            ],
            message="Use the airport route instead",
            session=session,
            session_id="sess-what-if",
            tool_registry=registry,
        )
        committed = trip_state_module.get_trip_state(session)
        assert committed["active_candidate_set_id"] == candidate_set_id
        assert committed["selected_candidate_id"] == candidate_id
        assert committed["temporary_candidate_set_id"] is None
        assert session["active_trip"]["card_id"] == "rc_what_if"

    async def test_parallel_tools_return_single_tool_result_message(self):
        rounds = [
            {
                "tool_use": [
                    {"id": "tu_1", "name": "ok_tool", "input": {"a": 1}},
                    {"id": "tu_2", "name": "fail_tool", "input": {"b": 2}},
                ],
                "stop_reason": "tool_use",
            },
            {"text": ["done"], "stop_reason": "end_turn"},
        ]
        events_out, _session = await self._run(rounds, tool_registry=_test_registry())

        tool_starts = [e for e in events_out if e.type == "tool_start"]
        assert {e.tool for e in tool_starts} == {"ok_tool", "fail_tool"}
        tool_ends = {e.tool: e for e in events_out if e.type == "tool_end"}
        assert tool_ends["ok_tool"].ok
        assert not tool_ends["fail_tool"].ok
        assert tool_ends["fail_tool"].summary == "That action could not be completed"

        second_call_kwargs = self.loop.client.messages.calls[1]
        last_message = second_call_kwargs["messages"][-1]
        assert last_message["role"] == "user"
        assert len(last_message["content"]) == 2
        ids = {block["tool_use_id"] for block in last_message["content"]}
        assert ids == {"tu_1", "tu_2"}
        error_blocks = [b for b in last_message["content"] if b.get("is_error")]
        assert len(error_blocks) == 1
        assert error_blocks[0]["tool_use_id"] == "tu_2"
        assert "boom" in error_blocks[0]["content"]

    async def test_tool_timeout_produces_is_error_tool_end(self):
        rounds = [
            {
                "tool_use": [{"id": "tu_1", "name": "slow_tool", "input": {}}],
                "stop_reason": "tool_use",
            },
            {"text": ["ok"], "stop_reason": "end_turn"},
        ]
        events_out, _ = await self._run(rounds, tool_registry=_test_registry())
        tool_end = next(e for e in events_out if e.type == "tool_end")
        assert not tool_end.ok
        assert tool_end.summary == "That action could not be completed"
        model_context = self.loop.client.messages.calls[1]["messages"][-1]
        assert "timed out" in model_context["content"][0]["content"]

    async def test_route_card_events_emitted_and_stored_in_session(self):
        rounds = [
            _declared_route_round(
                "prepare_route_options", "tu_1", {"destination": "Costco"}
            ),
            _route_present_round("tu_2"),
        ]
        events_out, session = await self._run(
            rounds,
            tool_registry=_model_led_registry(),
        )
        route_cards = [e for e in events_out if e.type == "route_card"]
        assert len(route_cards) == 1
        assert route_cards[0].card_id == "rc_conversational"
        assert route_cards[0].turn_id == "t1"
        assert session["route_cards"][0]["card_id"] == "rc_conversational"

    async def test_rider_can_explicitly_allow_bus_again(self):
        _discard_id, session = session_module.new_session()
        session["slots"] = {"constraints": {"exclude_modes": ["BUS"]}}
        rounds = [
            _declared_route_round(
                "prepare_route_options",
                "tu_1",
                {"destination": "Costco", "allowed_modes": ["BUS"]},
            ),
            _route_present_round("tu_2"),
        ]
        trace = self.loop.TurnTrace()

        await self._run(
            rounds,
            message="Bus is okay now",
            session=session,
            tool_registry=_model_led_registry(),
            trace=trace,
        )

        assert _trace_tool_input(trace, "prepare_route_options")["exclude_modes"] == []

    async def test_route_card_turn_gets_grounded_text_when_model_returns_no_prose(self):
        rounds = [
            _declared_route_round(
                "prepare_route_options", "tu_1", {"destination": "Costco"}
            ),
            _route_present_round("tu_2"),
        ]

        events_out, session = await self._run(
            rounds,
            tool_registry=_model_led_registry(),
        )

        prose = "".join(event.text for event in events_out if event.type == "token")
        assert prose == _DEFAULT_ROUTE_EXPLANATION
        assert session["history"][-1]["role"] == "assistant"
        assert session["history"][-1]["text"] == prose

    async def test_trace_records_tool_calls_and_final_text(self):
        trace = self.loop.TurnTrace()
        rounds = [
            {
                "tool_use": [{"id": "tu_1", "name": "ok_tool", "input": {"x": 1}}],
                "stop_reason": "tool_use",
            },
            _complete_round("final answer"),
        ]
        await self._run(rounds, tool_registry=_test_registry(), trace=trace)
        assert trace.tool_calls == [("ok_tool", {"x": 1}), ("complete_turn", {"outcome": "answer", "message": "final answer"})]
        assert trace.final_text == "final answer"
        assert trace.model_call_count == 2

    async def test_simple_arithmetic_skips_model_and_tools(self):
        events_out, session = await self._run([], message="What is 5 + 5?")
        assert len(self.loop.client.messages.calls) == 0
        assert "".join(event.text for event in events_out if event.type == "token") == "10."
        assert session["history"][-1]["text"] == "10."

    async def test_implicit_arrival_followup_uses_active_first_boarding(self):
        _session_id, session = session_module.new_session()
        session["active_trip"] = {
            "first_boarding": {
                "route_id": "2",
                "stop_id": "247",
                "stop_name": "Church Av",
                "direction_id": 2,
                "direction_label": "Manhattan",
                "destination_stop_id": "120",
            }
        }
        trace = self.loop.TurnTrace()

        events_out, _session = await self._run(
            [
                _tool_round(
                    "check_transit",
                    "arrival-followup",
                    _transit_input("arrivals", route_ids=["2"]),
                ),
                _complete_round("The next 2 train is in 4 minutes."),
            ],
            message="when is the next arrival",
            session=session,
            tool_registry=_test_registry(),
            trace=trace,
        )

        assert trace.tool_calls[0][0] == "check_transit"
        assert trace.tool_calls[0][1]["route_ids"] == ["2"]
        assert len(self.loop.client.messages.calls) == 2
        assert [event.type for event in events_out].count("done") == 1
        assert not any(event.type == "error" for event in events_out)

    async def test_arrival_clarification_is_terminal_and_never_becomes_a_generic_error(
        self,
    ):
        registry = _test_registry()

        async def ambiguous_arrival(tool_input, ctx):
            route_ids = tool_input.get("route_ids") or ["Q"]
            result = await _fake_arrival_clarification_tool(
                {
                    "route_id": route_ids[0],
                    "stop_query": tool_input.get("stop_query"),
                },
                ctx,
            )
            return ToolResult(
                ok=True,
                data={"operation": "arrivals", "result": result.data},
                summary=result.summary,
                events=result.events,
            )

        registry["check_transit"] = ToolSpec(
            schema={"name": "check_transit"},
            executor=ambiguous_arrival,
            label_fn=lambda _input: f"Checking {_input.get('route_id')} arrivals",
            timeout_s=5.0,
        )

        events_out, _session = await self._run(
            [
                _tool_round(
                    "check_transit",
                    "arrival-ambiguous",
                    _transit_input("arrivals", route_ids=["Q"], stop_query="34 St"),
                ),
                _complete_round(
                    "Which station do you mean: 34 St-Penn Station or "
                    "34 St-Hudson Yards?",
                    outcome="clarification",
                ),
            ],
            message="When does the next Q arrive at 34 St?",
            tool_registry=registry,
        )

        assert len(self.loop.client.messages.calls) == 2
        arrival_event = next(
            event for event in events_out if event.type == "arrival_card"
        )
        assert arrival_event.resolution_status == "ambiguous"
        assert not any(event.type == "error" for event in events_out)
        assert [event.type for event in events_out].count("done") == 1
        assert events_out[-1].stop_reason == "clarification_required"
        assert events_out[-1].terminal_state == "clarification_required"

    async def test_station_only_arrival_clarification_can_resume_lookup(self):
        clarification_registry = _test_registry()

        async def ambiguous_arrival(tool_input, ctx):
            route_ids = tool_input.get("route_ids") or ["Q"]
            result = await _fake_arrival_clarification_tool(
                {
                    "route_id": route_ids[0],
                    "stop_query": tool_input.get("stop_query"),
                },
                ctx,
            )
            return ToolResult(
                ok=True,
                data={"operation": "arrivals", "result": result.data},
                summary=result.summary,
                events=result.events,
            )

        clarification_registry["check_transit"] = ToolSpec(
            schema={"name": "check_transit"},
            executor=ambiguous_arrival,
            label_fn=lambda _input: f"Checking {_input.get('route_id')} arrivals",
            timeout_s=5.0,
        )
        first_events, session = await self._run(
            [
                _tool_round(
                    "check_transit",
                    "arrival-first-ambiguous",
                    _transit_input("arrivals", route_ids=["Q"], stop_query="34 St"),
                ),
                _complete_round(
                    "Which 34 St station do you mean?",
                    outcome="clarification",
                ),
            ],
            message="When does the next Q arrive at 34 St?",
            tool_registry=clarification_registry,
        )
        assert first_events[-1].terminal_state == "clarification_required"

        trace = self.loop.TurnTrace()
        await self._run(
            [
                _tool_round(
                    "check_transit",
                    "tu_arrival_followup",
                    _transit_input(
                        "arrivals",
                        route_ids=["Q"],
                        stop_query="34 St-Herald Sq",
                    ),
                ),
                _complete_round("The next downtown Q is in 4 minutes."),
            ],
            message="34 St-Herald Sq",
            session=session,
            tool_registry=_test_registry(),
            trace=trace,
        )

        first_model_call = self.loop.client.messages.calls[0]
        assert "check_transit" in {schema["name"] for schema in first_model_call["tools"]}
        assert trace.tool_calls[0][0] == "check_transit"

    async def test_destination_discovery_is_model_directed_and_grounded_in_both_modes(
        self,
    ):
        for mode, expected_limit in (("auto", 3), ("quick", 2)):
            with self.subTest(mode=mode):
                # Other test classes reload the module with narrow deadline
                # fixtures; use a fresh fake client for each presentation.
                self.loop = _load_agent_loop()
                trace = self.loop.TurnTrace()
                with (
                    patch.object(self.loop, "AGENT_TURN_DEADLINE_S", 60),
                    patch.object(self.loop.budget, "AGENT_DAILY_SPEND_LIMIT_USD", 5),
                ):
                    events_out, _session = await self._run(
                        [
                            {
                                "tool_use": [
                                    {
                                        "id": "place-goals",
                                        "name": "declare_goals",
                                        "input": {
                                            "goals": [
                                                {
                                                    "goal_key": "places",
                                                    "kind": "place_recommendation",
                                                    "depends_on": [],
                                                }
                                            ]
                                        },
                                    },
                                    {
                                        "id": "poi-1",
                                        "name": "discover_places",
                                        "input": {
                                            "goal_key": "places",
                                            "operation": "search",
                                            "query": "pizza Brooklyn",
                                            "scope": {
                                                "kind": "boroughs",
                                                "values": ["Brooklyn"],
                                            },
                                            "open_now": None,
                                            "max_results": expected_limit,
                                            "candidate_names": [],
                                        },
                                    },
                                ],
                                "stop_reason": "tool_use",
                            },
                            _tool_round(
                                "present_places",
                                "present-1",
                                {
                                    "discovery_set_id": "ds_test_only",
                                    "selections": [
                                        {
                                            "place_id": "pl_di_fara",
                                            "reason": "preference_match",
                                        }
                                    ],
                                    "research_used": False,
                                    "goal_key": "places",
                                    "lead_in": "",
                                    "follow_up": "",
                                },
                            ),
                        ],
                        message="What is one of the best pizza places in Brooklyn?",
                        response_presentation=mode,
                        tool_registry=_model_led_registry(),
                        trace=trace,
                    )
                assert trace.tool_calls, f"events={[event.type for event in events_out]} " f"errors={[getattr(event, 'code', '') for event in events_out if event.type == 'error']} " f"model_calls={len(self.loop.client.messages.calls)}"
                assert trace.tool_calls[0][0] == "declare_goals"
                assert _trace_tool_input(trace, "discover_places")["max_results"] == expected_limit
                assert len(self.loop.client.messages.calls) == 2
                response_text = "".join(
                    event.text
                    for event in events_out
                    if isinstance(event, agent_events.TokenEvent)
                )
                assert response_text == "Here are current verified matches."
                # The harness explicitly offers the injected fake-registry
                # schemas, so the real discovery surface (including the
                # native web_search appended by state policy) is asserted on
                # the real _tools_for_state path.
                assert "discover_places" in {schema["name"] for schema in self.loop.client.messages.calls[0]["tools"]}
                schemas = self.loop._tools_for_state(
                    self.loop.agent_policy.policy_for_mode(mode)
                )
                names = {schema["name"] for schema in schemas}
                assert names == set(self.loop.public_surface.INITIAL_TOOL_NAMES)
                assert "web_search" not in names
                assert "web_search" in {schema["name"] for schema in self.loop.client.messages.calls[1]["tools"]}

    async def test_failed_trip_resume_offer_is_added_once_without_auto_retry(self):
        _session_id, session = session_module.new_session()
        session_module.mark_pending_trip_failed(
            session,
            {"destination": "JFK"},
            "routing timed out",
        )
        first, session = await self._run([], message="5 + 5", session=session)
        second, _session = await self._run([], message="2 + 2", session=session)
        first_text = "".join(event.text for event in first if event.type == "token")
        second_text = "".join(event.text for event in second if event.type == "token")
        assert "retry the trip to JFK" in first_text
        assert second_text == "4."
        assert len(self.loop.client.messages.calls) == 0


class RoundCapTests(_AgentLoopHelpers, unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        cls.loop = _load_agent_loop(
            {"AGENT_AUTO_MAX_ROUNDS": "2", "AGENT_TURN_DEADLINE_S": "60"}
        )

    def setUp(self):
        cache._mem.clear()


class DeadlineTests(_AgentLoopHelpers, unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        # A deadline in the past trips on the very first check, before any
        # real round -- deterministic without needing to fake wall-clock time.
        cls.loop = _load_agent_loop(
            {"AGENT_MAX_ROUNDS": "50", "AGENT_TURN_DEADLINE_S": "-1"}
        )

    def setUp(self):
        cache._mem.clear()

    async def test_deadline_exceeded_before_first_round_never_starts_wrapup(self):
        events_out, _session = await self._run([], tool_registry=_test_registry())

        assert len(self.loop.client.messages.calls) == 0
        done = events_out[-1]
        assert done.stop_reason == "deadline"

    async def test_near_deadline_tool_is_cancelled_and_returns_deadline_terminal(self):
        async def slow_arrivals(_tool_input, _ctx):
            await asyncio.sleep(0.5)
            return ToolResult(ok=True, data={"source_status": "available"})

        registry = _test_registry()
        registry["lookup_arrivals"] = ToolSpec(
            schema={"name": "lookup_arrivals"},
            executor=slow_arrivals,
            label_fn=lambda _input: "Checking arrivals",
            timeout_s=5.0,
        )
        # Leave enough headroom for the mocked model round to begin the tool;
        # the much slower executor still deterministically crosses the turn
        # deadline and exercises in-flight cancellation rather than scheduler
        # timing before the tool starts.
        with patch.object(self.loop, "AGENT_TURN_DEADLINE_S", 0.1):
            events_out, _session = await self._run(
                [
                    {
                        "tool_use": [
                            {
                                "id": "slow-arrival",
                                "name": "lookup_arrivals",
                                "input": {"route_id": "Q"},
                            }
                        ],
                        "stop_reason": "tool_use",
                    }
                ],
                message="When is the next Q train?",
                tool_registry=registry,
            )
        assert [event.type for event in events_out].count("done") == 1
        assert events_out[-1].stop_reason == "deadline"
        assert [event.code for event in events_out if event.type == "error"] == ["deadline"]
        assert any(event.type == "tool_start" for event in events_out)
        assert any(event.type == "tool_end" and not event.ok for event in events_out)

    async def test_grounded_route_card_completes_without_a_followup_model_round(self):
        calls = 0

        async def scripted_stream(**stream_kwargs):
            nonlocal calls
            calls += 1
            if calls == 1:
                message = types.SimpleNamespace(
                    content=[
                        types.SimpleNamespace(
                            type="tool_use",
                            id="goals-1",
                            name="declare_goals",
                            input={
                                "goals": [
                                    {
                                        "goal_key": "route",
                                        "kind": "route",
                                        "depends_on": [],
                                    }
                                ]
                            },
                        ),
                        types.SimpleNamespace(
                            type="tool_use",
                            id="prepare-1",
                            name="prepare_route_options",
                            input={
                                "goal_key": "route",
                                "destination": "Costco",
                                "destination_source": "current_turn",
                            },
                        ),
                    ],
                    stop_reason="tool_use",
                    usage=types.SimpleNamespace(input_tokens=1, output_tokens=1),
                )
                yield model_stream.ModelCallCompleted(message, None, 1)
                return
            if calls == 2:
                message = types.SimpleNamespace(
                    content=[
                        types.SimpleNamespace(
                            type="tool_use",
                            id="present-1",
                            name="present_route",
                            input={
                                "candidate_id": "cd_test_only",
                                "goal_key": "route",
                                "lead_in": _DEFAULT_ROUTE_EXPLANATION,
                                "follow_up": "",
                                "reason_code": "meets_hard_constraints",
                            },
                        )
                    ],
                    stop_reason="tool_use",
                    usage=types.SimpleNamespace(input_tokens=1, output_tokens=1),
                )
                yield model_stream.ModelCallCompleted(message, None, 1)
                return
            remaining = max(0.0, stream_kwargs["deadline_monotonic"] - time.monotonic())
            await asyncio.sleep(remaining + 0.001)
            yield model_stream.ModelCallCompleted(
                None,
                agent_events.ErrorEvent(
                    code="deadline", message="timed out", retryable=True
                ),
                1,
            )

        started = time.monotonic()
        with (
            patch.object(self.loop, "AGENT_TURN_DEADLINE_S", 0.2),
            patch.object(model_stream, "stream_model_call", scripted_stream),
        ):
            events_out, session = await self._run(
                [],
                message="Plan a trip",
                tool_registry=_model_led_registry(),
            )

        assert time.monotonic() - started < 0.5
        assert calls == 2
        assert len([event for event in events_out if event.type == "done"]) == 1
        assert events_out[-1].stop_reason == "end_turn"
        assert any(event.type == "route_card" and event.card_id == "rc_conversational" for event in events_out)
        assert any(_DEFAULT_ROUTE_EXPLANATION in event.text for event in events_out if event.type == "token")
        assert any(card["card_id"] == "rc_conversational" for card in session["route_cards"])


class AgentDisabledBudgetTests(_AgentLoopHelpers, unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        cls.loop = _load_agent_loop()

    def setUp(self):
        cache._mem.clear()

    async def test_agent_enabled_false_short_circuits_before_any_model_call(self):
        with patch.dict(os.environ, {"AGENT_ENABLED": "0"}):
            events_out, _session = await self._run([])
        assert events_out[0].type == "meta"
        error = next(e for e in events_out if e.type == "error")
        assert error.code == "budget_exceeded"
        assert events_out[-1].type == "done"
        assert len(self.loop.client.messages.calls) == 0


class MockAgentModeTests(_AgentLoopHelpers, unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        cls.loop = _load_agent_loop({"SMARTROUTE_ENV": "test", "AGENT_MOCK_MODE": "1"})

    def setUp(self):
        cache._mem.clear()

    async def test_mock_mode_streams_preview_events_without_a_model_call(self):
        with patch.dict(os.environ, {"AGENT_MOCK_STEP_DELAY_MS": "0"}):
            events_out, session = await self._run(
                [], message="Heading to Costco with a cart"
            )

        event_types = [event.type for event in events_out]
        assert event_types[0] == "meta"
        assert event_types[-1] == "done"
        assert "tool_start" in event_types
        assert "tool_end" in event_types
        assert "token" in event_types
        assert "route_card" in event_types
        assert len(self.loop.client.messages.calls) == 0
        assert "preview" in "".join(event.text for event in events_out if event.type == "token").casefold()
        assert session["route_cards"][-1]["card_id"] == "mock-t1"

    async def test_quick_mock_copy_is_shorter_without_changing_route_facts(self):
        automatic = mock_turn.mock_trip_copy("Heading to Costco", "auto")
        quick = mock_turn.mock_trip_copy("Heading to Costco", "quick")

        assert len(quick[0]) < len(automatic[0])
        assert quick[1:] == automatic[1:]


class RateLimitBudgetTests(_BudgetConfiguredAgentLoopTests):
    BUDGET_ENV: ClassVar[dict[str, str]] = {"AGENT_TURNS_PER_SESSION_PER_MIN": "1"}

    def setUp(self):
        cache._mem.clear()

    async def test_second_turn_in_the_same_minute_is_rate_limited(self):
        session_id = "rate-limit-fixed-session"
        first, _session = await self._run(
            [{"text": ["ok"], "stop_reason": "end_turn"}], session_id=session_id
        )
        assert first[-1].stop_reason == "end_turn"

        second, _session2 = await self._run([], session_id=session_id)
        error = next(e for e in second if e.type == "error")
        assert error.code == "rate_limited"
        assert len(self.loop.client.messages.calls) == 0


class DailySpendBudgetTests(_BudgetConfiguredAgentLoopTests):
    BUDGET_ENV: ClassVar[dict[str, str]] = {"AGENT_DAILY_SPEND_LIMIT_USD": "0.000001"}

    def setUp(self):
        cache._mem.clear()

    async def test_daily_spend_over_limit_blocks_the_next_turn(self):
        self.budget.record_usage_cost(1000, 1000)  # trivially exceeds the tiny limit
        events_out, _session = await self._run([])
        error = next(e for e in events_out if e.type == "error")
        assert error.code == "budget_exceeded"
        assert not error.retryable
        assert len(self.loop.client.messages.calls) == 0


class ConcurrencyBudgetTests(_BudgetConfiguredAgentLoopTests):
    BUDGET_ENV: ClassVar[dict[str, str]] = {"AGENT_MAX_CONCURRENT_STREAMS": "1"}

    def setUp(self):
        cache._mem.clear()

    async def test_concurrency_semaphore_rejects_when_the_single_slot_is_taken(self):
        sem = self.budget.concurrency_semaphore()
        await sem.acquire()
        try:
            events_out, _session = await self._run([])
        finally:
            sem.release()
        error = next(e for e in events_out if e.type == "error")
        assert error.code == "rate_limited"
        assert len(self.loop.client.messages.calls) == 0


class DeterministicAndDeduplicationTests(
    _AgentLoopHelpers, unittest.IsolatedAsyncioTestCase
):
    @classmethod
    def setUpClass(cls):
        cls.loop = _load_agent_loop()

    def setUp(self):
        cache._mem.clear()

    async def test_ambiguous_transit_question_remains_model_backed(self):
        events_out, _session = await self._run(
            [
                _complete_round(
                    "Which line are you asking about?",
                    outcome="clarification",
                )
            ],
            message="Is the subway running?",
            tool_registry=_test_registry(),
        )
        assert any(event.type == "token" for event in events_out)
        assert len(self.loop.client.messages.calls) == 1

    async def test_turn_ledger_reuses_successes_and_retries_failures(self):
        calls = {"success": 0, "failure": 0}

        async def succeeds(tool_input, _ctx):
            calls["success"] += 1
            value = tool_input["value"]
            return ToolResult(
                ok=True,
                data={"value": value},
                summary=f"ok-{value}",
                events=[agent_events.TokenEvent(text=f"effect-{value}")],
                session_route_cards=[
                    {"card_id": f"card-{value}", "role": "recommended"}
                ],
                timings={"render_ms": 5},
            )

        async def fails_then_succeeds(_tool_input, _ctx):
            calls["failure"] += 1
            succeeded = calls["failure"] == 2
            return ToolResult(
                ok=succeeded,
                data={"retry": True},
                error=None if succeeded else "retry",
                events=[agent_events.TokenEvent(text="done")] if succeeded else [],
                terminal=succeeded,
                terminal_path="complete_turn" if succeeded else None,
            )

        registry = {
            "success": ToolSpec(
                schema={"name": "success"},
                executor=succeeds,
                label_fn=lambda _i: "Working",
                timeout_s=5,
            ),
            "failure": ToolSpec(
                schema={"name": "failure"},
                executor=fails_then_succeeds,
                label_fn=lambda _i: "Working",
                timeout_s=5,
            ),
        }
        rounds = [
            {
                "tool_use": [
                    {"id": "a", "name": "success", "input": {"value": 1, "other": 2}},
                    {"id": "b", "name": "success", "input": {"other": 2, "value": 1}},
                ],
                "stop_reason": "tool_use",
            },
            {
                "tool_use": [
                    {"id": "c", "name": "success", "input": {"value": 1, "other": 2}},
                    {"id": "d", "name": "success", "input": {"value": 2, "other": 2}},
                    {"id": "e", "name": "failure", "input": {"value": 1}},
                ],
                "stop_reason": "tool_use",
            },
            {
                "tool_use": [{"id": "f", "name": "failure", "input": {"value": 1}}],
                "stop_reason": "tool_use",
            },
        ]
        trace = self.loop.TurnTrace()
        events_out, session = await self._run(
            rounds, tool_registry=registry, trace=trace
        )

        assert calls == {"success": 2, "failure": 2}
        visible_text = "".join(
            event.text for event in events_out if event.type == "token"
        )
        assert visible_text == "effect-1\n\neffect-2\n\ndone"
        assert [entry["text"] for entry in session["history"] if entry["role"] == "tool"] == ["ok-1", "ok-2"]
        assert [card["card_id"] for card in session["route_cards"]] == ["card-1", "card-2"]
        assert trace.stage_ms["render_ms"] == 10
        assert len(trace.tool_calls) == 6
        assert trace.model_tool_use_count == 6
        assert trace.provider_tool_execution_count == 4
        assert len([event for event in events_out if event.type == "tool_end"]) == 6

    async def test_turn_ledger_caps_block_provider_work_at_the_boundaries(self):
        calls = 0

        async def fake_run(*_args, **_kwargs):
            nonlocal calls
            calls += 1
            return ToolResult(ok=True, data={})

        ledger = self.loop.TurnToolLedger()
        with (
            patch.object(self.loop, "MAX_TOOL_EXECUTIONS_PER_TURN", 2),
            patch.object(self.loop, "MAX_TOOL_EXECUTIONS_PER_NAME", 1),
            patch.object(self.loop, "_run_one_tool", fake_run),
        ):
            assert (await ledger.execute("one", {"value": 1}, ToolContext(), deadline_monotonic=999999)).ok
            assert not (await ledger.execute("one", {"value": 2}, ToolContext(), deadline_monotonic=999999)).ok
            assert (await ledger.execute("two", {"value": 1}, ToolContext(), deadline_monotonic=999999)).ok
            assert not (await ledger.execute("three", {"value": 1}, ToolContext(), deadline_monotonic=999999)).ok

        assert calls == 2


class AgentBudgetIsolationTests(unittest.TestCase):
    def test_budget_classes_are_order_independent_across_repeated_runs(self):
        budget_module = importlib.import_module("app.services.agent.model.budget")
        original_limits = (
            budget_module.AGENT_TURNS_PER_SESSION_PER_MIN,
            budget_module.AGENT_DAILY_SPEND_LIMIT_USD,
            budget_module.AGENT_MAX_CONCURRENT_STREAMS,
        )
        selected_classes = [
            (
                RateLimitBudgetTests,
                "test_second_turn_in_the_same_minute_is_rate_limited",
            ),
            (DailySpendBudgetTests, "test_daily_spend_over_limit_blocks_the_next_turn"),
            (
                ConcurrencyBudgetTests,
                "test_concurrency_semaphore_rejects_when_the_single_slot_is_taken",
            ),
            (
                LoopMechanicsTests,
                "test_destination_discovery_is_model_directed_and_grounded_in_both_modes",
            ),
        ]

        orders = []
        for ordered_classes in (list(selected_classes), list(reversed(selected_classes))):
            orders.append(
                [test_case.__name__ for test_case, _method in ordered_classes]
            )
            suite = unittest.TestSuite(
                test_case(method) for test_case, method in ordered_classes
            )
            result = unittest.TestResult()
            suite.run(result)
            assert result.wasSuccessful(), f"failures={result.failures} errors={result.errors}"
            assert original_limits == (
                budget_module.AGENT_TURNS_PER_SESSION_PER_MIN,
                budget_module.AGENT_DAILY_SPEND_LIMIT_USD,
                budget_module.AGENT_MAX_CONCURRENT_STREAMS,
            )

        assert orders[0] != orders[1]


if __name__ == "__main__":
    unittest.main()
