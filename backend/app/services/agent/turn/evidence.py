"""Turn-local evidence, obligations, and terminal enforcement.

Capability results update this state. It is not a pre-model intent router.
"""

from __future__ import annotations

import dataclasses
from typing import Any, Literal

from app.services.agent.turn.contract import GoalKind, GoalState, TurnContract

TerminalPath = Literal[
    "complete_turn",
    "present_places",
    "present_transit",
    "present_route",
    "deterministic_fallback",
    "truthful_failure",
    "",
]
SelectionSource = Literal["model", "deterministic_fallback", ""]


@dataclasses.dataclass
class TurnEvidence:
    discovery_set_id: str | None = None
    verified_place_count: int = 0
    route_candidate_count: int = 0
    transit_evidence: bool = False
    web_available: bool = False
    web_used: bool = False
    web_disabled: bool = False
    web_succeeded: bool = False
    web_research_required: bool = False
    discover_operation: str = ""
    structured_place_search_attempted: bool = False
    terminal: bool = False
    terminal_path: TerminalPath = ""
    selection_source: SelectionSource = ""
    prose_without_tool_rounds: int = 0
    capability_surface_version: str = "model_led_goals_v2"
    # Goal execution facts are deliberately separate from provider summaries.
    # They contain status metadata only, never provider payloads or
    # rider-language interpretation.
    goal_states: dict[str, GoalState] = dataclasses.field(default_factory=dict)
    goal_attempted: set[str] = dataclasses.field(default_factory=set)
    goal_presented: set[str] = dataclasses.field(default_factory=set)
    goal_recovery_options: dict[str, tuple[str, ...]] = dataclasses.field(
        default_factory=dict
    )
    goal_handles: dict[str, str] = dataclasses.field(default_factory=dict)
    turn_contract: TurnContract | None = None
    goal_transitions: list[dict[str, object]] = dataclasses.field(default_factory=list)

    def bind_contract(self, contract: TurnContract) -> None:
        if not isinstance(contract, TurnContract):
            raise TypeError("contract must be a TurnContract")
        if self.turn_contract is not None:
            raise ValueError("turn goals have already been declared")
        self.turn_contract = contract

    def record_goal(
        self,
        goal_key: str,
        state: GoalState | str,
        *,
        attempted: bool | None = None,
        presented: bool | None = None,
        approved_recovery_options: tuple[str, ...] = (),
    ) -> None:
        """Record backend execution facts for one declared goal."""

        key = str(goal_key or "").strip()
        if not key:
            raise ValueError("goal_key is required")
        if self.turn_contract is not None and key not in self.turn_contract:
            raise ValueError("goal_key is not declared for this turn")
        normalized = state if isinstance(state, GoalState) else GoalState(str(state))
        previous = self.goal_states.get(key, GoalState.PENDING)
        self.goal_states[key] = normalized
        if attempted is True:
            self.goal_attempted.add(key)
        elif attempted is False:
            self.goal_attempted.discard(key)
        if presented is True:
            self.goal_presented.add(key)
        elif presented is False:
            self.goal_presented.discard(key)
        options = tuple(
            option.strip()
            for option in approved_recovery_options
            if isinstance(option, str) and option.strip()
        )
        self.goal_recovery_options[key] = tuple(dict.fromkeys(options))
        if previous != normalized:
            self.goal_transitions.append(
                {
                    "goal_key": key,
                    "old_state": previous.value,
                    "new_state": normalized.value,
                    "attempted": key in self.goal_attempted,
                    "presented": key in self.goal_presented,
                }
            )

    def record_goal_handle(self, goal_key: str, handle: object) -> None:
        """Associate one opaque server-owned evidence handle with a goal."""

        key = str(goal_key or "").strip()
        value = str(handle or "").strip()
        if self.turn_contract is None or key not in self.turn_contract:
            raise ValueError("goal_key is not declared for this turn")
        if not value:
            raise ValueError("evidence handle is required")
        self.goal_handles[key] = value

    def handle_for(self, goal_key: str) -> str | None:
        return self.goal_handles.get(str(goal_key))

    def state_for(self, goal_key: str) -> GoalState:
        return self.goal_states.get(str(goal_key), GoalState.PENDING)

    def attempted_for(self, goal_key: str) -> bool:
        return str(goal_key) in self.goal_attempted

    def presented_for(self, goal_key: str) -> bool:
        return str(goal_key) in self.goal_presented

    def recovery_options_for(self, goal_key: str) -> tuple[str, ...]:
        return self.goal_recovery_options.get(str(goal_key), ())

    def note_discover_places(
        self,
        *,
        ok: bool,
        discovery_set_id: str | None,
        place_count: int,
        operation: str = "search",
    ) -> None:
        normalized_operation = str(operation or "search").strip().casefold()
        if normalized_operation in {"search", "verify"}:
            self.structured_place_search_attempted = True
        count = max(0, int(place_count)) if ok else 0
        had_usable_results = bool(self.discovery_set_id and self.verified_place_count)
        if not ok or count <= 0 or not discovery_set_id:
            if not had_usable_results:
                self.discovery_set_id = None
                self.verified_place_count = 0
                self.discover_operation = normalized_operation
                self.web_available = self.may_offer_web()
            return
        self.discovery_set_id = discovery_set_id
        self.verified_place_count = count
        self.discover_operation = normalized_operation
        self.web_research_required = normalized_operation == "verify"
        self.web_available = self.may_offer_web()

    def may_offer_web(self) -> bool:
        route_declared = bool(
            self.turn_contract
            and any(goal.kind == GoalKind.ROUTE for goal in self.turn_contract.goals)
        )
        return (
            not self.web_used
            and not self.web_disabled
            and self.structured_place_search_attempted
            and (
                not route_declared
                or self.verified_place_count == 0
                or not self.discovery_set_id
            )
        )

    def note_prepare_route_options(
        self,
        *,
        ok: bool,
        candidate_count: int,
        presentation_allowed: bool = True,
    ) -> None:
        # Route preparation closes the optional web-research recovery pass.
        self.disable_web()
        count = max(0, int(candidate_count)) if ok and presentation_allowed else 0
        had_usable_routes = self.route_candidate_count > 0
        if not ok or count <= 0:
            if not had_usable_routes:
                self.route_candidate_count = 0
            return
        self.route_candidate_count = count

    def note_check_transit(
        self,
        *,
        ok: bool,
        operation: str = "",
        data: object = None,
    ) -> None:
        if ok:
            self.transit_evidence = True

    def note_web(self, *, ok: bool) -> None:
        self.web_used = True
        self.web_available = False
        self.web_disabled = True
        self.web_succeeded = bool(ok)
        # Keep the research obligation after success as well as failure. The
        # presenter uses it to require research-backed detail instead of
        # silently falling back to the already-presented place list.

    def disable_web(self) -> None:
        self.web_available = False
        self.web_disabled = True

    def can_claim_research_used(self) -> bool:
        return self.web_succeeded

    def mark_terminal(
        self,
        path: TerminalPath,
        *,
        selection_source: SelectionSource = "",
    ) -> None:
        self.terminal = True
        self.terminal_path = path
        if selection_source:
            self.selection_source = selection_source

    def record_capability_result(
        self,
        name: str,
        tool_input: dict,
        result: Any,
    ) -> None:
        """Record one capability result as goal-state transitions."""
        ok = bool(getattr(result, "ok", False))
        data = getattr(result, "data", None)
        payload = data if isinstance(data, dict) else {}
        goal_key = str((tool_input or {}).get("goal_key") or "").strip()
        if name == "discover_places":
            self._record_discovery_result(goal_key, payload, ok=ok)
            return
        if name == "prepare_route_options":
            self._record_route_preparation_result(
                goal_key,
                tool_input or {},
                payload,
                ok=ok,
            )
            return
        if name == "check_transit":
            self._record_transit_result(
                goal_key,
                tool_input or {},
                payload,
                result,
                ok=ok,
            )
            return
        self._record_generic_capability_result(
            name,
            goal_key,
            payload,
            result,
            ok=ok,
        )

    def _record_generic_capability_result(
        self,
        name: str,
        goal_key: str,
        payload: dict[str, Any],
        result: Any,
        *,
        ok: bool,
    ) -> None:
        """Record generic research, presentation, and terminal outcomes."""
        if name == "web_search":
            self.note_web(ok=ok)
            return
        if name in {"present_places", "present_transit", "present_route"} and ok:
            self._record_presentation_result(name, goal_key, payload)
            return
        if getattr(result, "terminal", False):
            path = str(getattr(result, "terminal_path", "") or name)
            if path == "complete_turn":
                self.mark_terminal(path)

    def _record_discovery_result(
        self, goal_key: str, payload: dict[str, Any], *, ok: bool
    ) -> None:
        places = payload.get("places") if ok else []
        has_places = bool(places)
        self.note_discover_places(
            ok=ok and has_places,
            discovery_set_id=str(payload.get("discovery_set_id") or "") or None,
            place_count=len(places) if isinstance(places, list) else 0,
            operation=str(payload.get("operation") or "search"),
        )
        discovery_handle = str(payload.get("discovery_set_id") or "").strip()
        route_discovery_ready = (
            ok
            and has_places
            and bool(discovery_handle)
            and self.turn_contract is not None
            and self.turn_contract.route_allows_internal_discovery(goal_key)
        )
        if route_discovery_ready:
            self.goal_handles.pop(goal_key, None)
            self.record_goal(
                goal_key,
                GoalState.PENDING,
                attempted=True,
                presented=False,
            )
            return
        self._record_capability_goal(
            goal_key,
            ok=ok and has_places,
            handle=payload.get("discovery_set_id"),
            recovery="I can retry with a different place query or search area.",
        )

    def _record_route_preparation_result(
        self,
        goal_key: str,
        tool_input: dict[str, Any],
        payload: dict[str, Any],
        *,
        ok: bool,
    ) -> None:
        candidates = payload.get("candidates") or payload.get("options") or []
        count = len(candidates) if isinstance(candidates, list) else int(ok)
        presentation_allowed = payload.get("presentation_allowed") is not False
        presentable = ok and count > 0 and presentation_allowed
        self.note_prepare_route_options(
            ok=ok,
            candidate_count=count if ok else 0,
            presentation_allowed=presentation_allowed,
        )
        self._record_capability_goal(
            goal_key,
            ok=presentable,
            handle=payload.get("candidate_set_id"),
            recovery="I can retry with different route constraints or endpoints.",
        )
        if presentable and str(tool_input.get("destination_place_id") or "").strip():
            self._satisfy_consumed_destination_dependency(goal_key)

    def _record_transit_result(
        self,
        goal_key: str,
        tool_input: dict[str, Any],
        payload: dict[str, Any],
        result: Any,
        *,
        ok: bool,
    ) -> None:
        from app.services.agent.tools.transit import check_transit

        operation = str(tool_input.get("operation") or "")
        grounded = bool(
            getattr(result, "evidence_ready", ok)
            and check_transit.grounding_succeeded(operation, payload, ok=ok)
        )
        self.note_check_transit(ok=grounded, operation=operation, data=payload)
        self._record_capability_goal(
            goal_key,
            ok=grounded and bool(payload.get("evidence_set_id")),
            handle=payload.get("evidence_set_id"),
            recovery="I can retry when live transit data is available.",
        )

    def _record_presentation_result(
        self, name: str, goal_key: str, payload: dict[str, Any]
    ) -> None:
        if name == "present_route":
            source = str(payload.get("selection_source") or "").strip()
            if source in {"model", "deterministic_fallback"}:
                self.selection_source = source
            self._satisfy_consumed_destination_dependency(goal_key)
        if goal_key:
            self.record_goal(
                goal_key,
                GoalState.SATISFIED,
                attempted=True,
                presented=True,
            )

    def _record_capability_goal(
        self,
        goal_key: str,
        *,
        ok: bool,
        handle: object,
        recovery: str,
    ) -> None:
        if not goal_key or self.turn_contract is None:
            return
        if ok and str(handle or "").strip():
            self.record_goal_handle(goal_key, handle)
            self.record_goal(goal_key, GoalState.EVIDENCE_READY, attempted=True)
            return
        self.record_goal(
            goal_key,
            GoalState.ATTEMPTED_BUT_UNAVAILABLE,
            attempted=True,
            approved_recovery_options=(recovery,),
        )

    def _satisfy_consumed_destination_dependency(self, route_goal_key: str) -> None:
        """Resolve a delegated place choice once canonical routing consumes it."""
        contract = self.turn_contract
        route_goal = contract.get_goal(route_goal_key) if contract is not None else None
        if route_goal is None or route_goal.kind != GoalKind.ROUTE:
            return
        for dependency_key in route_goal.depends_on:
            dependency = contract.get_goal(dependency_key)
            if (
                dependency is not None
                and dependency.kind == GoalKind.DESTINATION_SELECTION
                and self.state_for(dependency_key) == GoalState.EVIDENCE_READY
                and self.handle_for(dependency_key)
            ):
                self.record_goal(dependency_key, GoalState.SATISFIED, attempted=True)

    def telemetry(self) -> dict[str, object]:
        result: dict[str, object] = {
            "capability_surface_version": self.capability_surface_version,
            "native_web_uses": int(self.web_used),
            "terminal_path": self.terminal_path or "none",
            "selection_source": self.selection_source or "none",
        }
        if self.turn_contract is not None:
            result.update(
                {
                    "turn_contract": self.turn_contract.to_payload(),
                    "goal_states": {
                        goal.goal_key: {
                            "kind": goal.kind.value,
                            "depends_on": list(goal.depends_on),
                            "state": self.state_for(goal.goal_key).value,
                            "attempted": self.attempted_for(goal.goal_key),
                            "presented": self.presented_for(goal.goal_key),
                            "evidence_handle": self.handle_for(goal.goal_key),
                        }
                        for goal in self.turn_contract.goals
                    },
                }
            )
        return result
