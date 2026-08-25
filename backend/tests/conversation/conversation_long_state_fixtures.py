"""Batch H fixtures and constants for long mixed-domain conversation audits.

Non-test module (no ``Test*``/``test_*`` names at module level): pytest never
collects it. Batch H drives the *real* agent loop
(``app.services.agent.loop.run_agent_turn``) with production intent/tool
filtering and the real registered ``TOOL_REGISTRY`` executors for
``prepare_route_options`` / ``present_route`` / ``search_local_places`` /
``get_place_details`` / ``lookup_arrivals``, the real candidate/discovery/
trip stores, the real tool ledger, and real SSE events across 10-32 turn
transcripts in ONE server-owned session (Auto and Quick). ``plan_trip`` and
``[ROUTE:N]`` are never used; no fake executor replaces a canonical tool.

Only genuine provider/data seams are scripted (identical to the documented
Batch A-D harness):

- ``prepare_route_options.prepare_single_leg`` -- the provider route/evidence
  seam inside the real canonical prepare executor.
- ``trips.enrichment._enrich_route`` -- live route enrichment the real
  ``present_route`` executor may call.
- ``tools.lookup_arrivals.execute`` -- the module-attribute patch the harness
  installs is inert for the registry (executors are captured at registry
  build), so the REAL registered ``lookup_arrivals`` executor runs against
  ``gtfs=None`` and truthfully reports provider unavailability -- never
  fabricated arrivals and never a route mutation.
- ``search_local_places.execute`` -- the provider place-search seam
  inside the real discovery executor (raw Places-shaped fixtures).
- ``candidate_store.new_candidate_id`` -- opaque id generation only, so
  scripted ``present_route`` input is deterministic; records are still
  written by the real store.
- ``candidate_store.store_candidate_set`` -- observed via the harness
  recording wrapper, never replaced.

Anthropic inference is deterministic mock text via ``tests/_fake_anthropic``;
no model/provider/web/DB/network calls escape.

Waypoint inclusion (test metadata): Batch D1's multi-stop presentation gate
is GREEN at execution time (``test_conversation_discovery_waypoint.py``
passes on the real loop), so H-03 includes the discovery -> waypoint ->
removal lifecycle per the batch conditions.
"""

from __future__ import annotations

from app.services.agent.tools.location_resolution import ResolvedPlace
from app.services.agent.tools.route.preparation_adapter import PreparedLeg
from tests.conversation.conversation_discovery_fixtures import discovery_leg_for
from tests.conversation.conversation_matrix_harness import PUBLIC_TOOL_PROFILE, make_leg

# ---------------------------------------------------------------------------
# Transcript messages (one deterministic message per turn family)
# ---------------------------------------------------------------------------

ROUTE_WORK_MESSAGE = "Get me to Work."
ROUTE_BARCLAYS_MESSAGE = "Get me to Barclays."
ROUTE_MUSEUM_MESSAGE = "Get me to the Museum of Natural History."
STATUS_MESSAGE = "How is the uptown Q line doing?"
RETURN_STATUS_MESSAGE = "How is my current trip looking?"
ARRIVAL_MESSAGE = "When is the next uptown Q train?"
RECOVERY_ARRIVAL_MESSAGE = "When is the next train?"
EXPLAIN_MESSAGE = "Why did you pick the Q train?"
SIMPLE_MESSAGE = "Thanks!"
PREVIEW_LATER_MESSAGE = "What if I leave 30 minutes later?"
PREVIEW_TEN_MESSAGE = "What if I leave 10 minutes later?"
PREVIEW_BUS_MESSAGE = "What if I take the bus?"
ACCEPT_MESSAGE = "Use that instead."
REJECT_MESSAGE = "Never mind."
STALE_PROBE_MESSAGE = "Show me the first option."
DISCOVERY_PIZZA_MESSAGE = "Find me pizza places in Brooklyn."
DISCOVERY_COFFEE_MESSAGE = "Find me coffee near Barclays."
SELECT_SECOND_MESSAGE = "The second one."
SELECT_THIRD_MESSAGE = "The third one."
ROUTE_SELECTED_MESSAGE = "Take me there."
AVOID_Q_MESSAGE = "Avoid the Q."
ALLOW_Q_MESSAGE = "Fine, allow the Q."
AVOID_STAIRS_MESSAGE = "Avoid stairs."
WAYPOINT_ADD_MESSAGE = "Take me to the third one first."
WAYPOINT_REMOVE_MESSAGE = "Actually remove the pizza stop."

TEMPORAL_DEPARTURE = "2026-08-06T12:30:00-04:00"  # now + 30 minutes
TEN_MIN_DEPARTURE = "2026-08-06T12:10:00-04:00"  # now + 10 minutes
CONFLICTING_LABEL = "Completely Different Text"  # stored identity must win

# ---------------------------------------------------------------------------
# Offered tool profiles (asserted from the ACTUAL model request each turn)
# ---------------------------------------------------------------------------

ROUTE_TOOL_PROFILE = set(PUBLIC_TOOL_PROFILE)
DISCOVERY_TOOL_PROFILE = set(PUBLIC_TOOL_PROFILE)
TRANSIT_TOOL_PROFILE = set(PUBLIC_TOOL_PROFILE)
EXPLANATION_TOOL_PROFILE = set(PUBLIC_TOOL_PROFILE)
SELECT_TOOL_PROFILE = set(PUBLIC_TOOL_PROFILE)
ACCEPT_TOOL_PROFILE = set(PUBLIC_TOOL_PROFILE)
REJECT_TOOL_PROFILE = set(PUBLIC_TOOL_PROFILE)
ARRIVAL_TOOL_PROFILE = set(PUBLIC_TOOL_PROFILE)

FORBIDDEN_TOOLS = (
    "plan_trip",
    "poi_search",
    "check_area_conditions",
)
LEAK_MARKERS = ("cd_", "cs_", "pl_", "ds_", "rc_", "chij")


# ---------------------------------------------------------------------------
# Provider-seam leg fixtures
# ---------------------------------------------------------------------------


def q_leg(destination: str) -> PreparedLeg:
    """Provider yields the Q route for a destination."""

    return make_leg(route_ids=("Q",), destination=destination)


def r_leg(destination: str) -> PreparedLeg:
    """Provider yields the R route (never the excluded Q)."""

    return make_leg(route_ids=("R",), destination=destination)


def bus_leg(destination: str) -> PreparedLeg:
    """Provider yields a bus-first route for the what-if BUS preview."""

    leg = make_leg(route_ids=("Q",), destination=destination)
    leg.parsed_routes = [[
        {"type": "WALK", "duration_seconds": 120,
         "departure_time_iso": "2026-08-06T12:00:00-04:00",
         "arrival_time_iso": "2026-08-06T12:02:00-04:00"},
        {"type": "BUS", "route_id": "B38", "duration_seconds": 1560,
         "departure_stop": "Home St", "arrival_stop": destination,
         "departure_time_iso": "2026-08-06T12:05:00-04:00",
         "arrival_time_iso": "2026-08-06T12:31:00-04:00"},
    ]]
    leg.scored = [{
        "index": 0, "score": 21, "total_minutes": 31,
        "transfers": 0, "alert_count": 0, "transit_count": 1,
        "event_crowd_penalty": 0, "rank": 1,
    }]
    return leg


def waypoint_chain_legs(waypoint_place: dict, destination: str) -> list:
    """The two DISTINCT provider segments of the waypoint-add chain.

    Segment 1 routes user -> the stored waypoint place (stored identity,
    coordinates, and provider id at the provider boundary); segment 2 routes
    that stored place -> the accepted destination. ``prepare_single_leg`` is
    called once per segment in call order, so a phantom same-place segment
    can never be built.
    """

    first = discovery_leg_for(waypoint_place)
    second = q_leg(destination)
    second.origin_place = ResolvedPlace(
        name=waypoint_place["name"],
        latitude=float(waypoint_place["latitude"]),
        longitude=float(waypoint_place["longitude"]),
        source="discovery",
        address=waypoint_place.get("address") or None,
        place_id=waypoint_place.get("provider_place_id"),
    )
    return [first, second]


__all__ = (
    "ACCEPT_MESSAGE",
    "ACCEPT_TOOL_PROFILE",
    "ALLOW_Q_MESSAGE",
    "ARRIVAL_MESSAGE",
    "ARRIVAL_TOOL_PROFILE",
    "AVOID_Q_MESSAGE",
    "AVOID_STAIRS_MESSAGE",
    "CONFLICTING_LABEL",
    "DISCOVERY_COFFEE_MESSAGE",
    "DISCOVERY_PIZZA_MESSAGE",
    "DISCOVERY_TOOL_PROFILE",
    "EXPLAIN_MESSAGE",
    "EXPLANATION_TOOL_PROFILE",
    "FORBIDDEN_TOOLS",
    "LEAK_MARKERS",
    "PREVIEW_BUS_MESSAGE",
    "PREVIEW_LATER_MESSAGE",
    "PREVIEW_TEN_MESSAGE",
    "RECOVERY_ARRIVAL_MESSAGE",
    "REJECT_MESSAGE",
    "REJECT_TOOL_PROFILE",
    "RETURN_STATUS_MESSAGE",
    "ROUTE_BARCLAYS_MESSAGE",
    "ROUTE_MUSEUM_MESSAGE",
    "ROUTE_SELECTED_MESSAGE",
    "ROUTE_TOOL_PROFILE",
    "ROUTE_WORK_MESSAGE",
    "SELECT_SECOND_MESSAGE",
    "SELECT_THIRD_MESSAGE",
    "SELECT_TOOL_PROFILE",
    "SIMPLE_MESSAGE",
    "STALE_PROBE_MESSAGE",
    "STATUS_MESSAGE",
    "TEMPORAL_DEPARTURE",
    "TEN_MIN_DEPARTURE",
    "TRANSIT_TOOL_PROFILE",
    "WAYPOINT_ADD_MESSAGE",
    "WAYPOINT_REMOVE_MESSAGE",
    "bus_leg",
    "q_leg",
    "r_leg",
    "waypoint_chain_legs",
)
