"""Penn Station labels must keep the existing Manhattan endpoint."""

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from app.services.trips.location import known_place, resolve_named_place


PENN_LABELS = (
    "34 St-Penn Station",
    "34 St–Penn Station",
    "34 St Penn Station",
    "34th Penn Station",
    "34th Street-Penn Station",
)


class PennStationAliasesTests(unittest.IsolatedAsyncioTestCase):
    async def test_station_labels_bypass_address_geocoding(self):
        with patch(
            "app.services.trips.location.geo.geocode_address_with_reason",
            side_effect=AssertionError("Penn Station must not be geocoded"),
        ):
            for label in PENN_LABELS:
                with self.subTest(label=label):
                    place, error = await resolve_named_place(
                        label, SimpleNamespace(origin=None),
                        missing_location_message="Origin required",
                    )
                    self.assertIsNone(error)
                    self.assertEqual(place.name, "Penn Station")
                    self.assertEqual((place.latitude, place.longitude), (40.7506, -73.9935))

    def test_unrelated_stations_are_not_penn_aliases(self):
        for label in ("36 St", "Newark Penn Station", "34 St-Herald Sq"):
            with self.subTest(label=label):
                self.assertIsNone(known_place(label))
