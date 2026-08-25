"""Route-constraint precedence after the model has interpreted the rider."""

from __future__ import annotations

import unittest

from app.services.agent.model import policy as agent_policy
from app.services.agent import tool_input_policy
from app.services.trips.preparation.input import normalize_route_ids


class PersistedRouteConstraintTests(unittest.TestCase):
    def test_reads_only_authoritative_session_state(self) -> None:
        session = {
            "slots": {"constraints": {"excluded_route_ids": ["q", "R", "Q"]}}
        }

        exclusions = tool_input_policy.rider_excluded_route_ids(
            "This prose is not interpreted here", session
        )

        self.assertEqual(exclusions, ("Q", "R"))
        self.assertEqual(
            session["slots"]["constraints"]["excluded_route_ids"],
            ["q", "R", "Q"],
        )

    def test_route_id_normalization_remains_bounded_syntax_validation(self) -> None:
        self.assertEqual(
            normalize_route_ids(["q", "Q44-SBS", "not a route", "q"]),
            ("Q", "Q44-SBS"),
        )


class ConstrainedToolInputTests(unittest.TestCase):
    def setUp(self) -> None:
        self.policy = agent_policy.policy_for_mode("auto")

    def _constrained(
        self,
        tool_input: dict,
        *,
        excluded_route_ids: tuple[str, ...] = (),
        excluded_modes: set[str] | None = None,
    ) -> dict:
        return tool_input_policy.constrained_tool_input(
            "prepare_route_options",
            tool_input,
            excluded_modes or set(),
            mode_policy=self.policy,
            excluded_route_ids=excluded_route_ids,
        )

    def test_explicit_model_allowance_relaxes_only_the_named_persisted_route(self) -> None:
        tool_input = self._constrained(
            {
                "destination": "Work",
                "allowed_route_ids": ["Q"],
                "excluded_route_ids": ["R"],
            },
            excluded_route_ids=("Q", "R"),
        )

        self.assertEqual(tool_input["excluded_route_ids"], ["R"])
        self.assertNotIn("allowed_route_ids", tool_input)

    def test_explicit_model_allowance_can_clear_persisted_exclusions(self) -> None:
        tool_input = self._constrained(
            {"destination": "Work", "allowed_route_ids": ["Q"]},
            excluded_route_ids=("Q",),
        )

        self.assertEqual(tool_input["excluded_route_ids"], [])

    def test_new_model_exclusion_is_merged_with_persisted_state(self) -> None:
        tool_input = self._constrained(
            {"destination": "Work", "excluded_route_ids": ["R"]},
            excluded_route_ids=("Q",),
        )

        self.assertEqual(tool_input["excluded_route_ids"], ["Q", "R"])

    def test_explicit_mode_allowance_relaxes_persisted_mode(self) -> None:
        tool_input = self._constrained(
            {"destination": "Work", "allowed_modes": ["BUS"]},
            excluded_modes={"BUS", "RAIL"},
        )

        self.assertEqual(tool_input["exclude_modes"], ["RAIL"])
        self.assertNotIn("allowed_modes", tool_input)

    def test_required_route_ids_are_preserved_for_backend_validation(self) -> None:
        tool_input = self._constrained(
            {"destination": "Work", "required_route_ids": ["Q"]}
        )

        self.assertEqual(tool_input["required_route_ids"], ["Q"])

    def test_model_arguments_receive_only_server_owned_budget_fields(self) -> None:
        tool_input = self._constrained({"destination": "Work"})

        self.assertEqual(tool_input["max_candidates"], self.policy.max_route_candidates)
        self.assertEqual(
            tool_input["include_first_leg_arrivals"], self.policy.optional_enrichment
        )


if __name__ == "__main__":
    unittest.main()
