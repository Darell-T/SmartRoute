from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from app.services.agent import intelligence, policy, session
from app.services.agent.tools import _location


class AgentModePolicyTests(unittest.TestCase):
    def test_auto_and_quick_use_configured_models_and_distinct_budgets(self):
        with patch.dict(
            os.environ,
            {
                "AGENT_AUTO_MODEL": "supported-sonnet",
                "AGENT_QUICK_MODEL": "supported-haiku",
                "AGENT_AUTO_MAX_ROUTE_CANDIDATES": "5",
                "AGENT_QUICK_MAX_ROUTE_CANDIDATES": "2",
                "AGENT_AUTO_RETRY_COUNT": "2",
                "AGENT_QUICK_RETRY_COUNT": "1",
                "AGENT_AUTO_MAX_OUTPUT_TOKENS": "900",
                "AGENT_QUICK_MAX_OUTPUT_TOKENS": "300",
            },
            clear=False,
        ):
            automatic = policy.policy_for_mode("auto")
            quick = policy.policy_for_mode("quick")

        self.assertEqual(automatic.model, "supported-sonnet")
        self.assertEqual(quick.model, "supported-haiku")
        self.assertGreater(automatic.max_route_candidates, quick.max_route_candidates)
        self.assertGreater(automatic.max_output_tokens, quick.max_output_tokens)
        self.assertGreater(automatic.retry_count, quick.retry_count)

    def test_unknown_mode_falls_back_to_auto(self):
        self.assertEqual(policy.policy_for_mode("turbo").mode, "auto")
        self.assertEqual(policy.policy_for_mode(None).mode, "auto")

    def test_model_label_is_safe_for_structured_logs(self):
        self.assertEqual(policy.safe_model_label("sonnet\nmessage=secret"), "sonnetmessagesecret")


class IntentAndContinuityTests(unittest.TestCase):
    def test_simple_arithmetic_is_deterministic(self):
        self.assertEqual(intelligence.evaluate_simple_arithmetic("What is 5 + 5?"), "10.")
        self.assertIsNone(intelligence.evaluate_simple_arithmetic("import os"))

    def test_crowd_avoidance_requires_event_aware_route_planning(self):
        parsed = intelligence.parse_intent("Get me to 34th Street and avoid busy stations")
        self.assertEqual(parsed.intent, "route_planning")
        self.assertTrue(parsed.avoid_crowds)
        self.assertTrue(parsed.required_evidence.routes)
        self.assertTrue(parsed.required_evidence.events)
        self.assertEqual(parsed.required_evidence.required_tools(), ("plan_trip",))

    def test_arrival_lookup_is_required_in_both_modes(self):
        parsed = intelligence.parse_intent("When is the next Q at Newkirk Avenue?")
        self.assertEqual(parsed.intent, "arrival_lookup")
        self.assertEqual(parsed.arrival_route_id, "Q")
        self.assertEqual(parsed.arrival_stop_query, "Newkirk Avenue")
        self.assertEqual(parsed.required_evidence.required_tools(), ("lookup_arrivals",))

    def test_destination_discovery_requires_place_evidence(self):
        parsed = intelligence.parse_intent("Find me a good pizza place in Brooklyn")
        self.assertEqual(parsed.intent, "destination_discovery")
        self.assertTrue(parsed.required_evidence.places)

    def test_failed_trip_resume_offer_is_emitted_once(self):
        _session_id, state = session.new_session()
        session.mark_pending_trip_failed(state, {"destination": "JFK"}, "timeout")
        self.assertEqual(session.consume_resume_offer(state), "Do you want me to retry the trip to JFK?")
        self.assertIsNone(session.consume_resume_offer(state))
        self.assertEqual(state["pending_trip"]["status"], "awaiting_confirmation")

    def test_new_trip_clears_stale_constraints_and_pending_state(self):
        _session_id, state = session.new_session()
        state["slots"] = {
            "destination": "JFK",
            "constraints": {"exclude_modes": ["BUS"]},
        }
        session.mark_pending_trip_failed(state, {"destination": "JFK"}, "timeout")
        session.reset_for_new_trip(state)
        self.assertNotIn("destination", state["slots"])
        self.assertNotIn("constraints", state["slots"])
        self.assertEqual(state["pending_trip"]["status"], "none")


class KnownPlaceAliasTests(unittest.TestCase):
    def test_major_destination_aliases_resolve_without_geocoding(self):
        cases = {
            "JFK": "John F. Kennedy International Airport",
            "LaGuardia": "LaGuardia Airport",
            "EWR": "Newark Liberty International Airport",
            "Penn Station": "Penn Station",
            "Barclays Center": "Barclays Center",
            "MSG": "Madison Square Garden",
        }
        for query, expected_name in cases.items():
            with self.subTest(query=query):
                resolved = _location.known_place(query)
                self.assertIsNotNone(resolved)
                self.assertEqual(resolved.name, expected_name)
                self.assertIsInstance(resolved.latitude, float)
                self.assertIsInstance(resolved.longitude, float)


if __name__ == "__main__":
    unittest.main()
