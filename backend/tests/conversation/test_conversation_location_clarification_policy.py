"""Canonical-loop coverage for route endpoint clarification precedence."""

from __future__ import annotations

import unittest
from unittest.mock import patch

from app.services.agent import profile
from app.services.agent import trip_state
from tests.conversation.conversation_matrix_harness import (
    _turn_round,
    clear_caches,
    load_agent_loop,
    make_leg,
    new_session,
    policy_model,
    route_cards,
    run_turn,
    seed_accepted_active_trip,
)


CURRENT_LOCATION = {"lat": 40.7411, "lng": -73.9897}

INITIAL_TOOL_PROFILE = frozenset(
    {
        "declare_goals",
        "discover_places",
        "check_transit",
        "prepare_route_options",
        "complete_turn",
    }
)


def _declared_route_round(tool_id: str, tool_input: dict) -> dict:
    """Script one route capability with its model-declared route goal."""

    payload = {"goal_key": "route", **tool_input}
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
                "id": f"{tool_id}-goals",
                "name": "declare_goals",
                "input": {
                    "goals": [
                        {
                            "goal_key": "route",
                            "kind": "route",
                            "depends_on": [],
                        }
                    ]
                },
            },
            {
                "id": tool_id,
                "name": "prepare_route_options",
                "input": payload,
            },
        ],
        "stop_reason": "tool_use",
    }


def _present_route_round(tool_id: str, candidate_id: str, **extra: object) -> dict:
    return _turn_round(
        "present_route",
        tool_id,
        {"goal_key": "route", "candidate_id": candidate_id, **extra},
    )


def _complete_route_round(tool_id: str, message: str, *, outcome: str) -> dict:
    return _turn_round(
        "complete_turn",
        tool_id,
        {
            "goal_keys": ["route"],
            "outcome": outcome,
            "message": message,
        },
    )


def _declared_route_clarification_round(tool_id: str, message: str) -> dict:
    """Clarify a route goal before attempting provider work when state is missing."""

    return {
        "tool_use": [
            {
                "id": f"{tool_id}-goals",
                "name": "declare_goals",
                "input": {
                    "goals": [
                        {
                            "goal_key": "route",
                            "kind": "route",
                            "depends_on": [],
                        }
                    ]
                },
            },
            _complete_route_round(tool_id, message, outcome="clarification")[
                "tool_use"
            ][0],
        ],
        "stop_reason": "tool_use",
    }


def _save_home_and_work(session: dict) -> None:
    profile.save_place(
        session,
        {"label": "Home", "latitude": 40.7128, "longitude": -74.0060},
        slot="home",
    )
    profile.save_place(
        session,
        {"label": "Work", "latitude": 40.7527, "longitude": -73.9772},
        slot="work",
    )


class CanonicalContextualReplanLoopTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        cls.loop = load_agent_loop()

    def setUp(self):
        clear_caches()

    async def _run_contextual_replan(self, *, mode: str, message: str) -> None:
        session_id, session = new_session()
        _save_home_and_work(session)
        seed = seed_accepted_active_trip(session, session_id)
        candidate_id = f"cd_location_{mode}"
        mocks: dict = {}
        trace = self.loop.TurnTrace()

        events, trace = await run_turn(
            self.loop,
            session=session,
            session_id=session_id,
            message=message,
            mode=mode,
            origin=dict(CURRENT_LOCATION),
            rounds=[
                _declared_route_round(
                    f"tu-{mode}-prepare",
                    {"required_route_ids": ["Q"]},
                ),
                _present_route_round(
                    f"tu-{mode}-present", candidate_id
                ),
            ],
            prepare_leg=make_leg(route_ids=("Q",), destination="Work"),
            fixed_candidate_id=candidate_id,
            mocks=mocks,
            trace=trace,
        )

        expected_mode, expected_model = policy_model(self.loop, mode)
        self.assertEqual(expected_mode, mode)
        self.assertEqual(
            [call["model"] for call in self.loop.client.messages.calls],
            [expected_model, expected_model],
        )
        first_context = str(
            self.loop.client.messages.calls[0]["messages"][-1]["content"]
        )
        self.assertIn('"origin":"Home"', first_context)
        self.assertIn('"destination":"Work"', first_context)
        self.assertIn(
            'accepted_route_endpoints: {"origin":"Home","destination":"Work",'
            '"source":"accepted_trip","clarification_required":false}',
            first_context,
        )
        self.assertIn("saved_places:", first_context)
        self.assertIn("rider_location: 40.7411,-73.9897", first_context)
        self.assertIn(seed.card_id, first_context)
        self.assertEqual(
            {
                schema["name"]
                for schema in self.loop.client.messages.calls[0]["tools"]
            },
            INITIAL_TOOL_PROFILE,
        )
        self.assertEqual(
            self.loop.client.messages.calls[1].get("tool_choice"),
            {"type": "tool", "name": "present_route"},
        )

        prepared_input = mocks["prepare_single_leg"].await_args.args[0]
        self.assertEqual(prepared_input["origin"], "Home")
        self.assertEqual(prepared_input["destination"], "Work")
        self.assertEqual(prepared_input["required_route_ids"], ["Q"])
        at_prepare = (mocks.get("session_at_store") or [{}])[0]
        self.assertEqual(at_prepare.get("active_trip"), seed.card)
        self.assertEqual(at_prepare.get("route_cards"), [seed.card])

        cards = route_cards(events)
        self.assertEqual(len(cards), 1)
        self.assertEqual(cards[0].summary["lines"], ["Q"])
        self.assertEqual(session["active_trip"]["lines"], ["Q"])
        self.assertEqual(
            trip_state.get_trip_state(session)["destination"], "Work"
        )
        rider_text = "".join(
            event.text for event in events if getattr(event, "type", None) == "token"
        ).casefold()
        self.assertNotIn("address", rider_text)
        self.assertEqual(
            [name for name, _tool_input in trace.tool_calls],
            ["declare_goals", "prepare_route_options", "present_route"],
        )

    async def _run_fresh_saved_route(
        self,
        *,
        mode: str,
        message: str,
        tool_input: dict,
        expected_origin: str,
        expected_destination: str,
    ) -> None:
        session_id, session = new_session()
        _save_home_and_work(session)
        candidate_id = f"cd_saved_{mode}_{expected_destination.casefold()}"
        mocks: dict = {}
        trace = self.loop.TurnTrace()
        events, trace = await run_turn(
            self.loop,
            session=session,
            session_id=session_id,
            message=message,
            mode=mode,
            origin=dict(CURRENT_LOCATION),
            rounds=[
                _declared_route_round(
                    f"tu-{mode}-prepare", tool_input
                ),
                _present_route_round(
                    f"tu-{mode}-present", candidate_id
                ),
            ],
            prepare_leg=make_leg(
                route_ids=("Q",), destination=expected_destination
            ),
            fixed_candidate_id=candidate_id,
            mocks=mocks,
            trace=trace,
        )

        context = str(
            self.loop.client.messages.calls[0]["messages"][-1]["content"]
        )
        self.assertIn("saved_places:", context)
        self.assertIn("rider_location: 40.7411,-73.9897", context)
        prepared_input = mocks["prepare_single_leg"].await_args.args[0]
        self.assertEqual(prepared_input["origin"].casefold(), expected_origin.casefold())
        self.assertEqual(
            prepared_input["destination"].casefold(),
            expected_destination.casefold(),
        )
        self.assertEqual(len(route_cards(events)), 1)
        self.assertEqual(
            [name for name, _tool_input in trace.tool_calls],
            ["declare_goals", "prepare_route_options", "present_route"],
        )
        rider_text = "".join(
            event.text for event in events if getattr(event, "type", None) == "token"
        ).casefold()
        self.assertNotIn("address", rider_text)

    async def test_lm05_contextual_replan_preserves_endpoints_auto_and_quick(self):
        for mode in ("auto", "quick"):
            with self.subTest(mode=mode):
                await self._run_contextual_replan(
                    mode=mode,
                    message="Take me to work but I really need the Q.",
                )

    async def test_neighboring_same_destination_phrases_use_the_same_contract(self):
        messages = (
            "Take me to work but use the Q.",
            "Get me to work using the Q instead.",
            "Same trip, but take the Q.",
            "Actually I need the Q.",
        )
        for mode in ("auto", "quick"):
            for message in messages:
                with self.subTest(mode=mode, message=message):
                    await self._run_contextual_replan(mode=mode, message=message)

    async def test_saved_home_work_and_current_origin_routes_auto_and_quick(self):
        cases = (
            ("Take me to work.", {"destination": "work"}, "user", "Work"),
            ("Take me home.", {"destination": "home"}, "user", "Home"),
            (
                "From home to work.",
                {"origin": "home", "destination": "work"},
                "Home",
                "Work",
            ),
        )
        for mode in ("auto", "quick"):
            for message, tool_input, expected_origin, expected_destination in cases:
                with self.subTest(mode=mode, message=message):
                    await self._run_fresh_saved_route(
                        mode=mode,
                        message=message,
                        tool_input=tool_input,
                        expected_origin=expected_origin,
                        expected_destination=expected_destination,
                    )

    async def test_destination_only_route_uses_current_location_without_address(self):
        for mode in ("auto", "quick"):
            with self.subTest(mode=mode):
                session_id, session = new_session()
                candidate_id = f"cd_barclays_{mode}"
                mocks: dict = {}
                trace = self.loop.TurnTrace()
                events, trace = await run_turn(
                    self.loop,
                    session=session,
                    session_id=session_id,
                    message="Get me to Barclays.",
                    mode=mode,
                    origin=dict(CURRENT_LOCATION),
                    rounds=[
                        _declared_route_round(
                            f"tu-{mode}-prepare",
                            {"destination": "Barclays Center"},
                        ),
                        _present_route_round(
                            f"tu-{mode}-present", candidate_id
                        ),
                    ],
                    prepare_leg=make_leg(route_ids=("Q",), destination="Barclays Center"),
                    fixed_candidate_id=candidate_id,
                    mocks=mocks,
                )
                prepared_input = mocks["prepare_single_leg"].await_args.args[0]
                self.assertEqual(prepared_input["origin"], "user")
                self.assertEqual(prepared_input["destination"], "Barclays Center")
                self.assertEqual(len(route_cards(events)), 1)
                rider_text = "".join(
                    event.text
                    for event in events
                    if getattr(event, "type", None) == "token"
                ).casefold()
                self.assertNotIn("address", rider_text)

    async def test_same_trip_avoid_q_preserves_endpoints_auto_and_quick(self):
        for mode in ("auto", "quick"):
            with self.subTest(mode=mode):
                session_id, session = new_session()
                _save_home_and_work(session)
                seed_accepted_active_trip(session, session_id)
                candidate_id = f"cd_avoid_q_{mode}"
                mocks: dict = {}
                trace = self.loop.TurnTrace()
                events, trace = await run_turn(
                    self.loop,
                    session=session,
                    session_id=session_id,
                    message="Same trip, but avoid the Q.",
                    mode=mode,
                    rounds=[
                        _declared_route_round(
                            f"tu-{mode}-prepare", {"excluded_route_ids": ["Q"]}
                        ),
                        _present_route_round(
                            f"tu-{mode}-present", candidate_id
                        ),
                    ],
                    prepare_leg=make_leg(route_ids=("A",), destination="Work"),
                    fixed_candidate_id=candidate_id,
                    mocks=mocks,
                    trace=trace,
                )
                prepared_input = mocks["prepare_single_leg"].await_args.args[0]
                self.assertEqual(prepared_input["origin"], "Home")
                self.assertEqual(prepared_input["destination"], "Work")
                self.assertEqual(prepared_input["excluded_route_ids"], ["Q"])
                self.assertEqual(route_cards(events)[0].summary["lines"], ["A"])

    async def test_what_if_explicit_saved_origin_overrides_active_and_gps(self):
        for mode in ("auto", "quick"):
            with self.subTest(mode=mode):
                session_id, session = new_session()
                _save_home_and_work(session)
                seed = seed_accepted_active_trip(
                    session, session_id, destination="Barclays Center"
                )
                candidate_id = f"cd_work_origin_{mode}"
                mocks: dict = {}
                trace = self.loop.TurnTrace()
                events, trace = await run_turn(
                    self.loop,
                    session=session,
                    session_id=session_id,
                    message="What if I leave from Work instead?",
                    mode=mode,
                    origin=dict(CURRENT_LOCATION),
                    rounds=[
                        _declared_route_round(
                            f"tu-{mode}-prepare",
                            {"origin": "work", "what_if": True},
                        ),
                        _present_route_round(
                            f"tu-{mode}-present",
                            candidate_id,
                            commit_scenario=False,
                        ),
                    ],
                    prepare_leg=make_leg(
                        route_ids=("Q",), destination="Barclays Center"
                    ),
                    fixed_candidate_id=candidate_id,
                    mocks=mocks,
                    trace=trace,
                )
                prepared_input = mocks["prepare_single_leg"].await_args.args[0]
                self.assertEqual(prepared_input["origin"], "work")
                self.assertEqual(prepared_input["destination"], "Barclays Center")
                self.assertTrue(trace.tool_calls[1][1]["what_if"])
                self.assertEqual(session["active_trip"], seed.card)
                self.assertTrue(route_cards(events))

    async def test_missing_authoritative_endpoint_clarifies_without_provider_work(self):
        cases = (
            (
                "Take me to work.",
                {"destination": "work"},
                dict(CURRENT_LOCATION),
                "What address should I use for Work?",
            ),
            (
                "Get me to Barclays.",
                {"destination": "Barclays Center"},
                {},
                "Share your current location or provide a starting point.",
            ),
        )
        for mode in ("auto", "quick"):
            for message, tool_input, origin, clarification in cases:
                with self.subTest(mode=mode, message=message):
                    session_id, session = new_session()
                    trace = self.loop.TurnTrace()
                    with patch(
                        "app.services.agent.tools.route.preparation_adapter.directions_service.get_transit_route",
                        side_effect=AssertionError("route provider must not run"),
                    ) as route_provider:
                        events, trace = await run_turn(
                            self.loop,
                            session=session,
                            session_id=session_id,
                            message=message,
                            mode=mode,
                            origin=origin,
                            rounds=[
                                _declared_route_clarification_round(
                                    f"tu-{mode}-clarify", clarification
                                ),
                            ],
                            trace=trace,
                        )
                    route_provider.assert_not_called()
                    self.assertEqual(route_cards(events), [])
                    self.assertEqual(
                        [name for name, _tool_input in trace.tool_calls],
                        ["declare_goals", "complete_turn"],
                    )
                    final_text = "".join(
                        event.text
                        for event in events
                        if getattr(event, "type", None) == "token"
                    )
                    self.assertIn(clarification, final_text)


if __name__ == "__main__":
    unittest.main()
