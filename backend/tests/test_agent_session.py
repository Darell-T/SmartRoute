"""Layer-1 tests for the conversational agent's session store
(app/services/agent/session.py). Uses the in-memory cache.py fallback
(REDIS_URL unset in the test environment) -- session.py is cache-backend
agnostic, so this exercises the same code paths Redis would."""

import unittest

from app.services.agent import session as agent_session
from app.utils import cache


class SessionLifecycleTests(unittest.TestCase):
    def setUp(self):
        cache._mem.clear()

    def test_new_session_has_expected_shape(self):
        session_id, session = agent_session.new_session()
        self.assertTrue(session_id)
        self.assertEqual(session["v"], agent_session.SCHEMA_VERSION)
        self.assertEqual(session["turn_seq"], 0)
        self.assertEqual(session["slots"], {})
        self.assertEqual(session["route_cards"], [])
        self.assertEqual(session["history"], [])

    def test_new_session_ids_are_unique_and_unguessable_length(self):
        ids = {agent_session.new_session_id() for _ in range(20)}
        self.assertEqual(len(ids), 20)
        for session_id in ids:
            self.assertGreaterEqual(len(session_id), 24)

    def test_save_then_load_round_trips(self):
        session_id, session = agent_session.new_session()
        agent_session.append_history(session, "user", "heading to Costco")
        agent_session.save_session(session_id, session)

        loaded = agent_session.load_session(session_id)
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded["history"][0]["text"], "heading to Costco")

    def test_load_missing_session_returns_none(self):
        self.assertIsNone(agent_session.load_session("does-not-exist"))

    def test_load_corrupt_blob_returns_none(self):
        session_id = agent_session.new_session_id()
        cache.cache_set(agent_session._session_key(session_id), "not json", 60)
        self.assertIsNone(agent_session.load_session(session_id))

    def test_load_wrong_schema_version_returns_none(self):
        session_id = agent_session.new_session_id()
        cache.cache_set(agent_session._session_key(session_id), '{"v": 999}', 60)
        self.assertIsNone(agent_session.load_session(session_id))

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
        self.assertGreater(refreshed_expiry, first_expiry - 1000)

    def test_next_turn_id_increments(self):
        _session_id, session = agent_session.new_session()
        self.assertEqual(agent_session.next_turn_id(session), "t1")
        self.assertEqual(agent_session.next_turn_id(session), "t2")


class SessionCapTests(unittest.TestCase):
    def setUp(self):
        cache._mem.clear()

    def test_history_capped_at_12_messages(self):
        session_id, session = agent_session.new_session()
        for i in range(20):
            agent_session.append_history(session, "user", f"message {i}")
        agent_session.save_session(session_id, session)

        loaded = agent_session.load_session(session_id)
        self.assertLessEqual(len(loaded["history"]), agent_session.MAX_HISTORY_MESSAGES)
        # The most recent messages survive, not the oldest.
        self.assertEqual(loaded["history"][-1]["text"], "message 19")

    def test_route_cards_capped_at_8(self):
        session_id, session = agent_session.new_session()
        cards = [{"card_id": f"rc_{i:08x}", "role": "alternative", "lines": ["Q"], "eta_minutes": i} for i in range(15)]
        agent_session.add_route_cards(session, cards)
        agent_session.save_session(session_id, session)

        loaded = agent_session.load_session(session_id)
        self.assertLessEqual(len(loaded["route_cards"]), agent_session.MAX_ROUTE_CARDS)
        # Oldest cards were dropped first -- the tail (highest index) survives.
        self.assertEqual(loaded["route_cards"][-1]["card_id"], "rc_0000000e")

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
        self.assertLess(blob_size, agent_session.MAX_SESSION_BYTES * 2)  # sanity, not a byte-exact check
        self.assertLess(len(loaded["route_cards"]), len(big_cards))
        # History (small) survives the oversized-card trim.
        self.assertEqual(loaded["history"][-1]["text"], "hello")


class SlotExtractionTests(unittest.TestCase):
    def test_extracts_origin_destination_from_plan_trip_call(self):
        _session_id, session = agent_session.new_session()
        agent_session.extract_slots(session, [("plan_trip", {"origin": "user", "destination": "Costco"})])
        self.assertEqual(session["slots"]["origin"], "user")
        self.assertEqual(session["slots"]["destination"], "Costco")

    def test_extracts_exclude_modes_into_constraints(self):
        _session_id, session = agent_session.new_session()
        agent_session.extract_slots(
            session, [("plan_trip", {"origin": "user", "destination": "Costco", "exclude_modes": ["BUS"]})]
        )
        self.assertEqual(session["slots"]["constraints"]["exclude_modes"], ["BUS"])

    def test_extracts_departure_time_as_time_anchor(self):
        _session_id, session = agent_session.new_session()
        agent_session.extract_slots(
            session,
            [("plan_trip", {"origin": "user", "destination": "MSG", "departure_time": "2026-07-16T22:00:00-04:00"})],
        )
        self.assertEqual(session["slots"]["time_anchor"], "2026-07-16T22:00:00-04:00")

    def test_non_plan_trip_calls_do_not_touch_slots(self):
        _session_id, session = agent_session.new_session()
        agent_session.extract_slots(session, [("transit_snapshot", {"near": "user"})])
        self.assertEqual(session["slots"], {})

    def test_later_call_overwrites_earlier_slot_in_same_turn(self):
        _session_id, session = agent_session.new_session()
        agent_session.extract_slots(
            session,
            [
                ("plan_trip", {"origin": "user", "destination": "Costco"}),
                ("plan_trip", {"origin": "user", "destination": "MSG"}),
            ],
        )
        self.assertEqual(session["slots"]["destination"], "MSG")


if __name__ == "__main__":
    unittest.main()
