"""Deterministic route-intelligence replay support.

The package only loads recorded provider responses and invokes existing
normalization/matching helpers.  It intentionally does not make route choices
or contact upstream providers.
"""

from typing import TYPE_CHECKING, Any

# Keep this package import light. Router unit tests intentionally substitute
# narrow FastAPI/Pydantic stubs; eagerly importing replay would pull the full
# 511NY settings model during unrelated route tests.
if TYPE_CHECKING:
    from .replay import (
        FrozenClock,
        ReplayFixtureAdapters,
        ReplayScenario,
        ScenarioValidationError,
        load_all_scenarios,
        load_scenario,
        network_disabled,
    )

__all__ = [
    "FrozenClock",
    "ReplayFixtureAdapters",
    "ReplayScenario",
    "ScenarioValidationError",
    "load_all_scenarios",
    "load_scenario",
    "network_disabled",
]


def __getattr__(name: str) -> Any:
    if name not in __all__:
        raise AttributeError(name)
    from . import replay

    return getattr(replay, name)
