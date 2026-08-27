"""Layer-1 tests for the conversational agent's session store
(app/services/agent/session.py). Uses the in-memory cache.py fallback
(REDIS_URL unset in the test environment) -- session.py is cache-backend
agnostic, so this exercises the same code paths Redis would."""

import time
import unittest

from app.services import cache
from app.services.agent import session as agent_session


class SessionLifecycleTests(unittest.TestCase):
    def setUp(self):
        cache._mem.clear()

    def test_new_session_has_expected_shape(self):
        session_id, session = agent_session.new_session()
        assert session_id
        assert session["v"] == agent_session.SCHEMA_VERSION
        assert session["turn_seq"] == 0
        assert session["current_location"] is None
        assert session["slots"] == {}
        assert session["route_cards"] == []
        assert session["history"] == []

    def test_new_session_ids_are_unique_and_unguessable_length(self):
        ids = {agent_session.new_session_id() for _ in range(20)}
        assert len(ids) == 20
        for session_id in ids:
            assert len(session_id) >= 24

    def test_save_then_load_round_trips(self):
        session_id, session = agent_session.new_session()
        agent_session.append_history(session, "user", "heading to Costco")
        agent_session.save_session(session_id, session)

        loaded = agent_session.load_session(session_id)
        assert loaded is not None
        assert loaded["history"][0]["text"] == "heading to Costco"

    def test_load_missing_session_returns_none(self):
        assert agent_session.load_session("does-not-exist") is None

    def test_load_corrupt_blob_returns_none(self):
        session_id = agent_session.new_session_id()
        cache.cache_set(agent_session._session_key(session_id), "not json", 60)
        assert agent_session.load_session(session_id) is None

    def test_load_wrong_schema_version_returns_none(self):
        session_id = agent_session.new_session_id()
        cache.cache_set(agent_session._session_key(session_id), '{"v": 999}', 60)
        assert agent_session.load_session(session_id) is None

    def test_save_refreshes_ttl(self):
        session_id, session = agent_session.new_session()
        agent_session.save_session(session_id, session)
        first_expiry = cache._mem[agent_session._session_key(session_id)][1]

        # Force a smaller remembered expiry, then save again and confirm the
        # TTL was refreshed back up to the full window rather than left alone.
        key = agent_session._session_key(session_id)
        value, _ = cache._mem[key]
        cache._mem[key] = (value, first_expiry - 1000)
        agent_session.save_session(session_id, session)
        refreshed_expiry = cache._mem[key][1]
        assert refreshed_expiry > first_expiry - 1000

    def test_save_without_refresh_preserves_existing_expiry(self):
        session_id, session = agent_session.new_session()
        agent_session.save_session(session_id, session)

        core_key = agent_session._session_key(session_id)
        transcript_key = agent_session._transcript_key(session_id)
        preserved_expiry = time.monotonic() + 100
        for key in (core_key, transcript_key):
            value, _expiry = cache._mem[key]
            cache._mem[key] = (value, preserved_expiry)

        session["history"].append({"role": "assistant", "text": "failed turn"})
        agent_session.save_session(session_id, session, refresh_ttl=False)

        assert cache._mem[core_key][1] == preserved_expiry
        assert cache._mem[transcript_key][1] == preserved_expiry
        assert agent_session.load_session(session_id)["history"][-1]["text"] == "failed turn"

    def test_next_turn_id_increments(self):
        _session_id, session = agent_session.new_session()
        assert agent_session.next_turn_id(session) == "t1"
        assert agent_session.next_turn_id(session) == "t2"

    def test_current_location_reuses_and_replaces_valid_device_fixes(self):
        _session_id, session = agent_session.new_session()
        first = {"lat": 40.6494, "lng": -73.9631}
        newer = {"lat": 40.6502, "lng": -73.9496}

        assert agent_session.update_current_location(session, first) == first
        assert agent_session.update_current_location(session, None) == first
        assert agent_session.update_current_location(session, newer) == newer
        assert agent_session.current_location(session) == newer

    def test_invalid_location_never_erases_a_valid_session_fix(self):
        _session_id, session = agent_session.new_session()
        valid = {"lat": 40.6494, "lng": -73.9631}
        agent_session.update_current_location(session, valid)

        assert agent_session.update_current_location(session, {"lat": float("nan"), "lng": -73.9}) == valid
        assert agent_session.update_current_location(session, {"lat": 35.0, "lng": -100.0}) == valid


class SessionCapTests(unittest.TestCase):
    def setUp(self):
        cache._mem.clear()

    def test_history_capped_at_12_messages(self):
        session_id, session = agent_session.new_session()
        for i in range(20):
            agent_session.append_history(session, "user", f"message {i}")
        agent_session.save_session(session_id, session)

        loaded = agent_session.load_session(session_id)
        assert len(loaded["history"]) <= agent_session.MAX_HISTORY_MESSAGES
        # The most recent messages survive, not the oldest.
        assert loaded["history"][-1]["text"] == "message 19"

    def test_route_cards_capped_at_8(self):
        session_id, session = agent_session.new_session()
        cards = [{"card_id": f"rc_{i:08x}", "role": "alternative", "lines": ["Q"], "eta_minutes": i} for i in range(15)]
        agent_session.add_route_cards(session, cards)
        agent_session.save_session(session_id, session)

        loaded = agent_session.load_session(session_id)
        assert len(loaded["route_cards"]) <= agent_session.MAX_ROUTE_CARDS
        # Oldest cards were dropped first -- the tail (highest index) survives.
        assert loaded["route_cards"][-1]["card_id"] == "rc_0000000e"

    def test_total_session_size_capped_at_64kb_drops_oldest_cards_first(self):
        session_id, session = agent_session.new_session()
        # A handful of route cards each padded to be individually large, so
        # the 64KB global cap trips before the 8-card cap does.
        big_cards = [
            {"card_id": f"rc_{i:08x}", "role": "alternative", "lines": ["Q"], "eta_minutes": i, "pad": "x" * 20000}
            for i in range(4)
        ]
        agent_session.add_route_cards(session, big_cards)
        agent_session.append_history(session, "user", "hello")
        agent_session.save_session(session_id, session)

        loaded = agent_session.load_session(session_id)
        blob_size = len(str(loaded).encode("utf-8"))
        assert blob_size < agent_session.MAX_SESSION_BYTES * 2  # sanity, not a byte-exact check
        assert len(loaded["route_cards"]) < len(big_cards)
        # History (small) survives the oversized-card trim.
        assert loaded["history"][-1]["text"] == "hello"


class SlotExtractionTests(unittest.TestCase):
    def test_extracts_origin_destination_from_route_preparation(self):
        _session_id, session = agent_session.new_session()
        agent_session.extract_slots(session, [("prepare_route_options", {"origin": "user", "destination": "Costco"})])
        assert session["slots"]["origin"] == "user"
        assert session["slots"]["destination"] == "Costco"

    def test_extracts_exclude_modes_into_constraints(self):
        _session_id, session = agent_session.new_session()
        agent_session.extract_slots(
            session, [("prepare_route_options", {"origin": "user", "destination": "Costco", "exclude_modes": ["BUS"]})]
        )
        assert session["slots"]["constraints"]["exclude_modes"] == ["BUS"]

    def test_extracts_departure_time_as_time_anchor(self):
        _session_id, session = agent_session.new_session()
        agent_session.extract_slots(
            session,
            [("prepare_route_options", {"origin": "user", "destination": "MSG", "departure_time": "2026-07-16T22:00:00-04:00"})],
        )
        assert session["slots"]["time_anchor"] == "2026-07-16T22:00:00-04:00"

    def test_non_route_preparation_calls_do_not_touch_slots(self):
        _session_id, session = agent_session.new_session()
        agent_session.extract_slots(session, [("transit_snapshot", {"near": "user"})])
        assert session["slots"] == {}

    def test_later_call_overwrites_earlier_slot_in_same_turn(self):
        _session_id, session = agent_session.new_session()
        agent_session.extract_slots(
            session,
            [
                ("prepare_route_options", {"origin": "user", "destination": "Costco"}),
                ("prepare_route_options", {"origin": "user", "destination": "MSG"}),
            ],
        )
        assert session["slots"]["destination"] == "MSG"


if __name__ == "__main__":
    unittest.main()
