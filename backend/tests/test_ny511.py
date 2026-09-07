from __future__ import annotations

import asyncio
import json
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import ClassVar
from unittest import IsolatedAsyncioTestCase, TestCase
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from app.services.incidents.ny511 import (
    DEFAULT_API_URL,
    NY511Client,
    NY511FetchError,
    NY511Poller,
    NY511Settings,
    SnapshotStore,
    normalize_event,
    normalize_events,
)


def _settings(**overrides):
    values = {
        "api_key": "not-a-real-key",
        "enabled": True,
        "poll_interval_seconds": 60.0,
        "request_timeout_seconds": 1.0,
        "stale_after_seconds": 10.0,
        "max_stale_seconds": 20.0,
    }
    values.update(overrides)
    return NY511Settings(**values)


def _event(**overrides):
    values = {
        "ID": "event-1",
        "Latitude": 40.7128,
        "Longitude": -74.006,
        "EventType": "closures",
        "Severity": "Severe",
        "Reported": 1_700_000_000,
        "LastUpdated": 1_700_000_100,
        "IsFullClosure": True,
        "LatitudeSecondary": 40.713,
        "LongitudeSecondary": -74.005,
        "EncodedPolyline": "abc123",
        "County": "New York",
    }
    values.update(overrides)
    return values


class NormalizationTests(TestCase):
    def test_normalizes_official_v2_fields_and_timestamps(self):
        incident = normalize_event(_event())

        assert incident is not None
        assert incident.source_id == "event-1"
        assert incident.severity_normalized == "high"
        assert incident.is_full_closure
        assert incident.reported_at == datetime.fromtimestamp(1700000000, UTC)
        assert incident.geometry == {"encoded_polyline": "abc123"}
        assert incident.secondary_latitude == 40.713

    def test_rejects_invalid_coordinates_and_outside_nyc(self):
        assert normalize_event(_event(Latitude=0)) is None
        assert normalize_event(_event(Latitude=float("nan"))) is None
        assert normalize_event(_event(Latitude=42.0, Longitude=-76.0)) is None

    def test_retains_unknown_severity_and_deduplicates_source_ids(self):
        incidents = normalize_events(
            [_event(), _event(Severity="Unclassified", Description="newer")]
        )

        assert len(incidents) == 1
        assert incidents[0].severity_normalized == "unknown"
        assert incidents[0].description == "newer"

    def test_severity_mapping_handles_common_labels_without_escalating_unknowns(self):
        major = normalize_event(_event(Severity="Major"))
        highest = normalize_event(_event(Severity="Highest"))
        unknown = normalize_event(_event(Severity="Very High"))

        assert major is not None
        assert highest is not None
        assert unknown is not None
        assert major.severity_normalized == "high"
        assert highest.severity_normalized == "critical"
        assert unknown.severity_raw == "Very High"
        assert unknown.severity_normalized == "unknown"

    def test_county_filter_rejects_known_non_nyc_and_uses_bbox_for_missing_county(self):
        in_nyc = normalize_event(_event(County="Kings County"))
        non_nyc = normalize_event(
            _event(County="Nassau County", Latitude=40.72, Longitude=-73.68)
        )
        missing_county = normalize_event(
            _event(County=None, Latitude=40.72, Longitude=-73.68)
        )

        assert in_nyc is not None
        assert non_nyc is None
        assert missing_county is not None

    def test_missing_optional_fields_do_not_reject_valid_event(self):
        incident = normalize_event(
            _event(
                Severity=None,
                IsFullClosure="yes",
                LatitudeSecondary=0,
                LongitudeSecondary=0,
            )
        )

        assert incident is not None
        assert incident.is_full_closure is None
        assert incident.secondary_latitude is None
        assert incident.severity_normalized == "unknown"

    def test_missing_key_and_invalid_configuration_disable_the_source(self):
        with patch.dict(
            "os.environ", {"NY511_API_KEY": "", "NY511_ENABLED": "true"}, clear=True
        ):
            settings = NY511Settings.from_env()
        assert not settings.enabled
        assert settings.diagnostic == "API key not configured"
        with patch.dict(
            "os.environ",
            {"NY511_API_KEY": "key", "NY511_API_BASE_URL": "http://bad"},
            clear=True,
        ):
            settings = NY511Settings.from_env()
        assert not settings.enabled
        assert "key" not in (settings.diagnostic or "")

    def test_poll_interval_is_configurable_but_cannot_break_provider_throttle(self):
        with patch.dict(
            "os.environ",
            {"NY511_API_KEY": "configured", "NY511_POLL_INTERVAL_SECONDS": "1"},
            clear=True,
        ):
            settings = NY511Settings.from_env()
        assert settings.enabled
        assert settings.poll_interval_seconds == 60.0

    def test_non_finite_numeric_configuration_disables_source(self):
        with patch.dict(
            "os.environ",
            {"NY511_API_KEY": "configured", "NY511_POLL_INTERVAL_SECONDS": "nan"},
            clear=True,
        ):
            settings = NY511Settings.from_env()
        assert not settings.enabled
        assert settings.diagnostic == "invalid 511NY numeric configuration"
        assert "configured" not in (settings.diagnostic or "")

    def test_base_url_cannot_include_another_host_or_credential_query(self):
        with patch.dict(
            "os.environ",
            {
                "NY511_API_KEY": "configured",
                "NY511_API_BASE_URL": "https://example.test/event",
            },
            clear=True,
        ):
            assert not NY511Settings.from_env().enabled
        with patch.dict(
            "os.environ",
            {
                "NY511_API_KEY": "configured",
                "NY511_API_BASE_URL": "https://511ny.org/api/v2/get/event?key=secret",
            },
            clear=True,
        ):
            settings = NY511Settings.from_env()
        assert not settings.enabled
        assert settings.diagnostic == "invalid API base URL"

    def test_fixture_mode_is_explicit_development_only_and_needs_no_key(self):
        with patch.dict(
            "os.environ",
            {
                "NY511_FIXTURE_PATH": "C:/fixtures/ny511.json",
                "SMARTROUTE_ENV": "development",
            },
            clear=True,
        ):
            settings = NY511Settings.from_env()
        assert settings.enabled
        assert settings.fixture_path == "C:/fixtures/ny511.json"
        assert settings.diagnostic == "using development 511NY fixture"
        assert settings.api_key is None

    def test_fixture_mode_is_rejected_in_production_without_falling_back_to_live(self):
        with patch.dict(
            "os.environ",
            {
                "NY511_FIXTURE_PATH": "C:/fixtures/ny511.json",
                "NY511_API_KEY": "live-key",
                "SMARTROUTE_ENV": "production",
            },
            clear=True,
        ):
            settings = NY511Settings.from_env()
        assert not settings.enabled
        assert settings.fixture_path is None
        assert (
            settings.diagnostic
            == "511NY fixture mode requires a development or test environment"
        )
        assert "live-key" not in (settings.diagnostic or "")


class ClientTests(IsolatedAsyncioTestCase):
    async def test_success_uses_v2_endpoint_and_safe_query_params(self):
        class Response:
            status_code = 200

            def json(self):
                return [_event()]

        class Client:
            calls: ClassVar[list[tuple[str, dict[str, str]]]] = []

            def __init__(self, **_kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *_args):
                return False

            async def get(self, url, *, params):
                type(self).calls.append((url, params))
                return Response()

        with patch("app.services.incidents.ny511.httpx.AsyncClient", Client):
            result = await NY511Client(_settings()).fetch_events()
        assert result == [_event()]
        assert Client.calls == [
            (DEFAULT_API_URL, {"key": "not-a-real-key", "format": "json"})
        ]

    async def test_401_is_not_retried_and_does_not_leak_request_details(self):
        class Response:
            status_code = 401

        class Client:
            calls = 0

            def __init__(self, **_kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *_args):
                return False

            async def get(self, *_args, **_kwargs):
                type(self).calls += 1
                return Response()

        with (
            patch("app.services.incidents.ny511.httpx.AsyncClient", Client),
            pytest.raises(NY511FetchError) as raised,
        ):
            await NY511Client(_settings()).fetch_events()
        assert Client.calls == 1
        assert str(raised.value) == "511NY request failed with HTTP 401"

    async def test_429_and_5xx_retry_with_bounded_backoff(self):
        class Response:
            headers: ClassVar[dict[str, str]] = {"Retry-After": "7"}

            def __init__(self, status_code):
                self.status_code = status_code

        class Client:
            statuses: ClassVar[list[int]] = [429, 503, 503]

            def __init__(self, **_kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *_args):
                return False

            async def get(self, *_args, **_kwargs):
                return Response(type(self).statuses.pop(0))

        with (
            patch("app.services.incidents.ny511.httpx.AsyncClient", Client),
            patch(
                "app.services.incidents.ny511.asyncio.sleep", new=AsyncMock()
            ) as sleep,
            pytest.raises(NY511FetchError, match="HTTP 503"),
        ):
            await NY511Client(_settings()).fetch_events()
        assert sleep.await_count == 2
        assert sleep.await_args_list[0].args == (7.0,)

    async def test_excessive_retry_after_is_capped_and_diagnostics_hide_secrets(self):
        class Response:
            status_code = 429
            headers: ClassVar[dict[str, str]] = {"Retry-After": "999999"}

        class Client:
            def __init__(self, **_kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *_args):
                return False

            async def get(self, *_args, **_kwargs):
                return Response()

        with (
            patch("app.services.incidents.ny511.httpx.AsyncClient", Client),
            patch(
                "app.services.incidents.ny511.asyncio.sleep", new=AsyncMock()
            ) as sleep,
            pytest.raises(NY511FetchError) as raised,
        ):
            await NY511Client(_settings(api_key="super-secret")).fetch_events()
        assert sleep.await_args_list[0].args == (60.0,)
        assert sleep.await_args_list[1].args == (60.0,)
        assert "super-secret" not in str(raised.value)

    async def test_timeout_retries_with_bounded_backoff(self):
        class Client:
            calls = 0

            def __init__(self, **_kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *_args):
                return False

            async def get(self, *_args, **_kwargs):
                type(self).calls += 1
                raise httpx.ReadTimeout(
                    "slow", request=httpx.Request("GET", "https://example.test")
                )

        with (
            patch("app.services.incidents.ny511.httpx.AsyncClient", Client),
            patch(
                "app.services.incidents.ny511.asyncio.sleep", new=AsyncMock()
            ) as sleep,
            pytest.raises(NY511FetchError, match="timed out"),
        ):
            await NY511Client(_settings()).fetch_events()
        assert Client.calls == 3
        assert sleep.await_count == 2

    async def test_one_attempt_client_never_retries_for_live_certification(self):
        class Client:
            calls = 0

            def __init__(self, **_kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *_args):
                return False

            async def get(self, *_args, **_kwargs):
                type(self).calls += 1
                raise httpx.ReadTimeout(
                    "slow", request=httpx.Request("GET", "https://example.test")
                )

        with (
            patch("app.services.incidents.ny511.httpx.AsyncClient", Client),
            patch(
                "app.services.incidents.ny511.asyncio.sleep", new=AsyncMock()
            ) as sleep,
            pytest.raises(NY511FetchError, match="timed out"),
        ):
            await NY511Client(_settings(), max_attempts=1).fetch_events()
        assert Client.calls == 1
        sleep.assert_not_awaited()

    async def test_connection_error_retries_and_is_sanitized(self):
        class Client:
            calls = 0

            def __init__(self, **_kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *_args):
                return False

            async def get(self, *_args, **_kwargs):
                type(self).calls += 1
                message = "provider detail key=secret"
                raise httpx.ConnectError(
                    message, request=httpx.Request("GET", "https://example.test")
                )

        with (
            patch("app.services.incidents.ny511.httpx.AsyncClient", Client),
            patch("app.services.incidents.ny511.asyncio.sleep", new=AsyncMock()),
            pytest.raises(NY511FetchError) as raised,
        ):
            await NY511Client(_settings()).fetch_events()
        assert Client.calls == 3
        assert str(raised.value) == "511NY connection failed"

    async def test_malformed_and_unexpected_responses_fail_safely(self):
        class Response:
            status_code = 200

            def json(self):
                message = "not json"
                raise ValueError(message)

        class Client:
            def __init__(self, **_kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *_args):
                return False

            async def get(self, *_args, **_kwargs):
                return Response()

        with (
            patch("app.services.incidents.ny511.httpx.AsyncClient", Client),
            pytest.raises(NY511FetchError, match="malformed JSON"),
        ):
            await NY511Client(_settings()).fetch_events()

    async def test_unexpected_schema_fails_without_retry(self):
        class Response:
            status_code = 200

            def json(self):
                return {"events": []}

        class Client:
            def __init__(self, **_kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *_args):
                return False

            async def get(self, *_args, **_kwargs):
                return Response()

        with (
            patch("app.services.incidents.ny511.httpx.AsyncClient", Client),
            pytest.raises(NY511FetchError, match="unexpected event schema"),
        ):
            await NY511Client(_settings()).fetch_events()


class StoreAndPollerTests(IsolatedAsyncioTestCase):
    async def test_empty_upstream_list_is_a_valid_fresh_snapshot(self):
        store = SnapshotStore(_settings())

        snapshot = await store.record_success([])

        assert snapshot.status == "fresh"
        assert snapshot.incidents == []
        assert snapshot.source_record_count == 0
        assert snapshot.invalid_record_count == 0

    async def test_never_fetched_snapshot_has_no_source_origin(self):
        snapshot = await SnapshotStore(_settings()).get_snapshot()
        assert snapshot.status == "unavailable"
        assert snapshot.source_origin is None

    async def test_fixture_refresh_uses_normal_snapshot_path_and_marks_origin(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "events.json"
            path.write_text(
                json.dumps([_event(), _event(ID="outside", Latitude=42.0)]),
                encoding="utf-8",
            )
            settings = _settings(api_key=None, fixture_path=str(path))
            client = AsyncMock()
            poller = NY511Poller(settings, client=client)

            assert await poller.refresh()
            snapshot = await poller.store.get_snapshot()

        assert client.fetch_events.await_count == 0
        assert snapshot.source_origin == "fixture"
        assert snapshot.source_record_count == 2
        assert snapshot.nyc_record_count == 1
        assert snapshot.invalid_record_count == 0
        assert snapshot.status == "fresh"

    async def test_empty_fixture_is_a_fresh_complete_snapshot(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "events.json"
            path.write_text("[]", encoding="utf-8")
            poller = NY511Poller(_settings(api_key=None, fixture_path=str(path)))
            assert await poller.refresh()
            snapshot = await poller.store.get_snapshot()
        assert snapshot.status == "fresh"
        assert snapshot.source_origin == "fixture"
        assert snapshot.incidents == []

    async def test_malformed_fixture_preserves_a_good_snapshot(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "events.json"
            path.write_text("{not-json", encoding="utf-8")
            settings = _settings(api_key=None, fixture_path=str(path))
            store = SnapshotStore(settings)
            await store.record_success([_event()])
            poller = NY511Poller(settings, store=store)

            assert not await poller.refresh()
            snapshot = await store.get_snapshot()

        assert snapshot.source_origin == "live"
        assert len(snapshot.incidents) == 1
        assert snapshot.last_error == "511NY fixture is malformed or unreadable"

    async def test_mixed_valid_records_track_invalid_count(self):
        snapshot = await SnapshotStore(_settings()).record_success([_event(), {}])

        assert len(snapshot.incidents) == 1
        assert snapshot.invalid_record_count == 1

    async def test_all_malformed_records_fail_refresh_and_preserve_prior_snapshot(self):
        settings = _settings()
        store = SnapshotStore(settings)
        await store.record_success([_event()])
        client = AsyncMock()
        client.fetch_events.return_value = [{}, _event(ID=None, Latitude=0)]
        poller = NY511Poller(settings, client=client, store=store)

        assert not await poller.refresh()
        snapshot = await store.get_snapshot()

        assert len(snapshot.incidents) == 1
        assert snapshot.invalid_record_count == 0
        assert snapshot.last_error == "511NY response contained no usable event records"

    async def test_snapshot_statuses_and_failure_preserves_last_success(self):
        settings = _settings(stale_after_seconds=10, max_stale_seconds=20)
        store = SnapshotStore(settings)
        now = datetime(2026, 1, 1, tzinfo=UTC)
        await store.record_success([_event()], fetched_at=now)
        await store.record_failure("511NY request timed out")

        fresh = await store.get_snapshot(now=now + timedelta(seconds=10))
        stale = await store.get_snapshot(now=now + timedelta(seconds=11))
        unavailable = await store.get_snapshot(now=now + timedelta(seconds=21))
        assert fresh.status == "fresh"
        assert stale.status == "stale"
        assert len(stale.incidents) == 1
        assert unavailable.status == "unavailable"
        assert unavailable.incidents == []
        assert unavailable.last_error == "511NY request timed out"

    async def test_refresh_is_single_flight_and_failure_keeps_snapshot(self):
        settings = _settings()
        store = SnapshotStore(settings)
        await store.record_success([_event()])
        client = AsyncMock()
        entered = asyncio.Event()
        release = asyncio.Event()

        async def fetch():
            entered.set()
            await release.wait()
            message = "511NY request timed out"
            raise NY511FetchError(message, retryable=True)

        client.fetch_events.side_effect = fetch
        poller = NY511Poller(settings, client=client, store=store)
        first = asyncio.create_task(poller.refresh())
        await entered.wait()
        assert not await poller.refresh()
        release.set()
        assert not await first
        assert client.fetch_events.await_count == 1
        assert len((await store.get_snapshot()).incidents) == 1

    async def test_start_does_initial_fetch_once_and_stop_is_clean(self):
        settings = _settings(poll_interval_seconds=3600)
        client = AsyncMock()
        client.fetch_events.return_value = [_event()]
        poller = NY511Poller(settings, client=client)
        task = poller.start()
        assert task is poller.start()
        for _ in range(20):
            if client.fetch_events.await_count:
                break
            await asyncio.sleep(0)
        assert client.fetch_events.await_count == 1
        await poller.stop()
        assert task is not None
        assert task.done()

    async def test_disabled_poller_makes_no_request_and_reports_unavailable(self):
        settings = _settings(enabled=False, diagnostic="API key not configured")
        client = AsyncMock()
        poller = NY511Poller(settings, client=client)
        assert poller.start() is None
        assert not await poller.refresh()
        assert client.fetch_events.await_count == 0
        snapshot = await poller.store.get_snapshot()
        assert snapshot.status == "unavailable"
        assert snapshot.last_error == "API key not configured"
