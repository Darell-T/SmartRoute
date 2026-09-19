from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest import IsolatedAsyncioTestCase, TestCase

from evaluation.route_intelligence.incident_fixtures import (
    SnapshotSettings,
    SnapshotStore,
    normalize_event,
    normalize_events,
)


def _settings(**overrides):
    return SnapshotSettings(stale_after_seconds=overrides.get("stale_after_seconds", 10), max_stale_seconds=overrides.get("max_stale_seconds", 20))


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



class SnapshotTests(IsolatedAsyncioTestCase):
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


    async def test_mixed_valid_records_track_invalid_count(self):
        snapshot = await SnapshotStore(_settings()).record_success([_event(), {}])

        assert len(snapshot.incidents) == 1
        assert snapshot.invalid_record_count == 1


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


