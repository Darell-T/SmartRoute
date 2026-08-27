"""Focused tests for canonical place discovery and stored references."""

from __future__ import annotations

import json
import math
import unittest
from unittest.mock import AsyncMock, patch

from app.services.agent import discovery_store, trip_state
from app.services.agent.tools._types import ToolContext, ToolResult
from app.services.agent.tools.places import (
    discover_places,
    place_reference,
    search_local_places,
)


def _ctx(session_id: str = "sess-disc") -> ToolContext:
    return ToolContext(
        session={},
        session_id=session_id,
        turn_id="t-disc",
        now_et="2026-08-08T12:00:00-04:00",
        origin={"lat": 40.75, "lng": -73.99},
        agent_mode="auto",
        agent_model="claude-test",
        agent_explanation_style="comparative",
    )


def _place(
    name: str,
    open_now=True,
    price_level=None,
    rating=None,
    review_count=None,
    place_id=None,
    borough=None,
):
    place = {
        "name": name,
        "address": f"123 {name} St" + (f", {borough}, NY" if borough else ""),
        "lat": 40.71,
        "lng": -73.98,
        "open_now": open_now,
        "price_level": price_level,
        "rating": rating,
        "review_count": review_count,
        "place_id": place_id,
    }
    if borough:
        place["address_components"] = [
            {"longText": borough, "types": ["sublocality_level_1"]},
            {"longText": "New York", "types": ["locality"]},
        ]
    return place


async def _discover(tool_input: dict, ctx: ToolContext) -> ToolResult:
    areas = [
        str(area).strip()
        for area in (tool_input.get("areas") or [])
        if str(area).strip()
    ]
    scope = (
        {"kind": "boroughs", "values": areas}
        if areas
        else {"kind": "current_location", "values": []}
    )
    return await discover_places.execute(
        {
            "operation": "search",
            "query": tool_input.get("query"),
            "scope": scope,
            "open_now": tool_input.get("open_now"),
            "max_results": tool_input.get("max_results", 8),
            "candidate_names": [],
        },
        ctx,
    )


class SearchLocalPlacesTests(unittest.IsolatedAsyncioTestCase):
    async def test_requires_session(self):
        result = await _discover({"query": "pizza"}, _ctx(session_id=""))
        assert not result.ok
        assert "session" in (result.error or "")

    async def test_open_now_filter_keeps_open_and_unknown(self):
        poi_result = ToolResult(
            ok=True,
            data={
                "results": [
                    _place("Open Spot", open_now=True),
                    _place("Closed Spot", open_now=False),
                    _place("Unknown Spot", open_now=None),
                ]
            },
            summary="3 places",
        )
        with patch.object(
            search_local_places, "execute", new=AsyncMock(return_value=poi_result)
        ):
            result = await _discover({"query": "pizza", "open_now": True}, _ctx())
        assert result.ok
        names = [place["name"] for place in result.data["places"]]
        assert names == ["Open Spot", "Unknown Spot"]

    async def test_empty_results_are_truthful(self):
        poi_result = ToolResult(ok=True, data={"results": []}, summary="none")
        with patch.object(
            search_local_places, "execute", new=AsyncMock(return_value=poi_result)
        ):
            result = await _discover({"query": "pizza"}, _ctx())
        assert result.ok
        assert result.data["discovery_set_id"] is None
        assert result.data["places"] == []
        assert "no matching" not in result.summary.casefold()

    async def test_partial_area_search_preserves_available_places(self):
        successful = ToolResult(
            ok=True,
            data={"results": [_place("Manhattan Pizza", borough="Manhattan")]},
        )
        failed = ToolResult(ok=False, error="place search timed out")
        with patch.object(
            search_local_places,
            "execute",
            new=AsyncMock(side_effect=[successful, failed]),
        ):
            result = await _discover(
                {
                    "query": "pizza",
                    "areas": ["Manhattan", "Brooklyn"],
                    "max_results": 5,
                },
                _ctx(),
            )

        assert result.ok
        assert [place["name"] for place in result.data["places"]] == ["Manhattan Pizza"]
        assert result.data["coverage"]["status"] == "partial"

    async def test_poi_failure_propagates(self):
        poi_result = ToolResult(ok=False, error="place search failed")
        with patch.object(
            search_local_places, "execute", new=AsyncMock(return_value=poi_result)
        ):
            result = await _discover({"query": "pizza"}, _ctx())
        assert not result.ok
        assert "unavailable" in (result.error or "")

    async def test_provider_exception_becomes_recoverable_search_error(self):
        with patch.object(
            search_local_places,
            "execute",
            new=AsyncMock(side_effect=RuntimeError("provider exploded")),
        ):
            result = await _discover({"query": "pizza"}, _ctx())

        assert not result.ok
        assert "place search" in (result.error or "")
        assert "provider exploded" not in (result.error or "")

    async def test_multi_area_search_keeps_one_grounded_result_set(self):
        manhattan_place = _place("Manhattan Pizza", rating=4.7, borough="Manhattan")
        manhattan_place["address"] = "1 E 8th St"
        brooklyn_place = _place("Brooklyn Pizza", rating=4.8, borough="Brooklyn")
        brooklyn_place["address"] = "2 Court St"
        manhattan = ToolResult(
            ok=True,
            data={"results": [manhattan_place]},
            timings={"place_resolution_ms": 10},
        )
        brooklyn = ToolResult(
            ok=True,
            data={"results": [brooklyn_place]},
            timings={"place_resolution_ms": 12},
        )
        search = AsyncMock(side_effect=[manhattan, brooklyn])
        with patch.object(search_local_places, "execute", new=search):
            result = await _discover(
                {
                    "query": "pizza",
                    "areas": ["Manhattan", "Brooklyn"],
                    "max_results": 5,
                },
                _ctx(),
            )

        assert result.ok
        assert result.data["scope"] == {"kind": "boroughs", "values": ["Manhattan", "Brooklyn"]}
        assert all("search_area" not in place for place in result.data["places"])
        record = discovery_store.load_discovery_set(
            result.data["discovery_set_id"],
            session_id="sess-disc",
        )
        assert [place["search_area"] for place in record["places"]] == ["Manhattan", "Brooklyn"]
        assert [call.args[0]["near"] for call in search.await_args_list] == ["Manhattan", "Brooklyn"]
    async def test_stores_set_binds_session_and_exposes_bounded_coordinates(self):
        poi_result = ToolResult(
            ok=True,
            data={
                "results": [
                    _place("A Pizza", rating=4.8, review_count=900, price_level=2),
                    _place("B Pizza", rating=4.2, review_count=100, price_level=1),
                ]
            },
            summary="2 places",
        )
        ctx = _ctx()
        with patch.object(
            search_local_places, "execute", new=AsyncMock(return_value=poi_result)
        ):
            result = await _discover({"query": "pizza"}, ctx)
        assert result.ok
        set_id = result.data["discovery_set_id"]
        assert set_id.startswith("ds_")
        state = trip_state.get_trip_state(ctx.session)
        assert state["active_discovery_set_id"] == set_id
        record = discovery_store.load_discovery_set(set_id, session_id="sess-disc")
        assert record is not None
        for place in result.data["places"]:
            assert place["place_id"].startswith("pl_")
            assert "latitude" not in place
            assert "longitude" not in place
            assert isinstance(place["rider_distance_meters"], float)
            assert math.isfinite(place["rider_distance_meters"])
            assert place["rider_distance_meters"] > 0.0
            assert "40.75" not in json.dumps(place, default=str)
            assert "-73.99" not in json.dumps(place, default=str)
            assert "provider_place_id" not in place
            assert "baseline_score" not in place
            assert "ranking_factors" not in place
        for place in record["places"]:
            assert isinstance(place["latitude"], float)
            assert isinstance(place["longitude"], float)
            assert math.isfinite(place["latitude"])
            assert math.isfinite(place["longitude"])
            assert place["baseline_score"] is not None

    async def test_stores_provider_place_id_server_side_only(self):
        poi_result = ToolResult(
            ok=True,
            data={
                "results": [
                    _place("A Pizza", place_id="ChIJ-abc"),
                ]
            },
            summary="1 place",
        )
        ctx = _ctx()
        with patch.object(
            search_local_places, "execute", new=AsyncMock(return_value=poi_result)
        ):
            result = await _discover({"query": "pizza"}, ctx)
        assert result.ok
        record = discovery_store.load_discovery_set(
            result.data["discovery_set_id"], session_id="sess-disc"
        )
        assert record["places"][0]["provider_place_id"] == "ChIJ-abc"
        assert "ChIJ-abc" not in json.dumps(result.data, default=str)

    async def test_baseline_ranking_is_deterministic_and_does_not_reorder(self):
        from app.services.agent.tools.places.search_local_places import baseline_ranking

        poi_result = ToolResult(
            ok=True,
            data={
                "results": [
                    _place("A Pizza", rating=4.0, review_count=100),
                    _place("B Pizza", rating=4.9, review_count=2000),
                ]
            },
            summary="2 places",
        )
        with patch.object(
            search_local_places, "execute", new=AsyncMock(return_value=poi_result)
        ):
            result = await _discover({"query": "pizza"}, _ctx())
        names = [place["name"] for place in result.data["places"]]
        assert names == ["A Pizza", "B Pizza"]
        record = discovery_store.load_discovery_set(
            result.data["discovery_set_id"],
            session_id="sess-disc",
        )
        scores = [place["baseline_score"] for place in record["places"]]
        assert scores[1] > scores[0]
        sample = _place("Stable", rating=4.5, review_count=300)
        first = baseline_ranking(sample)
        second = baseline_ranking(sample)
        assert first == second


class GetPlaceDetailsTests(unittest.IsolatedAsyncioTestCase):
    async def _seed_set(self, session_id: str = "sess-disc") -> str:
        return discovery_store.store_discovery_set(
            session_id=session_id,
            places=[
                {
                    "name": "Di Fara Pizza",
                    "address": "1424 Av J",
                    "open_status": "open",
                    "price_level": 2,
                    "rating": 4.7,
                    "review_count": 500,
                    "baseline_score": 0.82,
                    "ranking_factors": {
                        "rating": 0.94,
                        "review_volume": 0.1,
                        "open_bonus": 0.15,
                        "price_level": 2,
                    },
                }
            ],
            query="pizza",
        )

    async def test_resolves_from_active_set_and_binds_selected_place(self):
        ctx = _ctx()
        set_id = await self._seed_set()
        trip_state.bind_discovery_set(ctx.session, set_id)
        record = discovery_store.load_discovery_set(set_id, session_id="sess-disc")
        place_id = record["places"][0]["place_id"]
        result = await place_reference.execute(
            {"place_id": place_id, "discovery_set_id": set_id}, ctx
        )
        assert result.ok
        assert result.data["canonical"]
        assert result.data["destination_label"] == "Di Fara Pizza, 1424 Av J"
        assert result.data["baseline_score"] == 0.82
        state = trip_state.get_trip_state(ctx.session)
        assert state["selected_place_id"] == place_id

    async def test_explicit_discovery_set_id(self):
        set_id = await self._seed_set()
        record = discovery_store.load_discovery_set(set_id, session_id="sess-disc")
        place_id = record["places"][0]["place_id"]
        result = await place_reference.execute(
            {"place_id": place_id, "discovery_set_id": set_id}, _ctx()
        )
        assert result.ok

    async def test_cross_session_rejected(self):
        set_id = await self._seed_set(session_id="sess-a")
        record = discovery_store.load_discovery_set(set_id, session_id="sess-a")
        place_id = record["places"][0]["place_id"]
        result = await place_reference.execute(
            {"place_id": place_id, "discovery_set_id": set_id}, _ctx(session_id="sess-b")
        )
        assert not result.ok
        assert "unknown, expired" in (result.error or "")

    async def test_unknown_place_id_rejected(self):
        ctx = _ctx()
        set_id = await self._seed_set()
        trip_state.bind_discovery_set(ctx.session, set_id)
        result = await place_reference.execute({"place_id": "pl_bogus"}, ctx)
        assert not result.ok

    async def test_description_resolution_binds_selected_place(self):
        set_id = discovery_store.store_discovery_set(
            session_id="sess-disc",
            places=[
                {
                    "name": "Pricier Pizza",
                    "address": "1 Expensive Way",
                    "neighborhood": "Brooklyn",
                    "price_level": 3,
                },
                {
                    "name": "Cheap Pizza",
                    "address": "2 Cheap Ave",
                    "neighborhood": "Brooklyn",
                    "price_level": 1,
                },
            ],
            query="pizza",
        )
        record = discovery_store.load_discovery_set(set_id, session_id="sess-disc")
        cheap_id = record["places"][1]["place_id"]
        ctx = _ctx()
        trip_state.bind_discovery_set(ctx.session, set_id)
        result = await place_reference.execute(
            {"description": "the cheaper one"},
            ctx,
        )
        assert result.ok
        assert result.data["name"] == "Cheap Pizza"
        assert trip_state.get_trip_state(ctx.session)["selected_place_id"] == cheap_id

    async def _seed_two_place_set(self, session_id: str = "sess-disc") -> str:
        return discovery_store.store_discovery_set(
            session_id=session_id,
            places=[
                {
                    "name": "A Pizza",
                    "address": "1 A St",
                    "neighborhood": "Brooklyn",
                    "price_level": 2,
                    "rating": 4.7,
                    "review_count": 500,
                    "baseline_score": 0.82,
                },
                {
                    "name": "B Pizza",
                    "address": "2 B Ave",
                    "neighborhood": "Brooklyn",
                    "price_level": 1,
                    "rating": 4.2,
                    "review_count": 100,
                    "baseline_score": 0.7,
                },
            ],
            query="pizza",
        )

    async def test_explicit_older_set_rebinds_active_context_for_followups(self):
        ctx = _ctx()
        set_b = await self._seed_two_place_set()
        set_a = await self._seed_two_place_set()
        trip_state.bind_discovery_set(ctx.session, set_b)
        record_b = discovery_store.load_discovery_set(set_b, session_id="sess-disc")
        trip_state.bind_selected_place(ctx.session, record_b["places"][0]["place_id"])
        record_a = discovery_store.load_discovery_set(set_a, session_id="sess-disc")
        second_a = record_a["places"][1]["place_id"]

        result = await place_reference.execute(
            {"place_id": second_a, "discovery_set_id": set_a},
            ctx,
        )
        assert result.ok
        state = trip_state.get_trip_state(ctx.session)
        assert state["active_discovery_set_id"] == set_a
        assert state["selected_place_id"] == second_a

        # A later implicit ordinal follow-up must resolve against the rebound
        # set A, not the newer set B.
        follow_up = await place_reference.execute({"ordinal": 2}, ctx)
        assert follow_up.ok
        assert follow_up.data["place_id"] == second_a
        assert follow_up.data["name"] == "B Pizza"

    async def test_failed_explicit_reference_leaves_active_context_unchanged(self):
        ctx = _ctx()
        set_b = await self._seed_two_place_set()
        trip_state.bind_discovery_set(ctx.session, set_b)
        record_b = discovery_store.load_discovery_set(set_b, session_id="sess-disc")
        place_b = record_b["places"][0]["place_id"]
        trip_state.bind_selected_place(ctx.session, place_b)

        def assert_unchanged():
            state = trip_state.get_trip_state(ctx.session)
            assert state["active_discovery_set_id"] == set_b
            assert state["selected_place_id"] == place_b

        # Unknown explicit set id.
        failed = await place_reference.execute(
            {"ordinal": 1, "discovery_set_id": "ds_invented"},
            ctx,
        )
        assert not failed.ok
        assert_unchanged()

        # Ambiguous description within an explicit set.
        set_ambiguous = discovery_store.store_discovery_set(
            session_id="sess-disc",
            places=[
                {"name": "Tied One", "price_level": 2},
                {"name": "Tied Two", "price_level": 2},
            ],
            query="pizza",
        )
        failed = await place_reference.execute(
            {"description": "cheaper", "discovery_set_id": set_ambiguous},
            ctx,
        )
        assert not failed.ok
        assert "multiple" in (failed.error or "")
        assert_unchanged()

        # Cross-session explicit set.
        set_other = discovery_store.store_discovery_set(
            session_id="sess-other",
            places=[{"name": "Other Place"}],
            query="pizza",
        )
        failed = await place_reference.execute(
            {"ordinal": 1, "discovery_set_id": set_other},
            ctx,
        )
        assert not failed.ok
        assert "unknown, expired" in (failed.error or "")
        assert_unchanged()

        # Expired explicit set.
        with patch(
            "app.services.agent.discovery_store.time.time",
            return_value=1_700_000_000.0,
        ):
            set_expired = discovery_store.store_discovery_set(
                session_id="sess-disc",
                places=[{"name": "Expired Place"}],
                query="pizza",
                ttl_seconds=30,
            )
        with patch(
            "app.services.agent.discovery_store.time.time",
            return_value=1_700_000_400.0,
        ):
            failed = await place_reference.execute(
                {"ordinal": 1, "discovery_set_id": set_expired},
                ctx,
            )
        assert not failed.ok
        assert "expired" in (failed.error or "")
        assert_unchanged()



class SearchBindsDiscoveryOnlyTests(unittest.IsolatedAsyncioTestCase):
    async def test_search_binds_only_the_discovery_set_and_never_prepares_routes(self):
        poi_result = ToolResult(
            ok=True,
            data={
                "results": [
                    _place("A Pizza", rating=4.8, review_count=900, price_level=2),
                ]
            },
            summary="1 place",
        )
        ctx = _ctx()
        with (
            patch.object(
                search_local_places,
                "execute",
                new=AsyncMock(return_value=poi_result),
            ),
            patch(
                "app.services.agent.tools.route.prepare_route_options.execute",
                new=AsyncMock(
                    side_effect=AssertionError("search must never prepare routes")
                ),
            ),
        ):
            result = await _discover({"query": "pizza"}, ctx)
        assert result.ok
        state = trip_state.get_trip_state(ctx.session)
        assert state["active_discovery_set_id"] == result.data["discovery_set_id"]
        assert state["selected_place_id"] is None
        assert state["active_candidate_set_id"] is None

if __name__ == "__main__":
    unittest.main()
