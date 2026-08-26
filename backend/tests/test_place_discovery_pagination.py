"""Private Google Places continuation behavior for discover_places."""

from __future__ import annotations

import math
import os
import unittest
from unittest.mock import AsyncMock, patch

from app.services import cache
from app.services.agent import discovery_store
from app.services.agent.tools._types import ToolContext, ToolResult
from app.services.agent.tools.places import discover_places, search_local_places


def _ctx() -> ToolContext:
    return ToolContext(
        session={},
        session_id="sess-pages",
        turn_id="turn-pages",
        now_et="2026-08-13T12:00:00-04:00",
        origin={"lat": 40.65, "lng": -73.95},
        agent_mode="auto",
        rider_message="Find pizza",
    )


def _place(name: str, borough: str = "Brooklyn") -> dict:
    return {
        "name": name,
        "address": f"123 Main St, {borough}, NY",
        "lat": 40.71,
        "lng": -73.98,
        "open_now": True,
        "rating": 4.6,
        "review_count": 200,
        "place_id": f"google-{name}",
        "address_components": [
            {"longText": borough, "types": ["sublocality_level_1"]},
            {"longText": "New York", "types": ["locality"]},
        ],
    }


def _request(
    *,
    query: str = "pizza",
    scope: dict | None = None,
    exclude_presented: bool = False,
) -> dict:
    return {
        "operation": "search",
        "query": query,
        "scope": scope or {"kind": "current_location", "values": []},
        "open_now": None,
        "max_results": 8,
        "candidate_names": [],
        "exclude_presented": exclude_presented,
    }


class GooglePlacesRequestTests(unittest.IsolatedAsyncioTestCase):
    async def test_request_uses_page_size_token_and_requests_response_token(self):
        next_cursor = "next-google-page"
        prior_cursor = "prior-google-page"
        fetch = AsyncMock(
            return_value=(
                {
                    "places": [],
                    "nextPageToken": f" {next_cursor} ",
                },
                None,
            )
        )
        with (
            patch.dict(os.environ, {"GOOGLE_PLACES_API_KEY": "test-key"}),
            patch.object(search_local_places, "fetch_json", new=fetch),
        ):
            result = await search_local_places.execute(
                {
                    "query": "pizza",
                    "max_results": 999,
                    "page_token": f" {prior_cursor} ",
                },
                _ctx(),
            )

        assert result.ok
        request = fetch.await_args.kwargs
        assert request["json_body"]["pageSize"] == 8
        assert request["json_body"]["pageToken"] == prior_cursor
        assert "maxResultCount" not in request["json_body"]
        assert "nextPageToken" in request["headers"]["X-Goog-FieldMask"]
        assert result.data["next_page_token"] == next_cursor


class QueueContextTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        cache._mem.clear()

    def test_schema_requires_strict_queue_context(self):
        schema = discover_places.DISCOVER_PLACES_SCHEMA["input_schema"]
        queue_schema = schema["properties"]["queue_context"]

        assert "queue_context" in schema["required"]
        assert queue_schema["properties"]["mode"]["enum"] == [
            "ignore",
            "heads_up",
            "decision",
            "historical",
        ]
        assert queue_schema["required"] == ["mode", "max_wait_minutes"]
        assert queue_schema["additionalProperties"] is False

    async def test_missing_context_defaults_to_private_ignore(self):
        provider = AsyncMock(
            return_value=ToolResult(
                ok=True,
                data={"results": [_place("Default Pizza")]},
            )
        )
        with patch.object(
            discover_places.search_local_places,
            "_provider_search",
            new=provider,
        ):
            result = await discover_places.execute(_request(), _ctx())

        assert result.ok
        record = discovery_store.load_discovery_set(
            result.data["discovery_set_id"], session_id="sess-pages"
        )
        assert record["queue_context"] == {
            "mode": "ignore",
            "max_wait_minutes": None,
        }
        assert "queue_context" not in result.data
        assert "queue_context" not in result.data["places"][0]

    async def test_valid_modes_and_exact_threshold_are_private(self):
        for mode in ("ignore", "heads_up", "decision", "historical"):
            provider = AsyncMock(
                return_value=ToolResult(
                    ok=True,
                    data={"results": [_place(f"{mode} Pizza")]},
                )
            )
            request = _request()
            request["queue_context"] = {
                "mode": mode,
                "max_wait_minutes": 12.5,
            }
            with patch.object(
                discover_places.search_local_places,
                "_provider_search",
                new=provider,
            ):
                result = await discover_places.execute(request, _ctx())

            assert result.ok
            record = discovery_store.load_discovery_set(
                result.data["discovery_set_id"], session_id="sess-pages"
            )
            assert record["queue_context"] == {
                "mode": mode,
                "max_wait_minutes": 12.5,
            }
            assert isinstance(record["queue_context"]["max_wait_minutes"], float)

    async def test_invalid_context_is_an_internal_diagnostic(self):
        invalid_contexts = (
            None,
            "heads_up",
            {"mode": "unknown", "max_wait_minutes": None},
            {"mode": "decision"},
            {
                "mode": "decision",
                "max_wait_minutes": 10,
                "extra": True,
            },
            {"mode": "decision", "max_wait_minutes": True},
            {"mode": "decision", "max_wait_minutes": -1},
            {"mode": "decision", "max_wait_minutes": math.nan},
            {"mode": "decision", "max_wait_minutes": math.inf},
        )
        for queue_context in invalid_contexts:
            request = _request()
            request["queue_context"] = queue_context
            result = await discover_places.execute(request, _ctx())

            assert not result.ok
            assert result.internal_diagnostic
            assert "queue_context" in (result.error or "")


class DiscoveryContinuationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        cache._mem.clear()

    @staticmethod
    def _present_first(ctx: ToolContext, result: ToolResult) -> None:
        record = discovery_store.load_discovery_set(
            result.data["discovery_set_id"], session_id=ctx.session_id
        )
        discovery_store.record_presented_places(
            ctx.session,
            session_id=ctx.session_id,
            discovery_set_id=result.data["discovery_set_id"],
            places=[record["places"][0]],
        )

    async def test_same_query_and_scope_reuses_private_token(self):
        ctx = _ctx()
        next_cursor = "page-2"
        provider = AsyncMock(
            side_effect=[
                ToolResult(
                    ok=True,
                    data={
                        "results": [_place("First Pizza")],
                        "next_page_token": next_cursor,
                    },
                ),
                ToolResult(
                    ok=True,
                    data={"results": [_place("Second Pizza")]},
                ),
            ]
        )
        with patch.object(
            discover_places.search_local_places,
            "_provider_search",
            new=provider,
        ):
            first = await discover_places.execute(_request(), ctx)
            sanitized_context = discovery_store.sanitized_discovery_context(
                ctx.session, ctx.session_id
            )
            self._present_first(ctx, first)
            second = await discover_places.execute(
                _request(exclude_presented=True), ctx
            )

        assert provider.await_args_list[1].args[0]["page_token"] == next_cursor
        assert [place["name"] for place in second.data["places"]] == ["Second Pizza"]
        assert "next_page_token" not in first.data
        assert "continuation_tokens" not in first.data
        assert "page_token" not in str(first.data)
        assert next_cursor not in str(sanitized_context)
        assert next_cursor not in str(
            discovery_store.presented_entity_registry(ctx.session)
        )

    async def test_query_or_scope_mismatch_does_not_reuse_token(self):
        for changed_request in (
            _request(query="ramen", exclude_presented=True),
            _request(
                scope={"kind": "boroughs", "values": ["Brooklyn"]},
                exclude_presented=True,
            ),
        ):
            with self.subTest(changed_request=changed_request):
                cache._mem.clear()
                ctx = _ctx()
                provider = AsyncMock(
                    side_effect=[
                        ToolResult(
                            ok=True,
                            data={
                                "results": [_place("First Pizza")],
                                "next_page_token": "do-not-reuse",
                            },
                        ),
                        ToolResult(
                            ok=True,
                            data={"results": [_place("Fresh Place")]},
                        ),
                    ]
                )
                with patch.object(
                    discover_places.search_local_places,
                    "_provider_search",
                    new=provider,
                ):
                    await discover_places.execute(_request(), ctx)
                    await discover_places.execute(changed_request, ctx)

                assert "page_token" not in provider.await_args_list[1].args[0]

    async def test_multi_target_tokens_are_stored_and_reused_by_target(self):
        ctx = _ctx()
        scope = {"kind": "boroughs", "values": ["Manhattan", "Brooklyn"]}
        manhattan_cursor = "m-page-2"
        brooklyn_cursor = "b-page-2"
        provider = AsyncMock(
            side_effect=[
                ToolResult(
                    ok=True,
                    data={
                        "results": [_place("M One", "Manhattan")],
                        "next_page_token": manhattan_cursor,
                    },
                ),
                ToolResult(
                    ok=True,
                    data={
                        "results": [_place("B One")],
                        "next_page_token": brooklyn_cursor,
                    },
                ),
                ToolResult(
                    ok=True,
                    data={"results": [_place("M Two", "Manhattan")]},
                ),
                ToolResult(
                    ok=True,
                    data={"results": [_place("B Two")]},
                ),
            ]
        )
        with patch.object(
            discover_places.search_local_places,
            "_provider_search",
            new=provider,
        ):
            first = await discover_places.execute(_request(scope=scope), ctx)
            record = discovery_store.load_discovery_set(
                first.data["discovery_set_id"], session_id=ctx.session_id
            )
            assert record["continuation_tokens"] == {
                "target_0": manhattan_cursor,
                "target_1": brooklyn_cursor,
            }
            second = await discover_places.execute(
                _request(scope=scope, exclude_presented=True), ctx
            )

        assert provider.await_args_list[2].args[0]["page_token"] == manhattan_cursor
        assert provider.await_args_list[3].args[0]["page_token"] == brooklyn_cursor
        assert [place["name"] for place in second.data["places"]] == ["M Two", "B Two"]

    async def test_missing_or_exhausted_token_falls_back_without_duplicates(self):
        ctx = _ctx()
        old = _place("Old Pizza")
        fresh = _place("Fresh Pizza")
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
            first = await discover_places.execute(_request(), ctx)
            self._present_first(ctx, first)
            second = await discover_places.execute(
                _request(exclude_presented=True), ctx
            )
            self._present_first(ctx, second)
            exhausted = await discover_places.execute(
                _request(exclude_presented=True), ctx
            )

        assert "page_token" not in provider.await_args_list[1].args[0]
        assert [place["name"] for place in second.data["places"]] == ["Fresh Pizza"]
        assert exhausted.data["places"] == []
        assert exhausted.data["exhausted"]

    async def test_partial_provider_failure_keeps_successful_token(self):
        ctx = _ctx()
        scope = {"kind": "boroughs", "values": ["Manhattan", "Brooklyn"]}
        provider = AsyncMock(
            side_effect=[
                ToolResult(
                    ok=True,
                    data={
                        "results": [_place("M One", "Manhattan")],
                        "next_page_token": "m-page-2",
                    },
                ),
                RuntimeError("provider unavailable"),
            ]
        )
        with patch.object(
            discover_places.search_local_places,
            "_provider_search",
            new=provider,
        ):
            result = await discover_places.execute(_request(scope=scope), ctx)

        assert result.ok
        assert result.data["coverage"]["status"] == "partial"
        record = discovery_store.load_discovery_set(
            result.data["discovery_set_id"], session_id=ctx.session_id
        )
        assert record["continuation_tokens"] == {"target_0": "m-page-2"}


if __name__ == "__main__":
    unittest.main()
