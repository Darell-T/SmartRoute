import unittest

from app.services.trips import candidates, scoring


def _subway_route(line: str, total_minutes: int) -> list[dict]:
    return [
        {"type": "WALK", "minutes_until_arrival": 2},
        {
            "type": "SUBWAY",
            "route_id": line,
            "train_line": line,
            "departure_stop": "A St",
            "arrival_stop": "B St",
            "minutes_until_arrival": total_minutes,
            "route_total_minutes": total_minutes,
        },
    ]


def _candidate_reasons(
    routes: list[list[dict]],
    chosen_index: int,
    alerts: list[dict] | None = None,
) -> list[dict]:
    return candidates._build_route_candidates(
        routes,
        chosen_index,
        {},
        scoring._score_routes(routes, alerts or []),
    )


class TripCandidateReasonTests(unittest.TestCase):
    def test_candidate_fallback_recommends_fastest_with_no_reported_alerts(self):
        rows = _candidate_reasons(
            [_subway_route("Q", 20), _subway_route("B", 28)],
            chosen_index=0,
            alerts=[],
        )

        self.assertEqual(
            rows[0]["recommendation_reason"],
            "Fastest route at 20 min with no reported service alerts.",
        )

    def test_candidate_fallback_explains_slower_alternate_with_named_alert(self):
        rows = _candidate_reasons(
            [_subway_route("Q", 20), _subway_route("B", 28)],
            chosen_index=0,
            alerts=[
                {
                    "route_ids": ["B"],
                    "header": "Signal problem near DeKalb Av",
                }
            ],
        )

        self.assertEqual(
            rows[1]["rejection_reason"],
            "Slower by 8 min and affected by Signal problem near DeKalb Av.",
        )
        self.assertEqual(rows[1]["score_breakdown"]["active_alerts"], 1)

    def test_candidate_fallback_explains_faster_alternate_with_service_risk(self):
        rows = _candidate_reasons(
            [_subway_route("Q", 20), _subway_route("B", 18)],
            chosen_index=0,
            alerts=[
                {
                    "route_ids": ["B"],
                    "header": "Stalled vehicle near 34 St",
                }
            ],
        )

        self.assertEqual(
            rows[1]["rejection_reason"],
            "Faster by 2 min, but affected by Stalled vehicle near 34 St.",
        )

    def test_candidate_fallback_names_alert_when_recommended_route_has_one(self):
        rows = _candidate_reasons(
            [_subway_route("Q", 20)],
            chosen_index=0,
            alerts=[
                {
                    "route_ids": ["Q"],
                    "header": "Planned work on Q",
                }
            ],
        )

        self.assertEqual(
            rows[0]["recommendation_reason"],
            "Fastest route despite an alert: Planned work on Q.",
        )

    def test_candidate_labels_are_passenger_facing_context(self):
        routes = [
            _subway_route("Q", 20),
            [
                {"type": "WALK", "minutes_until_arrival": 3},
                {
                    "type": "BUS",
                    "route_id": "B41",
                    "train_line": "B41",
                    "departure_stop": "Flatbush Av/Church Av",
                    "arrival_stop": "Downtown Brooklyn",
                    "minutes_until_arrival": 22,
                    "route_total_minutes": 22,
                },
            ],
            [
                {"type": "WALK", "minutes_until_arrival": 2},
                {
                    "type": "SUBWAY",
                    "route_id": "D",
                    "train_line": "D",
                    "departure_stop": "Church Av",
                    "arrival_stop": "Atlantic Av-Barclays Ctr",
                    "minutes_until_arrival": 12,
                },
                {
                    "type": "SUBWAY",
                    "route_id": "Q",
                    "train_line": "Q",
                    "departure_stop": "Atlantic Av-Barclays Ctr",
                    "arrival_stop": "96 St",
                    "minutes_until_arrival": 30,
                    "route_total_minutes": 30,
                },
            ],
        ]

        labels = candidates._build_route_candidate_labels(routes)

        self.assertEqual(labels[0]["displayLabel"], "Q route from A St")
        self.assertEqual(
            labels[1]["displayLabel"],
            "B41 bus option from Flatbush Av/Church Av",
        )
        self.assertEqual(
            labels[2]["displayLabel"],
            "D/Q subway option via Atlantic Av-Barclays Ctr",
        )
        self.assertEqual(labels[2]["routeIds"], ["D", "Q"])


if __name__ == "__main__":
    unittest.main()
