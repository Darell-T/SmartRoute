"""Batch F2 audit fixtures: multi-intent conversational sequencing probes.

Non-test module (no ``Test*``/``test_*`` names at module level): pytest never
collects it. Every emitted tool name is a genuinely REGISTERED production tool
(asserted against ``TOOL_REGISTRY`` at runtime); the first model request uses
the stable initial model-led goal surface. State-valid presenters become
available only after their declared goal has usable server-owned evidence.

The fixtures keep the compact scenario wording while route-specific status
variants state the requested direction explicitly. The model rounds are
adapted by ``conversation_multi_intent_support`` to declare rider outcomes
first. Compound declarations use dependencies (for example,
``route`` depends on the selected ``destination``) rather than naming
production capabilities in the contract.

The opaque ``pl_*`` ids used across a same-turn discovery->route chain are
made deterministic through the ``discovery_store.new_place_id`` id-generation
seam (same class as the documented ``candidate_store.new_candidate_id`` seam
in the matrix harness); the real discovery store, real executor, and real
resolver still own every other step.
"""

from __future__ import annotations

from app.services.agent.public_surface import INITIAL_TOOL_NAMES

from tests.conversation.conversation_discovery_fixtures import (
    CONFLICTING_LABEL,
    PLACES_FIXTURE,
)
from tests.conversation.conversation_matrix_harness import make_leg

# ---- Deterministic multi-intent messages -----------------------------------
ROUTE_PLUS_STATUS_MESSAGE = (
    "Take me to Times Square and tell me whether the uptown Q is delayed."
)
DISCOVERY_PLUS_ROUTE_MESSAGE = (
    "Find pizza near Barclays and route me to the second one."
)
COMPARE_EXACT_MESSAGE = "Compare two routes and tell me which has fewer stairs."
COMPARE_ROUTE_MESSAGE = "Route me to Work and tell me which route has fewer stairs."
STATUS_PLUS_REPLAN_MESSAGE = "How bad is the uptown Q and if it's bad find another way."
DISCOVERY_PLUS_STATUS_MESSAGE = "Find pizza near Barclays and is the uptown Q delayed?"
ROUTE_PLUS_EXPLAIN_MESSAGE = (
    "Take me to Times Square and tell me why you picked that route."
)
GREETING_MESSAGE = "Hi"
STATUS_ONLY_MESSAGE = "Is the uptown Q train running normally?"
EXPLAIN_ONLY_MESSAGE = "Why did you pick the Q?"
DISCOVERY_ONLY_MESSAGE = "Find me pizza places in Brooklyn."
NO_GOOD_MESSAGE = "Avoid the Q"

# The declaration response exposes only the state-valid initial capabilities.
# Presenters are deliberately absent until a provider result binds their goal.
INITIAL_TOOL_PROFILE = frozenset(INITIAL_TOOL_NAMES)
ROUTE_TOOL_PROFILE = INITIAL_TOOL_PROFILE
ROUTE_PLUS_STATUS_TOOL_PROFILE = INITIAL_TOOL_PROFILE
DISCOVERY_TOOL_PROFILE = INITIAL_TOOL_PROFILE
DISCOVERY_ROUTE_TOOL_PROFILE = INITIAL_TOOL_PROFILE
DISCOVERY_PLUS_STATUS_TOOL_PROFILE = INITIAL_TOOL_PROFILE
TRANSIT_QUESTION_TOOL_PROFILE = INITIAL_TOOL_PROFILE
ACCESSIBILITY_TOOL_PROFILE = INITIAL_TOOL_PROFILE
NO_TOOL_PROFILE = INITIAL_TOOL_PROFILE

# Tools that must never execute on any F2 surface (legacy or cross-surface).
F2_FORBIDDEN_TOOLS = (
    "plan_trip",
    "poi_search",
    "check_area_conditions",
    "venue_crowd_window",
    "event_lookup",
)
ROUTE_FORBIDDEN_TOOLS = (*F2_FORBIDDEN_TOOLS, "search_local_places", "transit_snapshot", "lookup_arrivals", "lookup_facts", "web_search")
STATUS_FORBIDDEN_TOOLS = (*F2_FORBIDDEN_TOOLS, "search_local_places", "get_place_details", "prepare_route_options", "present_route", "web_search")

# ---- Scripted tool payloads -------------------------------------------------
PREPARE_TIMES_SQUARE_INPUT = {"destination": "Times Square"}
PREPARE_WORK_INPUT = {"destination": "Work"}
PREPARE_WORK_AVOID_Q_INPUT = {
    "destination": "Work",
    "excluded_route_ids": ["Q"],
}
SEARCH_BARCLAYS_INPUT = {"query": "pizza near Barclays", "max_results": 3}
TRANSIT_SNAPSHOT_Q_INPUT = {"lines": ["Q"]}
LOOKUP_FACTS_INPUT = {"topic": "transfers"}

# Opaque-id seams: candidate id for present_route input; deterministic
# discovery place ids so the same-turn prepare can carry the real ordinal-2 id.
FIXED_CANDIDATE_ID = "cd_f2_route_1"
TWO_CANDIDATE_IDS = ("cd_f2_qa", "cd_f2_rb")
DISCOVERY_SET_ID = "ds_f2_pizza"
DISCOVERY_PLACE_IDS = ("pl_f2_pizza_a", "pl_f2_pizza_b", "pl_f2_pizza_c")
ORDINAL_TWO_PLACE_ID = DISCOVERY_PLACE_IDS[1]

# Genuine provider/data seam targets the probes patch.
ALERTS_FETCH_SEAM = "app.services.mta.realtime.fetch_service_alerts"
ALERTS_PARSE_SEAM = "app.services.mta.realtime.parse_service_alerts"
POI_SEAM = "app.services.agent.tools.places.search_local_places.execute"
PREPARE_SEAM = "app.services.agent.tools.route.prepare_route_options.prepare_single_leg"

# ---- Provider fixtures -------------------------------------------------------


def times_square_leg():
    """One canonical prepared leg to Times Square (route Q)."""

    return make_leg(route_ids=("Q",), destination="Times Square")


def work_two_routes_leg():
    """Provider yields two distinct canonical candidates (Q and R) to Work."""

    return make_leg(
        route_ids=("Q", "R"),
        destination="Work",
        evidence_available=True,
    )


def q_only_work_leg():
    """Provider yields only the Q route (no-good for an excluded Q)."""

    return make_leg(route_ids=("Q",), destination="Work")


def r_only_work_leg():
    """Provider yields a Q-free alternative to Work."""

    return make_leg(route_ids=("R",), destination="Work")


def q_alerts_fixture():
    """Parsed service-alert shape the real transit_snapshot expects."""

    return [
        {
            "alert_id": "q-alert-1",
            "header": "Q train delayed at Prospect Park",
            "description": "fixture delay",
            "route_ids": ["Q"],
            "stop_ids": [],
            "start": None,
            "end": None,
        }
    ]


def stored_place2() -> dict:
    """The deterministic stored ordinal-2 record the real store will hold."""

    source = PLACES_FIXTURE[1]
    return {
        "place_id": ORDINAL_TWO_PLACE_ID,
        "name": source["name"],
        "address": source["address"],
        "latitude": source["lat"],
        "longitude": source["lng"],
        "provider_place_id": source["place_id"],
    }


def _goal_for_call(name: str, tool_input: dict) -> tuple[str, str] | None:
    """Map a scripted capability to its rider-facing outcome goal."""

    if name in {"discover_places", "present_places"}:
        return "places", "place_recommendation"
    if name in {"prepare_route_options", "present_route"}:
        return "route", "route"
    if name in {"check_transit", "present_transit"}:
        operation = str(tool_input.get("operation") or "service_status")
        kind = {
            "arrivals": "arrivals",
            "accessibility": "accessibility",
            "fact": "transit_fact",
            "area_conditions": "area_conditions",
            "event_schedule": "event_or_crowd",
            "venue_crowd_window": "event_or_crowd",
        }.get(operation, "service_status")
        return "status", kind
    if name == "complete_turn":
        keys = tool_input.get("goal_keys")
        if isinstance(keys, list) and keys:
            return str(keys[0]), "route" if "route" in keys else "general_response"
        return "response", "general_response"
    return None


_PREPARE_NULL_KEYS = (
    "origin",
    "destination",
    "destination_place_id",
    "exclude_modes",
    "allowed_modes",
    "excluded_route_ids",
    "required_route_ids",
    "allowed_route_ids",
    "preferred_modes",
    "routing_preference",
    "departure_time",
    "arrival_by",
    "waypoints",
    "waypoint_dwell_minutes",
    "avoid_crowds",
    "avoid_stairs",
    "accessibility_required",
    "walking_tolerance_minutes",
    "what_if",
)


def _compound_goals(calls: list[dict]) -> tuple[list[dict], bool, bool, bool]:
    names = {str(call.get("name") or "") for call in calls}
    has_places = bool(names & {"discover_places", "present_places"})
    has_route = bool(names & {"prepare_route_options", "present_route"})
    has_status = bool(names & {"check_transit", "present_transit"})
    if not (has_places or has_route or has_status):
        has_route = any(
            "route" in (call.get("input") or {}).get("goal_keys", [])
            for call in calls
            if str(call.get("name") or "") == "complete_turn"
        )
    goals: list[dict] = []
    if has_places:
        goals.append(
            {
                "goal_key": "destination" if has_route else "places",
                "kind": "destination_selection" if has_route else "place_recommendation",
                "depends_on": [],
            }
        )
    if has_status:
        goals.append(
            {"goal_key": "status", "kind": "service_status", "depends_on": []}
        )
    if has_route:
        dependencies = ["destination"] if has_places else []
        goals.append(
            {"goal_key": "route", "kind": "route", "depends_on": dependencies}
        )
    if not goals and calls:
        goals.append(
            {"goal_key": "response", "kind": "general_response", "depends_on": []}
        )
    return goals, has_places, has_route, has_status


def _fill_public_schema(name: str, tool_input: dict) -> dict:
    if name == "discover_places":
        tool_input.setdefault("activity_label", None)
    elif name == "check_transit":
        tool_input.setdefault("stop_source", "auto")
        tool_input.setdefault("concerns", [])
        tool_input.setdefault("activity_label", None)
    elif name == "prepare_route_options":
        for key in _PREPARE_NULL_KEYS:
            tool_input.setdefault(key, None)
        tool_input.setdefault("activity_label", None)
    elif name in {"present_places", "present_transit", "present_route"}:
        tool_input.setdefault("lead_in", "")
        tool_input.setdefault("follow_up", "")
    return tool_input


def _rewrite_multi_call(
    call: dict,
    *,
    evidence_id: str,
    goal_by_capability: dict[str, str],
    has_places: bool,
    has_route: bool,
    has_status: bool,
) -> dict:
    name = str(call.get("name") or "")
    tool_input = dict(call.get("input") or {})
    goal_key = goal_by_capability.get(name)
    if name == "complete_turn":
        provider_key = (
            "route" if has_route else "status" if has_status else
            "destination" if has_places else "response"
        )
        outcome = str(tool_input.get("outcome") or "answer")
        if has_status and not has_route and outcome == "answer":
            name = "present_transit"
            tool_input = {
                "goal_key": "status",
                "evidence_set_id": evidence_id,
            }
        else:
            tool_input.pop("goal_key", None)
            tool_input["goal_keys"] = [provider_key]
            if outcome == "answer" and provider_key != "response":
                tool_input["outcome"] = "unavailable"
        goal_key = None
    if name == "present_transit":
        tool_input["goal_key"] = "status"
        tool_input.setdefault("evidence_set_id", evidence_id)
    elif goal_key is not None:
        tool_input["goal_key"] = goal_key
    return {**call, "name": name, "input": _fill_public_schema(name, tool_input)}


def _model_led_rounds(
    rounds: list[dict], *, turn_id: str, evidence_id: str
) -> tuple[list[dict], str | None, dict[str, dict]]:
    calls = [
        call
        for scripted in rounds
        for call in scripted.get("tool_use") or []
        if str(call.get("name") or "") != "declare_goals"
    ]
    goals, has_places, has_route, has_status = _compound_goals(calls)
    if not goals:
        return rounds, None, {}
    goal_by_capability = {
        "discover_places": "destination" if has_route else "places",
        "present_places": "destination" if has_route else "places",
        "check_transit": "status",
        "present_transit": "status",
        "prepare_route_options": "route",
        "present_route": "route",
    }
    adapted: list[dict] = []
    declared = False
    for scripted in rounds:
        transformed = []
        for call in scripted.get("tool_use") or []:
            if str(call.get("name") or "") == "declare_goals":
                continue
            transformed.append(
                _rewrite_multi_call(
                    call,
                    evidence_id=evidence_id,
                    goal_by_capability=goal_by_capability,
                    has_places=has_places,
                    has_route=has_route,
                    has_status=has_status,
                )
            )
        if not transformed:
            continue
        if not declared:
            transformed.insert(
                0,
                {
                    "id": f"tu-{turn_id}-goals",
                    "name": "declare_goals",
                    "input": {"goals": goals},
                },
            )
            declared = True
        adapted.append({**scripted, "tool_use": transformed})
    return adapted, evidence_id, {goal["goal_key"]: goal for goal in goals}


__all__ = (
    "ACCESSIBILITY_TOOL_PROFILE",
    "ALERTS_FETCH_SEAM",
    "ALERTS_PARSE_SEAM",
    "COMPARE_EXACT_MESSAGE",
    "COMPARE_ROUTE_MESSAGE",
    "CONFLICTING_LABEL",
    "DISCOVERY_ONLY_MESSAGE",
    "DISCOVERY_PLACE_IDS",
    "DISCOVERY_PLUS_ROUTE_MESSAGE",
    "DISCOVERY_PLUS_STATUS_MESSAGE",
    "DISCOVERY_PLUS_STATUS_TOOL_PROFILE",
    "DISCOVERY_ROUTE_TOOL_PROFILE",
    "DISCOVERY_SET_ID",
    "DISCOVERY_TOOL_PROFILE",
    "EXPLAIN_ONLY_MESSAGE",
    "F2_FORBIDDEN_TOOLS",
    "FIXED_CANDIDATE_ID",
    "GREETING_MESSAGE",
    "INITIAL_TOOL_PROFILE",
    "LOOKUP_FACTS_INPUT",
    "NO_GOOD_MESSAGE",
    "NO_TOOL_PROFILE",
    "ORDINAL_TWO_PLACE_ID",
    "PLACES_FIXTURE",
    "POI_SEAM",
    "PREPARE_SEAM",
    "PREPARE_TIMES_SQUARE_INPUT",
    "PREPARE_WORK_AVOID_Q_INPUT",
    "PREPARE_WORK_INPUT",
    "ROUTE_FORBIDDEN_TOOLS",
    "ROUTE_PLUS_EXPLAIN_MESSAGE",
    "ROUTE_PLUS_STATUS_MESSAGE",
    "ROUTE_PLUS_STATUS_TOOL_PROFILE",
    "ROUTE_TOOL_PROFILE",
    "SEARCH_BARCLAYS_INPUT",
    "STATUS_FORBIDDEN_TOOLS",
    "STATUS_ONLY_MESSAGE",
    "STATUS_PLUS_REPLAN_MESSAGE",
    "TRANSIT_QUESTION_TOOL_PROFILE",
    "TRANSIT_SNAPSHOT_Q_INPUT",
    "TWO_CANDIDATE_IDS",
    "_model_led_rounds",
    "q_alerts_fixture",
    "q_only_work_leg",
    "r_only_work_leg",
    "stored_place2",
    "times_square_leg",
    "work_two_routes_leg",
)
