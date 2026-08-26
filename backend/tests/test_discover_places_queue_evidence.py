"""Model-facing queue evidence on destination-sensitive place discovery."""

from __future__ import annotations

import asyncio
import unittest
from datetime import UTC, date, datetime
from unittest.mock import AsyncMock, Mock, patch

from app.services import cache
from app.services.agent import discovery_store
from app.services.agent.tools._types import ToolContext, ToolResult
from app.services.agent.tools.places import damn_lines, discover_places

LINDUSTRIE_ID = "ChIJ92OsaJVZwokRsC54kf-J-3g"
JOHNS_ID = "ChIJuW43oZNZwokRdE5tLzpuykE"
UNMONITORED_ID = "google-unmonitored"
NOW = datetime(2026, 8, 25, 23, 0, tzinfo=UTC)


def _ctx(*, now_et: str = "2026-08-25T19:00:00-04:00") -> ToolContext:
    return ToolContext(
        session={},
        session_id="sess-queue-digest",
        turn_id="turn-queue-digest",
        now_et=now_et,
        origin={"lat": 40.7308, "lng": -73.9973},
        agent_mode="auto",
        rider_message="Find pizza without a long line",
    )


def _place(
    name: str,
    provider_place_id: str,
    *,
    open_now: bool | None = True,
) -> dict:
    return {
        "name": name,
        "address": "123 Bleecker St, New York, NY",
        "lat": 40.731,
        "lng": -73.998,
        "open_now": open_now,
        "rating": 4.7,
        "review_count": 500,
        "place_id": provider_place_id,
        "address_components": [
            {"longText": "Manhattan", "types": ["sublocality_level_1"]},
            {"longText": "New York", "types": ["locality"]},
        ],
    }


def _request(mode: str, *, max_wait_minutes: float | None = None) -> dict:
    return {
        "operation": "search",
        "query": "pizza",
        "scope": {"kind": "current_location", "values": []},
        "open_now": None,
        "max_results": 5,
        "candidate_names": [],
        "exclude_presented": False,
        "queue_context": {
            "mode": mode,
            "max_wait_minutes": max_wait_minutes,
        },
    }


async def _discover(
    ctx: ToolContext,
    places: list[dict],
    *,
    mode: str,
    max_wait_minutes: float | None = None,
) -> ToolResult:
    provider = AsyncMock(
        return_value=ToolResult(ok=True, data={"results": places})
    )
    with patch.object(
        discover_places.search_local_places,
        "_provider_search",
        new=provider,
    ):
        return await discover_places.execute(
            _request(mode, max_wait_minutes=max_wait_minutes), ctx
        )


def _history(place_id: str) -> damn_lines.HistoricalQueuePattern:
    return damn_lines.HistoricalQueuePattern(
        google_place_id=place_id,
        weekday=1,
        hour=19,
        people_mean=14.5,
        wait_minutes_mean=11.25,
        sample_count=48,
        comparable_dates=4,
        date_from=date(2026, 7, 28),
        date_to=date(2026, 8, 18),
    )


class DiscoverQueueEvidenceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        cache._mem.clear()
        damn_lines._current_refresh_task = None
        damn_lines._history_refresh_task = None
        damn_lines._warmup_tasks = set()
        damn_lines._history_loaded = False
        damn_lines._history_last_success = None
        damn_lines._history_index = {}
        warmup = patch.object(damn_lines, "schedule_history_warmup")
        warmup.start()
        self.addCleanup(warmup.stop)

    async def test_ignore_and_heads_up_do_not_enrich_discovery_digest(self):
        for mode in ("ignore", "heads_up"):
            with self.subTest(mode=mode):
                current = AsyncMock()
                historical = Mock()
                with (
                    patch.object(
                        damn_lines,
                        "get_current_observations",
                        new=current,
                    ),
                    patch.object(
                        damn_lines,
                        "get_historical_pattern",
                        new=historical,
                    ),
                ):
                    result = await _discover(
                        _ctx(),
                        [_place("L'Industrie", LINDUSTRIE_ID)],
                        mode=mode,
                    )

                assert result.ok
                assert "queue_evidence" not in result.data["places"][0]
                assert "queue_max_wait_minutes" not in result.data
                current.assert_not_awaited()
                historical.assert_not_called()

    async def test_decision_batches_supported_places_and_preserves_partial_current(self):
        captured_at = datetime(2026, 8, 25, 22, 58, tzinfo=UTC)
        current = AsyncMock(
            return_value=damn_lines.CurrentQueueResult(
                observations={
                    LINDUSTRIE_ID: damn_lines.QueueObservation(
                        LINDUSTRIE_ID, 12, 9.5, captured_at
                    ),
                    JOHNS_ID: damn_lines.QueueObservation(
                        JOHNS_ID, None, 7.0, captured_at
                    ),
                },
                provider_available=True,
            )
        )
        ctx = _ctx()
        with patch.object(
            damn_lines,
            "get_current_observations",
            new=current,
        ):
            result = await _discover(
                ctx,
                [
                    _place("L'Industrie", LINDUSTRIE_ID),
                    _place("John's", JOHNS_ID),
                    _place("Neighborhood Pizza", UNMONITORED_ID),
                ],
                mode="decision",
                max_wait_minutes=12.5,
            )

        assert result.ok
        current.assert_awaited_once()
        assert current.await_args.args[0] == [LINDUSTRIE_ID, JOHNS_ID]
        assert current.await_args.kwargs["now"] == NOW
        assert result.data["queue_max_wait_minutes"] == 12.5
        by_name = {place["name"]: place for place in result.data["places"]}
        assert by_name["L'Industrie"]["queue_evidence"] == {
            "coverage": "supported",
            "evidence_kind": "current",
            "people_count": 12,
            "wait_minutes": 9.5,
            "captured_at": captured_at.isoformat(),
        }
        assert by_name["John's"]["queue_evidence"] == {
            "coverage": "supported",
            "evidence_kind": "current",
            "wait_minutes": 7.0,
            "captured_at": captured_at.isoformat(),
        }
        assert by_name["Neighborhood Pizza"]["queue_evidence"] == {
            "coverage": "unmonitored"
        }

        record = discovery_store.load_discovery_set(
            result.data["discovery_set_id"], session_id=ctx.session_id
        )
        assert all("queue_evidence" not in place for place in record["places"])
        discovery_store.record_presented_places(
            ctx.session,
            session_id=ctx.session_id,
            discovery_set_id=result.data["discovery_set_id"],
            places=record["places"],
        )
        private_text = str(record)
        presented_text = str(discovery_store.presented_entity_registry(ctx.session))
        venue = damn_lines.get_supported_venue(LINDUSTRIE_ID)
        assert venue is not None
        for leaked in (venue.slug, venue.source_url):
            assert leaked not in private_text
            assert leaked not in presented_text
            assert leaked not in str(result.data)
        assert "queue_evidence" not in private_text
        assert "queue_evidence" not in presented_text

    async def test_decision_uses_history_only_for_open_supported_place(self):
        current = AsyncMock(
            return_value=damn_lines.CurrentQueueResult({}, True)
        )
        historical = Mock(return_value=_history(LINDUSTRIE_ID))
        with (
            patch.object(
                damn_lines,
                "get_current_observations",
                new=current,
            ),
            patch.object(
                damn_lines,
                "get_historical_pattern",
                new=historical,
            ),
        ):
            result = await _discover(
                _ctx(),
                [_place("L'Industrie", LINDUSTRIE_ID)],
                mode="decision",
            )

        evidence = result.data["places"][0]["queue_evidence"]
        assert evidence == {
            "coverage": "supported",
            "evidence_kind": "historical",
            "current_available": False,
            "weekday": 1,
            "hour": 19,
            "people_mean": 14.5,
            "wait_minutes_mean": 11.25,
            "sample_count": 48,
            "comparable_dates": 4,
            "date_from": "2026-07-28",
            "date_to": "2026-08-18",
        }
        historical.assert_called_once()

    async def test_decision_does_not_use_history_for_closed_or_unknown_place(self):
        current = AsyncMock(
            return_value=damn_lines.CurrentQueueResult({}, True)
        )
        historical = Mock()
        with (
            patch.object(
                damn_lines,
                "get_current_observations",
                new=current,
            ),
            patch.object(
                damn_lines,
                "get_historical_pattern",
                new=historical,
            ),
        ):
            result = await _discover(
                _ctx(),
                [
                    _place("L'Industrie", LINDUSTRIE_ID, open_now=False),
                    _place("John's", JOHNS_ID, open_now=None),
                ],
                mode="decision",
            )

        historical.assert_not_called()
        assert all(
            place["queue_evidence"]
            == {
                "coverage": "supported",
                "evidence_kind": "unavailable",
                "current_available": False,
                "provider_available": True,
            }
            for place in result.data["places"]
        )

    async def test_historical_mode_skips_current_and_uses_turn_time(self):
        current = AsyncMock()
        historical = Mock(
            side_effect=lambda place_id, *_args, **_kwargs: (
                _history(place_id) if place_id == LINDUSTRIE_ID else None
            )
        )
        with (
            patch.object(
                damn_lines,
                "get_current_observations",
                new=current,
            ),
            patch.object(
                damn_lines,
                "get_historical_pattern",
                new=historical,
            ),
        ):
            result = await _discover(
                _ctx(),
                [
                    _place("L'Industrie", LINDUSTRIE_ID),
                    _place("Neighborhood Pizza", UNMONITORED_ID),
                ],
                mode="historical",
            )

        current.assert_not_awaited()
        historical.assert_called_once()
        assert historical.call_args.args[1] == datetime.fromisoformat(
            "2026-08-25T19:00:00-04:00"
        )
        by_name = {place["name"]: place for place in result.data["places"]}
        evidence = by_name["L'Industrie"]["queue_evidence"]
        assert evidence["evidence_kind"] == "historical"
        assert evidence["comparable_dates"] == 4
        assert by_name["Neighborhood Pizza"]["queue_evidence"] == {
            "coverage": "unmonitored"
        }
        assert "queue_max_wait_minutes" not in result.data

    async def test_historical_missing_pattern_is_supported_but_unavailable(self):
        with (
            patch.object(
                damn_lines,
                "get_current_observations",
                new_callable=AsyncMock,
            ) as current,
            patch.object(
                damn_lines,
                "get_historical_pattern",
                return_value=None,
            ),
        ):
            result = await _discover(
                _ctx(now_et="invalid"),
                [_place("L'Industrie", LINDUSTRIE_ID)],
                mode="historical",
            )

        current.assert_not_awaited()
        assert result.data["places"][0]["queue_evidence"] == {
            "coverage": "supported",
            "evidence_kind": "unavailable",
        }

    async def test_current_provider_error_degrades_without_failing_discovery(self):
        current = AsyncMock(side_effect=RuntimeError("provider broke"))
        with patch.object(
            damn_lines,
            "get_current_observations",
            new=current,
        ):
            result = await _discover(
                _ctx(),
                [_place("L'Industrie", LINDUSTRIE_ID)],
                mode="decision",
            )

        assert result.ok
        assert result.data["places"][0]["queue_evidence"] == {
            "coverage": "supported",
            "evidence_kind": "unavailable",
            "current_available": False,
            "provider_available": False,
        }

    async def test_current_provider_cancellation_propagates(self):
        current = AsyncMock(side_effect=asyncio.CancelledError)
        with (
            patch.object(
                damn_lines,
                "get_current_observations",
                new=current,
            ),
            self.assertRaises(asyncio.CancelledError),
        ):
            await _discover(
                _ctx(),
                [_place("L'Industrie", LINDUSTRIE_ID)],
                mode="decision",
            )


if __name__ == "__main__":
    unittest.main()
