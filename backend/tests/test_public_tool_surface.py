"""Stable capability vocabulary with state-valid per-round exposure."""

from __future__ import annotations

import copy
import unittest

from app.services.agent import loop, public_surface
from app.services.agent.model import policy
from app.services.agent.tools import assert_strict_tool_schemas_compatible
from app.services.agent.tools import INTERNAL_TOOL_REGISTRY, TOOL_REGISTRY, TOOLS
from app.services.agent.turn.contract import GoalKind, GoalState, OutcomeGoal, TurnContract
from app.services.agent.turn.evidence import TurnEvidence


def _replay_session() -> dict:
    itinerary = {"legs": [{"mode": "SUBWAY", "route_id": "Q"}]}
    card = {
        "card_id": "rc_accepted",
        "role": "recommended",
        "origin": {"label": "Your location"},
        "destination": {"label": "Barclays Center"},
        "summary": {"eta_minutes": 23},
        "route": [{"type": "SUBWAY", "route_id": "Q"}],
        "alerts": [],
        "itinerary": itinerary,
    }
    return {
        "_transcript": {
            "v": 1,
            "history": [],
            "route_cards": [card],
            "arrival_cards": [],
        },
        "active_trip": {
            "card_id": "rc_accepted",
            "canonical_itinerary": copy.deepcopy(itinerary),
        },
        "trip_state": {"selected_candidate_id": "cd_selected"},
    }


class PublicToolSurfaceTests(unittest.TestCase):
    def test_union_counter_recurses_through_nested_schema(self):
        schema = {
            "type": "object",
            "properties": {
                "choice": {"anyOf": [{"type": "string"}, {"type": "null"}]},
                "nested": {"type": ["string", "null"]},
            },
            "items": [{"type": ["integer", "null"]}],
        }

        self.assertEqual(public_surface.schema_union_parameter_count(schema), 3)

    def test_transit_snapshot_progress_label_uses_request_context(self):
        label = INTERNAL_TOOL_REGISTRY["transit_snapshot"].label_fn

        self.assertEqual(label({"near": "Union Square"}), "Checking conditions near Union Square…")
        self.assertEqual(label({"lines": ["Q", "R"]}), "Checking alerts for Q/R…")
        self.assertEqual(label({}), "Checking live transit conditions…")

        arrivals_label = INTERNAL_TOOL_REGISTRY["lookup_arrivals"].label_fn
        self.assertEqual(
            arrivals_label({"route_id": "q", "stop_query": "Church Av"}),
            "Checking Q arrivals at Church Av...",
        )
        self.assertEqual(arrivals_label({}), "Checking YOUR LINE arrivals...")

    def test_initial_surface_is_semantics_neutral_and_compact(self):
        expected = list(public_surface.PUBLIC_TOOL_NAMES)
        self.assertEqual(
            expected,
            [
                "declare_goals",
                "discover_places",
                "check_transit",
                "prepare_route_options",
                "present_places",
                "present_transit",
                "present_route",
                "complete_turn",
            ],
        )
        for mode in ("auto", "quick"):
            with self.subTest(mode=mode):
                tools = loop._tools_for_state(policy.policy_for_mode(mode))
                custom = [
                    tool
                    for tool in tools
                    if tool.get("name") in public_surface.PUBLIC_TOOL_NAMES
                ]
                names = [tool.get("name") for tool in custom]
                self.assertEqual(set(names), set(public_surface.INITIAL_TOOL_NAMES))
                self.assertFalse(
                    any(tool.get("type") == "web_search_20250305" for tool in tools)
                )

    def test_presenters_appear_only_for_ready_server_evidence(self):
        evidence = TurnEvidence()
        evidence.bind_contract(
            TurnContract(
                (
                    OutcomeGoal("places", "place_recommendation"),
                    OutcomeGoal("status", "service_status"),
                    OutcomeGoal("route", "route"),
                )
            )
        )
        self.assertEqual(
            public_surface.state_valid_tool_names(evidence),
            frozenset(
                {
                    "discover_places",
                    "check_transit",
                    "prepare_route_options",
                    "complete_turn",
                }
            ),
        )
        evidence.record_goal("places", GoalState.EVIDENCE_READY, attempted=True)
        evidence.record_goal("status", GoalState.EVIDENCE_READY, attempted=True)
        evidence.record_goal("route", GoalState.EVIDENCE_READY, attempted=True)
        self.assertEqual(
            public_surface.state_valid_tool_names(evidence),
            frozenset(
                {
                    "present_places",
                    "present_transit",
                    "present_route",
                    "complete_turn",
                }
            ),
        )

    def test_failed_dependent_route_exposes_verified_place_for_partial_success(self):
        evidence = TurnEvidence()
        evidence.bind_contract(
            TurnContract(
                (
                    OutcomeGoal("place", "destination_selection"),
                    OutcomeGoal("route", "route", depends_on=("place",)),
                )
            )
        )
        evidence.record_goal("place", GoalState.EVIDENCE_READY, attempted=True)

        before_failure = public_surface.state_valid_tool_names(evidence)
        self.assertNotIn("present_places", before_failure)
        self.assertIn("prepare_route_options", before_failure)

        evidence.record_goal(
            "route",
            GoalState.ATTEMPTED_BUT_UNAVAILABLE,
            attempted=True,
        )
        after_failure = public_surface.state_valid_tool_names(evidence)
        self.assertIn("present_places", after_failure)
        self.assertIn("prepare_route_options", after_failure)
        self.assertIn("complete_turn", after_failure)
        self.assertEqual(
            public_surface.required_presenter_tool(evidence, after_failure),
            "present_places",
        )

    def test_route_replay_keeps_prepare_available_for_pending_and_unavailable_goals(self):
        evidence = TurnEvidence()
        evidence.bind_contract(TurnContract((OutcomeGoal("route", GoalKind.ROUTE),)))
        session = _replay_session()

        pending = public_surface.state_valid_tool_names(evidence, session=session)
        self.assertIn("present_route", pending)
        self.assertIn("prepare_route_options", pending)

        evidence.record_goal(
            "route", GoalState.ATTEMPTED_BUT_UNAVAILABLE, attempted=True
        )
        unavailable = public_surface.state_valid_tool_names(evidence, session=session)
        self.assertIn("present_route", unavailable)
        self.assertIn("prepare_route_options", unavailable)

    def test_route_replay_surface_requires_valid_card_and_selected_candidate(self):
        evidence = TurnEvidence()
        evidence.bind_contract(TurnContract((OutcomeGoal("route", GoalKind.ROUTE),)))

        invalid_card = _replay_session()
        invalid_card["_transcript"]["route_cards"][0]["itinerary"] = {
            "legs": [{"mode": "BUS"}]
        }
        names = public_surface.state_valid_tool_names(evidence, session=invalid_card)
        self.assertNotIn("present_route", names)
        self.assertIn("prepare_route_options", names)

        missing_candidate = _replay_session()
        missing_candidate["trip_state"].pop("selected_candidate_id")
        names = public_surface.state_valid_tool_names(
            evidence, session=missing_candidate
        )
        self.assertNotIn("present_route", names)

    def test_internal_leaf_tools_are_registered_but_never_offered(self):
        offered = {schema.get("name") for schema in TOOLS}
        for name in public_surface.INTERNAL_LEAF_TOOL_NAMES:
            self.assertIn(name, INTERNAL_TOOL_REGISTRY, name)
            self.assertNotIn(name, offered, name)
        for name in public_surface.PUBLIC_TOOL_NAMES:
            self.assertIn(name, TOOL_REGISTRY)
            self.assertIn(name, offered)

    def test_public_surface_never_enters_anthropic_strict_compilation(self):
        by_name = {schema["name"]: schema for schema in TOOLS}
        self.assertTrue(all("strict" not in schema for schema in by_name.values()))
        strict_tools = [tool for tool in TOOLS if tool.get("strict")]
        self.assertEqual(
            public_surface.optional_parameter_count(strict_tools),
            public_surface.STRICT_OPTIONAL_FIELD_BUDGET,
        )
        self.assertLessEqual(
            public_surface.optional_parameter_count(strict_tools),
            public_surface.OPTIONAL_FIELD_HARD_LIMIT,
        )
        self.assertEqual(
            public_surface.union_parameter_count(strict_tools),
            public_surface.STRICT_UNION_FIELD_BUDGET,
        )
        self.assertLessEqual(
            public_surface.union_parameter_count(strict_tools),
            public_surface.UNION_FIELD_HARD_LIMIT,
        )
        assert_strict_tool_schemas_compatible(TOOLS)


if __name__ == "__main__":
    unittest.main()
