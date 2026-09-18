"""Batch E3: ambiguity, contradictions, and temporal server boundaries.

Drives the *real* agent loop with production intent/tool filtering, real
registry/executors, real stores, ledger, and SSE events; only the documented
provider/data seams are scripted and Anthropic inference is deterministic
mock text. Time validation is additionally probed through the *real*
``prepare_route_options`` executor with the *real* ``prepare_single_leg``
validation body, so "rejected invalid times cannot create candidate/card
state" is proven at the canonical validation seam.

Families: E3-A ambiguous/missing references, E3-B contradictions/precedence
(deterministic precedence only where server logic defines it), E3-C temporal
server boundaries (NL interpretation is live-model backlog; the server
validates ISO format and time exclusivity). Production is not modified here.
"""

from __future__ import annotations

from app.services.agent import tool_input_policy
from app.services.agent import trip_state as trip_state_module
from app.services.directions import GoogleRoutesError

from tests.conversation.conversation_ambiguity_fixtures import (
    ARRIVAL_PAST,
    AVOID_AND_TAKE_Q,
    AVOID_STAIRS,
    BOTH_TIMES_MARKER,
    CONTROL_ROUTE_MESSAGE,
    DEPART_PAST,
    DEPART_PLUS_30,
    DERIVE_FAILED_MARKER,
    DEST_REQUIRED_MARKER,
    DONT_CHANGE_MAKE_THIS,
    FIXED_CANDIDATE_ID,
    LEAVE_NOW_ARRIVE_YESTERDAY,
    NAV_NO_CONTEXT,
    NO_BUS_ACTUALLY_BUS,
    NO_WALKING,
    ORDINAL_NO_CONTEXT,
    ORIGIN_ONLY_MESSAGES,
    PLUS_FIVE_PHRASE,
    REPLAN_WITHOUT_DESTINATION,
    RFC3339_MARKER,
    TEMPORAL_ROUTE_MESSAGES,
    TZ_OFFSET_MARKER,
    WHAT_IF_PLUS_30,
    bus_only_leg,
    inaccessible_leg,
)
from tests.conversation.conversation_ambiguity_support import _E3Base
from tests.conversation.conversation_matrix_harness import (
    _turn_round,
    load_agent_loop,
    make_leg,
    q_only_leg,
    route_cards,
)

INITIAL_TOOL_PROFILE = frozenset(
    {
        "declare_goals",
        "discover_places",
        "check_transit",
        "prepare_route_options",
        "complete_turn",
    }
)
ROUTE_TOOL_PROFILE = INITIAL_TOOL_PROFILE
TRANSIT_QUESTION_TOOL_PROFILE = INITIAL_TOOL_PROFILE
DISCOVERY_REFERENCE_TOOL_PROFILE = INITIAL_TOOL_PROFILE


def _declared_round(goals: list[dict], *tool_calls: dict) -> dict:
    return {
        "tool_use": [
            {
                "id": "tu-goals",
                "name": "declare_goals",
                "input": {"goals": goals},
            },
            *tool_calls,
        ],
        "stop_reason": "tool_use",
    }


def _goal(goal_key: str, kind: str, *, depends_on: list[str] | None = None) -> dict:
    return {
        "goal_key": goal_key,
        "kind": kind,
        "depends_on": depends_on or [],
    }


def _complete_goal_round(
    goal_key: str,
    outcome: str,
    message: str,
    *,
    tool_id: str = "tu-done",
) -> dict:
    return _turn_round(
        "complete_turn",
        tool_id,
        {
            "goal_keys": [goal_key],
            "outcome": outcome,
            "message": message,
        },
    )


def _route_prepare_round(tool_id: str, tool_input: dict) -> dict:
    payload = dict(tool_input)
    has_explicit_destination = bool(
        payload.get("destination") or payload.get("destination_place_id")
    )
    payload.setdefault(
        "destination_source",
        "current_turn" if has_explicit_destination else "accepted_trip",
    )
    return _declared_round(
        [_goal("route", "route")],
        {
            "id": tool_id,
            "name": "prepare_route_options",
            "input": {**payload, "goal_key": "route"},
        },
    )


class _MigratedE3Base(_E3Base):
    """E3 harness adapter for declared goals and state-valid terminals."""

    async def _no_tool_turn(self, *, session, session_id, message, mode, turn_id):
        return await self._scripted_turn(
            session=session,
            session_id=session_id,
            message=message,
            rounds=[
                _declared_round(
                    [_goal("clarify", "general_response")],
                ),
                _complete_goal_round(
                    "clarify",
                    "clarification",
                    "I need more context before I can do that safely.",
                ),
            ],
            mode=mode,
            turn_id=turn_id,
        )

    async def _cancel_scenario_turn(
        self, *, session, session_id, message, mode, turn_id
    ):
        return await self._scripted_turn(
            session=session,
            session_id=session_id,
            message=message,
            rounds=[
                _declared_round([_goal("scenario", "route")]),
                _complete_goal_round(
                    "scenario",
                    "cancelled",
                    "Okay, I kept your current trip.",
                ),
            ],
            mode=mode,
            turn_id=turn_id,
        )

    async def _no_context_noop_turn(
        self,
        *,
        session,
        session_id,
        message,
        mode,
        turn_id,
        scenario_id,
        pristine=True,
        offered=None,
    ):
        before = self._snapshot(session)
        events, trace, mocks = await self._no_tool_turn(
            session=session,
            session_id=session_id,
            message=message,
            mode=mode,
            turn_id=turn_id,
        )
        if offered is not None:
            assert self._offered() == offered, f"{scenario_id} offered={sorted(self._offered())}"
        assert self._names(trace) == ["declare_goals", "complete_turn"], f"{scenario_id} clarification terminal"
        self._assert_no_route_surface(scenario_id, trace, events, mocks)
        if pristine:
            self._assert_pristine_route_state(
                scenario_id,
                trip_state_module.get_trip_state(session),
            )
        self._assert_snapshot_unchanged(scenario_id, before, self._snapshot(session))
        self._assert_policy(mode, trace, scenario_id)
        return events, trace, mocks

    async def _failed_prepare_turn(
        self,
        *,
        session,
        session_id,
        message,
        mode,
        turn_id,
        prepare_input=None,
        prepare_leg=None,
    ):
        rounds = [
            _route_prepare_round("tu-prep", prepare_input or {}),
        ]
        return await self._scripted_turn(
            session=session,
            session_id=session_id,
            message=message,
            rounds=rounds,
            mode=mode,
            turn_id=turn_id,
            prepare_leg=prepare_leg,
        )

    async def _origin_only_scenario(self, *, mode, message, scenario_id):
        """Origin-only route turn: bounded destination-missing failure."""

        session_id, session = self._new_session(mode)
        events, trace, mocks = await self._failed_prepare_turn(
            session=session,
            session_id=session_id,
            message=message,
            mode=mode,
            turn_id="t1",
            prepare_input={"origin": "Home"},
            prepare_leg=make_leg(destination="Work"),
        )
        assert self._offered() == ROUTE_TOOL_PROFILE, f"{scenario_id} route profile offered"
        self._assert_failed_prepare(
            scenario_id=scenario_id,
            events=events,
            trace=trace,
            mocks=mocks,
            marker=DEST_REQUIRED_MARKER,
            provider_not_reached=True,
        )
        state = trip_state_module.get_trip_state(session)
        assert state["destination"] is None, f"{scenario_id} no destination"
        assert session["pending_trip"]["status"] == "failed", f"{scenario_id} pending trip records the bounded failure"
        self._assert_policy(mode, trace, scenario_id)

    def _assert_failed_prepare(
        self,
        *,
        scenario_id,
        events,
        trace,
        mocks,
        marker,
        provider_not_reached=False,
    ):
        assert self._names(trace) == ["declare_goals", "prepare_route_options"], f"{scenario_id} tool sequence"
        ends = self._tool_ends(events)
        ok, summary = ends["prepare_route_options"]
        assert not ok, f"{scenario_id} prepare must fail safely"
        assert summary == "Route options could not be prepared", f"{scenario_id} rider-safe bounded failure"
        assert marker not in (summary or ""), f"{scenario_id} hides diagnostics"
        self._assert_no_card(events, scenario_id)
        self._assert_no_candidate_sets(mocks, scenario_id)
        if provider_not_reached:
            self._assert_provider_not_reached(mocks, scenario_id)

    async def _hard_accessibility_scenario(self, *, mode, scenario_id):
        session, session_id, seed = self._seed_accepted(mode)
        events, _trace, mocks = await self._scripted_turn(
            session=session,
            session_id=session_id,
            message=AVOID_STAIRS,
            rounds=[
                _route_prepare_round(
                    "tu-access",
                    {
                        "destination": seed.destination,
                        "accessibility_required": True,
                        "avoid_stairs": True,
                    },
                ),
            ],
            mode=mode,
            turn_id="t1",
            prepare_leg=inaccessible_leg(seed.destination),
        )
        self._assert_no_card(events, scenario_id)
        audit = self._assert_audit(
            scenario_id=scenario_id,
            session_id=session_id,
            mocks=mocks,
            expected_status="no_hard_constraint_match",
            violations=("accessibility_unknown_or_unavailable",),
        )
        assert audit["tool_input"]["accessibility_required"], scenario_id
        assert audit["tool_input"]["avoid_stairs"], scenario_id
        self._assert_accepted_preserved(
            scenario_id=scenario_id,
            session=session,
            seed=seed,
        )

    async def _iso_departure_route_turn(
        self,
        *,
        mode,
        scenario_id,
        message,
        departure_iso,
        fixed_candidate_id,
    ):
        session_id, session = self._new_session(mode)
        prepare_input = {"destination": "Work"}
        if departure_iso is not None:
            prepare_input["departure_time"] = departure_iso
        events, trace, _mocks = await self._scripted_turn(
            session=session,
            session_id=session_id,
            message=message,
            rounds=[
                _route_prepare_round("tu-prep", prepare_input),
                _turn_round(
                    "present_route",
                    "tu-pres",
                    {
                        "goal_key": "route",
                        "candidate_id": fixed_candidate_id,
                    },
                ),
            ],
            mode=mode,
            turn_id="t1",
            prepare_leg=make_leg(destination="Work"),
            fixed_candidate_id=fixed_candidate_id,
        )
        assert self._names(trace) == ["declare_goals", "prepare_route_options", "present_route"], f"{scenario_id} canonical chain"
        assert "web_search" not in self._offered(), scenario_id
        cards = route_cards(events)
        assert len(cards) == 1, f"{scenario_id} one recommended card"
        assert cards[0].role == "recommended", scenario_id
        state = trip_state_module.get_trip_state(session)
        assert state["selected_candidate_id"] == fixed_candidate_id, scenario_id
        if departure_iso is None:
            assert state["planning_mode"] == "leave_now", scenario_id
            assert state["requested_departure"] is None, scenario_id
        else:
            assert state["planning_mode"] == "depart_at", scenario_id
            assert state["requested_departure"] == departure_iso, scenario_id
            assert cards[0].depart_iso == departure_iso, scenario_id
            assert (session.get("slots") or {}).get("time_anchor") == departure_iso, scenario_id
        self._assert_policy(mode, trace, scenario_id)
        return events, trace, _mocks


class MissingReferenceNoContextTests(_MigratedE3Base):
    """E3-A: no fabricated destination/candidate/card without context."""

    @classmethod
    def setUpClass(cls):
        cls.loop = load_agent_loop()

    async def _nav_no_context(self, mode: str):
        scenario_id = f"E3A-NAV-{mode}"
        session_id, session = self._new_session(mode)
        await self._no_context_noop_turn(
            session=session, session_id=session_id, message=NAV_NO_CONTEXT,
            mode=mode, turn_id="t1", scenario_id=scenario_id,
            offered=ROUTE_TOOL_PROFILE)

    async def test_e3_nav_no_context(self):
        for mode in ("auto",):
            with self.subTest(mode=mode):
                await self._nav_no_context(mode)

    async def _nav_no_context_accepted_trip(self, mode: str):
        scenario_id = f"E3A-NAV-ACC-{mode}"
        session, session_id, seed = self._seed_accepted(mode)
        await self._no_context_noop_turn(
            session=session, session_id=session_id, message=NAV_NO_CONTEXT,
            mode=mode, turn_id="t1", scenario_id=scenario_id, pristine=False,
            offered=ROUTE_TOOL_PROFILE)
        self._assert_accepted_preserved(scenario_id=scenario_id, session=session,
                                        seed=seed)

    async def test_e3_nav_no_context_accepted_trip(self):
        for mode in ("auto",):
            with self.subTest(mode=mode):
                await self._nav_no_context_accepted_trip(mode)

    async def _ordinal_no_active_set(self, mode: str):
        scenario_id = f"E3A-ORD-{mode}"
        session_id, session = self._new_session(mode)
        await self._no_context_noop_turn(
            session=session, session_id=session_id, message=ORDINAL_NO_CONTEXT,
            mode=mode, turn_id="t1", scenario_id=scenario_id,
            offered=TRANSIT_QUESTION_TOOL_PROFILE)
        assert "get_place_details" not in self._offered(), f"{scenario_id}"

    async def test_e3_ordinal_no_active_set(self):
        for mode in ("auto",):
            with self.subTest(mode=mode):
                await self._ordinal_no_active_set(mode)

    async def test_e3_ordinal_expired_set_binds_nothing_auto(self):
        """E3-A: an expired set resolves nothing; no ordinal fabrication."""

        scenario_id = "E3A-ORD-EXPIRED-auto"
        session_id, session = self._new_session("auto")
        set_id, record = self._bind_discovery_set(session_id, session)
        rounds = [
            _declared_round([_goal("clarify", "general_response")]),
            _complete_goal_round(
                "clarify",
                "clarification",
                "That result is no longer available. Search again so I can resolve it safely.",
                tool_id="tu-ref",
            ),
        ]
        with self._expired_clock(record):
            events, trace, mocks = await self._scripted_turn(
                session=session, session_id=session_id, message=ORDINAL_NO_CONTEXT,
                rounds=rounds, mode="auto", turn_id="t2")
        assert self._offered() == DISCOVERY_REFERENCE_TOOL_PROFILE, f"{scenario_id} public capability surface"
        assert self._names(trace) == ["declare_goals", "complete_turn"], f"{scenario_id}"
        assert events[-1].stop_reason == "clarification_required"
        self._assert_no_route_surface(scenario_id, trace, events, mocks)
        state = trip_state_module.get_trip_state(session)
        assert state["active_discovery_set_id"] == set_id, f"{scenario_id}"
        assert state["selected_place_id"] is None, f"{scenario_id}"
        self._assert_pristine_route_state(scenario_id, state)


class MissingDestinationRouteTests(_MigratedE3Base):
    """E3-A: origin-only / destination-missing turns cannot plan."""

    @classmethod
    def setUpClass(cls):
        cls.loop = load_agent_loop()

    async def _origin_only(self, mode: str, message: str):
        scenario_id = f"E3A-ORIGIN-{mode}-{message[:12]}"
        await self._origin_only_scenario(
            mode=mode, message=message, scenario_id=scenario_id)

    async def test_e3_origin_only_route_turns(self):
        for message in ORIGIN_ONLY_MESSAGES:
            with self.subTest(message=message):
                await self._origin_only("auto", message)

    async def test_e3_replan_without_any_destination_cannot_plan_auto(self):
        """E3-A: no canonical destination (input or trip state) means no plan."""

        scenario_id = "E3A-REPLAN-NODEST-auto"
        session_id, session = self._new_session("auto")
        events, trace, mocks = await self._failed_prepare_turn(
            session=session, session_id=session_id, message=REPLAN_WITHOUT_DESTINATION,
            mode="auto", turn_id="t1", prepare_input={},
            prepare_leg=make_leg(destination="Work"))
        assert self._offered() == ROUTE_TOOL_PROFILE, f"{scenario_id}"
        self._assert_failed_prepare(scenario_id=scenario_id, events=events,
                                    trace=trace, mocks=mocks,
                                    marker=DEST_REQUIRED_MARKER,
                                    provider_not_reached=True)
        state = trip_state_module.get_trip_state(session)
        assert state["destination"] is None, f"{scenario_id} no destination"
        assert state["active_candidate_set_id"] is None, f"{scenario_id} no candidate bound"


class ContradictionPrecedenceTests(_MigratedE3Base):
    """E3-B: deterministic precedence only where server logic defines it."""

    @classmethod
    def setUpClass(cls):
        cls.loop = load_agent_loop()

    async def _avoid_and_take_q(self, mode: str):
        scenario_id = f"E3B-AVOID-TAKE-Q-{mode}"
        session, session_id, seed = self._seed_accepted(mode)
        events, trace, mocks = await self._scripted_turn(
            session=session, session_id=session_id, message=AVOID_AND_TAKE_Q,
            rounds=[
                # The model resolves the contradiction into one hard route
                # exclusion; rider prose is not a server-side router.
                _route_prepare_round(
                    "tu-q",
                    {
                        "destination": seed.destination,
                        "excluded_route_ids": ["Q"],
                    },
                ),
            ],
            mode=mode, turn_id="t1", prepare_leg=q_only_leg(seed.destination))
        assert self._names(trace) == ["declare_goals", "prepare_route_options"], f"{scenario_id} sequence"
        prepare_input = trace.tool_calls[1][1]
        assert prepare_input.get("excluded_route_ids") == ["Q"], f"{scenario_id} precedence: exclusion wins over take"
        assert "required_route_ids" not in prepare_input, f"{scenario_id} excluded Q is never also required"
        self._assert_no_card(events, scenario_id)
        self._assert_audit(
            scenario_id=scenario_id, session_id=session_id, mocks=mocks,
            expected_status="no_hard_constraint_match", violations=("excluded_route",))
        self._assert_accepted_preserved(scenario_id=scenario_id, session=session,
                                        seed=seed)
        assert (session.get("slots") or {}).get("constraints", {}).get("excluded_route_ids") == ["Q"], f"{scenario_id} exclusion persisted"

    async def test_e3_avoid_q_take_q(self):
        for mode in ("auto",):
            with self.subTest(mode=mode):
                await self._avoid_and_take_q(mode)

    async def _no_buses_actually_only_buses(self, mode: str):
        scenario_id = f"E3B-NOBUS-{mode}"
        session, session_id, seed = self._seed_accepted(mode)
        events, trace, mocks = await self._scripted_turn(
            session=session, session_id=session_id, message=NO_BUS_ACTUALLY_BUS,
            rounds=[
                # The model supplies the accepted hard exclusion and profile
                # preference explicitly; no message-regex routing is used.
                _route_prepare_round(
                    "tu-bus",
                    {
                        "destination": seed.destination,
                        "exclude_modes": ["BUS"],
                        "preferred_modes": ["SUBWAY"],
                    },
                ),
            ],
            mode=mode, turn_id="t1", prepare_leg=bus_only_leg(seed.destination))
        assert trace.tool_calls[1][1].get("exclude_modes") == ["BUS"], f"{scenario_id} precedence: first hard exclusion wins"
        self._assert_no_card(events, scenario_id)
        self._assert_audit(
            scenario_id=scenario_id, session_id=session_id, mocks=mocks,
            expected_status="no_hard_constraint_match", violations=("excluded_mode",))
        self._assert_accepted_preserved(scenario_id=scenario_id, session=session,
                                        seed=seed)
        slots = (session.get("slots") or {}).get("constraints", {})
        assert slots.get("exclude_modes") == ["BUS"], f"{scenario_id} hard mode exclusion persisted"
        assert "excluded_route_ids" not in slots, f"{scenario_id}"
        # Recorded behavior: "no buses" also biases the profile preference;
        # the contradictory "only buses" clause is live-model backlog.
        assert trip_state_module.get_trip_state(session)["preferences"]["preferred_modes"] == ["SUBWAY"], f"{scenario_id} recorded natural-feedback preference"

    async def test_e3_no_buses_actually_only_buses(self):
        for mode in ("auto",):
            with self.subTest(mode=mode):
                await self._no_buses_actually_only_buses(mode)

    async def test_e3_no_buses_seam_contract(self):
        """E3-B: the deterministic mode-exclusion seam itself (no model)."""

        # Only accepted session constraints reach this seam; the message is
        # intentionally ignored so semantic regex routing cannot return.
        session = {"slots": {"constraints": {"exclude_modes": ["BUS"]}}}
        excluded = tool_input_policy.rider_excluded_modes(
            NO_BUS_ACTUALLY_BUS, session
        )
        assert excluded == {"BUS"}
        assert session["slots"]["constraints"]["exclude_modes"] == ["BUS"]

    async def test_e3_leave_now_but_arrive_yesterday_is_nl_backlog(self):
        """E3-B: no deterministic server meaning; no route surface, no card."""

        scenario_id = "E3B-LEAVE-ARRIVE-YESTERDAY-auto"
        session_id, session = self._new_session("auto")
        await self._no_context_noop_turn(
            session=session, session_id=session_id,
            message=LEAVE_NOW_ARRIVE_YESTERDAY, mode="auto", turn_id="t1",
            scenario_id=scenario_id,
            offered=TRANSIT_QUESTION_TOOL_PROFILE)

    async def test_e3_arrive_8_leave_9_deterministic_rejection(self):
        """E3-B: both times is a deterministic server rejection."""

        probe = await self._executor_probe({
            "destination": "Work",
            "departure_time": "2026-08-06T09:00:00-04:00",
            "arrival_by": "2026-08-06T08:00:00-04:00",
        })
        self._assert_probe_rejected(probe, BOTH_TIMES_MARKER, "E3B-BOTH-TIMES")

    async def _dont_change_make_this_route(self, mode: str):
        scenario_id = f"E3B-MAKE-THIS-{mode}"
        session, session_id, seed = self._seed_accepted(mode)
        await self._no_context_noop_turn(
            session=session, session_id=session_id, message=DONT_CHANGE_MAKE_THIS,
            mode=mode, turn_id="t1", scenario_id=scenario_id, pristine=False)
        self._assert_accepted_preserved(scenario_id=scenario_id, session=session,
                                        seed=seed)

    async def test_e3_dont_change_make_this_route(self):
        for mode in ("auto",):
            with self.subTest(mode=mode):
                await self._dont_change_make_this_route(mode)

    async def test_e3_hard_accessibility_incompatible(self):
        for mode in ("auto",):
            with self.subTest(mode=mode):
                await self._hard_accessibility_scenario(
                    mode=mode, scenario_id=f"E3B-ACCESS-{mode}")

    async def test_e3_zero_walking_hard_constraint_auto(self):
        scenario_id = "E3B-ZERO-WALK-auto"
        session, session_id, seed = self._seed_accepted("auto")
        events, _trace, mocks = await self._scripted_turn(
            session=session, session_id=session_id, message=NO_WALKING,
            rounds=[
                _route_prepare_round(
                    "tu-walk",
                    {
                        "destination": seed.destination,
                        "walking_tolerance_minutes": 0,
                    },
                ),
            ],
            mode="auto", turn_id="t1",
            prepare_leg=make_leg(destination=seed.destination))
        self._assert_no_card(events, scenario_id)
        audit = self._assert_audit(
            scenario_id=scenario_id, session_id=session_id, mocks=mocks,
            expected_status="no_hard_constraint_match",
            violations=("walking_tolerance",))
        assert audit["tool_input"]["walking_tolerance_minutes"] == 0, f"{scenario_id} zero-walk input preserved"
        self._assert_accepted_preserved(scenario_id=scenario_id, session=session,
                                        seed=seed)


class TemporalServerBoundaryTests(_MigratedE3Base):
    """E3-C: NL interpretation vs deterministic server validation."""

    @classmethod
    def setUpClass(cls):
        cls.loop = load_agent_loop()

    async def test_e3_temporal_only_turn_no_replan_auto(self):
        """E3-C: a bare temporal phrase never silently replans a trip."""

        scenario_id = "E3C-TEMP-ONLY-auto"
        session, session_id, seed = self._seed_accepted("auto")
        await self._no_context_noop_turn(
            session=session, session_id=session_id, message=PLUS_FIVE_PHRASE,
            mode="auto", turn_id="t1", scenario_id=scenario_id, pristine=False,
            offered=TRANSIT_QUESTION_TOOL_PROFILE)
        self._assert_accepted_preserved(scenario_id=scenario_id, session=session,
                                        seed=seed)

    async def test_e3_iso_departures(self):
        """E3-C: leave now, +5/+30, tonight/tomorrow/midnight (Auto+Quick)."""

        cases = [("E3C-LEAVE-NOW", CONTROL_ROUTE_MESSAGE, None, "auto")]
        cases += [(f"E3C-ISO-{key}", message, iso, "auto")
                  for key, (message, iso) in TEMPORAL_ROUTE_MESSAGES.items()]
        cases += [("E3C-ISO-PLUS30-QUICK",
                   TEMPORAL_ROUTE_MESSAGES["plus30"][0], DEPART_PLUS_30, "quick")]
        for scenario_id, message, iso, mode in cases:
            with self.subTest(scenario_id=scenario_id):
                await self._iso_departure_route_turn(
                    mode=mode, scenario_id=scenario_id, message=message,
                    departure_iso=iso, fixed_candidate_id=FIXED_CANDIDATE_ID)

    async def test_e3_what_if_temporal_remains_temporary_auto(self):
        """E3-C: temporary what-if never overwrites the accepted trip."""

        scenario_id = "E3C-WHATIF-TEMP-auto"
        session, session_id, seed = self._seed_accepted("auto")
        rounds = [
            _route_prepare_round(
                "tu-preview",
                {
                    "destination": seed.destination,
                    "departure_time": DEPART_PLUS_30,
                    "what_if": True,
                },
            ),
            _turn_round("present_route", "tu-preview-pres",
                        {
                            "goal_key": "route",
                            "candidate_id": FIXED_CANDIDATE_ID,
                        }),
        ]
        events, trace, mocks = await self._scripted_turn(
            session=session, session_id=session_id, message=WHAT_IF_PLUS_30,
            rounds=rounds, mode="auto", turn_id="t1",
            prepare_leg=make_leg(destination=seed.destination),
            fixed_candidate_id=FIXED_CANDIDATE_ID)
        assert trace.tool_calls[1][1].get("what_if") is True, f"{scenario_id} server-enforced what-if isolation"
        cards = route_cards(events)
        assert [(len(cards), cards[0].role if cards else None)] == [(1, "recommended")], f"{scenario_id} preview card"
        assert cards[0].depart_iso == DEPART_PLUS_30, f"{scenario_id} preview card carries the what-if time"
        state = trip_state_module.get_trip_state(session)
        assert state["temporary_candidate_set_id"] == mocks["stored_candidate_set_ids"][-1], f"{scenario_id} temporary set bound"
        assert state["temporary_selected_candidate_id"] == FIXED_CANDIDATE_ID, f"{scenario_id} temporary selection"
        self._assert_accepted_preserved(scenario_id=scenario_id, session=session,
                                        seed=seed)
        assert [card["card_id"] for card in session["route_cards"]] == [seed.card_id], f"{scenario_id} preview card is not persisted to the session"
        events2, trace2, _mocks2 = await self._cancel_scenario_turn(
            session=session, session_id=session_id, message="Never mind.",
            mode="auto", turn_id="t2")
        state2 = trip_state_module.get_trip_state(session)
        assert state2["temporary_candidate_set_id"] is None, f"{scenario_id} temporary scenario discarded"
        assert state2["temporary_selected_candidate_id"] is None, f"{scenario_id} temporary selection discarded"
        self._assert_accepted_preserved(scenario_id=f"{scenario_id}-reject",
                                        session=session, seed=seed)
        self._assert_no_card(events2, f"{scenario_id}-reject")
        assert self._names(trace2) == ["declare_goals", "complete_turn"], f"{scenario_id}-reject clarification does not auto-commit"

    async def test_e3_invalid_times_no_candidate_or_card_auto(self):
        """E3-C: rejected invalid times never create candidate/card state."""

        probes = {
            "malformed": ({"destination": "Work", "departure_time": "12:00"},
                          RFC3339_MARKER),
            "naive-no-offset": ({"destination": "Work",
                                 "departure_time": "2026-08-06T12:00:00"},
                                TZ_OFFSET_MARKER),
            "both-times": ({"destination": "Work",
                            "departure_time": "2026-08-06T09:00:00-04:00",
                            "arrival_by": "2026-08-06T08:00:00-04:00"},
                           BOTH_TIMES_MARKER),
            "missing-destination": ({"origin": "Home"}, DEST_REQUIRED_MARKER),
        }
        for key, (tool_input, marker) in probes.items():
            with self.subTest(key=key):
                probe = await self._executor_probe(tool_input)
                self._assert_probe_rejected(probe, marker, f"E3C-INVALID-{key}")

    async def test_e3_past_departure_recorded_contract_auto(self):
        """E3-C recorded contract: format validated, pastness is provider."""

        probe = await self._executor_probe(
            {"destination": "Work", "departure_time": DEPART_PAST})
        self._assert_probe_presentable(probe, "E3C-PAST-DEPART")
        assert probe.result.data["candidates"][0]["candidate_id"] is not None, "E3C-PAST-DEPART candidate exists"

    async def test_e3_arrive_by_derive_failure_bounded_auto(self):
        """E3-C: an impossible arrival target fails bounded with no state."""

        probe = await self._executor_probe(
            {"destination": "Work", "arrival_by": ARRIVAL_PAST},
            derive_error=GoogleRoutesError(
                "no_route", "no route available to estimate arrive-by departure"))
        self._assert_probe_rejected(probe, DERIVE_FAILED_MARKER,
                                    "E3C-ARRIVE-PAST")

    async def test_e3_valid_control_planning_works_auto(self):
        """E3-C control: ordinary valid planning still prepares a presentable set."""

        probe = await self._executor_probe({"destination": "Work"})
        self._assert_probe_presentable(probe, "E3C-CONTROL-VALID")


__all__ = ()
