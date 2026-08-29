from __future__ import annotations

from typing import Any

from app.services.agent import discovery_store

_PLACE_ID_FIELDS = frozenset(
    {
        "place_id",
        "destination_place_id",
        "selected_place_id",
        "waypoint_place_id",
    }
)
_PLACE_ID_LIST_FIELDS = frozenset({"destination_place_ids", "place_ids"})
_PROVIDER_ID_FIELDS = frozenset({"provider_place_id", "provider_place_ids"})
_PRESENTER_RECEIPT_FIELDS = frozenset(
    {
        "already_presented",
        "goal_key",
        "operation",
        "presentation_outcome",
    }
)


def opaque_place_id(value: object) -> str | None:
    """Return a model-safe place identity, or ``None`` for provider ids."""

    candidate = str(value or "").strip()
    return candidate if discovery_store.is_opaque_place_id(candidate) else None


def project_model_value(value: object) -> object:
    """Copy a payload while removing provider place identity fields."""

    if isinstance(value, dict):
        projected: dict[str, object] = {}
        for raw_key, raw_value in value.items():
            key = str(raw_key)
            if key in _PROVIDER_ID_FIELDS:
                continue
            if key in _PLACE_ID_FIELDS:
                safe = opaque_place_id(raw_value)
                if safe is not None:
                    projected[key] = safe
                continue
            if key in _PLACE_ID_LIST_FIELDS:
                if isinstance(raw_value, list):
                    projected[key] = [
                        safe
                        for item in raw_value
                        if (safe := opaque_place_id(item)) is not None
                    ]
                continue
            projected[key] = project_model_value(raw_value)
        return projected
    if isinstance(value, (list, tuple)):
        return [project_model_value(item) for item in value]
    return value


def project_route_preparation(
    data: object,
    tool_input: dict[str, Any] | None = None,
) -> object:
    """Project ``prepare_route_options`` data without losing opaque identity."""

    if not isinstance(data, dict):
        return project_model_value(data)

    projected = project_model_value(data)
    source_ids = _opaque_ids(data.get("destination_place_ids"))
    input_ids = _input_destination_ids(tool_input)
    destination_ids = source_ids or input_ids
    if not source_ids and input_ids:
        projected["destination_place_ids"] = list(input_ids)

    for list_key, id_key in (
        ("candidates", "destination_place_id"),
        ("branch_coverage", "place_id"),
    ):
        rows = data.get(list_key)
        if not isinstance(rows, list):
            continue
        projected_rows: list[object] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            row_projection = project_model_value(row)
            row_id = opaque_place_id(row.get(id_key)) or (
                destination_ids[0] if len(destination_ids) == 1 else None
            )
            row_projection.pop(id_key, None)
            if row_id is not None:
                row_projection[id_key] = row_id
            projected_rows.append(row_projection)
        projected[list_key] = projected_rows

    return projected


def project_place_point(
    point: object,
    *,
    fallback_place_id: object = None,
) -> dict[str, Any]:
    """Project a route-card place point and optionally restore its opaque id."""

    projected = project_model_value(point)
    result = dict(projected) if isinstance(projected, dict) else {}
    if "place_id" in result:
        return result
    fallback = opaque_place_id(fallback_place_id)
    if fallback is not None:
        result["place_id"] = fallback
    return result


def project_tool_result_data(
    name: str,
    data: object,
    tool_input: dict[str, Any] | None = None,
) -> object:
    """Return the smallest safe result required by the next model round."""

    if name == "prepare_route_options":
        projected = project_route_preparation(data, tool_input)
    elif name in {"present_places", "present_transit", "present_route"}:
        payload = data if isinstance(data, dict) else {}
        receipt = {
            key: payload[key]
            for key in _PRESENTER_RECEIPT_FIELDS
            if key in payload
        }
        projected = {"presented": True, **receipt}
    else:
        projected = data

    to_payload = getattr(projected, "to_payload", None)
    if callable(to_payload):
        projected = to_payload()
    return project_model_value(projected)


def project_presented_route(data: dict[str, Any]) -> dict[str, Any]:
    """Return a model receipt without private route-decision state."""

    private_keys = {
        "candidate_id",
        "candidate_set_id",
        "selected_candidate_id",
        "selected_route_index",
        "selection_decision",
    }
    visible = {key: value for key, value in data.items() if key not in private_keys}
    candidates = visible.get("candidates")
    if isinstance(candidates, list):
        private_candidate_keys = {
            "card_id",
            "event_crowd_penalty",
            "selection_score",
            "selection_rank",
            "score_breakdown",
            "score_summary",
            "structured_recommendation_reasons",
            "reason",
        }
        visible["candidates"] = [
            {
                key: value
                for key, value in candidate.items()
                if key not in private_candidate_keys
            }
            for candidate in candidates
            if isinstance(candidate, dict)
        ]
    return visible


def _input_destination_ids(tool_input: dict[str, Any] | None) -> list[str]:
    if not isinstance(tool_input, dict):
        return []
    raw = tool_input.get("destination_place_ids")
    if isinstance(raw, list):
        return _opaque_ids(raw)
    single = opaque_place_id(tool_input.get("destination_place_id"))
    return [single] if single is not None else []


def _opaque_ids(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [
        safe
        for item in value
        if (safe := opaque_place_id(item)) is not None
    ]


__all__ = (
    "opaque_place_id",
    "project_model_value",
    "project_place_point",
    "project_presented_route",
    "project_route_preparation",
    "project_tool_result_data",
)
