"""Central response-mode policy for the conversational SmartRoute agent.

Auto and Quick share one Sonnet model and the same state-scoped capability
contract. This module owns the only differences allowed between them:
candidate, place, output, web, and retry budgets.
"""

from __future__ import annotations

import dataclasses
import os
from typing import Literal

from app import runtime

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
    max_presented_places: int
    retry_count: int
    max_output_tokens: int
    output_effort: str
    max_rounds: int
    explanation_style: str
    optional_enrichment: bool
    web_research_timeout_s: float


@dataclasses.dataclass(frozen=True)
class ModelRequestCapabilities:
    supports_manual_thinking: bool
    supports_non_default_sampling: bool
    supports_assistant_prefill: bool
    supports_effort: bool


_DEFAULT_REQUEST_CAPABILITIES = ModelRequestCapabilities(
    supports_manual_thinking=True,
    supports_non_default_sampling=True,
    supports_assistant_prefill=True,
    supports_effort=False,
)
_SONNET_5_REQUEST_CAPABILITIES = ModelRequestCapabilities(
    supports_manual_thinking=False,
    supports_non_default_sampling=False,
    supports_assistant_prefill=False,
    supports_effort=True,
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


def _web_timeout_s(name: str, default: float) -> float:
    try:
        value = float(os.getenv(name, str(default)))
    except ValueError:
        return default
    return max(1.0, min(12.0, value))


def _output_effort(name: str) -> str:
    value = os.getenv(name, "medium").strip().lower()
    return value if value in {"low", "medium", "high", "xhigh", "max"} else "medium"


def policy_for_mode(value: object) -> AgentModePolicy:
    """Return a safe policy; unknown or missing values fall back to Auto."""

    mode: ResponseMode = "quick" if str(value or "").strip().lower() == "quick" else "auto"
    model = _sonnet_model()
    if mode == "quick":
        return AgentModePolicy(
            mode="quick",
            model=model,
            max_route_candidates=_positive_int("AGENT_QUICK_MAX_ROUTE_CANDIDATES", 2, minimum=1),
            max_presented_places=_positive_int("AGENT_QUICK_MAX_PRESENTED_PLACES", 3, minimum=1),
            retry_count=_positive_int("AGENT_QUICK_RETRY_COUNT", 1),
            max_output_tokens=_positive_int("AGENT_QUICK_MAX_OUTPUT_TOKENS", 1024, minimum=64),
            output_effort=_output_effort("AGENT_QUICK_OUTPUT_EFFORT"),
            max_rounds=_positive_int("AGENT_QUICK_MAX_ROUNDS", 4, minimum=1),
            explanation_style="concise",
            optional_enrichment=False,
            web_research_timeout_s=3.0,
        )
    return AgentModePolicy(
        mode="auto",
        model=model,
        max_route_candidates=_positive_int("AGENT_AUTO_MAX_ROUTE_CANDIDATES", 5, minimum=1),
        max_presented_places=_positive_int("AGENT_AUTO_MAX_PRESENTED_PLACES", 5, minimum=1),
        retry_count=_positive_int("AGENT_AUTO_RETRY_COUNT", 1),
        max_output_tokens=_positive_int_with_legacy(
            "AGENT_AUTO_MAX_OUTPUT_TOKENS",
            "AGENT_MAX_TOKENS_PER_ROUND",
            2048,
            minimum=64,
        ),
        output_effort=_output_effort("AGENT_AUTO_OUTPUT_EFFORT"),
        max_rounds=_positive_int_with_legacy(
            "AGENT_AUTO_MAX_ROUNDS",
            "AGENT_MAX_ROUNDS",
            5,
            minimum=1,
        ),
        explanation_style="comparative",
        optional_enrichment=True,
        web_research_timeout_s=_web_timeout_s("AGENT_WEB_RESEARCH_TIMEOUT_S", 6.0),
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

    runtime.validate_mock_safeguards()
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


NO_TOOL_CORRECTION = (
    "Do not write rider-facing prose without a tool. Choose one offered "
    "capability or a terminal tool now."
)
WEB_TIMEOUT_CONTINUATION = (
    "Web research timed out. Continue from the structured discovery evidence "
    "already returned. Do not use web_search again."
)
