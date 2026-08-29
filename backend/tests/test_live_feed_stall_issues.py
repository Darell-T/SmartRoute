import unittest
from types import SimpleNamespace

from app.services.live_feed import snapshot as rider_snapshot
from app.services.mta.static_gtfs.stop_patterns import StopPatternIndex

NOW = 1_700_000_000


def stall_pattern_index():
    return StopPatternIndex(
        {
            "stops": {
                "Q00": {"name": "Origin", "lat": 40.73, "lon": -73.99},
                "Q01": {"name": "One", "lat": 40.74, "lon": -73.99},
                "Q02": {"name": "Two", "lat": 40.75, "lon": -73.99},
                "Q03": {"name": "Three", "lat": 40.76, "lon": -73.99},
                "Q04": {"name": "Four", "lat": 40.77, "lon": -73.99},
            },
            "patterns": [
                {
                    "route_id": "Q",
                    "route_short_name": "Q",
                    "stop_ids": ["Q00", "Q01", "Q02", "Q03", "Q04"],
                }
            ],
        }
    )


def stall_alert(stop_id, *, route_id="Q", alert_id=None, header=None):
    return {
        "alert_id": alert_id or f"stall-{stop_id}",
        "header": header or f"Stalled {route_id} train at {stop_id}",
        "description": "",
        "route_ids": [route_id],
        "stop_ids": [stop_id],
    }


class NearbyStallIssueTests(unittest.TestCase):
    def test_missing_nearby_stop_does_not_invent_a_stall_origin(self):
        issues = rider_snapshot.build_nearby_transit_issues(
            gtfs=SimpleNamespace(_pattern_index=stall_pattern_index()),
            alerts=[stall_alert("Q00")],
            nearby_stop_id=None,
            nearby_stop_name=None,
            nearby_route_ids={"Q"},
            observed_at=NOW,
        )
        assert issues == []

    def _issues(self, alerts, selected=()):
        return rider_snapshot.build_nearby_transit_issues(
            gtfs=SimpleNamespace(_pattern_index=stall_pattern_index()),
            alerts=alerts,
            nearby_stop_id="Q00",
            nearby_stop_name="Origin",
            nearby_route_ids={"Q"},
            selected_route_ids=selected,
            observed_at=NOW,
        )

    def test_stall_hop_0_is_kept_as_nearby_issue(self):
        issues = self._issues([stall_alert("Q00")])

        assert issues[0]["stops_away"] == 0
        assert issues[0]["station_id"] == "Q00"
        assert issues[0]["summary"] == "Q train stalled at Origin"
        assert issues[0]["status"] == "stalled"
        assert issues[0]["confidence"] == "confirmed"

    def test_stall_hop_3_is_kept_as_nearby_issue(self):
        issues = self._issues([stall_alert("Q03")])

        assert issues[0]["stops_away"] == 3
        assert issues[0]["station_id"] == "Q03"

    def test_stall_hop_4_is_dropped_from_nearby_issues(self):
        assert self._issues([stall_alert("Q04")]) == []

    def test_non_stall_alert_text_is_not_an_issue(self):
        assert (
            self._issues(
                [stall_alert("Q00", header="Q trains are delayed in both directions")]
            )
            == []
        )

    def test_nearby_issues_returns_at_most_one_closest_stall(self):
        issues = self._issues([stall_alert("Q03"), stall_alert("Q00")])

        assert len(issues) == 1
        assert issues[0]["stops_away"] == 0
        assert issues[0]["station_id"] == "Q00"

    def test_selected_route_stall_relevance_is_planned_route(self):
        issues = self._issues([stall_alert("Q00")], selected={"Q"})

        assert issues[0]["relevance"] == "planned_route"

    def test_unselected_route_stall_relevance_is_nearby_line(self):
        issues = self._issues([stall_alert("Q00")], selected={"B"})

        assert issues[0]["relevance"] == "nearby_line"


if __name__ == "__main__":
    unittest.main()
