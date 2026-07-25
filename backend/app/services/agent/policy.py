"""Central response-mode policy for the conversational SmartRoute agent.

Auto and Quick share one orchestration/tool pipeline. This module owns the
only differences allowed between them: conversational model, candidate and
output budgets, retry budget, and optional enrichment depth.
"""

from __future__ import annotations

import dataclasses
import os
from typing import Literal

ResponseMode = Literal["auto", "quick"]


def _positive_int(name: str, default: int, *, minimum: int = 0) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except ValueError:
        return default
    return max(minimum, value)


def _positive_int_with_legacy(
    name: str,
    legacy_name: str,
    default: int,
    *,
    minimum: int = 0,
) -> int:
    if name in os.environ:
        return _positive_int(name, default, minimum=minimum)
    return _positive_int(legacy_name, default, minimum=minimum)


@dataclasses.dataclass(frozen=True)
class AgentModePolicy:
    mode: ResponseMode
    model: str
    max_route_candidates: int
    retry_count: int
    max_output_tokens: int
    wrapup_output_tokens: int
    max_rounds: int
    explanation_style: str
    optional_enrichment: bool


@dataclasses.dataclass(frozen=True)
class ModelRequestCapabilities:
    supports_manual_thinking: bool
    supports_non_default_sampling: bool
    supports_assistant_prefill: bool


_DEFAULT_REQUEST_CAPABILITIES = ModelRequestCapabilities(
    supports_manual_thinking=True,
    supports_non_default_sampling=True,
    supports_assistant_prefill=True,
)
_SONNET_5_REQUEST_CAPABILITIES = ModelRequestCapabilities(
    supports_manual_thinking=False,
    supports_non_default_sampling=False,
    supports_assistant_prefill=False,
)


def _sonnet_model() -> str:
    # AGENT_MODEL remains a backwards-compatible alias while deployments
    # migrate to the explicit Auto/Sonnet setting.
    return (
        os.getenv("AGENT_AUTO_MODEL", "").strip()
        or os.getenv("AGENT_SONNET_MODEL", "").strip()
        or os.getenv("AGENT_MODEL", "").strip()
        or "claude-sonnet-5"
    )


def _haiku_model() -> str:
    return (
        os.getenv("AGENT_QUICK_MODEL", "").strip()
        or os.getenv("AGENT_HAIKU_MODEL", "").strip()
        or "claude-haiku-4-5-20251001"
    )


def policy_for_mode(value: object) -> AgentModePolicy:
    """Return a safe policy; unknown or missing values fall back to Auto."""

    mode: ResponseMode = "quick" if str(value or "").strip().lower() == "quick" else "auto"
    if mode == "quick":
        return AgentModePolicy(
            mode="quick",
            model=_haiku_model(),
            max_route_candidates=_positive_int("AGENT_QUICK_MAX_ROUTE_CANDIDATES", 2, minimum=1),
            retry_count=_positive_int("AGENT_QUICK_RETRY_COUNT", 1),
            max_output_tokens=_positive_int("AGENT_QUICK_MAX_OUTPUT_TOKENS", 360, minimum=64),
            wrapup_output_tokens=_positive_int("AGENT_QUICK_WRAPUP_TOKENS", 180, minimum=64),
            max_rounds=_positive_int("AGENT_QUICK_MAX_ROUNDS", 4, minimum=1),
            explanation_style="concise",
            optional_enrichment=False,
        )
    return AgentModePolicy(
        mode="auto",
        model=_sonnet_model(),
        max_route_candidates=_positive_int("AGENT_AUTO_MAX_ROUTE_CANDIDATES", 5, minimum=1),
        retry_count=_positive_int("AGENT_AUTO_RETRY_COUNT", 2),
        max_output_tokens=_positive_int_with_legacy(
            "AGENT_AUTO_MAX_OUTPUT_TOKENS",
            "AGENT_MAX_TOKENS_PER_ROUND",
            900,
            minimum=64,
        ),
        wrapup_output_tokens=_positive_int("AGENT_AUTO_WRAPUP_TOKENS", 300, minimum=64),
        max_rounds=_positive_int_with_legacy(
            "AGENT_AUTO_MAX_ROUNDS",
            "AGENT_MAX_ROUNDS",
            5,
            minimum=1,
        ),
        explanation_style="comparative",
        optional_enrichment=True,
    )


def safe_model_label(model: str) -> str:
    """Bound model telemetry and strip characters that could forge log fields."""

    return "".join(char for char in str(model) if char.isalnum() or char in "._-")[:96] or "unconfigured"


def request_capabilities(model: str) -> ModelRequestCapabilities:
    """Return the request contract for a configured Claude model.

    Model-specific API behavior belongs here rather than in the agent loop.
    Unknown/private model IDs retain the legacy request surface so a custom
    deployment is not silently downgraded.
    """

    if safe_model_label(model).casefold() == "claude-sonnet-5":
        return _SONNET_5_REQUEST_CAPABILITIES
    return _DEFAULT_REQUEST_CAPABILITIES


def validate_agent_configuration() -> None:
    """Fail startup on unsafe or incomplete enabled-agent configuration."""

    if os.getenv("NEXT_PUBLIC_ANTHROPIC_API_KEY"):
        raise RuntimeError("Anthropic credentials must remain server-only")
    if os.getenv("AGENT_ENABLED", "1").strip() == "0":
        return
    if not os.getenv("ANTHROPIC_API_KEY", "").strip():
        raise RuntimeError(
            "ANTHROPIC_API_KEY is required when the conversational agent is enabled"
        )
    for mode in ("auto", "quick"):
        configured = policy_for_mode(mode)
        if safe_model_label(configured.model) == "unconfigured":
            raise RuntimeError(f"Agent {mode} model is not configured")
