"""Focused destination-reference boundary tests for stale selected places.

Covers ``_location.resolve_destination_reference`` and the real
``prepare_route_options`` executor boundary for the Batch E1 label-only
fallback finding: a session-selected place that can no longer resolve from
its active session-owned discovery set fails with a bounded domain error
even when a non-empty destination label is supplied -- a retyped label never
regains routing authority from a stale selection. Live selections still
canonicalize through the stored opaque identity, explicit opaque ids keep
precedence, and a genuinely new route request after the turn-start new-trip
reset uses the normal destination/provider path. Only the genuine provider
route seam is scripted; discovery/candidate/trip stores stay real.
"""

from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, patch

from app.services.agent import (
    candidate_store,
    discovery_store,
    presented_entity_registry,
    trip_state,
)
from app.services.agent.tools.route import prepare_route_options
from app.services.agent.tools.location_resolution import resolve_destination_reference
from app.services.agent.tools._types import ToolContext
from app.services import cache
from tests.conversation.conversation_matrix_harness import make_leg


def _ctx(session_id: str = "sess-e1-loc") -> ToolContext:
    return ToolContext(
        session={},
        session_id=session_id,
        turn_id="t-e1-loc",
        now_et="2026-08-08T12:00:00-04:00",
        origin={"lat": 40.75, "lng": -73.99},
    )


def _seed(session: dict, session_id: str, *, places=None) -> tuple[str, dict]:
    """One real discovery set bound as the session's active context."""

    set_id = discovery_store.store_discovery_set(
        session_id=session_id,
        places=places
        if places is not None
        else [
            {"name": "A Pizza", "latitude": 40.71, "longitude": -73.98},
            {"name": "B Pizza", "address": "2 B Ave",
             "latitude": 40.72, "longitude": -73.97,
             "provider_place_id": "ChIJ-bbb"},
        ],
        query="pizza",
    )
    trip_state.bind_discovery_set(session, set_id)
    record = discovery_store.load_discovery_set(set_id, session_id=session_id)
    assert record is not None
    return set_id, record


class DestinationReferenceBoundaryTests(unittest.IsolatedAsyncioTestCase):
    """Stale vs live selected-place semantics at the destination boundary."""

    def setUp(self):
        cache._mem.clear()

    def _expired_clock(self, record: dict):
        return patch(
            "app.services.agent.discovery_store.time.time",
            return_value=float(record["expires_at"]) + 60.0,
        )

    async def test_live_selected_place_empty_destination_resolves_via_opaque_id(self):
        ctx = _ctx()
        set_id, record = _seed(ctx.session, ctx.session_id)
        place = record["places"][1]
        trip_state.bind_selected_place(ctx.session, place["place_id"])
        resolved, place_id, error, used_set = await resolve_destination_reference(
            {}, {"destination": ""}, ctx)
        self.assertIsNone(error, f"error={error!r}")
        self.assertEqual(place_id, place["place_id"])
        self.assertEqual(used_set, set_id)
        self.assertEqual(resolved.name, "B Pizza")
        self.assertEqual(resolved.latitude, 40.72)
        self.assertEqual(resolved.place_id, place["place_id"])
        self.assertEqual(resolved.provider_place_id, "ChIJ-bbb")

    async def test_live_selected_place_matching_label_canonicalizes_through_opaque_id(self):
        ctx = _ctx()
        set_id, record = _seed(ctx.session, ctx.session_id)
        place = record["places"][1]
        trip_state.bind_selected_place(ctx.session, place["place_id"])
        for label in ("B Pizza", "2 B Ave"):
            with self.subTest(label=label):
                resolved, place_id, error, used_set = (
                    await resolve_destination_reference(
                        {}, {"destination": label}, ctx)
                )
                self.assertIsNone(error, f"error={error!r}")
                self.assertEqual(place_id, place["place_id"],
                                 "matching label resolves through the opaque id")
                self.assertEqual(used_set, set_id)
                self.assertEqual(resolved.name, "B Pizza",
                                 "stored identity wins, never a re-geocoded label")
                self.assertEqual(resolved.latitude, 40.72)

    async def test_live_selected_place_non_matching_label_uses_normal_free_text_path(self):
        ctx = _ctx()
        _set_id, record = _seed(ctx.session, ctx.session_id)
        place = record["places"][1]
        trip_state.bind_selected_place(ctx.session, place["place_id"])
        resolved, place_id, error, used_set = await resolve_destination_reference(
            {}, {"destination": "Brooklyn Bridge"}, ctx)
        self.assertIsNone(resolved)
        self.assertIsNone(place_id)
        self.assertIsNone(error)
        self.assertIsNone(used_set)

    async def test_presented_name_resolves_after_a_later_search_becomes_active(self):
        ctx = _ctx()
        first_set, first = _seed(
            ctx.session,
            ctx.session_id,
            places=[{
                "name": "Prince Street Pizza",
                "address": "27 Prince Street",
                "latitude": 40.7231,
                "longitude": -73.9946,
                "provider_place_id": "ChIJ-prince",
            }],
        )
        old_place = first["places"][0]
        presented_entity_registry.record(
            ctx.session,
            session_id=ctx.session_id,
            discovery_set_id=first_set,
            places=[old_place],
        )
        second_set, _second = _seed(
            ctx.session,
            ctx.session_id,
            places=[{
                "name": "Lo Duca Pizza",
                "latitude": 40.635,
                "longitude": -73.962,
            }],
        )

        resolved, place_id, error, used_set = await resolve_destination_reference(
            {}, {"destination": "Prince Street Pizza"}, ctx
        )

        self.assertIsNone(error)
        self.assertEqual(resolved.name, "Prince Street Pizza")
        self.assertEqual(resolved.place_id, old_place["place_id"])
        self.assertEqual(resolved.provider_place_id, "ChIJ-prince")
        self.assertEqual(place_id, old_place["place_id"])
        self.assertEqual(used_set, first_set)
        self.assertNotEqual(used_set, second_set)
        state = trip_state.get_trip_state(ctx.session)
        self.assertEqual(state["active_discovery_set_id"], first_set)
        self.assertEqual(state["selected_place_id"], old_place["place_id"])

    async def test_stale_selected_place_label_only_returns_bounded_error(self):
        ctx = _ctx()
        _set_id, record = _seed(ctx.session, ctx.session_id)
        place = record["places"][1]
        trip_state.bind_selected_place(ctx.session, place["place_id"])
        with self._expired_clock(record):
            resolved, place_id, error, used_set = await resolve_destination_reference(
                {}, {"destination": "B Pizza"}, ctx)
        self.assertIsNone(resolved)
        self.assertIsNone(place_id)
        self.assertIsNone(used_set)
        self.assertIsNotNone(error)
        self.assertIn("no longer available", error)

    async def test_stale_selected_place_empty_destination_returns_bounded_error(self):
        ctx = _ctx()
        _set_id, record = _seed(ctx.session, ctx.session_id)
        place = record["places"][1]
        trip_state.bind_selected_place(ctx.session, place["place_id"])
        with self._expired_clock(record):
            resolved, place_id, error, used_set = await resolve_destination_reference(
                {}, {"destination": ""}, ctx)
        self.assertIsNone(resolved)
        self.assertIsNone(place_id)
        self.assertIsNone(used_set)
        self.assertIsNotNone(error)
        self.assertIn("no longer available", error)

    async def test_explicit_opaque_id_keeps_precedence_over_selected_place(self):
        ctx = _ctx()
        set_id, record = _seed(ctx.session, ctx.session_id)
        place_a, place_b = record["places"][0], record["places"][1]
        trip_state.bind_selected_place(ctx.session, place_a["place_id"])
        resolved, place_id, error, used_set = await resolve_destination_reference(
            {"destination_place_id": place_b["place_id"]},
            {"destination": "B Pizza"}, ctx)
        self.assertIsNone(error, f"error={error!r}")
        self.assertEqual(place_id, place_b["place_id"],
                         "explicit opaque id wins over the selected place")
        self.assertEqual(used_set, set_id)
        self.assertEqual(resolved.name, "B Pizza")

    async def test_explicit_invalid_opaque_id_fails_bounded_even_with_selection(self):
        ctx = _ctx()
        _set_id, record = _seed(ctx.session, ctx.session_id)
        trip_state.bind_selected_place(ctx.session, record["places"][1]["place_id"])
        resolved, place_id, error, used_set = await resolve_destination_reference(
            {"destination_place_id": "pl_bogus"}, {"destination": "B Pizza"}, ctx)
        self.assertIsNone(resolved)
        self.assertIsNone(place_id)
        self.assertIsNone(used_set)
        self.assertIsNotNone(error)
        self.assertIn("invalid", error)

    async def test_stale_label_only_prepare_executor_fails_bounded(self):
        """P1 probe: a retyped label must never become routing authority."""

        ctx = _ctx()
        _set_id, record = _seed(
            ctx.session, ctx.session_id,
            places=[{"name": "B Pizza", "address": "2 B Ave",
                     "latitude": 40.72, "longitude": -73.97,
                     "provider_place_id": "ChIJ-bbb"}],
        )
        pl_id = record["places"][0]["place_id"]
        trip_state.bind_selected_place(ctx.session, pl_id)
        prepare_mock = AsyncMock(return_value=make_leg(destination="B Pizza"))
        stored: list[str] = []
        original_store = candidate_store.store_candidate_set

        def _recording_store(*args, **kwargs):
            new_id = original_store(*args, **kwargs)
            stored.append(new_id)
            return new_id

        with self._expired_clock(record), patch(
            "app.services.agent.tools.route.prepare_route_options.prepare_single_leg",
            new=prepare_mock), patch(
            "app.services.agent.candidate_store.store_candidate_set",
            new=_recording_store):
            result = await prepare_route_options.execute(
                {"destination": "B Pizza"}, ctx)
        state = trip_state.get_trip_state(ctx.session)
        self.assertFalse(result.ok,
                         "stale label-only prepare must fail bounded")
        self.assertIn("no longer available", result.error or "",
                      f"error={result.error!r}")
        self.assertEqual(prepare_mock.await_count, 0,
                         "provider seam must not be reached from a stale label")
        self.assertEqual(stored, [], "no candidate set stored")
        self.assertEqual(state["destination"], None, "no destination committed")
        self.assertEqual(state["active_candidate_set_id"], None)
        self.assertEqual(state["selected_place_id"], pl_id,
                         "stale selection stays bound after safe failure")

    async def test_new_route_after_reset_uses_normal_provider_path(self):
        """A new explicit destination after the reset resolves normally."""

        ctx = _ctx()
        _set_id, record = _seed(ctx.session, ctx.session_id)
        trip_state.bind_selected_place(ctx.session, record["places"][1]["place_id"])
        # The turn-start new-trip reset clears the stale selected place and
        # discovery context exactly as the loop's reset does.
        trip_state.reset_for_new_trip(ctx.session)
        state = trip_state.get_trip_state(ctx.session)
        self.assertIsNone(state["selected_place_id"])
        self.assertIsNone(state["active_discovery_set_id"])
        prepare_mock = AsyncMock(return_value=make_leg(destination="Barclays Center"))
        with patch(
            "app.services.agent.tools.route.prepare_route_options.prepare_single_leg",
            new=prepare_mock):
            result = await prepare_route_options.execute(
                {"destination": "Barclays Center"}, ctx)
        self.assertTrue(result.ok, f"normal prepare must succeed; {result.error!r}")
        self.assertEqual(prepare_mock.await_count, 1,
                         "provider path reached for the new explicit destination")
        state = trip_state.get_trip_state(ctx.session)
        self.assertEqual(state["destination"], "Barclays Center")


__all__ = ()
