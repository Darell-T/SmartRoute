"""Canonical discovery-scope geography after the model chooses a tool."""

from __future__ import annotations

import unittest

from app.services.agent.tools.places import geography as geo


class ConversationalGeographyTests(unittest.TestCase):
    def test_the_city_is_manhattan_not_rider_borough(self):
        scope, error = geo.normalize_scope(
            {"kind": "named_area", "values": ["the city"]}
        )
        assert error is None
        assert scope == {"kind": "boroughs", "values": ["Manhattan"]}

    def test_nyc_is_five_borough_scope(self):
        scope, error = geo.normalize_scope({"kind": "nyc", "values": []})
        assert error is None
        assert scope == {"kind": "nyc", "values": []}

    def test_nyc_scope_accepts_redundant_nyc_alias(self):
        scope, error = geo.normalize_scope(
            {"kind": "nyc", "values": ["NYC"]}
        )

        assert error is None
        assert scope == {"kind": "nyc", "values": []}

    def test_explicit_boroughs_are_canonical(self):
        scope, error = geo.normalize_scope(
            {"kind": "boroughs", "values": ["brooklyn", "Queens"]}
        )
        assert error is None
        assert scope == {"kind": "boroughs", "values": ["Brooklyn", "Queens"]}

    def test_current_location_rejects_values(self):
        scope, error = geo.normalize_scope(
            {"kind": "current_location", "values": ["Brooklyn"]}
        )
        assert scope is None
        assert "empty" in (error or "")

    def test_borough_from_sublocality_then_fails_closed(self):
        assert geo.borough_from_address_components([{"longText": "Brooklyn", "types": ["sublocality_level_1"]}]) == "Brooklyn"
        assert geo.borough_from_address_components([{"longText": "New York", "types": ["locality"]}]) is None

if __name__ == "__main__":
    unittest.main()
