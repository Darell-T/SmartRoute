"""Deterministic contract tests for shared route-advisor input shaping."""

import unittest

import pytest
from evaluation.route_intelligence.advisor_context import (
    PlanningMode,
    build_advisor_payload,
    normalize_ticketmaster_event_impacts,
    parse_advisor_selection,
    parse_candidate_analysis,
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

        assert payload["planning_mode"] == "baseline"
        assert payload["routes"] == _routes()
        assert payload["service_alerts"] == alerts
        assert len(payload["route_candidate_labels"]) == 2
        assert payload["incidents"] == []
        assert payload["stalled_trains"] == []
        assert payload["stalled_buses"] == []
        assert payload["ticketmaster_event_impacts"] == []

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

        assert payload["planning_mode"] == "intelligence"
        assert payload["incidents"][0]["source"] == "grok_web"
        assert payload["stalled_trains"][0]["route_id"] == "Q"
        assert payload["stalled_buses"][0]["route_id"] == "B35"
        assert payload["ticketmaster_event_impacts"][0]["event_id"] == "event-1"
        assert "penalty_minutes" not in payload["ticketmaster_event_impacts"][0]

    def test_shadow_mode_is_rejected_instead_of_silently_falling_back(self):
        with pytest.raises(ValueError, match="planning mode"):
            parse_planning_mode("shadow")
        with pytest.raises(ValueError, match="planning mode"):
            build_advisor_payload(routes=_routes(), service_alerts=[], mode="shadow")

    def test_invalid_mode_is_rejected_instead_of_silently_falling_back(self):
        with pytest.raises(ValueError, match="planning mode"):
            parse_planning_mode("live_everything")
        with pytest.raises(ValueError, match="planning mode"):
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

        assert len(impacts) == 12
        first = impacts[0]
        assert "url" not in first
        assert "secret" not in first["title"]
        assert len(first["stations"]) <= 8
        assert "crowd_level" not in first
        assert "penalty_minutes" not in first
        assert not any("unexpected" in row for row in impacts)

    def test_recorded_or_model_selection_uses_analysis_then_safe_route_zero_fallback(self):
        selected, analysis = parse_advisor_selection(
            '[CANDIDATE_ANALYSIS]{"selected_route_index":1,"candidate_analysis":[{"index":1,"is_recommended":true,"recommendation_reason":"avoids a delay"}]}[/CANDIDATE_ANALYSIS]',
            2,
        )
        assert selected == 1
        assert analysis[1]["recommendation_reason"] == "avoids a delay"

        out_of_range, _ = parse_advisor_selection("[ROUTE:99]", 2)
        assert out_of_range == 0

    def test_strict_agent_selection_requires_one_complete_matching_control_contract(self):
        raw = (
            '[ROUTE:1][CANDIDATE_ANALYSIS]{"selected_route_index":1,'
            '"candidate_analysis":['
            '{"index":0,"is_recommended":false,"rejection_reason":"slower"},'
            '{"index":1,"is_recommended":true,"recommendation_reason":"fewer transfers"}'
            ']}[/CANDIDATE_ANALYSIS]'
        )

        selected, analysis = parse_advisor_selection(raw, 2, strict=True)

        assert selected == 1
        assert analysis[0]["rejection_reason"] == "slower"
        assert analysis[1]["recommendation_reason"] == "fewer transfers"

    def test_strict_agent_selection_rejects_missing_malformed_or_invalid_controls(self):
        complete_analysis = (
            '[CANDIDATE_ANALYSIS]{"selected_route_index":0,'
            '"candidate_analysis":['
            '{"index":0,"is_recommended":true,"recommendation_reason":"fastest"},'
            '{"index":1,"is_recommended":false,"rejection_reason":"slower"}'
            ']}[/CANDIDATE_ANALYSIS]'
        )
        cases = {
            "missing_route_marker": complete_analysis,
            "out_of_range": "[ROUTE:2]" + complete_analysis,
            "malformed_analysis": "[ROUTE:0][CANDIDATE_ANALYSIS]{oops}[/CANDIDATE_ANALYSIS]",
            "missing_candidate_row": (
                '[ROUTE:0][CANDIDATE_ANALYSIS]{"selected_route_index":0,'
                '"candidate_analysis":[{"index":0,"is_recommended":true,'
                '"recommendation_reason":"fastest"}]}[/CANDIDATE_ANALYSIS]'
            ),
        }
        for name, raw in cases.items():
            with self.subTest(name=name), pytest.raises(
                ValueError,
                match=(
                    r"route selection control marker must appear exactly once|"
                    r"route selection index is outside the candidate range|"
                    r"candidate analysis is not valid JSON|"
                    r"candidate analysis must contain every candidate exactly once"
                ),
            ):
                parse_advisor_selection(raw, 2, strict=True)

    def test_intelligence_payload_can_include_normalized_scored_candidates(self):
        payload = build_advisor_payload(
            routes=_routes(),
            service_alerts=[],
            scored_candidates=[
                {"index": 1, "rank": 2, "score": 32.5, "total_minutes": 28, "ignored": "x"},
                {"index": 0, "rank": 1, "score": 20, "transfers": 0},
            ],
        )

        assert payload["scored_candidates"] == [{"index": 0, "rank": 1, "score": 20, "transfers": 0}, {"index": 1, "rank": 2, "score": 32.5, "total_minutes": 28}]

    def test_candidate_analysis_empty_malformed_and_duplicate_cases_are_frozen(self):
        assert parse_candidate_analysis("") == (None, {})
        assert parse_candidate_analysis("[CANDIDATE_ANALYSIS][][/CANDIDATE_ANALYSIS]") == (
            None,
            {},
        )
        assert parse_candidate_analysis(
            "[CANDIDATE_ANALYSIS]{oops}[/CANDIDATE_ANALYSIS]"
        ) == (None, {})

        with pytest.raises(ValueError, match="candidate analysis control block is missing"):
            parse_candidate_analysis("", strict=True)
        with pytest.raises(ValueError, match="candidate analysis is not valid JSON"):
            parse_candidate_analysis(
                "[CANDIDATE_ANALYSIS]{oops}[/CANDIDATE_ANALYSIS]",
                candidate_count=1,
                strict=True,
            )
        duplicate = (
            '[CANDIDATE_ANALYSIS]{"selected_route_index":0,"candidate_analysis":['
            '{"index":0,"is_recommended":true,"recommendation_reason":"fastest"},'
            '{"index":0,"is_recommended":false,"rejection_reason":"duplicate"}'
            "]}[/CANDIDATE_ANALYSIS]"
        )
        with pytest.raises(ValueError, match="duplicate index"):
            parse_candidate_analysis(duplicate, candidate_count=2, strict=True)

        selected, analysis = parse_advisor_selection("no markers here", 3)
        assert selected == 0
        assert analysis == {}
