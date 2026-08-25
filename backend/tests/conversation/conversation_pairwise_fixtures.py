"""Batch I fixtures/constants: pairwise invariants and metamorphic properties.

Non-test module (no ``Test*``/``test_*`` names at module level): pytest never
collects it. Batch I is an AUDIT-ONLY batch: it adds only the three owned
test/fixture/support files and never edits production or the coverage ledger.

Every scenario drives the *actual* canonical production conversation loop
(``app.services.agent.loop.run_agent_turn``) with production
state-scoped tool filtering, the *actual* registered ``TOOL_REGISTRY``
executors (``prepare_route_options``, ``present_route``,
``search_local_places``, ``get_place_details``), the *actual* candidate /
discovery / trip / session stores, the real tool ledger, and the real SSE
event path. Legacy ``plan_trip`` is never used and no fake executor replaces
a canonical route tool. Only genuine external/provider/data seams are
scripted, identical to the documented Batch A/B/C/D/E harness seams:

- ``prepare_route_options.prepare_single_leg`` -- provider route/evidence
  seam inside the real canonical prepare executor.
- ``trips.enrichment._enrich_route`` and ``tools.lookup_arrivals.execute`` --
  live enrichment / arrival fetches the real present executor may call.
- ``candidate_store.new_candidate_id`` -- opaque id generation only where a
  deterministic candidate id scripts ``present_route`` input.
- ``candidate_store.store_candidate_set`` -- observed, never replaced.
- ``search_local_places.execute`` -- provider place-search seam.

Anthropic inference is scripted through ``tests/_fake_anthropic`` as
deterministic mock text; no model linguistic accuracy is claimed. Real
``ds_*``/``pl_*``/``cs_*``/``cd_*`` ids are always read back from the real
stores between turns; invented ids appear only in the explicit
malicious-input rows.

=====================================================================
I-07 RISK-BASED PAIR COVERAGE (compact, no Cartesian explosion)
=====================================================================

Each row is one materially distinct interaction: intent family x initial
server state x hard constraint x temporal mode x reference type x
provider/evidence outcome x language form x follow-up. Families: I-01
status/explanation non-mutation (fresh / accepted / temporary / discovery /
stale), I-02 canonical one-present/one-card plus invalid/duplicate reference
authority neighbors (Auto + Quick), I-03 no-good with preserved accepted
state plus one valid-presentation control, I-04 what-if isolation (time /
bus / route exclusion / preference / destination; preview -> accept/reject;
status turn inside a live preview), I-05 discovery canonicalization (named
and ordinal references; latest vs stale/cross-set), I-06 metamorphic wording
equivalence plus one parser-boundary row. ``SCENARIO_ROWS`` below is the
machine-readable matrix walked by ``test_i07_scenario_table_consistency``.

Severity contract: a P0/P1 here means a row's loop-level behavior violates
its declared invariant (exact offered surface, non-mutation projection,
single-card present, temporary-state preservation, canonical place reference
authority). This batch reports findings only and never patches production.
"""

from __future__ import annotations

from app.services.agent.tools.location_resolution import ResolvedPlace
from app.services.agent.tools.route.preparation_adapter import PreparedLeg
from tests.conversation.conversation_discovery_fixtures import (
    DISCOVERY_TOOL_PROFILE,
    ROUTE_TOOL_PROFILE,
)
from tests.conversation.conversation_matrix_harness import _turn_round, make_leg

# ---------------------------------------------------------------------------
# Reused canonical tool profiles (frozen production values)
# ---------------------------------------------------------------------------

TRANSIT_QUESTION_TOOL_PROFILE = frozenset(DISCOVERY_TOOL_PROFILE)
STATUS_TOOL_PROFILE = TRANSIT_QUESTION_TOOL_PROFILE
ACCESSIBILITY_TOOL_PROFILE = frozenset(DISCOVERY_TOOL_PROFILE)
NO_TOOL_PROFILE = frozenset(DISCOVERY_TOOL_PROFILE)
ROUTE_NAVIGATION_TOOL_PROFILE = frozenset(ROUTE_TOOL_PROFILE)
DISCOVERY_REFERENCE_TOOL_PROFILE = frozenset(DISCOVERY_TOOL_PROFILE)

# ---------------------------------------------------------------------------
# I-01: status / explanation message families (all parse to transit_question)
# ---------------------------------------------------------------------------

LINE_STATUS_MESSAGES = (
    "Is the uptown Q train running normally?",
    "Is the uptown R running?",
    "is the Q running uptown",
)
STATION_STATUS_MESSAGES = (
    "Is Atlantic Avenue station open?",
    "Is the 14th Street station accessible?",
)
EXPLANATION_MESSAGES = (
    "Why did you pick the R?",
    "Why the R?",
    "Why did you choose this route?",
)


def transit_question_profile_for(message: str) -> frozenset[str]:
    """Frozen expected minimal surface for the conversational test matrix."""

    lowered = message.casefold()
    if (
        message in LINE_STATUS_MESSAGES
        or message in STATUS_WORDING_VARIANTS
        or message == AMBIGUOUS_LINE_STATUS_MESSAGE
    ):
        return STATUS_TOOL_PROFILE
    if message == STATUS_INSIDE_PREVIEW_MESSAGE:
        return STATUS_TOOL_PROFILE
    if message == STATION_STATUS_MESSAGES[1] or "accessibility" in lowered:
        return ACCESSIBILITY_TOOL_PROFILE
    return NO_TOOL_PROFILE

# ---------------------------------------------------------------------------
# I-06: metamorphic wording families (every variant parses to the family
# intent; loop behavior must be identical across wordings)
# ---------------------------------------------------------------------------

ROUTE_WORDING_VARIANTS = (
    "Take me to Grand Central.",
    "Get me to grand central please",
    "Head to Grand Central.",
    "take me to Grand Central!",
    "Could you take me to Grand Central?",
)
STATUS_WORDING_VARIANTS = (
    "Is the uptown Q running?",
    "is the Q running uptown",
    "Is the uptown Q train running right now?",
    "What's the status of the uptown Q?",
)
DISCOVERY_WORDING_VARIANTS = (
    "Find me pizza places in Brooklyn.",
    "find pizza near me please",
    "Recommend a good pizza place in Brooklyn",
    "Can you find me pizza in Brooklyn?",
)
WHAT_IF_EXCLUSION_VARIANTS = (
    "What if I avoid the Q?",
    "what if we skip the Q?",
    "What if I don't take the Q?",
)
BASE_ROUTE_MESSAGE = "Take me to Grand Central."
EXCLUSION_ROUTE_MESSAGE = "Take me to Grand Central avoiding the Q"
# Deterministic parser boundary (documented live-model interpretation
# backlog): a terse destination-only label without a navigation verb parses
# to transit_question and must never receive the route surface.
PARSER_BOUNDARY_MESSAGE = "Grand Central, please."

# Route-specific status without a direction is intentionally kept separate
# from the explicit-direction wording family. The loop may perform a grounded
# linewide status check without inventing a direction; arrivals and take/wait
# advice remain direction-gated.
AMBIGUOUS_LINE_STATUS_MESSAGE = "Is the Q running?"

# ---------------------------------------------------------------------------
# I-03 / I-04 / I-06: constraint and follow-up messages
# ---------------------------------------------------------------------------

AVOID_Q_MESSAGE = "Avoid the Q"
AVOID_STAIRS_MESSAGE = "Avoid stairs"
NO_WALKING_MESSAGE = "No walking at all"
CHANGE_ROUTE_MESSAGE = "Change the route"
VALID_ROUTE_MESSAGE = "Plan a route to Work"
FEWER_TRANSFERS_MESSAGE = "What if I want fewer transfers?"
TEMPORAL_WHAT_IF_MESSAGE = "What if I leave 30 minutes later?"
BUS_WHAT_IF_MESSAGE = "What if I take the bus?"
ALT_DESTINATION_MESSAGE = "What if I go to Coney Island instead?"
ACCEPT_MESSAGE = "Use that instead."
REJECT_MESSAGE = "Never mind."
SELECT_SECOND_MESSAGE = "The second one."
NAVIGATE_SELECTED_MESSAGE = "Take me there."
STATUS_INSIDE_PREVIEW_MESSAGE = "Is the uptown R running?"

# ---------------------------------------------------------------------------
# Fixed opaque ids (issued through the real store id seam where scripted)
# ---------------------------------------------------------------------------

CANDIDATE_I2 = "cd_i2_route_1"
CANDIDATE_I2_V2 = "cd_i2_route_2"
CANDIDATE_I4_PREVIEW = "cd_i4_preview_1"
CANDIDATE_I5_ROUTE = "cd_i5_route_1"
CANDIDATE_I6_ROUTE = "cd_i6_route_1"
CANDIDATE_SESSION_B = "cd_i2_session_b"
INVENTED_CANDIDATE_ID = "cd_i2_invented_000"
MODEL_PROSE_CANDIDATE_ID = "cd_from_model_prose"
TEMPORAL_DEPARTURE = "2026-08-06T12:30:00-04:00"  # now + 30 minutes

# ---------------------------------------------------------------------------
# Bounded production error markers
# ---------------------------------------------------------------------------

NO_ACTIVE_SET_MARKER = "no active candidate set"
CANDIDATE_UNKNOWN_MARKER = "candidate id is unknown for this set"
ALREADY_PRESENTED_MARKER = "already presented"
EXPIRED_SET_MARKER = "expired"
NO_HARD_CONSTRAINT_MATCH = "no_hard_constraint_match"
NO_GOOD_MODEL_TEXT = "I could not find a route that meets your constraints."
STATUS_MODEL_TEXT = "Here is what I can see about service right now."
ACCESSIBILITY_INPUT = {"accessibility_required": True, "avoid_stairs": True}

# ---------------------------------------------------------------------------
# Forbidden surfaces / leak markers
# ---------------------------------------------------------------------------

FORBIDDEN_TOOLS = (
    "plan_trip",
    "web_search",
    "poi_search",
    "event_lookup",
    "transit_snapshot",
    "lookup_arrivals",
    "lookup_facts",
    "venue_crowd_window",
    "check_area_conditions",
)
NO_ROUTE_SURFACE = ("prepare_route_options", "present_route", "get_place_details")
LEAK_MARKERS = ("pl_", "ds_", "cd_", "cs_", "rc_", "chij")

# ---------------------------------------------------------------------------
# Provider/data leg fixtures (the canonical prepare seam's "provider" output)
# ---------------------------------------------------------------------------


def r_leg(destination: str = "Work") -> PreparedLeg:
    """Provider yields the R route (satisfies an excluded-Q constraint)."""

    return make_leg(route_ids=("R",), destination=destination)


def q_only_leg(destination: str = "Work") -> PreparedLeg:
    """Provider yields only the Q route (hard-excluded in I-03-A)."""

    return make_leg(route_ids=("Q",), destination=destination)


def walking_leg(destination: str = "Work") -> PreparedLeg:
    """Default leg with 3 minutes of street walking (violates tolerance 0)."""

    return make_leg(route_ids=("R",), destination=destination)


def inaccessible_leg(destination: str = "Work") -> PreparedLeg:
    """Provider route with explicit inaccessible station evidence."""

    leg = make_leg(route_ids=("R",), destination=destination)
    leg.parsed_routes = [
        [
            {
                "type": "WALK",
                "duration_seconds": 120,
                "departure_time_iso": "2026-08-06T12:00:00-04:00",
                "arrival_time_iso": "2026-08-06T12:02:00-04:00",
            },
            {
                "type": "SUBWAY",
                "route_id": "R",
                "duration_seconds": 1200,
                "departure_stop": "Home St",
                "arrival_stop": destination,
                "departure_accessibility": "inaccessible",
                "arrival_accessibility": "accessible",
                "departure_time_iso": "2026-08-06T12:05:00-04:00",
                "arrival_time_iso": "2026-08-06T12:25:00-04:00",
            },
        ]
    ]
    return leg


def grand_central_leg(destination: str = "Grand Central") -> PreparedLeg:
    """Provider leg for the Grand Central metamorphic route variants."""

    return make_leg(route_ids=("Q",), destination=destination)


def coney_island_leg(destination: str = "Coney Island") -> PreparedLeg:
    """Provider leg for the alternate-destination what-if variant."""

    return make_leg(route_ids=("Q",), destination=destination)


def bus_leg(destination: str = "Work") -> PreparedLeg:
    """Provider leg with a BUS segment (satisfies the bus what-if)."""

    leg = make_leg(route_ids=("B38",), destination=destination)
    leg.parsed_routes = [
        [
            {
                "type": "WALK",
                "duration_seconds": 120,
                "departure_time_iso": "2026-08-06T12:00:00-04:00",
                "arrival_time_iso": "2026-08-06T12:02:00-04:00",
            },
            {
                "type": "BUS",
                "route_id": "B38",
                "duration_seconds": 1560,
                "departure_stop": "Home St",
                "arrival_stop": destination,
                "departure_time_iso": "2026-08-06T12:05:00-04:00",
                "arrival_time_iso": "2026-08-06T12:31:00-04:00",
            },
        ]
    ]
    leg.scored = [
        {
            "index": 0,
            "score": 21,
            "total_minutes": 31,
            "transfers": 0,
            "alert_count": 0,
            "transit_count": 1,
            "event_crowd_penalty": 0,
            "rank": 1,
        }
    ]
    return leg


def discovery_leg_for(place: dict) -> PreparedLeg:
    """One canonical prepared leg carrying the STORED discovery identity."""

    leg = make_leg(route_ids=("Q",), destination=place["name"])
    leg.destination_place = ResolvedPlace(
        name=place["name"],
        latitude=float(place["latitude"]),
        longitude=float(place["longitude"]),
        source="discovery",
        address=place.get("address") or None,
        place_id=place.get("provider_place_id"),
    )
    leg.destination_raw = place["name"]
    return leg


def coffee_poi_result():
    """Provider seam output: three distinct coffee places (discovery fixture)."""

    from app.services.agent.tools._types import ToolResult

    return ToolResult(
        ok=True,
        data={
            "results": [
                {
                    "name": "A Coffee",
                    "address": "11 A St, Brooklyn, NY",
                    "lat": 40.71,
                    "lng": -73.98,
                    "open_now": True,
                    "rating": 4.1,
                    "review_count": 200,
                    "place_id": "ChIJ-ccc1",
                },
                {
                    "name": "B Coffee",
                    "address": "22 B Ave, Brooklyn, NY",
                    "lat": 40.72,
                    "lng": -73.97,
                    "open_now": True,
                    "rating": 4.4,
                    "review_count": 150,
                    "place_id": "ChIJ-ccc2",
                },
                {
                    "name": "C Coffee",
                    "address": "33 C St, Brooklyn, NY",
                    "lat": 40.70,
                    "lng": -73.96,
                    "open_now": False,
                    "rating": 3.9,
                    "review_count": 90,
                    "place_id": "ChIJ-ccc3",
                },
            ]
        },
        summary="3 places",
    )


def no_good_rounds(extra: dict | None = None) -> list:
    """One scripted prepare + truthful terminal no-good response."""

    tool_input = {"destination": "Work",
                  "arrival_by": "2026-08-06T13:00:00-04:00",
                  **(extra or {})}
    from tests.conversation.conversation_matrix_harness import complete_turn_round

    return [
        _turn_round("prepare_route_options", "tu-prepare", tool_input),
        complete_turn_round(
            "tu-no-good",
            NO_GOOD_MODEL_TEXT,
            outcome="unavailable",
        ),
    ]


# I-03 no-good variants: (id, message, provider leg, expected violations,
# expected audit tool_input, expected slots.constraints, digest accessibility
# status, extra prepare input, expected preference patch). Every row runs on
# an accepted seeded trip and must preserve the accepted selection as one
# bound unit; preferences change only by the declared patch.
NO_GOOD_VARIANTS = (
    ("I-03-A", AVOID_Q_MESSAGE, q_only_leg(), ("excluded_route",),
     {"excluded_route_ids": ["Q"]}, {"excluded_route_ids": ["Q"]},
     None, {"excluded_route_ids": ["Q"]}, {}),
    ("I-03-B", AVOID_STAIRS_MESSAGE, r_leg(),
     ("accessibility_unknown_or_unavailable",), ACCESSIBILITY_INPUT,
     None, "unknown", ACCESSIBILITY_INPUT,
     {"avoid_stairs": True, "accessibility_required": True}),
    ("I-03-C", AVOID_STAIRS_MESSAGE, inaccessible_leg(),
     ("accessibility_unknown_or_unavailable",), ACCESSIBILITY_INPUT,
     None, "inaccessible", ACCESSIBILITY_INPUT,
     {"avoid_stairs": True, "accessibility_required": True}),
    ("I-03-D", NO_WALKING_MESSAGE, walking_leg(),
     ("walking_tolerance",), {"walking_tolerance_minutes": 0}, None,
     None, {"walking_tolerance_minutes": 0},
     {"walking_tolerance_minutes": 0}),
)


# ---------------------------------------------------------------------------
# I-07 machine-readable scenario rows (walked by the consistency test)
# ---------------------------------------------------------------------------

# Each row: (id, message, expected_intent, expected_profile, test_node).
SCENARIO_ROWS = (
    ("I-01-A", LINE_STATUS_MESSAGES[0], "transit_question",
     STATUS_TOOL_PROFILE, "test_i01_status_explanation_fresh"),
    ("I-01-B", EXPLANATION_MESSAGES[0], "transit_question",
     NO_TOOL_PROFILE, "test_i01_status_explanation_accepted_trip"),
    ("I-01-C", EXPLANATION_MESSAGES[1], "transit_question",
     NO_TOOL_PROFILE, "test_i01_status_explanation_temporary_what_if"),
    ("I-01-D", STATION_STATUS_MESSAGES[0], "transit_question",
     NO_TOOL_PROFILE, "test_i01_status_explanation_active_discovery"),
    ("I-01-E", LINE_STATUS_MESSAGES[1], "transit_question",
     STATUS_TOOL_PROFILE, "test_i01_status_explanation_expired_reference"),
    ("I-02-A", VALID_ROUTE_MESSAGE, "route_planning",
     ROUTE_NAVIGATION_TOOL_PROFILE, "test_i02_single_present_single_card"),
    ("I-02-B", CHANGE_ROUTE_MESSAGE, "route_planning",
     ROUTE_NAVIGATION_TOOL_PROFILE, "test_i02_cross_round_duplicate_present_rejected"),
    ("I-02-C", CHANGE_ROUTE_MESSAGE, "route_planning",
     ROUTE_NAVIGATION_TOOL_PROFILE, "test_i02_wrong_session_and_invented_present_rejected"),
    ("I-02-D", VALID_ROUTE_MESSAGE, "route_planning",
     ROUTE_NAVIGATION_TOOL_PROFILE, "test_i02_model_candidate_text_never_authority"),
    ("I-03-A", AVOID_Q_MESSAGE, "route_planning",
     ROUTE_NAVIGATION_TOOL_PROFILE, "test_i03_no_good_preserves_accepted_context"),
    ("I-03-B", AVOID_STAIRS_MESSAGE, "route_planning",
     ROUTE_NAVIGATION_TOOL_PROFILE, "test_i03_no_good_preserves_accepted_context"),
    ("I-03-C", AVOID_STAIRS_MESSAGE, "route_planning",
     ROUTE_NAVIGATION_TOOL_PROFILE, "test_i03_no_good_preserves_accepted_context"),
    ("I-03-D", NO_WALKING_MESSAGE, "route_planning",
     ROUTE_NAVIGATION_TOOL_PROFILE, "test_i03_no_good_preserves_accepted_context"),
    ("I-03-E", VALID_ROUTE_MESSAGE, "route_planning",
     ROUTE_NAVIGATION_TOOL_PROFILE, "test_i03_control_valid_prepare_presents_one_card"),
    ("I-04-A", WHAT_IF_EXCLUSION_VARIANTS[0], "route_planning",
     ROUTE_NAVIGATION_TOOL_PROFILE, "test_i04_route_exclusion_what_if_accept"),
    ("I-04-B", WHAT_IF_EXCLUSION_VARIANTS[1], "route_planning",
     ROUTE_NAVIGATION_TOOL_PROFILE, "test_i04_route_exclusion_what_if_reject"),
    ("I-04-C", TEMPORAL_WHAT_IF_MESSAGE, "route_planning",
     ROUTE_NAVIGATION_TOOL_PROFILE, "test_i04_temporal_what_if_preview_reject"),
    ("I-04-D", BUS_WHAT_IF_MESSAGE, "route_planning",
     ROUTE_NAVIGATION_TOOL_PROFILE, "test_i04_bus_what_if_preview_reject"),
    ("I-04-E", FEWER_TRANSFERS_MESSAGE, "route_planning",
     ROUTE_NAVIGATION_TOOL_PROFILE, "test_i04_routing_what_if_preview_reject"),
    ("I-04-F", ALT_DESTINATION_MESSAGE, "route_planning",
     ROUTE_NAVIGATION_TOOL_PROFILE, "test_i04_alternate_destination_what_if_reject"),
    ("I-04-G", STATUS_INSIDE_PREVIEW_MESSAGE, "transit_question",
     STATUS_TOOL_PROFILE, "test_i04_status_turn_inside_live_preview"),
    ("I-05-A", SELECT_SECOND_MESSAGE, "transit_question",
     DISCOVERY_REFERENCE_TOOL_PROFILE, "test_i05_named_selection_canonical_control"),
    ("I-05-B", SELECT_SECOND_MESSAGE, "transit_question",
     DISCOVERY_REFERENCE_TOOL_PROFILE, "test_i05_latest_set_supersedes"),
    ("I-05-C", SELECT_SECOND_MESSAGE, "transit_question",
     DISCOVERY_REFERENCE_TOOL_PROFILE, "test_i05_expired_set_reference_fails_safely"),
    ("I-05-D", DISCOVERY_WORDING_VARIANTS[0], "destination_discovery",
     DISCOVERY_TOOL_PROFILE, "test_i05_label_only_never_destination_authority"),
    ("I-06-A", ROUTE_WORDING_VARIANTS[0], "route_planning",
     ROUTE_NAVIGATION_TOOL_PROFILE, "test_i06_route_wording_equivalence"),
    ("I-06-B", STATUS_WORDING_VARIANTS[0], "transit_question",
     STATUS_TOOL_PROFILE, "test_i06_status_wording_equivalence"),
    ("I-06-C", DISCOVERY_WORDING_VARIANTS[0], "destination_discovery",
     DISCOVERY_TOOL_PROFILE, "test_i06_discovery_wording_equivalence"),
    ("I-06-D", WHAT_IF_EXCLUSION_VARIANTS[2], "route_planning",
     ROUTE_NAVIGATION_TOOL_PROFILE, "test_i06_what_if_wording_equivalence"),
    ("I-06-E", EXCLUSION_ROUTE_MESSAGE, "route_planning",
     ROUTE_NAVIGATION_TOOL_PROFILE, "test_i06_semantic_change_appears_in_state"),
    ("I-06-F", PARSER_BOUNDARY_MESSAGE, "transit_question",
     NO_TOOL_PROFILE, "test_i06_parser_boundary_label_not_authority"),
)


__all__ = (
    "ACCEPT_MESSAGE", "ACCESSIBILITY_INPUT", "ACCESSIBILITY_TOOL_PROFILE",
    "ALT_DESTINATION_MESSAGE",
    "ALREADY_PRESENTED_MARKER", "AVOID_Q_MESSAGE", "AVOID_STAIRS_MESSAGE",
    "BASE_ROUTE_MESSAGE",
    "BUS_WHAT_IF_MESSAGE", "CANDIDATE_I2", "CANDIDATE_I2_V2",
    "CANDIDATE_I4_PREVIEW", "CANDIDATE_I5_ROUTE", "CANDIDATE_I6_ROUTE",
    "CANDIDATE_SESSION_B", "CANDIDATE_UNKNOWN_MARKER", "CHANGE_ROUTE_MESSAGE",
    "DISCOVERY_REFERENCE_TOOL_PROFILE", "DISCOVERY_TOOL_PROFILE",
    "DISCOVERY_WORDING_VARIANTS", "EXCLUSION_ROUTE_MESSAGE",
    "EXPIRED_SET_MARKER", "EXPLANATION_MESSAGES", "FEWER_TRANSFERS_MESSAGE",
    "FORBIDDEN_TOOLS", "INVENTED_CANDIDATE_ID", "LEAK_MARKERS",
    "LINE_STATUS_MESSAGES", "MODEL_PROSE_CANDIDATE_ID",
    "AMBIGUOUS_LINE_STATUS_MESSAGE",
    "NAVIGATE_SELECTED_MESSAGE", "NO_ACTIVE_SET_MARKER", "NO_GOOD_MODEL_TEXT",
    "NO_HARD_CONSTRAINT_MATCH", "NO_ROUTE_SURFACE", "NO_WALKING_MESSAGE",
    "NO_GOOD_VARIANTS",
    "NO_TOOL_PROFILE",
    "PARSER_BOUNDARY_MESSAGE", "REJECT_MESSAGE",
    "ROUTE_NAVIGATION_TOOL_PROFILE", "ROUTE_TOOL_PROFILE",
    "ROUTE_WORDING_VARIANTS", "SCENARIO_ROWS", "SELECT_SECOND_MESSAGE",
    "STATUS_INSIDE_PREVIEW_MESSAGE", "STATUS_MODEL_TEXT",
    "STATUS_TOOL_PROFILE", "STATUS_WORDING_VARIANTS",
    "STATION_STATUS_MESSAGES", "TEMPORAL_DEPARTURE",
    "TEMPORAL_WHAT_IF_MESSAGE", "TRANSIT_QUESTION_TOOL_PROFILE",
    "transit_question_profile_for",
    "VALID_ROUTE_MESSAGE", "WHAT_IF_EXCLUSION_VARIANTS", "bus_leg",
    "coffee_poi_result", "coney_island_leg", "discovery_leg_for",
    "grand_central_leg", "inaccessible_leg", "q_only_leg", "r_leg",
    "walking_leg", "no_good_rounds",
)
