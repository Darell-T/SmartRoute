from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from unittest import IsolatedAsyncioTestCase, TestCase
from unittest.mock import AsyncMock, patch

import httpx

from app.services.ny511 import (
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
        self.assertEqual(incident.source_id, "event-1")
        self.assertEqual(incident.severity_normalized, "high")
        self.assertTrue(incident.is_full_closure)
        self.assertEqual(incident.reported_at, datetime.fromtimestamp(1_700_000_000, UTC))
        self.assertEqual(incident.geometry, {"encoded_polyline": "abc123"})
        self.assertEqual(incident.secondary_latitude, 40.713)

    def test_rejects_invalid_coordinates_and_outside_nyc(self):
        self.assertIsNone(normalize_event(_event(Latitude=0)))
        self.assertIsNone(normalize_event(_event(Latitude=float("nan"))))
        self.assertIsNone(normalize_event(_event(Latitude=42.0, Longitude=-76.0)))

    def test_retains_unknown_severity_and_deduplicates_source_ids(self):
        incidents = normalize_events([_event(), _event(Severity="Unclassified", Description="newer")])

        self.assertEqual(len(incidents), 1)
        self.assertEqual(incidents[0].severity_normalized, "unknown")
        self.assertEqual(incidents[0].description, "newer")

    def test_severity_mapping_handles_common_labels_without_escalating_unknowns(self):
        major = normalize_event(_event(Severity="Major"))
        highest = normalize_event(_event(Severity="Highest"))
        unknown = normalize_event(_event(Severity="Very High"))

        assert major is not None and highest is not None and unknown is not None
        self.assertEqual(major.severity_normalized, "high")
        self.assertEqual(highest.severity_normalized, "critical")
        self.assertEqual(unknown.severity_raw, "Very High")
        self.assertEqual(unknown.severity_normalized, "unknown")

    def test_county_filter_rejects_known_non_nyc_and_uses_bbox_for_missing_county(self):
        in_nyc = normalize_event(_event(County="Kings County"))
        non_nyc = normalize_event(_event(County="Nassau County", Latitude=40.72, Longitude=-73.68))
        missing_county = normalize_event(_event(County=None, Latitude=40.72, Longitude=-73.68))

        self.assertIsNotNone(in_nyc)
        self.assertIsNone(non_nyc)
        self.assertIsNotNone(missing_county)

    def test_missing_optional_fields_do_not_reject_valid_event(self):
        incident = normalize_event(_event(Severity=None, IsFullClosure="yes", LatitudeSecondary=0, LongitudeSecondary=0))

        assert incident is not None
        self.assertIsNone(incident.is_full_closure)
        self.assertIsNone(incident.secondary_latitude)
        self.assertEqual(incident.severity_normalized, "unknown")

    def test_missing_key_and_invalid_configuration_disable_the_source(self):
        with patch.dict("os.environ", {"NY511_API_KEY": "", "NY511_ENABLED": "true"}, clear=True):
            settings = NY511Settings.from_env()
        self.assertFalse(settings.enabled)
        self.assertEqual(settings.diagnostic, "API key not configured")
        with patch.dict("os.environ", {"NY511_API_KEY": "key", "NY511_API_BASE_URL": "http://bad"}, clear=True):
            settings = NY511Settings.from_env()
        self.assertFalse(settings.enabled)
        self.assertNotIn("key", settings.diagnostic or "")

    def test_poll_interval_is_configurable_but_cannot_break_provider_throttle(self):
        with patch.dict("os.environ", {"NY511_API_KEY": "configured", "NY511_POLL_INTERVAL_SECONDS": "1"}, clear=True):
            settings = NY511Settings.from_env()
        self.assertTrue(settings.enabled)
        self.assertEqual(settings.poll_interval_seconds, 60.0)

    def test_base_url_cannot_include_another_host_or_credential_query(self):
        with patch.dict("os.environ", {"NY511_API_KEY": "configured", "NY511_API_BASE_URL": "https://example.test/event"}, clear=True):
            self.assertFalse(NY511Settings.from_env().enabled)
        with patch.dict("os.environ", {"NY511_API_KEY": "configured", "NY511_API_BASE_URL": "https://511ny.org/api/v2/get/event?key=secret"}, clear=True):
            settings = NY511Settings.from_env()
        self.assertFalse(settings.enabled)
        self.assertEqual(settings.diagnostic, "invalid API base URL")


class ClientTests(IsolatedAsyncioTestCase):
    async def test_success_uses_v2_endpoint_and_safe_query_params(self):
        class Response:
            status_code = 200
            def json(self): return [_event()]

        class Client:
            calls = []
            def __init__(self, **_kwargs): pass
            async def __aenter__(self): return self
            async def __aexit__(self, *_args): return False
            async def get(self, url, *, params):
                type(self).calls.append((url, params))
                return Response()

        with patch("app.services.ny511.httpx.AsyncClient", Client):
            result = await NY511Client(_settings()).fetch_events()
        self.assertEqual(result, [_event()])
        self.assertEqual(Client.calls, [(DEFAULT_API_URL, {"key": "not-a-real-key", "format": "json"})])

    async def test_non_transient_http_errors_are_not_retried(self):
        class Response:
            status_code = 403
        class Client:
            calls = 0
            def __init__(self, **_kwargs): pass
            async def __aenter__(self): return self
            async def __aexit__(self, *_args): return False
            async def get(self, *_args, **_kwargs):
                type(self).calls += 1
                return Response()
        with patch("app.services.ny511.httpx.AsyncClient", Client):
            with self.assertRaisesRegex(NY511FetchError, "HTTP 403"):
                await NY511Client(_settings()).fetch_events()
        self.assertEqual(Client.calls, 1)

    async def test_401_is_not_retried_and_does_not_leak_request_details(self):
        class Response:
            status_code = 401
        class Client:
            calls = 0
            def __init__(self, **_kwargs): pass
            async def __aenter__(self): return self
            async def __aexit__(self, *_args): return False
            async def get(self, *_args, **_kwargs):
                type(self).calls += 1
                return Response()
        with patch("app.services.ny511.httpx.AsyncClient", Client):
            with self.assertRaises(NY511FetchError) as raised:
                await NY511Client(_settings()).fetch_events()
        self.assertEqual(Client.calls, 1)
        self.assertEqual(str(raised.exception), "511NY request failed with HTTP 401")

    async def test_429_and_5xx_retry_with_bounded_backoff(self):
        class Response:
            headers = {"Retry-After": "7"}
            def __init__(self, status_code): self.status_code = status_code
        class Client:
            statuses = [429, 503, 503]
            def __init__(self, **_kwargs): pass
            async def __aenter__(self): return self
            async def __aexit__(self, *_args): return False
            async def get(self, *_args, **_kwargs): return Response(type(self).statuses.pop(0))
        with patch("app.services.ny511.httpx.AsyncClient", Client), patch("app.services.ny511.asyncio.sleep", new=AsyncMock()) as sleep:
            with self.assertRaisesRegex(NY511FetchError, "HTTP 503"):
                await NY511Client(_settings()).fetch_events()
        self.assertEqual(sleep.await_count, 2)
        self.assertEqual(sleep.await_args_list[0].args, (7.0,))

    async def test_excessive_retry_after_is_capped_and_diagnostics_hide_secrets(self):
        class Response:
            status_code = 429
            headers = {"Retry-After": "999999"}
        class Client:
            def __init__(self, **_kwargs): pass
            async def __aenter__(self): return self
            async def __aexit__(self, *_args): return False
            async def get(self, *_args, **_kwargs): return Response()
        with patch("app.services.ny511.httpx.AsyncClient", Client), patch("app.services.ny511.asyncio.sleep", new=AsyncMock()) as sleep:
            with self.assertRaises(NY511FetchError) as raised:
                await NY511Client(_settings(api_key="super-secret")).fetch_events()
        self.assertEqual(sleep.await_args_list[0].args, (60.0,))
        self.assertEqual(sleep.await_args_list[1].args, (60.0,))
        self.assertNotIn("super-secret", str(raised.exception))

    async def test_timeout_retries_with_bounded_backoff(self):
        class Client:
            calls = 0
            def __init__(self, **_kwargs): pass
            async def __aenter__(self): return self
            async def __aexit__(self, *_args): return False
            async def get(self, *_args, **_kwargs):
                type(self).calls += 1
                raise httpx.ReadTimeout("slow", request=httpx.Request("GET", "https://example.test"))
        with patch("app.services.ny511.httpx.AsyncClient", Client), patch("app.services.ny511.asyncio.sleep", new=AsyncMock()) as sleep:
            with self.assertRaisesRegex(NY511FetchError, "timed out"):
                await NY511Client(_settings()).fetch_events()
        self.assertEqual(Client.calls, 3)
        self.assertEqual(sleep.await_count, 2)

    async def test_connection_error_retries_and_is_sanitized(self):
        class Client:
            calls = 0
            def __init__(self, **_kwargs): pass
            async def __aenter__(self): return self
            async def __aexit__(self, *_args): return False
            async def get(self, *_args, **_kwargs):
                type(self).calls += 1
                raise httpx.ConnectError("provider detail key=secret", request=httpx.Request("GET", "https://example.test"))
        with patch("app.services.ny511.httpx.AsyncClient", Client), patch("app.services.ny511.asyncio.sleep", new=AsyncMock()):
            with self.assertRaises(NY511FetchError) as raised:
                await NY511Client(_settings()).fetch_events()
        self.assertEqual(Client.calls, 3)
        self.assertEqual(str(raised.exception), "511NY connection failed")

    async def test_malformed_and_unexpected_responses_fail_safely(self):
        class Response:
            status_code = 200
            def json(self): raise ValueError("not json")
        class Client:
            def __init__(self, **_kwargs): pass
            async def __aenter__(self): return self
            async def __aexit__(self, *_args): return False
            async def get(self, *_args, **_kwargs): return Response()
        with patch("app.services.ny511.httpx.AsyncClient", Client):
            with self.assertRaisesRegex(NY511FetchError, "malformed JSON"):
                await NY511Client(_settings()).fetch_events()

    async def test_unexpected_schema_fails_without_retry(self):
        class Response:
            status_code = 200
            def json(self): return {"events": []}
        class Client:
            def __init__(self, **_kwargs): pass
            async def __aenter__(self): return self
            async def __aexit__(self, *_args): return False
            async def get(self, *_args, **_kwargs): return Response()
        with patch("app.services.ny511.httpx.AsyncClient", Client):
            with self.assertRaisesRegex(NY511FetchError, "unexpected event schema"):
                await NY511Client(_settings()).fetch_events()


class StoreAndPollerTests(IsolatedAsyncioTestCase):
    async def test_empty_upstream_list_is_a_valid_fresh_snapshot(self):
        store = SnapshotStore(_settings())

        snapshot = await store.record_success([])

        self.assertEqual(snapshot.status, "fresh")
        self.assertEqual(snapshot.incidents, [])
        self.assertEqual(snapshot.source_record_count, 0)
        self.assertEqual(snapshot.invalid_record_count, 0)

    async def test_mixed_valid_records_track_invalid_count(self):
        snapshot = await SnapshotStore(_settings()).record_success([_event(), {}])

        self.assertEqual(len(snapshot.incidents), 1)
        self.assertEqual(snapshot.invalid_record_count, 1)

    async def test_all_malformed_records_fail_refresh_and_preserve_prior_snapshot(self):
        settings = _settings()
        store = SnapshotStore(settings)
        await store.record_success([_event()])
        client = AsyncMock()
        client.fetch_events.return_value = [{}, _event(ID=None, Latitude=0)]
        poller = NY511Poller(settings, client=client, store=store)

        self.assertFalse(await poller.refresh())
        snapshot = await store.get_snapshot()

        self.assertEqual(len(snapshot.incidents), 1)
        self.assertEqual(snapshot.invalid_record_count, 0)
        self.assertEqual(snapshot.last_error, "511NY response contained no usable event records")

    async def test_snapshot_statuses_and_failure_preserves_last_success(self):
        settings = _settings(stale_after_seconds=10, max_stale_seconds=20)
        store = SnapshotStore(settings)
        now = datetime(2026, 1, 1, tzinfo=UTC)
        await store.record_success([_event()], fetched_at=now)
        await store.record_failure("511NY request timed out")

        fresh = await store.get_snapshot(now=now + timedelta(seconds=10))
        stale = await store.get_snapshot(now=now + timedelta(seconds=11))
        unavailable = await store.get_snapshot(now=now + timedelta(seconds=21))
        self.assertEqual(fresh.status, "fresh")
        self.assertEqual(stale.status, "stale")
        self.assertEqual(len(stale.incidents), 1)
        self.assertEqual(unavailable.status, "unavailable")
        self.assertEqual(unavailable.incidents, [])
        self.assertEqual(unavailable.last_error, "511NY request timed out")

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
            raise NY511FetchError("511NY request timed out", retryable=True)
        client.fetch_events.side_effect = fetch
        poller = NY511Poller(settings, client=client, store=store)
        first = asyncio.create_task(poller.refresh())
        await entered.wait()
        self.assertFalse(await poller.refresh())
        release.set()
        self.assertFalse(await first)
        self.assertEqual(client.fetch_events.await_count, 1)
        self.assertEqual(len((await store.get_snapshot()).incidents), 1)

    async def test_start_does_initial_fetch_once_and_stop_is_clean(self):
        settings = _settings(poll_interval_seconds=3600)
        client = AsyncMock()
        client.fetch_events.return_value = [_event()]
        poller = NY511Poller(settings, client=client)
        task = poller.start()
        self.assertIs(task, poller.start())
        for _ in range(20):
            if client.fetch_events.await_count:
                break
            await asyncio.sleep(0)
        self.assertEqual(client.fetch_events.await_count, 1)
        await poller.stop()
        self.assertTrue(task is not None and task.done())

    async def test_disabled_poller_makes_no_request_and_reports_unavailable(self):
        settings = _settings(enabled=False, diagnostic="API key not configured")
        client = AsyncMock()
        poller = NY511Poller(settings, client=client)
        self.assertIsNone(poller.start())
        self.assertFalse(await poller.refresh())
        self.assertEqual(client.fetch_events.await_count, 0)
        snapshot = await poller.store.get_snapshot()
        self.assertEqual(snapshot.status, "unavailable")
        self.assertEqual(snapshot.last_error, "API key not configured")
