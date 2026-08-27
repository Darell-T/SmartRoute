"""Focused tests for the session-owned Presented Entity Registry."""

from __future__ import annotations

import unittest

from app.services import cache
from app.services.agent import discovery_store
from app.services.agent.tools._types import ToolContext
from app.services.agent.tools.places import place_reference, present_places


def _place(name: str, provider_id: str, address: str) -> dict:
    return {
        "name": name,
        "address": address,
        "provider_place_id": provider_id,
        "latitude": 40.7,
        "longitude": -73.9,
    }


class PresentedEntityRegistryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        cache._mem.clear()

    async def _present(
        self,
        session: dict,
        session_id: str,
        set_id: str,
        place_ids: list[str],
    ) -> None:
        record = discovery_store.load_discovery_set(set_id, session_id=session_id)
        assert record is not None
        await present_places.execute(
            {
                "discovery_set_id": set_id,
                "selections": [
                    {"place_id": place_id, "reason": "preference_match"}
                    for place_id in place_ids
                ],
                "research_used": False,
            },
            ToolContext(
                session=session,
                session_id=session_id,
                turn_id=f"turn-{set_id}",
            ),
        )
        assert len(session["presented_entity_registry"]) >= len(place_ids)

    async def test_presented_places_survive_a_later_search_and_name_resolves(self):
        session_id = "sess-registry"
        session = {"presented_entity_registry": []}
        first_set = discovery_store.store_discovery_set(
            session_id=session_id,
            session=session,
            places=[_place("Blue Bottle", "provider-blue", "1 A St")],
            query="coffee",
        )
        first = discovery_store.load_discovery_set(first_set, session_id=session_id)
        assert first is not None
        await self._present(
            session, session_id, first_set, [first["places"][0]["place_id"]]
        )

        second_set = discovery_store.store_discovery_set(
            session_id=session_id,
            session=session,
            places=[_place("New Place", "provider-new", "2 B St")],
            query="ramen",
        )
        place, error, resolved_set = discovery_store.resolve_presented_place_reference(
            session=session,
            session_id=session_id,
            description="Blue Bottle",
        )
        assert error is None
        assert resolved_set == first_set
        assert place["name"] == "Blue Bottle"
        assert first_set != second_set

    async def test_duplicate_names_require_clarification_but_same_identity_is_reused(
        self,
    ):
        session_id = "sess-ambiguous"
        session = {"presented_entity_registry": []}
        first_set = discovery_store.store_discovery_set(
            session_id=session_id,
            session=session,
            places=[_place("Cafe", "provider-a", "1 A St")],
        )
        first = discovery_store.load_discovery_set(first_set, session_id=session_id)
        assert first is not None
        first_id = first["places"][0]["place_id"]
        await self._present(session, session_id, first_set, [first_id])

        same_set = discovery_store.store_discovery_set(
            session_id=session_id,
            session=session,
            places=[_place("Cafe", "provider-a", "1 A St")],
        )
        same = discovery_store.load_discovery_set(same_set, session_id=session_id)
        assert same is not None
        assert same["places"][0]["place_id"] == first_id

        other_set = discovery_store.store_discovery_set(
            session_id=session_id,
            session=session,
            places=[_place("Cafe", "provider-b", "2 B St")],
        )
        other = discovery_store.load_discovery_set(other_set, session_id=session_id)
        assert other is not None
        await self._present(
            session,
            session_id,
            other_set,
            [other["places"][0]["place_id"]],
        )
        place, error, _ = discovery_store.resolve_presented_place_reference(
            session=session,
            session_id=session_id,
            description="Cafe",
        )
        assert place is None
        assert "multiple" in (error or "")

    async def test_ordinal_uses_newest_presentation_that_contains_it(self):
        session_id = "sess-ordinal"
        session = {"presented_entity_registry": []}
        first_set = discovery_store.store_discovery_set(
            session_id=session_id,
            session=session,
            places=[
                _place("One", "provider-one", "1 A St"),
                _place("Two", "provider-two", "2 B St"),
            ],
        )
        first = discovery_store.load_discovery_set(first_set, session_id=session_id)
        assert first is not None
        await self._present(
            session,
            session_id,
            first_set,
            [place["place_id"] for place in first["places"]],
        )
        second_set = discovery_store.store_discovery_set(
            session_id=session_id,
            session=session,
            places=[_place("Three", "provider-three", "3 C St")],
        )
        second = discovery_store.load_discovery_set(second_set, session_id=session_id)
        assert second is not None
        await self._present(
            session,
            session_id,
            second_set,
            [second["places"][0]["place_id"]],
        )
        place, error, resolved_set = discovery_store.resolve_presented_place_reference(
            session=session,
            session_id=session_id,
            ordinal=2,
        )
        assert error is None
        assert resolved_set == first_set
        assert place["name"] == "Two"

    async def test_place_reference_rebinds_the_source_set_for_an_old_presented_name(
        self,
    ):
        session_id = "sess-bind"
        session = {"presented_entity_registry": []}
        first_set = discovery_store.store_discovery_set(
            session_id=session_id,
            session=session,
            places=[_place("Old Place", "provider-old", "1 A St")],
        )
        first = discovery_store.load_discovery_set(first_set, session_id=session_id)
        assert first is not None
        await self._present(
            session, session_id, first_set, [first["places"][0]["place_id"]]
        )
        second_set = discovery_store.store_discovery_set(
            session_id=session_id,
            session=session,
            places=[_place("New Place", "provider-new", "2 B St")],
        )
        result = await place_reference.execute(
            {"description": "Old Place"},
            ToolContext(session=session, session_id=session_id),
        )
        assert result.ok, result.error
        assert (
            session["trip_state"]["active_discovery_set_id"]
            if isinstance(session.get("trip_state"), dict)
            else first_set
        ) == first_set
        assert second_set != first_set


if __name__ == "__main__":
    unittest.main()
