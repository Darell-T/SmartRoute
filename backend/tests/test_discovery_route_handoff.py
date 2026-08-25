"""Focused tests for the discovery-to-route handoff (Phase 2A).

Covers routing by opaque destination_place_id, server-side canonical
identity preservation, the tool-start label, and the single-leg provider
handoff. Moved out of test_single_agent_route_tools.py so that phase does
not grow further.
"""
from __future__ import annotations
import json
import unittest
from unittest.mock import AsyncMock, patch
from app.services.agent import candidate_store, discovery_store, trip_state
from app.services.agent import session as session_module
from app.services.agent.tools.places import place_reference
from app.services.agent.tools.route import prepare_route_options, present_route
from app.services.agent.tools.location_resolution import ResolvedPlace

from tests.discovery_route_handoff_test_support import (
    DiscoveryRouteHandoffTestMixin,
    _ctx,
    _prepared_leg,
)


class DiscoveryDestinationHandoffTests(DiscoveryRouteHandoffTestMixin, unittest.IsolatedAsyncioTestCase):
    async def test_destination_place_id_wins_over_conflicting_free_text(self):
        ctx = _ctx()
        set_id = self._seed_set()
        trip_state.bind_discovery_set(ctx.session, set_id)
        record = discovery_store.load_discovery_set(set_id, session_id="sess-test")
        dest_place = record["places"][0]
        captured: dict = {}

        async def fake_prepare(tool_input, tool_ctx, timings, *, resolved_origin=None, resolved_destination=None, **kwargs):
            captured["tool_input"] = dict(tool_input)
            captured["resolved_destination"] = resolved_destination
            prepared = _prepared_leg()
            if resolved_destination is not None:
                prepared.destination_place = resolved_destination
            return prepared

        geocode = AsyncMock(return_value=(40.1, -73.1))
        with (
            patch(
                "app.services.agent.tools.route.prepare_route_options.prepare_single_leg",
                new=fake_prepare,
            ),
            patch(
                "app.services.agent.tools.location_resolution.geo.geocode_address_with_reason",
                new=geocode,
            ),
        ):
            result = await prepare_route_options.execute(
                {
                    "destination": "Completely Different Text",
                    "destination_place_id": dest_place["place_id"],
                },
                ctx,
            )
        self.assertTrue(result.ok)
        resolved = captured["resolved_destination"]
        self.assertEqual(resolved.name, "Di Fara Pizza")
        self.assertEqual(resolved.latitude, 40.6298)
        self.assertEqual(resolved.longitude, -73.9616)
        self.assertEqual(resolved.address, "1424 Av J")
        self.assertEqual(resolved.place_id, dest_place["place_id"])
        self.assertEqual(resolved.provider_place_id, "ChIJ-dest")
        self.assertEqual(captured["tool_input"]["destination"], "Di Fara Pizza")
        geocode_args = " ".join(str(args) for args in geocode.call_args_list)
        self.assertNotIn("Completely Different Text", geocode_args)
        state = trip_state.get_trip_state(ctx.session)
        self.assertEqual(state["destination"], "Di Fara Pizza")
        self.assertEqual(state["selected_place_id"], dest_place["place_id"])

    async def test_multi_stop_opaque_ids_never_surface_and_duplicates_keep_own_coords(self):
        ctx = _ctx()
        set_id = self._seed_set()
        trip_state.bind_discovery_set(ctx.session, set_id)
        record = discovery_store.load_discovery_set(set_id, session_id="sess-test")
        waypoint_a, waypoint_b, dest_place = record["places"]
        seen: list[dict] = []

        async def fake_prepare(tool_input, tool_ctx, timings, *, resolved_origin=None, resolved_destination=None, **kwargs):
            seen.append(
                {
                    "input": dict(tool_input),
                    "resolved_origin": resolved_origin,
                    "resolved_destination": resolved_destination,
                }
            )
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
                    "destination_place_id": dest_place["place_id"],
                    "waypoints": [waypoint_a["place_id"], waypoint_b["place_id"]],
                },
                ctx,
            )
        self.assertTrue(result.ok)
        self.assertEqual(len(seen), 3)
        # Duplicate display names must not swap stored coordinates: A then B
        # then the destination, each with its own lat/lng.
        self.assertEqual(seen[0]["resolved_destination"].name, "Di Fara Pizza")
        self.assertEqual(seen[0]["resolved_destination"].latitude, 40.6298)
        self.assertEqual(seen[1]["resolved_destination"].name, "Di Fara Pizza")
        self.assertEqual(seen[1]["resolved_destination"].latitude, 40.6360)
        self.assertEqual(seen[1]["resolved_origin"].latitude, 40.6298)
        self.assertEqual(seen[2]["resolved_destination"].name, "Lucali")
        self.assertEqual(seen[2]["resolved_destination"].latitude, 40.6810)
        self.assertEqual(seen[2]["resolved_origin"].latitude, 40.6360)

        state = trip_state.get_trip_state(ctx.session)
        self.assertEqual(state["waypoints"], ["Di Fara Pizza", "Di Fara Pizza"])
        self.assertEqual(state["destination"], "Lucali")
        persisted_blob = json.dumps(
            {
                "origin": state["origin"],
                "destination": state["destination"],
                "waypoints": state["waypoints"],
            },
            default=str,
        )
        self.assertNotIn("pl_", persisted_blob)

        record_payload = candidate_store.load_candidate_set(
            result.data["candidate_set_id"],
            session_id="sess-test",
        )
        self.assertIsNotNone(record_payload)
        for field in ("origin_raw", "destination_raw", "waypoints"):
            value = record_payload.get(field)
            self.assertNotIn("pl_", json.dumps(value, default=str), field)
        self.assertEqual(record_payload["destination_raw"], "Lucali")
        self.assertEqual(record_payload["waypoints"], ["Di Fara Pizza", "Di Fara Pizza"])
        self.assertEqual(record_payload["destination_place"]["name"], "Lucali")
        self.assertEqual(record_payload["destination_place"]["lat"], 40.6810)
        self.assertEqual(
            record_payload["destination_place"]["place_id"], dest_place["place_id"]
        )
        for segment in record_payload["aggregate_segments"][0]:
            self.assertNotIn(
                "pl_",
                json.dumps(segment["origin_place"], default=str),
            )
            self.assertNotIn(
                "pl_",
                json.dumps(segment["destination_place"], default=str),
            )

    async def test_free_text_destination_with_discovery_waypoint_keeps_provenance_separate(self):
        ctx = _ctx()
        set_id = self._seed_set()
        trip_state.bind_discovery_set(ctx.session, set_id)
        record = discovery_store.load_discovery_set(set_id, session_id=ctx.session_id)
        waypoint = record["places"][0]
        free_text_destination = "42nd Street and 8th Avenue"

        async def fake_prepare(
            tool_input,
            tool_ctx,
            timings,
            *,
            resolved_origin=None,
            resolved_destination=None,
            **kwargs,
        ):
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
            prepared_result = await prepare_route_options.execute(
                {
                    "destination": free_text_destination,
                    "waypoints": [waypoint["place_id"]],
                },
                ctx,
            )

        self.assertTrue(prepared_result.ok, prepared_result.error)
        candidate_set_id = prepared_result.data["candidate_set_id"]
        candidate = prepared_result.data["candidates"][0]
        candidate_set = candidate_store.load_candidate_set(
            candidate_set_id,
            session_id=ctx.session_id,
        )
        self.assertIsNotNone(candidate_set)
        self.assertIsNone(candidate_set["destination_discovery_set_id"])
        self.assertEqual(candidate_set["waypoint_discovery_set_id"], set_id)

        state_before_presentation = trip_state.get_trip_state(ctx.session)
        self.assertIsNone(state_before_presentation["selected_place_id"])
        self.assertEqual(state_before_presentation["destination"], free_text_destination)

        presented_result = await present_route.execute(
            {
                "candidate_id": candidate["candidate_id"],
                "lead_in": "The route options were close, so I chose this one for your trip.",
                "follow_up": "",
                "reason_code": "meets_hard_constraints",
            },
            ctx,
        )

        self.assertTrue(presented_result.ok, presented_result.error)
        card_event = next(
            event for event in presented_result.events if event.type == "route_card"
        )
        self.assertEqual(card_event.destination["label"], free_text_destination)
        self.assertEqual(
            presented_result.session_route_cards[0]["destination"]["label"],
            free_text_destination,
        )
        session_module.add_route_cards(ctx.session, presented_result.session_route_cards)
        self.assertEqual(
            ctx.session["active_trip"]["destination"]["label"],
            free_text_destination,
        )
        state_after_presentation = trip_state.get_trip_state(ctx.session)
        self.assertIsNone(state_after_presentation["selected_place_id"])
        self.assertNotEqual(state_after_presentation["destination"], waypoint["name"])
        self.assertEqual(state_after_presentation["destination"], free_text_destination)

    async def test_routes_by_opaque_destination_place_id_without_retyping_label(self):
        ctx = _ctx()
        set_id = self._seed_set()
        trip_state.bind_discovery_set(ctx.session, set_id)
        record = discovery_store.load_discovery_set(set_id, session_id="sess-test")
        place_id = record["places"][0]["place_id"]
        details = await place_reference.execute(
            {"place_id": place_id, "discovery_set_id": set_id}, ctx
        )
        self.assertTrue(details.ok)
        captured: dict = {}

        async def fake_prepare(tool_input, tool_ctx, timings, *, resolved_origin=None, resolved_destination=None, **kwargs):
            captured["tool_input"] = dict(tool_input)
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
                {"destination_place_id": place_id},
                ctx,
            )
        self.assertTrue(result.ok)
        # Routing follows the opaque id, never a model-retyped label.
        self.assertEqual(captured["resolved_destination"].name, "Di Fara Pizza")
        self.assertEqual(captured["resolved_destination"].place_id, place_id)
        self.assertEqual(
            captured["resolved_destination"].provider_place_id, "ChIJ-dest"
        )
        self.assertEqual(captured["tool_input"]["destination"], "Di Fara Pizza")
        state = trip_state.get_trip_state(ctx.session)
        self.assertEqual(state["selected_place_id"], place_id)

    async def test_provider_endpoint_id_cannot_replace_candidate_or_presented_opaque_id(self):
        ctx = _ctx()
        set_id = self._seed_set()
        trip_state.bind_discovery_set(ctx.session, set_id)
        record = discovery_store.load_discovery_set(set_id, session_id=ctx.session_id)
        destination = record["places"][0]
        provider_endpoint: dict[str, object] = {}

        async def provider_prepare(
            tool_input,
            tool_ctx,
            timings,
            *,
            resolved_origin=None,
            resolved_destination=None,
            **kwargs,
        ):
            provider_endpoint["resolved"] = resolved_destination
            prepared = _prepared_leg()
            if resolved_destination is not None:
                # Simulate a provider-shaped endpoint coming back from the
                # route seam. Persistence must restore the verified opaque
                # destination reference before candidate finalization.
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
            "app.services.agent.tools.route.prepare_route_options.prepare_single_leg",
            new=provider_prepare,
        ):
            prepared_result = await prepare_route_options.execute(
                {
                    "destination": "A conflicting label",
                    "destination_place_id": destination["place_id"],
                },
                ctx,
            )

        self.assertTrue(prepared_result.ok, prepared_result.error)
        self.assertEqual(
            provider_endpoint["resolved"].provider_place_id,
            destination["provider_place_id"],
        )
        candidate = prepared_result.data["candidates"][0]
        self.assertEqual(candidate["destination_place_id"], destination["place_id"])
        candidate_set = candidate_store.load_candidate_set(
            prepared_result.data["candidate_set_id"],
            session_id=ctx.session_id,
        )
        self.assertEqual(
            candidate_set["destination_place"]["place_id"], destination["place_id"]
        )
        entry = candidate_set["candidates"][0]
        self.assertEqual(
            entry["digest"]["destination_place_id"], destination["place_id"]
        )

        presented = await place_reference.execute(
            {"place_id": destination["place_id"], "discovery_set_id": set_id},
            ctx,
        )
        self.assertTrue(presented.ok, presented.error)
        route_result = await present_route.execute(
            {
                "candidate_id": candidate["candidate_id"],
                "lead_in": "The route options were close, so I chose this one for your trip.",
                "follow_up": "",
                "reason_code": "meets_hard_constraints",
            },
            ctx,
        )
        self.assertTrue(route_result.ok, route_result.error)
        card_event = next(event for event in route_result.events if event.type == "route_card")
        self.assertEqual(card_event.destination["place_id"], destination["place_id"])
        session_module.add_route_cards(ctx.session, route_result.session_route_cards)
        self.assertEqual(
            ctx.session["active_trip"]["destination"]["place_id"],
            destination["place_id"],
        )
        self.assertEqual(
            trip_state.get_trip_state(ctx.session)["selected_place_id"],
            destination["place_id"],
        )
