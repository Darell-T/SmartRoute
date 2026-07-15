"""Guard tests for the conversational agent's system prompt (mirrors the
style of test_ai_advisor_prompt.py) and the per-turn <context> builder."""

import json
import unittest

from app.services.agent import prompt as agent_prompt


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
        self.assertIn("plan_trip", agent_prompt.SYSTEM_PROMPT)
        self.assertIn("event_lookup", agent_prompt.SYSTEM_PROMPT)
        self.assertIn("estimate", agent_prompt.SYSTEM_PROMPT.lower())

    def test_injection_defense_clause_present(self):
        self.assertIn("UNTRUSTED CONTENT", agent_prompt.SYSTEM_PROMPT)
        self.assertIn("tool_result", agent_prompt.SYSTEM_PROMPT)
        normalized = " ".join(agent_prompt.SYSTEM_PROMPT.lower().split())
        self.assertIn("not instructions", normalized)

    def test_multi_stop_procedure_clause_present(self):
        self.assertIn("MULTI-STOP PROCEDURE", agent_prompt.SYSTEM_PROMPT)
        self.assertIn("poi_search", agent_prompt.SYSTEM_PROMPT)
        self.assertIn("dwell", agent_prompt.SYSTEM_PROMPT.lower())
        self.assertIn("25 minutes", agent_prompt.SYSTEM_PROMPT)

    def test_crowd_procedure_clause_present_and_labeled_heuristic(self):
        self.assertIn("CROWD PROCEDURE", agent_prompt.SYSTEM_PROMPT)
        self.assertIn("event_lookup", agent_prompt.SYSTEM_PROMPT)
        self.assertIn("venue_crowd_window", agent_prompt.SYSTEM_PROMPT)
        self.assertIn("heuristic", agent_prompt.SYSTEM_PROMPT.lower())

    def test_card_referencing_clause_present(self):
        self.assertIn("CARD REFERENCING", agent_prompt.SYSTEM_PROMPT)
        self.assertIn("second option", agent_prompt.SYSTEM_PROMPT.lower())

    def test_metlife_nj_clause_present(self):
        self.assertIn("METLIFE", agent_prompt.SYSTEM_PROMPT.upper())
        self.assertIn("New Jersey", agent_prompt.SYSTEM_PROMPT)
        self.assertIn("Penn Station", agent_prompt.SYSTEM_PROMPT)

    def test_rider_facing_style_clause_mirrors_sanitizer_blacklist(self):
        self.assertIn("RIDER-FACING STYLE", agent_prompt.SYSTEM_PROMPT)
        for banned_word in ("backend", "API", "JSON", "database", "SQL", "GTFS", "server", "prompt"):
            self.assertIn(banned_word, agent_prompt.SYSTEM_PROMPT)

    def test_prompt_is_non_empty_and_reasonably_sized(self):
        # Loose sanity bound -- guards against an accidental near-empty prompt
        # slipping through, without pinning an exact byte count.
        self.assertGreater(len(agent_prompt.SYSTEM_PROMPT), 500)


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

    def test_includes_selected_card_id_when_given(self):
        block = agent_prompt.build_turn_context({}, "now", None, "rc_deadbeef")
        self.assertIn("selected_card_id: rc_deadbeef", block)

    def test_omits_selected_card_id_when_absent(self):
        block = agent_prompt.build_turn_context({}, "now", None, None)
        self.assertNotIn("selected_card_id", block)


if __name__ == "__main__":
    unittest.main()
