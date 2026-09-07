from __future__ import annotations

import asyncio
import json
import os
import unittest
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

from app.services.agent.tools.places import damn_lines

LINDUSTRIE_ID = "ChIJ92OsaJVZwokRsC54kf-J-3g"
JOHNS_ID = "ChIJuW43oZNZwokRdE5tLzpuykE"
NOW = datetime(2026, 8, 25, 23, 0, tzinfo=UTC)


def _location(
    *,
    slug: str = "lindustrie_wv",
    captured_at: datetime | str | None = None,
    count: object = 7,
    wait: object = 12,
) -> dict:
    captured = captured_at if captured_at is not None else NOW - timedelta(minutes=1)
    return {
        "slug": slug,
        "status": {
            "captured_at": captured.isoformat() if isinstance(captured, datetime) else captured,
            "current_count": count,
            "wait_minutes": wait,
        },
        "image_url": "https://ignored.example/frame.jpg",
        "stream_supported": True,
    }


def _line(
    bucket_start: str,
    *,
    samples: object = 2,
    people: object = 4,
    wait: object = 8,
) -> dict:
    return {
        "bucket_start": bucket_start,
        "count_samples": samples,
        "people_mean": people,
        "wait_minutes_mean": wait,
    }


class DamnLinesTestCase(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.store: dict[str, object] = {}
        self.ttls: dict[str, int] = {}

        def cache_get(key: str, **_kwargs: object) -> object | None:
            return self.store.get(key)

        def cache_set(key: str, value: object, _ttl: int, **_kwargs: object) -> None:
            self.store[key] = value
            self.ttls[key] = _ttl

        self.cache_get_patch = patch.object(
            damn_lines.cache, "cache_get", side_effect=cache_get
        )
        self.cache_set_patch = patch.object(
            damn_lines.cache, "cache_set", side_effect=cache_set
        )
        self.cache_get_patch.start()
        self.cache_set_patch.start()
        self.env_patch = patch.dict(os.environ, {"DAMNLINES_API_KEY": "test-key"})
        self.env_patch.start()
        damn_lines._current_refresh_task = None
        damn_lines._history_refresh_task = None
        damn_lines._history_loaded = False
        damn_lines._history_last_success = None
        damn_lines._history_index = {}
        self.addCleanup(self.cache_get_patch.stop)
        self.addCleanup(self.cache_set_patch.stop)
        self.addCleanup(self.env_patch.stop)

    def test_registry_is_exact_branch_scoped_and_sources_keep_input_order(self) -> None:
        venue = damn_lines.get_supported_venue(LINDUSTRIE_ID)

        assert venue is not None
        assert venue.slug == "lindustrie_wv"
        assert damn_lines.get_supported_venue("same-brand-other-branch") is None
        sources = damn_lines.source_for_places(
            [JOHNS_ID, "unsupported", LINDUSTRIE_ID, JOHNS_ID]
        )
        assert [source.title for source in sources] == ["Damn Lines: John's of Bleecker Street", "Damn Lines: L'industrie Pizzeria"]
        assert all(source.url.startswith("https://damnlines.com/camera/") for source in sources)

    async def test_unsupported_places_do_not_read_cache_or_call_provider(self) -> None:
        with patch.object(damn_lines, "fetch_json", new_callable=AsyncMock) as fetch:
            result = await damn_lines.get_current_observations(["unsupported"], now=NOW)

        assert result.observations == {}
        assert not result.provider_available
        fetch.assert_not_awaited()

    def test_current_normalization_accepts_full_partial_and_zero(self) -> None:
        full = damn_lines._normalize_current_record(_location(), NOW)
        boundary = damn_lines._normalize_current_record(
            _location(captured_at=NOW - timedelta(minutes=5)), NOW
        )
        wait_only = damn_lines._normalize_current_record(
            _location(count=None, wait=0), NOW
        )
        count_only = damn_lines._normalize_current_record(
            _location(count=0, wait=None), NOW
        )

        assert (full.people_count, full.wait_minutes) == (7, 12)
        assert boundary is not None
        assert (wait_only.people_count, wait_only.wait_minutes) == (None, 0)
        assert (count_only.people_count, count_only.wait_minutes) == (0, None)

    def test_current_normalization_rejects_stale_future_and_invalid_numbers(self) -> None:
        invalid_records = [
            _location(captured_at=NOW - timedelta(minutes=5, microseconds=1)),
            _location(captured_at=NOW + timedelta(microseconds=1)),
            _location(captured_at="not-a-time"),
            _location(count=-1, wait=-2),
            _location(count=True, wait="nan"),
            {"slug": "lindustrie_wv", "status": "bad"},
            {"slug": "removed", "status": _location()["status"]},
        ]

        assert all(damn_lines._normalize_current_record(record, NOW) is None for record in invalid_records)

    async def test_malformed_record_does_not_poison_valid_record(self) -> None:
        payload = {"data": ["bad", _location(), _location(slug="removed")]}
        with patch.object(
            damn_lines, "fetch_json", new_callable=AsyncMock, return_value=(payload, None)
        ):
            result = await damn_lines.get_current_observations([LINDUSTRIE_ID], now=NOW)

        assert result.observations[LINDUSTRIE_ID].people_count == 7
        assert result.provider_available

    async def test_empty_or_stale_cache_attempts_one_refresh_per_invocation(self) -> None:
        stale = damn_lines.QueueObservation(
            LINDUSTRIE_ID, 5, 9, NOW - timedelta(minutes=6)
        )
        self.store[damn_lines._CURRENT_CACHE_KEY] = damn_lines._encode_current(
            {LINDUSTRIE_ID: stale}, NOW - timedelta(minutes=6)
        )
        with patch.object(
            damn_lines,
            "fetch_json",
            new_callable=AsyncMock,
            return_value=({"data": []}, None),
        ) as fetch:
            result = await damn_lines.get_current_observations([LINDUSTRIE_ID], now=NOW)

        assert result.observations == {}
        assert result.provider_available
        fetch.assert_awaited_once()

    async def test_valid_partial_snapshot_does_not_refetch_missing_venue(self) -> None:
        observation = damn_lines.QueueObservation(
            LINDUSTRIE_ID, 5, 9, NOW - timedelta(minutes=1)
        )
        self.store[damn_lines._CURRENT_CACHE_KEY] = damn_lines._encode_current(
            {LINDUSTRIE_ID: observation}, NOW - timedelta(minutes=1)
        )
        with patch.object(damn_lines, "fetch_json", new_callable=AsyncMock) as fetch:
            result = await damn_lines.get_current_observations([JOHNS_ID], now=NOW)

        assert result.observations == {}
        assert result.provider_available
        fetch.assert_not_awaited()

    async def test_concurrent_refreshes_share_one_request(self) -> None:
        started = asyncio.Event()
        release = asyncio.Event()

        async def fetch(*_args: object, **_kwargs: object) -> tuple[dict, None]:
            started.set()
            await release.wait()
            return {"data": [_location()]}, None

        with patch.object(damn_lines, "fetch_json", side_effect=fetch) as request:
            first = asyncio.create_task(
                damn_lines.get_current_observations([LINDUSTRIE_ID], now=NOW)
            )
            await started.wait()
            second = asyncio.create_task(
                damn_lines.get_current_observations([LINDUSTRIE_ID], now=NOW)
            )
            release.set()
            first_result, second_result = await asyncio.gather(first, second)

        assert request.await_count == 1
        assert LINDUSTRIE_ID in first_result.observations
        assert LINDUSTRIE_ID in second_result.observations

    async def test_provider_failures_fail_open_without_replacing_cache(self) -> None:
        for reason in (
            "timed out",
            "authentication failed",
            "rate limited",
            "temporarily unavailable",
            "unexpected response",
        ):
            self.store.pop(damn_lines._CURRENT_CACHE_KEY, None)
            with patch.object(
                damn_lines,
                "fetch_json",
                new_callable=AsyncMock,
                return_value=(None, reason),
            ):
                result = await damn_lines.get_current_observations(
                    [LINDUSTRIE_ID], now=NOW
                )
            assert result.observations == {}
            assert not result.provider_available

    async def test_missing_key_never_calls_provider(self) -> None:
        with (
            patch.dict(os.environ, {"DAMNLINES_API_KEY": ""}),
            patch.object(damn_lines, "fetch_json", new_callable=AsyncMock) as fetch,
        ):
            result = await damn_lines.get_current_observations(
                [LINDUSTRIE_ID], now=NOW
            )

        assert result.observations == {}
        assert not result.provider_available
        fetch.assert_not_awaited()

    async def test_retry_after_sets_shared_cooldown(self) -> None:
        async def rate_limited(*_args: object, **kwargs: object) -> tuple[None, str]:
            kwargs["on_response"](429, {"Retry-After": "120"})
            return None, "rate limited"

        with patch.object(damn_lines, "fetch_json", side_effect=rate_limited) as fetch:
            first = await damn_lines.get_current_observations([LINDUSTRIE_ID], now=NOW)
            second = await damn_lines.get_current_observations([LINDUSTRIE_ID], now=NOW)

        assert not first.provider_available
        assert not second.provider_available
        assert fetch.await_count == 1
        assert damn_lines._COOLDOWN_CACHE_KEY in self.store
        assert self.ttls[damn_lines._COOLDOWN_CACHE_KEY] == 120

    def test_retry_after_accepts_http_date(self) -> None:
        assert (
            damn_lines._retry_after_seconds("Tue, 25 Aug 2026 23:02:00 GMT", NOW)
            == 120
        )

    def test_history_is_sample_weighted_and_bucketed_in_new_york_time(self) -> None:
        rows = {
            LINDUSTRIE_ID: [
                _line("2026-08-04T23:00:00Z", samples=2, people=4, wait=8),
                _line("2026-08-11T23:00:00Z", samples=6, people=8, wait=16),
                _line("2026-08-11T23:30:00Z", samples=0, people=100, wait=100),
                _line("bad", samples=4),
            ]
        }

        index = damn_lines._aggregate_history(rows)
        pattern = index[(LINDUSTRIE_ID, 1, 19)]

        assert pattern.people_mean == 7
        assert pattern.wait_minutes_mean == 14
        assert pattern.sample_count == 8
        assert pattern.comparable_dates == 2
        assert pattern.date_from.isoformat() == "2026-08-04"
        assert pattern.date_to.isoformat() == "2026-08-11"

    async def test_history_pagination_passes_opaque_cursor(self) -> None:
        venue = damn_lines.get_supported_venue(LINDUSTRIE_ID)
        first_page = {
            "data_aggregated": [_line("2026-08-04T23:00:00Z")],
            "pagination": {"has_more": True, "next_cursor": "opaque"},
        }
        second_page = {
            "data_aggregated": [_line("2026-08-11T23:00:00Z")],
            "pagination": {"has_more": False, "next_cursor": None},
        }
        with (
            patch.object(damn_lines, "_SUPPORTED_VENUES", {LINDUSTRIE_ID: venue}),
            patch.object(
                damn_lines,
                "_request_json",
                new_callable=AsyncMock,
                side_effect=[first_page, second_page],
            ) as request,
        ):
            rows = await damn_lines._fetch_history_rows(NOW, "key")

        assert len(rows[LINDUSTRIE_ID]) == 2
        assert "cursor" not in request.await_args_list[0].kwargs["params"]
        assert request.await_args_list[1].kwargs["params"]["cursor"] == "opaque"

    async def test_invalid_history_pagination_fails_open(self) -> None:
        venue = damn_lines.get_supported_venue(LINDUSTRIE_ID)
        page = {
            "data_aggregated": [],
            "pagination": {"has_more": True, "next_cursor": None},
        }
        with (
            patch.object(damn_lines, "_SUPPORTED_VENUES", {LINDUSTRIE_ID: venue}),
            patch.object(
                damn_lines,
                "_request_json",
                new_callable=AsyncMock,
                return_value=page,
            ),
        ):
            assert await damn_lines._fetch_history_rows(NOW, "key") is None

    async def test_successful_history_refresh_populates_shared_and_process_cache(self) -> None:
        rows = {
            LINDUSTRIE_ID: [
                _line("2026-08-04T23:00:00Z", samples=2, people=4, wait=8)
            ]
        }
        with patch.object(
            damn_lines, "_fetch_history_rows", new_callable=AsyncMock, return_value=rows
        ):
            assert await damn_lines.refresh_history(now=NOW, force=True)

        assert damn_lines._HISTORY_CACHE_KEY in self.store
        pattern = damn_lines.get_historical_pattern(
            LINDUSTRIE_ID,
            datetime(2026, 8, 25, 19, tzinfo=damn_lines._NYC),
            now=NOW,
        )
        assert pattern is not None
        assert pattern.comparable_dates == 1

    async def test_recent_history_snapshot_skips_weekly_refresh(self) -> None:
        damn_lines._history_loaded = True
        damn_lines._history_last_success = NOW - timedelta(days=6)
        with patch.object(
            damn_lines, "_fetch_history_rows", new_callable=AsyncMock
        ) as fetch:
            assert not await damn_lines.refresh_history(now=NOW)

        fetch.assert_not_awaited()

    async def test_failed_history_refresh_retains_last_good_snapshot(self) -> None:
        pattern = damn_lines.HistoricalQueuePattern(
            LINDUSTRIE_ID, 1, 19, 7, 14, 8, 2,
            datetime(2026, 8, 4).date(), datetime(2026, 8, 11).date()
        )
        encoded = damn_lines._encode_history({(LINDUSTRIE_ID, 1, 19): pattern}, NOW)
        damn_lines._install_history(encoded)
        with patch.object(
            damn_lines, "_fetch_history_rows", new_callable=AsyncMock, return_value=None
        ):
            refreshed = await damn_lines.refresh_history(now=NOW, force=True)

        assert not refreshed
        assert damn_lines.get_historical_pattern(LINDUSTRIE_ID, datetime(2026, 8, 25, 19, tzinfo=damn_lines._NYC), now=NOW) == pattern

    def test_history_older_than_thirty_days_is_unavailable(self) -> None:
        pattern = damn_lines.HistoricalQueuePattern(
            LINDUSTRIE_ID, 1, 19, 7, 14, 8, 2,
            datetime(2026, 7, 1).date(), datetime(2026, 7, 8).date()
        )
        old = NOW - timedelta(days=30, microseconds=1)
        damn_lines._install_history(
            damn_lines._encode_history({(LINDUSTRIE_ID, 1, 19): pattern}, old)
        )

        result = damn_lines.get_historical_pattern(
            LINDUSTRIE_ID,
            datetime(2026, 8, 25, 19, tzinfo=damn_lines._NYC),
            now=NOW,
        )

        assert result is None

    async def test_warmup_refreshes_empty_history_without_touching_place_lookup(self) -> None:
        with patch.object(
            damn_lines, "refresh_history", new_callable=AsyncMock
        ) as refresh:
            assert damn_lines.get_historical_pattern(LINDUSTRIE_ID, datetime(2026, 8, 25, 19, tzinfo=damn_lines._NYC), now=NOW) is None
            refresh.assert_not_awaited()
            await damn_lines.warm_history(now=NOW)

        refresh.assert_awaited_once_with(now=NOW)

class DamnLinesCacheParsingTests(unittest.TestCase):
    def test_corrupt_history_record_is_ignored_without_poisoning_valid_record(self) -> None:
        valid = damn_lines.HistoricalQueuePattern(
            LINDUSTRIE_ID, 1, 19, 7, 14, 8, 2,
            datetime(2026, 8, 4).date(), datetime(2026, 8, 11).date()
        )
        payload = json.loads(
            damn_lines._encode_history({(LINDUSTRIE_ID, 1, 19): valid}, NOW)
        )
        payload["patterns"].insert(0, ["bad"])

        assert damn_lines._install_history(json.dumps(payload))
        assert damn_lines._history_index[LINDUSTRIE_ID, 1, 19] == valid
