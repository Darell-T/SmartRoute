from __future__ import annotations

import unittest

import pytest
from app.services.agent.turn.contract import (
    ContractValidationError,
    GoalKind,
    GoalState,
    OutcomeGoal,
    TurnContract,
)


class TurnContractTests(unittest.TestCase):
    def test_validates_unique_acyclic_outcome_goals(self) -> None:
        contract = TurnContract(
            (
                OutcomeGoal("destination", GoalKind.DESTINATION_SELECTION),
                OutcomeGoal("route", GoalKind.ROUTE, ("destination",)),
            )
        )

        assert contract.goal("route").depends_on == ("destination",)
        assert contract.ready_goal_keys() == ("destination",)
        assert contract.ready_goal_keys({"destination": GoalState.EVIDENCE_READY}) == ("route",)

    def test_rejects_duplicate_unknown_and_cyclic_dependencies(self) -> None:
        with pytest.raises(ContractValidationError):
            TurnContract(
                (
                    OutcomeGoal("route", GoalKind.ROUTE),
                    OutcomeGoal("ROUTE", GoalKind.ROUTE),
                )
            )
        with pytest.raises(ContractValidationError):
            TurnContract((OutcomeGoal("route", GoalKind.ROUTE, ("missing",)),))
        with pytest.raises(ContractValidationError):
            TurnContract(
                (
                    OutcomeGoal("a", GoalKind.GENERAL_RESPONSE, ("b",)),
                    OutcomeGoal("b", GoalKind.GENERAL_RESPONSE, ("a",)),
                )
            )

    def test_contract_is_immutable_and_limited_to_six_goals(self) -> None:
        goals = tuple(
            OutcomeGoal(str(index), GoalKind.GENERAL_RESPONSE) for index in range(6)
        )
        contract = TurnContract(goals)
        with pytest.raises(AttributeError):
            contract.goals = ()
        with pytest.raises(ContractValidationError):
            TurnContract((*goals, OutcomeGoal("sixth-plus-one", GoalKind.ROUTE)))


if __name__ == "__main__":
    unittest.main()
