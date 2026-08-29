"""Batch D1 required invariants for the state-aware waypoint turns (4 and 5).

Non-test module (no ``Test*``/``test_*`` names at module level): pytest never
collects it. Owns the turn-4 (waypoint addition) and turn-5 (waypoint
removal) assertions of the D1 transcript so ``test_conversation_discovery_
waypoint`` (which owns turns 1-3 and the transcript driver) stays small and
every Batch D1 source file stays well below the 500-line limit.

Crediting order on every state-aware turn: the OFFERED tool profile is
asserted FIRST, before any state produced by a scripted tool call can be
credited -- a scripted unoffered tool can never create a false pass. Turn 5
runs only after turn 4's required gates pass; if turn 4 fails, turn 5 is
recorded blocked/not executed.
"""

from __future__ import annotations

from app.services.agent import candidate_store

from tests.conversation.conversation_discovery_fixtures import LEAK_MARKERS
from tests.conversation.conversation_discovery_waypoint_fixtures import (
    BARCLAYS_CANONICAL_NAME,
    DESTINATION_LABEL,
    FIXED_CANDIDATE_REMOVAL,
    FIXED_CANDIDATE_WAYPOINT,
    M4_SECOND_FIRST,
    M5_REMOVE_STOP,
    TURN4_EXPECTED_PROFILE,
    TURN4_FORBIDDEN,
    TURN5_EXPECTED_PROFILE,
    TURN5_FORBIDDEN,
)
from tests.conversation.conversation_discovery_waypoint_support import (
    TurnEvidence,
    _context_trip_state,
    _DiscoveryWaypointBase,
)
from tests.conversation.conversation_matrix_harness import route_cards


class _DiscoveryWaypointAssertions(_DiscoveryWaypointBase):
    """Turn-4/5 required invariants for the D1 transcript (Auto + Quick)."""

    # ------------------------------------------------------------------
    # Turn 4: add the referenced place as an intermediate waypoint
    # ------------------------------------------------------------------

    def _assert_turn4(
        self,
        *,
        scenario_id: str,
        mode: str,
        set_id: str,
        place2: dict,
        ev: TurnEvidence,
    ):
        blob = self._evidence(
            scenario_id=scenario_id,
            mode=mode,
            message=M4_SECOND_FIRST,
            ev=ev,
            extra=(
                f"set_id={set_id!r} "
                f"ordinal2_place_id={place2['place_id']!r} "
                f"ordinal2_name={place2['name']!r}"
            ),
        )
        self._assert_turn4_offer_and_reset(scenario_id, blob, ev)
        self._assert_turn4_segments(scenario_id, blob, ev, place2)
        self._assert_turn4_record_and_commit(scenario_id, blob, ev, set_id, place2)

    def _assert_turn4_offer_and_reset(self, scenario_id, blob, ev):
        assert ev.offered == TURN4_EXPECTED_PROFILE, blob
        assert not set(self._names(ev)) & set(TURN4_FORBIDDEN), f"{scenario_id} turn4 forbidden tool; {blob}"
        assert ev.before_state is not None, f"{scenario_id} turn4 before state"
        trip = _context_trip_state(ev.context) or {}
        with self.subTest(gap="reset_preserves_destination"):
            assert trip.get("destination") == DESTINATION_LABEL, f"{scenario_id} turn4 first-request context keeps destination; {blob}"
        with self.subTest(gap="reset_preserves_discovery_set"):
            assert trip.get("has_active_discovery_set") is True, f"{scenario_id} turn4 first-request context keeps discovery; {blob}"
            assert "active_discovery:" in ev.context, blob
        with self.subTest(gap="reset_preserves_waypoints"):
            assert trip.get("waypoints") == [], f"{scenario_id} turn4 pre-model waypoints; {blob}"

    def _assert_turn4_segments(self, scenario_id, blob, ev, place2):
        assert ev.trace.tool_calls[0][1]["waypoints"] == [place2["place_id"]], f"{scenario_id} turn4 prepare waypoints; {blob}"
        assert self._names(ev) == ["prepare_route_options", "present_route"], f"{scenario_id} turn4 sequence; {blob}"
        prepare = ev.mocks["prepare_single_leg"]
        assert prepare.await_count == 2, f"{scenario_id} turn4 two provider-seam segments; {blob}"
        calls = prepare.await_args_list
        first_input = calls[0].args[0]
        second_input = calls[1].args[0]
        with self.subTest(gap="segment_order"):
            assert first_input.get("origin") == "user", blob
            assert first_input.get("destination") == place2["place_id"], f"{scenario_id} segment1 destination is the opaque ordinal-2 id; {blob}"
            assert second_input.get("origin") == place2["place_id"], f"{scenario_id} segment2 origin is the same opaque ordinal-2 id; {blob}"
            assert second_input.get("destination") == DESTINATION_LABEL, f"{scenario_id} segment2 destination inherits Barclays; {blob}"
        with self.subTest(gap="stored_identity_at_waypoint_boundary"):
            self._assert_stored_place(
                calls[0].kwargs.get("resolved_destination"),
                place2,
                f"{scenario_id} segment1 destination boundary",
            )
            assert calls[0].kwargs.get("resolved_origin") is None, f"{scenario_id} segment1 origin is the user location"
            self._assert_stored_place(
                calls[1].kwargs.get("resolved_origin"),
                place2,
                f"{scenario_id} segment2 origin boundary",
            )
            assert calls[1].kwargs.get("resolved_destination") is None, f"{scenario_id} segment2 destination is the inherited label"

    def _assert_turn4_record_and_commit(self, scenario_id, blob, ev, set_id, place2):
        cards = route_cards(ev.events)
        assert len(cards) == 1, f"{scenario_id} turn4 one card; {blob}"
        assert len(ev.mocks["stored_candidate_set_ids"]) == 1, f"{scenario_id} turn4 one stored set; {blob}"
        record = candidate_store.load_candidate_set(
            ev.state["active_candidate_set_id"],
            session_id=ev.session_id,
        )
        assert record is not None, f"{scenario_id} turn4 candidate record; {blob}"
        assert record["candidate_kind"] == "multi_stop", f"{scenario_id} turn4 multi-stop candidate; {blob}"
        assert record["destination_raw"] == DESTINATION_LABEL, blob
        assert record["waypoints"] == [place2["name"]], f"{scenario_id} turn4 stored waypoint label; {blob}"
        segments = record["aggregate_segments"][0]
        assert len(segments) == 2, f"{scenario_id} turn4 two segments; {blob}"
        assert segments[0]["destination_place"]["name"] == place2["name"], f"{scenario_id} turn4 segment1 destination place; {blob}"
        assert segments[1]["destination_place"]["name"] == BARCLAYS_CANONICAL_NAME, f"{scenario_id} turn4 segment2 destination place; {blob}"
        assert (segments[0].get("dwell_minutes"), segments[0].get("dwell_source")) == (25, "default"), f"{scenario_id} turn4 server-owned dwell provenance; {blob}"
        assert "dwell_minutes" not in segments[1], f"{scenario_id} turn4 final segment has no dwell; {blob}"
        itinerary = cards[0].itinerary
        assert itinerary is not None, f"{scenario_id} turn4 itinerary; {blob}"
        waypoints = itinerary.get("waypoints") or []
        assert len(waypoints) == 1, f"{scenario_id} turn4 one waypoint; {blob}"
        assert waypoints[0].get("display_name") == place2["name"], f"{scenario_id} turn4 itinerary waypoint label; {blob}"
        assert (waypoints[0].get("dwell_minutes"), waypoints[0].get("dwell_source")) == (25, "default"), f"{scenario_id} turn4 itinerary dwell provenance; {blob}"
        assert itinerary.get("total_dwell_seconds") == 1500, f"{scenario_id} turn4 itinerary dwell total; {blob}"
        assert (itinerary.get("destination") or {}).get("name") == BARCLAYS_CANONICAL_NAME, f"{scenario_id} turn4 itinerary destination; {blob}"
        snapshots = ev.mocks.get("session_at_store") or []
        assert len(snapshots) == 1, f"{scenario_id} turn4 exactly one prepare store; {blob}"
        assert (snapshots[0]["active_trip"] or {}).get("card_id") == (ev.before_session.active_trip or {}).get("card_id"), f"{scenario_id} turn4 old card survives until replacement; {blob}"
        assert snapshots[0]["route_cards"] == list(ev.before_session.route_cards), f"{scenario_id} turn4 old cards survive until replacement; {blob}"
        assert (ev.after_session.active_trip or {}).get("card_id") == cards[0].card_id, f"{scenario_id} turn4 committed active trip card; {blob}"
        assert ev.state["waypoints"] == [place2["name"]], blob
        assert ev.state["destination"] == DESTINATION_LABEL, blob
        assert ev.state["selected_candidate_id"] == FIXED_CANDIDATE_WAYPOINT, f"{scenario_id} turn4 committed candidate; {blob}"
        assert ev.state["active_discovery_set_id"] == set_id, blob
        self._no_temp(scenario_id, ev.state)
        assert ev.events[-1].type == "done", blob
        self._assert_no_leaks(scenario_id, "turn4", ev.trace.final_text)

    # ------------------------------------------------------------------
    # Turn 5: remove the discovery waypoint and restore the intended trip
    # ------------------------------------------------------------------

    def _assert_turn5(self, *, scenario_id: str, mode: str, ev: TurnEvidence):
        blob = self._evidence(
            scenario_id=scenario_id,
            mode=mode,
            message=M5_REMOVE_STOP,
            ev=ev,
        )
        # Gate 1 (earliest): the canonical route profile must be OFFERED.
        assert ev.offered == TURN5_EXPECTED_PROFILE, blob
        names = self._names(ev)
        assert names == ["prepare_route_options", "present_route"], f"{scenario_id} turn5 sequence; {blob}"
        assert names.count("prepare_route_options") == 1, blob
        assert names.count("present_route") == 1, blob
        assert not set(names) & set(TURN5_FORBIDDEN), f"{scenario_id} turn5 forbidden tool; {blob}"
        assert "get_place_details" not in names, f"{scenario_id} turn5 removal never resolves a place; {blob}"
        assert ev.trace.tool_calls[0][1]["waypoints"] == [], f"{scenario_id} turn5 explicit empty waypoints; {blob}"
        state = ev.state
        assert state["destination"] == DESTINATION_LABEL, blob
        assert state["waypoints"] == [], f"{scenario_id} turn5 empty; {blob}"
        assert state["selected_candidate_id"] == FIXED_CANDIDATE_REMOVAL, f"{scenario_id} turn5 committed candidate; {blob}"
        # The discovery set and selected place association stays safe and
        # session-owned (never cleared by the removal).
        assert state["active_discovery_set_id"] == ev.before_state["active_discovery_set_id"], f"{scenario_id} turn5 discovery set preserved; {blob}"
        assert state["selected_place_id"] == ev.before_state["selected_place_id"], f"{scenario_id} turn5 selected place preserved; {blob}"
        # Profile/preferences/timing unchanged across the removal.
        for field in (
            "origin",
            "destination",
            "planning_mode",
            "requested_departure",
            "requested_arrival",
            "preferences",
        ):
            assert state[field] == ev.before_state[field], f"{scenario_id} turn5 keeps {field}; {blob}"
        assert ev.after_session.slots == ev.before_session.slots, f"{scenario_id} turn5 slots unchanged; {blob}"
        # One prepare, one present, one card, one committed selection, and a
        # destination-only canonical record/itinerary with no waypoint or
        # dwell residue.
        assert ev.mocks["prepare_single_leg"].await_count == 1, f"{scenario_id} turn5 one provider seam; {blob}"
        cards = route_cards(ev.events)
        assert len(cards) == 1, f"{scenario_id} turn5 one card; {blob}"
        assert len(ev.mocks["stored_candidate_set_ids"]) == 1, f"{scenario_id} turn5 one stored set; {blob}"
        record = candidate_store.load_candidate_set(
            state["active_candidate_set_id"],
            session_id=ev.session_id,
        )
        assert record is not None, f"{scenario_id} turn5 candidate record; {blob}"
        assert record["candidate_kind"] == "single_leg", f"{scenario_id} turn5 destination-only candidate; {blob}"
        assert record["destination_raw"] == DESTINATION_LABEL, blob
        assert record["waypoints"] == [], f"{scenario_id} turn5 no waypoint residue; {blob}"
        assert record["aggregate_segments"] == [], f"{scenario_id} turn5 no segment residue; {blob}"
        itinerary = cards[0].itinerary
        assert itinerary is not None, f"{scenario_id} turn5 itinerary; {blob}"
        assert itinerary.get("waypoints") == [], f"{scenario_id} turn5 itinerary no waypoints; {blob}"
        assert itinerary.get("total_dwell_seconds") == 0, f"{scenario_id} turn5 itinerary no dwell; {blob}"
        assert (ev.after_session.active_trip or {}).get("card_id") == cards[0].card_id, f"{scenario_id} turn5 committed active trip card; {blob}"
        self._no_temp(scenario_id, state)
        assert ev.events[-1].type == "done", blob
        self._assert_no_leaks(scenario_id, "turn5", ev.trace.final_text)

    # ------------------------------------------------------------------
    # Shared helpers
    # ------------------------------------------------------------------

    def _assert_no_leaks(self, scenario_id: str, tag: str, text: str) -> None:
        lowered = text.casefold()
        for marker in LEAK_MARKERS:
            assert marker not in lowered, f"{scenario_id} {tag} leak {marker}"

    def _assert_stored_place(self, place, stored: dict, label: str) -> None:
        assert place is not None, f"{label} must be resolved from the store"
        assert place.name == stored["name"], f"{label} stored name wins"
        assert place.latitude == float(stored["latitude"]), f"{label} stored latitude wins"
        assert place.longitude == float(stored["longitude"]), f"{label} stored longitude wins"
        assert place.place_id == stored["place_id"], f"{label} stored opaque identity wins"
        assert place.provider_place_id == stored["provider_place_id"], f"{label} provider identity is preserved separately"


__all__ = ("_DiscoveryWaypointAssertions",)
