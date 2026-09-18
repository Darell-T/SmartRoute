"""Focused route-presentation framing and canonical-fact guards."""
from __future__ import annotations

import unittest

from app.services.trips.selection_decision import evaluate_candidate_decision

from tests.present_route_framing_test_support import (
    PresentRouteFramingTestMixin,
    _supported_reason_codes,
)


class RouteDecisionEvaluationTests(PresentRouteFramingTestMixin, unittest.IsolatedAsyncioTestCase):
    def test_tied_minimum_walking_does_not_support_less_walking(self):
        selected = {
            "candidate_id": "cd_selected",
            "digest": {
                "walking_minutes": 3,
                "hard_constraints_satisfied": True,
                "soft_preferences": {
                    "routing_preference": "LESS_WALKING",
                    "avoid_crowds": False,
                },
            },
        }
        record = {
            "candidates": [
                selected,
                {
                    "candidate_id": "cd_alternative",
                    "digest": {
                        "walking_minutes": 3,
                        "hard_constraints_satisfied": True,
                        "soft_preferences": {
                            "routing_preference": "LESS_WALKING",
                            "avoid_crowds": False,
                        },
                    },
                },
            ]
        }

        supported = _supported_reason_codes(record, selected)

        assert "less_walking" not in supported

    def test_partial_alternative_crowd_evidence_blocks_lower_crowd_claim(self):
        selected = {
            "candidate_id": "cd_selected",
            "digest": {
                "hard_constraints_satisfied": True,
                "event_evidence_status": "available",
                "event_or_crowd_impacts": [],
                "soft_preferences": {
                    "avoid_crowds": True,
                    "avoid_crowds_source": "current_turn",
                },
            },
        }
        alternative = {
            "candidate_id": "cd_alternative",
            "digest": {
                "hard_constraints_satisfied": True,
                "event_evidence_status": "partial",
                "event_or_crowd_impacts": [{"risk_score": 8}],
                "soft_preferences": {
                    "avoid_crowds": True,
                    "avoid_crowds_source": "current_turn",
                },
            },
        }

        decision = evaluate_candidate_decision(
            {"candidates": [selected, alternative]},
            selected,
        )

        assert "lower_event_crowd_exposure" not in decision["supported_reason_codes"]
        assert decision["crowd_limitation_required"]

    def test_context_only_event_evidence_cannot_support_lower_crowd_claim(self):
        selected = {
            "candidate_id": "cd_selected",
            "digest": {
                "hard_constraints_satisfied": True,
                "event_evidence_status": "available",
                "event_or_crowd_impacts": [],
                "soft_preferences": {
                    "avoid_crowds": True,
                    "avoid_crowds_source": "current_turn",
                },
            },
        }
        alternative = {
            "candidate_id": "cd_alternative",
            "digest": {
                "hard_constraints_satisfied": True,
                "event_evidence_status": "available",
                "event_or_crowd_impacts": [
                    {
                        "risk_score": 0,
                        "scoring_authorized": False,
                    }
                ],
                "soft_preferences": {
                    "avoid_crowds": True,
                    "avoid_crowds_source": "current_turn",
                },
            },
        }

        decision = evaluate_candidate_decision(
            {"candidates": [selected, alternative]},
            selected,
        )

        assert "lower_event_crowd_exposure" not in decision["supported_reason_codes"]
        assert decision["crowd_limitation_required"]

    def test_accessibility_reason_requires_an_active_rider_requirement(self):
        selected = {
            "candidate_id": "cd_selected",
            "digest": {
                "hard_constraints_satisfied": True,
                "accessibility_status": "accessible",
                "accessibility_required": False,
            },
        }

        supported = _supported_reason_codes(
            {"candidates": [selected]},
            selected,
        )

        assert "accessibility" not in supported

    def test_accessibility_reason_is_supported_when_required_and_satisfied(self):
        selected = {
            "candidate_id": "cd_selected",
            "digest": {
                "hard_constraints_satisfied": True,
                "accessibility_status": "accessible",
                "accessibility_required": True,
            },
        }

        supported = _supported_reason_codes(
            {"candidates": [selected]},
            selected,
        )

        assert "accessibility" in supported

    def test_unselectable_candidate_cannot_define_supported_reason(self):
        selected = {
            "candidate_id": "cd_selected",
            "digest": {
                "duration_minutes": 20,
                "hard_constraints_satisfied": True,
            },
        }
        record = {
            "candidates": [
                selected,
                {
                    "candidate_id": "cd_rejected",
                    "digest": {
                        "duration_minutes": 10,
                        "hard_constraints_satisfied": False,
                    },
                },
            ]
        }

        supported = _supported_reason_codes(record, selected)

        assert "meets_hard_constraints" in supported
        assert "fastest" not in supported

    def test_missing_or_inverse_preference_cannot_define_less_walking(self):
        for preferences in ({}, {"routing_preference": "FEWER_TRANSFERS"}):
            with self.subTest(preferences=preferences):
                selected = {
                    "candidate_id": "cd_selected",
                    "digest": {
                        "walking_minutes": 2,
                        "hard_constraints_satisfied": True,
                        "soft_preferences": preferences,
                    },
                }
                alternative = {
                    "candidate_id": "cd_alternative",
                    "digest": {
                        "walking_minutes": 4,
                        "hard_constraints_satisfied": True,
                        "soft_preferences": preferences,
                    },
                }
                assert "less_walking" not in _supported_reason_codes({"candidates": [selected, alternative]}, selected)

    def test_missing_numeric_factors_fail_closed_for_fastest_and_transfers(self):
        for reason, key, preferences in (
            ("fastest", "duration_minutes", {"routing_preference": "FEWER_TRANSFERS"}),
            ("fewer_transfers", "transfers", {"routing_preference": "FEWER_TRANSFERS"}),
        ):
            with self.subTest(reason=reason):
                selected = {
                    "candidate_id": "cd_selected",
                    "digest": {
                        "hard_constraints_satisfied": True,
                        "soft_preferences": preferences,
                    },
                }
                alternative = {
                    "candidate_id": "cd_alternative",
                    "digest": {
                        key: 9,
                        "hard_constraints_satisfied": True,
                        "soft_preferences": preferences,
                    },
                }
                assert reason not in _supported_reason_codes({"candidates": [selected, alternative]}, selected)

    def test_reason_code_is_grounded_in_the_active_rider_preference(self):
        selected = {
            "candidate_id": "cd_selected",
            "digest": {
                "hard_constraints_satisfied": True,
                "transfers": 0,
                "duration_minutes": 30,
                "soft_preferences": {
                    "routing_preference": "FEWER_TRANSFERS",
                    "routing_preference_source": "current_turn",
                },
            },
        }
        alternative = {
            "candidate_id": "cd_alternative",
            "digest": {
                "hard_constraints_satisfied": True,
                "transfers": 1,
                "duration_minutes": 35,
                "soft_preferences": {
                    "routing_preference": "FEWER_TRANSFERS",
                    "routing_preference_source": "current_turn",
                },
            },
        }
        record = {"candidates": [selected, alternative]}

        supported = _supported_reason_codes(record, selected)

        assert "fewer_transfers" in supported
        assert "less_walking" not in supported
        assert "fastest" not in supported
