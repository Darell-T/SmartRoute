"""Model tool for declaring rider outcomes before capability execution."""

from __future__ import annotations

from typing import Any

from app.services.agent.tools._types import ToolContext, ToolResult
from app.services.agent.turn.contract import (
    ContractValidationError,
    GoalKind,
    TurnContract,
)

DECLARE_GOALS_SCHEMA = {
    "name": "declare_goals",
    "description": (
        "Declare the rider outcomes this turn must deliver. Use short unique "
        "goal keys and outcome kinds, not tool or provider names. At most six."
    ),
    "strict": True,
    "input_schema": {
        "type": "object",
        "properties": {
            "goals": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "goal_key": {
                            "type": "string",
                            "description": "Short unique key for this outcome.",
                        },
                        "kind": {
                            "type": "string",
                            "enum": [kind.value for kind in GoalKind],
                            "description": (
                                "The rider-facing outcome kind. Use "
                                "place_recommendation when the rider wants a "
                                "shortlist to choose from. Use destination_selection "
                                "when SmartRoute should choose one verified place for "
                                "a dependent action such as routing. Use route for the "
                                "canonical trip outcome."
                            ),
                        },
                        "depends_on": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Goal keys whose evidence is needed first.",
                        },
                    },
                    "required": ["goal_key", "kind", "depends_on"],
                    "additionalProperties": False,
                },
            }
        },
        "required": ["goals"],
        "additionalProperties": False,
    },
}


async def execute(tool_input: dict[str, Any], ctx: ToolContext | None = None) -> ToolResult:
    """Validate and return a typed contract without emitting rider prose."""

    try:
        contract = TurnContract.from_payload(tool_input)
    except (ContractValidationError, TypeError, ValueError) as exc:
        return ToolResult(
            ok=False,
            error=str(exc),
            internal_diagnostic=True,
        )
    if ctx is not None:
        evidence = getattr(ctx, "turn_evidence", None)
        bind_contract = getattr(evidence, "bind_contract", None)
        if callable(bind_contract):
            bind_contract(contract)
    return ToolResult(ok=True, data=contract, events=[])


__all__ = ["DECLARE_GOALS_SCHEMA", "execute"]
