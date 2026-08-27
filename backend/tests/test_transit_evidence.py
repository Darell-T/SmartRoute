"""Focused invariants for the server-owned transit evidence contract."""

from __future__ import annotations

import unittest
from typing import ClassVar

from app.services import cache
from app.services.agent.tools.transit import evidence as transit_evidence
from app.services.agent.tools.transit import (
    evidence_projection as transit_evidence_projection,
)
from app.services.agent.tools.transit import transit_snapshot
from app.services.agent.tools.transit.direction import (
    normalize_direction,
    resolve_direction,
    resolve_model_direction,
)


class TransitEvidenceTests(unittest.TestCase):
    def setUp(self) -> None:
        cache._mem.clear()

    def test_operation_fact_text_covers_each_passenger_facing_operation(self) -> None:
        render = transit_evidence_projection.operation_facts_text

        assert render("fact", {"text": "The base fare is $2.90."}) == "The base fare is $2.90."
        assert "Incident: Signal problem" in render("area_conditions", {"area": "Union Square", "incidents": [{"name": "Signal problem"}], "events": [{"name": "Street fair"}]})
        assert "No matching incident" in render("area_conditions", {"area": "SoHo", "incident_status": "complete", "event_status": "complete"})
        assert "coverage near SoHo is incomplete" in render("area_conditions", {"area": "SoHo", "incident_status": "partial"})
        assert "Concert at Barclays Center" in render("event_schedule", {"events": [{"name": "Concert", "venue_name": "Barclays Center", "start_iso": "2026-08-24T20:00:00-04:00"}]})
        assert "didn't find matching events" in render("event_schedule", {})
        assert "2026-08-24T22:00:00-04:00 to 2026-08-25T00:00:00-04:00" in render("venue_crowd_window", {"venue": "MSG", "surge_start_iso": "2026-08-24T22:00:00-04:00", "surge_end_iso": "2026-08-25T00:00:00-04:00"})
        assert "unavailable for MSG" in render("venue_crowd_window", {"venue": "MSG"})
        assert "unavailable" in render("unknown", {})

    def test_route_alert_does_not_gain_requested_downtown_scope(self) -> None:
        set_id, payload = transit_evidence.build_evidence_set(
            session_id="s1",
            operation="service_status",
            route_ids=["q"],
            direction="downtown",
            concerns=["stalled_train", "delay"],
            result={
                "source": "mta_service_alerts",
                "freshness": "live",
                "status": "active_alerts",
                "alerts": [{
                    "alert_id": "a1",
                    "header": "Q delays",
                    "route_ids": ["Q"],
                    "direction_scope": "unspecified",
                }],
                "unconfirmed_signals": [{"route_id": "Q", "stop_id": "D28"}],
            },
        )

        assert set_id.startswith("te_")
        assert payload["checked_routes"] == ["Q"]
        assert payload["direction_scope"]["requested"] == "downtown"
        assert not payload["direction_scope"]["authoritative"]
        assert payload["direction_scope"]["resolved"] is None
        assert payload["confirmed_matching_alerts"][0]["route_ids"] == ["Q"]
        assert payload["unconfirmed_signals"] == []
        assert "downtown direction was not resolved" in " ".join(payload["unknowns"])

    def test_planned_q_alert_survives_snapshot_and_applies_to_both_directions(self) -> None:
        raw_alert = {
            "source": "mta_service_alerts",
            "source_id": "lmm:planned_work:33095",
            "alert_id": "lmm:planned_work:33095",
            "header": "Q trains run local",
            "description": (
                "In Manhattan, Q runs local in both directions between "
                "57 St-7 Av and Canal St"
            ),
            "route_ids": ["Q"],
            "stop_ids": ["Q01N", "Q01S"],
            "direction_ids": ["0", "1"],
            "direction_scope": "both_directions",
            "affected_segments": [
                {"route_id": "Q", "stop_id": "Q01N", "direction_id": "0"},
                {"route_id": "Q", "stop_id": "Q01S", "direction_id": "1"},
            ],
            "planned_status": "planned",
            "change_type": "express_to_local",
            "service_operating": True,
            "material_disruption": False,
            "effective_window": {"start": 100, "end": 200},
            "effective_start": 100,
            "effective_end": 200,
            "feed_observed_at": "2026-08-23T12:00:00+00:00",
            "local_verified_at": "2026-08-23T12:01:00+00:00",
        }
        snapshot_alert = transit_snapshot._safe_alert(raw_alert)
        assert snapshot_alert["source_id"] == "lmm:planned_work:33095"
        assert snapshot_alert["direction_scope"] == "both_directions"
        assert not snapshot_alert["material_disruption"]

        for requested_direction in ("uptown", "downtown"):
            with self.subTest(direction=requested_direction):
                _set_id, payload = transit_evidence.build_evidence_set(
                    session_id=f"q-{requested_direction}",
                    operation="service_status",
                    route_ids=["Q"],
                    direction=requested_direction,
                    result={
                        "source": "mta_service_alerts",
                        "freshness": "live",
                        "status": "active_alerts",
                        "alerts": [snapshot_alert],
                    },
                )
                alert = payload["confirmed_matching_alerts"][0]
                assert alert["source"] == "mta_service_alerts"
                assert alert["source_id"] == "lmm:planned_work:33095"
                assert alert["alert_id"] == "lmm:planned_work:33095"
                assert alert["route_ids"] == ["Q"]
                assert alert["stop_ids"] == ["Q01N", "Q01S"]
                assert alert["direction_ids"] == ["0", "1"]
                assert alert["direction_scope"] == "both_directions"
                assert alert["affected_segments"][0]["stop_id"] == "Q01N"
                assert alert["planned_status"] == "planned"
                assert alert["change_type"] == "express_to_local"
                assert alert["service_operating"] is True
                assert alert["material_disruption"] is False
                assert alert["effective_window"] == {"start": 100, "end": 200}
                assert alert["feed_observed_at"] == "2026-08-23T12:00:00+00:00"
                assert alert["local_verified_at"] == "2026-08-23T12:01:00+00:00"
                assert "direction" not in alert
                assert payload["direction_scope"]["resolved"] == requested_direction
                assert payload["direction_scope"]["authoritative"]
                assert "did not specify the requested direction" not in " ".join(payload["unknowns"])

    def test_arrivals_are_grouped_by_verified_direction_and_owned_by_session(self) -> None:
        set_id, payload = transit_evidence.build_evidence_set(
            session_id="s1",
            operation="arrivals",
            route_ids=["Q"],
            direction="downtown",
            result={
                "route_id": "Q",
                "stop": {"id": "D28", "name": "Newkirk Plaza"},
                "source_status": "live",
                "updated_at": "2026-08-15T12:00:00+00:00",
                "directions": [{
                    "id": "downtown",
                    "label": "Downtown / Brooklyn-bound",
                    "arrivals": [{"expected_at": "2026-08-15T12:05:00+00:00", "minutes": 5, "trip_id": "secret"}],
                }],
            },
        )

        assert payload["direction_scope"]["resolved"] == "downtown"
        assert payload["direction_scope"]["authoritative"]
        assert payload["arrivals_by_direction"]["downtown"][0]["minutes"] == 5
        assert "trip_id" not in repr(payload)
        assert transit_evidence.load_evidence_set(set_id, session_id="s1") is not None
        assert transit_evidence.load_evidence_set(set_id, session_id="other") is None

    def test_downtown_query_cannot_leak_uptown_catchability(self) -> None:
        _set_id, payload = transit_evidence.build_evidence_set(
            session_id="s1",
            operation="arrivals",
            route_ids=["Q"],
            direction="downtown",
            result={
                "route_id": "Q",
                "source_status": "live",
                "directions": [
                    {
                        "id": "uptown",
                        "label": "Uptown / Manhattan-bound",
                        "arrivals": [{"minutes": 3}],
                    },
                    {
                        "id": "downtown",
                        "label": "Downtown / Brooklyn-bound",
                        "arrivals": [{"minutes": 9}],
                    },
                ],
                "catchability": {
                    "walking_minutes": 2,
                    "boarding_buffer_minutes": 2,
                    "arrival_minutes": [3, 9],
                    "catchable_arrival_minutes": 3,
                    "confidence": 0.9,
                },
            },
        )

        safe_result = payload["results"][0]
        assert [group["id"] for group in safe_result["directions"]] == ["downtown"]
        assert sorted(payload["arrivals_by_direction"]) == ["downtown"]
        assert safe_result["catchability"]["arrival_minutes"] == [9]
        assert safe_result["catchability"]["catchable_arrival_minutes"] == 9
        assert "uptown" not in repr(safe_result)

    def test_numeric_provider_direction_ids_stay_unknown_without_semantic_context(self) -> None:
        _set_id, payload = transit_evidence.build_evidence_set(
            session_id="s1",
            operation="arrivals",
            route_ids=["M15"],
            result={
                "route_id": "M15",
                "source_status": "live",
                "directions": [
                    {"id": 0, "label": "Outbound", "arrivals": [{"minutes": 5}]},
                    {"id": 1, "label": "Inbound", "arrivals": [{"minutes": 7}]},
                ],
            },
        )

        assert sorted(payload["arrivals_by_direction"]) == ["unknown"]
        assert "uptown" not in repr(payload)
        assert "downtown" not in repr(payload)

    def test_bus_signal_does_not_inherit_subway_platform_direction(self) -> None:
        signal = transit_evidence.safe_unconfirmed_signal(
            {"route_id": "M15", "stop_id": "123N", "mode": "bus"}
        )

        assert "direction" not in signal

    def test_unresolved_direction_is_unknown_not_all_clear(self) -> None:
        _set_id, payload = transit_evidence.build_evidence_set(
            session_id="s1",
            operation="service_status",
            route_ids=["Q"],
            direction="downtown",
            result={
                "source": "mta_service_alerts",
                "freshness": "live",
                "status": "no_active_alerts",
                "alerts": [],
            },
        )

        assert payload["service_status_by_direction"]["all"]["status"] == "unknown"
        assert "downtown direction was not resolved" in " ".join(payload["unknowns"])
        assert "no_matching_alerts" not in repr(payload["service_status_by_direction"])

    def test_partial_coverage_never_becomes_all_clear(self) -> None:
        _set_id, payload = transit_evidence.build_evidence_set(
            session_id="s1",
            operation="service_status",
            route_ids=["Q"],
            result={"source": "mta_service_alerts", "freshness": "partial", "status": "no_active_alerts", "alerts": []},
        )

        assert payload["source_coverage"]["alerts"] == "partial"
        assert "partial or missing coverage" in " ".join(payload["unknowns"])

    def test_typed_alert_kind_wins_over_provider_text_fallback(self) -> None:
        _set_id, payload = transit_evidence.build_evidence_set(
            session_id="s1",
            operation="service_status",
            route_ids=["Q"],
            concerns=["delay"],
            result={
                "source": "mta_service_alerts",
                "freshness": "live",
                "status": "active_alerts",
                "alerts": [
                    {
                        "alert_id": "info",
                        "kind": "planned work",
                        "header": "Q delays are possible",
                        "route_ids": ["Q"],
                    },
                    {
                        "alert_id": "delay",
                        "kind": "service_delay",
                        "header": "Q service update",
                        "route_ids": ["Q"],
                    },
                ],
            },
        )

        assert [item["alert_id"] for item in payload["confirmed_matching_alerts"]] == ["delay"]

    def test_direction_aliases_are_structured_not_substring_classification(self) -> None:
        assert normalize_direction("uptown") == "uptown"
        assert normalize_direction("downtown") == "downtown"
        assert normalize_direction(0) is None
        assert normalize_direction(1) is None
        assert normalize_direction("not uptown") is None

        resolved = resolve_direction(
            "Coney Island-Stillwell Av",
            [
                {
                    "direction_id": 1,
                    "direction": "downtown",
                    "direction_label": "Coney Island-Stillwell Av",
                }
            ],
        )
        assert resolved.resolved == "downtown"
        assert resolved.authoritative
        borough_label = resolve_direction(
            "Manhattan",
            [{"direction_id": 2, "direction_label": "Manhattan"}],
        )
        assert borough_label.resolved == "manhattan"
        assert borough_label.resolved not in {"uptown", "downtown"}
        assert resolve_direction(0, [{"direction_id": 0, "direction_label": "uptown"}]).resolved == "uptown"
        assert resolve_direction(0, [{"direction_id": 0, "direction_label": "Outbound"}]).resolved is None

    def test_destination_resolves_against_static_pattern_terminal(self) -> None:
        class PatternIndex:
            route_patterns: ClassVar[dict] = {
                "Q": [
                    {
                        "direction_id": 0,
                        "direction_label": "uptown",
                        "stop_ids": ["north-terminal"],
                    },
                    {
                        "direction_id": 1,
                        "direction_label": "downtown",
                        "stop_ids": ["south-terminal"],
                    },
                ]
            }
            stops: ClassVar[dict] = {
                "north-terminal": {"name": "96 St"},
                "south-terminal": {"name": "Coney Island-Stillwell Av"},
            }

        class Gtfs:
            _pattern_index = PatternIndex()

        result = resolve_model_direction("96 St", ["Q"], gtfs=Gtfs())
        assert result.resolved == "uptown"
        assert result.authoritative

    def test_unconfirmed_signal_without_direction_is_not_relevant_to_directional_query(self) -> None:
        _set_id, payload = transit_evidence.build_evidence_set(
            session_id="s1",
            operation="service_status",
            route_ids=["Q"],
            direction="uptown",
            concerns=["stalled_train"],
            result={
                "source": "mta_service_alerts",
                "freshness": "live",
                "status": "no_active_alerts",
                "alerts": [],
                "gtfs_rt_coverage": "current",
                "unconfirmed_signals": [
                    {
                        "kind": "possible_delay",
                        "route_id": "Q",
                        "stop_id": "D28S",
                        "reason": "stale vehicle timestamp",
                        "confirmed": False,
                    }
                ],
            },
        )

        assert payload["unconfirmed_signals"] == []
        assert "uptown direction was not resolved" in " ".join(payload["unknowns"])

    def test_stalled_train_concern_keeps_matching_possible_signal(self) -> None:
        _set_id, payload = transit_evidence.build_evidence_set(
            session_id="s1",
            operation="service_status",
            route_ids=["Q"],
            direction="downtown",
            concerns=["stalled_train"],
            direction_resolution=resolve_direction(
                "downtown",
                [{"direction": "downtown"}],
            ),
            result={
                "source": "mta_service_alerts",
                "freshness": "live",
                "status": "active_alerts",
                "alerts": [],
                "gtfs_rt_coverage": "current",
                "unconfirmed_signals": [
                    {
                        "kind": "possible_stalled_train",
                        "route_id": "Q",
                        "mode": "subway",
                        "direction": "downtown",
                    }
                ],
            },
        )

        assert payload["unconfirmed_signals"][0]["kind"] == "possible_stalled_train"

    def test_typed_views_keep_incidents_stalls_and_source_freshness_separate(self) -> None:
        _set_id, payload = transit_evidence.build_evidence_set(
            session_id="s1",
            operation="service_status",
            route_ids=["Q"],
            result={
                "source": "mta_service_alerts",
                "freshness": "live",
                "observed_at": "2026-08-18T12:00:00Z",
                "status": "no_active_alerts",
                "alerts": [],
                "gtfs_rt_coverage": "current",
                "gtfs_rt_observed_at": "2026-08-18T11:59:00Z",
                "incident_coverage": "partial",
                "incidents": [
                    {
                        "incident_id": "inc-1",
                        "state": "confirmed",
                        "location_name": "Near Church Av",
                        "affected_route_ids": ["Q"],
                    }
                ],
                "unconfirmed_signals": [
                    {
                        "route_id": "Q",
                        "mode": "subway",
                        "stop_id": "D28S",
                        "direction": "downtown",
                    }
                ],
            },
        )

        assert "service_conditions" in payload
        assert "arrivals" in payload
        assert "direction" in payload
        assert payload["incidents"][0]["incident_id"] == "inc-1"
        assert payload["stalled_vehicles"][0]["mode"] == "subway"
        assert payload["observed_at"]["alerts"] == "2026-08-18T12:00:00Z"
        assert payload["freshness_by_source"]["gtfs_rt"]["observed_at"] == "2026-08-18T11:59:00Z"
        assert "partial or missing coverage" in " ".join(payload["unknowns"])

    def test_evidence_handle_is_immutable_after_first_snapshot(self) -> None:
        evidence_id, first = transit_evidence.build_evidence_set(
            session_id="s1",
            operation="service_status",
            route_ids=["Q"],
            evidence_set_id="te_immutable",
            result={
                "source": "mta_service_alerts",
                "freshness": "live",
                "status": "active_alerts",
                "alerts": [{"alert_id": "q1", "header": "Q delays", "route_ids": ["Q"]}],
            },
        )
        _same_id, second = transit_evidence.build_evidence_set(
            session_id="s1",
            operation="service_status",
            route_ids=["Q"],
            evidence_set_id=evidence_id,
            result={
                "source": "mta_service_alerts",
                "freshness": "live",
                "status": "active_alerts",
                "alerts": [{"alert_id": "q2", "header": "Q suspended", "route_ids": ["Q"]}],
            },
        )

        assert first["confirmed_matching_alerts"] == second["confirmed_matching_alerts"]
        assert first["confirmed_matching_alerts"][0]["alert_id"] == "q1"


if __name__ == "__main__":
    unittest.main()
