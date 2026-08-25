from types import SimpleNamespace
import unittest

from app.services.live_feed.snapshot import build_nearby_transit_issues


class FakeGtfs:
    def __init__(self):
        pattern = {
            "stop_ids": ["D38", "D39", "D40", "D41", "D42", "D43", "D44"],
            "pos": {
                "D38": 0,
                "D39": 1,
                "D40": 2,
                "D41": 3,
                "D42": 4,
                "D43": 5,
                "D44": 6,
            },
        }
        self._pattern_index = SimpleNamespace(
            route_patterns={"Q": [pattern], "B": [pattern]},
            stops={
                "D38": {"name": "Prospect Park"},
                "D39": {"name": "Parkside Av"},
                "D40": {"name": "Church Av"},
                "D41": {"name": "Beverley Rd"},
                "D42": {"name": "Cortelyou Rd"},
                "D43": {"name": "Newkirk Plaza"},
                "D44": {"name": "Avenue H"},
            },
        )


def alert(route_id="Q", stop_id="D38", header="Stalled Q train at Prospect Park"):
    return {
        "alert_id": "mta-1",
        "header": header,
        "description": "",
        "route_ids": [route_id],
        "stop_ids": [stop_id],
    }


def build(alerts, *, selected=()):
    return build_nearby_transit_issues(
        gtfs=FakeGtfs(),
        alerts=alerts,
        nearby_stop_id="D40",
        nearby_stop_name="Church Av",
        nearby_route_ids={"B", "Q"},
        selected_route_ids=selected,
        observed_at=1_700_000_000,
    )


class NearbyIssuesTest(unittest.TestCase):
    def test_confirmed_nearby_stall_uses_station_hops_and_product_copy(self):
        issues = build([alert()])

        self.assertEqual(
            issues,
            [
                {
                    "id": "mta-1",
                    "route_ids": ["Q"],
                    "station_id": "D38",
                    "station_name": "Prospect Park",
                    "stops_away": 2,
                    "confidence": "confirmed",
                    "status": "stalled",
                    "summary": (
                        "Q train stalled near Prospect Park "
                        "· 2 stops from Church Av"
                    ),
                    "source_types": ["mta_service_alert"],
                    "observed_at": "2023-11-14T22:13:20+00:00",
                    "relevance": "nearby_line",
                }
            ],
        )

    def test_unrelated_distant_and_unlocalized_alerts_are_suppressed(self):
        self.assertEqual(build([alert(route_id="A")]), [])
        self.assertEqual(build([alert(stop_id="D44")]), [])
        self.assertEqual(
            build([alert(stop_id="", header="Stalled Q train")]),
            [],
        )

    def test_non_stall_provider_text_is_not_promoted_to_issue(self):
        self.assertEqual(
            build([alert(header="Q trains are delayed in both directions")]),
            [],
        )

    def test_selected_route_marks_local_issue_as_planned_route_relevant(self):
        issue = build([alert()], selected={"Q"})[0]
        self.assertEqual(issue["relevance"], "planned_route")


if __name__ == "__main__":
    unittest.main()
