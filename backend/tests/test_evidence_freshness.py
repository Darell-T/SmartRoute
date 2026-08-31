from __future__ import annotations

import unittest
from dataclasses import FrozenInstanceError
from datetime import UTC, datetime, timedelta

import pytest
from app.services.evidence import (
    current_payload,
    evidence_envelope,
    parse_timestamp,
)
from app.services.trips.preparation import evidence as preparation_evidence
from evaluation.route_intelligence import advisor_context

NOW = datetime.now(UTC)


class EvidenceFreshnessTests(unittest.TestCase):
    def test_current_stale_and_unavailable_are_distinct(self):
        current = evidence_envelope(
            "mta",
            [{"id": "current"}],
            observed_at=NOW,
            ttl_seconds=60,
        )
        unavailable = evidence_envelope(
            "ticketmaster",
            [],
            observed_at=NOW,
            available=False,
        )
        assert current.status_at(NOW) == "current"
        assert current.status_at(NOW + timedelta(seconds=61)) == "stale"
        assert unavailable.status_at(NOW) == "unavailable"

    def test_expired_payload_is_suppressed_but_provenance_remains(self):
        stale = evidence_envelope(
            "mta_alerts",
            [{"header": "expired"}],
            observed_at=NOW,
            ttl_seconds=30,
        )
        later = NOW + timedelta(seconds=31)
        assert current_payload(stale, now=later, empty=[]) == []
        serialized = stale.to_dict(later)
        assert serialized["status"] == "stale"
        assert serialized["source"] == "mta_alerts"
        assert serialized["payload"] == [{"header": "expired"}]

    def test_current_payload_returns_current_evidence(self):
        current = evidence_envelope(
            "mta",
            [{"id": "current"}],
            observed_at=NOW,
            ttl_seconds=60,
        )

        assert current_payload(current, now=NOW, empty=[]) == [{"id": "current"}]

    def test_evidence_defaults_to_available_and_cannot_be_rewritten(self):
        envelope = evidence_envelope("mta", ["payload"], observed_at=NOW)

        assert envelope.status_at(NOW) == "current"
        with pytest.raises(FrozenInstanceError):
            envelope.available = False

    def test_timestamp_parser_rejects_untrusted_shapes_and_normalizes_offsets(self):
        assert parse_timestamp("not-a-time") is None
        assert parse_timestamp("2026-08-30T12:00:00") is None
        assert parse_timestamp({"timestamp": "2026-08-30T12:00:00Z"}) is None
        assert parse_timestamp("2026-08-30T08:00:00-04:00") == datetime(
            2026, 8, 30, 12, 0, tzinfo=UTC
        )

    def test_zero_and_negative_ttl_expire_immediately_after_observation(self):
        just_after = NOW + timedelta(microseconds=1)
        for ttl_seconds in (0, -30):
            with self.subTest(ttl_seconds=ttl_seconds):
                envelope = evidence_envelope(
                    "mta",
                    ["payload"],
                    observed_at=NOW,
                    ttl_seconds=ttl_seconds,
                )
                assert envelope.status_at(NOW) == "current"
                assert envelope.status_at(just_after) == "stale"

    def test_serialization_preserves_source_bound_and_expiry(self):
        valid_until = NOW + timedelta(minutes=5)
        envelope = evidence_envelope(
            "x" * 100,
            ["payload"],
            observed_at=NOW,
            valid_until=valid_until,
        )

        serialized = envelope.to_dict(NOW)
        assert serialized["source"] == "x" * 80
        assert serialized["observedAt"] == NOW.isoformat()
        assert serialized["validUntil"] == valid_until.isoformat()

    def test_merged_route_evidence_keeps_the_worst_freshness_status(self):
        current = evidence_envelope(
            "mta",
            [{"id": "current"}],
            observed_at=NOW,
            ttl_seconds=60,
        )
        stale = evidence_envelope(
            "mta",
            [{"id": "stale"}],
            observed_at=NOW - timedelta(minutes=5),
            ttl_seconds=60,
        )

        current_only = preparation_evidence.merge_evidence_envelopes(
            [{"alerts": current}]
        )["alerts"]
        mixed = preparation_evidence.merge_evidence_envelopes(
            [{"alerts": current}, {"alerts": stale}]
        )["alerts"]

        assert current_only.status_at(NOW) == "current"
        assert current_only.current_payload(NOW) == [{"id": "current"}]
        assert mixed.status_at(NOW) == "stale"
        assert mixed.current_payload(NOW) is None
        assert preparation_evidence.serialize_evidence_envelopes(
            {"alerts": mixed}
        )["alerts"]["payload"] == []

    def test_advisor_boundary_excludes_expired_evidence(self):
        stale = evidence_envelope(
            "ticketmaster",
            [{"event_id": "expired", "title": "Old event"}],
            observed_at=NOW - timedelta(minutes=10),
            valid_until=NOW - timedelta(minutes=1),
        )
        payload = advisor_context.build_advisor_payload(
            routes=[[]],
            service_alerts=[],
            ticketmaster_event_impacts=[{"event_id": "fallback"}],
            evidence={"events": stale},
        )
        assert payload["ticketmaster_event_impacts"] == []
        assert payload["evidence"]["events"]["status"] == "stale"
        assert payload["evidence"]["events"]["payload"] == []


if __name__ == "__main__":
    unittest.main()
