"""Immutable declarations for the outcomes of one agent turn.

The model declares rider outcomes, never provider or capability names. The
backend records execution state elsewhere and uses the lookup helpers here to
decide which declared outcomes are ready to work on.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Mapping, Sequence
from enum import Enum
from typing import Any

MAX_GOALS = 6
MAX_GOAL_KEY_LENGTH = 64


class ContractValidationError(ValueError):
    """Raised when a model-declared outcome contract is invalid."""


class GoalKind(str, Enum):
    PLACE_RECOMMENDATION = "place_recommendation"
    DESTINATION_SELECTION = "destination_selection"
    ROUTE = "route"
    SERVICE_STATUS = "service_status"
    ARRIVALS = "arrivals"
    ACCESSIBILITY = "accessibility"
    TRANSIT_FACT = "transit_fact"
    AREA_CONDITIONS = "area_conditions"
    EVENT_OR_CROWD = "event_or_crowd"
    GENERAL_RESPONSE = "general_response"


class GoalState(str, Enum):
    PENDING = "pending"
    IN_FLIGHT = "in_flight"
    EVIDENCE_READY = "evidence_ready"
    SATISFIED = "satisfied"
    BLOCKED_WAITING_FOR_RIDER = "blocked_waiting_for_rider"
    ATTEMPTED_BUT_UNAVAILABLE = "attempted_but_unavailable"
    UNSUPPORTED = "unsupported"
    CANCELLED_BY_RIDER = "cancelled_by_rider"
    SUPERSEDED = "superseded"


def _key(value: object, field: str = "goal_key") -> str:
    if not isinstance(value, str):
        raise ContractValidationError(f"{field} must be a string")
    value = value.strip()
    if not value:
        raise ContractValidationError(f"{field} must not be empty")
    if len(value) > MAX_GOAL_KEY_LENGTH:
        raise ContractValidationError(
            f"{field} must be at most {MAX_GOAL_KEY_LENGTH} characters"
        )
    if any(ord(char) < 32 for char in value):
        raise ContractValidationError(f"{field} contains a control character")
    return value


@dataclasses.dataclass(frozen=True, slots=True)
class OutcomeGoal:
    goal_key: str
    kind: GoalKind
    depends_on: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "goal_key", _key(self.goal_key))
        try:
            kind = self.kind if isinstance(self.kind, GoalKind) else GoalKind(self.kind)
        except (TypeError, ValueError) as exc:
            raise ContractValidationError(f"unknown goal kind: {self.kind!r}") from exc
        object.__setattr__(self, "kind", kind)
        if self.depends_on is None:
            dependencies: tuple[str, ...] = ()
        elif isinstance(self.depends_on, (str, bytes)) or not isinstance(
            self.depends_on, Sequence
        ):
            raise ContractValidationError("depends_on must be an array of strings")
        else:
            dependencies = tuple(_key(value, "depends_on") for value in self.depends_on)
        if len({value.casefold() for value in dependencies}) != len(dependencies):
            raise ContractValidationError("depends_on contains duplicate keys")
        object.__setattr__(self, "depends_on", dependencies)


@dataclasses.dataclass(frozen=True, slots=True)
class TurnContract:
    """Immutable, validated model declaration for a single turn."""

    goals: tuple[OutcomeGoal, ...]

    def __post_init__(self) -> None:
        if isinstance(self.goals, (str, bytes)) or not isinstance(self.goals, Sequence):
            raise ContractValidationError("goals must be an array")
        if not self.goals:
            raise ContractValidationError("at least one goal is required")
        if len(self.goals) > MAX_GOALS:
            raise ContractValidationError(f"at most {MAX_GOALS} goals are allowed")
        try:
            goals = tuple(
                goal if isinstance(goal, OutcomeGoal) else OutcomeGoal(**goal)
                for goal in self.goals
            )
        except (TypeError, ValueError) as exc:
            raise ContractValidationError("each goal needs key, kind, depends_on") from exc
        folded = [goal.goal_key.casefold() for goal in goals]
        if len(set(folded)) != len(folded):
            raise ContractValidationError("goal_key values must be unique")
        keys = {goal.goal_key for goal in goals}
        for goal in goals:
            unknown = set(goal.depends_on) - keys
            if unknown:
                raise ContractValidationError(
                    f"goal {goal.goal_key!r} depends on unknown goal"
                )
        visiting: set[str] = set()
        visited: set[str] = set()
        by_key = {goal.goal_key: goal for goal in goals}

        def visit(key: str) -> None:
            if key in visiting:
                raise ContractValidationError("goal dependencies must be acyclic")
            if key in visited:
                return
            visiting.add(key)
            for dependency in by_key[key].depends_on:
                visit(dependency)
            visiting.remove(key)
            visited.add(key)

        for goal in goals:
            visit(goal.goal_key)
        object.__setattr__(self, "goals", goals)

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> TurnContract:
        if not isinstance(payload, Mapping) or set(payload) != {"goals"}:
            raise ContractValidationError("payload must contain only goals")
        raw_goals = payload["goals"]
        if isinstance(raw_goals, (str, bytes)) or not isinstance(raw_goals, Sequence):
            raise ContractValidationError("goals must be an array")
        try:
            goals = tuple(OutcomeGoal(**goal) for goal in raw_goals)
        except (TypeError, ValueError) as exc:
            raise ContractValidationError("each goal needs key, kind, depends_on") from exc
        return cls(goals)

    def to_payload(self) -> dict[str, Any]:
        return {
            "goals": [
                {
                    "goal_key": goal.goal_key,
                    "kind": goal.kind.value,
                    "depends_on": list(goal.depends_on),
                }
                for goal in self.goals
            ]
        }

    def goal(self, goal_key: str) -> OutcomeGoal:
        for goal in self.goals:
            if goal.goal_key == goal_key:
                return goal
        raise KeyError(goal_key)

    def get_goal(self, goal_key: str) -> OutcomeGoal | None:
        try:
            return self.goal(goal_key)
        except KeyError:
            return None

    def __contains__(self, goal_key: object) -> bool:
        return any(goal.goal_key == goal_key for goal in self.goals)

    def dependencies_for(self, goal_key: str) -> tuple[str, ...]:
        return self.goal(goal_key).depends_on

    def route_allows_internal_discovery(self, goal_key: str) -> bool:
        """Whether a route may discover its destination before routing."""

        route_goal = self.get_goal(goal_key)
        if route_goal is None or route_goal.kind != GoalKind.ROUTE:
            return False
        for dependency_key in route_goal.depends_on:
            dependency = self.get_goal(dependency_key)
            if (
                dependency is not None
                and dependency.kind == GoalKind.DESTINATION_SELECTION
            ):
                return False
        return True

    @staticmethod
    def _status_for(statuses: object, key: str) -> GoalState:
        raw = statuses.get(key) if isinstance(statuses, Mapping) else None
        if raw is None and statuses is not None:
            method = getattr(statuses, "state_for", None) or getattr(
                statuses, "status_for", None
            )
            if callable(method):
                raw = method(key)
        raw = getattr(raw, "state", raw)
        if raw is None:
            return GoalState.PENDING
        if isinstance(raw, Mapping):
            raw = raw.get("state")
            if raw is None:
                return GoalState.PENDING
        try:
            return raw if isinstance(raw, GoalState) else GoalState(str(raw))
        except ValueError as exc:
            raise ContractValidationError(f"unknown state for {key!r}") from exc

    def dependencies_ready(self, goal_key: str, statuses: object = None) -> bool:
        return all(
            self._status_for(statuses, dependency)
            in {GoalState.EVIDENCE_READY, GoalState.SATISFIED}
            for dependency in self.dependencies_for(goal_key)
        )

    def ready_goal_keys(self, statuses: object = None) -> tuple[str, ...]:
        return tuple(
            goal.goal_key
            for goal in self.goals
            if self._status_for(statuses, goal.goal_key) == GoalState.PENDING
            and self.dependencies_ready(goal.goal_key, statuses)
        )

    def dependency_blockers(self, goal_key: str, statuses: object = None) -> tuple[str, ...]:
        return tuple(
            dependency
            for dependency in self.dependencies_for(goal_key)
            if self._status_for(statuses, dependency)
            not in {GoalState.EVIDENCE_READY, GoalState.SATISFIED}
        )


__all__ = [
    "MAX_GOALS",
    "MAX_GOAL_KEY_LENGTH",
    "ContractValidationError",
    "GoalKind",
    "GoalState",
    "OutcomeGoal",
    "TurnContract",
]
