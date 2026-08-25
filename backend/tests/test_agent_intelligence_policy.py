from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from app.services.agent import loop, session
from app.services.agent.model import policy
from app.services.agent.tools import location_resolution as _location
from app.services.agent.tools._types import ToolContext
from app.services.trips.preparation.input import (
    MAX_NORMALIZED_ROUTE_IDS,
    normalize_route_id,
    normalize_route_ids,
)


class AgentModePolicyTests(unittest.TestCase):
    def test_auto_and_quick_share_sonnet_with_distinct_budgets(self):
        with patch.dict(
            os.environ,
            {
                "AGENT_AUTO_MODEL": "supported-sonnet",
                "AGENT_QUICK_MODEL": "ignored-legacy-quick",
                "AGENT_HAIKU_MODEL": "ignored-legacy-haiku",
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
        self.assertEqual(quick.model, "supported-sonnet")
        self.assertGreater(automatic.max_route_candidates, quick.max_route_candidates)
        self.assertGreater(automatic.max_output_tokens, quick.max_output_tokens)
        self.assertGreater(automatic.retry_count, quick.retry_count)

    def test_unknown_mode_falls_back_to_auto(self):
        self.assertEqual(policy.policy_for_mode("turbo").mode, "auto")
        self.assertEqual(policy.policy_for_mode(None).mode, "auto")

    def test_model_label_is_safe_for_structured_logs(self):
        self.assertEqual(
            policy.safe_model_label("sonnet\nmessage=secret"),
            "sonnetmessagesecret",
        )

    def test_sonnet_request_capabilities_are_centralized(self):
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

    def test_fixture_replay_is_rejected_outside_local_runtime(self):
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
            with self.assertRaisesRegex(RuntimeError, "fixture replay requires"):
                policy.validate_agent_configuration()


class BoundedServerParsingTests(unittest.TestCase):
    """Only bounded syntax belongs here; rider meaning belongs to the Agent."""

    def test_simple_arithmetic_is_bounded_and_deterministic(self):
        self.assertEqual(loop.evaluate_simple_arithmetic("What is 5 + 5?"), "10.")
        self.assertIsNone(loop.evaluate_simple_arithmetic("import os"))
        self.assertIsNone(loop.evaluate_simple_arithmetic("2 ** 100"))

    def test_route_id_normalization_is_uppercase_deduplicated_and_bounded(self):
        self.assertEqual(
            normalize_route_ids(
                [" q ", "Q", "B35", "", "q train", "M15-SBS", "M15+", None]
            ),
            ("Q", "B35", "M15-SBS", "M15+"),
        )
        self.assertEqual(normalize_route_id("q"), "Q")
        self.assertEqual(normalize_route_id("q44-sbs"), "Q44-SBS")
        self.assertIsNone(normalize_route_id("q train"))
        self.assertIsNone(normalize_route_id("M15-SBS local"))
        self.assertIsNone(normalize_route_id(None))

        over_limit = normalize_route_ids(
            [f"R{index}" for index in range(30)]
        )
        self.assertEqual(len(over_limit), MAX_NORMALIZED_ROUTE_IDS)

    def test_route_id_collections_reject_scalar_and_mapping_junk(self):
        self.assertEqual(normalize_route_ids("M15"), ())
        self.assertEqual(normalize_route_ids(b"M15"), ())
        self.assertEqual(normalize_route_ids({"M15": "Q44"}), ())
        self.assertEqual(normalize_route_ids(None), ())

    def test_set_like_route_ids_are_sorted_deterministically(self):
        expected = ("12", "B35", "M15-SBS", "Q")
        self.assertEqual(
            normalize_route_ids({"q", "b35", "m15-sbs", "12"}),
            expected,
        )
        self.assertEqual(
            normalize_route_ids(frozenset({"m15-sbs", "12", "q", "b35"})),
            expected,
        )


class SessionContinuityTests(unittest.TestCase):
    def test_failed_trip_resume_offer_is_emitted_once(self):
        _session_id, state = session.new_session()
        session.mark_pending_trip_failed(state, {"destination": "JFK"}, "timeout")
        self.assertEqual(
            session.consume_resume_offer(state),
            "Do you want me to retry the trip to JFK?",
        )
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


class RiderLocationResolutionTests(unittest.IsolatedAsyncioTestCase):
    async def test_user_origin_resolves_from_tool_context_gps(self):
        context = ToolContext(origin={"lat": 40.7, "lng": -73.9})
        place, error = await _location.resolve_named_place(
            "user",
            context,
            missing_location_message="need GPS",
        )
        self.assertIsNone(error)
        self.assertEqual(place.name, "Your location")
        self.assertEqual((place.latitude, place.longitude), (40.7, -73.9))
        self.assertEqual(place.source, "user")

        point, point_error = await _location.resolve_named_point(
            "",
            context,
            missing_location_message="need GPS",
        )
        self.assertEqual(point, (40.7, -73.9))
        self.assertIsNone(point_error)

    async def test_missing_gps_returns_clarification_without_fabricated_coordinates(self):
        context = ToolContext(origin=None)
        place, error = await _location.resolve_named_place(
            "user",
            context,
            missing_location_message="need GPS",
        )
        self.assertIsNone(place)
        self.assertEqual(error, "need GPS")

        point, point_error = await _location.resolve_named_point(
            "user",
            context,
            missing_location_message="need GPS",
        )
        self.assertIsNone(point)
        self.assertEqual(point_error, "need GPS")

    async def test_explicit_place_never_falls_back_to_current_location(self):
        context = ToolContext(origin={"lat": 40.7, "lng": -73.9})
        place, error = await _location.resolve_named_place(
            "Barclays Center",
            context,
            missing_location_message="need GPS",
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
