from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from app.services.agent.session import (
    DEFAULT_TTL,
    MAX_CONTINUATION_ATTEMPTS,
    MAX_PERSISTED_CONTINUATIONS,
    PendingContinuation,
    retain_continuations,
)


class PendingContinuationTests(unittest.TestCase):
    def test_defaults_to_thirty_minutes_and_round_trips_allowed_metadata(self) -> None:
        now = datetime(2026, 8, 15, 12, tzinfo=timezone.utc)
        item = PendingContinuation.create(
            ("route",),
            missing_fields=("destination",),
            constraints=("accessible",),
            references=("place_123",),
            approved_recovery_options=("ask destination",),
            now=now,
        )

        self.assertEqual(item.expires_at - item.created_at, DEFAULT_TTL)
        self.assertFalse(item.is_expired(now))
        self.assertTrue(item.is_expired(now + DEFAULT_TTL))
        restored = PendingContinuation.from_dict(item.to_dict())
        self.assertEqual(restored, item)
        self.assertEqual(restored.attempt_count, 1)
        self.assertNotIn("provider", item.to_dict())

    def test_retains_newest_three_and_drops_expired(self) -> None:
        now = datetime(2026, 8, 15, 12, tzinfo=timezone.utc)
        items = [
            PendingContinuation.create((str(index),), now=now + timedelta(minutes=index))
            for index in range(MAX_PERSISTED_CONTINUATIONS + 1)
        ]
        items.append(
            PendingContinuation.create(
                ("expired",), now=now - timedelta(hours=1), ttl=timedelta(minutes=1)
            )
        )

        retained = retain_continuations(items, now=now + timedelta(minutes=3))
        self.assertEqual(len(retained), MAX_PERSISTED_CONTINUATIONS)
        self.assertEqual(tuple(item.unresolved_outcomes[0] for item in retained), ("3", "2", "1"))

    def test_rejects_nested_payload_like_metadata(self) -> None:
        with self.assertRaises(ValueError):
            PendingContinuation(("route",), constraints={"provider_payload": {}})

    def test_rejects_attempt_count_outside_the_bounded_range(self) -> None:
        with self.assertRaises(ValueError):
            PendingContinuation(("route",), attempt_count=0)
        with self.assertRaises(ValueError):
            PendingContinuation(
                ("route",), attempt_count=MAX_CONTINUATION_ATTEMPTS + 1
            )


if __name__ == "__main__":
    unittest.main()
