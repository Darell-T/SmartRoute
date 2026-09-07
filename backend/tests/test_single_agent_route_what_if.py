"""Tests for the unflagged prepare_route_options / present_route path."""
from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.services.agent import discovery_store, trip_state
from app.services.agent.tools.route import (
    prepare_route_options,
    present_route,
)

from tests.single_agent_route_test_support import (
    _ctx,
    _prepared_leg,
    _present_route_input,
)


class SingleAgentRouteWhatIfTests(unittest.IsolatedAsyncioTestCase):
    async def test_what_if_preferences_stay_temporary_until_explicit_commit(self):
        prepared = _prepared_leg()
        ctx = _ctx()
        with patch(
            "app.services.agent.tools.route.prepare_route_options.prepare_single_leg",
            new=AsyncMock(return_value=prepared),
        ):
            result = await prepare_route_options.execute(
                {
                    "destination": "Barclays Center",
                    "destination_source": "current_turn",
                    "routing_preference": "LESS_WALKING",
                    "what_if": True,
                },
                ctx,
            )
        state = trip_state.get_trip_state(ctx.session)
        assert state["preferences"]["walking_preference"] == "any"
        assert state["temporary_candidate_set_id"] == result.data["candidate_set_id"]
        candidate_id = result.data["candidates"][0]["candidate_id"]
        with patch(
            "app.services.trips.enrichment._enrich_route",
            new=AsyncMock(return_value=None),
        ):
            presented = await present_route.execute(
                _present_route_input(candidate_id, commit_scenario=True),
                ctx,
            )
        assert presented.ok
        committed = trip_state.get_trip_state(ctx.session)
        assert committed["preferences"]["walking_preference"] == "less_walking"
        assert committed["temporary_candidate_set_id"] is None

    async def test_what_if_preview_can_commit_the_same_candidate_later(self):
        prepared = _prepared_leg()
        ctx = _ctx()
        trip_state.update_trip_state(
            ctx.session,
            origin="Home",
            destination="Work",
            active_candidate_set_id="cs_active",
            selected_candidate_id="cd_active",
        )
        with patch(
            "app.services.agent.tools.route.prepare_route_options.prepare_single_leg",
            new=AsyncMock(return_value=prepared),
        ):
            result = await prepare_route_options.execute(
                {
                    "origin": "Home",
                    "destination": "Barclays Center",
                    "destination_source": "current_turn",
                    "what_if": True,
                },
                ctx,
            )
        candidate_id = result.data["candidates"][0]["candidate_id"]
        with patch(
            "app.services.trips.enrichment._enrich_route",
            new=AsyncMock(return_value=None),
        ):
            preview = await present_route.execute(
                _present_route_input(candidate_id),
                ctx,
            )
            preview_state = trip_state.get_trip_state(ctx.session)
            accepted = await present_route.execute(
                _present_route_input(candidate_id, commit_scenario=True),
                ctx,
            )

        assert preview.ok
        assert preview.session_route_cards == []
        assert accepted.ok
        assert preview_state["active_candidate_set_id"] == "cs_active"
        assert preview_state["selected_candidate_id"] == "cd_active"
        committed = trip_state.get_trip_state(ctx.session)
        assert committed["active_candidate_set_id"] == result.data["candidate_set_id"]
        assert committed["selected_candidate_id"] == candidate_id
        assert committed["temporary_candidate_set_id"] is None

    async def test_what_if_route_exclusion_stays_temporary_until_explicit_commit(self):
        prepared = _prepared_leg()
        prepared.parsed_routes = [[{"type": "SUBWAY", "route_id": "A"}]]
        ctx = _ctx()
        trip_state.update_trip_state(
            ctx.session,
            origin="Home",
            destination="Work",
            active_candidate_set_id="cs_active",
            selected_candidate_id="cd_active",
        )
        with patch(
            "app.services.agent.tools.route.prepare_route_options.prepare_single_leg",
            new=AsyncMock(return_value=prepared),
        ):
            result = await prepare_route_options.execute(
                {
                    "origin": "Home",
                    "destination": "Barclays Center",
                    "destination_source": "current_turn",
                    "what_if": True,
                    "excluded_route_ids": ["Q"],
                },
                ctx,
            )
        state = trip_state.get_trip_state(ctx.session)
        assert state["active_candidate_set_id"] == "cs_active"
        assert state["selected_candidate_id"] == "cd_active"
        assert state["temporary_candidate_set_id"] == result.data["candidate_set_id"]
        candidate_id = result.data["candidates"][0]["candidate_id"]
        with patch(
            "app.services.trips.enrichment._enrich_route",
            new=AsyncMock(return_value=None),
        ):
            preview = await present_route.execute(
                _present_route_input(candidate_id),
                ctx,
            )
            preview_state = trip_state.get_trip_state(ctx.session)
            accepted = await present_route.execute(
                _present_route_input(candidate_id, commit_scenario=True),
                ctx,
            )

        assert preview.ok
        assert accepted.ok
        assert preview_state["active_candidate_set_id"] == "cs_active"
        assert preview_state["selected_candidate_id"] == "cd_active"
        committed = trip_state.get_trip_state(ctx.session)
        assert committed["active_candidate_set_id"] == result.data["candidate_set_id"]
        assert committed["selected_candidate_id"] == candidate_id
        assert ctx.session["slots"]["constraints"]["excluded_route_ids"] == ["Q"]

    async def test_what_if_rejects_unpresented_older_destination_id(self):
        ctx = _ctx()
        set_b = discovery_store.store_discovery_set(
            session_id="sess-test",
            places=[
                {
                    "name": "B1 Coffee",
                    "address": "1 B St",
                    "latitude": 40.71,
                    "longitude": -73.98,
                },
                {
                    "name": "B2 Coffee",
                    "address": "2 B Ave",
                    "latitude": 40.72,
                    "longitude": -73.97,
                },
            ],
            query="coffee",
        )
        set_a = discovery_store.store_discovery_set(
            session_id="sess-test",
            places=[
                {
                    "name": "A1 Pizza",
                    "address": "1 A St",
                    "latitude": 40.6298,
                    "longitude": -73.9616,
                    "provider_place_id": "ChIJ-a1",
                },
                {
                    "name": "A2 Pizza",
                    "address": "2 A Ave",
                    "latitude": 40.6360,
                    "longitude": -73.9600,
                    "provider_place_id": "ChIJ-a2",
                },
            ],
            query="pizza",
        )
        trip_state.bind_discovery_set(ctx.session, set_b)
        record_b = discovery_store.load_discovery_set(set_b, session_id="sess-test")
        place_b = record_b["places"][0]["place_id"]
        trip_state.bind_selected_place(ctx.session, place_b)
        record_a = discovery_store.load_discovery_set(set_a, session_id="sess-test")
        dest_a = record_a["places"][0]
        provider = AsyncMock(return_value=_prepared_leg())
        with patch(
            "app.services.agent.tools.route.prepare_route_options.prepare_single_leg",
            new=provider,
        ):
            result = await prepare_route_options.execute(
                {
                    "destination": "Take me to A1",
                    "destination_place_id": dest_a["place_id"],
                    "destination_source": "current_turn",
                    "discovery_set_id": set_a,
                    "what_if": True,
                },
                ctx,
            )
        assert not result.ok
        assert "destination place reference is invalid" in (result.error or "")
        provider.assert_not_awaited()
        state = trip_state.get_trip_state(ctx.session)
        assert state["active_discovery_set_id"] == set_b
        assert state["selected_place_id"] == place_b
        assert state.get("temporary_candidate_set_id") is None

    async def test_what_if_rejects_expired_unpresented_older_destination_id(self):
        ctx = _ctx()
        # B keeps a long TTL so it stays genuinely valid after the clock
        # advances; A uses the default short TTL so it crosses expiry.
        set_b = discovery_store.store_discovery_set(
            session_id="sess-test",
            places=[
                {
                    "name": "B1 Coffee",
                    "address": "1 B St",
                    "latitude": 40.71,
                    "longitude": -73.98,
                },
                {
                    "name": "B2 Coffee",
                    "address": "2 B Ave",
                    "latitude": 40.72,
                    "longitude": -73.97,
                },
            ],
            query="coffee",
            ttl_seconds=3600,
        )
        set_a = discovery_store.store_discovery_set(
            session_id="sess-test",
            places=[
                {
                    "name": "A1 Pizza",
                    "address": "1 A St",
                    "latitude": 40.6298,
                    "longitude": -73.9616,
                    "provider_place_id": "ChIJ-a1",
                },
                {
                    "name": "A2 Pizza",
                    "address": "2 A Ave",
                    "latitude": 40.6360,
                    "longitude": -73.9600,
                    "provider_place_id": "ChIJ-a2",
                },
            ],
            query="pizza",
        )
        trip_state.bind_discovery_set(ctx.session, set_b)
        record_b = discovery_store.load_discovery_set(set_b, session_id="sess-test")
        place_b = record_b["places"][0]["place_id"]
        trip_state.bind_selected_place(ctx.session, place_b)
        record_a = discovery_store.load_discovery_set(set_a, session_id="sess-test")
        dest_a = record_a["places"][0]
        provider = AsyncMock(return_value=_prepared_leg())
        with patch(
            "app.services.agent.tools.route.prepare_route_options.prepare_single_leg",
            new=provider,
        ):
            expired_at = float(record_a["expires_at"])
            with patch(
                "app.services.agent.discovery_store.time",
                new=SimpleNamespace(time=lambda: expired_at + 1),
            ):
                result = await prepare_route_options.execute(
                    {
                        "destination": "Take me to A1",
                        "destination_place_id": dest_a["place_id"],
                        "destination_source": "current_turn",
                        "discovery_set_id": set_a,
                        "what_if": True,
                    },
                    ctx,
                )
        assert not result.ok
        assert "destination place reference is invalid" in (result.error or "")
        provider.assert_not_awaited()
        state = trip_state.get_trip_state(ctx.session)
        assert state["active_discovery_set_id"] == set_b
        assert state["selected_place_id"] == place_b
        assert state.get("temporary_candidate_set_id") is None
