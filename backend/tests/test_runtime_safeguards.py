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
