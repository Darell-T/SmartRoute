"""Unit coverage for server-owned route endpoint resolution precedence."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from app.services.agent import profile, trip_state
from app.services.agent.tools.location_resolution import (
    resolve_named_place,
    resolve_named_point,
)
from app.services.agent.tools.route.route_input import merge_route_preparation_input

from tests.conversation.conversation_matrix_harness import new_session

CURRENT_LOCATION = {"lat": 40.7411, "lng": -73.9897}


class _RouteAwareGtfs:
    def get_subway_stops_with_routes(self, route_ids):
        if route_ids != {"Q"}:
            return []
        return [
            {
                "stop_id": "D40",
                "stop_name": "Church Av",
                "stop_lat": 40.6505,
                "stop_lon": -73.9629,
                "route_ids": ["B", "Q"],
            }
        ]


def _save_home_and_work(session: dict) -> None:
    profile.save_place(
        session,
        {"label": "Home", "latitude": 40.7128, "longitude": -74.0060},
        slot="home",
    )
    profile.save_place(
        session,
        {"label": "Work", "latitude": 40.7527, "longitude": -73.9772},
        slot="work",
    )


class EndpointResolutionPolicyTests(unittest.IsolatedAsyncioTestCase):
    async def test_named_point_resolves_alias_profile_station_and_geocoder(self):
        session_id, session = new_session()
        _save_home_and_work(session)
        ctx = SimpleNamespace(
            session=session,
            session_id=session_id,
            origin=dict(CURRENT_LOCATION),
            gtfs=_RouteAwareGtfs(),
        )

        alias, alias_error = await resolve_named_point(
            "MSG", ctx, missing_location_message="current location unavailable"
        )
        home, home_error = await resolve_named_point(
            "home", ctx, missing_location_message="current location unavailable"
        )
        station, station_error = await resolve_named_point(
            "Church Avenue on the Q line",
            ctx,
            missing_location_message="current location unavailable",
        )
        with patch(
            "app.services.agent.tools.location_resolution.geo.geocode_address_with_reason",
            return_value=((40.75, -73.99), None),
        ) as geocode:
            geocoded, geocode_error = await resolve_named_point(
                "A named address",
                ctx,
                missing_location_message="current location unavailable",
            )

        assert alias_error is None
        assert home_error is None
        assert station_error is None
        assert geocode_error is None
        assert home == (40.7128, -74.006)
        assert station == (40.6505, -73.9629)
        assert geocoded == (40.75, -73.99)
        assert alias != tuple(CURRENT_LOCATION.values())
        geocode.assert_called_once_with("A named address")

    async def test_named_point_rejects_missing_or_ambiguous_saved_place(self):
        session_id, session = new_session()
        session["profile"]["saved_places"] = [
            {"label": "Gym", "latitude": 40.71, "longitude": -74.00},
            {"label": "Gym", "latitude": 40.76, "longitude": -73.98},
        ]
        ctx = SimpleNamespace(
            session=session,
            session_id=session_id,
            origin=dict(CURRENT_LOCATION),
            gtfs=_RouteAwareGtfs(),
        )

        ambiguous, ambiguous_error = await resolve_named_point(
            "Gym", ctx, missing_location_message="current location unavailable"
        )
        missing, missing_error = await resolve_named_point(
            "home", ctx, missing_location_message="current location unavailable"
        )
        station, station_error = await resolve_named_point(
            "Church Avenue on the R line",
            ctx,
            missing_location_message="current location unavailable",
        )

        assert ambiguous is None
        assert ambiguous_error == "saved place reference is ambiguous"
        assert missing is None
        assert missing_error == "saved Home is unavailable"
        assert station is None
        assert station_error == "that R station could not be found"

    def test_merge_precedence_is_explicit_then_active_then_current_location(self):
        _session_id, session = new_session()
        trip_state.update_trip_state(session, origin="Home", destination="Work")
        ctx = SimpleNamespace(session=session)

        explicit = merge_route_preparation_input(
            {
                "origin": "Work",
                "destination": "Home",
                "destination_source": "current_turn",
            },
            ctx,
        )
        inherited = merge_route_preparation_input(
            {"destination_source": "accepted_trip"}, ctx
        )
        trip_state.reset_for_new_trip(session)
        current = merge_route_preparation_input(
            {
                "destination": "Barclays Center",
                "destination_source": "current_turn",
            },
            ctx,
        )

        assert (explicit["origin"], explicit["destination"]) == ("Work", "Home")
        assert (inherited["origin"], inherited["destination"]) == ("Home", "Work")
        assert (current["origin"], current["destination"]) == ("user", "Barclays Center")

    def test_current_turn_destination_never_falls_back_to_accepted_trip(self):
        _session_id, session = new_session()
        trip_state.update_trip_state(
            session,
            origin="Your location",
            destination="Konoha Yakitori Ramen and Sushi House",
        )
        ctx = SimpleNamespace(session=session)

        merged = merge_route_preparation_input(
            {"destination_source": "current_turn"}, ctx
        )

        assert merged["destination"] == ""

    def test_explicit_current_turn_destination_supersedes_accepted_trip(self):
        _session_id, session = new_session()
        trip_state.update_trip_state(
            session,
            origin="Your location",
            destination="Konoha Yakitori Ramen and Sushi House",
        )
        ctx = SimpleNamespace(session=session)

        merged = merge_route_preparation_input(
            {
                "destination": "Kyuramen",
                "destination_source": "current_turn",
                "routing_preference": "LESS_WALKING",
            },
            ctx,
        )

        assert merged["destination"] == "Kyuramen"
        assert merged["routing_preference"] == "LESS_WALKING"

    def test_merge_normalizes_canonical_rider_location_labels(self):
        _session_id, session = new_session()
        ctx = SimpleNamespace(session=session)

        for label in ("user", "Your location", "current location"):
            with self.subTest(label=label):
                merged = merge_route_preparation_input(
                    {"origin": label, "destination": "Madison Square Garden"},
                    ctx,
                )
                assert merged["origin"] == "user"

    async def test_current_location_and_saved_places_resolve_server_side(self):
        session_id, session = new_session()
        _save_home_and_work(session)
        ctx = SimpleNamespace(
            session=session,
            session_id=session_id,
            origin=dict(CURRENT_LOCATION),
        )

        current, current_error = await resolve_named_place(
            "user", ctx, missing_location_message="current location unavailable"
        )
        home, home_error = await resolve_named_place(
            "home", ctx, missing_location_message="current location unavailable"
        )
        work, work_error = await resolve_named_place(
            "work", ctx, missing_location_message="current location unavailable"
        )

        assert current_error is None
        assert current.name == "Your location"
        assert (current.latitude, current.longitude) == (40.7411, -73.9897)
        assert current.source == "user"
        assert home_error is None
        assert (home.name, home.source) == ("Home", "profile")
        assert work_error is None
        assert (work.name, work.source) == ("Work", "profile")
        assert (home.latitude, home.longitude) != (current.latitude, current.longitude)

    async def test_missing_current_location_fails_bounded_without_fabrication(self):
        session_id, session = new_session()
        ctx = SimpleNamespace(session=session, session_id=session_id, origin=None)

        place, error = await resolve_named_place(
            "user", ctx, missing_location_message="current location unavailable"
        )

        assert place is None
        assert error == "current location unavailable"

    async def test_route_qualified_station_resolves_from_gtfs_not_street_geocoding(self):
        session_id, session = new_session()
        ctx = SimpleNamespace(
            session=session,
            session_id=session_id,
            origin=dict(CURRENT_LOCATION),
            gtfs=_RouteAwareGtfs(),
        )

        place, error = await resolve_named_place(
            "Church Avenue on the Q line",
            ctx,
            missing_location_message="current location unavailable",
        )

        assert error is None
        assert (place.name, place.source) == ("Church Av", "gtfs")
        assert (place.latitude, place.longitude) == (40.6505, -73.9629)
        assert place.place_id == "D40"

    async def test_unknown_route_qualified_station_fails_closed(self):
        session_id, session = new_session()
        ctx = SimpleNamespace(
            session=session,
            session_id=session_id,
            origin=dict(CURRENT_LOCATION),
            gtfs=_RouteAwareGtfs(),
        )

        place, error = await resolve_named_place(
            "Church Avenue on the R line",
            ctx,
            missing_location_message="current location unavailable",
        )

        assert place is None
        assert error == "that R station could not be found"

    async def test_missing_home_or_work_never_falls_through_to_geocoding(self):
        session_id, session = new_session()
        ctx = SimpleNamespace(
            session=session,
            session_id=session_id,
            origin=dict(CURRENT_LOCATION),
        )
        for label in ("home", "work"):
            with self.subTest(label=label):
                place, error = await resolve_named_place(
                    label,
                    ctx,
                    missing_location_message="current location unavailable",
                )
                assert place is None
                assert error == f"saved {label.title()} is unavailable"

    async def test_ambiguous_saved_label_requires_clarification(self):
        session_id, session = new_session()
        session["profile"]["saved_places"] = [
            {"label": "Gym", "latitude": 40.71, "longitude": -74.00},
            {"label": "Gym", "latitude": 40.76, "longitude": -73.98},
        ]
        ctx = SimpleNamespace(
            session=session,
            session_id=session_id,
            origin=dict(CURRENT_LOCATION),
        )

        place, error = await resolve_named_place(
            "Gym", ctx, missing_location_message="current location unavailable"
        )

        assert place is None
        assert error == "saved place reference is ambiguous"

    async def test_home_slot_name_remains_authoritative_when_labels_collide(self):
        session_id, session = new_session()
        profile.save_place(
            session,
            {"label": "Apartment", "latitude": 40.71, "longitude": -74.00},
            slot="home",
        )
        session["profile"]["saved_places"] = [
            {"label": "Home", "latitude": 40.76, "longitude": -73.98}
        ]
        ctx = SimpleNamespace(
            session=session,
            session_id=session_id,
            origin=dict(CURRENT_LOCATION),
        )

        place, error = await resolve_named_place(
            "home", ctx, missing_location_message="current location unavailable"
        )

        assert error is None
        assert place.name == "Apartment"
        assert (place.latitude, place.longitude) == (40.71, -74.0)


if __name__ == "__main__":
    unittest.main()
