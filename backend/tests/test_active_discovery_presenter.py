"""Existing discovery facts can be presented without a second provider call."""

from __future__ import annotations

import unittest

from app.services import cache
from app.services.agent import (
    discovery_store,
    public_surface,
    tool_input_policy,
    trip_state,
)
from app.services.agent.tools._types import ToolContext
from app.services.agent.tools.places import present_places
from app.services.agent.turn.contract import (
    GoalKind,
    GoalState,
    OutcomeGoal,
    TurnContract,
)
from app.services.agent.turn.evidence import TurnEvidence


class ActiveDiscoveryPresenterTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        cache._mem.clear()

    def _session_with_discovery(self, session_id: str) -> tuple[dict, str, dict]:
        session = {"trip_state": trip_state.empty_trip_state()}
        set_id = discovery_store.store_discovery_set(
            session_id=session_id,
            query="pizza",
            search_scope={"kind": "current_location", "values": []},
            places=[
                {
                    "name": "Stored Pizza",
                    "address": "1 Main St, Brooklyn, NY",
                    "borough": "Brooklyn",
                    "rating": 4.7,
                }
            ],
        )
        trip_state.bind_discovery_set(session, set_id)
        record = discovery_store.load_discovery_set(set_id, session_id=session_id)
        assert record is not None
        return session, set_id, record

    def _pending_place_evidence(self) -> TurnEvidence:
        evidence = TurnEvidence()
        evidence.bind_contract(
            TurnContract((OutcomeGoal("place", GoalKind.DESTINATION_SELECTION),))
        )
        return evidence

    def _blocked_place_evidence(self) -> TurnEvidence:
        evidence = TurnEvidence()
        evidence.bind_contract(
            TurnContract(
                (
                    OutcomeGoal("preference", GoalKind.GENERAL_RESPONSE),
                    OutcomeGoal(
                        "place",
                        GoalKind.DESTINATION_SELECTION,
                        ("preference",),
                    ),
                )
            )
        )
        return evidence

    def test_destination_selection_for_route_does_not_offer_place_presenter(self) -> None:
        session, _set_id, _record = self._session_with_discovery("sess-route")
        evidence = TurnEvidence()
        evidence.bind_contract(
            TurnContract(
                (
                    OutcomeGoal("destination", GoalKind.DESTINATION_SELECTION),
                    OutcomeGoal(
                        "route",
                        GoalKind.ROUTE,
                        depends_on=("destination",),
                    ),
                )
            )
        )

        names = public_surface.state_valid_tool_names(
            evidence,
            session=session,
            session_id="sess-route",
        )

        assert "discover_places" in names
        assert "present_places" not in names

    def test_route_owned_discovery_is_allowed_without_destination_dependency(self) -> None:
        evidence = TurnEvidence()
        evidence.bind_contract(
            TurnContract((OutcomeGoal("route", GoalKind.ROUTE),))
        )
        ctx = ToolContext(
            session={},
            session_id="sess-route",
            turn_evidence=evidence,
        )

        assert tool_input_policy.goal_error("discover_places", {"goal_key": "route"}, ctx) is None

    def test_explicit_destination_dependency_keeps_discovery_on_dependency_goal(self) -> None:
        evidence = TurnEvidence()
        evidence.bind_contract(
            TurnContract(
                (
                    OutcomeGoal("destination", GoalKind.DESTINATION_SELECTION),
                    OutcomeGoal("route", GoalKind.ROUTE, ("destination",)),
                )
            )
        )
        ctx = ToolContext(
            session={},
            session_id="sess-route",
            turn_evidence=evidence,
        )

        assert tool_input_policy.goal_error("discover_places", {"goal_key": "route"}, ctx) == "discover_places cannot satisfy the declared route outcome"

    def test_active_owned_discovery_adds_presenter_for_pending_place_goal(self) -> None:
        session, set_id, _record = self._session_with_discovery("sess-a")
        evidence = self._pending_place_evidence()

        names = public_surface.state_valid_tool_names(
            evidence,
            session=session,
            session_id="sess-a",
        )

        assert "present_places" in names
        assert "discover_places" in names
        assert set_id != ""

    def test_stale_or_cross_session_discovery_does_not_add_presenter(self) -> None:
        session, _set_id, _record = self._session_with_discovery("sess-a")
        evidence = self._pending_place_evidence()

        assert "present_places" not in public_surface.state_valid_tool_names(evidence, session=session, session_id="sess-b")
        session["trip_state"]["active_discovery_set_id"] = "ds_expired"
        assert "present_places" not in public_surface.state_valid_tool_names(evidence, session=session, session_id="sess-a")

    async def test_presenter_binds_owned_active_discovery_without_provider_call(self) -> None:
        session, set_id, record = self._session_with_discovery("sess-a")
        evidence = self._pending_place_evidence()
        place_id = record["places"][0]["place_id"]

        result = await present_places.execute(
            {
                "discovery_set_id": set_id,
                "selections": [
                    {"place_id": place_id, "reason": "top_pick"},
                ],
                "research_used": False,
                "goal_key": "place",
            },
            ToolContext(
                session=session,
                session_id="sess-a",
                turn_id="t-present",
                turn_evidence=evidence,
            ),
        )

        assert result.ok
        assert [event.type for event in result.events] == ["token", "sources"]
        assert result.events[-1].to_data() == {"sources": [{"title": "Google Maps", "url": "https://www.google.com/maps"}]}
        assert evidence.handle_for("place") == set_id
        assert evidence.state_for("place") == GoalState.SATISFIED
        assert "Stored Pizza" in result.data["passenger_text"]

    async def test_executor_does_not_bypass_unresolved_goal_dependencies(self) -> None:
        session, set_id, record = self._session_with_discovery("sess-a")
        evidence = self._blocked_place_evidence()

        result = await present_places.execute(
            {
                "discovery_set_id": set_id,
                "selections": [
                    {
                        "place_id": record["places"][0]["place_id"],
                        "reason": "top_pick",
                    }
                ],
                "research_used": False,
                "goal_key": "place",
            },
            ToolContext(
                session=session,
                session_id="sess-a",
                turn_evidence=evidence,
            ),
        )

        assert not result.ok
        assert evidence.handle_for("place") is None
        assert evidence.state_for("place") == GoalState.PENDING

    def test_runtime_policy_allows_only_the_owned_active_discovery(self) -> None:
        session, set_id, record = self._session_with_discovery("sess-a")
        evidence = self._pending_place_evidence()
        place_id = record["places"][0]["place_id"]
        tool_input = {
            "discovery_set_id": set_id,
            "selections": [{"place_id": place_id, "reason": "top_pick"}],
            "research_used": False,
            "goal_key": "place",
        }

        assert tool_input_policy.goal_error("present_places", tool_input, ToolContext(session=session, session_id="sess-a", turn_evidence=evidence)) is None
        assert tool_input_policy.goal_error("present_places", tool_input, ToolContext(session=session, session_id="sess-b", turn_evidence=evidence)) == "presenter requires ready server-owned evidence"

    async def test_research_details_match_stable_place_id_across_discovery_sets(self):
        session = {}
        first_set = discovery_store.store_discovery_set(
            session_id="sess-pres",
            session=session,
            query="pizza",
            places=[
                {
                    "name": "L'Industrie",
                    "address": "1 Main St, Brooklyn, NY",
                    "provider_place_id": "provider-li",
                }
            ],
        )
        first_record = discovery_store.load_discovery_set(
            first_set, session_id="sess-pres"
        )
        place_id = first_record["places"][0]["place_id"]
        initial = await present_places.execute(
            {
                "discovery_set_id": first_set,
                "selections": [{"place_id": place_id, "reason": "top_pick"}],
                "research_used": False,
            },
            ToolContext(
                session=session,
                session_id="sess-pres",
                turn_id="t-initial",
                agent_mode="auto",
                turn_evidence=TurnEvidence(),
            ),
        )
        assert initial.ok

        second_set = discovery_store.store_discovery_set(
            session_id="sess-pres",
            session=session,
            query="L'Industrie",
            places=[
                {
                    "name": "L'Industrie",
                    "address": "1 Main St, Brooklyn, NY",
                    "provider_place_id": "provider-li",
                }
            ],
        )
        second_record = discovery_store.load_discovery_set(
            second_set, session_id="sess-pres"
        )
        assert second_record["places"][0]["place_id"] == place_id
        evidence = TurnEvidence()
        evidence.bind_contract(
            TurnContract((OutcomeGoal("place", GoalKind.PLACE_RECOMMENDATION),))
        )
        evidence.record_goal_handle("place", second_set)
        evidence.record_goal("place", GoalState.EVIDENCE_READY, attempted=True)
        evidence.web_research_required = True
        evidence.note_web(ok=True)
        missing_research = await present_places.execute(
            {
                "discovery_set_id": second_set,
                "selections": [{"place_id": place_id, "reason": "top_pick"}],
                "research_used": False,
                "goal_key": "place",
                "lead_in": "",
                "follow_up": "",
            },
            ToolContext(
                session=session,
                session_id="sess-pres",
                turn_id="t-missing-research",
                agent_mode="auto",
                turn_evidence=evidence,
            ),
        )
        assert not missing_research.ok
        assert "require current-turn research" in missing_research.error
        details = await present_places.execute(
            {
                "discovery_set_id": second_set,
                "selections": [{"place_id": place_id, "reason": "top_pick"}],
                "research_used": True,
                "goal_key": "place",
                "lead_in": (
                    "The sourdough crust is a local favorite, with a crisp edge "
                    "and a light center that holds up well under the toppings. "
                    "Current recommendations often point to the burrata slice "
                    "for something rich and the fig-and-bacon slice for a sweet, "
                    "savory contrast. It is counter service, so the practical "
                    "choice is to order a few slices and share."
                ),
                "follow_up": "",
            },
            ToolContext(
                session=session,
                session_id="sess-pres",
                turn_id="t-details",
                agent_mode="auto",
                turn_evidence=evidence,
            ),
        )
        assert details.ok
        assert [event.type for event in details.events] == ["token"]
        visible = "".join(event.text for event in details.events)
        assert "sourdough" in visible
        assert "L'Industrie" not in visible
        assert details.data["presented"] == [{"place_id": place_id, "reason": "preference_match"}]
        assert evidence.state_for("place") == GoalState.SATISFIED


if __name__ == "__main__":
    unittest.main()
