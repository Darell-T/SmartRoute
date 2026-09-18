"""Guard tests for the conversational agent's system prompt (mirrors the
style of test_ai_advisor_prompt.py) and the per-turn <context> builder."""

import json
import unittest

from app.services.agent import discovery_store, profile, trip_state
from app.services.agent.model import prompt as agent_prompt


class SystemPromptGuardTests(unittest.TestCase):
    def test_scope_contract_clause_present(self):
        assert "SCOPE CONTRACT" in agent_prompt.SYSTEM_PROMPT
        assert "NYC" in agent_prompt.SYSTEM_PROMPT
        assert "driving" in agent_prompt.SYSTEM_PROMPT.lower()

    def test_timezone_and_rfc3339_clause_present(self):
        assert "America/New_York" in agent_prompt.SYSTEM_PROMPT
        assert "RFC3339" in agent_prompt.SYSTEM_PROMPT

    def test_grounding_invariants_clause_present(self):
        assert "GROUNDING INVARIANTS" in agent_prompt.SYSTEM_PROMPT
        assert "prepare_route_options" in agent_prompt.SYSTEM_PROMPT
        assert "present_route" in agent_prompt.SYSTEM_PROMPT
        assert "check_transit" in agent_prompt.SYSTEM_PROMPT
        assert "estimate" in agent_prompt.SYSTEM_PROMPT.lower()

    def test_injection_defense_clause_present(self):
        assert "UNTRUSTED CONTENT" in agent_prompt.SYSTEM_PROMPT
        assert "tool_result" in agent_prompt.SYSTEM_PROMPT
        normalized = " ".join(agent_prompt.SYSTEM_PROMPT.lower().split())
        assert "not instructions" in normalized

    def test_transit_claims_cannot_expand_partial_evidence_into_all_clear(self):
        normalized = " ".join(agent_prompt.SYSTEM_PROMPT.lower().split())
        assert "never generalize partial transit evidence" in normalized
        assert "unmentioned line, direction, station, or incident class" in normalized
        assert "no_active_alerts" in normalized
        assert "requested_routes" in normalized

    def test_immediate_route_advice_collects_and_presents_both_transit_goals(self):
        normalized = " ".join(agent_prompt.SYSTEM_PROMPT.lower().split())
        assert "should i take" in normalized
        assert "service-status and arrival goals" in normalized
        assert "pass explicit direction to check_transit when supplied" in normalized
        assert (
            "active candidate or accepted-trip context resolve direction" in normalized
        )
        assert "do not echo an accepted-trip headsign" in normalized
        assert "arrivals returns structured clarification" in normalized
        assert "use complete_turn to ask one concise direction question" in normalized
        assert "do not call check_transit yet" not in normalized
        assert "do not preemptively clarify" in normalized
        assert "present both through present_transit" in normalized

    def test_discovery_pagination_and_presentation_modes_are_explicit(self):
        normalized = " ".join(agent_prompt.SYSTEM_PROMPT.lower().split())
        assert "more/other options" in normalized
        assert "unseen verified places" in normalized
        assert "exclude_presented=true" in normalized
        assert "no additional verified options were found" in normalized
        assert "never recycle a shown place" in normalized
        assert "presentation_mode=recommendations" in normalized
        assert "details only for one referenced" in normalized
        assert "straight-line rider distance" in normalized
        assert "prepare a route when travel relevance matters" in normalized
        assert "menu, drinks, patio, and pricing claims" in normalized

    def test_route_dependent_destination_choice_uses_route_facts_before_commitment(
        self,
    ):
        normalized = " ".join(agent_prompt.SYSTEM_PROMPT.lower().split())
        assert (
            "distinguish place-only destination choice from route-dependent destination choice"
            in normalized
        )
        assert "least walking" in normalized
        assert "do not select one place first" in normalized
        assert "pass multiple opaque destination_place_ids" in normalized
        assert "compare actual route facts" in normalized
        assert "even when the rider does not say" in normalized
        assert (
            "select one defensible verified place from the returned evidence"
            not in normalized
        )

    def test_rider_facing_style_is_conversational_without_feature_dumps(self):
        normalized = " ".join(agent_prompt.SYSTEM_PROMPT.lower().split())
        assert "not a report or a feature catalogue" in normalized
        assert "simple greetings to one natural sentence" in normalized
        assert "do not list capabilities unless the rider asks" in normalized
        assert "concise contextual framing" in normalized

    def test_prompt_is_non_empty_and_reasonably_sized(self):
        # Loose sanity bound -- guards against an accidental near-empty prompt
        # slipping through, without pinning an exact byte count.
        assert len(agent_prompt.SYSTEM_PROMPT) > 500

    def test_response_modes_share_evidence_without_model_visible_scoring(self):
        assert "RESPONSE PRESENTATION" in agent_prompt.SYSTEM_PROMPT
        normalized = " ".join(agent_prompt.SYSTEM_PROMPT.lower().split())
        assert "final rider-facing prose only" in normalized
        assert "same sonnet model" in normalized
        assert "same eight-capability vocabulary" in normalized
        assert "evidence requirements" in normalized
        assert "smaller candidate" in normalized
        assert "must never omit mandatory evidence" in normalized
        assert "no numeric score" in normalized
        assert "private deterministic order" in normalized
        assert "share evidence requirements, scoring" not in normalized
        assert "severe disruptions" in normalized

    def test_queue_evidence_stays_inside_existing_place_capabilities(self):
        assert "QUEUE EVIDENCE" in agent_prompt.SYSTEM_PROMPT
        normalized = " ".join(agent_prompt.SYSTEM_PROMPT.lower().split())
        assert "not another capability" in normalized
        assert "queue_context" in normalized
        assert "present_places owns every rider-facing queue number" in normalized
        assert "damn_lines" not in normalized
        assert "never invent a global threshold" in normalized
        assert "join-now estimate" in normalized
        assert "keep plausible physical branches as separate candidates" in normalized
        assert "ask which location" not in normalized

    def test_prompt_instructs_excluded_route_followups(self):
        normalized = " ".join(agent_prompt.SYSTEM_PROMPT.lower().split())
        assert "excluded_route_ids" in agent_prompt.SYSTEM_PROMPT
        assert "avoid the q" in normalized
        assert "never treat an avoided line as required" in normalized
        assert "allowed_route_ids" in agent_prompt.SYSTEM_PROMPT
        assert "required_route_ids" in agent_prompt.SYSTEM_PROMPT
        assert "clear the old exclusion" in normalized
        assert "backend enforces the choice" in normalized
        assert (
            "keep the accepted trip unchanged until the rider commits it" in normalized
        )

    def test_route_framing_stays_natural_without_rewriting_canonical_facts(self):
        normalized = " ".join(agent_prompt.SYSTEM_PROMPT.lower().split())
        assert "do not repeat place ratings" in normalized
        assert "transit line names" in normalized
        assert "every normal route presentation requires" in normalized
        assert "even when the rider did not state a preference" in normalized
        assert "concrete qualitative explanation" in normalized
        assert "do not answer only that it fits" in normalized
        assert "name the supported route-quality factor directly" in normalized
        assert "no comparative factor or explicit rider constraint" in normalized
        assert "options were close" in normalized
        assert "nothing had a clear edge" in normalized
        assert "covers what the rider asked for" in normalized
        assert "do not invent a specific advantage" in normalized
        assert "expose backend language" in normalized
        assert "canonical itinerary itself supports" in normalized
        assert "hard validity alone does not prove route shape" in normalized
        assert "qualitative transfer, walking, disruption, crowd" in normalized
        assert "never claim a factor that is tied" in normalized
        assert "reasonable_local_option" in normalized
        assert "correct it once from the same active candidate set" in normalized
        assert "deterministic fallback" in normalized
        assert "do not prepare routes again" in normalized

    def test_prompt_requires_server_owned_endpoint_precedence_before_clarification(
        self,
    ):
        normalized = " ".join(agent_prompt.SYSTEM_PROMPT.lower().split())
        assert "consult server-owned context before clarification" in normalized
        assert "current gps is a sufficient origin" in normalized
        assert "never invent home or work" in normalized
        assert (
            "never let current location replace an explicitly supplied origin"
            in normalized
        )


class BuildTurnContextTests(unittest.TestCase):
    def test_includes_current_time(self):
        block = agent_prompt.build_turn_context(
            {}, "2026-07-15T21:30:00-04:00", None, None
        )
        assert block.startswith("<context>")
        assert block.endswith("</context>")
        assert "now: 2026-07-15T21:30:00-04:00" in block

    def test_includes_rider_location_when_given(self):
        block = agent_prompt.build_turn_context(
            {}, "now", {"lat": 40.7128, "lng": -74.006}, None
        )
        assert "rider_location: 40.7128,-74.0060" in block

    def test_omits_rider_location_when_absent(self):
        block = agent_prompt.build_turn_context({}, "now", None, None)
        assert "rider_location" not in block

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
        assert json.loads(saved_line[len("saved_places: ") :]) == {
            "home": "Apartment",
            "work": "Office",
        }
        assert "123 Private St" not in saved_line
        assert "456 Private Ave" not in saved_line
        assert "40.7128" not in saved_line
        assert "provider-secret-id" not in saved_line

    def test_includes_known_slots(self):
        session = {"slots": {"origin": "user", "destination": "Costco"}}
        block = agent_prompt.build_turn_context(session, "now", None, None)
        assert "known_slots:" in block
        # The embedded JSON round-trips.
        slots_line = next(
            line for line in block.splitlines() if line.startswith("known_slots:")
        )
        parsed = json.loads(slots_line[len("known_slots: ") :])
        assert parsed["destination"] == "Costco"

    def test_includes_recent_route_cards_digest(self):
        session = {
            "route_cards": [
                {
                    "card_id": "rc_aaaa1111",
                    "role": "recommended",
                    "lines": ["Q"],
                    "eta_minutes": 20,
                },
            ]
        }
        block = agent_prompt.build_turn_context(session, "now", None, None)
        assert "recent_route_cards:" in block
        assert "rc_aaaa1111" in block

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
            line
            for line in block.splitlines()
            if line.startswith("accepted_route_replay:")
        )
        assert json.loads(replay_line.split(": ", 1)[1]) == {
            "candidate_id": "cd_selected",
            "card_id": "rc_accepted",
        }

        session["active_trip"]["canonical_itinerary"] = {"legs": [{"mode": "BUS"}]}
        invalid = agent_prompt.build_turn_context(session, "now")
        assert "accepted_route_replay:" not in invalid

    def test_includes_selected_card_id_when_given(self):
        block = agent_prompt.build_turn_context({}, "now", None, "rc_deadbeef")
        assert "selected_card_id: rc_deadbeef" in block

    def test_omits_selected_card_id_when_absent(self):
        block = agent_prompt.build_turn_context({}, "now", None, None)
        assert "selected_card_id" not in block

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

        assert "response_presentation: auto" in automatic
        assert "response_presentation: quick" in quick
        assert "response_presentation: auto" in invalid

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
        parsed = json.loads(trip_line[len("trip_state: ") :])
        assert parsed["waypoints"] == ["Di Fara Pizza"]
        assert place_id not in trip_line
        assert "active_discovery" in block


if __name__ == "__main__":
    unittest.main()
