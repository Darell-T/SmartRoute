"""Current line check for venues the Damn Lines registry already monitors."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from app.services.agent import discovery_store, public_surface
from app.services.agent.tool_input_policy import validated_goal_key
from app.services.agent.tools.base import ToolContext, ToolResult
from app.services.agent.tools.places import damn_lines
from app.services.agent.turn.contract import GoalKind

_NYC = ZoneInfo("America/New_York")
_GENERIC_NAME_TOKENS = frozenset(
    {
        "pizzeria",
        "restaurant",
        "cafe",
        "caffe",
        "the",
        "by",
        "of",
        "street",
    }
)
_AREA_SLUG_TOKENS = {"west village": "wv"}
_SLUG_AREA_LABELS = {"wv": "West Village"}

CHECK_PLACE_LINE_SCHEMA = {
    "name": "check_place_line",
    "description": (
        "Read the current line for one monitored venue. Use the rider's "
        "venue_name and area, or a place_id already issued in this session."
    ),
    "strict": True,
    "input_schema": {
        "type": "object",
        "properties": {
            "goal_key": {
                "type": "string",
                "description": "Declared general_response goal for this line question.",
            },
            "venue_name": {
                "type": "string",
                "description": "Place name the rider used. Empty when place_id is set.",
            },
            "area": {
                "type": "string",
                "description": "Neighborhood or area the rider named. Empty if none.",
            },
            "place_id": {
                "type": "string",
                "description": "Opaque place id from this session. Empty when matching by name.",
            },
        },
        "required": ["goal_key", "venue_name", "area", "place_id"],
        "additionalProperties": False,
    },
}


def _clock(value: datetime) -> str:
    return value.astimezone(_NYC).strftime("%I:%M %p").lstrip("0")


def _area_label(venue: damn_lines.SupportedVenue) -> str:
    token = venue.slug.rsplit("_", 1)[-1]
    return _SLUG_AREA_LABELS.get(token, "")


def _name_hits(query: str, venue_name: str) -> bool:
    wanted = [
        token
        for token in discovery_store.normalized_name(query).split()
        if token not in _GENERIC_NAME_TOKENS
    ]
    actual = discovery_store.normalized_name(venue_name).split()
    if not wanted or not actual:
        return False
    return all(any(word == token or word.startswith(token) for word in actual) for token in wanted)


def _area_hits(area: str, venue: damn_lines.SupportedVenue) -> bool:
    text = discovery_store.normalized_name(area)
    if not text:
        return True
    if text in discovery_store.normalized_name(venue.name):
        return True
    slug_tokens = set(venue.slug.replace("_", " ").split())
    token = _AREA_SLUG_TOKENS.get(text)
    return token in slug_tokens if token else False


def match_venues(name: str, area: str) -> tuple[damn_lines.SupportedVenue, ...]:
    hits = [
        venue
        for venue in damn_lines.supported_venues()
        if _name_hits(name, venue.name) and _area_hits(area, venue)
    ]
    return tuple(hits)


def _venue_for_place_id(ctx: ToolContext, place_id: str) -> damn_lines.SupportedVenue | None:
    session = ctx.session if isinstance(ctx.session, dict) else None
    provider_id = _provider_id(session, ctx.session_id, place_id)
    if not provider_id:
        return None
    return damn_lines.get_supported_venue(provider_id)


def _presented_provider_id(
    session: dict | None, session_id: str, place_id: str
) -> str:
    resolved, _, _ = discovery_store.resolve_presented_place_reference(
        session=session,
        session_id=session_id,
        place_id=place_id,
    )
    if not isinstance(resolved, dict):
        return ""
    return str(resolved.get("provider_place_id") or "").strip()


def _stored_provider_id(places: object, place_id: str) -> str:
    if not isinstance(places, list):
        return ""
    for place in places:
        if str(place.get("place_id") or "") == place_id:
            return str(place.get("provider_place_id") or "").strip()
    return ""


def _provider_id(session: dict | None, session_id: str, place_id: str) -> str:
    presented = _presented_provider_id(session, session_id, place_id)
    if presented:
        return presented
    set_id = public_surface.active_discovery_set_id(session, session_id=session_id)
    if not set_id:
        return ""
    record = discovery_store.load_discovery_set(set_id, session_id=session_id)
    places = record.get("places") if isinstance(record, dict) else None
    return _stored_provider_id(places, place_id)


def _payload(
    status: str,
    venue: damn_lines.SupportedVenue | None = None,
    **extra: object,
) -> dict[str, object]:
    payload: dict[str, object] = {"status": status}
    if venue is not None:
        payload["name"] = venue.name
        area = _area_label(venue)
        if area:
            payload["area"] = area
    payload.update(extra)
    return payload


def _venues_for_input(
    tool_input: dict, ctx: ToolContext
) -> tuple[tuple[damn_lines.SupportedVenue, ...], ToolResult | None]:
    place_id = str(tool_input.get("place_id") or "").strip()
    name = str(tool_input.get("venue_name") or "").strip()
    if place_id:
        venue = _venue_for_place_id(ctx, place_id)
        found = (venue,) if venue is not None else ()
        return found, None
    if name:
        area = str(tool_input.get("area") or "").strip()
        return match_venues(name, area), None
    return (), ToolResult(
        ok=False,
        error="venue_name or place_id is required",
        internal_diagnostic=True,
    )


def _line_payload(
    venue: damn_lines.SupportedVenue, current: damn_lines.CurrentQueueResult
) -> dict[str, object]:
    observation = current.observations.get(venue.google_place_id)
    if observation is None or not current.provider_available:
        return _payload("unavailable", venue)
    data = _payload(
        "ready",
        venue,
        observed_at=_clock(observation.captured_at),
    )
    if observation.wait_minutes is not None:
        data["wait_minutes"] = observation.wait_minutes
    if observation.people_count is not None:
        data["people_count"] = observation.people_count
    return data


async def execute(tool_input: dict, ctx: ToolContext) -> ToolResult:
    _goal_key, goal_error = validated_goal_key(
        tool_input,
        ctx,
        compatible_kinds=frozenset({GoalKind.GENERAL_RESPONSE}),
        incompatible_error="check_place_line requires a general_response goal",
    )
    if goal_error is not None:
        return goal_error
    del _goal_key
    venues, error = _venues_for_input(tool_input, ctx)
    if error is not None:
        return error
    if not venues:
        return ToolResult(ok=True, data=_payload("not_monitored"))
    if len(venues) > 1:
        names = [venue.name for venue in venues]
        return ToolResult(ok=True, data={"status": "ambiguous", "names": names})
    current = await damn_lines.get_current_observations([venues[0].google_place_id])
    return ToolResult(ok=True, data=_line_payload(venues[0], current))
