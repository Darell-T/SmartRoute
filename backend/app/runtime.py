"""Small, shared runtime-profile guardrails for deployment-only behavior."""

from __future__ import annotations

import os


_PRODUCTION_PROFILES = frozenset({"production", "prod"})
_LOCAL_TEST_PROFILES = frozenset({"local", "development", "dev", "test", "testing"})
_TRUE_VALUES = frozenset({"1", "true", "yes", "on"})


def runtime_profile() -> str:
    """Return the configured profile using the established environment order."""

    for name in ("SMARTROUTE_ENV", "APP_ENV", "ENVIRONMENT"):
        value = os.getenv(name, "").strip().casefold()
        if value:
            return value
    return ""


def is_production() -> bool:
    return runtime_profile() in _PRODUCTION_PROFILES


def is_render_runtime() -> bool:
    """Recognize Render's deployment markers independently of profile text."""

    return any(
        os.getenv(name, "").strip()
        for name in ("RENDER", "RENDER_SERVICE_ID", "RENDER_EXTERNAL_URL")
    )


def allows_mock_modes() -> bool:
    """Mocks are opt-in only for an explicit local or test process."""

    return runtime_profile() in _LOCAL_TEST_PROFILES and not is_render_runtime()


def enabled(name: str) -> bool:
    return os.getenv(name, "").strip().casefold() in _TRUE_VALUES


def runtime_mode_label() -> str:
    """A diagnostic-safe profile label; never returns raw environment values."""

    if is_production() or is_render_runtime():
        return "production"
    if runtime_profile() in _LOCAL_TEST_PROFILES:
        return "local_test"
    return "unknown"


def validate_mock_safeguards() -> None:
    """Reject local-only runtime modes in a production process before serving."""

    local_modes_allowed = allows_mock_modes()
    if any(enabled(name) for name in ("AGENT_MOCK_MODE", "JARVIS_MOCK_ADVISOR")) and not local_modes_allowed:
        raise RuntimeError("Mock agent modes require an explicit local or test runtime profile")
    if enabled("AGENT_ALLOW_MEMORY_SESSIONS") and not local_modes_allowed:
        raise RuntimeError(
            "In-memory agent sessions require an explicit local or test runtime profile"
        )
    if os.getenv("AGENT_TOOL_FIXTURES", "").strip() and not local_modes_allowed:
        raise RuntimeError("Agent tool fixture replay requires an explicit local or test runtime profile")
    if enabled("AGENT_TOOL_FIXTURES_RECORD") and not local_modes_allowed:
        raise RuntimeError("Agent tool fixture recording requires an explicit local or test runtime profile")
