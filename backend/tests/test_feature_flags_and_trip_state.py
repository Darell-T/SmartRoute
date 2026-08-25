"""Trip state and candidate/discovery store unit tests."""

from __future__ import annotations

import unittest
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

from app.services.agent import (
    candidate_store,
    discovery_store,
    profile,
    session as session_module,
    trip_state,
)
from app.services.agent.tools.route.route_input import merge_route_preparation_input


class TripStateTests(unittest.TestCase):
    def test_saving_same_place_replaces_existing_identity(self):
        session: dict = {}
        profile.save_place(
            session,
            {
                "label": "Old label",
                "latitude": 40.71,
                "longitude": -74.0,
                "place_id": "place-1",
            },
        )
        profile.save_place(
            session,
            {
                "label": "New label",
                "latitude": 40.72,
                "longitude": -73.99,
                "place_id": "place-1",
            },
        )

        saved = profile.get_profile(session)["saved_places"]
        self.assertEqual(len(saved), 1)
        self.assertEqual(saved[0]["label"], "New label")

    def test_preference_and_destination_updates(self):
        session: dict = {}
        trip_state.set_destination(session, "Barclays Center")
        trip_state.apply_preference_patch(session, {"avoid_stairs": True})
        trip_state.add_waypoint_before_destination(session, "Whole Foods")
        state = trip_state.get_trip_state(session)
        self.assertEqual(state["destination"], "Barclays Center")
        self.assertTrue(state["preferences"]["avoid_stairs"])
        self.assertEqual(state["waypoints"], ["Whole Foods"])

    def test_state_normalizes_waypoint_and_timing_bounds(self):
        state: dict = {}
        trip_state.replace_waypoints(
            state,
            ["valid", 2, "x" * 161, "second", "third", "fourth"],
        )
        trip_state.set_planning_time(
            state,
            planning_mode="depart_at",
            requested_departure="tomorrow morning",
        )
        normalized = trip_state.get_trip_state(state)
        self.assertEqual(normalized["waypoints"], ["valid", "second", "third"])
        self.assertEqual(normalized["planning_mode"], "leave_now")
        self.assertIsNone(normalized["requested_departure"])

    def test_unrelated_question_does_not_require_clearing(self):
        session: dict = {}
        trip_state.set_destination(session, "Penn Station")
        # Reading state alone must not erase destination.
        again = trip_state.get_trip_state(session)
        self.assertEqual(again["destination"], "Penn Station")

    def test_new_trip_reset_preserves_profile_and_unrelated_conversation(self):
        _session_id, state = session_module.new_session()
        profile.save_place(
            state,
            {"label": "Home", "latitude": 40.71, "longitude": -74.0},
            slot="home",
        )
        profile.update_preferences(
            state,
            {"avoid_stairs": True, "walking_tolerance_minutes": 12},
        )
        state["history"] = [{"role": "user", "text": "keep this"}]
        state["slots"]["origin"] = "Old origin"
        state["slots"]["unrelated"] = "retain"
        trip_state.update_trip_state(
            state,
            origin="Home",
            destination="Work",
            waypoints=["Coffee"],
            planning_mode="arrive_by",
            requested_arrival="2026-08-08T09:00:00-04:00",
            active_candidate_set_id="cs_active",
            selected_candidate_id="cd_active",
        )
        trip_state.bind_temporary_candidate_set(
            state,
            "cs_temp",
            base_candidate_set_id="cs_active",
        )

        session_module.reset_for_new_trip(state)

        reset = trip_state.get_trip_state(state)
        self.assertIsNone(reset["origin"])
        self.assertIsNone(reset["destination"])
        self.assertEqual(reset["waypoints"], [])
        self.assertIsNone(reset["requested_arrival"])
        self.assertIsNone(reset["active_candidate_set_id"])
        self.assertIsNone(reset["temporary_candidate_set_id"])
        self.assertTrue(reset["preferences"]["avoid_stairs"])
        self.assertEqual(reset["preferences"]["walking_tolerance_minutes"], 12)
        self.assertEqual(profile.profile_place(state, "home")["label"], "Home")
        self.assertEqual(state["history"][0]["text"], "keep this")
        self.assertEqual(state["slots"]["unrelated"], "retain")
        self.assertNotIn("origin", state["slots"])
        self.assertEqual(state["route_cards"], [])

    def test_explicit_false_route_preferences_override_saved_true(self):
        _session_id, state = session_module.new_session()
        profile.update_preferences(
            state,
            {
                "avoid_stairs": True,
                "avoid_crowds": True,
                "accessibility_required": True,
            },
        )
        ctx = SimpleNamespace(session=state)
        merged = merge_route_preparation_input(
            {
                "destination": "Work",
                "avoid_stairs": False,
                "avoid_crowds": False,
                "accessibility_required": False,
            },
            ctx,
        )
        self.assertFalse(merged["avoid_stairs"])
        self.assertFalse(merged["avoid_crowds"])
        self.assertFalse(merged["accessibility_required"])
        hypothetical = merge_route_preparation_input(
            {
                "destination": "Airport",
                "avoid_stairs": False,
                "avoid_crowds": False,
                "accessibility_required": False,
                "what_if": True,
            },
            ctx,
        )
        self.assertFalse(hypothetical["avoid_stairs"])
        self.assertFalse(hypothetical["avoid_crowds"])
        self.assertFalse(hypothetical["accessibility_required"])

    def test_explicit_route_preference_correction_persists_exclusively(self):
        _session_id, state = session_module.new_session()
        ctx = SimpleNamespace(session=state)

        less_walking = merge_route_preparation_input(
            {
                "destination": "First destination",
                "routing_preference": "LESS_WALKING",
            },
            ctx,
        )
        self.assertEqual(less_walking["routing_preference"], "LESS_WALKING")
        preferences = trip_state.get_trip_state(state)["preferences"]
        self.assertEqual(preferences["walking_preference"], "less_walking")
        self.assertFalse(preferences["prefer_fewer_transfers"])

        fewer_transfers = merge_route_preparation_input(
            {
                "destination": "Second destination",
                "routing_preference": "FEWER_TRANSFERS",
            },
            ctx,
        )
        self.assertEqual(fewer_transfers["routing_preference"], "FEWER_TRANSFERS")
        self.assertEqual(
            fewer_transfers["routing_preference_source"],
            "current_turn",
        )
        preferences = trip_state.get_trip_state(state)["preferences"]
        self.assertEqual(preferences["walking_preference"], "any")
        self.assertTrue(preferences["prefer_fewer_transfers"])

        next_route = merge_route_preparation_input(
            {"destination": "Third destination"},
            ctx,
        )
        self.assertEqual(next_route["routing_preference"], "FEWER_TRANSFERS")
        self.assertEqual(
            next_route["routing_preference_source"],
            "persisted_rider",
        )

    def test_what_if_tool_finalization_does_not_overwrite_active_slots(self):
        _session_id, state = session_module.new_session()
        state["slots"] = {
            "origin": "Home",
            "destination": "Work",
            "time_anchor": "2026-08-08T09:00:00-04:00",
        }
        trip_state.update_trip_state(
            state,
            origin="Home",
            destination="Work",
            waypoints=["Coffee"],
            requested_departure="2026-08-08T09:00:00-04:00",
            active_candidate_set_id="cs_active",
            selected_candidate_id="cd_active",
        )
        session_module.extract_slots(
            state,
            [
                (
                    "prepare_route_options",
                    {
                        "origin": "Home",
                        "destination": "Airport",
                        "waypoints": ["Terminal"],
                        "departure_time": "2026-08-08T10:00:00-04:00",
                        "what_if": True,
                    },
                )
            ],
        )
        current = trip_state.get_trip_state(state)
        self.assertEqual(state["slots"]["destination"], "Work")
        self.assertEqual(current["destination"], "Work")
        self.assertEqual(current["waypoints"], ["Coffee"])
        self.assertEqual(current["selected_candidate_id"], "cd_active")

    def test_what_if_can_be_discarded_or_committed(self):
        _session_id, state = session_module.new_session()
        trip_state.update_trip_state(
            state,
            origin="Home",
            destination="Work",
            active_candidate_set_id="cs_active",
            selected_candidate_id="cd_active",
        )
        trip_state.bind_temporary_candidate_set(
            state,
            "cs_temp",
            base_candidate_set_id="cs_active",
        )
        trip_state.bind_temporary_selected_candidate(state, "cd_temp")
        trip_state.discard_scenario(state)
        discarded = trip_state.get_trip_state(state)
        self.assertEqual(discarded["active_candidate_set_id"], "cs_active")
        self.assertEqual(discarded["selected_candidate_id"], "cd_active")
        self.assertIsNone(discarded["temporary_candidate_set_id"])

        trip_state.bind_temporary_candidate_set(state, "cs_temp")
        trip_state.bind_temporary_selected_candidate(state, "cd_temp")
        trip_state.commit_scenario(
            state,
            candidate_set_id="cs_temp",
            candidate_id="cd_temp",
            tool_input={
                "origin": "Home",
                "destination": "Airport",
                "waypoints": ["Coffee"],
                "arrival_by": "2026-08-08T10:00:00-04:00",
            },
        )
        committed = trip_state.get_trip_state(state)
        self.assertEqual(committed["active_candidate_set_id"], "cs_temp")
        self.assertEqual(committed["selected_candidate_id"], "cd_temp")
        self.assertEqual(committed["destination"], "Airport")
        self.assertEqual(committed["waypoints"], ["Coffee"])
        self.assertIsNone(committed["temporary_candidate_set_id"])

    def test_what_if_route_exclusion_stays_temporary_until_explicit_commit(self):
        _session_id, state = session_module.new_session()
        state["slots"] = {"constraints": {"excluded_route_ids": ["R"]}}
        trip_state.update_trip_state(
            state,
            origin="Home",
            destination="Work",
            active_candidate_set_id="cs_active",
            selected_candidate_id="cd_active",
        )
        # A what-if preparation must not persist its temporary exclusion.
        session_module.extract_slots(
            state,
            [
                (
                    "prepare_route_options",
                    {
                        "origin": "Home",
                        "destination": "Airport",
                        "excluded_route_ids": ["Q"],
                        "what_if": True,
                    },
                )
            ],
        )
        current = trip_state.get_trip_state(state)
        self.assertEqual(current["active_candidate_set_id"], "cs_active")
        self.assertEqual(current["selected_candidate_id"], "cd_active")
        self.assertEqual(state["slots"]["constraints"]["excluded_route_ids"], ["R"])

        # Discarding the temporary scenario leaves the active constraint set
        # and candidate selection unchanged.
        trip_state.bind_temporary_candidate_set(state, "cs_temp")
        trip_state.bind_temporary_selected_candidate(state, "cd_temp")
        trip_state.discard_scenario(state)
        discarded = trip_state.get_trip_state(state)
        self.assertEqual(discarded["active_candidate_set_id"], "cs_active")
        self.assertEqual(discarded["selected_candidate_id"], "cd_active")
        self.assertEqual(state["slots"]["constraints"]["excluded_route_ids"], ["R"])

        # Explicitly committing the scenario activates its route exclusion.
        trip_state.bind_temporary_candidate_set(state, "cs_temp")
        trip_state.commit_scenario(
            state,
            candidate_set_id="cs_temp",
            candidate_id="cd_temp",
            tool_input={
                "origin": "Home",
                "destination": "Airport",
                "excluded_route_ids": ["R", "q"],
            },
        )
        committed = trip_state.get_trip_state(state)
        self.assertEqual(committed["active_candidate_set_id"], "cs_temp")
        self.assertEqual(committed["selected_candidate_id"], "cd_temp")
        self.assertEqual(
            sorted(state["slots"]["constraints"]["excluded_route_ids"]),
            ["Q", "R"],
        )

    def test_active_route_exclusion_persists_normalized_from_actual_tool_calls(self):
        _session_id, state = session_module.new_session()
        session_module.extract_slots(
            state,
            [
                (
                    "prepare_route_options",
                    {
                        "origin": "Home",
                        "destination": "Airport",
                        "excluded_route_ids": [" q ", "Q", "B35", "", "M15"],
                    },
                )
            ],
        )
        self.assertEqual(
            state["slots"]["constraints"]["excluded_route_ids"],
            ["Q", "B35", "M15"],
        )


class CandidateStoreTests(unittest.TestCase):
    def test_session_scoping_and_expiry_guards(self):
        set_id = candidate_store.store_candidate_set(
            session_id="sess-a",
            payload={
                "candidates": [{"candidate_id": "cd_test", "index": 0}],
                "parsed_routes": [[{"type": "WALK"}]],
                "scored": [{"index": 0, "score": 1, "total_minutes": 10, "transfers": 0}],
            },
            ttl_seconds=120,
        )
        ok = candidate_store.load_candidate_set(set_id, session_id="sess-a")
        self.assertIsNotNone(ok)
        self.assertIsNone(candidate_store.load_candidate_set(set_id, session_id="sess-b"))
        record, entry, error = candidate_store.get_candidate(
            set_id, "invented", session_id="sess-a"
        )
        self.assertIsNotNone(record)
        self.assertIsNone(entry)
        self.assertIn("unknown", error or "")

    def test_duplicate_presentation_rejected(self):
        set_id = candidate_store.store_candidate_set(
            session_id="sess-a",
            payload={
                "candidates": [{"candidate_id": "cd_one", "index": 0}],
                "parsed_routes": [[]],
            },
            ttl_seconds=120,
        )
        self.assertIsNone(
            candidate_store.mark_presented(set_id, "cd_one", session_id="sess-a")
        )
        err = candidate_store.mark_presented(set_id, "cd_one", session_id="sess-a")
        self.assertIn("already presented", err or "")

    def test_concurrent_presentation_reserves_exactly_once(self):
        set_id = candidate_store.store_candidate_set(
            session_id="sess-concurrent",
            payload={"candidates": [{"candidate_id": "cd_once", "index": 0}]},
            ttl_seconds=120,
        )
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(
                executor.map(
                    lambda _value: candidate_store.mark_presented(
                        set_id,
                        "cd_once",
                        session_id="sess-concurrent",
                    ),
                    (1, 2),
                )
            )
        self.assertEqual(results.count(None), 1)
        self.assertEqual(sum("already presented" in str(value) for value in results), 1)


class DiscoveryStoreTests(unittest.TestCase):
    def test_ordinal_and_cross_session(self):
        set_id = discovery_store.store_discovery_set(
            session_id="sess-a",
            places=[{"name": "A Pizza"}, {"name": "B Pizza"}],
            query="pizza",
        )
        place, error = discovery_store.resolve_place_reference(
            session_id="sess-a",
            discovery_set_id=set_id,
            ordinal=2,
        )
        self.assertIsNone(error)
        self.assertEqual(place["name"], "B Pizza")
        place_b, error_b = discovery_store.resolve_place_reference(
            session_id="sess-b",
            discovery_set_id=set_id,
            ordinal=2,
        )
        self.assertIsNone(place_b)
        self.assertIsNotNone(error_b)


if __name__ == "__main__":
    unittest.main()
