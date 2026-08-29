import unittest

from app.services.trips import candidates, direct_plan, scoring


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
    def test_finalized_score_keeps_vehicle_signals_unconfirmed_and_deduplicated(self):
        route = _subway_route("Q", 20)

        score = scoring.finalized_route_score(
            route=route,
            itinerary=None,
            alerts=[],
            incidents=[],
            vehicle_claims=[
                {"route_id": "Q", "status": "stopped"},
                {"route": "Q", "progress_status": "stopped"},
                {"route_id": "R", "ProgressStatus": "layover"},
                {"status": "stopped"},
                "untrusted",
            ],
            event_impacts=[],
        )

        assert score["vehicle_signal_count"] == 1
        assert score["unconfirmed_vehicle_impacts"] == ["possible delay signal on Q"]
        assert score["service_condition_penalty"] == 4.0
    def test_candidate_reason_cannot_reuse_stale_model_duration(self):
        route = _subway_route("Q", 46)
        rows = candidates._build_route_candidates(
            [route],
            chosen_index=0,
            candidate_analysis={
                0: {
                    "recommendation_reason": "Fastest route at 44 min with no reported service alerts.",
                    "rejection_reason": "",
                }
            },
            scored_routes=[
                {
                    "index": 0,
                    "total_minutes": 46,
                    "transfers": 0,
                    "alert_count": 0,
                    "score": 46,
                    "rank": 1,
                }
            ],
        )

        assert "44 min" not in rows[0]["recommendation_reason"]
        assert "46 min" in rows[0]["recommendation_reason"]

    def test_recommendation_reasons_surface_less_walking_before_alert_absence(self):
        reasons = direct_plan.build_recommendation_reasons(
            {
                "total_minutes": 46,
                "transfers": 1,
                "walk_minutes": 4,
                "walking_penalty": 8,
                "alert_count": 0,
                "event_crowd_penalty": 0,
                "score": 54,
            },
            [
                {
                    "total_minutes": 44,
                    "transfers": 2,
                    "walk_minutes": 12,
                    "walking_penalty": 24,
                    "alert_count": 0,
                    "event_crowd_penalty": 0,
                    "score": 76,
                }
            ],
        )

        assert reasons[0]["code"] == "less_walking"
        assert reasons[1]["code"] == "fewer_transfers"
        assert direct_plan.format_recommendation_reason(reasons[0]) == "Uses 8 fewer minutes of walking (4 min on foot)."

    def test_airtrain_tram_counts_as_a_transfer_and_route_line(self):
        route = _subway_route("F", 71)
        route.append(
            {
                "type": "TRAM",
                "route_id": "Jamaica AirTrain",
                "train_line": "Jamaica AirTrain",
                "departure_stop": "Jamaica",
                "arrival_stop": "Terminal 1",
                "minutes_until_arrival": 8,
                "route_total_minutes": 71,
            }
        )

        score = scoring._route_score(route, [])

        assert score["transfers"] == 1
        assert scoring._route_lines(route) == ["F", "JAMAICA AIRTRAIN"]

    def test_candidate_fallback_recommends_fastest_without_using_alert_absence(self):
        rows = _candidate_reasons(
            [_subway_route("Q", 20), _subway_route("B", 28)],
            chosen_index=0,
            alerts=[],
        )

        assert rows[0]["recommendation_reason"] == "Fastest route at 20 min."

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

        assert rows[1]["rejection_reason"] == "Slower by 8 min and affected by Signal problem near DeKalb Av."
        assert rows[1]["score_breakdown"]["active_alerts"] == 1

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

        assert rows[1]["rejection_reason"] == "Faster by 2 min, but affected by Stalled vehicle near 34 St."

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

        assert rows[0]["recommendation_reason"] == "Fastest route despite an alert: Planned work on Q."

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

        assert labels[0]["displayLabel"] == "Q route from A St"
        assert labels[1]["displayLabel"] == "B41 bus option from Flatbush Av/Church Av"
        assert labels[2]["displayLabel"] == "D/Q subway option via Atlantic Av-Barclays Ctr"
        assert labels[2]["routeIds"] == ["D", "Q"]


if __name__ == "__main__":
    unittest.main()
