from __future__ import annotations

import os
import tempfile
import unittest
from unittest.mock import patch

import pytest
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

        assert automatic.model == "supported-sonnet"
        assert quick.model == "supported-sonnet"
        assert automatic.max_route_candidates > quick.max_route_candidates
        assert automatic.max_output_tokens > quick.max_output_tokens
        assert automatic.retry_count > quick.retry_count

    def test_unknown_mode_falls_back_to_auto(self):
        assert policy.policy_for_mode("turbo").mode == "auto"
        assert policy.policy_for_mode(None).mode == "auto"

    def test_model_label_is_safe_for_structured_logs(self):
        assert policy.safe_model_label("sonnet\nmessage=secret") == "sonnetmessagesecret"

    def test_sonnet_request_capabilities_are_centralized(self):
        capabilities = policy.request_capabilities("claude-sonnet-5")
        assert not capabilities.supports_manual_thinking
        assert not capabilities.supports_non_default_sampling
        assert not capabilities.supports_assistant_prefill

    def test_private_model_keeps_legacy_request_capabilities(self):
        capabilities = policy.request_capabilities("private-claude-endpoint")
        assert capabilities.supports_manual_thinking
        assert capabilities.supports_non_default_sampling
        assert capabilities.supports_assistant_prefill

    def test_enabled_agent_requires_server_side_credential(self):
        with patch.dict(
            os.environ,
            {"AGENT_ENABLED": "1", "ANTHROPIC_API_KEY": ""},
            clear=True,
        ), pytest.raises(RuntimeError, match="ANTHROPIC_API_KEY"):
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
        ), pytest.raises(RuntimeError, match="server-only"):
            policy.validate_agent_configuration()

    def test_fixture_replay_is_rejected_outside_local_runtime(self):
        with (
            patch.dict(
                os.environ,
                {
                    "SMARTROUTE_ENV": "production",
                    "AGENT_TOOL_FIXTURES": os.path.join(tempfile.gettempdir(), "smartroute-fixtures"),
                    "AGENT_ENABLED": "1",
                    "ANTHROPIC_API_KEY": "server-test-key",
                },
                clear=True,
            ),
            pytest.raises(RuntimeError, match="fixture replay requires"),
        ):
            policy.validate_agent_configuration()


class BoundedServerParsingTests(unittest.TestCase):
    """Only bounded syntax belongs here; rider meaning belongs to the Agent."""

    def test_simple_arithmetic_is_bounded_and_deterministic(self):
        assert loop.evaluate_simple_arithmetic("What is 5 + 5?") == "10."
        assert loop.evaluate_simple_arithmetic("import os") is None
        assert loop.evaluate_simple_arithmetic("2 ** 100") is None

    def test_route_id_normalization_is_uppercase_deduplicated_and_bounded(self):
        assert normalize_route_ids([" q ", "Q", "B35", "", "q train", "M15-SBS", "M15+", None]) == ("Q", "B35", "M15-SBS", "M15+")
        assert normalize_route_id("q") == "Q"
        assert normalize_route_id("q44-sbs") == "Q44-SBS"
        assert normalize_route_id("q train") is None
        assert normalize_route_id("M15-SBS local") is None
        assert normalize_route_id(None) is None

        over_limit = normalize_route_ids(
            [f"R{index}" for index in range(30)]
        )
        assert len(over_limit) == MAX_NORMALIZED_ROUTE_IDS

    def test_route_id_collections_reject_scalar_and_mapping_junk(self):
        assert normalize_route_ids("M15") == ()
        assert normalize_route_ids(b"M15") == ()
        assert normalize_route_ids({"M15": "Q44"}) == ()
        assert normalize_route_ids(None) == ()

    def test_set_like_route_ids_are_sorted_deterministically(self):
        expected = ("12", "B35", "M15-SBS", "Q")
        assert normalize_route_ids({"q", "b35", "m15-sbs", "12"}) == expected
        assert normalize_route_ids(frozenset({"m15-sbs", "12", "q", "b35"})) == expected


class SessionContinuityTests(unittest.TestCase):
    def test_failed_trip_resume_offer_is_emitted_once(self):
        _session_id, state = session.new_session()
        session.mark_pending_trip_failed(state, {"destination": "JFK"}, "timeout")
        assert session.consume_resume_offer(state) == "Do you want me to retry the trip to JFK?"
        assert session.consume_resume_offer(state) is None
        assert state["pending_trip"]["status"] == "awaiting_confirmation"

    def test_new_trip_clears_stale_constraints_and_pending_state(self):
        _session_id, state = session.new_session()
        state["slots"] = {
            "destination": "JFK",
            "constraints": {"exclude_modes": ["BUS"]},
        }
        session.mark_pending_trip_failed(state, {"destination": "JFK"}, "timeout")

        session.reset_for_new_trip(state)

        assert "destination" not in state["slots"]
        assert "constraints" not in state["slots"]
        assert state["pending_trip"]["status"] == "none"


class RiderLocationResolutionTests(unittest.IsolatedAsyncioTestCase):
    async def test_user_origin_resolves_from_tool_context_gps(self):
        context = ToolContext(origin={"lat": 40.7, "lng": -73.9})
        place, error = await _location.resolve_named_place(
            "user",
            context,
            missing_location_message="need GPS",
        )
        assert error is None
        assert place.name == "Your location"
        assert (place.latitude, place.longitude) == (40.7, -73.9)
        assert place.source == "user"

        point, point_error = await _location.resolve_named_point(
            "",
            context,
            missing_location_message="need GPS",
        )
        assert point == (40.7, -73.9)
        assert point_error is None

    async def test_missing_gps_returns_clarification_without_fabricated_coordinates(self):
        context = ToolContext(origin=None)
        place, error = await _location.resolve_named_place(
            "user",
            context,
            missing_location_message="need GPS",
        )
        assert place is None
        assert error == "need GPS"

        point, point_error = await _location.resolve_named_point(
            "user",
            context,
            missing_location_message="need GPS",
        )
        assert point is None
        assert point_error == "need GPS"

    async def test_explicit_place_never_falls_back_to_current_location(self):
        context = ToolContext(origin={"lat": 40.7, "lng": -73.9})
        place, error = await _location.resolve_named_place(
            "Barclays Center",
            context,
            missing_location_message="need GPS",
        )
        assert error is None
        assert place.name == "Barclays Center"
        assert (place.latitude, place.longitude) != (40.7, -73.9)


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
                assert resolved is not None
                assert resolved.name == expected_name
                assert isinstance(resolved.latitude, float)
                assert isinstance(resolved.longitude, float)


if __name__ == "__main__":
    unittest.main()
