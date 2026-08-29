"""Tests for the unflagged prepare_route_options / present_route path."""

from __future__ import annotations

import unittest
from types import SimpleNamespace

from app.services.agent.tools.route.route_input import merge_route_preparation_input
from app.services.trips.preparation.constraints import route_constraints


class RouteExclusionConstraintTests(unittest.TestCase):
    def test_route_constraints_reject_excluded_route_id(self):
        constraints = route_constraints(
            [{"type": "SUBWAY", "route_id": "Q"}],
            {"excluded_route_ids": ["Q"]},
        )
        assert not constraints["satisfied"]
        assert "excluded_route" in constraints["violations"]

    def test_route_constraints_keep_other_routes_valid(self):
        constraints = route_constraints(
            [{"type": "SUBWAY", "route_id": "A"}],
            {"excluded_route_ids": ["Q"]},
        )
        assert constraints["satisfied"]
        assert "excluded_route" not in constraints["violations"]

    def test_route_constraints_required_and_excluded_is_invalid(self):
        constraints = route_constraints(
            [{"type": "SUBWAY", "route_id": "Q"}],
            {"required_route_ids": ["Q"], "excluded_route_ids": ["Q"]},
        )
        assert not constraints["satisfied"]
        assert "excluded_route" in constraints["violations"]

    def test_route_constraints_any_excluded_leg_invalidates_the_chain(self):
        constraints = route_constraints(
            [
                {"type": "SUBWAY", "route_id": "A"},
                {"type": "BUS", "route_id": "B35"},
            ],
            {"excluded_route_ids": ["B35"]},
        )
        assert not constraints["satisfied"]
        assert "excluded_route" in constraints["violations"]

    def test_merge_tool_input_normalizes_bounded_excluded_route_ids(self):
        ctx = SimpleNamespace(session={})
        merged = merge_route_preparation_input(
            {
                "destination": "Work",
                "excluded_route_ids": [
                    " q ",
                    "Q",
                    "q",
                    "B35",
                    "",
                    "q train",
                    "M15",
                    "A" * 20,
                ],
            },
            ctx,
        )
        assert merged["excluded_route_ids"] == ["Q", "B35", "M15"]

    def test_merge_tool_input_keeps_real_provider_bus_forms(self):
        ctx = SimpleNamespace(session={})
        merged = merge_route_preparation_input(
            {
                "destination": "Work",
                "excluded_route_ids": [
                    "m15-sbs",
                    "M15+",
                    "BX12",
                    "q44-sbs",
                    "M15-SBS",
                    "M15 SBS",
                ],
            },
            ctx,
        )
        # Exact normalized identities survive merge; whitespace junk drops.
        assert merged["excluded_route_ids"] == ["M15-SBS", "M15+", "BX12", "Q44-SBS"]

    def test_route_constraints_enforce_provider_bus_forms_exactly(self):
        excluded_sbs = route_constraints(
            [{"type": "BUS", "route_id": "M15-SBS"}],
            {"excluded_route_ids": ["M15-SBS"]},
        )
        assert not excluded_sbs["satisfied"]
        assert "excluded_route" in excluded_sbs["violations"]

        excluded_plus = route_constraints(
            [{"type": "BUS", "route_id": "M15+"}],
            {"excluded_route_ids": ["M15+"]},
        )
        assert not excluded_plus["satisfied"]
        assert "excluded_route" in excluded_plus["violations"]

    def test_route_constraints_never_alias_provider_bus_forms(self):
        # Exact normalized identity only: M15-SBS and M15+ are distinct from
        # M15 and from each other unless a canonical helper proves otherwise.
        cases = (
            ([{"type": "BUS", "route_id": "M15-SBS"}], ["M15"]),
            ([{"type": "BUS", "route_id": "M15"}], ["M15-SBS"]),
            ([{"type": "BUS", "route_id": "M15-SBS"}], ["M15+"]),
            ([{"type": "BUS", "route_id": "M15+"}], ["M15-SBS"]),
            ([{"type": "BUS", "route_id": "BX12"}], ["B12"]),
        )
        for route, excluded in cases:
            with self.subTest(route=route, excluded=excluded):
                constraints = route_constraints(
                    route,
                    {"excluded_route_ids": excluded},
                )
                assert constraints["satisfied"]
                assert "excluded_route" not in constraints["violations"]

    def test_route_constraints_enforce_non_m_sbs_bus_forms_exactly(self):
        excluded_sbs = route_constraints(
            [{"type": "BUS", "route_id": "Q44-SBS"}],
            {"excluded_route_ids": ["Q44-SBS"]},
        )
        assert not excluded_sbs["satisfied"]
        assert "excluded_route" in excluded_sbs["violations"]

        # The plain Q44 and the Q44-SBS variant stay distinct identities.
        constraints_plain = route_constraints(
            [{"type": "BUS", "route_id": "Q44"}],
            {"excluded_route_ids": ["Q44-SBS"]},
        )
        assert constraints_plain["satisfied"]
        assert "excluded_route" not in constraints_plain["violations"]
