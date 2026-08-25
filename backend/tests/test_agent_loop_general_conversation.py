from __future__ import annotations

import json
from copy import deepcopy

from app.services.agent import candidate_store
from app.services.agent import trip_state as trip_state_module
from tests.agent_loop_reliability_support import AgentLoopReliabilityTestCase
from tests.test_agent_loop import (
    _declared_general_round,
    _model_led_registry,
)


class AgentLoopGeneralConversationTests(AgentLoopReliabilityTestCase):
    async def test_general_conversation_uses_model_declared_complete_turn(self):
        cases = {
            "hello": "Hi — I can plan NYC subway and bus trips, check arrivals, and explain service changes.",
            "thanks": "You’re welcome.",
            "help": "Tell me where you’re starting and going, or ask about a train or bus arrival.",
            "tell me a joke": "SmartRoute is for NYC transit help. I can plan a subway or bus trip, compare routes, or check arrivals.",
        }
        for message, expected in cases.items():
            with self.subTest(message=message):
                trace = self.loop.TurnTrace()
                events_out, _session = await self._run(
                    [_declared_general_round(expected)],
                    message=message,
                    trace=trace,
                )
                self.assertEqual("".join(e.text for e in events_out if e.type == "token"), expected)
                self.assertEqual(trace.model_call_count, 1)
                self.assertEqual(trace.tool_call_count, 2)
                self.assertEqual(len(self.loop.client.messages.calls), 1)

    async def test_general_paraphrases_stay_model_led(self):
        for message in ("good morning", "thank you so much", "what can you do", "write me a poem"):
            with self.subTest(message=message):
                trace = self.loop.TurnTrace()
                events_out, _session = await self._run(
                    [_declared_general_round("I can help with an NYC trip or transit question.")],
                    message=message,
                    trace=trace,
                )
                self.assertTrue(any(event.type == "token" for event in events_out))
                self.assertEqual(trace.model_call_count, 1)
                self.assertEqual(trace.tool_call_count, 2)

    def _accepted_route_session(self, *, include_q: bool) -> tuple[dict, str]:
        candidates = [
            {
                "candidate_id": "cd_b",
                "digest": {
                    "destination_name": "Madison Square Garden",
                    "transit_lines": ["B"],
                    "duration_minutes": 35,
                    "walking_minutes": 4,
                    "transfers": 0,
                    "official_service_impacts": [],
                    "confirmed_incident_impacts": [],
                    "unconfirmed_material_claims": [],
                    "event_or_crowd_impacts": [],
                },
            },
            {
                "candidate_id": "cd_q" if include_q else "cd_f",
                "digest": {
                    "destination_name": "Madison Square Garden",
                    "transit_lines": ["Q"] if include_q else ["F"],
                    "duration_minutes": 34 if include_q else 38,
                    "walking_minutes": 12 if include_q else 6,
                    "transfers": 1,
                    "official_service_impacts": [],
                    "confirmed_incident_impacts": [],
                    "unconfirmed_material_claims": [],
                    "event_or_crowd_impacts": [],
                },
            },
        ]
        session = {"active_trip": {"card_id": "rc_winner"}}
        set_id = candidate_store.store_candidate_set(
            session_id="sess-why-q",
            payload={"candidates": candidates},
        )
        trip_state_module.bind_candidate_set(session, set_id)
        trip_state_module.bind_selected_candidate(session, "cd_b")
        active_trip = {
            "card_id": "rc_winner",
            "destination": "Madison Square Garden",
            "canonical_itinerary": {
                "total_duration_seconds": 2100,
                "transfers": 0,
                "walking_seconds": 240,
                "route_lines": ["B"],
            },
            "selection_decision": {
                "reason_code": "fewer_transfers",
                "selection_source": "model",
            },
        }
        session["active_trip"] = deepcopy(active_trip)
        session["route_cards"] = [deepcopy(active_trip)]
        return session, set_id

    async def _run_route_explanation(
        self,
        session: dict,
        *,
        message: str,
        model_message: str,
    ) -> tuple[list, object]:
        trace = self.loop.TurnTrace()
        events_out, returned_session = await self._run(
            [_declared_general_round(model_message)],
            message=message,
            session=session,
            session_id="sess-why-q",
            trace=trace,
            tool_registry=_model_led_registry(),
        )
        self.assertEqual(
            [name for name, _tool_input in trace.tool_calls],
            ["declare_goals", "complete_turn"],
        )
        self.assertEqual(trace.tool_call_count, 2)
        return events_out, returned_session

    def _assert_comparison_context(self, *, include_q: bool) -> str:
        context = str(self.loop.client.messages.calls[0]["messages"][-1]["content"])
        line = next(
            line
            for line in context.splitlines()
            if line.startswith("accepted_route_comparison:")
        )
        comparison = json.loads(line.split(": ", 1)[1])
        self.assertEqual(
            [option["lines"] for option in comparison["options"]],
            [["B"], ["Q"] if include_q else ["F"]],
        )
        self.assertNotIn("cd_b", line)
        self.assertNotIn("cd_q", line)
        self.assertNotIn('"score"', line)
        return context

    async def test_why_not_q_without_prepared_q_keeps_route_unchanged(self):
        session, set_id = self._accepted_route_session(include_q=False)
        before_record = deepcopy(
            candidate_store.load_candidate_set(set_id, session_id="sess-why-q")
        )
        before_state = deepcopy(trip_state_module.get_trip_state(session))
        before_cards = deepcopy(session["route_cards"])
        before_trip = deepcopy(session["active_trip"])
        events_out, _ = await self._run_route_explanation(
            session,
            message="Why not the Q?",
            model_message=(
                "The Q was not among the prepared alternatives, so I kept the "
                "accepted route."
            ),
        )
        context = self._assert_comparison_context(include_q=False)
        visible = "".join(event.text for event in events_out if event.type == "token")
        self.assertIn("not among the prepared alternatives", visible)
        self.assertIn("accepted route", visible)
        self.assertIn("accepted_route_comparison", context)
        self.assertEqual(
            before_record,
            candidate_store.load_candidate_set(set_id, session_id="sess-why-q"),
        )
        self.assertEqual(before_state, trip_state_module.get_trip_state(session))
        self.assertEqual(before_cards, session["route_cards"])
        self.assertEqual(before_trip, session["active_trip"])

    async def test_why_not_q_compares_prepared_alternative_without_replanning(self):
        session, set_id = self._accepted_route_session(include_q=True)
        before_record = deepcopy(
            candidate_store.load_candidate_set(set_id, session_id="sess-why-q")
        )
        before_state = deepcopy(trip_state_module.get_trip_state(session))
        before_cards = deepcopy(session["route_cards"])
        before_trip = deepcopy(session["active_trip"])
        events_out, _ = await self._run_route_explanation(
            session,
            message="Why not the Q?",
            model_message=(
                "The accepted B route involved less walking than the Q alternative."
            ),
        )
        context = self._assert_comparison_context(include_q=True)
        visible = "".join(event.text for event in events_out if event.type == "token")
        self.assertIn("B route", visible)
        self.assertIn("Q alternative", visible)
        self.assertIn("walking", visible)
        self.assertIn("accepted_route_comparison", context)
        self.assertEqual(
            before_record,
            candidate_store.load_candidate_set(set_id, session_id="sess-why-q"),
        )
        self.assertEqual(before_state, trip_state_module.get_trip_state(session))
        self.assertEqual(before_cards, session["route_cards"])
        self.assertEqual(before_trip, session["active_trip"])
