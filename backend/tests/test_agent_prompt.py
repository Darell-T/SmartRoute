"""Guard tests for the conversational agent's system prompt (mirrors the
style of test_ai_advisor_prompt.py) and the per-turn <context> builder."""

import json
import unittest

from app.services.agent.model import prompt as agent_prompt
from app.services.agent import discovery_store
from app.services.agent import profile
from app.services.agent import trip_state
from app.services.agent.tools import complete_turn


class SystemPromptGuardTests(unittest.TestCase):
    def test_scope_contract_clause_present(self):
        self.assertIn("SCOPE CONTRACT", agent_prompt.SYSTEM_PROMPT)
        self.assertIn("NYC", agent_prompt.SYSTEM_PROMPT)
        self.assertIn("driving", agent_prompt.SYSTEM_PROMPT.lower())

    def test_timezone_and_rfc3339_clause_present(self):
        self.assertIn("America/New_York", agent_prompt.SYSTEM_PROMPT)
        self.assertIn("RFC3339", agent_prompt.SYSTEM_PROMPT)

    def test_grounding_invariants_clause_present(self):
        self.assertIn("GROUNDING INVARIANTS", agent_prompt.SYSTEM_PROMPT)
        self.assertIn("prepare_route_options", agent_prompt.SYSTEM_PROMPT)
        self.assertIn("present_route", agent_prompt.SYSTEM_PROMPT)
        self.assertIn("check_transit", agent_prompt.SYSTEM_PROMPT)
        self.assertIn("estimate", agent_prompt.SYSTEM_PROMPT.lower())

    def test_injection_defense_clause_present(self):
        self.assertIn("UNTRUSTED CONTENT", agent_prompt.SYSTEM_PROMPT)
        self.assertIn("tool_result", agent_prompt.SYSTEM_PROMPT)
        normalized = " ".join(agent_prompt.SYSTEM_PROMPT.lower().split())
        self.assertIn("not instructions", normalized)

    def test_multi_stop_procedure_clause_present(self):
        self.assertIn("MULTI-STOP PROCEDURE", agent_prompt.SYSTEM_PROMPT)
        self.assertIn("discover_places", agent_prompt.SYSTEM_PROMPT)
        self.assertNotIn("poi_search", agent_prompt.SYSTEM_PROMPT)
        self.assertIn("dwell", agent_prompt.SYSTEM_PROMPT.lower())
        self.assertIn("25 minutes", agent_prompt.SYSTEM_PROMPT)

    def test_crowd_procedure_clause_present_and_labeled_heuristic(self):
        self.assertIn("CROWD PROCEDURE", agent_prompt.SYSTEM_PROMPT)
        self.assertIn("event_schedule", agent_prompt.SYSTEM_PROMPT)
        self.assertIn("venue_crowd_window", agent_prompt.SYSTEM_PROMPT)
        self.assertIn("heuristic", agent_prompt.SYSTEM_PROMPT.lower())
        normalized = " ".join(agent_prompt.SYSTEM_PROMPT.lower().split())
        self.assertIn("materially overlaps its destination and travel window", normalized)
        self.assertIn("never silently replace it", normalized)

    def test_route_reliability_language_stays_with_route_selection(self):
        normalized = " ".join(agent_prompt.SYSTEM_PROMPT.lower().split())
        self.assertIn("reliability language inside a trip request", normalized)
        self.assertIn("not automatically as a separate systemwide status", normalized)
        self.assertIn("route me there and avoid delays", normalized)
        self.assertIn("never promise a delay-free or crowd-free trip", normalized)

    def test_accepted_route_replay_guidance_uses_existing_card(self):
        normalized = " ".join(agent_prompt.SYSTEM_PROMPT.lower().split())
        self.assertIn("show, repeat, or recap the unchanged accepted route", normalized)
        self.assertIn("present_route with accepted_route_replay.candidate_id", normalized)
        self.assertIn("do not call prepare_route_options", normalized)
        self.assertIn("reprepare for new endpoints, constraints, or time", normalized)

    def test_factual_grounding_clause_present_with_new_tool_names(self):
        self.assertIn("FACTUAL GROUNDING", agent_prompt.SYSTEM_PROMPT)
        self.assertIn("check_transit fact", agent_prompt.SYSTEM_PROMPT)
        self.assertIn("accessibility", agent_prompt.SYSTEM_PROMPT)
        self.assertIn("wheelchair", agent_prompt.SYSTEM_PROMPT.lower())

    def test_transit_claims_cannot_expand_partial_evidence_into_all_clear(self):
        normalized = " ".join(agent_prompt.SYSTEM_PROMPT.lower().split())
        self.assertIn("never generalize partial transit evidence", normalized)
        self.assertIn("unmentioned line, direction, station, or incident class", normalized)
        self.assertIn("no_active_alerts", normalized)
        self.assertIn("requested_routes", normalized)

    def test_immediate_route_advice_collects_and_presents_both_transit_goals(self):
        normalized = " ".join(agent_prompt.SYSTEM_PROMPT.lower().split())
        self.assertIn("should i take", normalized)
        self.assertIn("service-status and arrival goals", normalized)
        self.assertIn("pass explicit direction to check_transit when supplied", normalized)
        self.assertIn("active candidate or accepted-trip context resolve direction", normalized)
        self.assertIn("do not echo an accepted-trip headsign", normalized)
        self.assertIn("arrivals returns structured clarification", normalized)
        self.assertIn("use complete_turn to ask one concise direction question", normalized)
        self.assertNotIn("do not call check_transit yet", normalized)
        self.assertIn("do not preemptively clarify", normalized)
        self.assertIn("present both through present_transit", normalized)

    def test_transit_direction_and_systemwide_scope_are_model_semantics(self):
        normalized = " ".join(agent_prompt.SYSTEM_PROMPT.lower().split())
        self.assertIn("route-specific current-status question", normalized)
        self.assertIn("uptown, downtown, or a destination/headsign", normalized)
        self.assertIn("supplied by the rider in this turn", normalized)
        self.assertIn("active candidate", normalized)
        self.assertIn("authoritative accepted-trip direction", normalized)
        self.assertIn("linewide status check", normalized)
        self.assertIn("only if the tool returns it", normalized)
        self.assertIn("do not preemptively ask", normalized)
        self.assertNotIn("direction clarification before checking", normalized)
        self.assertIn("genuinely systemwide request", normalized)
        self.assertIn("interpret these distinctions semantically", normalized)

    def test_take_or_wait_advice_clarifies_only_missing_boarding_context(self):
        normalized = " ".join(agent_prompt.SYSTEM_PROMPT.lower().split())
        self.assertIn("immediate take-or-wait advice", normalized)
        self.assertIn("arrivals require a resolved direction", normalized)
        self.assertIn("no single boarding stop can be resolved safely", normalized)
        self.assertIn("instead of presenting an ambiguous arrival result", normalized)
        self.assertIn("ask only for what is actually missing", normalized)

    def test_card_referencing_clause_present(self):
        self.assertIn("CARD REFERENCING", agent_prompt.SYSTEM_PROMPT)
        self.assertIn("second option", agent_prompt.SYSTEM_PROMPT.lower())

    def test_place_detail_followup_uses_authoritative_discovery_context(self):
        normalized = " ".join(agent_prompt.SYSTEM_PROMPT.lower().split())
        self.assertIn("tell me more about the second one", normalized)
        self.assertIn("use present_places with the selected opaque place_id", normalized)
        self.assertIn("do not start a new search", normalized)

    def test_discovery_pagination_and_presentation_modes_are_explicit(self):
        normalized = " ".join(agent_prompt.SYSTEM_PROMPT.lower().split())
        self.assertIn("more/other options", normalized)
        self.assertIn("unseen verified places", normalized)
        self.assertIn("exclude_presented=true", normalized)
        self.assertIn("no additional verified options were found", normalized)
        self.assertIn("never recycle a shown place", normalized)
        self.assertIn("presentation_mode=recommendations", normalized)
        self.assertIn("details only for one referenced", normalized)
        self.assertIn("straight-line rider distance", normalized)
        self.assertIn("prepare a route when travel relevance matters", normalized)
        self.assertIn("menu, drinks, patio, and pricing claims", normalized)

    def test_compound_discovery_routes_directly_to_route_presentation(self):
        normalized = " ".join(agent_prompt.SYSTEM_PROMPT.lower().split())
        self.assertIn("compound place-and-route turn retains the route goal", normalized)
        self.assertIn("cannot stop until routing is resolved", normalized)
        self.assertIn("without calling present_places", normalized)
        self.assertIn("lists belong to explicit recommendation requests", normalized)

    def test_route_dependent_destination_choice_uses_route_facts_before_commitment(self):
        normalized = " ".join(agent_prompt.SYSTEM_PROMPT.lower().split())
        self.assertIn(
            "distinguish place-only destination choice from route-dependent destination choice",
            normalized,
        )
        self.assertIn("least walking", normalized)
        self.assertIn("do not select one place first", normalized)
        self.assertIn("pass multiple opaque destination_place_ids", normalized)
        self.assertIn("compare actual route facts", normalized)
        self.assertIn("even when the rider does not say", normalized)
        self.assertNotIn(
            "select one defensible verified place from the returned evidence",
            normalized,
        )

    def test_compound_discovery_transit_and_route_has_one_terminal_sequence(self):
        normalized = " ".join(agent_prompt.SYSTEM_PROMPT.lower().split())
        self.assertIn("capabilities for independent goals may run together", normalized)
        self.assertIn("a dependent route may use an opaque place", normalized)
        self.assertIn("present every rider-visible provider-grounded result", normalized)

    def test_multi_borough_discovery_uses_one_combined_search(self):
        normalized = " ".join(agent_prompt.SYSTEM_PROMPT.lower().split())
        self.assertIn("one discover_places call", normalized)
        self.assertIn("all requested boroughs in scope.values", normalized)
        self.assertIn("never issue parallel discovery calls", normalized)

    def test_successful_place_discovery_goes_directly_to_place_presentation(self):
        normalized = " ".join(agent_prompt.SYSTEM_PROMPT.lower().split())
        self.assertIn("present_places: present one through five verified places", normalized)
        self.assertIn("does not by itself end a compound turn", normalized)
        self.assertIn("complete_turn", normalized)

    def test_metlife_nj_clause_present(self):
        normalized = " ".join(agent_prompt.SYSTEM_PROMPT.split())
        self.assertIn("METLIFE", agent_prompt.SYSTEM_PROMPT.upper())
        self.assertIn("New Jersey", normalized)
        self.assertIn("Penn Station", agent_prompt.SYSTEM_PROMPT)

    def test_rider_facing_style_clause_mirrors_sanitizer_blacklist(self):
        self.assertIn("RIDER-FACING STYLE", agent_prompt.SYSTEM_PROMPT)
        for banned_word in ("backend", "API", "JSON", "database", "SQL", "GTFS", "server", "prompt"):
            self.assertIn(banned_word, agent_prompt.SYSTEM_PROMPT)

    def test_rider_facing_style_is_conversational_without_feature_dumps(self):
        normalized = " ".join(agent_prompt.SYSTEM_PROMPT.lower().split())
        self.assertIn("not a report or a feature catalogue", normalized)
        self.assertIn("simple greetings to one natural sentence", normalized)
        self.assertIn("do not list capabilities unless the rider asks", normalized)
        self.assertIn("concise contextual framing", normalized)

    def test_prompt_is_non_empty_and_reasonably_sized(self):
        # Loose sanity bound -- guards against an accidental near-empty prompt
        # slipping through, without pinning an exact byte count.
        self.assertGreater(len(agent_prompt.SYSTEM_PROMPT), 500)

    def test_response_modes_share_evidence_without_model_visible_scoring(self):
        self.assertIn("RESPONSE PRESENTATION", agent_prompt.SYSTEM_PROMPT)
        normalized = " ".join(agent_prompt.SYSTEM_PROMPT.lower().split())
        self.assertIn("final rider-facing prose only", normalized)
        self.assertIn("same sonnet model", normalized)
        self.assertIn("same eight-capability vocabulary", normalized)
        self.assertIn("evidence requirements", normalized)
        self.assertIn("smaller candidate", normalized)
        self.assertIn("must never omit mandatory evidence", normalized)
        self.assertIn("no numeric score", normalized)
        self.assertIn("private deterministic order", normalized)
        self.assertNotIn("share evidence requirements, scoring", normalized)
        self.assertIn("severe disruptions", normalized)

    def test_activity_copy_is_contextual_but_runtime_gated(self):
        normalized = " ".join(agent_prompt.SYSTEM_PROMPT.lower().split())
        self.assertIn("short activity_label", normalized)
        self.assertIn("use null when a simple or fast action", normalized)
        self.assertIn("the runtime decides whether and when", normalized)
        self.assertIn("do not emit separate progress prose", normalized)

    def test_prompt_instructs_excluded_route_followups(self):
        normalized = " ".join(agent_prompt.SYSTEM_PROMPT.lower().split())
        self.assertIn("excluded_route_ids", agent_prompt.SYSTEM_PROMPT)
        self.assertIn("avoid the q", normalized)
        self.assertIn("never treat an avoided line as required", normalized)
        self.assertIn("allowed_route_ids", agent_prompt.SYSTEM_PROMPT)
        self.assertIn("required_route_ids", agent_prompt.SYSTEM_PROMPT)
        self.assertIn("clear the old exclusion", normalized)
        self.assertIn("backend enforces the choice", normalized)
        self.assertIn("keep the accepted trip unchanged until the rider commits it", normalized)

    def test_route_framing_stays_natural_without_rewriting_canonical_facts(self):
        normalized = " ".join(agent_prompt.SYSTEM_PROMPT.lower().split())
        self.assertIn("do not repeat place ratings", normalized)
        self.assertIn("transit line names", normalized)
        self.assertIn("every normal route presentation requires", normalized)
        self.assertIn("even when the rider did not state a preference", normalized)
        self.assertIn("concrete qualitative explanation", normalized)
        self.assertIn("do not answer only that it fits", normalized)
        self.assertIn("name the supported route-quality factor directly", normalized)
        self.assertIn("no comparative factor or explicit rider constraint", normalized)
        self.assertIn("options were close", normalized)
        self.assertIn("nothing had a clear edge", normalized)
        self.assertIn("covers what the rider asked for", normalized)
        self.assertIn("do not invent a specific advantage", normalized)
        self.assertIn("expose backend language", normalized)
        self.assertIn("canonical itinerary itself supports", normalized)
        self.assertIn("hard validity alone does not prove route shape", normalized)
        self.assertIn("qualitative transfer, walking, disruption, crowd", normalized)
        self.assertIn("never claim a factor that is tied", normalized)
        self.assertIn("reasonable_local_option", normalized)
        self.assertIn("correct it once from the same active candidate set", normalized)
        self.assertIn("deterministic fallback", normalized)
        self.assertIn("do not prepare routes again", normalized)

    def test_route_choice_has_a_private_semantic_precommit_check(self):
        normalized = " ".join(agent_prompt.SYSTEM_PROMPT.lower().split())
        self.assertIn("before committing a choice", normalized)
        self.assertIn("identify what the rider is optimizing", normalized)
        self.assertIn("compare the strongest competitor", normalized)
        self.assertIn("what the choice gains and sacrifices", normalized)
        self.assertIn("whether the sacrifice is proportional", normalized)
        self.assertIn("primary preference is met without a meaningful downside", normalized)
        self.assertIn("confirm service claims are supported by evidence", normalized)
        self.assertIn("whether the trip makes practical sense", normalized)
        self.assertIn("internal check, not hidden reasoning to expose", normalized)
        self.assertIn("never request or reveal chain-of-thought", normalized)

    def test_new_destination_supersedes_the_accepted_trip_endpoint(self):
        normalized = " ".join(agent_prompt.SYSTEM_PROMPT.lower().split())
        self.assertIn("new named destination", normalized)
        self.assertIn("supersedes the accepted trip destination", normalized)
        self.assertIn("destination_source=current_turn", normalized)
        self.assertIn("ask which location the rider means", normalized)

    def test_transit_framing_is_natural_but_canonical_facts_stay_owned(self):
        normalized = " ".join(agent_prompt.SYSTEM_PROMPT.lower().split())
        self.assertIn("use lead_in for a brief natural interpretation", normalized)
        self.assertIn("the server inserts canonical transit facts", normalized)
        self.assertIn("state those verified findings before qualifying", normalized)

    def test_complete_turn_route_comparison_exception_is_bounded(self):
        description = complete_turn.COMPLETE_TURN_SCHEMA["description"]
        self.assertIn("why-not explanation", description)
        self.assertIn("accepted_route_comparison", description)
        self.assertIn("do not add a route, card", description)

    def test_prompt_requires_server_owned_endpoint_precedence_before_clarification(self):
        normalized = " ".join(agent_prompt.SYSTEM_PROMPT.lower().split())
        self.assertIn("consult server-owned context before clarification", normalized)
        self.assertIn("current gps is a sufficient origin", normalized)
        self.assertIn("never invent home or work", normalized)
        self.assertIn("never let current location replace an explicitly supplied origin", normalized)


class BuildTurnContextTests(unittest.TestCase):
    def test_includes_current_time(self):
        block = agent_prompt.build_turn_context({}, "2026-07-15T21:30:00-04:00", None, None)
        self.assertTrue(block.startswith("<context>"))
        self.assertTrue(block.endswith("</context>"))
        self.assertIn("now: 2026-07-15T21:30:00-04:00", block)

    def test_includes_rider_location_when_given(self):
        block = agent_prompt.build_turn_context({}, "now", {"lat": 40.7128, "lng": -74.006}, None)
        self.assertIn("rider_location: 40.7128,-74.0060", block)

    def test_omits_rider_location_when_absent(self):
        block = agent_prompt.build_turn_context({}, "now", None, None)
        self.assertNotIn("rider_location", block)

    def test_includes_saved_place_labels_without_coordinates_or_addresses(self):
        session: dict = {}
        profile.save_place(
            session,
            {
                "label": "Apartment",
                "address": "123 Private St",
                "latitude": 40.7128,
                "longitude": -74.0060,
                "place_id": "provider-secret-id",
            },
            slot="home",
        )
        profile.save_place(
            session,
            {
                "label": "Office",
                "address": "456 Private Ave",
                "latitude": 40.7500,
                "longitude": -73.9900,
            },
            slot="work",
        )

        block = agent_prompt.build_turn_context(session, "now")

        saved_line = next(
            line for line in block.splitlines() if line.startswith("saved_places:")
        )
        self.assertEqual(
            json.loads(saved_line[len("saved_places: "):]),
            {"home": "Apartment", "work": "Office"},
        )
        self.assertNotIn("123 Private St", saved_line)
        self.assertNotIn("456 Private Ave", saved_line)
        self.assertNotIn("40.7128", saved_line)
        self.assertNotIn("provider-secret-id", saved_line)

    def test_includes_known_slots(self):
        session = {"slots": {"origin": "user", "destination": "Costco"}}
        block = agent_prompt.build_turn_context(session, "now", None, None)
        self.assertIn("known_slots:", block)
        # The embedded JSON round-trips.
        slots_line = next(line for line in block.splitlines() if line.startswith("known_slots:"))
        parsed = json.loads(slots_line[len("known_slots: "):])
        self.assertEqual(parsed["destination"], "Costco")

    def test_includes_recent_route_cards_digest(self):
        session = {
            "route_cards": [
                {"card_id": "rc_aaaa1111", "role": "recommended", "lines": ["Q"], "eta_minutes": 20},
            ]
        }
        block = agent_prompt.build_turn_context(session, "now", None, None)
        self.assertIn("recent_route_cards:", block)
        self.assertIn("rc_aaaa1111", block)

    def test_includes_accepted_route_replay_only_when_card_matches_active_trip(self):
        itinerary = {"legs": [{"mode": "SUBWAY", "route_id": "Q"}]}
        card = {
            "card_id": "rc_accepted",
            "role": "recommended",
            "origin": {"label": "Your location"},
            "destination": {"label": "Barclays Center"},
            "summary": {"eta_minutes": 23},
            "route": [{"type": "SUBWAY", "route_id": "Q"}],
            "alerts": [],
            "itinerary": itinerary,
        }
        session = {
            "_transcript": {
                "v": 1,
                "history": [],
                "route_cards": [card],
                "arrival_cards": [],
            },
            "active_trip": {
                "card_id": "rc_accepted",
                "canonical_itinerary": itinerary,
            },
            "trip_state": {"selected_candidate_id": "cd_selected"},
        }
        block = agent_prompt.build_turn_context(session, "now")
        replay_line = next(
            line for line in block.splitlines() if line.startswith("accepted_route_replay:")
        )
        self.assertEqual(
            json.loads(replay_line.split(": ", 1)[1]),
            {"candidate_id": "cd_selected", "card_id": "rc_accepted"},
        )

        session["active_trip"]["canonical_itinerary"] = {
            "legs": [{"mode": "BUS"}]
        }
        invalid = agent_prompt.build_turn_context(session, "now")
        self.assertNotIn("accepted_route_replay:", invalid)

    def test_includes_selected_card_id_when_given(self):
        block = agent_prompt.build_turn_context({}, "now", None, "rc_deadbeef")
        self.assertIn("selected_card_id: rc_deadbeef", block)

    def test_omits_selected_card_id_when_absent(self):
        block = agent_prompt.build_turn_context({}, "now", None, None)
        self.assertNotIn("selected_card_id", block)

    def test_response_presentation_defaults_to_auto_and_accepts_quick(self):
        automatic = agent_prompt.build_turn_context({}, "now")
        quick = agent_prompt.build_turn_context(
            {},
            "now",
            response_presentation="quick",
        )
        invalid = agent_prompt.build_turn_context(
            {},
            "now",
            response_presentation="verbose",
        )

        self.assertIn("response_presentation: auto", automatic)
        self.assertIn("response_presentation: quick", quick)
        self.assertIn("response_presentation: auto", invalid)

    def test_trip_digest_waypoints_use_stored_names_not_opaque_ids(self):
        set_id = discovery_store.store_discovery_set(
            session_id="sess-prompt",
            places=[
                {
                    "name": "Di Fara Pizza",
                    "address": "1424 Av J",
                }
            ],
            query="pizza",
        )
        record = discovery_store.load_discovery_set(set_id, session_id="sess-prompt")
        place_id = record["places"][0]["place_id"]
        session: dict = {}
        trip_state.bind_discovery_set(session, set_id)
        trip_state.update_trip_state(
            session,
            destination="Di Fara Pizza",
            waypoints=[place_id],
        )
        block = agent_prompt.build_turn_context(
            session,
            "now",
            None,
            None,
            session_id="sess-prompt",
        )
        trip_line = next(
            line for line in block.splitlines() if line.startswith("trip_state:")
        )
        parsed = json.loads(trip_line[len("trip_state: "):])
        self.assertEqual(parsed["waypoints"], ["Di Fara Pizza"])
        self.assertNotIn(place_id, trip_line)
        self.assertIn("active_discovery", block)


if __name__ == "__main__":
    unittest.main()
