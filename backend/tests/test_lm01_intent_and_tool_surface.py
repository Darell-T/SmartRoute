"""The initial model surface is stable across rider phrasing and mode."""

from __future__ import annotations

import unittest

from app.services.agent import loop, session
from app.services.agent.model import policy


class ModelLedInitialSurfaceTests(unittest.TestCase):
    def test_initial_surface_is_identical_across_paraphrases_and_modes(self) -> None:
        _session_id, fresh = session.new_session()
        expected = {
            "declare_goals",
            "discover_places",
            "check_transit",
            "prepare_route_options",
            "complete_turn",
        }

        for message in (
            "gimme a ride to work",
            "Is hopping on the Q a smart move right now?",
            "Find ramen and route me there by subway",
            "What about Manhattan?",
            "How was your ride to work?",
        ):
            for mode in ("auto", "quick"):
                with self.subTest(message=message, mode=mode):
                    names = {
                        schema["name"]
                        for schema in loop._tools_for_state(
                            policy.policy_for_mode(mode),
                            session=fresh,
                        )
                    }
                    self.assertEqual(names, expected)

    def test_presenters_are_not_offered_without_server_owned_evidence(self) -> None:
        _session_id, fresh = session.new_session()
        for mode in ("auto", "quick"):
            with self.subTest(mode=mode):
                names = {
                    schema["name"]
                    for schema in loop._tools_for_state(
                        policy.policy_for_mode(mode),
                        session=fresh,
                    )
                }
                self.assertTrue(
                    {"present_places", "present_transit", "present_route"}.isdisjoint(
                        names
                    )
                )


if __name__ == "__main__":
    unittest.main()
