"""State-based public capability-surface tests.

The model owns semantic interpretation. These tests therefore do not assert
that a phrase maps to a backend intent or that a phrase changes the tool
surface. They assert only the stable public vocabulary and the state-valid
subset after the model has declared typed rider outcomes.
"""

from __future__ import annotations

import unittest

from app.services.agent import loop, public_surface
from app.services.agent.tools import INTERNAL_TOOL_REGISTRY, TOOL_REGISTRY
from app.services.agent.turn.contract import GoalState, TurnContract
from app.services.agent.turn.evidence import TurnEvidence


def _evidence_for(*goals: tuple[str, str, tuple[str, ...]], states=None) -> TurnEvidence:
    contract = TurnContract.from_payload(
        {
            "goals": [
                {
                    "goal_key": key,
                    "kind": kind,
                    "depends_on": list(depends_on),
                }
                for key, kind, depends_on in goals
            ]
        }
    )
    evidence = TurnEvidence()
    evidence.bind_contract(contract)
    for key, state in (states or {}).items():
        evidence.record_goal(
            key,
            state,
            attempted=state
            in {
                GoalState.IN_FLIGHT,
                GoalState.EVIDENCE_READY,
                GoalState.SATISFIED,
                GoalState.ATTEMPTED_BUT_UNAVAILABLE,
            },
            presented=state == GoalState.SATISFIED,
        )
    return evidence


def _tool_names(message: str = "", *, evidence: TurnEvidence | None = None) -> set[str]:
    del message
    schemas = loop._tools_for_state(turn_evidence=evidence)
    return {schema["name"] for schema in schemas}


_INITIAL = set(public_surface.INITIAL_TOOL_NAMES)


class PublicCapabilitySurfaceTests(unittest.TestCase):
    def test_initial_surface_is_state_based_and_provider_safe(self):
        for message in (
            "Are there delays on the Q?",
            "Is hopping on the Q a smart move right now?",
            "Find ramen and route me there by subway.",
            "What about Manhattan?",
            "How about the second one?",
            "Tell me a joke.",
        ):
            with self.subTest(message=message):
                schemas = loop._tools_for_state()
                assert {schema["name"] for schema in schemas} == _INITIAL
                assert all("strict" not in schema for schema in schemas)
                assert public_surface.optional_parameter_count(schemas) == 0

    def test_full_public_vocabulary_is_registered_but_presenters_are_state_gated(self):
        offered = public_surface.offered_custom_tools(
            spec.schema for spec in TOOL_REGISTRY.values()
        )
        assert {schema["name"] for schema in offered} == set(public_surface.PUBLIC_TOOL_NAMES)
        assert all("strict" not in schema for schema in offered)
        assert _tool_names() == _INITIAL

    def test_pending_goals_offer_only_their_capability(self):
        cases = (
            ("place_recommendation", "discover_places"),
            ("service_status", "check_transit"),
        )
        for kind, capability in cases:
            with self.subTest(kind=kind):
                evidence = _evidence_for(("goal", kind, ()))
                assert _tool_names(evidence=evidence) == {"complete_turn", capability}

        route_evidence = _evidence_for(("goal", "route", ()))
        assert _tool_names(evidence=route_evidence) == {"complete_turn", "discover_places", "prepare_route_options"}

    def test_ready_evidence_offers_only_the_matching_presenter(self):
        cases = (
            ("place_recommendation", "present_places"),
            ("service_status", "present_transit"),
            ("route", "present_route"),
        )
        for kind, presenter in cases:
            with self.subTest(kind=kind):
                evidence = _evidence_for(
                    ("goal", kind, ()),
                    states={"goal": GoalState.EVIDENCE_READY},
                )
                assert _tool_names(evidence=evidence) == {"complete_turn", presenter}

    def test_dependency_state_blocks_route_until_discovery_evidence_is_ready(self):
        evidence = _evidence_for(
            ("destination", "destination_selection", ()),
            ("route", "route", ("destination",)),
        )
        assert _tool_names(evidence=evidence) == {"complete_turn", "discover_places"}

        evidence.record_goal("destination", GoalState.EVIDENCE_READY, attempted=True)
        assert _tool_names(evidence=evidence) == {"complete_turn", "prepare_route_options"}

    def test_registered_but_unoffered_leaf_tools_stay_internal(self):
        offered = _tool_names()
        assert "prepare_route_options" in TOOL_REGISTRY
        assert "search_local_places" not in INTERNAL_TOOL_REGISTRY
        assert "search_local_places" not in offered
        assert "transit_snapshot" not in offered
        assert "lookup_arrivals" not in offered

    def test_public_surface_does_not_change_for_compound_language(self):
        evidence = _evidence_for(
            ("places", "place_recommendation", ()),
            ("route", "route", ("places",)),
            ("status", "service_status", ()),
        )
        names = _tool_names(
            "Find accessible ramen, route me there by subway, and tell me if the Q is delayed.",
            evidence=evidence,
        )
        assert names == {"complete_turn", "discover_places", "check_transit"}


if __name__ == "__main__":
    unittest.main()
