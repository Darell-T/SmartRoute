"""Tests for the unflagged prepare_route_options / present_route path."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.services.agent import candidate_store, loop, trip_state
from app.services.agent.model import policy
from app.services.agent.public_surface import INITIAL_TOOL_NAMES
from app.services.agent.tools._types import ToolResult
from app.services.agent.tools.route import (
    prepare_route_options,
    present_route,
)
from app.services.agent.tools.route.preparation_adapter import prepare_single_leg
from app.services.trips.preparation.constraints import route_status

from tests.single_agent_route_test_support import (
    _ctx,
    _prepared_leg,
    _present_route_input,
    _stored_itinerary,
)


class SingleAgentRouteAvailabilityTests(unittest.IsolatedAsyncioTestCase):
    async def test_prepare_reports_no_hard_constraint_match_when_all_routes_excluded(
        self,
    ):
        prepared = _prepared_leg()
        prepared.parsed_routes = [[{"type": "SUBWAY", "route_id": "Q"}]]
        prepared.scored = [
            {"index": 0, "score": 22, "total_minutes": 23, "transfers": 0}
        ]
        ctx = _ctx()
        with patch(
            "app.services.agent.tools.route.prepare_route_options.prepare_single_leg",
            new=AsyncMock(return_value=prepared),
        ):
            result = await prepare_route_options.execute(
                {
                    "destination": "Barclays Center",
                    "destination_source": "current_turn",
                    "excluded_route_ids": ["Q"],
                },
                ctx,
            )
        assert result.ok
        assert result.data["route_status"] == "no_hard_constraint_match"
        assert not result.data["presentation_allowed"]
        assert result.data["candidates"] == []
        assert result.data["candidate_count"] == 0
        stored = candidate_store.load_candidate_set(
            result.data["candidate_set_id"],
            session_id=ctx.session_id,
        )
        assert stored is not None
        assert stored["candidates"][0]["digest"]["hard_constraint_violations"] == [
            "excluded_route"
        ]

    async def test_present_route_rejects_excluded_stored_candidate(self):
        ctx = _ctx()
        set_id = candidate_store.store_candidate_set(
            session_id=ctx.session_id,
            payload={
                "tool_input": {
                    "origin": "user",
                    "destination": "Barclays Center",
                    "excluded_route_ids": ["Q"],
                },
                "origin_place": {
                    "name": "Your location",
                    "latitude": 40.75,
                    "longitude": -73.99,
                },
                "destination_place": {
                    "name": "Barclays Center",
                    "latitude": 40.68,
                    "longitude": -73.97,
                },
                "parsed_routes": [[{"type": "SUBWAY", "route_id": "Q"}]],
                "scored": [
                    {"index": 0, "score": 1, "total_minutes": 10, "transfers": 0}
                ],
                "candidates": [
                    {
                        "candidate_id": "cd_q",
                        "index": 0,
                        "digest": {
                            "_canonical_itinerary": _stored_itinerary(
                                [{"type": "SUBWAY", "route_id": "Q"}],
                                {
                                    "name": "Your location",
                                    "latitude": 40.75,
                                    "longitude": -73.99,
                                },
                                {
                                    "name": "Barclays Center",
                                    "latitude": 40.68,
                                    "longitude": -73.97,
                                },
                            )
                        },
                    }
                ],
                "route_status": "good",
            },
        )
        trip_state.bind_candidate_set(ctx.session, set_id)
        with patch(
            "app.services.trips.enrichment._enrich_route",
            new=AsyncMock(return_value=None),
        ):
            result = await present_route.execute(
                _present_route_input("cd_q"),
                ctx,
            )
        assert not result.ok
        assert "hard constraints" in (result.error or "")

    def test_route_status_does_not_force_degraded_or_insufficient_candidates(self):
        assert (
            route_status(
                candidates=[{"hard_constraints_satisfied": False}],
                coverage={"mta": "current"},
                incident_impacts=[],
            )
            == "no_hard_constraint_match"
        )
        assert (
            route_status(
                candidates=[{"hard_constraints_satisfied": True}],
                coverage={"mta": "unavailable", "incidents": "unscanned"},
                incident_impacts=[],
            )
            == "insufficient_coverage"
        )

    async def test_prepare_returns_typed_nonpresentable_status_when_modes_are_exhausted(
        self,
    ):
        ctx = _ctx()
        with patch(
            "app.services.agent.tools.route.prepare_route_options.prepare_single_leg",
            new=AsyncMock(
                return_value=ToolResult(
                    ok=False,
                    error="no transit modes left after excluding all of them",
                )
            ),
        ):
            result = await prepare_route_options.execute(
                {
                    "destination": "Barclays Center",
                    "destination_source": "current_turn",
                    "what_if": True,
                },
                ctx,
            )
        assert result.ok
        assert result.data["route_status"] == "no_hard_constraint_match"
        assert not result.data["presentation_allowed"]

    async def test_prepare_nonfatal_active_keeps_accepted_selection_bound(self):
        """Active nonfatal prepare stores an audit set without moving selection."""
        ctx = _ctx()
        set_id = candidate_store.store_candidate_set(
            session_id=ctx.session_id,
            payload={
                "tool_input": {"origin": "user", "destination": "Barclays Center"},
                "parsed_routes": [[{"type": "SUBWAY", "route_id": "Q"}]],
                "scored": [
                    {"index": 0, "score": 1, "total_minutes": 10, "transfers": 0}
                ],
                "candidates": [{"candidate_id": "cd_accepted", "index": 0}],
                "route_status": "good",
            },
        )
        trip_state.bind_candidate_set(ctx.session, set_id)
        trip_state.bind_selected_candidate(ctx.session, "cd_accepted")
        with patch(
            "app.services.agent.tools.route.prepare_route_options.prepare_single_leg",
            new=AsyncMock(
                return_value=ToolResult(
                    ok=False,
                    error="no transit modes left after excluding all of them",
                )
            ),
        ):
            result = await prepare_route_options.execute(
                {
                    "destination": "Barclays Center",
                    "destination_source": "current_turn",
                    "exclude_modes": ["SUBWAY", "BUS"],
                },
                ctx,
            )
        assert result.ok
        assert result.data["route_status"] == "no_hard_constraint_match"
        assert not result.data["presentation_allowed"]
        state = trip_state.get_trip_state(ctx.session)
        assert state["active_candidate_set_id"] == set_id
        assert state["selected_candidate_id"] == "cd_accepted"
        assert state["temporary_candidate_set_id"] is None
        audit = candidate_store.load_candidate_set(
            result.data["candidate_set_id"],
            session_id=ctx.session_id,
        )
        assert audit is not None
        assert audit["candidate_set_id"] != set_id
        assert not audit["presented"]

    async def test_prepare_rejects_invalid_timing_before_provider_work(self):
        result = await prepare_single_leg(
            {
                "destination": "Barclays Center",
                "departure_time": "tomorrow morning",
            },
            _ctx(),
            {},
            dependencies=SimpleNamespace(),
        )
        assert not result.ok
        assert "RFC3339" in (result.error or "")

    def test_route_tools_use_initial_model_led_surface_without_legacy_plan_trip(self):
        names = {
            tool.get("name")
            for tool in loop._tools_for_state(policy.policy_for_mode("auto"))
        }
        assert names == set(INITIAL_TOOL_NAMES)
        assert "plan_trip" not in names
