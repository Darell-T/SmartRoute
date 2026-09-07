from __future__ import annotations

import json
import unittest
from datetime import UTC, datetime, timedelta

from app.services import cache
from app.services.agent import session as session_module


class SessionPendingContinuationTests(unittest.TestCase):
    def setUp(self) -> None:
        cache._mem.clear()

    def test_new_session_and_save_restore_include_metadata_only_continuations(
        self,
    ) -> None:
        now = datetime.now(UTC)
        session_id, session = session_module.new_session()
        assert session["pending_continuations"] == []
        item = session_module.PendingContinuation.create(
            ("route",),
            missing_fields=("destination",),
            references=("place_1",),
            now=now,
        )
        session_module.add_pending_continuation(session, item, now=now)
        session_module.save_session(session_id, session)

        loaded = session_module.load_session(session_id)
        assert loaded is not None
        active = session_module.get_pending_continuations(loaded, now=now)
        assert active == (item,)
        raw = json.loads(cache.cache_get(session_module._session_key(session_id)))
        assert raw["pending_continuations"][0]["unresolved_outcomes"] == ["route"]
        assert "provider_payload" not in raw["pending_continuations"][0]

    def test_expired_continuations_are_pruned_on_read(self) -> None:
        now = datetime(2026, 8, 15, 12, tzinfo=UTC)
        session = session_module.new_session()[1]
        expired = session_module.PendingContinuation.create(
            ("old",), now=now - timedelta(hours=1), ttl=timedelta(minutes=1)
        )
        active = session_module.PendingContinuation.create(("new",), now=now)
        session["pending_continuations"] = [expired.to_dict(), active.to_dict()]

        assert session_module.get_pending_continuations(session, now=now) == (active,)
        assert session["pending_continuations"] == [active.to_dict()]

    def test_add_retains_newest_three(self) -> None:
        base = datetime(2026, 8, 15, 12, tzinfo=UTC)
        session = session_module.new_session()[1]
        for index in range(4):
            item = session_module.PendingContinuation.create(
                (str(index),), now=base + timedelta(minutes=index)
            )
            kept = session_module.add_pending_continuation(
                session, item, now=base + timedelta(minutes=index)
            )

        assert len(kept) == session_module.MAX_PERSISTED_CONTINUATIONS
        assert tuple(item.unresolved_outcomes[0] for item in kept) == ("3", "2", "1")

    def test_older_session_migrates_missing_or_malformed_field(self) -> None:
        session_id = session_module.new_session_id()
        cache.cache_set(
            session_module._session_key(session_id),
            json.dumps({"v": 1, "history": [], "pending_continuations": {}}),
            60,
        )

        loaded = session_module.load_session(session_id)
        assert loaded is not None
        assert loaded["v"] == session_module.SCHEMA_VERSION
        assert loaded["pending_continuations"] == []

    def test_new_trip_clears_all_pending_continuations(self) -> None:
        session = session_module.new_session()[1]
        item = session_module.PendingContinuation.create(("route",))
        session_module.add_pending_continuation(session, item)
        session_module.reset_for_new_trip(session)

        assert session["pending_continuations"] == []
        assert session_module.get_pending_continuations(session) == ()

    def test_later_success_resolves_only_matching_outcomes(self) -> None:
        session = session_module.new_session()[1]
        item = session_module.PendingContinuation.create(
            ("place_recommendation", "route")
        )
        session_module.add_pending_continuation(session, item)

        session_module.resolve_pending_continuations(
            session,
            {"place_recommendation"},
        )

        active = session_module.get_pending_continuations(session)
        assert len(active) == 1
        assert active[0].unresolved_outcomes == ("route",)
        assert active[0].attempt_count == 1

    def test_same_continuation_is_removed_after_three_attempts(self) -> None:
        now = datetime(2026, 8, 15, 12, tzinfo=UTC)
        session = session_module.new_session()[1]

        for expected in range(1, session_module.MAX_CONTINUATION_ATTEMPTS + 1):
            kept = session_module.add_pending_continuation(
                session,
                session_module.PendingContinuation.create(("route",), now=now),
                now=now,
            )
            assert len(kept) == 1
            assert kept[0].attempt_count == expected
            assert kept[0].created_at == now

        kept = session_module.add_pending_continuation(
            session,
            session_module.PendingContinuation.create(("route",), now=now),
            now=now,
        )
        assert kept == ()

    def test_unrelated_continuation_does_not_consume_existing_attempts(self) -> None:
        now = datetime(2026, 8, 15, 12, tzinfo=UTC)
        session = session_module.new_session()[1]
        session_module.add_pending_continuation(
            session,
            session_module.PendingContinuation.create(("route",), now=now),
            now=now,
        )
        kept = session_module.add_pending_continuation(
            session,
            session_module.PendingContinuation.create(("service_status",), now=now),
            now=now,
        )

        assert {item.attempt_count for item in kept} == {1}
        assert {item.unresolved_outcomes for item in kept} == {
            ("route",),
            ("service_status",),
        }

    def test_old_record_without_attempt_count_migrates_to_first_attempt(self) -> None:
        now = datetime(2026, 8, 15, 12, tzinfo=UTC)
        session = session_module.new_session()[1]
        payload = session_module.PendingContinuation.create(
            ("route",), now=now
        ).to_dict()
        payload.pop("attempt_count")
        session["pending_continuations"] = [payload]

        active = session_module.get_pending_continuations(session, now=now)

        assert active[0].attempt_count == 1


if __name__ == "__main__":
    unittest.main()
