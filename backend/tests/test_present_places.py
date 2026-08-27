"""present_places validation and one canonical passenger list."""

from __future__ import annotations

import unittest

from app.services import cache
from app.services.agent import discovery_store, trip_state
from app.services.agent.tools._types import ToolContext
from app.services.agent.tools.places import present_places
from app.services.agent.turn.completion import evaluate_completion
from app.services.agent.turn.contract import (
    GoalKind,
    GoalState,
    OutcomeGoal,
    TurnContract,
)
from app.services.agent.turn.evidence import TurnEvidence

GOOGLE_MAPS_SOURCE_DATA = {
    "sources": [
        {
            "title": "Google Maps",
            "url": "https://www.google.com/maps",
        }
    ]
}


def _ctx(evidence=None) -> ToolContext:
    return ToolContext(
        session={},
        session_id="sess-pres",
        turn_id="t-pres",
        agent_mode="auto",
        turn_evidence=evidence or TurnEvidence(),
    )


class PresentPlacesTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        cache._mem.clear()

    def _store(self):
        return discovery_store.store_discovery_set(
            session_id="sess-pres",
            query="pizza",
            search_scope={"kind": "boroughs", "values": ["Manhattan"]},
            places=[
                {
                    "name": "Best Slice",
                    "address": "1 Main St, Manhattan, NY",
                    "borough": "Manhattan",
                    "rating": 4.8,
                    "review_count": 900,
                    "price_level": 1,
                    "open_status": "open",
                },
                {
                    "name": "Other Slice",
                    "address": "2 Main St, Manhattan, NY",
                    "borough": "Manhattan",
                    "rating": 4.1,
                    "review_count": 40,
                    "price_level": 3,
                    "open_status": "closed",
                },
            ],
        )

    def _details_evidence(self, set_id: str, *, researched: bool = True) -> TurnEvidence:
        evidence = TurnEvidence()
        evidence.bind_contract(
            TurnContract((OutcomeGoal("details", GoalKind.PLACE_RECOMMENDATION),))
        )
        evidence.record_goal_handle("details", set_id)
        evidence.record_goal("details", GoalState.EVIDENCE_READY, attempted=True)
        if researched:
            evidence.note_web(ok=True)
        return evidence

    def test_schema_requires_bounded_presentation_mode(self):
        schema = present_places.PRESENT_PLACES_SCHEMA["input_schema"]
        mode = schema["properties"]["presentation_mode"]
        assert mode["enum"] == ["recommendations", "details"]
        assert "presentation_mode" in schema["required"]

    async def test_recommendations_reject_cross_turn_replay_but_details_allows_one(self):
        session = {}
        set_id = self._store()
        record = discovery_store.load_discovery_set(set_id, session_id="sess-pres")
        place_id = record["places"][0]["place_id"]
        first = await present_places.execute(
            {
                "discovery_set_id": set_id,
                "selections": [{"place_id": place_id, "reason": "top_pick"}],
                "research_used": False,
            },
            ToolContext(session=session, session_id="sess-pres", turn_id="t1"),
        )
        assert first.ok

        replay = await present_places.execute(
            {
                "discovery_set_id": set_id,
                "selections": [{"place_id": place_id, "reason": "top_pick"}],
                "research_used": False,
                "presentation_mode": "recommendations",
            },
            ToolContext(session=session, session_id="sess-pres", turn_id="t2"),
        )
        assert not replay.ok
        assert "cannot repeat" in (replay.error or "")

        presented_before_details = discovery_store.presented_entity_registry(session)
        details = await present_places.execute(
            {
                "discovery_set_id": set_id,
                "selections": [{"place_id": place_id, "reason": "preference_match"}],
                "research_used": False,
                "presentation_mode": "details",
                "lead_in": "Here are the stored details for this place.",
            },
            ToolContext(session=session, session_id="sess-pres", turn_id="t3"),
        )
        assert not details.ok
        assert "successful current-turn research" in (details.error or "")
        assert details.events == []
        assert discovery_store.presented_entity_registry(session) == presented_before_details

        researched = TurnEvidence()
        researched.note_web(ok=True)
        details = await present_places.execute(
            {
                "discovery_set_id": set_id,
                "selections": [{"place_id": place_id, "reason": "preference_match"}],
                "research_used": True,
                "presentation_mode": "details",
                "lead_in": "Current listings highlight the house pie.",
            },
            ToolContext(
                session=session,
                session_id="sess-pres",
                turn_id="t3-researched",
                turn_evidence=researched,
            ),
        )
        assert details.ok, details.error
        assert [event.type for event in details.events] == ["token"]
        assert "house pie" in details.events[0].text

        too_many = await present_places.execute(
            {
                "discovery_set_id": set_id,
                "selections": [
                    {"place_id": place_id, "reason": "preference_match"},
                    {
                        "place_id": record["places"][1]["place_id"],
                        "reason": "preference_match",
                    },
                ],
                "research_used": True,
                "presentation_mode": "details",
                "lead_in": "Details for one place.",
            },
            ToolContext(
                session=session,
                session_id="sess-pres",
                turn_id="t4",
                turn_evidence=researched,
            ),
        )
        assert not too_many.ok
        assert "exactly one" in (too_many.error or "")

    async def test_destination_selection_may_reselect_one_verified_shown_place(self):
        session = {}
        set_id = discovery_store.store_discovery_set(
            session_id="sess-pres",
            session=session,
            query="pizza",
            places=[
                {
                    "name": "Best Slice",
                    "address": "1 Main St, Manhattan, NY",
                },
                {
                    "name": "Other Slice",
                    "address": "2 Main St, Manhattan, NY",
                },
            ],
        )
        record = discovery_store.load_discovery_set(set_id, session_id="sess-pres")
        place_id = record["places"][0]["place_id"]

        first = await present_places.execute(
            {
                "discovery_set_id": set_id,
                "selections": [{"place_id": place_id, "reason": "top_pick"}],
                "research_used": False,
                "presentation_mode": "recommendations",
            },
            ToolContext(session=session, session_id="sess-pres", turn_id="t1"),
        )
        assert first.ok, first.error

        evidence = TurnEvidence()
        evidence.bind_contract(
            TurnContract((OutcomeGoal("destination", GoalKind.DESTINATION_SELECTION),))
        )
        evidence.record_goal_handle("destination", set_id)
        evidence.record_goal("destination", GoalState.EVIDENCE_READY, attempted=True)
        trip_state.bind_discovery_set(session, set_id)
        replay = await present_places.execute(
            {
                "discovery_set_id": set_id,
                "selections": [{"place_id": place_id, "reason": "preference_match"}],
                "research_used": False,
                "presentation_mode": "recommendations",
                "goal_key": "destination",
            },
            ToolContext(
                session=session,
                session_id="sess-pres",
                turn_id="t2",
                turn_evidence=evidence,
            ),
        )

        assert replay.ok, replay.error
        assert len(replay.data["presented"]) == 1
        assert replay.data["presented"][0]["place_id"] == place_id
        assert trip_state.get_trip_state(session)["selected_place_id"] == place_id
        assert evidence.state_for("destination") == GoalState.SATISFIED

    async def test_researched_details_rebind_stable_place_id_to_verified_handle(self):
        session = {}
        original_set = discovery_store.store_discovery_set(
            session_id="sess-pres",
            session=session,
            query="pizza",
            places=[
                {
                    "name": "L'Industrie",
                    "address": "1 Main St, Manhattan, NY",
                    "provider_place_id": "lindustrie",
                }
            ],
        )
        original = discovery_store.load_discovery_set(
            original_set, session_id="sess-pres"
        )
        place_id = original["places"][0]["place_id"]
        first = await present_places.execute(
            {
                "discovery_set_id": original_set,
                "selections": [{"place_id": place_id, "reason": "top_pick"}],
                "research_used": False,
            },
            ToolContext(session=session, session_id="sess-pres", turn_id="t1"),
        )
        assert first.ok, first.error
        verified_set = discovery_store.store_discovery_set(
            session_id="sess-pres",
            session=session,
            query="L'Industrie",
            places=[
                {
                    "name": "L'Industrie",
                    "address": "1 Main St, Manhattan, NY",
                    "provider_place_id": "lindustrie",
                }
            ],
        )
        verified = discovery_store.load_discovery_set(
            verified_set, session_id="sess-pres"
        )
        assert verified["places"][0]["place_id"] == place_id
        evidence = self._details_evidence(verified_set)

        result = await present_places.execute(
            {
                "discovery_set_id": original_set,
                "selections": [{"place_id": place_id, "reason": "preference_match"}],
                "research_used": True,
                "presentation_mode": "details",
                "goal_key": "details",
                "lead_in": "Current listings highlight the signature pizza.",
            },
            ToolContext(
                session=session,
                session_id="sess-pres",
                turn_id="t2",
                turn_evidence=evidence,
            ),
        )

        assert result.ok, result.error
        assert result.data["discovery_set_id"] == verified_set
        assert result.data["presented"][0]["place_id"] == place_id
        assert evidence.state_for("details") == GoalState.SATISFIED

    async def test_researched_details_rebind_rejects_wrong_place_id_without_mutation(self):
        session = {}
        original_set = self._store()
        original = discovery_store.load_discovery_set(original_set, session_id="sess-pres")
        first = await present_places.execute(
            {
                "discovery_set_id": original_set,
                "selections": [{"place_id": original["places"][0]["place_id"], "reason": "top_pick"}],
                "research_used": False,
            },
            ToolContext(session=session, session_id="sess-pres", turn_id="t1"),
        )
        assert first.ok, first.error
        verified_set = discovery_store.store_discovery_set(
            session_id="sess-pres",
            session=session,
            query="verified",
            places=[
                {
                    "name": "Best Slice",
                    "address": "1 Main St, Manhattan, NY",
                    "provider_place_id": "verified-best-slice",
                }
            ],
        )
        evidence = self._details_evidence(verified_set)
        registry_before = discovery_store.presented_entity_registry(session)
        result = await present_places.execute(
            {
                "discovery_set_id": original_set,
                "selections": [{"place_id": "pl_not_in_verified_set", "reason": "preference_match"}],
                "research_used": True,
                "presentation_mode": "details",
                "goal_key": "details",
                "lead_in": "Current verified details.",
            },
            ToolContext(session=session, session_id="sess-pres", turn_id="t2", turn_evidence=evidence),
        )

        assert not result.ok
        assert "authoritative verified discovery set" in (result.error or "")
        assert result.events == []
        assert discovery_store.presented_entity_registry(session) == registry_before
        assert evidence.state_for("details") == GoalState.EVIDENCE_READY

    async def test_researched_details_rebind_rejects_wrong_session_and_missing_research(self):
        session = {}
        original_set = self._store()
        original = discovery_store.load_discovery_set(original_set, session_id="sess-pres")
        place_id = original["places"][0]["place_id"]
        first = await present_places.execute(
            {
                "discovery_set_id": original_set,
                "selections": [{"place_id": place_id, "reason": "top_pick"}],
                "research_used": False,
            },
            ToolContext(session=session, session_id="sess-pres", turn_id="t1"),
        )
        assert first.ok, first.error
        foreign_set = discovery_store.store_discovery_set(
            session_id="other-session",
            query="verified",
            places=[
                {
                    "name": "Best Slice",
                    "address": "1 Main St, Manhattan, NY",
                    "provider_place_id": "foreign-best-slice",
                }
            ],
        )
        registry_before = discovery_store.presented_entity_registry(session)
        wrong_session = self._details_evidence(foreign_set)
        result = await present_places.execute(
            {
                "discovery_set_id": original_set,
                "selections": [{"place_id": place_id, "reason": "preference_match"}],
                "research_used": True,
                "presentation_mode": "details",
                "goal_key": "details",
                "lead_in": "Current verified details.",
            },
            ToolContext(
                session=session,
                session_id="sess-pres",
                turn_id="t2",
                turn_evidence=wrong_session,
            ),
        )
        assert not result.ok
        assert "not owned by this session" in (result.error or "")
        assert result.events == []
        assert discovery_store.presented_entity_registry(session) == registry_before

        no_research = self._details_evidence(foreign_set, researched=False)
        result = await present_places.execute(
            {
                "discovery_set_id": original_set,
                "selections": [{"place_id": place_id, "reason": "preference_match"}],
                "research_used": False,
                "presentation_mode": "details",
                "goal_key": "details",
                "lead_in": "Current verified details.",
            },
            ToolContext(
                session=session,
                session_id="sess-pres",
                turn_id="t3",
                turn_evidence=no_research,
            ),
        )
        assert not result.ok
        assert "successful current-turn research" in (result.error or "")
        assert result.events == []
        assert discovery_store.presented_entity_registry(session) == registry_before

    async def test_rejects_unknown_and_duplicate_ids(self):
        set_id = self._store()
        record = discovery_store.load_discovery_set(set_id, session_id="sess-pres")
        place_id = record["places"][0]["place_id"]
        result = await present_places.execute(
            {
                "discovery_set_id": set_id,
                "selections": [
                    {"place_id": place_id, "reason": "top_pick"},
                    {"place_id": place_id, "reason": "highest_rating"},
                ],
                "research_used": False,
            },
            _ctx(),
        )
        assert not result.ok
        assert "duplicate" in (result.error or "")
        assert result.internal_diagnostic

    async def test_downgrades_unproven_objective_reason(self):
        set_id = self._store()
        record = discovery_store.load_discovery_set(set_id, session_id="sess-pres")
        weaker = record["places"][1]["place_id"]
        result = await present_places.execute(
            {
                "discovery_set_id": set_id,
                "selections": [{"place_id": weaker, "reason": "highest_rating"}],
                "research_used": False,
            },
            _ctx(),
        )
        assert result.ok
        assert result.data["presented"] == [{"place_id": weaker, "reason": "preference_match"}]
        assert "highest rated" not in result.events[0].text
        assert "matches your request" not in result.events[0].text
        assert "reviews" not in result.events[0].text

    async def test_downgrades_top_pick_when_it_is_not_first(self):
        set_id = self._store()
        record = discovery_store.load_discovery_set(set_id, session_id="sess-pres")
        first = record["places"][0]["place_id"]
        second = record["places"][1]["place_id"]

        result = await present_places.execute(
            {
                "discovery_set_id": set_id,
                "selections": [
                    {"place_id": first, "reason": "preference_match"},
                    {"place_id": second, "reason": "top_pick"},
                ],
                "research_used": False,
            },
            _ctx(),
        )

        assert result.ok
        assert result.data["presented"][1]["reason"] == "preference_match"

    async def test_renders_one_canonical_list(self):
        set_id = self._store()
        record = discovery_store.load_discovery_set(set_id, session_id="sess-pres")
        first = record["places"][0]["place_id"]
        result = await present_places.execute(
            {
                "discovery_set_id": set_id,
                "selections": [{"place_id": first, "reason": "top_pick"}],
                "research_used": False,
            },
            _ctx(),
        )
        assert result.ok
        # Presenter success is composable; turn resolution owns the terminal
        # decision so a compound place-and-route turn can continue.
        assert not result.terminal
        text = result.events[0].text
        assert "Best Slice" in text
        assert text.count("Best Slice") == 1
        assert "pl_" not in text

    async def test_emits_model_framing_around_canonical_place_facts(self):
        set_id = self._store()
        record = discovery_store.load_discovery_set(set_id, session_id="sess-pres")
        first = record["places"][0]["place_id"]
        result = await present_places.execute(
            {
                "discovery_set_id": set_id,
                "selections": [{"place_id": first, "reason": "top_pick"}],
                "research_used": False,
                "lead_in": "A strong nearby option:",
                "follow_up": "Want directions there?",
            },
            _ctx(),
        )

        assert result.ok
        assert [event.type for event in result.events] == ["token", "token", "sources"]
        assert result.events[-1].to_data() == GOOGLE_MAPS_SOURCE_DATA
        visible = "".join(event.text for event in result.events[:-1])
        assert visible.startswith("A strong nearby option:")
        assert "Best Slice" in visible
        assert "Want directions there?" not in visible
        assert "Here are current verified matches:" not in visible
        assert "Here are a few options:" not in visible
        assert result.data["lead_in"] == "A strong nearby option:"
        assert result.data["follow_up"] == ""
        assert "pl_" not in visible

    def test_render_place_list_distinguishes_default_from_explicit_empty_heading(self):
        selections = [{"name": "Best Slice", "borough": "Manhattan"}]

        default_text = present_places.render_place_list(
            selections,
            source_label=None,
        )
        empty_heading_text = present_places.render_place_list(
            selections,
            source_label="",
        )

        assert "Here are a few options:" in default_text
        assert "Here are a few options:" not in empty_heading_text
        assert empty_heading_text == "1. Best Slice — Manhattan"

    async def test_empty_framing_keeps_server_heading_and_single_canonical_event(self):
        set_id = self._store()
        record = discovery_store.load_discovery_set(set_id, session_id="sess-pres")
        first = record["places"][0]["place_id"]
        result = await present_places.execute(
            {
                "discovery_set_id": set_id,
                "selections": [{"place_id": first, "reason": "top_pick"}],
                "research_used": False,
                "lead_in": "",
                "follow_up": "",
            },
            _ctx(),
        )

        assert result.ok
        assert [event.type for event in result.events] == ["token", "sources"]
        assert result.events[-1].to_data() == GOOGLE_MAPS_SOURCE_DATA
        assert "Here are a few options:" in result.events[0].text
        assert result.data["lead_in"] == ""
        assert result.data["follow_up"] == ""

    async def test_framing_rejects_internal_ids_and_overlong_text(self):
        set_id = self._store()
        record = discovery_store.load_discovery_set(set_id, session_id="sess-pres")
        first = record["places"][0]["place_id"]
        base = {
            "discovery_set_id": set_id,
            "selections": [{"place_id": first, "reason": "top_pick"}],
            "research_used": False,
            "lead_in": "",
            "follow_up": "",
        }
        for field, value in (
            ("lead_in", "Showing place pl_internal_123."),
            ("follow_up", "x" * 241),
        ):
            with self.subTest(field=field):
                payload = {**base, field: value}
                result = await present_places.execute(payload, _ctx())
                assert not result.ok
                assert result.internal_diagnostic
                assert field in (result.error or "")

    async def test_framing_suppresses_unowned_next_step_offer(self):
        set_id = self._store()
        record = discovery_store.load_discovery_set(
            set_id, session_id="sess-pres"
        )
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
                "lead_in": "A strong option:",
                "follow_up": "Would you like directions to this one?",
            },
            _ctx(),
        )

        assert result.ok
        text = "".join(
            event.text for event in result.events if event.type == "token"
        )
        assert "Would you like directions to this one?" not in text
        assert result.data["follow_up"] == ""

    async def test_partial_coverage_caveat_remains_server_owned_with_framing(self):
        set_id = discovery_store.store_discovery_set(
            session_id="sess-pres",
            query="ramen",
            requested_count=5,
            coverage={
                "status": "partial",
                "searched_areas": ["Manhattan", "Brooklyn"],
                "unavailable_areas": ["Brooklyn"],
            },
            places=[
                {
                    "name": "Verified Ramen",
                    "address": "1 Main St, Manhattan, NY",
                    "borough": "Manhattan",
                }
            ],
        )
        record = discovery_store.load_discovery_set(set_id, session_id="sess-pres")
        result = await present_places.execute(
            {
                "discovery_set_id": set_id,
                "selections": [
                    {"place_id": record["places"][0]["place_id"], "reason": "top_pick"}
                ],
                "research_used": False,
                "lead_in": "I found one verified option.",
                "follow_up": "Coverage was limited, so I can search again if you want.",
            },
            _ctx(),
        )

        assert result.ok
        assert [event.type for event in result.events] == ["token", "token", "sources"]
        assert result.events[-1].to_data() == GOOGLE_MAPS_SOURCE_DATA
        visible = "".join(event.text for event in result.events[:-1])
        assert "Search coverage was limited in Brooklyn" in visible
        assert "Verified Ramen" in visible

    async def test_open_now_reason_is_not_rendered_twice(self):
        set_id = self._store()
        record = discovery_store.load_discovery_set(set_id, session_id="sess-pres")
        first = record["places"][0]["place_id"]

        result = await present_places.execute(
            {
                "discovery_set_id": set_id,
                "selections": [{"place_id": first, "reason": "open_now"}],
                "research_used": False,
            },
            _ctx(),
        )

        assert result.ok
        assert result.events[0].text.count("open now") == 1
        assert "top pick" not in result.events[0].text
        assert "reviews" not in result.events[0].text

    def test_reason_schema_explains_objective_fact_constraints(self):
        reason = present_places.PRESENT_PLACES_SCHEMA["input_schema"]["properties"][
            "selections"
        ]["items"]["properties"]["reason"]
        description = str(reason.get("description") or "")

        assert "top_pick" in description
        assert "first" in description
        assert "highest_rating" in description
        assert "preference_match" in description

    async def test_presents_places_while_compound_route_goal_remains_pending(self):
        set_id = self._store()
        record = discovery_store.load_discovery_set(set_id, session_id="sess-pres")
        first = record["places"][0]["place_id"]
        evidence = TurnEvidence()
        contract = TurnContract(
            (
                OutcomeGoal("places", GoalKind.PLACE_RECOMMENDATION),
                OutcomeGoal("route", GoalKind.ROUTE),
            )
        )
        evidence.bind_contract(contract)
        evidence.record_goal_handle("places", set_id)
        evidence.record_goal("places", GoalState.EVIDENCE_READY, attempted=True)

        result = await present_places.execute(
            {
                "discovery_set_id": set_id,
                "selections": [{"place_id": first, "reason": "top_pick"}],
                "research_used": False,
                "goal_key": "places",
            },
            _ctx(evidence),
        )

        assert result.ok
        decision = evaluate_completion(contract, evidence)
        assert not decision.may_terminate
        assert decision.remaining_goal_keys == ("route",)
        assert not evidence.terminal

    async def test_repeated_presentation_is_idempotent_within_one_turn(self):
        set_id = self._store()
        record = discovery_store.load_discovery_set(set_id, session_id="sess-pres")
        first = record["places"][0]["place_id"]
        evidence = TurnEvidence()
        payload = {
            "discovery_set_id": set_id,
            "selections": [{"place_id": first, "reason": "top_pick"}],
            "research_used": False,
        }
        ctx = _ctx(evidence)

        first_result = await present_places.execute(payload, ctx)
        second_result = await present_places.execute(payload, ctx)

        assert first_result.ok
        assert second_result.ok
        assert [event.type for event in first_result.events] == ["token", "sources"]
        assert first_result.events[-1].to_data() == GOOGLE_MAPS_SOURCE_DATA
        assert second_result.events == []

    async def test_partial_search_explains_smaller_verified_set(self):
        set_id = discovery_store.store_discovery_set(
            session_id="sess-pres",
            query="ramen",
            requested_count=5,
            coverage={
                "status": "partial",
                "searched_areas": ["Manhattan", "Brooklyn"],
                "unavailable_areas": ["Brooklyn"],
            },
            places=[
                {
                    "name": "Verified Ramen",
                    "address": "1 Main St, Manhattan, NY",
                    "borough": "Manhattan",
                }
            ],
        )
        record = discovery_store.load_discovery_set(set_id, session_id="sess-pres")
        result = await present_places.execute(
            {
                "discovery_set_id": set_id,
                "selections": [
                    {"place_id": record["places"][0]["place_id"], "reason": "top_pick"}
                ],
                "research_used": False,
            },
            _ctx(),
        )

        assert result.ok
        assert "verified matches" not in result.events[0].text.casefold()
        assert "Only" not in result.events[0].text
        assert "limited in Brooklyn" in result.events[0].text

    async def test_first_research_presentation_keeps_lead_in_and_canonical_list(self):
        session = {}
        set_id = discovery_store.store_discovery_set(
            session_id="sess-pres",
            session=session,
            query="pizza",
            places=[
                {
                    "name": "New Verified Place",
                    "address": "2 Main St, Brooklyn, NY",
                    "provider_place_id": "provider-new",
                }
            ],
        )
        record = discovery_store.load_discovery_set(set_id, session_id="sess-pres")
        place_id = record["places"][0]["place_id"]
        evidence = TurnEvidence()
        evidence.bind_contract(
            TurnContract((OutcomeGoal("place", GoalKind.PLACE_RECOMMENDATION),))
        )
        evidence.record_goal_handle("place", set_id)
        evidence.record_goal("place", GoalState.EVIDENCE_READY, attempted=True)
        evidence.note_web(ok=True)
        result = await present_places.execute(
            {
                "discovery_set_id": set_id,
                "selections": [{"place_id": place_id, "reason": "top_pick"}],
                "research_used": True,
                "goal_key": "place",
                "lead_in": "The house pie is highlighted by current listings.",
                "follow_up": "",
            },
            ToolContext(
                session=session,
                session_id="sess-pres",
                turn_id="t-first-research",
                agent_mode="auto",
                turn_evidence=evidence,
            ),
        )
        assert result.ok
        assert [event.type for event in result.events] == ["token", "token", "sources"]
        assert result.events[-1].to_data() == GOOGLE_MAPS_SOURCE_DATA
        visible = "".join(event.text for event in result.events[:-1])
        assert "house pie" in visible
        assert "New Verified Place" in visible
        assert result.data["presented"][0]["place_id"] == place_id
        assert evidence.state_for("place") == GoalState.SATISFIED


if __name__ == "__main__":
    unittest.main()
