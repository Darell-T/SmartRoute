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


if __name__ == "__main__":
    unittest.main()
