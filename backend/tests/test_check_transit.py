"""check_transit operation dispatch."""

from __future__ import annotations

import unittest
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

from app.services import cache, evidence
from app.services.agent import candidate_store, trip_state
from app.services.agent.tools._types import ToolContext, ToolOutcome, ToolResult
from app.services.agent.tools.transit import (
    check_transit,
    lookup_arrivals,
    present_transit,
    transit_snapshot,
)
from app.services.agent.turn.contract import GoalKind, OutcomeGoal, TurnContract
from app.services.agent.turn.evidence import TurnEvidence
from app.services.incidents import index as incident_index
from app.services.mta import realtime as mta_realtime


def _base_input(**overrides):
    payload = {
        "operation": "service_status",
        "route_ids": ["Q"],
        "stop_source": "auto",
        "stop_query": None,
        "direction": None,
        "area": None,
        "station": None,
        "topic": None,
        "event_query": None,
        "venue": None,
        "at": None,
        "window_start": None,
        "window_end": None,
    }
    payload.update(overrides)
    return payload


def _q_alert(alert_id: str = "lmm:planned_work:33095") -> dict:
    return {
        "source": "mta_service_alerts",
        "source_id": alert_id,
        "alert_id": alert_id,
        "header": "Q trains run local",
        "description": "In Manhattan, Q runs local in both directions",
        "route_ids": ["Q"],
        "direction_scope": "both_directions",
        "planned_status": "planned",
        "change_type": "express_to_local",
        "service_operating": True,
        "material_disruption": False,
    }


def _candidate_session(
    session_id: str,
    *,
    alert: dict | None = None,
    envelope: dict | None = None,
    incidents: list[dict] | None = None,
    signals: list[dict] | None = None,
) -> dict:
    alert = alert or _q_alert()
    if envelope is None:
        now = datetime.now(UTC)
        envelope = evidence.evidence_envelope(
            "mta_service_alerts",
            [alert],
            observed_at=now - timedelta(seconds=5),
            ttl_seconds=300,
        ).to_model_dict(empty=[])
    q_evidence = {
        "alerts": [alert],
        "incidents": incidents or [],
        "unconfirmed_material_claims": signals or [],
        "evidence_envelopes": {"alerts": envelope},
        "evidence_coverage": {"vehicles": "current", "incidents": "current"},
    }
    candidate_set_id = candidate_store.store_candidate_set(
        session_id=session_id,
        payload={
            "evidence_envelopes": {"alerts": envelope},
            "candidate_evidence": [{"alerts": []}, q_evidence],
            "candidates": [
                {"candidate_id": "candidate-2", "digest": {"transit_lines": ["2"]}},
                {"candidate_id": "candidate-q", "digest": {"transit_lines": ["Q"]}},
            ],
        },
    )
    session: dict = {}
    trip_state.bind_candidate_set(session, candidate_set_id)
    trip_state.bind_selected_candidate(session, "candidate-2")
    return session


def _serialized_alert_envelope(
    alert: dict,
    *,
    status: str = "stale",
    valid_until: datetime | None = None,
) -> dict:
    expires = valid_until or (datetime.now(UTC) - timedelta(seconds=30))
    observed = datetime.now(UTC) - timedelta(minutes=2)
    return {
        "source": "mta_service_alerts",
        "observedAt": observed.isoformat(),
        "status": status,
        "validUntil": expires.isoformat(),
        "payload": [alert],
    }


def _direction_candidate_session(
    session_id: str,
    q_directions: tuple[str, ...] = ("uptown",),
    q_headsign: str | None = None,
) -> dict:
    def itinerary(route: str, direction: str, headsign: str | None = None) -> dict:
        return {
            "legs": [
                {
                    "mode": "SUBWAY",
                    "service_id": route,
                    "direction": direction,
                    "headsign": headsign or direction,
                }
            ]
        }

    candidates = [
        {
            "candidate_id": "candidate-2",
            "digest": {
                "transit_lines": ["2"],
                "_canonical_itinerary": itinerary("2", "downtown"),
            },
        }
    ]
    evidence_rows = [{"alerts": []}]
    for index, direction in enumerate(q_directions, start=1):
        candidates.append(
            {
                "candidate_id": f"candidate-q-{index}",
                "digest": {
                    "transit_lines": ["Q"],
                    "_canonical_itinerary": itinerary("Q", direction, q_headsign),
                },
            }
        )
        evidence_rows.append({"alerts": []})
    candidate_set_id = candidate_store.store_candidate_set(
        session_id=session_id,
        payload={"candidates": candidates, "candidate_evidence": evidence_rows},
    )
    session: dict = {}
    trip_state.bind_candidate_set(session, candidate_set_id)
    trip_state.bind_selected_candidate(session, "candidate-2")
    return session


class CheckTransitTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        cache._mem.clear()

    async def test_service_status_does_not_call_arrivals(self):
        evidence = TurnEvidence()
        ctx = ToolContext(session_id="s", turn_evidence=evidence)
        status = ToolResult(ok=True, data={"alerts": []}, summary="Q is running")
        with (
            patch.object(transit_snapshot, "execute", new=AsyncMock(return_value=status)) as snap,
            patch.object(lookup_arrivals, "execute", new=AsyncMock()) as arrivals,
            patch.object(
                mta_realtime,
                "get_stalled_trains",
                new=AsyncMock(return_value=[]),
            ),
            patch.object(
                incident_index,
                "lookup_incidents",
                return_value={"incidents": [], "coverage_status": "current"},
            ),
        ):
            result = await check_transit.execute(
                _base_input(direction="uptown"), ctx
            )
        assert result.ok
        snap.assert_awaited_once()
        arrivals.assert_not_called()
        assert evidence.transit_evidence

    async def test_stalled_train_scope_keeps_delay_and_unconfirmed_train_signal(self):
        status = ToolResult(
            ok=True,
            data={
                "source": "mta_service_alerts",
                "freshness": "live",
                "status": "active_alerts",
                "alerts": [
                    {
                        "header": "Q delay",
                        "route_ids": ["Q"],
                        "kind": "delay",
                    }
                ],
                "unconfirmed_signals": [
                    {
                        "route_id": "Q",
                        "kind": "stalled_train",
                        "direction": "uptown",
                    }
                ],
            },
        )
        with patch.object(
            check_transit,
            "collect_service_status",
            new=AsyncMock(return_value=status),
        ):
            result = await check_transit.execute(
                _base_input(direction="uptown", concerns=["stalled_train"]),
                ToolContext(session_id="s"),
            )

        assert result.ok, result.error
        evidence = result.data["evidence"]
        assert evidence["concerns"] == ["stalled_train", "delay"]
        assert evidence["confirmed_matching_alerts"][0]["header"] == "Q delay"
        assert evidence["unconfirmed_signals"][0]["kind"] == "stalled_train"

    async def test_arrivals_requires_route_id(self):
        result = await check_transit.execute(
            _base_input(operation="arrivals", route_ids=[]),
            ToolContext(session_id="s"),
        )
        assert not result.ok
        assert "route_id" in (result.error or "")

    async def test_multi_route_arrivals_merge_grounded_results(self):
        lookup_results = [
            ToolResult(
                ok=True,
                data={"route_id": route, "source_status": "complete", "predictions": []},
                timings={"lookup_ms": duration},
            )
            for route, duration in (("Q", 2.0), ("R", 3.0))
        ]
        with patch.object(
            lookup_arrivals,
            "execute",
            new=AsyncMock(side_effect=lookup_results),
        ) as lookup:
            result = await check_transit.execute(
                _base_input(
                    operation="arrivals",
                    route_ids=["Q", "R"],
                    direction="uptown",
                ),
                ToolContext(session_id="multi-arrivals", turn_id="t1"),
            )

        assert result.ok, result.error
        assert lookup.await_count == 2
        assert "evidence_set_id" in result.data
        assert len(result.data["result"]["results"]) == 2
        assert result.timings["lookup_ms"] == 5.0

    async def test_multi_route_arrivals_propagate_leaf_failure(self):
        with patch.object(
            lookup_arrivals,
            "execute",
            new=AsyncMock(
                side_effect=[
                    ToolResult(ok=True, data={"source_status": "complete"}),
                    ToolResult(ok=False, error="provider unavailable"),
                ]
            ),
        ):
            result = await check_transit.execute(
                _base_input(
                    operation="arrivals",
                    route_ids=["Q", "R"],
                    direction="uptown",
                ),
                ToolContext(session_id="multi-arrivals"),
            )

        assert not result.ok
        assert result.error == "provider unavailable"

    async def test_multi_route_arrivals_keep_clarification_when_none_are_grounded(self):
        with patch.object(
            lookup_arrivals,
            "execute",
            new=AsyncMock(
                side_effect=[
                    ToolResult(
                        ok=True,
                        outcome=ToolOutcome.NEEDS_CLARIFICATION,
                        data={"source_status": "stop_not_resolved"},
                    ),
                    ToolResult(
                        ok=True,
                        outcome=ToolOutcome.UNAVAILABLE,
                        data={"source_status": "provider_unavailable"},
                    ),
                ]
            ),
        ):
            result = await check_transit.execute(
                _base_input(
                    operation="arrivals",
                    route_ids=["Q", "R"],
                    direction="uptown",
                ),
                ToolContext(session_id="multi-arrivals"),
            )

        assert result.ok
        assert result.outcome == ToolOutcome.NEEDS_CLARIFICATION
        assert "evidence_set_id" not in result.data

    async def test_event_and_venue_operations_are_wrapped_as_grounded_evidence(self):
        event_result = ToolResult(ok=True, data={"events": [{"name": "Concert"}]})
        venue_result = ToolResult(
            ok=True,
            data={
                "venue": "MSG",
                "surge_start_iso": "2026-08-24T22:00:00-04:00",
                "surge_end_iso": "2026-08-25T00:00:00-04:00",
            },
        )
        with (
            patch.object(
                check_transit,
                "execute_event_lookup",
                new=AsyncMock(return_value=event_result),
            ),
            patch.object(check_transit.venues, "execute", new=AsyncMock(return_value=venue_result)),
        ):
            event = await check_transit.execute(
                _base_input(operation="event_schedule", event_query="concert"),
                ToolContext(session_id="events"),
            )
            venue = await check_transit.execute(
                _base_input(
                    operation="venue_crowd_window",
                    venue="MSG",
                    window_end="2026-08-24T22:00:00-04:00",
                ),
                ToolContext(session_id="events"),
            )

        assert "evidence_set_id" in event.data
        assert "evidence_set_id" in venue.data

    async def test_unresolved_arrival_attempt_does_not_count_as_grounding(self):
        evidence = TurnEvidence()
        evidence.bind_contract(
            TurnContract((OutcomeGoal("arrival", GoalKind.ARRIVALS),))
        )
        unresolved = ToolResult(
            ok=True,
            data={
                "source_status": "stop_not_resolved",
                "ambiguity": [{"stop_name": "34 St-Herald Sq"}],
            },
            summary="station clarification required",
        )
        with patch.object(
            lookup_arrivals,
            "execute",
            new=AsyncMock(return_value=unresolved),
        ):
            result = await check_transit.execute(
                _base_input(
                    operation="arrivals",
                    route_ids=["Q"],
                    stop_query="34 St",
                ),
                ToolContext(session_id="s", turn_evidence=evidence),
            )

        assert result.ok
        assert result.outcome == ToolOutcome.NEEDS_CLARIFICATION
        assert "evidence_set_id" not in result.data
        assert result.events == []
        assert not evidence.transit_evidence

    async def test_destination_headsign_resolves_against_active_trip_and_scopes_signal(self):
        status = ToolResult(
            ok=True,
            data={
                "source": "mta_service_alerts",
                "freshness": "live",
                "status": "no_active_alerts",
                "alerts": [],
            },
            summary="Q is running",
        )
        ctx = ToolContext(
            session_id="s",
            session={
                "active_trip": {
                    "first_boarding": {
                        "route_id": "Q",
                        "direction_id": 1,
                        "direction": "downtown",
                        "direction_label": "Coney Island-Stillwell Av",
                    }
                }
            },
        )
        with (
            patch.object(transit_snapshot, "execute", new=AsyncMock(return_value=status)),
            patch.object(
                mta_realtime,
                "get_stalled_trains",
                new=AsyncMock(
                    return_value=[
                        {
                            "route_id": "Q",
                            "stop_id": "D28S",
                            "stalled_minutes": 4,
                        }
                    ]
                ),
            ),
            patch.object(
                incident_index,
                "lookup_incidents",
                return_value={"incidents": [], "coverage_status": "current"},
            ),
        ):
            result = await check_transit.execute(
                _base_input(direction="Coney Island-Stillwell Av"), ctx
            )

        assert result.ok
        evidence = result.data["evidence"]
        assert evidence["direction_scope"]["resolved"] == "downtown"
        assert evidence["direction_scope"]["authoritative"]
        assert evidence["unconfirmed_signals"][0]["direction"] == "downtown"

    async def test_service_status_uses_bus_stalled_source_for_bus_routes(self):
        status = ToolResult(
            ok=True,
            data={
                "source": "mta_service_alerts",
                "freshness": "live",
                "status": "no_active_alerts",
                "alerts": [],
            },
            summary="M15 is running",
        )
        with (
            patch.object(transit_snapshot, "execute", new=AsyncMock(return_value=status)),
            patch.object(
                mta_realtime,
                "get_stalled_trains",
                new=AsyncMock(return_value=[]),
            ) as stalled_trains,
            patch.object(
                mta_realtime,
                "get_stalled_buses",
                new=AsyncMock(
                    return_value=[
                        {
                            "route_id": "M15",
                            "direction": "downtown",
                            "time_recorded": "2026-08-17T12:00:00Z",
                        }
                    ]
                ),
            ) as stalled_buses,
            patch.object(
                incident_index,
                "lookup_incidents",
                return_value={"incidents": [], "coverage_status": "current"},
            ),
        ):
            result = await check_transit.execute(
                _base_input(route_ids=["M15"], direction="downtown"),
                ToolContext(session_id="s"),
            )

        assert result.ok
        stalled_trains.assert_not_awaited()
        stalled_buses.assert_awaited_once_with({"M15"})
        evidence = result.data["evidence"]
        assert evidence["source_coverage"]["bustime"] == "partial"
        assert evidence["unconfirmed_signals"][0]["route_id"] == "M15"

    async def test_unresolved_destination_returns_structured_direction_clarification(self):
        result = await check_transit.execute(
            _base_input(direction="Coney Island"),
            ToolContext(session_id="s"),
        )

        assert result.ok
        assert result.data["status"] == "clarification_required"
        assert result.data["clarification"]["kind"] == "transit_direction"
        assert "uptown or downtown" in result.data["clarification"]["question"]

    async def test_route_status_without_resolved_direction_runs_linewide_check(self):
        status = ToolResult(
            ok=True,
            data={
                "source": "mta_service_alerts",
                "freshness": "live",
                "status": "no_active_alerts",
                "alerts": [],
            },
        )
        with patch.object(
            check_transit, "collect_service_status", new=AsyncMock(return_value=status)
        ) as collect:
            result = await check_transit.execute(
                _base_input(direction=None), ToolContext(session_id="s")
            )

        assert result.ok
        assert result.data.get("status") != "clarification_required"
        collect.assert_awaited_once()
        assert collect.await_args.args[1]["direction"] is None

    async def test_systemwide_status_does_not_require_direction(self):
        status = ToolResult(
            ok=True,
            data={
                "source": "mta_service_alerts",
                "freshness": "live",
                "status": "active_alerts",
                "alerts": [
                    {"header": "Q delays", "route_ids": ["Q"]},
                ],
            },
            summary="Systemwide alerts",
        )
        with patch.object(
            transit_snapshot, "execute", new=AsyncMock(return_value=status)
        ) as snapshot:
            result = await check_transit.execute(
                _base_input(route_ids=[], direction=None), ToolContext(session_id="s")
            )

        assert result.ok
        assert "evidence" in result.data
        snapshot.assert_awaited_once()

    async def test_service_status_reuses_current_nonselected_candidate_evidence(self):
        session = _candidate_session("reuse-session")
        ctx = ToolContext(session_id="reuse-session", session=session)
        with (
            patch.object(transit_snapshot, "execute", new=AsyncMock()) as snapshot,
            patch.object(
                mta_realtime, "fetch_service_alerts", new=AsyncMock()
            ) as fetch,
            patch.object(
                mta_realtime,
                "get_stalled_trains",
                new=AsyncMock(),
            ) as stalled,
            patch.object(
                incident_index,
                "lookup_incidents",
                return_value={"incidents": [], "coverage_status": "current"},
            ) as incidents,
        ):
            result = await check_transit.execute(
                _base_input(direction="uptown"), ctx
            )

        assert result.ok, result.error
        snapshot.assert_not_awaited()
        fetch.assert_not_awaited()
        stalled.assert_not_awaited()
        incidents.assert_not_called()
        evidence_payload = result.data["evidence"]
        assert evidence_payload["confirmed_matching_alerts"][0]["source_id"] == "lmm:planned_work:33095"
        assert evidence_payload["freshness"]["origin"] == "accepted_candidate_evidence"

    async def test_reused_candidate_evidence_projects_only_requested_route_facts(self):
        session = _candidate_session(
            "reuse-facts",
            incidents=[
                {
                    "incident_id": "incident-q",
                    "location_name": "Canal St",
                    "description": "Police investigation",
                    "affected_route_ids": ["Q"],
                    "state": "confirmed",
                    "direction": "uptown",
                },
                {
                    "incident_id": "incident-a",
                    "location_name": "Fulton St",
                    "affected_route_ids": ["A"],
                },
            ],
            signals=[
                {
                    "kind": "possible_delay_unconfirmed",
                    "route_id": "Q",
                    "mode": "subway",
                    "location": "Canal St",
                },
                {
                    "kind": "possible_delay_unconfirmed",
                    "route_id": "A",
                    "mode": "subway",
                    "location": "Fulton St",
                },
            ],
        )

        result = await check_transit.execute(
            _base_input(direction="uptown"),
            ToolContext(session_id="reuse-facts", session=session),
        )

        assert result.ok, result.error
        evidence_payload = result.data["evidence"]
        assert [row["incident_id"] for row in evidence_payload["incidents"]] == ["incident-q"]
        assert [row["route_id"] for row in evidence_payload["unconfirmed_signals"]] == ["Q"]
        assert not evidence_payload["unconfirmed_signals"][0]["confirmed"]

    async def test_candidate_evidence_ownership_and_freshness_fail_closed(self):
        cases = (
            ("wrong-owner", "owner", "wrong-owner", None),
            (
                "expired",
                "expired",
                "expired",
                _serialized_alert_envelope(_q_alert()),
            ),
            (
                "malformed",
                "malformed",
                "malformed",
                {
                    **_serialized_alert_envelope(_q_alert()),
                    "status": "current",
                    "validUntil": "not-a-timestamp",
                },
            ),
        )
        for label, owner, requestor, serialized in cases:
            with self.subTest(case=label):
                session = _candidate_session(
                    owner,
                    envelope=serialized,
                ) if serialized is not None else _candidate_session(owner)
                ctx = ToolContext(session_id=requestor, session=session)
                status = ToolResult(
                    ok=True,
                    data={
                        "source": "mta_service_alerts",
                        "freshness": "live",
                        "status": "no_active_alerts",
                        "alerts": [],
                    },
                )
                with patch.object(
                    transit_snapshot, "execute", new=AsyncMock(return_value=status)
                ) as snapshot:
                    result = await check_transit.execute(
                        _base_input(direction="uptown"), ctx
                    )
                assert result.ok
                snapshot.assert_awaited_once()

    async def test_stale_candidate_evidence_compares_same_and_new_alert_ids(self):
        old_alert = _q_alert("lmm:planned_work:old")
        new_alert = _q_alert("lmm:planned_work:new")
        for refreshed, changed in ((old_alert, False), (new_alert, True)):
            with self.subTest(changed=changed):
                session_id = f"stale-{changed}"
                session = _candidate_session(
                    session_id,
                    alert=old_alert,
                    envelope=_serialized_alert_envelope(old_alert),
                )
                ctx = ToolContext(session_id=session_id, session=session)
                status = ToolResult(
                    ok=True,
                    data={
                        "source": "mta_service_alerts",
                        "freshness": "live",
                        "status": "active_alerts",
                        "alerts": [refreshed],
                    },
                )
                with (
                    patch.object(
                        transit_snapshot, "execute", new=AsyncMock(return_value=status)
                    ) as snapshot,
                    patch.object(
                mta_realtime,
                        "get_stalled_trains",
                        new=AsyncMock(return_value=[]),
                    ),
                    patch.object(
                        incident_index,
                        "lookup_incidents",
                        return_value={"incidents": [], "coverage_status": "current"},
                    ),
                ):
                    result = await check_transit.execute(
                        _base_input(direction="uptown"), ctx
                    )
                assert result.ok, result.error
                snapshot.assert_awaited_once()
                marker = result.data["evidence"]["freshness"].get("continuity")
                assert marker == {"comparable": True, "changed": changed}

    async def test_nonofficial_candidate_alert_does_not_become_official(self):
        social_alert = {
            "source": "social",
            "source_id": "social-q",
            "header": "Q rider report",
            "route_ids": ["Q"],
        }
        session = _candidate_session(
            "nonofficial",
            alert=social_alert,
            envelope=_serialized_alert_envelope(social_alert),
        )
        status = ToolResult(
            ok=True,
            data={
                "source": "mta_service_alerts",
                "freshness": "live",
                "status": "no_active_alerts",
                "alerts": [],
            },
        )
        with patch.object(
            transit_snapshot, "execute", new=AsyncMock(return_value=status)
        ) as snapshot:
            result = await check_transit.execute(
                _base_input(direction="uptown"),
                ToolContext(session_id="nonofficial", session=session),
            )
        assert result.ok
        snapshot.assert_awaited_once()
        assert result.data["evidence"]["confirmed_matching_alerts"] == []

    async def test_explicit_direction_overrides_candidate_direction(self):
        session = _direction_candidate_session("explicit-direction")
        status = ToolResult(ok=True, data={"alerts": []})
        with patch.object(
            check_transit, "collect_service_status", new=AsyncMock(return_value=status)
        ) as collect:
            result = await check_transit.execute(
                _base_input(direction="downtown"),
                ToolContext(session_id="explicit-direction", session=session),
            )

        assert result.ok
        assert collect.await_args.args[1]["direction"] == "downtown"

    async def test_explicit_headsign_resolves_against_exact_candidate_leg(self):
        session = _direction_candidate_session(
            "explicit-headsign", ("downtown",), q_headsign="Coney Island"
        )
        status = ToolResult(ok=True, data={"alerts": []})
        with patch.object(
            check_transit, "collect_service_status", new=AsyncMock(return_value=status)
        ) as collect:
            result = await check_transit.execute(
                _base_input(direction="Coney Island"),
                ToolContext(session_id="explicit-headsign", session=session),
            )

        assert result.ok
        assert collect.await_args.args[1]["direction"] == "downtown"

    async def test_explicit_headsign_never_substitutes_candidate_direction(self):
        cases = (
            ("unmatched-headsign", ("uptown",), None),
            ("conflicting-headsign", ("uptown", "downtown"), "Coney Island"),
        )
        for session_id, directions, headsign in cases:
            with self.subTest(session_id=session_id):
                session = _direction_candidate_session(
                    session_id,
                    directions,
                    q_headsign=headsign,
                )
                with patch.object(
                    check_transit,
                    "collect_service_status",
                    new=AsyncMock(),
                ) as collect:
                    result = await check_transit.execute(
                        _base_input(direction="Coney Island"),
                        ToolContext(session_id=session_id, session=session),
                    )

                assert result.ok
                assert result.data["status"] == "clarification_required"
                assert result.data["clarification"]["kind"] == "transit_direction"
                collect.assert_not_awaited()

    async def test_nonselected_q_candidate_direction_is_used_and_presentable(self):
        session = _direction_candidate_session("candidate-direction")
        planned_alert = _q_alert()
        planned_alert["header"] = "Q trains run local"
        status = ToolResult(
            ok=True,
            data={
                "source": "mta_service_alerts",
                "freshness": "live",
                "status": "active_alerts",
                "gtfs_rt_coverage": "current",
                "incident_coverage": "current",
                "alerts": [planned_alert],
            },
        )
        with patch.object(
            check_transit, "collect_service_status", new=AsyncMock(return_value=status)
        ) as collect:
            result = await check_transit.execute(
                _base_input(direction=None),
                ToolContext(session_id="candidate-direction", session=session),
            )

        assert result.ok
        assert collect.await_args.args[1]["direction"] == "uptown"
        presented = await present_transit.execute(
            {"evidence_set_id": result.data["evidence_set_id"], "goal_key": "status"},
            ToolContext(session_id="candidate-direction", session=session),
        )
        assert presented.ok, presented.error
        passenger_text = presented.data["passenger_text"]
        assert "Official planned service change" in passenger_text
        assert "does not confirm the requested direction" not in passenger_text

    async def test_accepted_active_trip_direction_is_fallback(self):
        session = {
            "active_trip": {
                "first_boarding": {"route_id": "Q", "direction": "downtown"}
            }
        }
        status = ToolResult(ok=True, data={"alerts": []})
        with patch.object(
            check_transit, "collect_service_status", new=AsyncMock(return_value=status)
        ) as collect:
            result = await check_transit.execute(
                _base_input(direction=None),
                ToolContext(session_id="accepted-direction", session=session),
            )

        assert result.ok
        assert collect.await_args.args[1]["direction"] == "downtown"

    async def test_conflicting_candidate_directions_do_not_guess(self):
        session = _direction_candidate_session(
            "conflicting-directions", ("uptown", "downtown")
        )
        status = ToolResult(ok=True, data={"alerts": []})
        with patch.object(
            check_transit, "collect_service_status", new=AsyncMock(return_value=status)
        ) as collect:
            result = await check_transit.execute(
                _base_input(direction=None),
                ToolContext(session_id="conflicting-directions", session=session),
            )

        assert result.ok
        assert result.data.get("status") != "clarification_required"
        assert collect.await_args.args[1]["direction"] is None

    async def test_arrivals_without_resolved_direction_clarifies(self):
        with patch.object(lookup_arrivals, "execute", new=AsyncMock()) as lookup:
            result = await check_transit.execute(
                _base_input(operation="arrivals", direction=None),
                ToolContext(session_id="unresolved-arrivals"),
            )

        assert result.ok
        assert result.data["status"] == "clarification_required"
        assert result.data["clarification"]["kind"] == "transit_direction"
        lookup.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
