import os
import unittest
from unittest.mock import patch

from app import runtime


class RuntimeSafeguardTests(unittest.TestCase):
    def test_nonlocal_profiles_reject_each_mock_flag_and_both(self):
        for flags in (
            {"AGENT_MOCK_MODE": "1", "JARVIS_MOCK_ADVISOR": "0"},
            {"AGENT_MOCK_MODE": "0", "JARVIS_MOCK_ADVISOR": "1"},
            {"AGENT_MOCK_MODE": "1", "JARVIS_MOCK_ADVISOR": "1"},
        ):
            for profile in (None, "production", "staging", "custom-platform"):
                environment = dict(flags)
                if profile is not None:
                    environment["SMARTROUTE_ENV"] = profile
                with self.subTest(flags=flags, profile=profile), patch.dict(
                    os.environ, environment, clear=True
                ):
                    with self.assertRaisesRegex(RuntimeError, "Mock agent modes require"):
                        runtime.validate_mock_safeguards()

    def test_explicit_local_test_profiles_permit_mock_flags(self):
        for profile in ("local", "development", "dev", "test", "testing"):
            with self.subTest(profile=profile), patch.dict(
                os.environ,
                {"APP_ENV": profile, "AGENT_MOCK_MODE": "1", "JARVIS_MOCK_ADVISOR": "1"},
                clear=True,
            ):
                runtime.validate_mock_safeguards()
                self.assertEqual(runtime.runtime_mode_label(), "local_test")

    def test_render_signal_fails_closed_even_with_local_profile(self):
        with patch.dict(
            os.environ,
            {"SMARTROUTE_ENV": "local", "RENDER_SERVICE_ID": "srv-example", "AGENT_MOCK_MODE": "1"},
            clear=True,
        ):
            with self.assertRaisesRegex(RuntimeError, "Mock agent modes require"):
                runtime.validate_mock_safeguards()
            self.assertEqual(runtime.runtime_mode_label(), "production")

    def test_memory_sessions_are_rejected_outside_local_test_profiles(self):
        for profile in (None, "production", "staging", "custom-platform"):
            environment = {"AGENT_ALLOW_MEMORY_SESSIONS": "1"}
            if profile is not None:
                environment["SMARTROUTE_ENV"] = profile
            with self.subTest(profile=profile), patch.dict(
                os.environ, environment, clear=True
            ):
                with self.assertRaisesRegex(RuntimeError, "In-memory agent sessions require"):
                    runtime.validate_mock_safeguards()

    def test_explicit_local_test_profiles_permit_memory_sessions(self):
        for profile in ("local", "development", "dev", "test", "testing"):
            with self.subTest(profile=profile), patch.dict(
                os.environ,
                {"SMARTROUTE_ENV": profile, "AGENT_ALLOW_MEMORY_SESSIONS": "1"},
                clear=True,
            ):
                runtime.validate_mock_safeguards()

    def test_render_rejects_memory_sessions_even_with_local_profile(self):
        with patch.dict(
            os.environ,
            {
                "SMARTROUTE_ENV": "local",
                "RENDER_SERVICE_ID": "srv-example",
                "AGENT_ALLOW_MEMORY_SESSIONS": "1",
            },
            clear=True,
        ):
            with self.assertRaisesRegex(RuntimeError, "In-memory agent sessions require"):
                runtime.validate_mock_safeguards()

    def test_unknown_profile_has_a_safe_readiness_label(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(runtime.runtime_mode_label(), "unknown")

    def test_established_environment_precedence_is_preserved(self):
        with patch.dict(
            os.environ,
            {"SMARTROUTE_ENV": "dev", "APP_ENV": "production", "ENVIRONMENT": "production"},
            clear=True,
        ):
            self.assertFalse(runtime.is_production())


class FixtureSafeguardTests(unittest.TestCase):
    """Startup guard for AGENT_TOOL_FIXTURES replay and record configuration."""

    def test_fixture_replay_is_rejected_outside_explicit_local_test_runtime(self):
        for profile in (None, "production", "staging", "custom"):
            environment = {"AGENT_TOOL_FIXTURES": "/tmp/fixtures"}
            if profile is not None:
                environment["SMARTROUTE_ENV"] = profile
            with self.subTest(profile=profile), patch.dict(os.environ, environment, clear=True):
                with self.assertRaisesRegex(RuntimeError, "Agent tool fixture replay requires"):
                    runtime.validate_mock_safeguards()

    def test_fixture_replay_is_rejected_on_render_even_with_local_profile(self):
        for marker in ("RENDER", "RENDER_SERVICE_ID", "RENDER_EXTERNAL_URL"):
            with self.subTest(marker=marker), patch.dict(
                os.environ,
                {"SMARTROUTE_ENV": "local", marker: "set", "AGENT_TOOL_FIXTURES": "/tmp/fixtures"},
                clear=True,
            ):
                with self.assertRaisesRegex(RuntimeError, "Agent tool fixture replay requires"):
                    runtime.validate_mock_safeguards()

    def test_fixture_record_is_rejected_outside_explicit_local_test_runtime(self):
        for profile in (None, "production", "staging", "custom"):
            for record_value in ("1", "true", "on"):
                environment = {"AGENT_TOOL_FIXTURES_RECORD": record_value}
                if profile is not None:
                    environment["SMARTROUTE_ENV"] = profile
                with self.subTest(profile=profile, record=record_value), patch.dict(
                    os.environ, environment, clear=True
                ):
                    with self.assertRaisesRegex(RuntimeError, "Agent tool fixture recording requires"):
                        runtime.validate_mock_safeguards()

    def test_fixture_record_is_rejected_on_render_even_with_local_profile(self):
        with patch.dict(
            os.environ,
            {
                "SMARTROUTE_ENV": "test",
                "RENDER_SERVICE_ID": "srv-example",
                "AGENT_TOOL_FIXTURES_RECORD": "1",
            },
            clear=True,
        ):
            with self.assertRaisesRegex(RuntimeError, "Agent tool fixture recording requires"):
                runtime.validate_mock_safeguards()

    def test_falsy_record_value_is_not_fixture_recording_configuration(self):
        with patch.dict(
            os.environ,
            {"SMARTROUTE_ENV": "production", "AGENT_TOOL_FIXTURES_RECORD": "0"},
            clear=True,
        ):
            runtime.validate_mock_safeguards()

    def test_local_test_profiles_permit_fixture_replay_and_record(self):
        for profile in ("local", "development", "dev", "test", "testing"):
            with self.subTest(profile=profile), patch.dict(
                os.environ,
                {
                    "APP_ENV": profile,
                    "AGENT_TOOL_FIXTURES": "/tmp/fixtures",
                    "AGENT_TOOL_FIXTURES_RECORD": "1",
                },
                clear=True,
            ):
                runtime.validate_mock_safeguards()
                self.assertEqual(runtime.runtime_mode_label(), "local_test")

    def test_fixture_guard_error_never_includes_fixture_path_or_values(self):
        fixture_path = "/tmp/smartroute-secret-fixtures"
        with patch.dict(
            os.environ,
            {"SMARTROUTE_ENV": "production", "AGENT_TOOL_FIXTURES": fixture_path},
            clear=True,
        ):
            with self.assertRaises(RuntimeError) as raised:
                runtime.validate_mock_safeguards()
            self.assertNotIn(fixture_path, str(raised.exception))

        with patch.dict(
            os.environ,
            {"SMARTROUTE_ENV": "staging", "AGENT_TOOL_FIXTURES_RECORD": "yes"},
            clear=True,
        ):
            with self.assertRaises(RuntimeError) as raised:
                runtime.validate_mock_safeguards()
            self.assertNotIn("staging", str(raised.exception))
            self.assertNotIn("yes", str(raised.exception))
