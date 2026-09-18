"""Focused tests for the discovery-to-route handoff (Phase 2A).

Covers routing by opaque destination_place_id, server-side canonical
identity preservation, the tool-start label, and the single-leg provider
handoff. Moved out of test_single_agent_route_tools.py so that phase does
not grow further.
"""
from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, patch

from app.services.agent import candidate_store, discovery_store, trip_state
from app.services.agent import session as session_module
from app.services.agent.tools.location_resolution import ResolvedPlace
from app.services.agent.tools.route import (
    prepare_route_options,
    prepare_route_persistence,
    present_route,
)

from tests.discovery_route_handoff_test_support import (
    DiscoveryRouteHandoffTestMixin,
    _ctx,
    _prepared_leg,
)


class DiscoveryRouteBranchCommitTests(DiscoveryRouteHandoffTestMixin, unittest.IsolatedAsyncioTestCase):
    def test_destination_identity_matches_provider_or_coordinates(self):
        provider_destination = ResolvedPlace(
            "Second",
            40.72,
            -73.98,
            "discovery",
            place_id="ChIJ-second",
        )
        provider_options = [
            ({"place_id": "ChIJ-first"}, "pl_first"),
            ({"provider_place_id": "chij-second"}, "pl_second"),
        ]
        coordinate_destination = ResolvedPlace("Coordinate", 40.70, -74.00, "user")
        coordinate_options = [
            ({"latitude": 40.71, "longitude": -74.01}, "pl_other"),
            ({"lat": 40.70, "lng": -74.00}, "pl_coordinate"),
        ]

        assert prepare_route_persistence._destination_identity(provider_destination, options=provider_options, fallback_id=None) == "pl_second"
        assert prepare_route_persistence._destination_identity(coordinate_destination, options=coordinate_options, fallback_id=None) == "pl_coordinate"
        assert prepare_route_persistence._destination_identity(coordinate_destination, options=[("unmatched", "pl_other"), ({"lat": "bad"}, "pl_bad")], fallback_id="pl_fallback") == "pl_fallback"

    async def test_multi_branch_commit_uses_selected_digest_opaque_id(self):
        ctx = _ctx()
        set_id = self._seed_set()
        trip_state.bind_discovery_set(ctx.session, set_id)
        record = discovery_store.load_discovery_set(set_id, session_id=ctx.session_id)
        first, duplicate, second = record["places"]
        destinations = [first, duplicate, second]

        async def provider_prepare(
            *_args,
            resolved_destination=None,
            **_kwargs,
        ):
            prepared = _prepared_leg()
            alternate_route = [
                {**step, "route_id": "B" if step.get("type") == "SUBWAY" else step.get("route_id")}
                for step in prepared.parsed_routes[0]
            ]
            prepared.parsed_routes.append(alternate_route)
            prepared.scored.append(
                {
                    **prepared.scored[0],
                    "index": 1,
                    "score": 80,
                    "rank": 2,
                }
            )
            if resolved_destination is not None:
                prepared.destination_place = ResolvedPlace(
                    name=resolved_destination.name,
                    latitude=resolved_destination.latitude,
                    longitude=resolved_destination.longitude,
                    source=resolved_destination.source,
                    address=resolved_destination.address,
                    place_id=resolved_destination.provider_place_id,
                )
            return prepared

        with patch(
            "app.services.agent.tools.route.prepare_route_branches.prepare_single_leg",
            new=provider_prepare,
        ):
            prepared_result = await prepare_route_options.execute(
                {
                    "destination_place_ids": [
                        first["place_id"],
                        second["place_id"],
                    ],
                    "max_candidates": 3,
                },
                ctx,
            )

        assert prepared_result.ok, prepared_result.error
        candidates = prepared_result.data["candidates"]
        assert [candidate["destination_place_id"] for candidate in candidates] == [place["place_id"] for place in destinations]
        candidate_set = candidate_store.load_candidate_set(
            prepared_result.data["candidate_set_id"],
            session_id=ctx.session_id,
        )
        assert candidate_set["destination_place_id"] is None
        assert [entry["digest"]["destination_place_id"] for entry in candidate_set["candidates"]] == [place["place_id"] for place in destinations]
        for candidate, entry, destination in zip(
            candidates, candidate_set["candidates"], destinations, strict=False
        ):
            assert candidate["candidate_id"] == entry["candidate_id"]
            assert entry["destination_place"]["place_id"] == destination["place_id"]
            assert entry["digest"]["_canonical_itinerary"]["destination"]["place_id"] == destination["place_id"]

        selected = candidates[2]
        route_result = await present_route.execute(
            {
                "candidate_id": selected["candidate_id"],
                "lead_in": "The route options were close, so I chose this one for your trip.",
                "follow_up": "",
                "reason_code": "meets_hard_constraints",
            },
            ctx,
        )
        assert route_result.ok, route_result.error
        card_event = next(event for event in route_result.events if event.type == "route_card")
        assert card_event.destination["place_id"] == second["place_id"]
        assert card_event.itinerary["destination"]["place_id"] == second["place_id"]
        assert route_result.session_route_cards[0]["canonical_itinerary"]["destination"]["place_id"] == second["place_id"]
        session_module.add_route_cards(ctx.session, route_result.session_route_cards)
        assert ctx.session["active_trip"]["destination"]["place_id"] == second["place_id"]
        assert trip_state.get_trip_state(ctx.session)["selected_place_id"] == second["place_id"]
        presented_set = candidate_store.load_candidate_set(
            prepared_result.data["candidate_set_id"],
            session_id=ctx.session_id,
        )
        assert presented_set["selected_candidate_id"] == selected["candidate_id"]
        assert presented_set["candidates"][2]["digest"]["destination_place_id"] == second["place_id"]

    async def test_multi_branch_invalid_digest_fails_before_reservation(self):
        ctx = _ctx()
        set_id = self._seed_set()
        trip_state.bind_discovery_set(ctx.session, set_id)
        record = discovery_store.load_discovery_set(set_id, session_id=ctx.session_id)
        first, second = record["places"][0], record["places"][2]

        async def provider_prepare(
            *_args,
            resolved_destination=None,
            **_kwargs,
        ):
            prepared = _prepared_leg()
            if resolved_destination is not None:
                prepared.destination_place = ResolvedPlace(
                    name=resolved_destination.name,
                    latitude=resolved_destination.latitude,
                    longitude=resolved_destination.longitude,
                    source=resolved_destination.source,
                    address=resolved_destination.address,
                    place_id=resolved_destination.provider_place_id,
                )
            return prepared

        with patch(
            "app.services.agent.tools.route.prepare_route_branches.prepare_single_leg",
            new=provider_prepare,
        ):
            prepared_result = await prepare_route_options.execute(
                {
                    "destination_place_ids": [first["place_id"], second["place_id"]],
                },
                ctx,
            )

        assert prepared_result.ok, prepared_result.error
        candidate_set_id = prepared_result.data["candidate_set_id"]
        candidate = prepared_result.data["candidates"][0]
        stored = candidate_store.load_candidate_set(
            candidate_set_id,
            session_id=ctx.session_id,
        )
        tampered_entry = next(
            entry
            for entry in stored["candidates"]
            if entry["candidate_id"] == candidate["candidate_id"]
        )
        tampered_entry["digest"]["destination_place_id"] = "ChIJ-provider"
        with (
            patch(
                "app.services.agent.candidate_store.get_candidate",
                return_value=(stored, tampered_entry, None),
            ),
            patch("app.services.agent.candidate_store.mark_presented") as mark_presented,
        ):
            result = await present_route.execute(
                {
                    "candidate_id": candidate["candidate_id"],
                    "lead_in": "The route options were close, so I chose this one for your trip.",
                    "follow_up": "",
                    "reason_code": "meets_hard_constraints",
                },
                ctx,
            )

        assert not result.ok
        assert "not bound to the session discovery place" in result.error
        mark_presented.assert_not_called()
        unchanged = candidate_store.load_candidate_set(
            candidate_set_id,
            session_id=ctx.session_id,
        )
        assert not unchanged["presented"]
        assert unchanged["selected_candidate_id"] is None

    async def test_unpresented_older_destination_id_is_rejected_without_rebinding(self):
        ctx = _ctx()
        set_b = self._seed_set()
        set_a = self._seed_set()
        trip_state.bind_discovery_set(ctx.session, set_b)
        record_b = discovery_store.load_discovery_set(set_b, session_id="sess-test")
        selected_place_id = record_b["places"][0]["place_id"]
        trip_state.bind_selected_place(ctx.session, selected_place_id)
        record_a = discovery_store.load_discovery_set(set_a, session_id="sess-test")
        dest_a = record_a["places"][0]
        captured: dict = {}

        async def fake_prepare(
            *_args,
            resolved_destination=None,
            **_kwargs,
        ):
            captured["resolved_destination"] = resolved_destination
            prepared = _prepared_leg()
            if resolved_destination is not None:
                prepared.destination_place = resolved_destination
            return prepared

        with patch(
            "app.services.agent.tools.route.prepare_route_options.prepare_single_leg",
            new=fake_prepare,
        ):
            result = await prepare_route_options.execute(
                {
                    "destination": "Completely Different Text",
                    "destination_place_id": dest_a["place_id"],
                    # Legacy callers could supply the old set directly.  The
                    # route tool must ignore it and use server-owned state.
                    "discovery_set_id": set_a,
                },
                ctx,
            )
        assert not result.ok
        assert "destination place reference is invalid" in result.error
        # An opaque id from an older set is not authorized unless it was
        # actually presented; a stale legacy set id must not bypass that rule.
        assert captured == {}
        state = trip_state.get_trip_state(ctx.session)
        assert state["active_discovery_set_id"] == set_b
        assert state["selected_place_id"] == selected_place_id

    async def test_unused_explicit_set_with_free_text_does_not_rebind(self):
        ctx = _ctx()
        set_b = self._seed_set()
        set_a = self._seed_set()
        trip_state.bind_discovery_set(ctx.session, set_b)
        record_b = discovery_store.load_discovery_set(set_b, session_id="sess-test")
        place_b = record_b["places"][0]["place_id"]
        trip_state.bind_selected_place(ctx.session, place_b)

        with patch(
            "app.services.agent.tools.route.prepare_route_options.prepare_single_leg",
            new=AsyncMock(return_value=_prepared_leg()),
        ):
            result = await prepare_route_options.execute(
                {"destination": "Barclays Center", "discovery_set_id": set_a},
                ctx,
            )
        assert result.ok
        # The explicit set never participated in canonical resolution, so the
        # active discovery context stays exactly as it was.
        state = trip_state.get_trip_state(ctx.session)
        assert state["active_discovery_set_id"] == set_b
        assert state["selected_place_id"] == place_b
        record_payload = candidate_store.load_candidate_set(
            result.data["candidate_set_id"],
            session_id="sess-test",
        )
        assert record_payload["discovery_set_id"] is None
        assert record_payload["destination_place_id"] is None

    async def test_unpresented_older_waypoint_ids_are_rejected_without_rebinding(self):
        ctx = _ctx()
        set_b = self._seed_set()
        set_a = self._seed_set()
        trip_state.bind_discovery_set(ctx.session, set_b)
        record_b = discovery_store.load_discovery_set(set_b, session_id="sess-test")
        selected_place_id = record_b["places"][0]["place_id"]
        trip_state.bind_selected_place(ctx.session, selected_place_id)
        destination_b = record_b["places"][2]
        record_a = discovery_store.load_discovery_set(set_a, session_id="sess-test")
        wp_a1, wp_a2, _dest_a = record_a["places"]
        captured: list[dict] = []

        async def fake_prepare(
            tool_input,
            *_args,
            resolved_origin=None,
            resolved_destination=None,
            **_kwargs,
        ):
            captured.append(dict(tool_input))
            prepared = _prepared_leg()
            if resolved_origin is not None:
                prepared.origin_place = resolved_origin
            if resolved_destination is not None:
                prepared.destination_place = resolved_destination
            return prepared

        with patch(
            "app.services.agent.tools.route.prepare_route_options.prepare_single_leg",
            new=fake_prepare,
        ):
            result = await prepare_route_options.execute(
                {
                    "destination": "Take me to the Lucali downtown one",
                    "destination_place_id": destination_b["place_id"],
                    "waypoints": [wp_a1["place_id"], wp_a2["place_id"]],
                    # Neither an old opaque waypoint nor a legacy set id may
                    # bypass the active server-owned discovery context.
                    "discovery_set_id": set_a,
                },
                ctx,
            )
        assert not result.ok
        assert "waypoint place reference is invalid" in result.error
        # Invalid older waypoint ids fail before route-provider execution and
        # cannot rebind the active discovery context or selected place.
        assert captured == []
        state = trip_state.get_trip_state(ctx.session)
        assert state["active_discovery_set_id"] == set_b
        assert state["selected_place_id"] == selected_place_id
