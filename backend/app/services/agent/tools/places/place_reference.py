"""Server-owned place-reference resolution for conversational discovery.

``get_place_details`` binds a discovery place (opaque place_id, ordinal, or a
deterministic description) as the selected place and returns the opaque
place_id for route preparation. The destination_label is display-only and is
never used to resolve routing coordinates.
"""

from __future__ import annotations

from app.services.agent.tools._types import ToolContext, ToolResult

GET_PLACE_DETAILS_SCHEMA = {
    "name": "get_place_details",
    "description": (
        "Resolve a server-owned presented place reference (opaque place_id, "
        "an ordinal from the newest compatible presentation, or a name "
        "across the session's Presented Entity Registry). Deterministic "
        "descriptions such as 'cheaper' or 'Brooklyn' still use the active "
        "discovery set. Bind the result as the selected place. Returns the "
        "opaque place_id; pass it to "
        "prepare_route_options as destination_place_id (or add it to "
        "waypoints for an intermediate stop). destination_label is "
        "display-only and is never used to resolve routing coordinates."
    ),
    "strict": True,
    "input_schema": {
        "type": "object",
        "properties": {
            "place_id": {
                "type": "string",
                "description": (
                    "Opaque place id from a presented discovery result. Provide "
                    "exactly one of place_id, ordinal, or description."
                ),
            },
            "ordinal": {
                "type": "integer",
                "description": "1-based position in the discovery set, e.g. 2 for 'the second one'.",
            },
            "description": {
                "type": "string",
                "description": (
                    "A deterministic description such as 'cheaper', a borough "
                    "such as 'Brooklyn', or a unique presented name/category "
                    "fragment. Explicit place names search all places shown "
                    "during this session."
                ),
            },
        },
        "required": [],
        "additionalProperties": False,
    },
}


def _parsed_place_selector(
    tool_input: dict,
) -> tuple[str, int | None, str, ToolResult | None]:
    place_id = str(tool_input.get("place_id") or "").strip()
    ordinal = tool_input.get("ordinal")
    if isinstance(ordinal, bool):
        return "", None, "", ToolResult(ok=False, error="ordinal must be a whole number")
    ordinal_int = None
    if ordinal is not None:
        try:
            ordinal_int = int(ordinal)
        except (TypeError, ValueError):
            return "", None, "", ToolResult(ok=False, error="ordinal must be a whole number")
    description = str(tool_input.get("description") or "").strip()
    provided = sum(
        value is not None
        for value in (place_id or None, ordinal_int, description or None)
    )
    if provided != 1:
        return "", None, "", ToolResult(
            ok=False,
            error="provide exactly one of place_id, ordinal, or description",
        )
    return place_id, ordinal_int, description, None


def _resolve_owned_place(
    *,
    session: dict,
    session_id: str,
    place_id: str | None,
    ordinal: int | None,
    description: str | None,
) -> tuple[dict | None, str | None, str | None]:
    """Presented-place lookup, then the session's active discovery set."""
    from app.services.agent import discovery_store
    from app.services.agent import trip_state as trip_state_module

    place, error, presented_set_id = discovery_store.resolve_presented_place_reference(
        session=session,
        session_id=session_id,
        place_id=place_id,
        ordinal=ordinal,
        description=description,
    )
    if place is not None:
        return place, None, presented_set_id
    if presented_set_id:
        place, error = discovery_store.resolve_place_reference(
            session_id=session_id,
            discovery_set_id=presented_set_id,
            place_id=place_id,
            ordinal=ordinal,
            description=description,
        )
        return place, error, presented_set_id
    if error:
        return None, error, None
    state = trip_state_module.get_trip_state(session)
    active_set_id = str(state.get("active_discovery_set_id") or "").strip() or None
    place, error = discovery_store.resolve_place_reference(
        session_id=session_id,
        discovery_set_id=active_set_id,
        place_id=place_id,
        ordinal=ordinal,
        description=description,
    )
    return place, error, None


def _bind_resolved_place(
    ctx: ToolContext,
    *,
    presented_set_id: str | None,
    place_id: str,
) -> None:
    from app.services.agent import trip_state as trip_state_module

    if not isinstance(ctx.session, dict):
        return
    if presented_set_id:
        # Bind the set the rider actually selected so later ordinals
        # do not follow a newer active set.
        trip_state_module.bind_discovery_context(
            ctx.session,
            discovery_set_id=presented_set_id,
            selected_place_id=place_id,
        )
        return
    trip_state_module.bind_selected_place(ctx.session, place_id)


def _place_details_result(place: dict) -> ToolResult:
    place_id = str(place.get("place_id") or "").strip()
    label = str(place.get("name") or "").strip()
    address = str(place.get("address") or "").strip()
    destination = f"{label}, {address}" if label and address else (label or address)
    return ToolResult(
        ok=True,
        data={
            "place_id": place_id,
            "destination_label": destination,
            "name": label,
            "address": address,
            "open_status": place.get("open_status"),
            "baseline_score": place.get("baseline_score"),
            "canonical": True,
        },
        summary=f"resolved {label or 'place'} for routing",
    )


async def execute(tool_input: dict, ctx: ToolContext) -> ToolResult:
    session_id = str(getattr(ctx, "session_id", None) or "").strip()
    if not session_id:
        return ToolResult(ok=False, error="session is required")
    place_id, ordinal_int, description, selector_error = _parsed_place_selector(
        tool_input
    )
    if selector_error:
        return selector_error
    session = ctx.session if isinstance(ctx.session, dict) else {}
    place, resolve_error, presented_set_id = _resolve_owned_place(
        session=session,
        session_id=session_id,
        place_id=place_id or None,
        ordinal=ordinal_int,
        description=description or None,
    )
    missing = _missing_place_result(resolve_error, place)
    if missing is not None:
        return missing
    bound_place_id = str(place.get("place_id") or "").strip()
    _bind_resolved_place(
        ctx,
        presented_set_id=presented_set_id,
        place_id=bound_place_id,
    )
    return _place_details_result(place)


def _missing_place_result(resolve_error: str | None, place: dict | None) -> ToolResult | None:
    if resolve_error or place is None:
        return ToolResult(ok=False, error=resolve_error or "place not found")
    return None


__all__ = ("GET_PLACE_DETAILS_SCHEMA", "execute")
