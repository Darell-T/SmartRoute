"""Structured local discovery with server-owned place IDs."""

from __future__ import annotations

import math

from app.services.agent import discovery_store
from app.services.agent import trip_state as trip_state_module
from app.services.agent.tools import poi_search
from app.services.agent.tools._types import ToolContext, ToolResult

SEARCH_LOCAL_PLACES_SCHEMA = {
    "name": "search_local_places",
    "description": (
        "Search for local places (restaurants, venues, stores, landmarks) near "
        "an NYC location. Returns opaque place IDs for conversational follow-ups "
        "such as 'the second one' or 'take me there'. Prefer this over free-form "
        "web search when structured place data is enough."
    ),
    "strict": True,
    "input_schema": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "What to find, e.g. 'pizza' or 'coffee near Barclays'.",
            },
            "near": {
                "type": "string",
                "description": "'user', an NYC place name, or lat,lng. Omit for NYC-wide bias.",
            },
            "open_now": {
                "type": "boolean",
                "description": "When true, only include places currently open if hours are known.",
            },
            "max_results": {
                "type": "integer",
                "description": "Maximum places to return (default 3).",
            },
        },
        "required": ["query"],
        "additionalProperties": False,
    },
}


_OPEN_BONUS = {"open": 0.15, "unknown": 0.05, "closed": 0.0}


def _finite(value: object, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default


def _clamped(value: float, maximum: float) -> float:
    return max(0.0, min(maximum, value))


def baseline_ranking(place: dict) -> dict[str, object]:
    """Deterministic advisory ranking metadata; never reorders provider results."""

    rating = _clamped(_finite(place.get("rating")), 5.0) / 5.0
    review = _clamped(_finite(place.get("review_count")), 5000.0) / 5000.0
    open_status = str(place.get("open_status") or "unknown")
    open_bonus = _OPEN_BONUS.get(open_status, 0.05)
    score = round(0.50 * rating + 0.25 * review + 0.25 * open_bonus, 4)
    return {
        "baseline_score": score,
        "ranking_factors": {
            "rating": round(rating, 4),
            "review_volume": round(review, 4),
            "open_bonus": open_bonus,
            "price_level": place.get("price_level"),
        },
    }


async def execute(tool_input: dict, ctx: ToolContext) -> ToolResult:
    session_id = str(getattr(ctx, "session_id", None) or "").strip()
    if not session_id:
        return ToolResult(ok=False, error="session is required for local discovery")

    poi_input = {
        "query": tool_input.get("query"),
        "near": tool_input.get("near"),
        "max_results": tool_input.get("max_results"),
    }
    result = await poi_search.execute(poi_input, ctx)
    if not result.ok:
        return result

    raw_places = []
    data = result.data if isinstance(result.data, dict) else {}
    for place in data.get("places") or data.get("results") or []:
        if not isinstance(place, dict):
            continue
        open_now = place.get("open_now")
        if tool_input.get("open_now") is True and open_now is False:
            continue
        ranked = {
            "name": place.get("name") or place.get("display_name"),
            "address": place.get("address") or place.get("formatted_address"),
            "neighborhood": place.get("neighborhood") or "",
            "category": place.get("category") or tool_input.get("query"),
            "open_status": (
                "open" if open_now is True else ("closed" if open_now is False else "unknown")
            ),
            "price_level": place.get("price_level"),
            "rating": place.get("rating"),
            "review_count": place.get("review_count") or place.get("user_rating_count"),
            "latitude": (place.get("location") or {}).get("latitude")
            if isinstance(place.get("location"), dict)
            else place.get("lat") or place.get("latitude"),
            "longitude": (place.get("location") or {}).get("longitude")
            if isinstance(place.get("location"), dict)
            else place.get("lng") or place.get("longitude"),
            "provider_place_id": place.get("place_id") or place.get("id"),
            "transit_context": place.get("transit_context") or {},
        }
        ranking = baseline_ranking(ranked)
        ranked["baseline_score"] = ranking["baseline_score"]
        ranked["ranking_factors"] = ranking["ranking_factors"]
        raw_places.append(ranked)

    if not raw_places:
        return ToolResult(
            ok=True,
            data={"discovery_set_id": None, "places": []},
            summary="no matching places found",
            timings=result.timings,
        )

    set_id = discovery_store.store_discovery_set(
        session_id=session_id,
        places=raw_places,
        query=str(tool_input.get("query") or ""),
    )
    record = discovery_store.load_discovery_set(set_id, session_id=session_id) or {}
    model_places = [
        {
            "place_id": place.get("place_id"),
            "ordinal": place.get("ordinal"),
            "name": place.get("name"),
            "neighborhood": place.get("neighborhood"),
            "category": place.get("category"),
            "open_status": place.get("open_status"),
            "price_level": place.get("price_level"),
            "rating": place.get("rating"),
            "review_count": place.get("review_count"),
            "baseline_score": place.get("baseline_score"),
            "ranking_factors": discovery_store.sanitized_ranking_factors(
                place.get("ranking_factors")
            ),
            "address": place.get("address"),
        }
        for place in (record.get("places") or [])
    ]
    if isinstance(ctx.session, dict):
        trip_state_module.bind_discovery_set(ctx.session, set_id)
    return ToolResult(
        ok=True,
        data={"discovery_set_id": set_id, "places": model_places},
        summary=f"found {len(model_places)} place(s)",
        timings=result.timings,
    )
