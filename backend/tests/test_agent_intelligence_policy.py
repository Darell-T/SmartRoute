from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from app.services.agent import intelligence, policy, session
from app.services.agent.tools import _location
from app.services.agent.tools._types import ToolContext


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

    def test_startup_validation_rejects_fixture_replay_outside_local_runtime(self):
        # The centralized runtime guard is wired through policy startup
        # validation, so an accidental fixture environment cannot silently
        # replay tool results in production.
        with patch.dict(
            os.environ,
            {
                "SMARTROUTE_ENV": "production",
                "AGENT_TOOL_FIXTURES": "/tmp/fixtures",
                "AGENT_ENABLED": "1",
                "ANTHROPIC_API_KEY": "server-test-key",
            },
            clear=True,
        ):
            with self.assertRaisesRegex(RuntimeError, "Agent tool fixture replay requires"):
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
        self.assertEqual(
            parsed.required_evidence.required_tools(),
            ("prepare_route_options", "present_route"),
        )

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

    def test_direct_area_conditions_get_the_bounded_area_intent(self):
        parsed = intelligence.parse_intent("Is there police activity near Barclays Center?")

        self.assertEqual(parsed.intent, "area_conditions")

    def test_route_request_with_incident_language_remains_route_planning(self):
        for message in (
            "Get me to Barclays Center while avoiding police activity and closures",
            "Plan a trip to Barclays Center while avoiding crowds and protests",
        ):
            with self.subTest(message=message):
                parsed = intelligence.parse_intent(message)
                self.assertEqual(parsed.intent, "route_planning")

    def test_explicit_route_request_becomes_a_hard_planning_constraint(self):
        parsed = intelligence.parse_intent("Plan a Q route to Coney Island")

        self.assertEqual(parsed.intent, "route_planning")
        self.assertEqual(parsed.requested_route_ids, ("Q",))
        self.assertEqual(
            intelligence.parse_intent("Plan a trip to Coney Island").requested_route_ids,
            (),
        )

    def test_explicit_negative_route_request_becomes_a_hard_exclusion(self):
        cases = (
            ("What if I avoid the Q?", ("Q",)),
            ("Get me home without the Q train", ("Q",)),
            ("No Q train for this trip", ("Q",)),
            ("Get me to Coney Island, no Q train", ("Q",)),
        )
        for message, excluded in cases:
            with self.subTest(message=message):
                parsed = intelligence.parse_intent(message)
                self.assertEqual(parsed.intent, "route_planning")
                self.assertEqual(parsed.excluded_route_ids, excluded)
                self.assertEqual(parsed.requested_route_ids, ())

    def test_negative_route_request_is_never_misclassified_as_required(self):
        parsed = intelligence.parse_intent("No Q train, take the A")
        self.assertEqual(parsed.requested_route_ids, ("A",))
        self.assertEqual(parsed.excluded_route_ids, ("Q",))
        self.assertNotIn("Q", parsed.requested_route_ids)

    def test_negative_route_phrases_never_turn_words_or_articles_into_route_ids(self):
        for message in (
            "avoid a train",
            "avoid buses",
            "no buses please",
            "avoid the area",
            "no worries",
            "no problem",
        ):
            with self.subTest(message=message):
                parsed = intelligence.parse_intent(message)
                self.assertEqual(parsed.excluded_route_ids, ())
                self.assertNotIn("A", parsed.excluded_route_ids)

    def test_affirmative_and_negative_route_requests_coexist_without_confusion(self):
        parsed = intelligence.parse_intent("Take the Q but avoid the R")
        self.assertEqual(parsed.requested_route_ids, ("Q",))
        self.assertEqual(parsed.excluded_route_ids, ("R",))
        self.assertEqual(
            intelligence.parse_intent("Plan a Q route to Coney Island").excluded_route_ids,
            (),
        )

    def test_route_id_normalization_is_bounded_uppercase_and_deterministic(self):
        self.assertEqual(
            intelligence.normalize_route_ids(
                [" q ", "Q", "q", "B35", "", "q train", "M15", "A" * 20, 12, None]
            ),
            ("Q", "B35", "M15", "12"),
        )
        self.assertEqual(intelligence.normalize_route_id("q"), "Q")
        self.assertIsNone(intelligence.normalize_route_id("q train"))
        self.assertIsNone(intelligence.normalize_route_id(""))
        self.assertIsNone(intelligence.normalize_route_id(None))

    def test_route_id_normalization_accepts_real_provider_bus_forms(self):
        self.assertEqual(intelligence.normalize_route_id("M15-SBS"), "M15-SBS")
        self.assertEqual(intelligence.normalize_route_id("m15-sbs"), "M15-SBS")
        self.assertEqual(intelligence.normalize_route_id("M15+"), "M15+")
        self.assertEqual(intelligence.normalize_route_id("m15+"), "M15+")
        self.assertEqual(intelligence.normalize_route_id("BX12"), "BX12")
        self.assertEqual(intelligence.normalize_route_id("BM1"), "BM1")
        self.assertEqual(intelligence.normalize_route_id("QM5"), "QM5")
        self.assertEqual(intelligence.normalize_route_id("SIM1"), "SIM1")
        self.assertEqual(intelligence.normalize_route_id("Q44-SBS"), "Q44-SBS")

    def test_route_id_normalization_rejects_internal_punctuation_and_free_text(self):
        for junk in (
            "M15 SBS",
            "M15-SB",
            "M15-SBS+",
            "M15-SBX",
            "M15*",
            "BX-12",
            "q train",
            "the Q",
            "M15-SBS local",
        ):
            with self.subTest(value=junk):
                self.assertIsNone(intelligence.normalize_route_id(junk))

    def test_route_id_normalization_truncates_over_limit_collections(self):
        over_limit = intelligence.normalize_route_ids(
            [f"R{index}" for index in range(30)]
        )
        self.assertEqual(
            len(over_limit),
            intelligence.MAX_NORMALIZED_ROUTE_IDS,
        )
        self.assertEqual(over_limit[0], "R0")
        self.assertEqual(over_limit[-1], f"R{intelligence.MAX_NORMALIZED_ROUTE_IDS - 1}")

    def test_route_id_normalization_rejects_scalar_and_mapping_junk(self):
        self.assertEqual(intelligence.normalize_route_ids("M15"), ())
        self.assertEqual(intelligence.normalize_route_ids(b"M15"), ())
        self.assertEqual(intelligence.normalize_route_ids({"M15": "Q44"}), ())
        self.assertEqual(intelligence.normalize_route_ids(12), ())
        self.assertEqual(intelligence.normalize_route_ids(None), ())
        # Real internal collection shapes are accepted deliberately.
        self.assertEqual(
            intelligence.normalize_route_ids(("M15", "Q44", "M15")),
            ("M15", "Q44"),
        )
        self.assertEqual(
            set(intelligence.normalize_route_ids({"q", "b35"})),
            {"Q", "B35"},
        )

    def test_route_id_normalization_sorts_set_like_inputs_deterministically(self):
        # Sets have no intrinsic iteration order, so set/frozenset inputs
        # must be sorted by a stable textual key before normalization: the
        # ordered tuple is identical no matter how the set was constructed.
        set_input = intelligence.normalize_route_ids({"q", "b35", "m15-sbs", "12"})
        frozenset_input = intelligence.normalize_route_ids(
            frozenset({"m15-sbs", "12", "q", "b35"})
        )
        self.assertEqual(set_input, frozenset_input)
        self.assertEqual(set_input, ("12", "B35", "M15-SBS", "Q"))
        # The 16-unique bound still applies after sorting.
        oversized = intelligence.normalize_route_ids(
            {f"R{index:02d}" for index in range(30)}
        )
        self.assertEqual(len(oversized), intelligence.MAX_NORMALIZED_ROUTE_IDS)
        self.assertEqual(oversized, tuple(f"R{index:02d}" for index in range(16)))
        # List/tuple inputs keep caller order untouched.
        self.assertEqual(
            intelligence.normalize_route_ids(["q", "b35", "q"]),
            ("Q", "B35"),
        )
        self.assertEqual(
            intelligence.normalize_route_ids(("b35", "q", "b35")),
            ("B35", "Q"),
        )

    def test_excluded_route_extraction_supports_provider_bus_forms(self):
        cases = (
            ("avoid M15-SBS", ("M15-SBS",)),
            ("What if I avoid the M15-SBS?", ("M15-SBS",)),
            ("Get me home without the BX12 bus", ("BX12",)),
            ("What if I avoid SIM1?", ("SIM1",)),
            ("No QM5 for this trip", ("QM5",)),
            ("What if the M15-SBS bus is out?", ("M15-SBS",)),
        )
        for message, excluded in cases:
            with self.subTest(message=message):
                parsed = intelligence.parse_intent(message)
                self.assertEqual(parsed.excluded_route_ids, excluded)

    def test_excluded_route_extraction_keeps_sbs_suffix_on_all_bus_prefixes(self):
        cases = (
            ("avoid Q44-SBS", ("Q44-SBS",)),
            ("without the BX12-SBS bus", ("BX12-SBS",)),
        )
        for message, excluded in cases:
            with self.subTest(message=message):
                parsed = intelligence.parse_intent(message)
                self.assertEqual(parsed.excluded_route_ids, excluded)
                self.assertEqual(parsed.requested_route_ids, ())

    def test_requested_route_extraction_keeps_sbs_suffix_on_all_bus_prefixes(self):
        # Extraction keeps the exact SBS identity. The bare phrase alone
        # lands in the pre-existing transit_question fallback that carries
        # no route ids, so the extraction boundary itself must not truncate
        # "Q44-SBS" down to "Q44".
        self.assertEqual(
            intelligence._requested_route_ids("take the Q44-SBS bus"),
            ("Q44-SBS",),
        )
        # With a destination the extracted id flows into route planning as a
        # required-route constraint.
        parsed = intelligence.parse_intent("take the Q44-SBS bus to Coney Island")
        self.assertEqual(parsed.intent, "route_planning")
        self.assertEqual(parsed.requested_route_ids, ("Q44-SBS",))
        self.assertEqual(parsed.excluded_route_ids, ())

    def test_what_if_flag_is_server_determined_from_the_message(self):
        self.assertTrue(
            intelligence.parse_intent("What if I avoid the Q?").what_if
        )
        self.assertTrue(
            intelligence.parse_intent("What if I avoid SIM1?").what_if
        )
        self.assertFalse(
            intelligence.parse_intent("Plan a trip to Coney Island").what_if
        )
        self.assertFalse(
            intelligence.parse_intent("Avoid the Q").what_if
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

    def test_route_followups_and_what_if_use_the_route_contract_without_resetting(self):
        route_followups = (
            "What if I avoid buses?",
            "No stairs for that trip",
            "Add a stop at Barclays Center",
        )
        for phrase in route_followups:
            with self.subTest(phrase=phrase):
                parsed = intelligence.parse_intent(phrase)
                self.assertEqual(parsed.intent, "route_planning")
                self.assertEqual(
                    parsed.required_evidence.required_tools(),
                    ("prepare_route_options", "present_route"),
                )
                self.assertFalse(intelligence.is_new_trip_request(phrase))

        for phrase in ("What if I avoid buses?", "No stairs for that trip"):
            with self.subTest(what_if_phrase=phrase):
                self.assertTrue(intelligence.is_what_if_request(phrase))

        self.assertFalse(intelligence.is_what_if_request("Add a stop at Barclays Center"))

    def test_destination_discovery_requires_place_evidence(self):
        parsed = intelligence.parse_intent("Find me a good pizza place in Brooklyn")
        self.assertEqual(parsed.intent, "destination_discovery")
        self.assertTrue(parsed.required_evidence.places)
        self.assertEqual(
            parsed.required_evidence.required_tools(),
            ("search_local_places",),
        )

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


    def test_saved_place_requests_are_new_trip_shaped_without_session_context(self):
        # Intent parsing has no session. Explicit saved-place endpoints are
        # new-trip-shaped here; contextual_replan suppresses a reset only when
        # they match an authoritative accepted trip.
        saved_place_turns = (
            "Take me to work but I really need the Q.",
            "Take me to work but use the Q.",
            "Take me to work but I want the Q.",
            "Get me to work using the Q instead.",
            "Take me to work.",
            "Take me home.",
            "From home to work.",
            "Can I start from Work instead?",
        )
        for phrase in saved_place_turns:
            with self.subTest(phrase=phrase):
                self.assertTrue(
                    intelligence.is_new_trip_request(phrase),
                    phrase,
                )

        for phrase in (
            "Same trip, but take the Q.",
            "Same trip, but avoid the Q.",
            "Actually I need the Q.",
            "What if I leave from Work instead?",
        ):
            with self.subTest(phrase=phrase):
                self.assertFalse(intelligence.is_new_trip_request(phrase), phrase)

    def test_genuine_new_destination_after_accepted_trip_still_resets(self):
        # A real new destination (or an explicit fresh O/D pair) must keep
        # the new-trip reset so stale accepted-trip state cannot leak into
        # an unrelated plan.
        new_trips = (
            "Get me to Barclays Center.",
            "Take me to JFK instead.",
            "Go to Costco.",
            "Route me to the Bronx Zoo.",
            "Plan a trip from Union Square to Grand Central.",
            "Take me to Penn Station from Coney Island.",
        )
        for phrase in new_trips:
            with self.subTest(phrase=phrase):
                self.assertTrue(
                    intelligence.is_new_trip_request(phrase),
                    phrase,
                )

    def test_saved_place_turns_classify_as_route_planning(self):
        saved_place_turns = (
            "Take me to work but I really need the Q.",
            "Take me to work.",
            "Take me home.",
            "From home to work.",
            "What if I leave from Work instead?",
        )
        for phrase in saved_place_turns:
            with self.subTest(phrase=phrase):
                parsed = intelligence.parse_intent(phrase)
                self.assertEqual(parsed.intent, "route_planning", phrase)
                self.assertTrue(parsed.required_evidence.routes, phrase)
                self.assertEqual(
                    parsed.required_evidence.required_tools(),
                    ("prepare_route_options", "present_route"),
                    phrase,
                )

    def test_contextual_route_requests_keep_canonical_route_tools_available(self):
        phrases = (
            "route me there please",
            "take me there",
            "get us there",
            "how do I get there?",
            "give me directions there",
            "let's go there",
        )

        for phrase in phrases:
            with self.subTest(phrase=phrase):
                parsed = intelligence.parse_intent(phrase)
                self.assertEqual(parsed.intent, "route_planning")
                self.assertEqual(
                    parsed.required_evidence.required_tools(),
                    ("prepare_route_options", "present_route"),
                )

    def test_colloquial_destination_request_leaves_place_and_route_choice_to_model(self):
        parsed = intelligence.parse_intent("lets get some L'Industrie now")

        self.assertEqual(parsed.intent, "destination_discovery")
        self.assertEqual(
            parsed.required_evidence.required_tools(),
            ("search_local_places",),
        )

    def test_contextual_route_grammar_does_not_capture_neighboring_intents(self):
        expected = {
            "When is the next Q train?": "arrival_lookup",
            "How much is the subway fare?": "transit_question",
            "Find me a good pizza place in Brooklyn": "destination_discovery",
        }

        for phrase, intent in expected.items():
            with self.subTest(phrase=phrase):
                self.assertEqual(intelligence.parse_intent(phrase).intent, intent)

    def test_need_the_route_id_grammar_maps_to_required_route(self):
        # "need the Q" is the same required-route intent as
        # take/use/via/prefer/want, and only where unambiguous.
        cases = {
            "Take me to work but I really need the Q.": ("Q",),
            "Take me to work but use the Q.": ("Q",),
            "Same trip, but take the Q.": ("Q",),
            "Get me to work using the Q instead.": ("Q",),
            "Actually I need the Q.": ("Q",),
            "We require the A train.": ("A",),
        }
        for phrase, expected in cases.items():
            with self.subTest(phrase=phrase):
                parsed = intelligence.parse_intent(phrase)
                self.assertEqual(parsed.requested_route_ids, expected, phrase)

        # Articles are not route identifiers; nothing ambiguous maps to a
        # fabricated hard constraint.
        for phrase in ("I need a ride.", "I need an A.", "Need a train."):
            with self.subTest(phrase=phrase):
                self.assertEqual(
                    intelligence._requested_route_ids(phrase),
                    (),
                    phrase,
                )


class RiderLocationResolutionTests(unittest.IsolatedAsyncioTestCase):
    """rider_location reaches the route-preparation tool layer, not just the
    model prompt: origin 'user' resolves deterministically from
    ToolContext.origin without reverse geocoding or model-copied prose."""

    async def test_user_origin_resolves_to_tool_context_gps_with_your_location_label(self):
        ctx = ToolContext(origin={"lat": 40.7, "lng": -73.9})
        place, error = await _location.resolve_named_place(
            "user", ctx, missing_location_message="need GPS"
        )
        self.assertIsNone(error)
        self.assertEqual(place.name, "Your location")
        self.assertEqual((place.latitude, place.longitude), (40.7, -73.9))
        self.assertEqual(place.source, "user")

        point, point_error = await _location.resolve_named_point(
            "", ctx, missing_location_message="need GPS"
        )
        self.assertEqual(point, (40.7, -73.9))
        self.assertIsNone(point_error)

    async def test_user_origin_without_gps_returns_missing_message_not_fabricated_coords(self):
        ctx = ToolContext(origin=None)
        place, error = await _location.resolve_named_place(
            "user", ctx, missing_location_message="need GPS"
        )
        self.assertIsNone(place)
        self.assertEqual(error, "need GPS")

        point, point_error = await _location.resolve_named_point(
            "user", ctx, missing_location_message="need GPS"
        )
        self.assertIsNone(point)
        self.assertEqual(point_error, "need GPS")

    async def test_explicit_place_reference_never_uses_current_location(self):
        ctx = ToolContext(origin={"lat": 40.7, "lng": -73.9})
        place, error = await _location.resolve_named_place(
            "Barclays Center", ctx, missing_location_message="need GPS"
        )
        self.assertIsNone(error)
        self.assertEqual(place.name, "Barclays Center")
        self.assertNotEqual((place.latitude, place.longitude), (40.7, -73.9))


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
