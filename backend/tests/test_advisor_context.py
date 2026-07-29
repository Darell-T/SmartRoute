"""Deterministic contract tests for shared route-advisor input shaping."""

import unittest

from app.services.trips.advisor_context import (
    PlanningMode,
    build_advisor_payload,
    normalize_ticketmaster_event_impacts,
    parse_advisor_selection,
    parse_planning_mode,
)


def _routes():
    return [
        [{"type": "SUBWAY", "route_id": "Q", "departure_stop": "Church Av"}],
        [{"type": "BUS", "route_id": "B35", "departure_stop": "Church Av"}],
    ]


class AdvisorContextTests(unittest.TestCase):
    def test_baseline_keeps_candidates_and_core_mta_alerts_but_removes_supplemental_signals(self):
        alerts = [{"header": "Q trains delayed", "route_ids": ["Q"]}]
        payload = build_advisor_payload(
            routes=_routes(),
            service_alerts=alerts,
            incidents=[{"source": "511ny", "severity": "high"}],
            stalled_trains=[{"route_id": "Q"}],
            stalled_buses=[{"route_id": "B35"}],
            ticketmaster_event_impacts=[{"event_id": "msg-game", "venue": "MSG"}],
            mode="baseline",
        )

        self.assertEqual(payload["planning_mode"], "baseline")
        self.assertEqual(payload["routes"], _routes())
        self.assertEqual(payload["service_alerts"], alerts)
        self.assertEqual(len(payload["route_candidate_labels"]), 2)
        self.assertEqual(payload["incidents"], [])
        self.assertEqual(payload["stalled_trains"], [])
        self.assertEqual(payload["stalled_buses"], [])
        self.assertEqual(payload["ticketmaster_event_impacts"], [])

    def test_intelligence_carries_all_current_signals(self):
        ticketmaster = {
            "event_id": "event-1",
            "title": "Knicks game",
            "venue": "Madison Square Garden",
            "stations": ["34 St-Herald Sq"],
            "lines": ["B", "D", "F", "M"],
            "crowd_level": "high",
            "penalty_minutes": 6,
            "window_start_iso": "2026-07-22T20:00:00-04:00",
            "window_end_iso": "2026-07-22T23:00:00-04:00",
        }
        payload = build_advisor_payload(
            routes=_routes(),
            service_alerts=[{"header": "Q trains delayed"}],
            incidents=[{"source": "grok_web", "description": "station access restricted"}],
            stalled_trains=[{"route_id": "Q", "direction": "northbound"}],
            stalled_buses=[{"route_id": "B35"}],
            ticketmaster_event_impacts=[ticketmaster],
            mode=PlanningMode.INTELLIGENCE,
        )

        self.assertEqual(payload["planning_mode"], "intelligence")
        self.assertEqual(payload["incidents"][0]["source"], "grok_web")
        self.assertEqual(payload["stalled_trains"][0]["route_id"], "Q")
        self.assertEqual(payload["stalled_buses"][0]["route_id"], "B35")
        self.assertEqual(payload["ticketmaster_event_impacts"][0]["event_id"], "event-1")
        self.assertNotIn("penalty_minutes", payload["ticketmaster_event_impacts"][0])

    def test_shadow_carries_intelligence_evidence_without_deciding_display_behavior(self):
        payload = build_advisor_payload(
            routes=_routes(),
            service_alerts=[],
            incidents=[{"source": "grok_x"}],
            mode="shadow",
        )

        self.assertEqual(payload["planning_mode"], "shadow")
        self.assertEqual(payload["incidents"], [{"source": "grok_x"}])

    def test_invalid_mode_is_rejected_instead_of_silently_falling_back(self):
        with self.assertRaisesRegex(ValueError, "planning mode"):
            parse_planning_mode("live_everything")
        with self.assertRaises(ValueError):
            build_advisor_payload(routes=_routes(), service_alerts=[], mode="unknown")

    def test_ticketmaster_evidence_is_bounded_and_excludes_unstructured_or_url_data(self):
        raw = [
            {
                "event_id": "event-1",
                "title": "Game https://example.test/?apikey=secret",
                "venue": "Arena",
                "stations": ["A", "A", "B", *[f"s-{index}" for index in range(20)]],
                "lines": ["Q", "Q", "B"],
                "crowd_level": "EXTREME",
                "penalty_minutes": float("inf"),
                "url": "https://provider.test/?apikey=secret",
            },
            {"unexpected": "row"},
            *[{"event_id": f"event-{index}", "venue": "Arena"} for index in range(2, 20)],
        ]
        impacts = normalize_ticketmaster_event_impacts(raw)

        self.assertEqual(len(impacts), 12)
        first = impacts[0]
        self.assertNotIn("url", first)
        self.assertNotIn("secret", first["title"])
        self.assertLessEqual(len(first["stations"]), 8)
        self.assertNotIn("crowd_level", first)
        self.assertNotIn("penalty_minutes", first)
        self.assertFalse(any("unexpected" in row for row in impacts))

    def test_recorded_or_model_selection_uses_analysis_then_safe_route_zero_fallback(self):
        selected, analysis = parse_advisor_selection(
            '[CANDIDATE_ANALYSIS]{"selected_route_index":1,"candidate_analysis":[{"index":1,"is_recommended":true,"recommendation_reason":"avoids a delay"}]}[/CANDIDATE_ANALYSIS]',
            2,
        )
        self.assertEqual(selected, 1)
        self.assertEqual(analysis[1]["recommendation_reason"], "avoids a delay")

        out_of_range, _ = parse_advisor_selection("[ROUTE:99]", 2)
        self.assertEqual(out_of_range, 0)
