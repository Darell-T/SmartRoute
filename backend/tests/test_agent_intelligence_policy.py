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

    def test_sonnet_five_request_capabilities_are_centralized(self):
        capabilities = policy.request_capabilities("claude-sonnet-5")
        self.assertFalse(capabilities.supports_manual_thinking)
        self.assertFalse(capabilities.supports_non_default_sampling)
        self.assertFalse(capabilities.supports_assistant_prefill)

    def test_private_model_keeps_legacy_request_capabilities(self):
        capabilities = policy.request_capabilities("private-claude-endpoint")
        self.assertTrue(capabilities.supports_manual_thinking)
        self.assertTrue(capabilities.supports_non_default_sampling)
        self.assertTrue(capabilities.supports_assistant_prefill)

    def test_enabled_agent_requires_server_side_credential(self):
        with patch.dict(
            os.environ,
            {"AGENT_ENABLED": "1", "ANTHROPIC_API_KEY": ""},
            clear=True,
        ):
            with self.assertRaisesRegex(RuntimeError, "ANTHROPIC_API_KEY"):
                policy.validate_agent_configuration()

    def test_disabled_agent_does_not_require_credential(self):
        with patch.dict(os.environ, {"AGENT_ENABLED": "0"}, clear=True):
            policy.validate_agent_configuration()

    def test_public_anthropic_credential_is_rejected(self):
        with patch.dict(
            os.environ,
            {
                "AGENT_ENABLED": "1",
                "ANTHROPIC_API_KEY": "server-test-key",
                "NEXT_PUBLIC_ANTHROPIC_API_KEY": "unsafe-public-key",
            },
            clear=True,
        ):
            with self.assertRaisesRegex(RuntimeError, "server-only"):
                policy.validate_agent_configuration()


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

    def test_civic_and_street_crowd_requests_trigger_event_research(self):
        for phrase in (
            "Plan a trip to City Hall and avoid protests",
            "Get me to Fifth Avenue and avoid parades",
            "Head to Columbus Circle and check street conditions",
        ):
            with self.subTest(phrase=phrase):
                parsed = intelligence.parse_intent(phrase)
                self.assertEqual(parsed.intent, "route_planning")
                self.assertTrue(parsed.avoid_crowds)

    def test_explicit_route_request_becomes_a_hard_planning_constraint(self):
        parsed = intelligence.parse_intent("Plan a Q route to Coney Island")

        self.assertEqual(parsed.intent, "route_planning")
        self.assertEqual(parsed.requested_route_ids, ("Q",))
        self.assertEqual(
            intelligence.parse_intent("Plan a trip to Coney Island").requested_route_ids,
            (),
        )

    def test_arrival_lookup_is_required_in_both_modes(self):
        parsed = intelligence.parse_intent("When is the next Q at Newkirk Avenue?")
        self.assertEqual(parsed.intent, "arrival_lookup")
        self.assertEqual(parsed.arrival_route_id, "Q")
        self.assertEqual(parsed.arrival_stop_query, "Newkirk Avenue")
        self.assertEqual(parsed.required_evidence.required_tools(), ("lookup_arrivals",))

    def test_implicit_next_arrival_followup_is_deterministic(self):
        parsed = intelligence.parse_intent("when is the next arrival")

        self.assertEqual(parsed.intent, "arrival_lookup")
        self.assertIsNone(parsed.arrival_route_id)

    def test_arrival_paraphrase_matrix_produces_structured_intent(self):
        phrases = (
            "next arrivals",
            "show me the next arrivals",
            "show arrivals",
            "what's coming next",
            "when is my train",
            "next Q",
            "any Q trains coming",
            "how long until the Q",
            "when's the next bus",
            "are there any M15s nearby",
            "will I make the next F",
        )

        for phrase in phrases:
            with self.subTest(phrase=phrase):
                parsed = intelligence.parse_intent(phrase)
                self.assertEqual(parsed.intent, "arrival_lookup", phrase)
                if phrase == "when's the next bus":
                    self.assertIsNone(parsed.arrival.route_id)
                self.assertTrue(parsed.arrival.requested)
                self.assertTrue(parsed.arrival.include_multiple_arrivals)
                self.assertGreaterEqual(parsed.arrival.confidence, 0.9)

    def test_arrival_intent_preserves_explicit_stop_direction_and_catchability(self):
        parsed = intelligence.parse_intent(
            "Will I make the next downtown Q at Newkirk Plaza?"
        )

        self.assertEqual(parsed.arrival.route_id, "Q")
        self.assertEqual(parsed.arrival.stop_query, "Newkirk Plaza")
        self.assertEqual(parsed.arrival.direction_query, "downtown")
        self.assertTrue(parsed.arrival.catchability_requested)
        self.assertFalse(parsed.arrival.use_active_trip)

    def test_destination_eta_question_is_not_a_vehicle_arrival_lookup(self):
        parsed = intelligence.parse_intent("when will I arrive?")

        self.assertEqual(parsed.intent, "transit_question")

    def test_destination_discovery_requires_place_evidence(self):
        parsed = intelligence.parse_intent("Find me a good pizza place in Brooklyn")
        self.assertEqual(parsed.intent, "destination_discovery")
        self.assertTrue(parsed.required_evidence.places)

    def test_conversational_pancake_request_uses_destination_discovery(self):
        parsed = intelligence.parse_intent(
            "In the mood for pancakes this Sunday morning, any suggestions on where to go?"
        )

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
