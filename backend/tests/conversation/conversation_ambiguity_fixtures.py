"""Batch E3 fixtures/constants for ambiguity, contradiction, temporal audits.

Non-test module (no ``Test*``/``test_*`` names at module level): pytest never
collects it. Kept separate from ``conversation_ambiguity_support`` so every
Batch E3 source file stays well below the 500-line limit.

Only genuine external/provider/data seams are scripted, identical to the
documented Batch A/B/C/D/E1 harness seams:

- ``prepare_route_options.prepare_single_leg`` -- the provider route/evidence
  seam inside the real canonical prepare executor (loop scenarios).
- ``app.services.trips.preparation.dependencies.resolve_named_place`` /
  ``route_with_recovery`` /
  ``derive_arrive_by_departure`` -- the provider/geo seams of the *real*
  ``prepare_single_leg`` for the direct executor time-validation probes.
- ``mta.realtime`` collection/parse seams and
  ``trips.route_incidents.scan`` -- live-data seams of the real
  executor probes.
- ``candidate_store.store_candidate_set`` -- observed, never replaced.

Fixed timestamps are America/New_York EDT (``-04:00``) around the harness
clock ``2026-08-06T12:00:00-04:00``.
"""

from __future__ import annotations

from app.services.agent.tools.route.preparation_adapter import PreparedLeg

from tests.conversation.conversation_matrix_harness import PUBLIC_TOOL_PROFILE, make_leg

# ---------------------------------------------------------------------------
# Fixed America/New_York timestamps (EDT, -04:00)
# ---------------------------------------------------------------------------

NOW_ET = "2026-08-06T12:00:00-04:00"
DEPART_PLUS_5 = "2026-08-06T12:05:00-04:00"
DEPART_PLUS_30 = "2026-08-06T12:30:00-04:00"  # Batch B temporal control neighbor
DEPART_LATER_TONIGHT = "2026-08-06T23:30:00-04:00"
DEPART_TOMORROW = "2026-08-07T08:00:00-04:00"
DEPART_MIDNIGHT_CROSSING = "2026-08-07T00:30:00-04:00"
DEPART_PAST = "2026-08-06T11:30:00-04:00"
ARRIVAL_PAST = "2026-08-05T12:00:00-04:00"  # "yesterday" relative to NOW_ET
MALFORMED_TIME = "12:00"
NAIVE_TIME = "2026-08-06T12:00:00"  # no timezone offset

# ---------------------------------------------------------------------------
# E3-A: ambiguous / missing reference messages
# ---------------------------------------------------------------------------

NAV_NO_CONTEXT = "Take me there."
ORDINAL_NO_CONTEXT = "The second one."
ORIGIN_ONLY_MESSAGES = ("Plan a trip from Home", "Route me from Home")
REPLAN_WITHOUT_DESTINATION = "Change the route"

# ---------------------------------------------------------------------------
# E3-B: contradiction / precedence messages
# ---------------------------------------------------------------------------

AVOID_AND_TAKE_Q = "Avoid Q but take Q."
NO_BUS_ACTUALLY_BUS = "No buses\u2014actually only buses."
LEAVE_NOW_ARRIVE_YESTERDAY = "Leave now but arrive yesterday."
ARRIVE_AT_8_LEAVE_AT_9 = "Arrive at 8 but leave at 9."
DONT_CHANGE_MAKE_THIS = "Don't change my route; make this the route."
AVOID_STAIRS = "Avoid stairs"
NO_WALKING = "No walking at all"

# ---------------------------------------------------------------------------
# E3-C: temporal messages (natural language; the server receives ISO only)
# ---------------------------------------------------------------------------

LEAVE_NOW_PHRASE = "Leave now"
PLUS_FIVE_PHRASE = "Leave in 5 minutes"
PLUS_THIRTY_PHRASE = "Leave 30 minutes from now"
LATER_TONIGHT_PHRASE = "Leave later tonight"
TOMORROW_PHRASE = "Leave tomorrow at 8am"
WHAT_IF_PLUS_30 = "What if I leave 30 minutes later?"

# Route-classified messages whose scripted model round supplies the ISO.
CONTROL_ROUTE_MESSAGE = "Plan a trip from Home to Work"
TEMPORAL_ROUTE_MESSAGES = {
    "plus5": ("Plan a trip from Home to Work and leave in 5 minutes", DEPART_PLUS_5),
    "plus30": (
        "Plan a trip from Home to Work and leave 30 minutes from now",
        DEPART_PLUS_30,
    ),
    "later_tonight": (
        "Plan a trip from Home to Work and leave later tonight",
        DEPART_LATER_TONIGHT,
    ),
    "tomorrow": (
        "Plan a trip from Home to Work and leave tomorrow at 8am",
        DEPART_TOMORROW,
    ),
    "midnight_crossing": (
        "Plan a trip from Home to Work and leave after midnight",
        DEPART_MIDNIGHT_CROSSING,
    ),
}

# ---------------------------------------------------------------------------
# Tool profiles and forbidden surfaces (production values, frozen here)
# ---------------------------------------------------------------------------

TRANSIT_QUESTION_TOOL_PROFILE = frozenset(PUBLIC_TOOL_PROFILE)
ROUTE_TOOL_PROFILE = frozenset(PUBLIC_TOOL_PROFILE)
DISCOVERY_REFERENCE_TOOL_PROFILE = frozenset(PUBLIC_TOOL_PROFILE)
FORBIDDEN_ROUTE_SURFACE = (
    "plan_trip",
    "present_route",
    "web_search",
    "search_local_places",
    "event_lookup",
    "transit_snapshot",
    "lookup_arrivals",
    "lookup_facts",
    "venue_crowd_window",
    "check_area_conditions",
    "poi_search",
)

# Bounded markers from the real production executors/validators.
DEST_REQUIRED_MARKER = "destination is required"
RFC3339_MARKER = "must be RFC3339 with a timezone offset"
TZ_OFFSET_MARKER = "must include a timezone offset"
BOTH_TIMES_MARKER = "use either departure_time or arrival_by, not both"
DERIVE_FAILED_MARKER = "could not estimate an arrive-by departure"
EXPIRED_MARKER = "expired"
NO_TRANSIT_MODES_MARKER = "no transit modes left"

# ---------------------------------------------------------------------------
# Provider legs for loop scenarios (the prepare_route_options seam)
# ---------------------------------------------------------------------------

FIXED_CANDIDATE_ID = "cd_e3_fixed_1"


def bus_only_leg(destination: str = "Work") -> PreparedLeg:
    """One BUS-only provider fixture (for the no-buses contradiction)."""

    leg = make_leg(route_ids=("Q",), destination=destination)
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


def inaccessible_leg(destination: str = "Work") -> PreparedLeg:
    """A Q fixture whose station access is explicitly *incompatible*.

    ``route_accessibility`` returns ``"inaccessible"`` (not merely unknown),
    so an accessibility-required prepare must reject it deterministically.
    """

    leg = make_leg(route_ids=("Q",), destination=destination)
    leg.parsed_routes[0][1]["departure_accessibility"] = "inaccessible"
    return leg


# ---------------------------------------------------------------------------
# Direct executor probe fixtures (the real prepare_single_leg provider seam)
# ---------------------------------------------------------------------------

PROVIDER_ROUTE = [
    [
        {
            "type": "WALK",
            "duration_seconds": 180,
            "departure_time_iso": "2026-08-06T12:00:00-04:00",
            "arrival_time_iso": "2026-08-06T12:03:00-04:00",
        },
        {
            "type": "SUBWAY",
            "route_id": "Q",
            "duration_seconds": 1200,
            "departure_stop": "Home St",
            "arrival_stop": "Work",
            "departure_time_iso": "2026-08-06T12:05:00-04:00",
            "arrival_time_iso": "2026-08-06T12:25:00-04:00",
        },
    ]
]

SCAN_PAYLOAD = {
    "incidents": [],
    "scan_metadata": {
        "status": "complete",
        "lookup_status": "ok",
        "coverage_status": "complete",
        "lookup_kind": "index",
        "warning_count": 0,
        "cache_hit": False,
        "sources": {"attempted": [], "completed": []},
    },
}

__all__ = (
    "ARRIVAL_PAST",
    "ARRIVE_AT_8_LEAVE_AT_9",
    "AVOID_AND_TAKE_Q",
    "AVOID_STAIRS",
    "BOTH_TIMES_MARKER",
    "CONTROL_ROUTE_MESSAGE",
    "DEPART_LATER_TONIGHT",
    "DEPART_MIDNIGHT_CROSSING",
    "DEPART_PAST",
    "DEPART_PLUS_5",
    "DEPART_PLUS_30",
    "DEPART_TOMORROW",
    "DERIVE_FAILED_MARKER",
    "DEST_REQUIRED_MARKER",
    "DISCOVERY_REFERENCE_TOOL_PROFILE",
    "DONT_CHANGE_MAKE_THIS",
    "EXPIRED_MARKER",
    "FIXED_CANDIDATE_ID",
    "FORBIDDEN_ROUTE_SURFACE",
    "LATER_TONIGHT_PHRASE",
    "LEAVE_NOW_ARRIVE_YESTERDAY",
    "LEAVE_NOW_PHRASE",
    "MALFORMED_TIME",
    "NAIVE_TIME",
    "NAV_NO_CONTEXT",
    "NOW_ET",
    "NO_BUS_ACTUALLY_BUS",
    "NO_TRANSIT_MODES_MARKER",
    "NO_WALKING",
    "ORDINAL_NO_CONTEXT",
    "ORIGIN_ONLY_MESSAGES",
    "PLUS_FIVE_PHRASE",
    "PLUS_THIRTY_PHRASE",
    "PROVIDER_ROUTE",
    "REPLAN_WITHOUT_DESTINATION",
    "RFC3339_MARKER",
    "ROUTE_TOOL_PROFILE",
    "SCAN_PAYLOAD",
    "TEMPORAL_ROUTE_MESSAGES",
    "TOMORROW_PHRASE",
    "TRANSIT_QUESTION_TOOL_PROFILE",
    "TZ_OFFSET_MARKER",
    "WHAT_IF_PLUS_30",
    "bus_only_leg",
    "inaccessible_leg",
)
