from __future__ import annotations

import unittest
from datetime import UTC, datetime, timedelta

from app.services.evidence import current_payload, evidence_envelope
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
