"""Deterministic non-Anthropic fixtures for live Sonnet certification."""

from __future__ import annotations

from app.services.agent.tools._types import ToolResult
from tests.conversation.conversation_matrix_harness import make_leg

def _place_results() -> ToolResult:
    return ToolResult(
        ok=True,
        data={
            "results": [
                {
                    "name": "Ramen A",
                    "address": "10 East 14th Street, New York, NY",
                    "lat": 40.7358,
                    "lng": -73.9924,
                    "open_now": True,
                    "rating": 4.7,
                    "review_count": 900,
                    "place_id": "provider-ramen-a",
                    "address_components": [
                        {"longText": "Manhattan", "types": ["sublocality_level_1"]}
                    ],
                },
                {
                    "name": "Ramen B",
                    "address": "20 West 8th Street, New York, NY",
                    "lat": 40.7332,
                    "lng": -73.9986,
                    "open_now": True,
                    "rating": 4.8,
                    "review_count": 700,
                    "place_id": "provider-ramen-b",
                    "address_components": [
                        {"longText": "Manhattan", "types": ["sublocality_level_1"]}
                    ],
                },
                {
                    "name": "Ramen C",
                    "address": "30 East 9th Street, New York, NY",
                    "lat": 40.7319,
                    "lng": -73.9876,
                    "open_now": True,
                    "rating": 4.6,
                    "review_count": 500,
                    "place_id": "provider-ramen-c",
                    "address_components": [
                        {"longText": "Manhattan", "types": ["sublocality_level_1"]}
                    ],
                },
            ]
        },
        summary="3 verified ramen places",
    )

def _transit_status() -> ToolResult:
    return ToolResult(
        ok=True,
        data={
            "source": "mta_service_alerts",
            "freshness": "live",
            "status": "active_alerts",
            "alerts": [
                {
                    "alert_id": "q-delay",
                    "header": "Downtown Q trains are running with delays",
                    "route_ids": ["Q"],
                    "direction": "downtown",
                }
            ],
            "unconfirmed_signals": [
                {
                    "kind": "possible_stalled_train",
                    "route_id": "Q",
                    "mode": "subway",
                    "direction": "downtown",
                }
            ],
            "incident_coverage": "current",
            "gtfs_rt_coverage": "current",
        },
        summary="Checked Q service",
    )

def _place(
    name: str,
    address: str,
    borough: str,
    rating: float,
    reviews: int,
    place_id: str,
) -> dict:
    return {
        "name": name,
        "address": address,
        "lat": 40.7358,
        "lng": -73.9924,
        "open_now": True,
        "rating": rating,
        "review_count": reviews,
        "place_id": place_id,
        "address_components": [
            {"longText": borough, "types": ["sublocality_level_1"]}
        ],
    }

async def _contextual_place_results(tool_input: dict, _ctx) -> ToolResult:
    query = str(tool_input.get("query") or "").casefold()
    near = str(tool_input.get("near") or "").casefold()
    if "ramen" in query:
        return _place_results()
    if "brooklyn" in near:
        results = [
            _place(
                "Lo Duca Pizza",
                "14 Newkirk Plaza, Brooklyn, NY",
                "Brooklyn", 4.8, 780, "provider-lo-duca",
            ),
            _place(
                "Little Plaza Pizza",
                "188 Parkside Avenue, Brooklyn, NY",
                "Brooklyn", 4.7, 190, "provider-little-plaza",
            ),
            _place(
                "Wheated",
                "905 Church Avenue, Brooklyn, NY",
                "Brooklyn", 4.6, 740, "provider-wheated",
            ),
        ]
    else:
        results = [
            _place(
                "Prince Street Pizza",
                "27 Prince Street, New York, NY",
                "Manhattan", 4.5, 9100, "provider-prince-street",
            ),
            _place(
                "L'Industrie Pizzeria",
                "104 Christopher Street, New York, NY",
                "Manhattan", 4.7, 390, "provider-lindustrie",
            ),
            _place(
                "John's of Bleecker Street",
                "278 Bleecker Street, New York, NY",
                "Manhattan", 4.6, 8500, "provider-johns",
            ),
        ]
    return ToolResult(
        ok=True,
        data={"results": results},
        summary=f"{len(results)} verified pizza places",
    )

async def _prepared_route(*args, **kwargs):
    destination = kwargs.get("resolved_destination")
    tool_input = args[0] if args and isinstance(args[0], dict) else {}
    label = (
        getattr(destination, "name", None)
        or str(tool_input.get("destination") or "").strip()
        or "the selected place"
    )
    leg = make_leg(
        route_ids=("Q", "B"),
        destination=label,
        evidence_available=True,
    )
    if destination is not None:
        leg.destination_place = destination
        leg.destination_raw = label
    return leg

async def _prepared_crowd_route(*args, **kwargs):
    destination = kwargs.get("resolved_destination")
    tool_input = args[0] if args and isinstance(args[0], dict) else {}
    label = (
        getattr(destination, "name", None)
        or str(tool_input.get("destination") or "").strip()
        or "the selected place"
    )
    leg = make_leg(
        route_ids=("Q", "B"),
        destination=label,
        event_impacts=(
            {
                "route_index": 0,
                "title": "Heavy event crowd near the transfer path",
                "venue": "Midtown venue",
                "crowd_level": "high",
                "confidence": "high",
                "risk_score": 12,
            },
        ),
        evidence_available=True,
        event_evidence_status="current",
    )
    leg.collect_crowd_evidence = True
    if destination is not None:
        leg.destination_place = destination
        leg.destination_raw = label
    return leg

async def _prepared_advisory_route(*args, **kwargs):
    destination = kwargs.get("resolved_destination")
    tool_input = args[0] if args and isinstance(args[0], dict) else {}
    label = (
        getattr(destination, "name", None)
        or str(tool_input.get("destination") or "").strip()
        or "the selected place"
    )
    leg = make_leg(
        route_ids=("Q", "B"),
        destination=label,
        alerts=(
            {
                "header": "Downtown Q trains are running with delays",
                "route_ids": ["Q"],
                "direction": "downtown",
            },
        ),
        incident_status="unscanned",
        evidence_available=True,
    )
    if destination is not None:
        leg.destination_place = destination
        leg.destination_raw = label
    return leg
