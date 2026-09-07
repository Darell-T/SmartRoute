"""Candidate selection and hard-constraint tests for route-option assembly."""

from __future__ import annotations

import unittest
from types import SimpleNamespace

from app.services.trips import candidates
from app.services.trips.preparation import multi_stop as prepare_route_multi_stop
from app.services.trips.preparation.constraints import route_constraints
from app.services.trips.preparation.prepare import PreparedChain


class RouteOptionAssemblyTests(unittest.IsolatedAsyncioTestCase):
    """Focused gates for canonical candidate selection and constraints."""

    @staticmethod
    def _selection_leg(routes: list[list[dict]], rows: list[dict], **tool_input):
        return SimpleNamespace(
            parsed_routes=routes,
            scored=rows,
            tool_input={"origin": "user", "destination": "Final", **tool_input},
        )

    @staticmethod
    def _line_route(line: str) -> list[dict]:
        return [{"type": "SUBWAY", "route_id": line}]

    def test_multi_stop_choices_preserve_provider_order_not_private_score(self):
        prepared = self._selection_leg(
            [
                self._line_route("Q"),
                self._line_route("A"),
                self._line_route("B"),
            ],
            [
                {
                    "index": 0,
                    "score": 90,
                    "total_minutes": 30,
                    "transfers": 1,
                    "street_walking_seconds": 120,
                    "in_station_transfer_seconds": 0,
                    "alert_penalty": 0,
                    "event_crowd_penalty": 0,
                    "preferred_mode_penalty": 0,
                },
                {
                    "index": 1,
                    "score": 10,
                    "total_minutes": 10,
                    "transfers": 0,
                    "street_walking_seconds": 60,
                    "in_station_transfer_seconds": 0,
                    "alert_penalty": 0,
                    "event_crowd_penalty": 0,
                    "preferred_mode_penalty": 0,
                },
                {
                    "index": 2,
                    "score": 50,
                    "total_minutes": 20,
                    "transfers": 0,
                    "street_walking_seconds": 90,
                    "in_station_transfer_seconds": 0,
                    "alert_penalty": 0,
                    "event_crowd_penalty": 0,
                    "preferred_mode_penalty": 0,
                },
            ],
        )

        choices = prepare_route_multi_stop._candidate_choices(prepared, 5)

        assert choices == [0, 1, 2]
        assert prepare_route_multi_stop._route_score(prepared, 1) == 10

    def test_route_family_dedupe_preserves_first_occurrence_and_distinct_topology(self):
        first = [
            {"type": "subway", "route_id": "Q", "departure_stop": "Alpha Av", "arrival_stop": "Beta St"},
            {"type": "SUBWAY", "route_id": "R", "departure_stop": "Beta St", "arrival_stop": "Delta Pkwy"},
        ]
        duplicate = [dict(step) for step in first]
        distinct = [
            {"type": "SUBWAY", "route_id": "Q", "departure_stop": "Alpha Av", "arrival_stop": "Gamma Sq"},
            {"type": "SUBWAY", "route_id": "R", "departure_stop": "Gamma Sq", "arrival_stop": "Delta Pkwy"},
        ]

        assert candidates.dedupe_route_families([first, duplicate, distinct]) == [first, distinct]

    def test_multi_stop_dominance_removes_only_same_signature_route(self):
        route = self._line_route("Q")
        prepared = self._selection_leg(
            [route, route, self._line_route("A")],
            [
                {
                    "index": 0,
                    "score": 20,
                    "total_minutes": 20,
                    "transfers": 0,
                    "street_walking_seconds": 60,
                    "in_station_transfer_seconds": 0,
                    "alert_penalty": 0,
                    "event_crowd_penalty": 0,
                    "preferred_mode_penalty": 0,
                },
                {
                    "index": 1,
                    "score": 70,
                    "total_minutes": 30,
                    "transfers": 1,
                    "street_walking_seconds": 180,
                    "in_station_transfer_seconds": 60,
                    "alert_penalty": 8,
                    "event_crowd_penalty": 5,
                    "preferred_mode_penalty": 4,
                },
                {
                    "index": 2,
                    "score": 5,
                    "total_minutes": 15,
                    "transfers": 0,
                    "street_walking_seconds": 30,
                    "in_station_transfer_seconds": 0,
                    "alert_penalty": 0,
                    "event_crowd_penalty": 0,
                    "preferred_mode_penalty": 0,
                },
            ],
        )

        choices = prepare_route_multi_stop._candidate_choices(prepared, 5)

        assert choices == [0, 2]

    def test_multi_stop_beam_truncation_keeps_provider_order(self):
        prepared = self._selection_leg(
            [
                self._line_route("Q"),
                self._line_route("A"),
                self._line_route("B"),
                self._line_route("R"),
            ],
            [
                {
                    "index": index,
                    "score": score,
                    "total_minutes": 10 + index,
                    "transfers": 0,
                    "street_walking_seconds": 60,
                    "in_station_transfer_seconds": 0,
                    "alert_penalty": 0,
                    "event_crowd_penalty": 0,
                    "preferred_mode_penalty": 0,
                }
                for index, score in enumerate((90, 80, 70, 1))
            ],
        )
        chains = [
            PreparedChain(
                legs=[(prepared, index)],
                score=score,
            )
            for index, score in enumerate((90, 80, 70, 1))
        ]

        bounded = prepare_route_multi_stop._bounded_provider_order(chains, 3)

        assert [chain.legs[0][1] for chain in bounded] == [0, 1, 2]

    def test_multi_stop_choices_keep_hard_constraint_valid_routes(self):
        prepared = self._selection_leg(
            [self._line_route("Q"), self._line_route("A")],
            [
                {"index": 0, "score": 1},
                {"index": 1, "score": 99},
            ],
            excluded_route_ids=["Q"],
        )

        choices = prepare_route_multi_stop._candidate_choices(prepared, 5)

        assert choices == [1]

    def test_arrival_by_is_a_hard_constraint_after_itinerary_finalization(self):
        base_input = {"arrival_by": "2026-08-06T13:00:00-04:00"}

        on_time = route_constraints(
            [],
            base_input,
            itinerary={"arrival_at": "2026-08-06T13:00:00-04:00"},
        )
        early = route_constraints(
            [],
            base_input,
            itinerary={"arrival_at": "2026-08-06T12:59:00-04:00"},
        )
        late = route_constraints(
            [],
            base_input,
            itinerary={"arrival_at": "2026-08-06T13:01:00-04:00"},
        )

        assert on_time["satisfied"]
        assert early["satisfied"]
        assert not late["satisfied"]
        assert late["violations"] == ["arrival_by_missed"]

    def test_arrival_by_comparison_ignores_missing_or_unparseable_timestamps(self):
        tool_input = {"arrival_by": "2026-08-06T13:00:00-04:00"}

        for itinerary in ({}, {"arrival_at": "not-a-timestamp"}):
            with self.subTest(itinerary=itinerary):
                constraints = route_constraints([], tool_input, itinerary=itinerary)
                assert constraints["satisfied"]
                assert "arrival_by_missed" not in constraints["violations"]

        constraints = route_constraints(
            [],
            {"arrival_by": "not-a-timestamp"},
            itinerary={"arrival_at": "2026-08-06T13:30:00-04:00"},
        )
        assert constraints["satisfied"]
        assert "arrival_by_missed" not in constraints["violations"]


__all__ = ()
