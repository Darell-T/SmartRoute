"""Tests for the unflagged prepare_route_options / present_route path."""
from __future__ import annotations

import copy
import unittest
from typing import get_args
from unittest.mock import AsyncMock, patch

from app.services.agent import candidate_store, trip_state
from app.services.agent.tools.route import (
    prepare_route_branches,
    prepare_route_options,
    prepare_route_persistence,
    present_route,
)
from app.services.trips.selection_record import SelectionReason

from tests.single_agent_route_test_support import (
    _ctx,
    _prepared_leg,
    _present_route_input,
    _stored_itinerary,
)


class SingleAgentToolSurfaceTests(unittest.IsolatedAsyncioTestCase):
    async def test_prepare_returns_opaque_candidates_without_route_card(self):
        prepared = _prepared_leg()
        ctx = _ctx()
        with patch(
            "app.services.agent.tools.route.prepare_route_options.prepare_single_leg",
            new=AsyncMock(return_value=prepared),
        ):
            result = await prepare_route_options.execute(
                {
                    "origin": "user",
                    "destination": "Barclays Center",
                    "destination_source": "current_turn",
                },
                ctx,
            )
        assert result.ok
        assert result.events == []
        data = result.data
        assert "candidate_set_id" in data
        assert len(data["candidates"]) == 1
        assert str(data["candidates"][0]["candidate_id"]).startswith("cd_")
        assert "evidence_coverage" in data
        assert ctx.session["trip_state"]["active_candidate_set_id"] == data["candidate_set_id"]

    async def test_legacy_discovery_set_id_does_not_bind_discovery_context(self):
        prepared = _prepared_leg()
        ctx = _ctx()
        with (
            patch.object(
                prepare_route_branches,
                "resolve_destination_reference",
                new=AsyncMock(
                    return_value=(
                        prepared.destination_place,
                        "pl_verified",
                        None,
                        None,
                    )
                ),
            ),
            patch.object(
                prepare_route_options,
                "resolve_waypoint_places",
                new=AsyncMock(return_value=([], [], None, None)),
            ),
            patch.object(
                prepare_route_options,
                "prepare_single_leg",
                new=AsyncMock(return_value=prepared),
            ),
            patch.object(
                prepare_route_persistence.trip_state_module,
                "bind_discovery_context",
            ) as bind_context,
        ):
            result = await prepare_route_options.execute(
                {
                    "origin": "user",
                    "destination": "Barclays Center",
                    "destination_source": "current_turn",
                    "discovery_set_id": "ds_legacy",
                },
                ctx,
            )

        assert result.ok
        bind_context.assert_not_called()
        assert trip_state.get_trip_state(ctx.session)["selected_place_id"] == "pl_verified"

    async def test_present_rejects_invented_candidate(self):
        prepared = _prepared_leg()
        ctx = _ctx()
        with patch(
            "app.services.agent.tools.route.prepare_route_options.prepare_single_leg",
            new=AsyncMock(return_value=prepared),
        ):
            prepared_result = await prepare_route_options.execute(
                {
                    "destination": "Barclays Center",
                    "destination_source": "current_turn",
                },
                ctx,
            )
        set_id = prepared_result.data["candidate_set_id"]
        result = await present_route.execute(
            _present_route_input("cd_invented"),
            ctx,
        )
        assert not result.ok
        assert result.error == "candidate id is unknown for this set"
        assert result.events == []
        stored = candidate_store.load_candidate_set(set_id, session_id=ctx.session_id)
        assert not stored["presented"]
        assert stored["selected_candidate_id"] is None
        assert "route_cards" not in ctx.session

    async def test_present_rejects_wrong_session(self):
        prepared = _prepared_leg()
        ctx_a = _ctx("sess-a")
        with patch(
            "app.services.agent.tools.route.prepare_route_options.prepare_single_leg",
            new=AsyncMock(return_value=prepared),
        ):
            prepared_result = await prepare_route_options.execute(
                {
                    "destination": "Barclays Center",
                    "destination_source": "current_turn",
                },
                ctx_a,
            )
        set_id = prepared_result.data["candidate_set_id"]
        candidate_id = prepared_result.data["candidates"][0]["candidate_id"]
        ctx_b = _ctx("sess-b")
        ctx_b.session = {
            "trip_state": {"active_candidate_set_id": set_id},
        }
        result = await present_route.execute(
            _present_route_input(candidate_id),
            ctx_b,
        )
        assert not result.ok

    async def test_present_emits_exactly_one_canonical_route_card_and_is_one_time(self):
        prepared = _prepared_leg()
        ctx = _ctx()
        with patch(
            "app.services.agent.tools.route.prepare_route_options.prepare_single_leg",
            new=AsyncMock(return_value=prepared),
        ):
            prepared_result = await prepare_route_options.execute(
                {
                    "destination": "Barclays Center",
                    "destination_source": "current_turn",
                },
                ctx,
            )
        candidate = prepared_result.data["candidates"][0]
        with patch(
            "app.services.trips.enrichment._enrich_route",
            new=AsyncMock(return_value=None),
        ):
            result = await present_route.execute(
                _present_route_input(candidate["candidate_id"]),
                ctx,
            )
        assert result.ok
        assert [event.role for event in result.events if event.type == "route_card"] == ["recommended"]
        assert len(result.session_route_cards) == 1
        assert candidate["candidate_id"] not in str(result.data)
        duplicate = await present_route.execute(
            _present_route_input(candidate["candidate_id"]),
            ctx,
        )
        assert not duplicate.ok
        assert "already presented" in (duplicate.error or "")

    async def test_present_fails_closed_before_mutation_when_snapshot_is_incomplete(self):
        cases = (
            (
                "canonical itinerary",
                "prepared candidate is missing its canonical itinerary snapshot",
            ),
            ("comparison factors", "prepared candidate is missing finalized comparison factors"),
        )
        for index, (missing, expected_error) in enumerate(cases):
            with self.subTest(missing=missing):
                prepared = _prepared_leg()
                ctx = _ctx(f"sess-incomplete-{index}")
                with patch(
                    "app.services.agent.tools.route.prepare_route_options.prepare_single_leg",
                    new=AsyncMock(return_value=prepared),
                ):
                    prepared_result = await prepare_route_options.execute(
                        {
                            "destination": "Barclays Center",
                            "destination_source": "current_turn",
                        },
                        ctx,
                    )
                set_id = prepared_result.data["candidate_set_id"]
                candidate_id = prepared_result.data["candidates"][0]["candidate_id"]
                stored = candidate_store.load_candidate_set(
                    set_id,
                    session_id=ctx.session_id,
                )
                assert stored is not None
                record = copy.deepcopy(stored)
                entry = next(
                    item
                    for item in record["candidates"]
                    if item["candidate_id"] == candidate_id
                )
                if missing == "canonical itinerary":
                    entry["digest"].pop("_canonical_itinerary", None)
                else:
                    record["scored"] = []

                with patch(
                    "app.services.agent.tools.route.present_route_state."
                    "candidate_store.get_candidate",
                    return_value=(record, entry, None),
                ):
                    result = await present_route.execute(
                        _present_route_input(candidate_id),
                        ctx,
                    )

                assert not result.ok
                assert result.error == expected_error
                unchanged = candidate_store.load_candidate_set(
                    set_id,
                    session_id=ctx.session_id,
                )
                assert not unchanged["presented"]
                assert "route_cards" not in ctx.session

    async def test_present_route_card_carries_contract_valid_outer_agent_selection(self):
        prepared = _prepared_leg()
        ctx = _ctx()
        with patch(
            "app.services.agent.tools.route.prepare_route_options.prepare_single_leg",
            new=AsyncMock(return_value=prepared),
        ):
            prepared_result = await prepare_route_options.execute(
                {
                    "destination": "Barclays Center",
                    "destination_source": "current_turn",
                },
                ctx,
            )
        candidate = prepared_result.data["candidates"][0]
        with patch(
            "app.services.trips.enrichment._enrich_route",
            new=AsyncMock(return_value=None),
        ):
            result = await present_route.execute(
                _present_route_input(candidate["candidate_id"]),
                ctx,
            )
        assert result.ok
        # Selection metadata remains on the canonical card event, not in the
        # tool result returned to Sonnet. Returning private score/selection
        # fields would bias a later completion call.
        assert "selection_decision" not in result.data
        recommended = next(
            event
            for event in result.events
            if event.type == "route_card" and event.role == "recommended"
        )
        reason = recommended.selection_decision["selection_reason"]
        assert reason == "outer_agent_selection"
        assert reason in get_args(SelectionReason)
        assert recommended.selection_decision["selection_reason"] == reason
        assert recommended.itinerary["selection_decision"]["selection_reason"] == reason

    async def test_present_rechecks_hard_constraints_after_outer_selection(self):
        ctx = _ctx()
        set_id = candidate_store.store_candidate_set(
            session_id=ctx.session_id,
            payload={
                "tool_input": {
                    "origin": "user",
                    "destination": "Barclays Center",
                    "exclude_modes": ["BUS"],
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
                "parsed_routes": [[{"type": "BUS", "route_id": "B1"}]],
                "scored": [{"index": 0, "score": 1, "total_minutes": 10, "transfers": 0}],
                "candidates": [
                    {
                        "candidate_id": "cd_bus",
                        "index": 0,
                        "digest": {
                            "_canonical_itinerary": _stored_itinerary(
                                [{"type": "BUS", "route_id": "B1"}],
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
                _present_route_input("cd_bus"),
                ctx,
            )
        assert not result.ok
        assert "hard constraints" in (result.error or "")
