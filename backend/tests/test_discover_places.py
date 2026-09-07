"""discover_places search, verify, and hard borough filtering."""

from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, patch

from app.services import cache
from app.services.agent import discovery_store
from app.services.agent.tools._types import ToolContext, ToolResult
from app.services.agent.tools.places import discover_places
from app.services.agent.turn.contract import GoalKind, OutcomeGoal, TurnContract
from app.services.agent.turn.evidence import TurnEvidence


def _ctx(session_id: str = "sess-disc") -> ToolContext:
    return ToolContext(
        session={},
        session_id=session_id,
        turn_id="t-disc",
        now_et="2026-08-13T12:00:00-04:00",
        origin={"lat": 40.65, "lng": -73.95},
        agent_mode="auto",
        rider_message="In the mood for pizza, what are some good places in the city?",
    )


def _place(name: str, borough: str, *, open_now=True):
    return {
        "name": name,
        "address": f"123 Main St, {borough}, NY",
        "lat": 40.71,
        "lng": -73.98,
        "open_now": open_now,
        "rating": 4.6,
        "review_count": 200,
        "place_id": f"prov-{name}",
        "address_components": [
            {"longText": borough, "types": ["sublocality_level_1"]},
            {"longText": "New York", "types": ["locality"]},
            {"longText": "New York", "types": ["administrative_area_level_1"]},
        ],
    }


class DiscoverPlacesTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        cache._mem.clear()

    @staticmethod
    def _route_context() -> tuple[ToolContext, TurnEvidence]:
        evidence = TurnEvidence()
        evidence.bind_contract(
            TurnContract((OutcomeGoal("route", GoalKind.ROUTE),))
        )
        return _ctx(), evidence

    async def test_route_owned_discovery_updates_canonical_places_but_keeps_route_pending(self):
        ctx, evidence = self._route_context()
        ctx.turn_evidence = evidence
        provider = AsyncMock(
            return_value=ToolResult(
                ok=True,
                data={"results": [_place("Kyuramen", "Brooklyn")]},
            )
        )
        with patch.object(
            discover_places.search_local_places,
            "_provider_search",
            new=provider,
        ):
            result = await discover_places.execute(
                {
                    "operation": "search",
                    "query": "Kyuramen",
                    "scope": {"kind": "current_location", "values": []},
                    "open_now": None,
                    "max_results": 8,
                    "candidate_names": [],
                    "goal_key": "route",
                },
                ctx,
            )

        assert result.ok
        assert result.data["discovery_set_id"] is not None
        evidence.record_capability_result(
            "discover_places",
            {"goal_key": "route"},
            result,
        )
        assert evidence.state_for("route").value == "pending"
        assert evidence.handle_for("route") is None

    async def test_discovery_cannot_act_on_route_with_explicit_destination_dependency(self):
        evidence = TurnEvidence()
        evidence.bind_contract(
            TurnContract(
                (
                    OutcomeGoal("destination", GoalKind.DESTINATION_SELECTION),
                    OutcomeGoal("route", GoalKind.ROUTE, ("destination",)),
                )
            )
        )
        ctx = _ctx()
        ctx.turn_evidence = evidence

        result = await discover_places.execute(
            {
                "operation": "search",
                "query": "Kyuramen",
                "scope": {"kind": "current_location", "values": []},
                "open_now": None,
                "max_results": 8,
                "candidate_names": [],
                "goal_key": "route",
            },
            ctx,
        )

        assert not result.ok
        assert "incompatible" in (result.error or "")

    async def test_request_validation_rejects_invalid_operation_and_query(self):
        for field, value in (("operation", "recommend"), ("query", "")):
            with self.subTest(field=field):
                tool_input = {
                    "operation": "search",
                    "query": "pizza",
                    "scope": {"kind": "nyc", "values": []},
                    "open_now": None,
                    "max_results": 8,
                    "candidate_names": [],
                }
                tool_input[field] = value
                result = await discover_places.execute(tool_input, _ctx())

                assert not result.ok
                assert result.internal_diagnostic

    def test_schema_requires_exclude_presented_search_flag(self):
        schema = discover_places.DISCOVER_PLACES_SCHEMA["input_schema"]
        assert schema["properties"]["exclude_presented"]["type"] == "boolean"
        assert "exclude_presented" in schema["required"]

    async def test_request_validation_rejects_missing_session_and_non_boolean_open_now(self):
        missing_session = await discover_places.execute(
            {
                "operation": "search",
                "query": "pizza",
                "scope": {"kind": "nyc", "values": []},
                "open_now": None,
                "max_results": 8,
                "candidate_names": [],
            },
            _ctx(session_id=""),
        )
        assert not missing_session.ok
        assert "session" in (missing_session.error or "")

        invalid_open_now = await discover_places.execute(
            {
                "operation": "search",
                "query": "pizza",
                "scope": {"kind": "nyc", "values": []},
                "open_now": "yes",
                "max_results": 8,
                "candidate_names": [],
            },
            _ctx(),
        )
        assert not invalid_open_now.ok
        assert invalid_open_now.internal_diagnostic
        assert "boolean" in (invalid_open_now.error or "")

    async def test_request_validation_enforces_operation_specific_candidate_names(self):
        search_with_names = await discover_places.execute(
            {
                "operation": "search",
                "query": "pizza",
                "scope": {"kind": "nyc", "values": []},
                "open_now": None,
                "max_results": 8,
                "candidate_names": ["Prince Street Pizza"],
            },
            _ctx(),
        )
        assert not search_with_names.ok
        assert "empty candidate_names" in (search_with_names.error or "")

        missing_names = await discover_places.execute(
            {
                "operation": "verify",
                "query": "pizza",
                "scope": {"kind": "nyc", "values": []},
                "open_now": None,
                "max_results": 8,
                "candidate_names": [],
            },
            _ctx(),
        )
        assert not missing_names.ok
        assert "one through five" in (missing_names.error or "")

        invalid_exclusion = await discover_places.execute(
            {
                "operation": "verify",
                "query": "pizza",
                "scope": {"kind": "nyc", "values": []},
                "open_now": None,
                "max_results": 8,
                "candidate_names": ["Prince Street Pizza"],
                "exclude_presented": True,
            },
            _ctx(),
        )
        assert not invalid_exclusion.ok
        assert "only for search" in (invalid_exclusion.error or "")

    async def test_request_validation_normalizes_names_and_clamps_provider_cap(self):
        provider = AsyncMock(return_value=ToolResult(ok=True, data={"results": []}))
        with patch.object(
            discover_places.search_local_places,
            "_provider_search",
            new=provider,
        ):
            result = await discover_places.execute(
                {
                    "operation": "search",
                    "query": " pizza ",
                    "scope": {"kind": "nyc", "values": []},
                    "open_now": None,
                    "max_results": "not-a-number",
                    "candidate_names": [],
                },
                _ctx(),
            )

        assert result.ok
        assert provider.await_count == 5
        assert all(call.args[0]["max_results"] == 2 for call in provider.await_args_list)

    async def test_hard_filters_requested_boroughs(self):
        poi = ToolResult(
            ok=True,
            data={
                "results": [
                    _place("Manhattan Slice", "Manhattan"),
                    _place("Brooklyn Pie", "Brooklyn"),
                ]
            },
        )
        with patch.object(
            discover_places.search_local_places,
            "execute",
            new=AsyncMock(return_value=poi),
        ):
            result = await discover_places.execute(
                {
                    "operation": "search",
                    "query": "pizza",
                    "scope": {"kind": "boroughs", "values": ["Manhattan"]},
                    "open_now": None,
                    "max_results": 8,
                    "candidate_names": [],
                },
                _ctx(),
            )
        assert result.ok
        names = [place["name"] for place in result.data["places"]]
        assert names == ["Manhattan Slice"]
        assert result.data["scope"]["values"] == ["Manhattan"]
        assert "latitude" not in result.data["places"][0]
        assert "longitude" not in result.data["places"][0]
        assert result.data["places"][0]["rider_distance_meters"] > 0
        record = discovery_store.load_discovery_set(
            result.data["discovery_set_id"], session_id="sess-disc"
        )
        assert record["places"][0]["latitude"]
        assert record["places"][0]["longitude"]

    async def test_multi_area_results_interleave_and_reject_wrong_target_rows(self):
        manhattan_one = _place("Manhattan One", "Manhattan")
        manhattan_two = _place("Manhattan Two", "Manhattan")
        brooklyn_one = _place("Brooklyn One", "Brooklyn")
        brooklyn_two = _place("Brooklyn Two", "Brooklyn")
        responses = {
            "Manhattan": ToolResult(
                ok=True,
                data={
                    "results": [
                        manhattan_one,
                        _place("Brooklyn Leak", "Brooklyn"),
                        manhattan_two,
                    ]
                },
            ),
            "Brooklyn": ToolResult(
                ok=True,
                data={
                    "results": [
                        brooklyn_one,
                        _place("Manhattan Leak", "Manhattan"),
                        brooklyn_two,
                    ]
                },
            ),
        }

        async def search(request, _ctx):
            return responses[request["near"]]

        with patch.object(
            discover_places.search_local_places,
            "_provider_search",
            new=search,
        ):
            result = await discover_places.execute(
                {
                    "operation": "search",
                    "query": "ramen",
                    "scope": {
                        "kind": "boroughs",
                        "values": ["Manhattan", "Brooklyn"],
                    },
                    "open_now": None,
                    "max_results": 4,
                    "candidate_names": [],
                },
                _ctx(),
            )

        assert result.ok
        assert [place["name"] for place in result.data["places"]] == ["Manhattan One", "Brooklyn One", "Manhattan Two", "Brooklyn Two"]

    async def test_exclude_presented_filters_old_identity_and_reports_exhaustion(self):
        ctx = _ctx()
        old = _place("Old Pizza", "Brooklyn")
        fresh = _place("Fresh Pizza", "Brooklyn")
        provider = AsyncMock(
            side_effect=[
                ToolResult(ok=True, data={"results": [old]}),
                ToolResult(ok=True, data={"results": [old, fresh]}),
                ToolResult(ok=True, data={"results": [old]}),
            ]
        )
        with patch.object(
            discover_places.search_local_places,
            "_provider_search",
            new=provider,
        ):
            first = await discover_places.execute(
                {
                    "operation": "search",
                    "query": "pizza",
                    "scope": {"kind": "current_location", "values": []},
                    "open_now": None,
                    "max_results": 8,
                    "candidate_names": [],
                },
                ctx,
            )
            old_id = first.data["places"][0]["place_id"]
            record = discovery_store.load_discovery_set(
                first.data["discovery_set_id"], session_id=ctx.session_id
            )
            discovery_store.record_presented_places(
                ctx.session,
                session_id=ctx.session_id,
                discovery_set_id=first.data["discovery_set_id"],
                places=[record["places"][0]],
            )
            second = await discover_places.execute(
                {
                    "operation": "search",
                    "query": "pizza",
                    "scope": {"kind": "current_location", "values": []},
                    "open_now": None,
                    "max_results": 8,
                    "candidate_names": [],
                    "exclude_presented": True,
                },
                ctx,
            )
            exhausted = await discover_places.execute(
                {
                    "operation": "search",
                    "query": "pizza",
                    "scope": {"kind": "current_location", "values": []},
                    "open_now": None,
                    "max_results": 8,
                    "candidate_names": [],
                    "exclude_presented": True,
                },
                ctx,
            )

        assert second.ok
        assert [place["name"] for place in second.data["places"]] == ["Fresh Pizza"]
        assert second.data["places"][0]["place_id"] != old_id
        assert exhausted.ok
        assert exhausted.outcome.value == "unavailable"
        assert exhausted.data["places"] == []
        assert exhausted.data["exhausted"]
        assert not exhausted.data["additional_options"]

    async def test_distance_context_is_finite_and_provider_safe(self):
        poi = ToolResult(ok=True, data={"results": [_place("Measured Pizza", "Brooklyn")]})
        ctx = _ctx()
        with patch.object(
            discover_places.search_local_places,
            "_provider_search",
            new=AsyncMock(return_value=poi),
        ):
            result = await discover_places.execute(
                {
                    "operation": "search",
                    "query": "pizza",
                    "scope": {"kind": "current_location", "values": []},
                    "open_now": None,
                    "max_results": 8,
                    "candidate_names": [],
                },
                ctx,
            )
        place = result.data["places"][0]
        assert "latitude" not in place
        assert "longitude" not in place
        assert place["rider_distance_meters"] > 0
        assert "provider_place_id" not in place
        assert "travel_time" not in place
        record = discovery_store.load_discovery_set(
            result.data["discovery_set_id"], session_id=ctx.session_id
        )
        assert isinstance(record["places"][0]["latitude"], float)
        assert isinstance(record["places"][0]["longitude"], float)
        context = discovery_store.sanitized_discovery_context(ctx.session, ctx.session_id)
        option = context["options"][0]
        assert option["rider_distance_meters"] == place["rider_distance_meters"]
        assert "latitude" not in option
        assert "longitude" not in option
        assert "40.65" not in str(context)
        assert "-73.95" not in str(context)

    async def test_near_me_requires_current_location(self):
        ctx = _ctx()
        ctx.origin = None
        result = await discover_places.execute(
            {
                "operation": "search",
                "query": "coffee",
                "scope": {"kind": "current_location", "values": []},
                "open_now": None,
                "max_results": 8,
                "candidate_names": [],
            },
            ctx,
        )
        assert not result.ok
        assert "location" in (result.error or "")
        assert not result.internal_diagnostic

    async def test_conflicting_nyc_scope_is_internal_diagnostic(self):
        result = await discover_places.execute(
            {
                "operation": "search",
                "query": "pizza",
                "scope": {"kind": "nyc", "values": ["Manhattan"]},
                "open_now": None,
                "max_results": 8,
                "candidate_names": [],
            },
            _ctx(),
        )

        assert not result.ok
        assert "empty values" in (result.error or "")
        assert result.internal_diagnostic

    async def test_verify_reports_unverified_names(self):
        async def fake_execute(tool_input, _ctx):
            if tool_input["query"] == "L'Industrie":
                return ToolResult(ok=True, data={"results": [_place("L'Industrie", "Brooklyn")]})
            return ToolResult(ok=True, data={"results": []})

        with patch.object(
            discover_places.search_local_places,
            "execute",
            new=AsyncMock(side_effect=fake_execute),
        ):
            result = await discover_places.execute(
                {
                    "operation": "verify",
                    "query": "L'Industrie",
                    "scope": {"kind": "named_area", "values": ["Williamsburg"]},
                    "open_now": None,
                    "max_results": 8,
                    "candidate_names": ["L'Industrie", "Imaginary Pizza"],
                },
                _ctx(),
            )
        assert result.ok
        assert result.data["unverified_names"] == ["Imaginary Pizza"]
        assert result.data["places"][0]["name"] == "L'Industrie"

    async def test_verify_searches_every_authorized_area(self):
        calls: list[str | None] = []

        async def fake_execute(tool_input, _ctx):
            calls.append(tool_input.get("near"))
            if tool_input["near"] == "Brooklyn" and tool_input["query"] == "L'Industrie":
                return ToolResult(ok=True, data={"results": [_place("L'Industrie", "Brooklyn")]})
            return ToolResult(ok=True, data={"results": []})

        with patch.object(
            discover_places.search_local_places,
            "execute",
            new=AsyncMock(side_effect=fake_execute),
        ):
            result = await discover_places.execute(
                {
                    "operation": "verify",
                    "query": "L'Industrie",
                    "scope": {"kind": "boroughs", "values": ["Manhattan", "Brooklyn"]},
                    "open_now": None,
                    "max_results": 8,
                    "candidate_names": ["L'Industrie"],
                },
                _ctx(),
            )
        assert result.ok
        assert set(calls) == {"Manhattan", "Brooklyn"}
        assert result.data["places"][0]["name"] == "L'Industrie"

    async def test_citywide_keeps_nyc_coordinate_when_address_components_are_missing(self):
        """A valid Places result must not vanish only because components are omitted."""

        place = _place("Citywide Ramen", "Manhattan")
        place.pop("address_components")
        poi = ToolResult(ok=True, data={"results": [place]})
        search = AsyncMock(return_value=poi)
        with patch.object(discover_places.search_local_places, "execute", new=search):
            result = await discover_places.execute(
                {
                    "operation": "search",
                    "query": "ramen",
                    "scope": {"kind": "nyc", "values": []},
                    "open_now": None,
                    "max_results": 5,
                    "candidate_names": [],
                },
                _ctx(),
            )

        assert result.ok
        assert search.await_count == 5
        assert [place["name"] for place in result.data["places"]] == ["Citywide Ramen"]

    async def test_partial_area_search_keeps_successful_verified_results(self):
        successful = ToolResult(
            ok=True,
            data={"results": [_place("Manhattan Ramen", "Manhattan")]},
        )
        failed = ToolResult(ok=False, error="place search timed out")

        with patch.object(
            discover_places.search_local_places,
            "execute",
            new=AsyncMock(side_effect=[successful, failed]),
        ):
            result = await discover_places.execute(
                {
                    "operation": "search",
                    "query": "ramen",
                    "scope": {
                        "kind": "boroughs",
                        "values": ["Manhattan", "Brooklyn"],
                    },
                    "open_now": None,
                    "max_results": 5,
                    "candidate_names": [],
                },
                _ctx(),
            )

        assert result.ok
        assert [place["name"] for place in result.data["places"]] == ["Manhattan Ramen"]
        assert result.data["coverage"]["status"] == "partial"

    async def test_empty_search_does_not_publish_internal_no_match_activity(self):
        poi = ToolResult(ok=True, data={"results": []})
        with patch.object(
            discover_places.search_local_places,
            "execute",
            new=AsyncMock(return_value=poi),
        ):
            result = await discover_places.execute(
                {
                    "operation": "search",
                    "query": "ramen",
                    "scope": {"kind": "nyc", "values": []},
                    "open_now": None,
                    "max_results": 5,
                    "candidate_names": [],
                },
                _ctx(),
            )

        assert result.ok
        assert "no matching verified" not in result.summary.casefold()


if __name__ == "__main__":
    unittest.main()
