"""Regression tests for route-card event serialization."""

from __future__ import annotations

import unittest

from app.services.agent import events as agent_events


class RouteCardEventItineraryWireTests(unittest.TestCase):
    def test_to_data_omits_itinerary_when_none(self):
        event = agent_events.RouteCardEvent(
            card_id="rc_x",
            turn_id="t1",
            role="recommended",
            origin={"label": "A"},
            destination={"label": "B"},
            summary={"eta_minutes": 1, "transfers": 0, "lines": [], "reason": None},
            route=[],
            alerts=[],
        )
        data = event.to_data()
        assert "itinerary" not in data

    def test_to_data_includes_itinerary_when_present(self):
        itinerary = {
            "itinerary_id": "rc_x",
            "total_duration_seconds": 120,
            "transfer_count": 0,
        }
        event = agent_events.RouteCardEvent(
            card_id="rc_x",
            turn_id="t1",
            role="recommended",
            origin={"label": "A"},
            destination={"label": "B"},
            summary={"eta_minutes": 2, "transfers": 0, "lines": [], "reason": None},
            route=[],
            alerts=[],
            itinerary=itinerary,
        )
        data = event.to_data()
        assert data["itinerary"] == itinerary


if __name__ == "__main__":
    unittest.main()
